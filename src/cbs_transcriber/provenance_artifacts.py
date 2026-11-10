"""Producer-time byte observations; no persistence, capture backfill or restore authority.

The caller owns the managed producer directory and must durably finish files before capture,
holding its producer/relocation guard until checkpoint publication. These receipts observe bytes
at that boundary; they do not prove provider success, continued retention or reproducibility.
Historical validation is pure and compares references supplied by the trusted persistence layer.
The producer must classify its files honestly: a normalized SDK dump is output, not original
wire-response bytes, and retained request/environment files must never contain credentials.
"""

import copy
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

FORMAT = "cbs-producer-artifacts-v1"
ENVIRONMENT_FORMAT = "cbs-processing-environment-v1"
LIMITS = {
    "request": 4 * 1024 * 1024,
    "raw_response": 32 * 1024 * 1024,
    "output": 32 * 1024 * 1024,
    "environment": 64 * 1024,
}
STAGES = {"preparation", "transcription", "alignment", "diarization"}
PACKAGES = {
    "cbs-transcriber",
    "whisperx",
    "faster-whisper",
    "ctranslate2",
    "torch",
    "torchaudio",
    "transformers",
    "pyannote.audio",
    "numpy",
    "openai",
    "ucloud-workflow",
    "imageio-ffmpeg",
}
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-f0-9]{32}")
_VERSION = re.compile(
    r"[0-9]+(?:\.[0-9]+){0,3}(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?"
    r"(?:\.dev[0-9]+)?(?:\+(?:cpu|cu[0-9]{2,3}|rocm[0-9]+(?:\.[0-9]+)*))?"
)


def _invalid():
    raise ValueError("The processing artifact evidence is missing, changed or invalid.") from None


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _choice(value, choices):
    return isinstance(value, str) and value in choices


def _fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        _invalid()


def _version(value):
    return isinstance(value, str) and len(value) <= 64 and _VERSION.fullmatch(value) is not None


