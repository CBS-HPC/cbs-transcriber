"""Exact Runtime publication/collection observations, separate from inference claims.

The existing fenced attempt writer persists these documents. File observations finish
outside its transaction. Historical validation and inventories use trusted stored attempt
rows only; remote preparation, original media and worker environments remain distinct.
"""

import copy
import hashlib
import json
import re
import uuid

from . import local_asr_capture as local
from . import provenance_artifacts as artifacts

POLICY = "runtime-artifact-v1"
FORMAT = "cbs-runtime-artifacts-v1"
MAX_BYTES = 65536
FILES = {
    "transcription.json": ("transcription", "transcription"),
    "retranscription.json": ("retranscription", "transcription"),
    "alignment.json": ("alignment", "alignment"),
    "diarization.json": ("diarization", "diarization"),
    "outcome.json": ("task_outcome", "preparation"),
    "audio.preparation.json": ("audio_preparation", "preparation"),
}
SOURCE_FORMATS = {
    "wav",
    "mp3",
    "mp4",
    "m4a",
    "flac",
    "ogg",
    "webm",
    "mpeg",
    "mpga",
    "mov",
    "mkv",
    "aac",
    "wma",
    "aiff",
    "aif",
    "mpg",
    "avi",
}


def _invalid():
    raise ValueError("The Runtime artifact evidence changed or is invalid. Preserve its files.")


def _encoded(value):
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            _invalid()
        return encoded
    except (TypeError, ValueError, RecursionError):
        _invalid()


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _file(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"size", "sha256"}
        or type(value["size"]) is not int
        or not 0 <= value["size"] < 2**63
        or not _sha(value["sha256"])
    ):
        _invalid()
    return value


def _input_name(name):
    return name in {
        "request.json",
        "audio.wav",
        "original.input.json",
        "transcription.json",
        "retranscription.json",
        "alignment.json",
        "diarization.json",
    } or (
        isinstance(name, str)
        and name.startswith("audio.source.")
        and name.removeprefix("audio.source.") in SOURCE_FORMATS
    )


def eligible(params):
    return (
        isinstance(params, dict)
        and isinstance(params.get("enhancement"), dict)
        and params["enhancement"].get("execution") == "ucloud"
        and params.get("transcription") is True
        and params["enhancement"].get("transcribe") is True
        and params["enhancement"].get("retranscribe") is False
        and isinstance(params.get("ucloud_api"), dict)
        and isinstance(params["ucloud_api"].get("batch_plan"), dict)
        and not any(
            key in params
            for key in (
                "realign",
                "timing_units",
                "source_snapshot",
                "segment_id",
                "segment_ids",
                "retranscription",
                "native_batch_id",
                "cloud_settings",
            )
        )
    )


def freeze(params):
    """Only explicit fresh admission invokes this, never insertion, retry or recovery."""
    if not eligible(params):
        return params
    if params.get("runtime_artifact_policy") not in {None, POLICY}:
        _invalid()
    return {**params, "runtime_artifact_policy": POLICY}


def assert_params_preserved(previous, proposed):
    if previous.get("runtime_artifact_policy") != proposed.get("runtime_artifact_policy"):
        _invalid()


def _params(job):
    try:
        value = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
    except (ValueError, TypeError, RecursionError):
        _invalid()
    if not isinstance(value, dict):
        _invalid()
    return value


def _object(encoded):
    try:
        if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_BYTES:
            _invalid()
        value = json.loads(encoded)
        if not isinstance(value, dict):
            _invalid()
        return value
    except (ValueError, TypeError, RecursionError):
        _invalid()


def _binding(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"batch_id", "task_id", "attempt", "identity_hash"}
        or not _identifier(value["batch_id"])
        or not _identifier(value["task_id"])
        or type(value["attempt"]) is not int
        or not 1 <= value["attempt"] < 2**31
        or not _sha(value["identity_hash"])
    ):
        _invalid()
    return value


