"""Pinned Danish Whisper conversion; explicit preparation and file-free historical evidence.

This module is also shipped beside the isolated worker. Import uses only the standard
library. Download/conversion dependencies are loaded only by explicit ``prepare`` calls.
"""

import copy
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import uuid
from functools import cache as cached
from pathlib import Path

CHOICES = ("edda-turbo", "edda-large", "roest-v3")
CATALOG_SHA256 = "8fd6fa3bea645fedeec90613ac65ce7410459a6744502f864bdc5ec0881b8fa5"
RECIPE_ID = "cbs-whisper-ct2-fp16-v1"
MAX_RECEIPT_BYTES = 65536
MAX_OUTPUT_BYTES = 8 * 1024**3
MARKER = ".cbs-ct2.json"
OUTPUT_NAMES = frozenset(
    {
        "config.json",
        "model.bin",
        "vocabulary.json",
        "tokenizer.json",
        "preprocessor_config.json",
        "SOURCE_README.md",
        "SOURCE_MANIFEST.json",
    }
)
CONTRACT = {
    "id": RECIPE_ID,
    "ctranslate2": "4.8.3",
    "transformers": "4.57.6",
    "safetensors": "0.8.0",
    "torch": "2.8.0",
    "quantization": "float16",
    "load_as_float16": False,
    "trust_remote_code": False,
    "local_files_only": True,
    "weights_format": "safetensors",
}
_HASH = re.compile(r"[a-f0-9]{64}")
_REV = re.compile(r"[a-f0-9]{40}")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]{0,32}")
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_BASE = {"schema", "choice", "source", "recipe", "files", "artifact_sha256"}


class PreparationError(ValueError):
    """Curated refusal; never includes local paths or upstream credentials."""


def _fail():
    raise PreparationError("Danish model preparation evidence is missing, changed or invalid.")


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _digest(value):
    try:
        return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail()


RECIPE_CONTRACT_SHA256 = _digest(CONTRACT)


def _sha(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _json(raw, limit=MAX_RECEIPT_BYTES):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        _fail()
    try:
        text = raw.decode("utf-8")
        depth, quoted, escaped = 0, False, False
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > 12:
                    _fail()
            elif char in "]}":
                depth -= 1
                if depth < 0:
                    _fail()
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda _: _fail())
    except (ValueError, UnicodeError, RecursionError):
        _fail()


@cached
def _models():
    here = Path(__file__).resolve().parent
    path = here / "catalogs" / "danish_whisper_models.json"
    if not path.exists():
        path = here / "danish_whisper_models.json"
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECEIPT_BYTES + 1).replace(b"\r\n", b"\n")
    except OSError:
        _fail()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA256:
        _fail()
    document = _json(raw)
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "models"}
        or document["schema"] != "cbs.transcriber.danish_whisper_sources.v1"
        or not isinstance(document["models"], list)
        or len(document["models"]) != len(CHOICES)
    ):
        _fail()
    result = {}
    for item in document["models"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("choice"), str)
            or item["choice"] not in CHOICES
            or item["choice"] in result
            or not isinstance(item.get("repo"), str)
            or not _REPO.fullmatch(item["repo"])
            or not isinstance(item.get("revision"), str)
            or not _REV.fullmatch(item["revision"])
            or item.get("language") != "da"
            or item.get("sampling_rate") != 16000
            or item.get("mel_bins") != 128
            or item.get("window_seconds") != 30
        ):
            _fail()
        files = item.get("source_files")
        if not isinstance(files, list) or not 1 <= len(files) <= 20:
            _fail()
        names = set()
        for entry in files:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"name", "bytes", "sha256"}
                or not isinstance(entry["name"], str)
                or not _NAME.fullmatch(entry["name"])
                or entry["name"] in {".", ".."}
                or entry["name"] in names
                or type(entry["bytes"]) is not int
                or not 0 < entry["bytes"] <= MAX_OUTPUT_BYTES
                or not _sha(entry["sha256"])
                or entry["name"].endswith((".py", ".bin", ".pt", ".pkl"))
            ):
                _fail()
            names.add(entry["name"])
        if not {"config.json", "preprocessor_config.json", "tokenizer.json", "README.md"} <= names:
            _fail()
        if not any(name.endswith(".safetensors") for name in names):
            _fail()
        result[item["choice"]] = item
    if set(result) != set(CHOICES):
        _fail()
    return result


def models():
    return copy.deepcopy(_models())


def source_identity(choice):
    item = _definition(choice)
    return item["repo"], item["revision"]


def _definition(choice):
    if not isinstance(choice, str) or choice not in CHOICES:
        _fail()
    return _models()[choice]


def relative_folder(choice):
    item = _definition(choice)
    return f"cbs-ct2/{choice}/{item['revision']}/{RECIPE_CONTRACT_SHA256}"


