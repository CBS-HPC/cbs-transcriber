"""Fresh API transcription request/output observations, without server-runtime claims.

Only explicit fresh admission freezes the policy. Before each upload the coordinator
writes and records a credential-free logical request descriptor; after a response it
observes the preserved normalized SDK output and publishes it with its normalized part
in one short job write. File hashing runs outside writer transactions. Historical helpers
accept trusted stored rows only, never paths or callbacks, and import no audio, provider
or runtime code, so the contained portable checker can execute this exact source.
A logical descriptor is not the multipart HTTP request sent by the SDK, and the output is
the SDK's normalized response, not original wire bytes. Native UCloud Transcriber jobs
do not receive this policy.
"""

import copy
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

from . import provenance_artifacts as artifacts
from .history import encode_payload
from .transcripts import combine_chunks, pop_hints

POLICY = "api-artifact-v1"
FORMAT = "cbs-provider-artifacts-v1"
DESCRIPTOR = "cbs-provider-request-descriptor-v1"
PROVIDER_TYPES = frozenset({"openai", "custom", "ucloud-vllm"})
TRANSPORTS = frozenset({"original", "mp3"})
REQUEST_FIELDS = frozenset(
    {
        "model",
        "language",
        "languages",
        "response_format",
        "timestamp_granularities",
        "chunking_strategy",
        "include",
    }
)
EXCLUDED = (
    "native_batch_id",
    "runtime_batch_id",
    "enhancement",
    "transcription",
    "source_snapshot",
    "ucloud_run",
    "ucloud_api",
    "segment_id",
    "segment_ids",
    "input_intent",
)
KEYS = ("provider_artifact_policy", "provider_artifact_capture")
MAX_PARTS = 2000
MAX_ATTEMPTS = 100
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_PART_BYTES = 32 * 1024**2
# Job parameters and bound version contexts must stay below the 8 MiB portable JSON limit.
MAX_CAPTURE_BYTES = 5 * 1024**2
MAX_BOUND_BYTES = 6 * 1024**2
ATTEMPT_RESERVE = 16 * 1024
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-f0-9]{32}")


def _invalid():
    raise ValueError("The provider artifact evidence changed or is invalid. Preserve its files.")


def canonical(value, maximum=MAX_CAPTURE_BYTES):
    try:
        raw = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        _invalid()
    if len(raw) > maximum:
        _invalid()
    return raw