def _producer(origin, stage):
    try:
        return {
            "recording_id": origin["recording_id"],
            "job_id": origin["job_id"],
            "request_sha256": origin["request_checkpoint"]["producer"]["request_sha256"],
            "stage": stage,
        }
    except (KeyError, TypeError):
        _invalid()


def validate_origin(value, *, recording_id, job_id, binding, files, manifest_sha256):
    _encoded(value)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "format",
            "generation",
            "recording_id",
            "job_id",
            "binding",
            "input_manifest_sha256",
            "input_files",
            "request_checkpoint",
        }
        or value["format"] != FORMAT
        or not _identifier(value["generation"])
        or value["recording_id"] != recording_id
        or value["job_id"] != job_id
        or _binding(value["binding"]) != _binding(binding)
        or not _sha(manifest_sha256)
        or value["input_manifest_sha256"] != manifest_sha256
        or _encoded(value["input_files"]) != _encoded(files)
        or not isinstance(files, dict)
        or "request.json" not in files
        or any(not _input_name(name) for name in files)
    ):
        _invalid()
    for receipt in files.values():
        _file(receipt)
    originals = [name for name in files if name.startswith("audio.source.")]
    if "audio.wav" in files:
        if "original.input.json" in files or originals:
            _invalid()
    elif "original.input.json" not in files or len(originals) != 1:
        _invalid()
    checkpoint = artifacts.validate_checkpoint(
        value["request_checkpoint"], producer=_producer(value, "preparation")
    )
    request = checkpoint["artifacts"]["request"]
    if (
        request == "not_captured"
        or {"sha256": request["sha256"], "size": request["bytes"]} != files["request.json"]
        or any(
            receipt != "not_captured"
            for role, receipt in checkpoint["artifacts"].items()
            if role != "request"
        )
    ):
        _invalid()
    return copy.deepcopy(value)


def origin(job, folder, *, binding, manifest_sha256, files):
    params = _params(job)
    if params.get("runtime_artifact_policy") is None:
        return None
    if not eligible(params) or params["runtime_artifact_policy"] != POLICY:
        _invalid()
    local.seal(folder / "request.json")
    request = artifacts.capture_file(folder / "request.json", folder, role="request", format="json")
    value = {
        "format": FORMAT,
        "generation": uuid.uuid4().hex,
        "recording_id": job["recording_id"],
        "job_id": job["id"],
        "binding": binding,
        "input_manifest_sha256": manifest_sha256,
        "input_files": files,
    }
    producer = {
        "recording_id": job["recording_id"],
        "job_id": job["id"],
        "request_sha256": request.receipt()["sha256"],
        "stage": "preparation",
    }
    value["request_checkpoint"] = artifacts.make_checkpoint([request], producer=producer)
    return validate_origin(
        value,
        recording_id=job["recording_id"],
        job_id=job["id"],
        binding=binding,
        files=files,
        manifest_sha256=manifest_sha256,
    )


def validate_checkpoints(value, origin, *, files):
    _encoded(value)
    if not isinstance(value, dict) or value.keys() - FILES.keys() or not isinstance(files, dict):
        _invalid()
    for name, checkpoint in value.items():
        if name not in files:
            _invalid()
        expected = _file(files[name])
        verified = artifacts.validate_checkpoint(
            checkpoint, producer=_producer(origin, FILES[name][1])
        )
        output = verified["artifacts"]["output"]
        if (
            output == "not_captured"
            or {"sha256": output["sha256"], "size": output["bytes"]} != expected
            or verified["artifacts"]["request"]
            != origin["request_checkpoint"]["artifacts"]["request"]
            or any(
                verified["artifacts"][role] != "not_captured"
                for role in ("raw_response", "environment")
            )
        ):
            _invalid()
    return copy.deepcopy(value)


def collect(origin, folder, *, files):
    """Only after this collection actually received and content-validated these exact files."""
    if origin is None:
        return None
    request = artifacts.capture_file(folder / "request.json", folder, role="request", format="json")
    if request.receipt() != origin["request_checkpoint"]["artifacts"]["request"]:
        _invalid()
    captured = {}
    for name, expected in files.items():
        if name not in FILES:
            _invalid()
        _file(expected)
        local.seal(folder / name)
        output = artifacts.capture_file(folder / name, folder, role="output", format="json")
        captured[name] = artifacts.make_checkpoint(
            [request, output], producer=_producer(origin, FILES[name][1])
        )
    return validate_checkpoints(captured, origin, files=files)