def _cache_root(cache):
    if cache is not None:
        return Path(cache).expanduser().absolute()
    for variable in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if os.environ.get(variable):
            return Path(os.environ[variable]).expanduser().absolute()
    return (
        Path(os.environ.get("HF_HOME") or Path.home() / ".cache/huggingface") / "hub"
    ).absolute()


def _owned_folder(folder, root):
    try:
        if not folder.resolve().is_relative_to(root.resolve()):
            _fail()
        current = root
        for part in folder.relative_to(root).parts:
            current /= part
            if current.is_symlink() or current.is_junction():
                _fail()
            if current.exists() and not current.is_dir():
                _fail()
    except (OSError, ValueError):
        _fail()


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _file(path, root, *, limit, source=False, verify=True):
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            _fail()
        if not source and (path.is_symlink() or path.is_junction()):
            _fail()
        before = path.stat()
        if (
            not stat.S_ISREG(before.st_mode)
            or (not source and before.st_nlink != 1)
            or not 0 < before.st_size <= limit
        ):
            _fail()
        digest = None
        if verify:
            hasher, count = hashlib.sha256(), 0
            with path.open("rb") as stream:
                if _signature(os.fstat(stream.fileno())) != _signature(before):
                    _fail()
                while block := stream.read(min(1024 * 1024, before.st_size + 1 - count)):
                    count += len(block)
                    if count > before.st_size:
                        _fail()
                    hasher.update(block)
                if count != before.st_size or _signature(os.fstat(stream.fileno())) != _signature(
                    before
                ):
                    _fail()
            digest = hasher.hexdigest()
        if path.resolve() != resolved or _signature(path.stat()) != _signature(before):
            _fail()
        return {"name": path.name, "bytes": before.st_size, "sha256": digest}
    except (OSError, ValueError):
        _fail()


def _read(path, root, limit=MAX_RECEIPT_BYTES, *, source=False):
    try:
        before, resolved = path.stat(), path.resolve()
        _file(path, root, limit=limit, source=source, verify=False)
        with path.open("rb") as stream:
            if _signature(os.fstat(stream.fileno())) != _signature(before):
                _fail()
            data = stream.read(limit + 1)
            if len(data) != before.st_size or _signature(os.fstat(stream.fileno())) != _signature(
                before
            ):
                _fail()
        if path.resolve() != resolved or _signature(path.stat()) != _signature(before):
            _fail()
    except OSError:
        _fail()
    return _json(data, limit)


def _source_manifest(item):
    return sorted(item["source_files"], key=lambda value: value["name"])


def _source(folder, item, root):
    try:
        if not folder.resolve().is_relative_to(root.resolve()) or not folder.is_dir():
            _fail()
        expected = _source_manifest(item)
        if {path.name for path in folder.iterdir()} != {entry["name"] for entry in expected}:
            _fail()
        for entry in expected:
            found = _file(folder / entry["name"], root, limit=entry["bytes"], source=True)
            if found != entry:
                _fail()
        config = _read(folder / "config.json", root, source=True)
        preprocessor = _read(folder / "preprocessor_config.json", root, source=True)
        if (
            not isinstance(config, dict)
            or config.get("model_type") != "whisper"
            or config.get("architectures") != ["WhisperForConditionalGeneration"]
            or config.get("num_mel_bins") != 128
            or "auto_map" in config
            or not isinstance(preprocessor, dict)
            or preprocessor.get("feature_size") != 128
            or preprocessor.get("sampling_rate") != 16000
            or preprocessor.get("chunk_length") != 30
            or "auto_map" in preprocessor
        ):
            _fail()
        for name in ("tokenizer_config.json", "processor_config.json"):
            if (folder / name).exists():
                value = _read(folder / name, root, 1024 * 1024, source=True)
                if not isinstance(value, dict) or "auto_map" in value:
                    _fail()
        return _digest(expected)
    except (OSError, ValueError):
        _fail()


def _recipe():
    try:
        versions = {
            name: importlib.metadata.version(name)
            for name in ("ctranslate2", "transformers", "safetensors", "torch")
        }
    except importlib.metadata.PackageNotFoundError:
        raise PreparationError(
            "The selected runtime lacks the pinned conversion dependencies."
        ) from None
    if any(versions[name].split("+", 1)[0] != CONTRACT[name] for name in versions):
        raise PreparationError("The selected runtime differs from the pinned conversion recipe.")
    recipe = {
        **CONTRACT,
        "contract_sha256": RECIPE_CONTRACT_SHA256,
        "torch_build": versions["torch"],
    }
    return {**recipe, "sha256": _digest(recipe)}


