"""Synthetic Danish source conversion, artifact integrity and pure provenance checks."""

import builtins
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import danish_models as dm


def digest(data):
    return hashlib.sha256(data).hexdigest()


def recipe():
    value = {
        **dm.CONTRACT,
        "contract_sha256": dm.RECIPE_CONTRACT_SHA256,
        "torch_build": "2.8.0+cpu",
    }
    return {**value, "sha256": dm._digest(value)}


@pytest.fixture
def conversion(tmp_path, monkeypatch):
    cache = tmp_path / "hub"
    source = cache / "models--synthetic" / "snapshots" / ("1" * 40)
    source.mkdir(parents=True)
    payloads = {
        "config.json": json.dumps(
            {
                "model_type": "whisper",
                "architectures": ["WhisperForConditionalGeneration"],
                "num_mel_bins": 128,
            }
        ).encode(),
        "preprocessor_config.json": json.dumps(
            {"feature_size": 128, "sampling_rate": 16000, "chunk_length": 30}
        ).encode(),
        "tokenizer.json": b'{"synthetic": "tokenizer"}',
        "tokenizer_config.json": b'{"tokenizer_class":"WhisperTokenizer"}',
        "README.md": "Synthetic Danish model. Attribution: Test authors. æøå".encode(),
        "model.safetensors": b"synthetic safe weight bytes",
    }
    for name, payload in payloads.items():
        (source / name).write_bytes(payload)
    actual = dm.models()
    item = copy.deepcopy(actual["edda-turbo"])
    item["source_files"] = [
        {"name": name, "bytes": len(data), "sha256": digest(data)}
        for name, data in sorted(payloads.items())
    ]
    actual["edda-turbo"] = item
    monkeypatch.setattr(dm, "_models", lambda: actual)
    monkeypatch.setattr(dm, "_recipe", recipe)
    calls = []

    def download(definition, root):
        calls.append(("download", definition["repo"], root))
        return source

    def convert(raw, output):
        calls.append(("convert", raw, output))
        assert raw == source
        assert not output.exists()
        output.mkdir()
        (output / "model.bin").write_bytes(b"Synthetic CT2 artifact")
        (output / "config.json").write_bytes(b"{}")
        (output / "vocabulary.json").write_bytes(b"[]")
        for name in ("tokenizer.json", "preprocessor_config.json"):
            shutil.copyfile(raw / name, output / name)

    monkeypatch.setattr(dm, "_download_source", download)
    monkeypatch.setattr(dm, "_convert", convert)
    return cache, source, item, calls


def test_catalog_is_pinned_without_loading_conversion_dependencies(monkeypatch):
    dm._models.cache_clear()
    monkeypatch.setattr(dm.importlib.metadata, "version", lambda *a: pytest.fail("runtime lookup"))
    models = dm.models()
    assert set(models) == {"edda-turbo", "edda-large", "roest-v3"}
    assert dm.source_identity("edda-turbo") == (
        "danish-foundation-models/edda-v0.2",
        "698f8d04b5970f963d31c4079c05a76fa7f4f5bb",
    )
    assert dm.source_identity("roest-v3")[0] == "CoRal-project/roest-v3-whisper-1.5b"


def test_import_and_catalog_use_standard_library_only(monkeypatch):
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".", 1)[0] not in {
            "torch",
            "transformers",
            "ctranslate2",
            "safetensors",
            "huggingface_hub",
        }
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    spec = importlib.util.spec_from_file_location("isolated_danish_models", dm.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    models = module.models()
    assert set(models) == set(module.CHOICES)
    assert all(value["mel_bins"] == 128 for value in models.values())
    assert all(
        not file["name"].endswith((".py", ".bin"))
        for value in models.values()
        for file in value["source_files"]
    )
    models["edda-turbo"]["revision"] = "changed"
    assert module.source_identity("edda-turbo")[1] != "changed"


def test_explicit_conversion_publishes_verified_artifact_last_and_reuses(conversion):
    cache, source, item, calls = conversion
    before = {path.name: path.read_bytes() for path in source.iterdir()}
    folder, receipt = dm.prepare("edda-turbo", cache)
    assert folder == cache / dm.relative_folder("edda-turbo")
    assert {path.name for path in folder.iterdir()} == dm.OUTPUT_NAMES | {dm.MARKER}
    assert dm.prepared("edda-turbo", cache) == (folder, receipt)
    assert dm.prepare("edda-turbo", cache) == (folder, receipt)
    assert [call[0] for call in calls] == ["download", "convert"]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before
    assert receipt["source"] == {
        "repo": item["repo"],
        "revision": item["revision"],
        "manifest_sha256": dm._digest(item["source_files"]),
    }
    assert (folder / "SOURCE_README.md").read_bytes() == before["README.md"]
    assert (folder / "preprocessor_config.json").read_bytes() == before["preprocessor_config.json"]


def test_missing_cache_never_downloads_or_creates_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "_download_source", lambda *a: pytest.fail("implicit download"))
    cache = tmp_path / "not-created"
    with pytest.raises(dm.PreparationError):
        dm.prepared("edda-turbo", cache)
    assert not cache.exists()