def document(binding, inputs, remote):
    if not isinstance(inputs, dict) or not isinstance(remote, dict):
        _invalid()
    if "artifact_origin" not in inputs:
        return None
    origin = inputs["artifact_origin"]
    validate_origin(
        origin,
        recording_id=origin["recording_id"],
        job_id=origin["job_id"],
        binding=binding,
        files=inputs["files"],
        manifest_sha256=inputs["manifest_sha256"],
    )
    checkpoints = remote.get("artifact_checkpoints", {})
    files = remote.get("artifact_files", {})
    validate_checkpoints(checkpoints, origin, files=files)
    return {
        "format": FORMAT,
        "boundary": "validated_local_collection",
        "origin": origin,
        "checkpoints": checkpoints,
        "artifact_files": files,
    }


def check_update(job, binding, previous_inputs, previous_remote, inputs, remote, *, terminal):
    """Called only by the existing task/epoch CAS writer; no file reads or new authority."""
    captured = inputs.get("artifact_origin")
    if captured is None:
        if (
            "artifact_origin" in inputs
            or "artifact_checkpoints" in remote
            or "artifact_files" in remote
        ):
            _invalid()
        return
    if not eligible(job["params"]) or job["params"].get("runtime_artifact_policy") != POLICY:
        _invalid()
    validate_origin(
        captured,
        recording_id=job["recording_id"],
        job_id=job["id"],
        binding=binding,
        files=inputs.get("files"),
        manifest_sha256=inputs.get("manifest_sha256"),
    )
    previous = previous_inputs.get("artifact_origin")
    if previous is not None:
        if _encoded(previous) != _encoded(captured):
            _invalid()
    elif previous_inputs.get("publish_intent") or terminal:
        _invalid()
    checkpoints = remote.get("artifact_checkpoints", {})
    files = remote.get("artifact_files", {})
    if not isinstance(files, dict) or files.keys() - FILES.keys():
        _invalid()
    for name, receipt in files.items():
        _file(receipt)
        if name in {
            "transcription.json",
            "retranscription.json",
            "alignment.json",
            "diarization.json",
        } and receipt != remote.get("files", {}).get(name):
            _invalid()
    validate_checkpoints(checkpoints, captured, files=files)
    before = previous_remote.get("artifact_checkpoints", {})
    if any(
        _encoded(checkpoints.get(name)) != _encoded(receipt) for name, receipt in before.items()
    ):
        _invalid()
    if terminal and _encoded(checkpoints) != _encoded(before):
        _invalid()
    previous_files = previous_remote.get("artifact_files", {})
    if (
        any(
            _encoded(files.get(name)) != _encoded(receipt)
            for name, receipt in previous_files.items()
        )
        or terminal
        and _encoded(files) != _encoded(previous_files)
    ):
        _invalid()