def _validate_receipt(value):
    if not isinstance(value, dict) or set(value) != _BASE:
        _fail()
    if type(value["schema"]) is not int or value["schema"] != 1:
        _fail()
    if not isinstance(value["choice"], str) or value["choice"] not in CHOICES:
        _fail()
    source = value["source"]
    if (
        not isinstance(source, dict)
        or set(source) != {"repo", "revision", "manifest_sha256"}
        or not isinstance(source["repo"], str)
        or not _REPO.fullmatch(source["repo"])
        or not isinstance(source["revision"], str)
        or not _REV.fullmatch(source["revision"])
        or not _sha(source["manifest_sha256"])
    ):
        _fail()
    recipe = value["recipe"]
    if not isinstance(recipe, dict) or set(recipe) != set(CONTRACT) | {
        "contract_sha256",
        "sha256",
        "torch_build",
    }:
        _fail()
    contract = {key: recipe[key] for key in CONTRACT}
    if (
        recipe["id"] != RECIPE_ID
        or any(
            not isinstance(recipe[name], str) or not _VERSION.fullmatch(recipe[name])
            for name in ("ctranslate2", "transformers", "safetensors", "torch", "torch_build")
        )
        or recipe["torch_build"].split("+", 1)[0] != recipe["torch"]
        or recipe["quantization"] != "float16"
        or recipe["weights_format"] != "safetensors"
        or recipe["load_as_float16"] is not False
        or recipe["trust_remote_code"] is not False
        or recipe["local_files_only"] is not True
        or not _sha(recipe["contract_sha256"])
        or recipe["contract_sha256"] != _digest(contract)
        or not _sha(recipe["sha256"])
        or recipe["sha256"] != _digest({key: recipe[key] for key in recipe if key != "sha256"})
    ):
        _fail()
    files, seen, total = value["files"], set(), 0
    if not isinstance(files, list) or len(files) != len(OUTPUT_NAMES):
        _fail()
    for item in files:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "bytes", "sha256"}
            or not isinstance(item["name"], str)
            or item["name"] not in OUTPUT_NAMES
            or item["name"] in seen
            or type(item["bytes"]) is not int
            or not 0 < item["bytes"] <= MAX_OUTPUT_BYTES
            or not _sha(item["sha256"])
        ):
            _fail()
        total += item["bytes"]
        seen.add(item["name"])
    if (
        total > MAX_OUTPUT_BYTES
        or seen != OUTPUT_NAMES
        or [item["name"] for item in files] != sorted(seen)
        or not _sha(value["artifact_sha256"])
        or value["artifact_sha256"]
        != _digest({key: value[key] for key in value if key != "artifact_sha256"})
    ):
        _fail()
    return copy.deepcopy(value)


def validate_receipt(value, *, choice=None, model=None):
    """Pure unbound marker validation for remote inventory and historical checks."""
    receipt = _validate_receipt(value)
    if choice is not None and receipt["choice"] != choice:
        _fail()
    if model is not None and (
        not isinstance(model, (tuple, list))
        or len(model) != 2
        or (receipt["source"]["repo"], receipt["source"]["revision"]) != tuple(model)
    ):
        _fail()
    return receipt


def validate_evidence(value, *, choice=None, model=None, request_sha256=None, input_sha256=None):
    """Validate a captured receipt only; current registry, artifacts and imports stay untouched."""
    if not isinstance(value, dict) or set(value) != _BASE | {"request_sha256", "input_sha256"}:
        _fail()
    receipt = validate_receipt({key: value[key] for key in _BASE}, choice=choice, model=model)
    if any(not _sha(value[key]) for key in ("input_sha256", "request_sha256")):
        _fail()
    if request_sha256 is not None and value["request_sha256"] != request_sha256:
        _fail()
    if input_sha256 is not None and value["input_sha256"] != input_sha256:
        _fail()
    return {
        **receipt,
        "input_sha256": value["input_sha256"],
        "request_sha256": value["request_sha256"],
    }


def evidence(choice, receipt, *, request_sha256, input_sha256):
    return validate_evidence(
        {**receipt, "request_sha256": request_sha256, "input_sha256": input_sha256}, choice=choice
    )