def digest(value, maximum=MAX_CAPTURE_BYTES):
    return hashlib.sha256(canonical(value, maximum)).hexdigest()


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _id(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _decoded(value, expected):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            _invalid()
    if not isinstance(value, expected):
        _invalid()
    return value


def _params(job):
    return _decoded(job["params"], dict)


def _chunks(job):
    return _decoded(job["chunks"], list)


def _job(row):
    """Decode a trusted job row once; nested per-attempt checks reuse the decoded copy."""
    job = dict(row)
    job["params"] = _params(job)
    if "chunks" in job:
        job["chunks"] = _chunks(job)
    return job


def eligible(params, kind="transcribe"):
    settings = params.get("settings") if isinstance(params, dict) else None
    return (
        kind == "transcribe"
        and isinstance(settings, dict)
        and settings.get("provider_type") in PROVIDER_TYPES
        and isinstance(settings.get("base_url"), str)
        and isinstance(params.get("audio_input"), dict)
        and params.get("audio_transport") in TRANSPORTS
        and not any(key in params for key in EXCLUDED)
    )


def freeze(params):
    """Only explicit fresh admission calls this; insertion, retry and recovery never do."""
    if not eligible(params):
        return params
    if params.get("provider_artifact_policy", POLICY) != POLICY or KEYS[1] in params:
        _invalid()
    return {**params, "provider_artifact_policy": POLICY}


def enabled(job):
    """A present marker is checked; a missing marker never derives today's policy."""
    params = _params(job)
    if KEYS[0] not in params:
        if KEYS[1] in params:
            _invalid()
        return False
    if params[KEYS[0]] != POLICY or not eligible(params, job["kind"]):
        _invalid()
    return True


def assert_params_preserved(previous, proposed):
    """Generic job writes cannot create, erase or replace provider observations."""
    for key in KEYS:
        if (key in previous) != (key in proposed) or (
            key in previous and canonical(previous[key]) != canonical(proposed[key])
        ):
            _invalid()
    if KEYS[0] in previous:
        # These frozen choices determined every recorded descriptor.
        for key in ("settings", "audio_input", "audio_transport", "audio_preparations"):
            if key in previous and (
                key not in proposed
                or canonical(previous[key], MAX_PART_BYTES)
                != canonical(proposed[key], MAX_PART_BYTES)
            ):
                _invalid()


def _clean(segments):
    value = copy.deepcopy(segments)
    pop_hints(value)
    return value


def part_hash(part):
    """Exact normalized part as published to the job, including its review hints."""
    if (
        not isinstance(part, dict)
        or set(part) != {"index", "offset", "segments"}
        or type(part["index"]) is not int
        or not 0 <= part["index"] < MAX_PARTS
        or not _number(part["offset"])
        or part["offset"] < 0
        or not isinstance(part["segments"], list)
    ):
        _invalid()
    return digest(part, MAX_PART_BYTES)


def assembly(parts):
    """The exact result payload hash that Store creation computes from these parts."""
    try:
        segments = _clean(combine_chunks(parts))
        return encode_payload(segments, {})[1]
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        _invalid()


def _plan(params):
    receipts = params.get("audio_preparations")
    if not isinstance(receipts, list) or len(receipts) != 1:
        _invalid()
    plan = receipts[0]
    if (
        not isinstance(plan, dict)
        or plan.get("transport") not in TRANSPORTS
        or plan["transport"] != params.get("audio_transport")
        or not isinstance(plan.get("chunks"), list)
        or not isinstance(plan.get("parts"), list)
        or not 1 <= len(plan["chunks"]) <= MAX_PARTS
        or len(plan["parts"]) != len(plan["chunks"])
    ):
        _invalid()
    return plan


def _input(params, index):
    plan = _plan(params)
    if type(index) is not int or not 0 <= index < len(plan["chunks"]):
        _invalid()
    chunk, part = plan["chunks"][index], plan["parts"][index]
    prepared = part.get("prepared") if isinstance(part, dict) else None
    if not isinstance(chunk, dict) or not isinstance(prepared, dict):
        _invalid()
    value = {
        "scope": "managed_original" if plan["transport"] == "original" else "prepared_part",
        "sha256": prepared.get("sha256"),
        "bytes": prepared.get("bytes"),
        "format": prepared.get("format"),
        "offset": chunk.get("offset"),
        "duration": chunk.get("duration"),
    }
    if (
        not _sha(value["sha256"])
        or type(value["bytes"]) is not int
        or not 0 < value["bytes"] < 2**63
        or not isinstance(value["format"], str)
        or not 0 < len(value["format"]) <= 16
        or not _number(value["offset"])
        or value["offset"] < 0
        or not _number(value["duration"])
        or value["duration"] <= 0
    ):
        _invalid()
    return value


def _endpoint(settings):
    return hashlib.sha256(settings["base_url"].encode("utf-8")).hexdigest()


def check_fields(fields, settings):
    """The logical SDK fields must follow the frozen capability and language."""
    if (
        not isinstance(fields, dict)
        or fields.keys() - REQUEST_FIELDS
        or not isinstance(fields.get("model"), str)
        or fields["model"] != settings.get("model")
        or not 0 < len(fields["model"]) <= 200
    ):
        _invalid()
    language = settings.get("language", "")
    if not isinstance(language, str):
        _invalid()
    if language:
        if not (
            (fields.get("language") == language and "languages" not in fields)
            or (fields.get("languages") == [language] and "language" not in fields)
        ):
            _invalid()
    elif "language" in fields or "languages" in fields:
        _invalid()
    capability = settings.get("capability")
    expected = (
        "verbose_json"
        if capability in {"words", "words_speakers", "segments"}
        else "diarized_json"
        if capability == "speakers"
        else "json"
    )
    if fields.get("response_format") != expected:
        _invalid()
    if capability in {"words", "words_speakers"}:
        if fields.get("timestamp_granularities") != ["word", "segment"]:
            _invalid()
    elif "timestamp_granularities" in fields:
        _invalid()
    if "chunking_strategy" in fields and (
        capability != "speakers" or fields["chunking_strategy"] != "auto"
    ):
        _invalid()
    if "include" in fields and (capability != "json" or fields["include"] != ["logprobs"]):
        _invalid()
    return copy.deepcopy(fields)


def _producer(body, stage):
    return {
        "recording_id": body["recording_id"],
        "job_id": body["job_id"],
        "request_sha256": digest(body, MAX_DESCRIPTOR_BYTES),
        "stage": stage,
    }


_DESCRIPTOR_FIELDS = {
    "format",
    "representation",
    "policy",
    "generation",
    "recording_id",
    "job_id",
    "index",
    "attempt",
    "provider_type",
    "endpoint_sha256",
    "source_sha256",
    "input",
    "parameters",
}


def _descriptor_shape(body, recording_id, job_id, index, attempt):
    if (
        not isinstance(body, dict)
        or set(body) != _DESCRIPTOR_FIELDS
        or body["format"] != DESCRIPTOR
        or body["representation"] != "logical_sdk_request"
        or body["policy"] != POLICY
        or not _id(body["generation"])
        or body["recording_id"] != recording_id
        or body["job_id"] != job_id
        or type(body["index"]) is not int
        or body["index"] != index
        or type(body["attempt"]) is not int
        or body["attempt"] != attempt
        or body["provider_type"] not in PROVIDER_TYPES
        or not _sha(body["endpoint_sha256"])
        or not _sha(body["source_sha256"])
        or not isinstance(body["input"], dict)
        or not isinstance(body["parameters"], dict)
        or body["parameters"].keys() - REQUEST_FIELDS
    ):
        _invalid()
    canonical(body, MAX_DESCRIPTOR_BYTES)


def _descriptor(body, job, index, attempt, frozen=None):
    """Exact descriptor for this job row's frozen settings, source and prepared part."""
    params = _params(job)
    _descriptor_shape(body, job["recording_id"], job["id"], index, attempt)
    endpoint, source = frozen or (
        _endpoint(params["settings"]),
        digest(params["audio_input"], MAX_PART_BYTES),
    )
    if (
        body["provider_type"] != params["settings"]["provider_type"]
        or body["endpoint_sha256"] != endpoint
        or body["source_sha256"] != source
        or canonical(body["input"]) != canonical(_input(params, index))
    ):
        _invalid()
    check_fields(body["parameters"], params["settings"])
    return copy.deepcopy(body)


def _checkpoint(value, body, stage, creation):
    producer = _producer(body, stage)
    if creation is None:
        return artifacts.validate_checkpoint(value, producer=producer)
    return artifacts.validate_bound(value, producer=producer, creation=creation)


def _structure(value, *, recording_id, job_id, creation=None):
    """Owner-independent checks shared by Store, reports and the contained checker."""
    try:
        canonical(value, MAX_BOUND_BYTES if creation is not None else MAX_CAPTURE_BYTES)
        fields = {"format", "policy", "recording_id", "job_id", "units", "assembly_sha256"}
        if creation is not None:
            fields.add("creation")
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value["format"] != FORMAT
            or value["policy"] != POLICY
            or value["recording_id"] != recording_id
            or value["job_id"] != job_id
            or not isinstance(value["units"], dict)
            or len(value["units"]) > MAX_PARTS
            or (value["assembly_sha256"] is not None and not _sha(value["assembly_sha256"]))
        ):
            _invalid()
        if creation is not None and (
            value["creation"] != creation or value["assembly_sha256"] != creation["payload_sha256"]
        ):
            _invalid()
        shared = None
        for key, unit in value["units"].items():
            if not isinstance(key, str) or re.fullmatch(r"[0-9]{4}", key) is None:
                _invalid()
            if (
                not isinstance(unit, dict)
                or set(unit) != {"attempts", "cached_part_sha256"}
                or not isinstance(unit["attempts"], list)
                or len(unit["attempts"]) > MAX_ATTEMPTS
                or (unit["cached_part_sha256"] is not None and not _sha(unit["cached_part_sha256"]))
                or (not unit["attempts"] and unit["cached_part_sha256"] is None)
            ):
                _invalid()
            for number, item in enumerate(unit["attempts"]):
                if not isinstance(item, dict) or set(item) != {
                    "descriptor",
                    "request",
                    "response",
                    "part_sha256",
                }:
                    _invalid()
                body = item["descriptor"]
                _descriptor_shape(body, recording_id, job_id, int(key), number)
                if shared is None:
                    shared = canonical(body["parameters"])
                elif canonical(body["parameters"]) != shared:
                    _invalid()  # Every part and retry replays the first frozen fields.
                request = _checkpoint(item["request"], body, "preparation", creation)["artifacts"]
                if (
                    request["request"] == "not_captured"
                    or request["request"]["format"] != "json"
                    or request["request"]["bytes"] != len(canonical(body, MAX_DESCRIPTOR_BYTES))
                    or any(
                        request[role] != "not_captured"
                        for role in ("raw_response", "output", "environment")
                    )
                ):
                    _invalid()
                if item["response"] is None:
                    if item["part_sha256"] is not None:
                        _invalid()
                    continue
                if (
                    number != len(unit["attempts"]) - 1
                    or unit["cached_part_sha256"] is not None
                    or not _sha(item["part_sha256"])
                ):
                    _invalid()
                observed = _checkpoint(item["response"], body, "transcription", creation)
                observed = observed["artifacts"]
                if (
                    observed["request"] != request["request"]
                    or observed["output"] == "not_captured"
                    or observed["output"]["format"] != "json"
                    or observed["raw_response"] != "not_captured"
                    or observed["environment"] != "not_captured"
                ):
                    _invalid()
        return copy.deepcopy(value)
    except (KeyError, TypeError, AttributeError, RecursionError):
        _invalid()


