"""Producer-owned local ASR receipts, fenced independently of mutable job progress.

File observations happen outside database/queue locks. The short dedicated writer only
publishes an absent origin/stage or verifies an identical retry. Historical readers use
stored rows and never inspect today's files, packages, device or inference runtime.
"""

import copy
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from . import provenance_artifacts as artifacts

POLICY = "local-full-asr-v1"
ENHANCEMENT_POLICY = "local-enhancement-v1"
POLICIES = {POLICY, ENHANCEMENT_POLICY}
FORMAT = "cbs-local-asr-capture-v1"
STAGES = ("transcription", "alignment", "diarization")
MAX_DOCUMENT_BYTES = 1024 * 1024


def _invalid():
    raise ValueError("The local ASR artifact evidence changed or is invalid. Preserve its files.")


def _canonical(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_DOCUMENT_BYTES:
            _invalid()
        return encoded
    except (TypeError, ValueError, RecursionError):
        _invalid()


def eligible(params, kind="enhance"):
    options = params.get("enhancement") if isinstance(params, dict) else None
    return (
        kind == "enhance"
        and isinstance(options, dict)
        and options.get("execution") == "local"
        and options.get("retranscribe") is False
        and not any(
            key in params
            for key in (
                "segment_id",
                "segment_ids",
                "timing_units",
                "realign",
                "retranscription",
                "ucloud_api",
                "cloud_settings",
                "runtime_batch_id",
                "native_batch_id",
            )
        )
        and (
            params.get("transcription") is True
            and options.get("transcribe") is True
            and "source_snapshot" not in params
            or not params.get("transcription")
            and options.get("transcribe") is False
            and (options.get("align") is True or options.get("diarize") is True)
            and isinstance(params.get("source_snapshot"), dict)
        )
    )


def frozen_policy(params):
    """Fresh request boundaries may opt in; shared insertion/retry seams must not call this."""
    if not eligible(params) or "producer_artifact_capture" in params:
        _invalid()
    return {
        **params,
        "producer_artifact_policy": POLICY if params.get("transcription") else ENHANCEMENT_POLICY,
    }


def assert_params_preserved(previous, proposed):
    """Generic job updates cannot create, erase or replace producer observations."""
    from .timing_capture import assert_params_preserved as preserve_timing

    preserve_timing(previous, proposed)
    from .runtime_capture import assert_params_preserved as preserve_runtime

    preserve_runtime(previous, proposed)
    for key in ("producer_artifact_policy", "producer_artifact_capture"):
        if _canonical(previous.get(key)) != _canonical(proposed.get(key)):
            _invalid()
    if previous.get("producer_artifact_capture") is not None:
        # These exact choices produced the request bytes. Progress/journals remain mutable.
        for key in ("enhancement", "source_snapshot", "audio_input"):
            if previous.get(key) != proposed.get(key):
                _invalid()


def _checked_policy(params, kind):
    if not eligible(params, kind) or params.get("producer_artifact_policy") != (
        POLICY if params.get("transcription") else ENHANCEMENT_POLICY
    ):
        _invalid()


def _requested(params, stage):
    return (
        params["enhancement"].get(
            {"transcription": "transcribe", "alignment": "align", "diarization": "diarize"}[stage]
        )
        is True
    )


def _producer(origin, stage):
    return {
        "recording_id": origin["recording_id"],
        "job_id": origin["job_id"],
        "request_sha256": origin["audio_binding"]["request_sha256"],
        "stage": stage,
    }


def validate(value, *, recording_id, job_id, bound_creation=None):
    """Validate serialized transport only against trusted owning row references."""
    try:
        _canonical(value)
        if not isinstance(value, dict) or set(value) != {"format", "origin", "checkpoints"}:
            _invalid()
        origin, checkpoints = value["origin"], value["checkpoints"]
        if (
            value["format"] != FORMAT
            or not isinstance(origin, dict)
            or set(origin)
            != {"generation", "recording_id", "job_id", "request_checkpoint", "audio_binding"}
            or not isinstance(origin["generation"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", origin["generation"])
            or origin["recording_id"] != recording_id
            or origin["job_id"] != job_id
            or not isinstance(checkpoints, dict)
            or checkpoints.keys() - set(STAGES)
        ):
            _invalid()
        binding = origin["audio_binding"]
        if (
            not isinstance(binding, dict)
            or set(binding) != {"input_sha256", "request_sha256", "audio_frames"}
            or any(
                not isinstance(binding[key], str) or not re.fullmatch(r"[a-f0-9]{64}", binding[key])
                for key in ("input_sha256", "request_sha256")
            )
            or type(binding["audio_frames"]) is not int
            or not 0 < binding["audio_frames"] <= 16000 * 21600
        ):
            _invalid()
        verifier = (
            artifacts.validate_bound
            if bound_creation is not None
            else artifacts.validate_checkpoint
        )
        extra = {"creation": bound_creation} if bound_creation is not None else {}
        request = verifier(
            origin["request_checkpoint"], producer=_producer(origin, "preparation"), **extra
        )
        if request["artifacts"]["request"] == "not_captured" or any(
            receipt != "not_captured"
            for role, receipt in request["artifacts"].items()
            if role != "request"
        ):
            _invalid()
        for stage, checkpoint in checkpoints.items():
            verified = verifier(checkpoint, producer=_producer(origin, stage), **extra)
            if (
                verified["artifacts"]["output"] == "not_captured"
                or verified["artifacts"]["request"] != request["artifacts"]["request"]
                or verified["artifacts"]["raw_response"] != "not_captured"
            ):
                _invalid()
        return copy.deepcopy(value)
    except (KeyError, TypeError, RecursionError):
        _invalid()


def _publish(store, job, document):
    """Merge append-only observations without replacing another writer's job parameters."""
    from . import restored_owners
    from .store import now

    restored_owners.require_job(store, job["id"])

    value = validate(document, recording_id=job["recording_id"], job_id=job["id"])
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        restored_owners.require_owned(db, "job", job["id"])
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if row is None or row["recording_id"] != job["recording_id"] or row["state"] != "running":
            _invalid()
        params = json.loads(row["params"])
        _checked_policy(params, row["kind"])
        if any(not _requested(params, stage) for stage in value["checkpoints"]):
            _invalid()
        previous = params.get("producer_artifact_capture")
        if previous is not None:
            previous = validate(previous, recording_id=row["recording_id"], job_id=row["id"])
            if _canonical(previous["origin"]) != _canonical(value["origin"]):
                _invalid()
            for stage, checkpoint in previous["checkpoints"].items():
                if _canonical(value["checkpoints"].get(stage)) != _canonical(checkpoint):
                    _invalid()
            if previous == value:
                return previous
        params["producer_artifact_capture"] = value
        db.execute(
            "UPDATE jobs SET params=?,updated=? WHERE id=?",
            (
                json.dumps(params, sort_keys=True, ensure_ascii=False, allow_nan=False),
                now(),
                row["id"],
            ),
        )
    job["params"] = params
    return value


def seal(path):
    """Flush an already closed producer file; refusal leaves it unclaimed and retained."""
    path = Path(path)
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_request(job, folder, expected_bytes):
    """Do not overwrite changed frozen request bytes before the retry check can see them."""
    document = job["params"].get("producer_artifact_capture")
    if document is not None:
        value = validate(document, recording_id=job["recording_id"], job_id=job["id"])
        observed = artifacts.capture_file(
            folder / "request.json", folder, role="request", format="json"
        )
        if (
            observed.receipt()["sha256"] != hashlib.sha256(expected_bytes).hexdigest()
            or observed.receipt()["bytes"] != len(expected_bytes)
            or observed.receipt() != value["origin"]["request_checkpoint"]["artifacts"]["request"]
        ):
            _invalid()


def begin(store, job, folder):
    """Persist the exact request/audio origin before new inference; never backfill outputs."""
    from .enhancement_worker import language_binding

    params = job["params"]
    _checked_policy(params, job["kind"])
    existing = params.get("producer_artifact_capture")
    if existing is not None:
        value = validate(existing, recording_id=job["recording_id"], job_id=job["id"])
        verify_files(value, folder)
        _worker_origin(folder, value)
        return value
    if any((folder / (stage + ".json")).exists() for stage in STAGES):
        # A crash before publication is a provenance gap, not permission to repeat ASR.
        return None
    seal(folder / "audio.wav")
    seal(folder / "request.json")
    observed = artifacts.capture_file(
        folder / "request.json", folder, role="request", format="json"
    )
    origin = {
        "generation": uuid.uuid4().hex,
        "recording_id": job["recording_id"],
        "job_id": job["id"],
        "audio_binding": language_binding(folder),
    }
    origin["request_checkpoint"] = artifacts.make_checkpoint(
        [observed], producer=_producer(origin, "preparation")
    )
    value = _publish(store, job, {"format": FORMAT, "origin": origin, "checkpoints": {}})
    _worker_origin(folder, value)
    return value


def _worker_origin(folder, value):
    from .enhancement_worker import write_json

    origin = value["origin"]
    write_json(
        folder / "producer-origin.json",
        {key: origin[key] for key in ("generation", "audio_binding")},
    )


def verify_files(value, folder):
    """Reobserve retained bytes only for execution recovery, never for history/report reads."""
    from .enhancement_worker import language_binding

    origin = value["origin"]
    if language_binding(folder) != origin["audio_binding"]:
        _invalid()
    request = artifacts.capture_file(folder / "request.json", folder, role="request", format="json")
    if request.receipt() != origin["request_checkpoint"]["artifacts"]["request"]:
        _invalid()
    for stage, checkpoint in value["checkpoints"].items():
        output = artifacts.capture_file(
            folder / (stage + ".json"), folder, role="output", format="json"
        )
        if output.receipt() != checkpoint["artifacts"]["output"]:
            _invalid()
        environment = checkpoint["artifacts"]["environment"]
        if (
            environment != "not_captured"
            and artifacts.capture_file(
                folder / (stage + ".environment.json"), folder, role="environment", format="json"
            ).receipt()
            != environment
        ):
            _invalid()


def complete_stage(store, job, folder, stage):
    """Called only after the producer's existing content/input validator accepts a stage."""
    if stage not in STAGES or not _requested(job["params"], stage):
        _invalid()
    value = validate(
        job["params"]["producer_artifact_capture"],
        recording_id=job["recording_id"],
        job_id=job["id"],
    )
    verify_files(value, folder)
    if stage in value["checkpoints"]:
        return value
    seal(folder / (stage + ".json"))
    observed = artifacts.capture_file(
        folder / (stage + ".json"), folder, role="output", format="json"
    )
    request = artifacts.capture_file(folder / "request.json", folder, role="request", format="json")
    captures = [request, observed]
    environment = folder / (stage + ".environment.json")
    if environment.exists():
        seal(environment)
        captures.append(
            artifacts.capture_file(environment, folder, role="environment", format="json")
        )
    checkpoint = artifacts.make_checkpoint(captures, producer=_producer(value["origin"], stage))
    value["checkpoints"][stage] = checkpoint
    return _publish(store, job, value)


def for_creation(parent, recording_id, supplied):
    """Fixed Store validator: no file I/O, callbacks, current runtime or inferred capture."""
    if supplied is None:
        if parent is not None:
            params = (
                json.loads(parent["params"])
                if isinstance(parent["params"], str)
                else parent["params"]
            )
            if isinstance(params, dict) and params.get("producer_artifact_capture") is not None:
                _invalid()
        return None
    if parent is None or parent["recording_id"] != recording_id:
        _invalid()
    params = json.loads(parent["params"]) if isinstance(parent["params"], str) else parent["params"]
    _checked_policy(params, parent["kind"])
    stored = validate(
        params.get("producer_artifact_capture"), recording_id=recording_id, job_id=parent["id"]
    )
    if _canonical(stored) != _canonical(supplied):
        _invalid()
    if any(not _requested(params, stage) for stage in stored["checkpoints"]):
        _invalid()
    if "audio_input" in params:
        from .audio_inputs import validate_evidence

        receipts = params.get("audio_preparations", [])
        if not isinstance(receipts, list) or len(receipts) != 1:
            _invalid()
        receipt = validate_evidence(receipts[0], params["audio_input"])
        prepared = receipt.get("prepared", {})
        if (
            prepared.get("sha256") != stored["origin"]["audio_binding"]["input_sha256"]
            or prepared.get("frames") != stored["origin"]["audio_binding"]["audio_frames"]
        ):
            _invalid()
    return stored


def bind(value, *, creation):
    """Bind only in the transaction allocating the immutable sequence-one version."""
    value = copy.deepcopy(value)
    origin = value["origin"]
    origin["request_checkpoint"] = artifacts.bind_creation(
        origin["request_checkpoint"], producer=_producer(origin, "preparation"), creation=creation
    )
    value["checkpoints"] = {
        stage: artifacts.bind_creation(
            checkpoint, producer=_producer(origin, stage), creation=creation
        )
        for stage, checkpoint in value["checkpoints"].items()
    }
    return value


def public_projection(value, *, recording_id, job_id, creation):
    verified = validate(value, recording_id=recording_id, job_id=job_id, bound_creation=creation)
    origin = verified["origin"]
    return {
        "format": FORMAT,
        "request": artifacts.public_projection(
            origin["request_checkpoint"],
            producer=_producer(origin, "preparation"),
            creation=creation,
        ),
        "stages": {
            stage: artifacts.public_projection(
                checkpoint, producer=_producer(origin, stage), creation=creation
            )
            if (checkpoint := verified["checkpoints"].get(stage)) is not None
            else "not_captured"
            for stage in STAGES
        },
        "retained_files": "not_checked",
    }


def unbound(value, *, recording_id, job_id, creation):
    """Recover original transport for exact comparisons with its trusted producing row."""
    verified = validate(value, recording_id=recording_id, job_id=job_id, bound_creation=creation)
    verified["origin"]["request_checkpoint"].pop("creation")
    for checkpoint in verified["checkpoints"].values():
        checkpoint.pop("creation")
    return verified


def inventory(value, *, recording_id, job_id, creation=None):
    """Pure expected filenames/identities; the caller supplies DB-owned directory authority.

    The WAV observation includes its exact hash/frame count, not a guessed container size.
    Every path here is a fixed leaf beneath enhancements/<trusted owning job ID>.
    """
    verified = validate(value, recording_id=recording_id, job_id=job_id, bound_creation=creation)
    origin = verified["origin"]
    request = origin["request_checkpoint"]["artifacts"]["request"]
    output = [
        {
            "filename": "request.json",
            "role": "request",
            "sha256": request["sha256"],
            "bytes": request["bytes"],
        },
        {
            "filename": "audio.wav",
            "role": "prepared_input",
            "sha256": origin["audio_binding"]["input_sha256"],
            "bytes": None,
            "audio_frames": origin["audio_binding"]["audio_frames"],
        },
    ]
    for stage, checkpoint in verified["checkpoints"].items():
        for role, filename in (
            ("output", stage + ".json"),
            ("environment", stage + ".environment.json"),
        ):
            receipt = checkpoint["artifacts"][role]
            if receipt != "not_captured":
                output.append(
                    {
                        "filename": filename,
                        "role": role,
                        "sha256": receipt["sha256"],
                        "bytes": receipt["bytes"],
                    }
                )
    return output