def test_cache_location_honors_modern_and_legacy_hf_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "modern"))
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(tmp_path / "legacy"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    assert dm._cache_root(None) == tmp_path / "modern"
    assert dm._cache_root(tmp_path / "explicit") == tmp_path / "explicit"
    monkeypatch.delenv("HF_HUB_CACHE")
    assert dm._cache_root(None) == tmp_path / "legacy"
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE")
    assert dm._cache_root(None) == tmp_path / "home" / "hub"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "damage",
    [
        "missing-marker",
        "changed-artifact",
        "extra-file",
        "missing-tokenizer",
        "incomplete-marker",
        "linked-file",
        "wrong-raw-manifest",
    ],
)
def test_changed_or_partial_artifacts_are_not_reprepared_over(conversion, damage):
    cache, _, _, calls = conversion
    folder, _ = dm.prepare("edda-turbo", cache)
    if damage == "missing-marker":
        (folder / dm.MARKER).unlink()
    elif damage == "changed-artifact":
        data = (folder / "model.bin").read_bytes()
        (folder / "model.bin").write_bytes(b"X" * len(data))
    elif damage == "extra-file":
        (folder / "injected.py").write_text("not executed")
    elif damage == "missing-tokenizer":
        (folder / "tokenizer.json").unlink()
    elif damage == "incomplete-marker":
        (folder / dm.MARKER).write_bytes(b"{")
    elif damage == "linked-file":
        other = folder.parent / "outside.bin"
        os.link(folder / "model.bin", other)
    else:
        (folder / "SOURCE_MANIFEST.json").write_text("{}")
    before = [(path.name, path.stat().st_size) for path in folder.iterdir()]
    with pytest.raises(dm.PreparationError):
        dm.prepared("edda-turbo", cache)
    with pytest.raises(dm.PreparationError):
        dm.prepare("edda-turbo", cache)
    assert [(path.name, path.stat().st_size) for path in folder.iterdir()] == before
    assert len(calls) == 2


def test_failed_conversion_keeps_source_and_never_commits(conversion, monkeypatch):
    cache, source, _, _ = conversion

    def failed(raw, output):
        output.mkdir()
        (output / "model.bin").write_bytes(b"partial")
        raise OSError("private path must not be shown")

    monkeypatch.setattr(dm, "_convert", failed)
    with pytest.raises(dm.PreparationError, match="incomplete artifacts") as error:
        dm.prepare("edda-turbo", cache)
    assert "private path" not in str(error.value)
    assert source.is_dir()
    assert not (cache / dm.relative_folder("edda-turbo")).exists()
    assert list(cache.rglob(".conversion-*"))
    assert not list(cache.rglob("*.preparing"))


@pytest.mark.parametrize("damage", ["weight", "mel", "remote-code", "extra-code", "outside-link"])
def test_source_must_match_pinned_closed_safe_inventory(conversion, monkeypatch, damage):
    cache, source, item, _ = conversion
    if damage == "weight":
        (source / "model.safetensors").write_bytes(b"changed")
    elif damage in {"mel", "remote-code"}:
        config = json.loads((source / "config.json").read_text())
        config.update({"num_mel_bins": 80} if damage == "mel" else {"auto_map": {}})
        data = json.dumps(config).encode()
        (source / "config.json").write_bytes(data)
        next(v for v in item["source_files"] if v["name"] == "config.json").update(
            bytes=len(data), sha256=digest(data)
        )
    elif damage == "extra-code":
        (source / "remote_model.py").write_bytes(b"not executed")
    else:
        target = cache.parent / "outside-model"
        target.write_bytes((source / "model.safetensors").read_bytes())
        (source / "model.safetensors").unlink()
        try:
            (source / "model.safetensors").symlink_to(target)
        except OSError:
            pytest.skip("Windows account cannot create symlinks")
    monkeypatch.setattr(dm, "_convert", lambda *a: pytest.fail("unsafe source reached converter"))
    with pytest.raises(dm.PreparationError):
        dm.prepare("edda-turbo", cache)


def test_normal_hf_blob_symlinks_are_allowed_inside_hub(conversion):
    cache, source, _, _ = conversion
    blob = cache / "models--synthetic" / "blobs" / "pinned"
    blob.parent.mkdir()
    shutil.move(source / "model.safetensors", blob)
    try:
        (source / "model.safetensors").symlink_to(blob)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    assert dm.prepare("edda-turbo", cache)[1]["choice"] == "edda-turbo"