def validate(value, job):
    """Check an unbound job document against its trusted owning row; no file reads."""
    job = _job(job)
    if not enabled(job):
        _invalid()
    value = _structure(value, recording_id=job["recording_id"], job_id=job["id"])
    params = job["params"]
    count = len(_plan(params)["chunks"])
    # Computed once per document; every descriptor must repeat the same frozen identities.
    frozen = (_endpoint(params["settings"]), digest(params["audio_input"], MAX_PART_BYTES))
    for key, unit in value["units"].items():
        if int(key) >= count:
            _invalid()
        for number, item in enumerate(unit["attempts"]):
            _descriptor(item["descriptor"], job, int(key), number, frozen)
    return value


def validate_ownership(value, job):
    """Every published part is the exact part observed for its unit and assembly."""
    job = _job(job)
    value = validate(value, job)
    plan, chunks = _plan(job["params"]), job["chunks"]
    if value["assembly_sha256"] is None or len(chunks) != len(plan["chunks"]):
        _invalid()
    if set(value["units"]) != {f"{index:04d}" for index in range(len(plan["chunks"]))}:
        _invalid()
    indexed = {}
    for part in chunks:
        if not isinstance(part, dict):
            _invalid()
        index = part.get("index")
        if type(index) is not int or index in indexed or not 0 <= index < len(plan["chunks"]):
            _invalid()
        if part.get("offset") != plan["chunks"][index].get("offset"):
            _invalid()
        unit = value["units"][f"{index:04d}"]
        recorded = unit["cached_part_sha256"]
        if recorded is None and unit["attempts"]:
            recorded = unit["attempts"][-1]["part_sha256"]
        if recorded is None or recorded != part_hash(part):
            _invalid()
        indexed[index] = part
    if assembly([indexed[index] for index in sorted(indexed)]) != value["assembly_sha256"]:
        _invalid()
    return value