def for_creation(db, parent, recording_id, supplied, runtime_receipt=None):
    """Fixed Store validator against actual owning attempt rows, never supplied job claims."""
    if parent is None:
        if supplied is not None:
            _invalid()
        return None
    params = _params(parent)
    if (
        supplied is None
        and params.get("runtime_artifact_policy") is None
        and runtime_receipt is None
        and params.get("runtime_output_receipt") is None
    ):
        return None
    sizes = db.execute(
        "SELECT length(CAST(a.input_receipt AS BLOB)),length(CAST(a.remote_receipt AS BLOB)),"
        "length(CAST(b.identity AS BLOB)) "
        "FROM runtime_batch_members m JOIN runtime_batch_attempts a "
        "ON a.job_id=m.job_id AND a.attempt=m.current_attempt "
        "JOIN runtime_batch_runs b ON b.id=m.batch_id WHERE m.job_id=?",
        (parent["id"],),
    ).fetchone()
    if sizes is not None and any(
        type(size) is not int or not 0 <= size <= MAX_BYTES for size in sizes
    ):
        _invalid()
    row = db.execute(
        "SELECT m.batch_id,m.recording_id,m.current_attempt,m.result_id,a.task_id,"
        "a.input_receipt,a.remote_receipt,b.compatibility_hash,b.identity "
        "FROM runtime_batch_members m "
        "JOIN runtime_batch_attempts a ON a.job_id=m.job_id AND a.attempt=m.current_attempt "
        "JOIN runtime_batch_runs b ON b.id=m.batch_id WHERE m.job_id=?",
        (parent["id"],),
    ).fetchone()
    if row is None:
        if supplied is not None or params.get("runtime_artifact_policy") is not None:
            _invalid()
        return None
    inputs, remote = _object(row["input_receipt"]), _object(row["remote_receipt"])
    if "artifact_origin" not in inputs:
        if supplied is not None or params.get("runtime_artifact_policy") is not None:
            _invalid()
        return None
    if (
        row["recording_id"] != recording_id
        or parent["recording_id"] != recording_id
        or parent["kind"] != "enhance"
        or not eligible(params)
        or params.get("runtime_artifact_policy") != POLICY
    ):
        _invalid()
    identity = _object(row["identity"])
    if (
        not isinstance(identity, dict)
        or identity.get("operation") != "transcription"
        or hashlib.sha256(_encoded(identity)).hexdigest() != row["compatibility_hash"]
    ):
        _invalid()
    binding = {
        "batch_id": row["batch_id"],
        "task_id": row["task_id"],
        "attempt": row["current_attempt"],
        "identity_hash": row["compatibility_hash"],
    }
    output = runtime_receipt or params.get("runtime_output_receipt", {})
    return validate_document(
        supplied,
        recording_id=recording_id,
        job_id=parent["id"],
        binding=binding,
        inputs=inputs,
        remote=remote,
        output_receipt=output,
    )


def validate_document(
    value, *, recording_id, job_id, binding, inputs, remote, output_receipt=None, creation=None
):
    """Pure comparison with caller-observed owning attempt rows; no SQLite/Store lookup."""
    observed = (
        unbound(value, recording_id=recording_id, job_id=job_id, creation=creation)
        if creation is not None
        else value
    )
    expected = document(binding, inputs, remote)
    if observed is None or expected is None or _encoded(observed) != _encoded(expected):
        _invalid()
    origin = observed["origin"]
    validate_origin(
        origin,
        recording_id=recording_id,
        job_id=job_id,
        binding=binding,
        files=inputs["files"],
        manifest_sha256=inputs["manifest_sha256"],
    )
    for name, receipt in observed["artifact_files"].items():
        if name in {
            "transcription.json",
            "retranscription.json",
            "alignment.json",
            "diarization.json",
        } and receipt != remote.get("files", {}).get(name):
            _invalid()
    if output_receipt is None:
        return copy.deepcopy(observed)
    output = output_receipt
    if (
        not isinstance(output, dict)
        or any(output.get(key) != binding[key] for key in ("batch_id", "task_id", "attempt"))
        or remote.get("outcome") != "complete"
    ):
        _invalid()
    validate_checkpoints(observed["checkpoints"], observed["origin"], files=output.get("files", {}))
    if any(
        output.get("files", {}).get(name) != receipt
        for name, receipt in observed["artifact_files"].items()
    ):
        _invalid()
    return observed


def bind(value, *, creation):
    value = copy.deepcopy(value)
    value["creation"] = creation
    origin = value["origin"]
    origin["request_checkpoint"] = artifacts.bind_creation(
        origin["request_checkpoint"], producer=_producer(origin, "preparation"), creation=creation
    )
    for name, checkpoint in value["checkpoints"].items():
        value["checkpoints"][name] = artifacts.bind_creation(
            checkpoint, producer=_producer(origin, FILES[name][1]), creation=creation
        )
    return value