def test_captured_conversion_evidence_is_file_and_runtime_free(conversion, monkeypatch):
    cache, _, _, _ = conversion
    _, receipt = dm.prepare("edda-turbo", cache)
    bound = dm.evidence("edda-turbo", receipt, request_sha256="a" * 64, input_sha256="b" * 64)
    monkeypatch.setattr(dm, "_models", lambda: pytest.fail("today's registry"))
    monkeypatch.setattr(dm.importlib.metadata, "version", lambda *a: pytest.fail("today's runtime"))
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("artifact access"))
    assert dm.validate_evidence(bound) == bound
    assert dm.validate_receipt(receipt) == receipt


@pytest.mark.parametrize("field", ["choice", "model", "request_sha256", "input_sha256"])
def test_evidence_refuses_other_frozen_input_or_model(conversion, field):
    cache, _, _, _ = conversion
    _, receipt = dm.prepare("edda-turbo", cache)
    bound = dm.evidence("edda-turbo", receipt, request_sha256="a" * 64, input_sha256="b" * 64)
    value = {
        "choice": "edda-large",
        "model": ("other/model", "f" * 40),
        "request_sha256": "c" * 64,
        "input_sha256": "d" * 64,
    }[field]
    with pytest.raises(dm.PreparationError):
        dm.validate_evidence(bound, **{field: value})


@pytest.mark.parametrize(
    "damage",
    [
        "bool-schema",
        "wrong-recipe-hash",
        "unsafe-recipe",
        "extra-path",
        "duplicate-file",
        "file-size-bool",
        "invalid-hash",
        "unbound",
    ],
)
def test_malformed_evidence_is_refused(conversion, damage):
    cache, _, _, _ = conversion
    _, receipt = dm.prepare("edda-turbo", cache)
    bound = dm.evidence("edda-turbo", receipt, request_sha256="a" * 64, input_sha256="b" * 64)
    if damage == "bool-schema":
        bound["schema"] = True
    elif damage == "wrong-recipe-hash":
        bound["recipe"]["sha256"] = "f" * 64
    elif damage == "unsafe-recipe":
        bound["recipe"]["trust_remote_code"] = True
    elif damage == "extra-path":
        bound["private_path"] = "should never project"
    elif damage == "duplicate-file":
        bound["files"][0] = copy.deepcopy(bound["files"][1])
    elif damage == "file-size-bool":
        bound["files"][0]["bytes"] = True
    elif damage == "invalid-hash":
        bound["source"]["revision"] = "main"
    else:
        bound.pop("input_sha256")
    with pytest.raises(dm.PreparationError):
        dm.validate_evidence(bound)


def test_marker_json_rejects_duplicates_and_excessive_depth():
    with pytest.raises(dm.PreparationError):
        dm._json(b'{"schema":1,"schema":1}')
    with pytest.raises(dm.PreparationError):
        dm._json(b"[" * 13 + b"]" * 13)
    assert dm._json(b'{"literal":"[[[[]]]]"}') == {"literal": "[[[[]]]]"}