def _empty(job):
    return {
        "format": FORMAT,
        "policy": POLICY,
        "recording_id": job["recording_id"],
        "job_id": job["id"],
        "units": {},
        "assembly_sha256": None,
    }


def _write(store, job, *, begin=None, response=None, assembly_sha256=None):
    """Fixed short writer. It merges into the current row and never replaces other state."""
    from . import restored_owners
    from .store import now

    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        restored_owners.require_owned(db, "job", job["id"])
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if row is None or row["recording_id"] != job["recording_id"]:
            _invalid()
        current = _job(row)
        if not enabled(current):
            _invalid()
        params, chunks = current["params"], current["chunks"]
        value = validate(params[KEYS[1]], current) if KEYS[1] in params else _empty(current)
        if value["assembly_sha256"] is not None:
            if begin is None and response is None and assembly_sha256 == value["assembly_sha256"]:
                return value
            _invalid()
        if begin is not None:
            # Terminal jobs never gain a new request; Stop is fenced by the worker's event.
            if row["state"] not in {"queued", "running"}:
                _invalid()
            body, checkpoint = begin
            unit = value["units"].setdefault(
                f"{body['index']:04d}", {"attempts": [], "cached_part_sha256": None}
            )
            if (
                unit["cached_part_sha256"] is not None
                or body["attempt"] != len(unit["attempts"])
                or (unit["attempts"] and unit["attempts"][-1]["response"] is not None)
            ):
                _invalid()
            unit["attempts"].append(
                {"descriptor": body, "request": checkpoint, "response": None, "part_sha256": None}
            )
            if len(canonical(value)) > MAX_CAPTURE_BYTES - ATTEMPT_RESERVE:
                raise ValueError(
                    "This job reached its provider evidence limit. Start a new job; "
                    "no further audio was sent."
                )
        if response is not None:
            index, body, checkpoint, part = response
            # Accepted requests drain after Stop/failure; only a committed result is final.
            if row["state"] == "completed":
                _invalid()
            if part.get("index") != index:
                _invalid()
            observed = part_hash(part)
            unit = value["units"].setdefault(
                f"{index:04d}", {"attempts": [], "cached_part_sha256": None}
            )
            if body is None:
                # A response file preserved before its observation was published remains a
                # gap. Its normalized part is still recorded; the bytes are never backfilled.
                if (
                    checkpoint is not None
                    or unit["cached_part_sha256"] not in (None, observed)
                    or (unit["attempts"] and unit["attempts"][-1]["response"] is not None)
                ):
                    _invalid()
                unit["cached_part_sha256"] = observed
            else:
                if (
                    not unit["attempts"]
                    or canonical(unit["attempts"][-1]["descriptor"]) != canonical(body)
                    or unit["cached_part_sha256"] is not None
                ):
                    _invalid()
                item = unit["attempts"][-1]
                if item["response"] is not None and (
                    canonical(item["response"]) != canonical(checkpoint)
                    or item["part_sha256"] != observed
                ):
                    _invalid()
                item["response"], item["part_sha256"] = checkpoint, observed
            indexed = {}
            for existing in chunks:
                position = existing.get("index") if isinstance(existing, dict) else None
                if type(position) is not int or position in indexed:
                    _invalid()
                indexed[position] = existing
            if index in indexed and part_hash(indexed[index]) != observed:
                _invalid()
            indexed[index] = part
            chunks = [indexed[position] for position in sorted(indexed)]
        if assembly_sha256 is not None:
            value["assembly_sha256"] = assembly_sha256
            validate_ownership(value, {**current, "chunks": chunks})
        params[KEYS[1]] = validate(value, current)
        db.execute(
            "UPDATE jobs SET params=?,chunks=?,updated=? WHERE id=?",
            (json.dumps(params), json.dumps(chunks), now(), job["id"]),
        )
    return value