def unbound(value, *, recording_id, job_id, creation):
    _encoded(value)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"format", "boundary", "origin", "checkpoints", "artifact_files", "creation"}
        or value["format"] != FORMAT
        or value["boundary"] != "validated_local_collection"
        or value["creation"] != creation
    ):
        _invalid()
    value = copy.deepcopy(value)
    origin = value["origin"]
    if not isinstance(origin, dict) or not isinstance(value["checkpoints"], dict):
        _invalid()
    if origin["recording_id"] != recording_id or origin["job_id"] != job_id:
        _invalid()
    request = origin["request_checkpoint"]
    artifacts.validate_bound(request, producer=_producer(origin, "preparation"), creation=creation)
    request.pop("creation")
    for name, checkpoint in value["checkpoints"].items():
        if name not in FILES:
            _invalid()
        artifacts.validate_bound(
            checkpoint, producer=_producer(origin, FILES[name][1]), creation=creation
        )
        checkpoint.pop("creation")
    value.pop("creation")
    validate_checkpoints(value["checkpoints"], origin, files=value["artifact_files"])
    return value


def public_projection(value, *, recording_id, job_id, creation):
    verified = unbound(value, recording_id=recording_id, job_id=job_id, creation=creation)
    origin = verified["origin"]
    return {
        "format": FORMAT,
        "boundary": verified["boundary"],
        "input_manifest_sha256": origin["input_manifest_sha256"],
        "request_boundary": "before_publication",
        "input_observations": [
            {
                "kind": "request"
                if name == "request.json"
                else "prepared_local_audio"
                if name == "audio.wav"
                else "source_descriptor"
                if name == "original.input.json"
                else "managed_original_transport"
                if name.startswith("audio.source.")
                else "carried_" + FILES[name][0],
                "sha256": receipt["sha256"],
                "bytes": receipt["size"],
                "observation": "frozen_publication_manifest",
            }
            for name, receipt in sorted(origin["input_files"].items())
        ],
        "request": artifacts.public_projection(
            value["origin"]["request_checkpoint"],
            producer=_producer(origin, "preparation"),
            creation=creation,
        ),
        "outputs": {
            FILES[name][0]: artifacts.public_projection(
                value["checkpoints"][name],
                producer=_producer(origin, FILES[name][1]),
                creation=creation,
            )
            for name in verified["checkpoints"]
        },
        "retained_files": "not_checked",
        "worker_environment": "not_captured",
    }


def inventory(value, *, recording_id, job_id, creation=None):
    observed = (
        unbound(value, recording_id=recording_id, job_id=job_id, creation=creation)
        if creation is not None
        else value
    )
    if (
        not isinstance(observed, dict)
        or set(observed) != {"format", "boundary", "origin", "checkpoints", "artifact_files"}
        or not isinstance(observed["origin"], dict)
    ):
        _invalid()
    origin = observed["origin"]
    validate_origin(
        origin,
        recording_id=recording_id,
        job_id=job_id,
        binding=origin["binding"],
        files=origin["input_files"],
        manifest_sha256=origin["input_manifest_sha256"],
    )
    validate_checkpoints(observed["checkpoints"], origin, files=observed["artifact_files"])
    files = []
    for name, receipt in origin["input_files"].items():
        scope = (
            {"scope": "managed_original", "transport_name": name}
            if name.startswith("audio.source.")
            else {
                "scope": "producer_attempt",
                "attempt": origin["binding"]["attempt"],
                "filename": name,
            }
            if name in FILES
            else {"scope": "producer_directory", "filename": name}
        )
        files.append(
            {
                **scope,
                "sha256": receipt["sha256"],
                "bytes": receipt["size"],
                "role": "runtime_input",
            }
        )
    for name, checkpoint in observed["checkpoints"].items():
        if name not in FILES:
            _invalid()
        output = checkpoint["artifacts"]["output"]
        files.append(
            {
                "scope": "producer_attempt",
                "attempt": origin["binding"]["attempt"],
                "filename": name,
                "sha256": output["sha256"],
                "bytes": output["bytes"],
                "role": "output",
            }
        )
    return files