def test_bounded_read_rejects_replaced_open_file(tmp_path, monkeypatch):
    marker = tmp_path / "marker.json"
    replacement = tmp_path / "other.json"
    marker.write_bytes(b"{}")
    replacement.write_bytes(b"{}")
    original_open = Path.open

    def swapped(path, *args, **kwargs):
        return original_open(replacement if path == marker else path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", swapped)
    with pytest.raises(dm.PreparationError):
        dm._read(marker, tmp_path)


def test_recipe_records_actual_torch_build_without_changing_remote_location(monkeypatch):
    before = dm.relative_folder("edda-turbo")
    versions = {
        key: value
        for key, value in dm.CONTRACT.items()
        if key in {"torch", "ctranslate2", "transformers", "safetensors"}
    }
    monkeypatch.setattr(dm.importlib.metadata, "version", versions.__getitem__)
    first = dm._recipe()
    versions["torch"] = "2.8.0+cu128"
    second = dm._recipe()
    assert first["contract_sha256"] == second["contract_sha256"]
    assert first["sha256"] != second["sha256"]
    assert dm.relative_folder("edda-turbo") == before


def test_explicit_download_is_finite_pinned_and_anonymous(tmp_path, monkeypatch):
    import huggingface_hub

    observed = []
    monkeypatch.setattr(
        huggingface_hub,
        "snapshot_download",
        lambda **kwargs: observed.append(kwargs) or str(tmp_path),
    )
    item = dm.models()["edda-large"]
    assert dm._download_source(item, tmp_path) == tmp_path
    assert observed == [
        {
            "repo_id": item["repo"],
            "revision": item["revision"],
            "cache_dir": str(tmp_path),
            "token": False,
            "allow_patterns": [f["name"] for f in item["source_files"]],
        }
    ]


def test_converter_forces_local_safe_loads_and_supported_constructor(tmp_path, monkeypatch):
    observed = []

    class Loader:
        @staticmethod
        def from_pretrained(path, **kwargs):
            observed.append((path, kwargs))

    class Converter:
        def __init__(
            self, source, *, copy_files, load_as_float16, low_cpu_mem_usage, trust_remote_code
        ):
            self.source = source
            assert copy_files == ["tokenizer.json", "preprocessor_config.json"]
            assert load_as_float16 is low_cpu_mem_usage is trust_remote_code is False

        def convert(self, output, *, quantization, force):
            assert quantization == "float16" and force is False
            self.load_model(Loader, self.source, low_cpu_mem_usage=True, trust_remote_code=True)
            self.load_tokenizer(Loader, self.source, trust_remote_code=True, token="untrusted")

    monkeypatch.setitem(
        sys.modules, "ctranslate2.converters", SimpleNamespace(TransformersConverter=Converter)
    )
    dm._convert(tmp_path / "raw", tmp_path / "derived")
    assert observed == [
        (
            str(tmp_path / "raw"),
            {
                "local_files_only": True,
                "use_safetensors": True,
                "trust_remote_code": False,
                "low_cpu_mem_usage": False,
                "token": False,
            },
        ),
        (
            str(tmp_path / "raw"),
            {
                "local_files_only": True,
                "trust_remote_code": False,
                "token": False,
            },
        ),
    ]


@pytest.mark.skipif(
    os.environ.get("CBS_TRANSCRIBER_TEST_DANISH_CONVERSION") != "1",
    reason="Explicit tiny conversion proof; set CBS_TRANSCRIBER_TEST_DANISH_CONVERSION=1",
)
def test_real_tiny_whisper_conversion_preserves_128_mel_without_download(tmp_path, monkeypatch):
    """Real converter, generated <1 MB weights; no model inference or network."""
    pytest.importorskip("ctranslate2")
    pytest.importorskip("torch")
    from transformers import (
        WhisperConfig,
        WhisperFeatureExtractor,
        WhisperForConditionalGeneration,
        WhisperTokenizerFast,
    )

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "isolated-cache"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network attempted"))
    source = tmp_path / "hub" / "synthetic-whisper"
    source.mkdir(parents=True)
    vocabulary = {
        "<|endoftext|>": 0,
        "<|startoftranscript|>": 1,
        "<|da|>": 2,
        "<|transcribe|>": 3,
        "<|notimestamps|>": 4,
        "a": 5,
    }
    (source / "vocab.json").write_text(json.dumps(vocabulary), encoding="utf-8")
    (source / "merges.txt").write_text("#version: 0.2\n", encoding="utf-8")
    tokenizer = WhisperTokenizerFast(
        vocab_file=str(source / "vocab.json"), merges_file=str(source / "merges.txt")
    )
    tokenizer.save_pretrained(source)
    WhisperFeatureExtractor(feature_size=128).save_pretrained(source)
    config = WhisperConfig(
        vocab_size=len(tokenizer),
        d_model=8,
        encoder_layers=1,
        decoder_layers=1,
        encoder_attention_heads=1,
        decoder_attention_heads=1,
        encoder_ffn_dim=16,
        decoder_ffn_dim=16,
        max_source_positions=1500,
        max_target_positions=448,
        num_mel_bins=128,
        pad_token_id=0,
        bos_token_id=0,
        eos_token_id=0,
        decoder_start_token_id=1,
    )
    model = WhisperForConditionalGeneration(config)
    model.generation_config.suppress_tokens = []
    model.generation_config.begin_suppress_tokens = []
    model.save_pretrained(source, safe_serialization=True)
    assert (source / "model.safetensors").stat().st_size < 1024 * 1024
    (source / "README.md").write_text("Generated tiny conversion test; no trained weights.")
    definitions = dm.models()
    item = definitions["edda-turbo"]
    item["source_files"] = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}
        for path in sorted(source.iterdir())
    ]
    monkeypatch.setattr(dm, "_models", lambda: definitions)
    monkeypatch.setattr(dm, "_download_source", lambda *a: source)
    folder, receipt = dm.prepare("edda-turbo", cache=source.parent)
    assert dm.prepared("edda-turbo", cache=source.parent) == (folder, receipt)
    assert (folder / "tokenizer.json").read_bytes() == (source / "tokenizer.json").read_bytes()
    assert json.loads((folder / "preprocessor_config.json").read_text())["feature_size"] == 128
    assert 0 < (folder / "model.bin").stat().st_size < 1024 * 1024