def _seal(path):
    with Path(path).open("r+b") as stream:
        os.fsync(stream.fileno())


def request_path(directory, job_id, index, attempt):
    return Path(directory) / "chunks" / job_id / f"request-{index:04d}-{attempt:04d}.json"


def response_path(directory, job_id, index):
    return Path(directory) / "chunks" / job_id / f"response-{index:04d}.json"


def begin_request(store, job, index):
    """Coordinator only, before upload. Returns the recorded descriptor, or None for legacy."""
    from . import audio_inputs, providers

    current = store.job(job["id"])
    if not enabled(current):
        return None
    params = current["params"]
    audio_inputs.validate_evidence(_plan(params), params["audio_input"])
    value = validate(params[KEYS[1]], current) if KEYS[1] in params else _empty(current)
    if value["assembly_sha256"] is not None:
        _invalid()
    unit = value["units"].get(f"{index:04d}", {"attempts": [], "cached_part_sha256": None})
    if unit["cached_part_sha256"] is not None or (
        unit["attempts"] and unit["attempts"][-1]["response"] is not None
    ):
        _invalid()
    number = len(unit["attempts"])
    if number >= MAX_ATTEMPTS:
        raise ValueError("This audio part reached its provider attempt limit. Start a new job.")
    recorded = next(
        (
            item["descriptor"]["parameters"]
            for other in value["units"].values()
            for item in other["attempts"]
        ),
        None,
    )
    fields = (
        recorded
        if recorded is not None
        else providers.transcription_fields(providers.Settings.model_validate(params["settings"]))
    )
    body = {
        "format": DESCRIPTOR,
        "representation": "logical_sdk_request",
        "policy": POLICY,
        "generation": uuid.uuid4().hex,
        "recording_id": current["recording_id"],
        "job_id": current["id"],
        "index": index,
        "attempt": number,
        "provider_type": params["settings"]["provider_type"],
        "endpoint_sha256": _endpoint(params["settings"]),
        "source_sha256": digest(params["audio_input"], MAX_PART_BYTES),
        "input": _input(params, index),
        "parameters": copy.deepcopy(fields),
    }
    body = _descriptor(body, current, index, number)
    directory = Path(store.directory(current["recording_id"]))
    path = request_path(directory, current["id"], index, number)
    if path.exists():
        # A descriptor written before a failed record keeps its original generation.
        if path.is_symlink() or path.stat().st_size > MAX_DESCRIPTOR_BYTES:
            _invalid()
        raw = path.read_bytes()
        try:
            previous = json.loads(raw)
        except (ValueError, RecursionError):
            _invalid()
        previous = _descriptor(previous, current, index, number)
        if raw != canonical(previous, MAX_DESCRIPTOR_BYTES) or any(
            canonical(previous[key]) != canonical(body[key]) for key in body if key != "generation"
        ):
            _invalid()
        body = previous
    else:
        pending = path.with_name(path.name + ".pending")
        with pending.open("wb") as stream:
            stream.write(canonical(body, MAX_DESCRIPTOR_BYTES))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    _seal(path)
    captured = artifacts.capture_file(path, directory, role="request", format="json")
    checkpoint = artifacts.make_checkpoint([captured], producer=_producer(body, "preparation"))
    _write(store, current, begin=(body, checkpoint))
    return body