def validate_environment(value):
    """Allow actual worker facts only; null means that the observation was not captured."""
    _fields(value, {"format", "python", "platform", "packages", "device", "code", "recipe"})
    if value["format"] != ENVIRONMENT_FORMAT:
        _invalid()
    if value["python"] is not None:
        item = value["python"]
        _fields(item, {"implementation", "version"})
        if not _choice(item["implementation"], {"cpython", "pypy"}) or not _version(
            item["version"]
        ):
            _invalid()
    if value["platform"] is not None:
        item = value["platform"]
        _fields(item, {"system", "machine"})
        if not _choice(item["system"], {"Windows", "Darwin", "Linux"}) or not _choice(
            item["machine"],
            {
                "x86_64",
                "AMD64",
                "arm64",
                "aarch64",
                "x86",
            },
        ):
            _invalid()
    packages = value["packages"]
    if packages is not None and (
        not isinstance(packages, dict)
        or packages.keys() - PACKAGES
        or any(not _version(item) for item in packages.values())
    ):
        _invalid()
    if value["device"] is not None:
        item = value["device"]
        _fields(item, {"type", "index", "memory_bytes"})
        if (
            not _choice(item["type"], {"cpu", "cuda", "mps"})
            or (
                item["index"] is not None
                and (type(item["index"]) is not int or not 0 <= item["index"] <= 1023)
            )
            or (
                item["memory_bytes"] is not None
                and (type(item["memory_bytes"]) is not int or not 0 < item["memory_bytes"] < 2**63)
            )
            or (item["type"] != "cuda" and item["index"] is not None)
        ):
            _invalid()
    if value["code"] is not None:
        item = value["code"]
        _fields(item, {"application_sha256", "worker_sha256", "lock_sha256"})
        if any(digest is not None and not _sha(digest) for digest in item.values()):
            _invalid()
    if value["recipe"] is not None:
        item = value["recipe"]
        _fields(item, {"id", "compute_type"})
        if not _choice(
            item["id"],
            {
                "whisperx-transcription-v1",
                "whisperx-alignment-v1",
                "whisperx-diarization-v1",
                "openai-audio-v1",
                "ucloud-native-transcriber-v1",
                "mono-16000-pcm16-v1",
            },
        ) or (
            item["compute_type"] is not None
            and not _choice(
                item["compute_type"],
                {
                    "float32",
                    "float16",
                    "bfloat16",
                    "int8",
                    "int8_float32",
                    "int8_float16",
                },
            )
        ):
            _invalid()
    return copy.deepcopy(value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _environment_bytes(data):
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
        return validate_environment(value)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _invalid()


def _regular(info):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        _invalid()
    return info


def _owned(path, root):
    # The trusted caller chooses the owned root, never an imported artifact dictionary.
    if not path.is_absolute() or not root.is_absolute() or ".." in (*path.parts, *root.parts):
        _invalid()
    relative = path.relative_to(root)
    if not relative.parts:
        _invalid()
    directories = [root, *(root / parent for parent in reversed(relative.parents) if parent.parts)]
    identity = []
    for directory in directories:
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or directory.is_symlink()
            or directory.is_junction()
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            _invalid()
        identity.append((info.st_dev, info.st_ino))
    _regular(path.lstat())
    if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
        _invalid()
    return identity


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@dataclass(frozen=True, init=False)
class CapturedArtifact:
    """In-process observation; serialized dictionaries are not new capture inputs."""

    _json: bytes

    def receipt(self):
        return json.loads(self._json)


def capture_file(path, managed_root, *, role, format):
    """Hash bounded exact bytes after a durable producer write; never retain their contents."""
    if not _choice(role, LIMITS) or not _choice(format, {"json", "jsonl", "text", "bytes"}):
        _invalid()
    if role == "environment" and format != "json":
        _invalid()
    try:
        path, root = Path(path), Path(managed_root)
        parents = _owned(path, root)
        before = _regular(path.lstat())
        if not 0 <= before.st_size <= LIMITS[role]:
            _invalid()
        digest, count, environment = hashlib.sha256(), 0, bytearray()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            # Keep descriptor ownership if wrapper allocation/close fails too.
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                opened = _regular(os.fstat(stream.fileno()))
                if _signature(before) != _signature(opened):
                    _invalid()
                while block := stream.read(min(1024 * 1024, opened.st_size + 1 - count)):
                    count += len(block)
                    if count > opened.st_size:
                        _invalid()
                    digest.update(block)
                    if role == "environment":
                        environment.extend(block)
                final_open = _regular(os.fstat(stream.fileno()))
        finally:
            os.close(descriptor)
        after = _regular(path.lstat())
        if (
            count != opened.st_size
            or _signature(before) != _signature(after)
            or _signature(opened) != _signature(final_open)
            # Windows path and descriptor ctime can differ; compare like observations.
            or before.st_ctime_ns != after.st_ctime_ns
            or opened.st_ctime_ns != final_open.st_ctime_ns
            or parents != _owned(path, root)
        ):
            _invalid()
        receipt = {"role": role, "sha256": digest.hexdigest(), "bytes": count, "format": format}
        if role == "environment":
            receipt["environment"] = _environment_bytes(bytes(environment))
        captured = object.__new__(CapturedArtifact)
        object.__setattr__(captured, "_json", json.dumps(receipt, sort_keys=True).encode("utf-8"))
        return captured
    except (OSError, TypeError, ValueError):
        _invalid()


def _producer(value):
    _fields(value, {"recording_id", "job_id", "request_sha256", "stage"})
    if (
        any(
            not isinstance(value[key], str) or not _ID.fullmatch(value[key])
            for key in ("recording_id", "job_id")
        )
        or not _sha(value["request_sha256"])
        or not _choice(value["stage"], STAGES)
    ):
        _invalid()
    return copy.deepcopy(value)


def _creation(value):
    _fields(value, {"result_id", "version_id", "payload_sha256"})
    if any(
        not isinstance(value[key], str) or not _ID.fullmatch(value[key])
        for key in ("result_id", "version_id")
    ) or not _sha(value["payload_sha256"]):
        _invalid()
    return copy.deepcopy(value)


def _receipt(value, role):
    fields = {"role", "sha256", "bytes", "format"}
    if role == "environment":
        fields.add("environment")
    _fields(value, fields)
    if (
        value["role"] != role
        or not _sha(value["sha256"])
        or type(value["bytes"]) is not int
        or not 0 <= value["bytes"] <= LIMITS[role]
        or not _choice(value["format"], {"json", "jsonl", "text", "bytes"})
        or (role == "environment" and value["format"] != "json")
    ):
        _invalid()
    if role == "environment":
        validate_environment(value["environment"])
    return copy.deepcopy(value)


def make_checkpoint(captures, *, producer):
    """Make a new producer checkpoint from this process's observations only."""
    if not isinstance(captures, (tuple, list)) or not 1 <= len(captures) <= len(LIMITS):
        _invalid()
    artifacts = dict.fromkeys(LIMITS, "not_captured")
    for captured in captures:
        if type(captured) is not CapturedArtifact:
            _invalid()
        value = captured.receipt()
        role = value.get("role")
        if role not in LIMITS or artifacts[role] != "not_captured":
            _invalid()
        artifacts[role] = _receipt(value, role)
    return validate_checkpoint(
        {"format": FORMAT, "producer": _producer(producer), "artifacts": artifacts},
        producer=producer,
    )


def validate_checkpoint(value, *, producer):
    """Pure historical validation; expected references must come from trusted stored rows."""
    _fields(value, {"format", "producer", "artifacts"})
    if value["format"] != FORMAT or _producer(value["producer"]) != _producer(producer):
        _invalid()
    _fields(value["artifacts"], LIMITS)
    captured = 0
    for role, receipt in value["artifacts"].items():
        if receipt != "not_captured":
            _receipt(receipt, role)
            captured += 1
    if not captured:
        _invalid()
    request = value["artifacts"]["request"]
    if request != "not_captured" and request["sha256"] != producer["request_sha256"]:
        _invalid()
    return copy.deepcopy(value)


def bind_creation(checkpoint, *, producer, creation):
    """Bind a preserved checkpoint to the exact creation row in its committing transaction."""
    return {**validate_checkpoint(checkpoint, producer=producer), "creation": _creation(creation)}


def validate_bound(value, *, producer, creation):
    """No lookup: the caller must prove job/result ownership and creation-row identity."""
    _fields(value, {"format", "producer", "artifacts", "creation"})
    if _creation(value["creation"]) != _creation(creation):
        _invalid()
    validate_checkpoint(
        {key: item for key, item in value.items() if key != "creation"}, producer=producer
    )
    return copy.deepcopy(value)


def public_projection(value, *, producer, creation):
    """Only hashes, sizes and safe observations; no paths, filenames, IDs or current checks."""
    verified = validate_bound(value, producer=producer, creation=creation)
    return {
        "format": FORMAT,
        "artifacts": verified["artifacts"],
        "retained_files": "not_checked",
    }