def prepared(choice, cache=None, verify=True):
    """Cached-only inspection. False checks metadata/sizes; True also verifies actual bytes."""
    root, item = _cache_root(cache), _definition(choice)
    folder = root / relative_folder(choice)
    _owned_folder(folder, root)
    try:
        if {path.name for path in folder.iterdir()} != OUTPUT_NAMES | {MARKER}:
            _fail()
        receipt = _validate_receipt(_read(folder / MARKER, root))
        if (
            receipt["choice"] != choice
            or (receipt["source"]["repo"], receipt["source"]["revision"]) != source_identity(choice)
            or receipt["source"]["manifest_sha256"] != _digest(_source_manifest(item))
            or receipt["recipe"]["contract_sha256"] != RECIPE_CONTRACT_SHA256
        ):
            _fail()
        for entry in receipt["files"]:
            found = _file(folder / entry["name"], root, limit=entry["bytes"], verify=verify)
            if found["bytes"] != entry["bytes"] or (verify and found != entry):
                _fail()
        expected = {entry["name"]: entry for entry in _source_manifest(item)}
        files = {entry["name"]: entry for entry in receipt["files"]}
        for name in ("tokenizer.json", "preprocessor_config.json"):
            if files[name] != expected[name]:
                _fail()
        if any(
            files["SOURCE_README.md"][key] != expected["README.md"][key]
            for key in ("bytes", "sha256")
        ):
            _fail()
        manifest = {
            "repo": item["repo"],
            "revision": item["revision"],
            "files": _source_manifest(item),
        }
        if _canonical(_read(folder / "SOURCE_MANIFEST.json", root)) != _canonical(manifest):
            _fail()
        return folder, receipt
    except (OSError, ValueError):
        _fail()


def _download_source(item, root):
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=item["repo"],
            revision=item["revision"],
            cache_dir=str(root),
            token=False,
            allow_patterns=[entry["name"] for entry in _source_manifest(item)],
        )
    )


def _convert(source, output):
    from ctranslate2.converters import TransformersConverter

    class LocalWhisperConverter(TransformersConverter):
        def load_model(self, model_class, model_name_or_path, **kwargs):
            return model_class.from_pretrained(
                model_name_or_path,
                **{
                    **kwargs,
                    "local_files_only": True,
                    "use_safetensors": True,
                    "trust_remote_code": False,
                    "low_cpu_mem_usage": False,
                    "token": False,
                },
            )

        def load_tokenizer(self, tokenizer_class, model_name_or_path, **kwargs):
            return tokenizer_class.from_pretrained(
                model_name_or_path,
                **{
                    **kwargs,
                    "local_files_only": True,
                    "trust_remote_code": False,
                    "token": False,
                },
            )

    LocalWhisperConverter(
        str(source),
        copy_files=["tokenizer.json", "preprocessor_config.json"],
        load_as_float16=False,
        low_cpu_mem_usage=False,
        trust_remote_code=False,
    ).convert(str(output), quantization="float16", force=False)


def prepare(choice, cache=None):
    """Explicit preparation only: public pinned download, local conversion, immutable commit."""
    root, item = _cache_root(cache), _definition(choice)
    folder = root / relative_folder(choice)
    _owned_folder(folder, root)
    if folder.exists():
        return prepared(choice, root)
    recipe = _recipe()
    folder.parent.mkdir(parents=True, exist_ok=True)
    _owned_folder(folder, root)
    lock = folder.parent / (folder.name + ".preparing")
    descriptor = None
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        lock_info = os.fstat(descriptor)
    except OSError:
        raise PreparationError(
            "Danish model preparation is already reserved; preserve its cache."
        ) from None
    try:
        if folder.exists():
            return prepared(choice, root)
        source = _download_source(item, root)
        source_hash = _source(source, item, root)
        attempt = folder.parent / (".conversion-" + uuid.uuid4().hex)
        attempt.mkdir()
        output = attempt / "model"
        _convert(source, output)
        # Do not publish a derivation of a source that changed while conversion ran.
        if _source(source, item, root) != source_hash:
            _fail()
        shutil.copyfile(source / "README.md", output / "SOURCE_README.md")
        manifest = {
            "repo": item["repo"],
            "revision": item["revision"],
            "files": _source_manifest(item),
        }
        (output / "SOURCE_MANIFEST.json").write_text(_canonical(manifest), encoding="utf-8")
        if {path.name for path in output.iterdir()} != OUTPUT_NAMES:
            _fail()
        files = [
            _file(output / name, root, limit=MAX_OUTPUT_BYTES) for name in sorted(OUTPUT_NAMES)
        ]
        receipt = {
            "schema": 1,
            "choice": choice,
            "source": {
                "repo": item["repo"],
                "revision": item["revision"],
                "manifest_sha256": source_hash,
            },
            "recipe": recipe,
            "files": files,
        }
        receipt["artifact_sha256"] = _digest(receipt)
        _validate_receipt(receipt)
        folder.mkdir()  # Exclusive publication reservation; never overwrite another generation.
        for name in sorted(OUTPUT_NAMES):
            (output / name).rename(folder / name)
        with (folder / MARKER).open("xb") as stream:
            stream.write(_canonical(receipt).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        return prepared(choice, root)
    except PreparationError:
        raise
    except Exception:
        raise PreparationError(
            "Danish model conversion failed; its incomplete artifacts are preserved."
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
            try:
                if _signature(lock.lstat()) == _signature(lock_info):
                    lock.unlink()
            except OSError:
                pass