def observe_response(directory, job, index, body):
    """Run by the accepted part worker after preserving output; publishes nothing."""
    if body is None:
        return None
    directory = Path(directory)
    path = response_path(directory, job["id"], index)
    _seal(path)
    request = artifacts.capture_file(
        request_path(directory, job["id"], index, body["attempt"]),
        directory,
        role="request",
        format="json",
    )
    output = artifacts.capture_file(path, directory, role="output", format="json")
    return artifacts.make_checkpoint([request, output], producer=_producer(body, "transcription"))


def record_response(store, job, index, body, checkpoint, part):
    """Publish one part with its observation (or explicit cached gap) atomically."""
    return _write(store, job, response=(index, body, checkpoint, part))


def finish(store, job, parts):
    """Record the exact assembly before Store creation binds it to the result version."""
    if not enabled(job):
        return None
    return _write(store, job, assembly_sha256=assembly(parts))


def for_creation(parent, recording_id, supplied):
    """Fixed Store validator: persisted observations only, no files, callbacks or inference."""
    if parent is None:
        if supplied is not None:
            _invalid()
        return None
    parent = _job(parent)
    params = parent["params"]
    if supplied is None:
        # Omitted evidence cannot silently discard a present observation. A policy without
        # any observation (no request was ever recorded) remains an explicit gap.
        if KEYS[1] in params:
            _invalid()
        return None
    if KEYS[0] not in params or parent["recording_id"] != recording_id:
        _invalid()
    stored = validate_ownership(params.get(KEYS[1]), parent)
    if canonical(stored) != canonical(supplied):
        _invalid()
    return stored


def bind(value, *, creation):
    """Bind only in the transaction allocating the immutable sequence-one version."""
    if value["assembly_sha256"] != creation["payload_sha256"]:
        _invalid()
    value = copy.deepcopy(value)
    value["creation"] = creation
    for unit in value["units"].values():
        for item in unit["attempts"]:
            body = item["descriptor"]
            item["request"] = artifacts.bind_creation(
                item["request"], producer=_producer(body, "preparation"), creation=creation
            )
            if item["response"] is not None:
                item["response"] = artifacts.bind_creation(
                    item["response"], producer=_producer(body, "transcription"), creation=creation
                )
    canonical(value, MAX_BOUND_BYTES)
    return value


def unbound(value, *, recording_id, job_id, creation):
    """Recover the creation-time transport from a bound copy; owner rows are separate."""
    value = _structure(value, recording_id=recording_id, job_id=job_id, creation=creation)
    value.pop("creation")
    for unit in value["units"].values():
        for item in unit["attempts"]:
            item["request"].pop("creation")
            if item["response"] is not None:
                item["response"].pop("creation")
    return _structure(value, recording_id=recording_id, job_id=job_id)


def historical(value, *, parent, recording_id, creation):
    """Reports: bound copy equals the exact creation-time job observations and parts."""
    transport = unbound(value, recording_id=recording_id, job_id=parent["id"], creation=creation)
    if for_creation(parent, recording_id, transport) is None:
        _invalid()
    return transport


def public_projection(value, *, parent, recording_id, creation):
    """Hashes, sizes and part ordinals only; no IDs, paths, endpoint or transcript text."""
    verified = historical(value, parent=parent, recording_id=recording_id, creation=creation)
    parts, first = [], None
    for key in sorted(verified["units"]):
        unit = verified["units"][key]
        attempts = []
        for item in unit["attempts"]:
            body = item["descriptor"]
            first = first or body
            request = item["request"]["artifacts"]["request"]
            output = item["response"]["artifacts"]["output"] if item["response"] else None
            attempts.append(
                {
                    "attempt": body["attempt"] + 1,
                    "request": {"sha256": request["sha256"], "bytes": request["bytes"]},
                    "output": {"sha256": output["sha256"], "bytes": output["bytes"]}
                    if output
                    else "outcome_not_observed",
                    "input": copy.deepcopy(body["input"]),
                }
            )
        cached = unit["cached_part_sha256"]
        parts.append(
            {
                "part": f"part-{int(key) + 1:04d}",
                "attempts": attempts,
                # A preserved response whose observation was never published stays a gap.
                "output_observation": "not_captured" if cached is not None else "captured",
                "normalized_part_sha256": cached or unit["attempts"][-1]["part_sha256"],
            }
        )
    return {
        "format": FORMAT,
        "policy": verified["policy"],
        "provider_type": first["provider_type"] if first else "not_captured",
        "request_representation": "logical_sdk_request",
        "output_representation": "normalized_sdk_output",
        "parameters": copy.deepcopy(first["parameters"]) if first else "not_captured",
        "parts": parts,
        "assembly_sha256": verified["assembly_sha256"],
        "wire_request": "not_captured",
        "wire_response": "not_captured",
        "environment": "not_captured",
        "server_processing": "not_checked",
        "retained_files": "not_checked",
    }


def inventory(value, *, recording_id, job_id, creation=None):
    """Fixed leaf names beneath chunks/<trusted owning job ID>; prepared audio is not retained."""
    verified = (
        unbound(value, recording_id=recording_id, job_id=job_id, creation=creation)
        if creation is not None
        else _structure(value, recording_id=recording_id, job_id=job_id)
    )
    files = []
    for key in sorted(verified["units"]):
        index = int(key)
        for item in verified["units"][key]["attempts"]:
            request = item["request"]["artifacts"]["request"]
            files.append(
                {
                    "filename": f"request-{index:04d}-{item['descriptor']['attempt']:04d}.json",
                    "role": "request",
                    "sha256": request["sha256"],
                    "bytes": request["bytes"],
                }
            )
            if item["response"] is not None:
                output = item["response"]["artifacts"]["output"]
                files.append(
                    {
                        "filename": f"response-{index:04d}.json",
                        "role": "output",
                        "sha256": output["sha256"],
                        "bytes": output["bytes"],
                    }
                )
    return files
