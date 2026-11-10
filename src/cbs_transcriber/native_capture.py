"""Fresh native UCloud Transcriber submission/output observations (ADR 0100).

Only explicit fresh admission freezes the policy. Before each POST the native coordinator
writes a credential-free projection of the exact submission specification for every job
that the allocation serves; after download it observes the exact downloaded output bytes
outside writer transactions. A shared batch row keeps the remote journal while each member
job keeps its own submission/output evidence. Historical helpers accept trusted stored rows
only and import no UCloud, network or audio code, so the contained portable checker can
execute this exact source. A projection is not the HTTP request and proves no remote
execution, model weights or provider environment.
"""

import copy
import hashlib
import json
import os
import re
import uuid
from pathlib import Path, PurePosixPath

from . import provenance_artifacts as artifacts
from .history import encode_payload
from .transcripts import pop_hints

POLICY = "native-artifact-v1"
FORMAT = "cbs-native-artifacts-v1"
DESCRIPTOR = "cbs-native-submission-descriptor-v1"
KEYS = ("native_artifact_policy", "native_artifact_capture")
EXCLUDED = (
    "runtime_batch_id",
    "enhancement",
    "transcription",
    "source_snapshot",
    "ucloud_api",
    "segment_id",
    "segment_ids",
    "input_intent",
)
# Values of these declared kinds are bounded app choices or owned paths, never free text.
RECORDED_KINDS = frozenset(
    {"enumeration", "boolean", "integer", "floating_point", "input_file", "input_directory"}
)
SPEC_FIELDS = frozenset(
    {"application", "product", "name", "replicas", "timeAllocation", "sshEnabled", "resources"}
)
MAX_SUBMISSIONS = 20
MAX_DESCRIPTOR_BYTES = 256 * 1024
MAX_CAPTURE_BYTES = 4 * 1024**2
MAX_BOUND_BYTES = 5 * 1024**2
_SHA = re.compile(r"[a-f0-9]{64}")
_ID = re.compile(r"[a-f0-9]{32}")
_SUFFIX = r"\.[a-z0-9]{1,12}"
ALLOCATION_POLICY = "native-allocation-v1"
ALLOCATION_KEY = "native_allocation"
_MIG_PRODUCT = {
    "id": "gpu-nvidia-b200-1-mig.1g",
    "category": "gpu-nvidia-b200",
    "provider": "ucloud",
}


def validate_allocation(params):
    """Pure frozen-policy validation, also used by the contained portable checker."""
    if not isinstance(params, dict):
        raise ValueError("The frozen native allocation is invalid. Preserve this job.")
    if ALLOCATION_KEY not in params:
        return None  # Legacy jobs retain their exact original compute behavior.
    value = params[ALLOCATION_KEY]
    product = value.get("product") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"policy", "product"}
        or value["policy"] != ALLOCATION_POLICY
        or not isinstance(product, dict)
        or set(product) != {"id", "category", "provider"}
        or any(
            not isinstance(v, str) or not v.strip() or len(v) > 200 or any(ord(c) < 32 for c in v)
            for v in product.values()
        )
        or product["id"] not in {"gpu-nvidia-b200-1-gpu", _MIG_PRODUCT["id"]}
        or (product["id"] == _MIG_PRODUCT["id"] and product != _MIG_PRODUCT)
    ):
        raise ValueError("The frozen native allocation is invalid. Preserve this job.")
    return copy.deepcopy(value)


def _invalid():
    raise ValueError("The native artifact evidence changed or is invalid. Preserve its files.")


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


def _text(value, maximum=200):
    return isinstance(value, str) and len(value) <= maximum and not any(ord(c) < 32 for c in value)


def _decoded(value, expected):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            _invalid()
    if not isinstance(value, expected):
        _invalid()
    return value


def _job(row):
    """Decode a trusted job row once; nested checks reuse the decoded parameters."""
    job = dict(row)
    job["params"] = _decoded(job["params"], dict)
    return job


def eligible(params, kind="transcribe"):
    settings = params.get("settings") if isinstance(params, dict) else None
    return (
        kind == "transcribe"
        and isinstance(settings, dict)
        and settings.get("provider_type") == "ucloud"
        and isinstance(settings.get("base_url"), str)
        and isinstance(params.get("audio_input"), dict)
        and params.get("audio_transport") == "original"
        and not any(key in params for key in EXCLUDED)
    )


def freeze(params):
    """Only explicit fresh admission calls this; insertion, retry and recovery never do."""
    if not eligible(params):
        return params
    if params.get(KEYS[0], POLICY) != POLICY or KEYS[1] in params:
        _invalid()
    return {**params, KEYS[0]: POLICY}


def enabled(job):
    """A present marker is checked; a missing marker never derives today's policy."""
    params = _decoded(job["params"], dict)
    if KEYS[0] not in params:
        if KEYS[1] in params:
            _invalid()
        return False
    if params[KEYS[0]] != POLICY or not eligible(params, job["kind"]):
        _invalid()
    return True


def assert_params_preserved(previous, proposed):
    """Generic job writes cannot create, erase or replace native observations."""
    for key in (*KEYS, ALLOCATION_KEY):
        if (key in previous) != (key in proposed) or (
            key in previous and canonical(previous[key]) != canonical(proposed[key])
        ):
            _invalid()
    if KEYS[0] in previous or ALLOCATION_KEY in previous:
        for key in ("settings", "audio_input", "native_batch_id"):
            if (key in previous) != (key in proposed) or (
                key in previous and canonical(previous[key]) != canonical(proposed[key])
            ):
                _invalid()


def projection(specification, declared, owned):
    """Credential-free submission projection.

    Application/product/allocation fields, owned input/output paths and bounded parameter
    choices are recorded exactly. Free-text values that this app did not set (for example an
    inherited template password) are withheld by name, with no value or hash.
    """
    if not isinstance(specification, dict) or not isinstance(declared, dict):
        _invalid()
    recorded = {
        key: copy.deepcopy(specification[key]) for key in SPEC_FIELDS & specification.keys()
    }
    parameters, withheld = {}, []
    values = specification.get("parameters") or {}
    if not isinstance(values, dict):
        _invalid()
    for name, value in values.items():
        kind = declared.get(name, {}).get("type") if isinstance(declared.get(name), dict) else None
        if kind in RECORDED_KINDS or name in owned:
            parameters[name] = copy.deepcopy(value)
        else:
            withheld.append(name)
    recorded["parameters"] = parameters
    return {
        "specification": recorded,
        "withheld_parameters": sorted(withheld),
        "withheld_fields": sorted(specification.keys() - SPEC_FIELDS - {"parameters"}),
    }


def _endpoint(settings):
    return hashlib.sha256(settings["base_url"].encode("utf-8")).hexdigest()


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
    "attempt",
    "scope",
    "batch_id",
    "endpoint_sha256",
    "source_sha256",
    "input",
    "submission",
    "output_selector",
}


def _descriptor_shape(body, recording_id, job_id, attempt):
    if (
        not isinstance(body, dict)
        or set(body) != _DESCRIPTOR_FIELDS | ({ALLOCATION_KEY} if ALLOCATION_KEY in body else set())
        or body["format"] != DESCRIPTOR
        or body["representation"] != "credential_free_submission"
        or body["policy"] != POLICY
        or not _id(body["generation"])
        or body["recording_id"] != recording_id
        or body["job_id"] != job_id
        or type(body["attempt"]) is not int
        or body["attempt"] != attempt
        or body["scope"] not in {"single", "batch"}
        or (body["scope"] == "batch") != _id(body["batch_id"])
        or (body["scope"] == "single" and body["batch_id"] is not None)
        or not _sha(body["endpoint_sha256"])
        or not _sha(body["source_sha256"])
    ):
        _invalid()
    item = body["input"]
    expected = (job_id if body["scope"] == "batch" else "recording") + _SUFFIX
    if (
        not isinstance(item, dict)
        or set(item) != {"remote_name", "sha256", "bytes", "format"}
        or not isinstance(item["remote_name"], str)
        or re.fullmatch(expected, item["remote_name"]) is None
        or not _sha(item["sha256"])
        or type(item["bytes"]) is not int
        or not 0 < item["bytes"] < 2**63
        or not _text(item["format"], 16)
    ):
        _invalid()
    submission = body["submission"]
    if (
        not isinstance(submission, dict)
        or set(submission) != {"specification", "withheld_parameters", "withheld_fields"}
        or not isinstance(submission["specification"], dict)
        or submission["specification"].keys() - SPEC_FIELDS - {"parameters"}
        or not isinstance(submission["specification"].get("parameters"), dict)
        or any(
            not isinstance(names, list)
            or names != sorted(set(names))
            or any(not _text(name) for name in names)
            for names in (submission["withheld_parameters"], submission["withheld_fields"])
        )
        or set(submission["withheld_parameters"]) & submission["specification"]["parameters"].keys()
    ):
        _invalid()
    allocation = validate_allocation(body)
    if (
        allocation is not None
        and submission["specification"].get("product") != allocation["product"]
    ):
        _invalid()
    selector = body["output_selector"]
    if (
        not isinstance(selector, dict)
        or set(selector) != {"model", "language"}
        or not _text(selector["model"])
        or not _text(selector["language"], 20)
    ):
        _invalid()
    canonical(body, MAX_DESCRIPTOR_BYTES)


def _descriptor(body, job, attempt):
    """Exact descriptor for this job row's frozen settings, scope and managed original."""
    params = job["params"]
    _descriptor_shape(body, job["recording_id"], job["id"], attempt)
    source = params["audio_input"]
    if (ALLOCATION_KEY in body) != (ALLOCATION_KEY in params) or validate_allocation(
        body
    ) != validate_allocation(params):
        _invalid()
    if (
        body["endpoint_sha256"] != _endpoint(params["settings"])
        or body["source_sha256"] != digest(source)
        or body["batch_id"] != params.get("native_batch_id")
        or body["scope"] != ("batch" if params.get("native_batch_id") else "single")
        or [body["input"][key] for key in ("sha256", "bytes", "format")]
        != [source.get(key) for key in ("sha256", "bytes", "format")]
    ):
        _invalid()
    return copy.deepcopy(body)


def _match(match, body):
    """The output leaf must follow the exact input identity and frozen model/language."""
    if (
        not isinstance(match, dict)
        or set(match) != {"remote_name", "model", "language"}
        or not isinstance(match["remote_name"], str)
        or not 0 < len(match["remote_name"]) <= 255
        or "/" in match["remote_name"]
        or [match["model"], match["language"]]
        != [body["output_selector"]["model"], body["output_selector"]["language"]]
    ):
        _invalid()
    name = match["remote_name"]
    if body["scope"] == "batch":
        filename = body["input"]["remote_name"]
        stem = PurePosixPath(filename).stem
        language = re.escape(match["language"]) if match["language"] else "[a-z]{2,3}"
        names = {stem + ".json", filename + ".json"}
        if match["model"] == "parakeet":
            names.add(stem + "_parakeet.json")
        if name not in names and (
            re.fullmatch(re.escape(stem + "_" + match["model"] + "_") + language + r"\.json", name)
            is None
        ):
            _invalid()
    elif not name.endswith(".json") or "_merged" in name:
        _invalid()


def _checkpoint(value, body, stage, creation):
    producer = _producer(body, stage)
    if creation is None:
        return artifacts.validate_checkpoint(value, producer=producer)
    return artifacts.validate_bound(value, producer=producer, creation=creation)


def _structure(value, *, recording_id, job_id, creation=None):
    """Owner-independent checks shared by Store, reports and the contained checker."""
    try:
        canonical(value, MAX_BOUND_BYTES if creation is not None else MAX_CAPTURE_BYTES)
        fields = {"format", "policy", "recording_id", "job_id", "submissions", "output"}
        fields.add("assembly_sha256")
        if creation is not None:
            fields.add("creation")
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or value["format"] != FORMAT
            or value["policy"] != POLICY
            or value["recording_id"] != recording_id
            or value["job_id"] != job_id
            or not isinstance(value["submissions"], list)
            or len(value["submissions"]) > MAX_SUBMISSIONS
            or (value["assembly_sha256"] is None) != (value["output"] is None)
            or (value["assembly_sha256"] is not None and not _sha(value["assembly_sha256"]))
            or (value["output"] is not None and not value["submissions"])
        ):
            _invalid()
        if creation is not None and (
            value["creation"] != creation or value["assembly_sha256"] != creation["payload_sha256"]
        ):
            _invalid()
        request = None
        for number, item in enumerate(value["submissions"]):
            if not isinstance(item, dict) or set(item) != {"descriptor", "request"}:
                _invalid()
            body = item["descriptor"]
            _descriptor_shape(body, recording_id, job_id, number)
            if number and any(
                body[key] != value["submissions"][0]["descriptor"][key]
                for key in ("scope", "batch_id", "endpoint_sha256", "source_sha256", "input")
            ):
                _invalid()
            if number and (
                (ALLOCATION_KEY in body)
                != (ALLOCATION_KEY in value["submissions"][0]["descriptor"])
                or validate_allocation(body)
                != validate_allocation(value["submissions"][0]["descriptor"])
            ):
                _invalid()
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
        output = value["output"]
        if output is not None and output != "not_captured":
            if not isinstance(output, dict) or set(output) != {"match", "response"}:
                _invalid()
            last = value["submissions"][-1]["descriptor"]
            _match(output["match"], last)
            observed = _checkpoint(output["response"], last, "transcription", creation)
            observed = observed["artifacts"]
            if (
                observed["request"] != request["request"]
                or observed["raw_response"] == "not_captured"
                or observed["raw_response"]["format"] != "json"
                or observed["output"] != "not_captured"
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
    for number, item in enumerate(value["submissions"]):
        _descriptor(item["descriptor"], job, number)
    return value


def submission_sha256(value):
    """The journaled projection digest of the accepted (last) submission."""
    return digest(value["submissions"][-1]["descriptor"]["submission"], MAX_DESCRIPTOR_BYTES)


def _empty(job):
    return {
        "format": FORMAT,
        "policy": POLICY,
        "recording_id": job["recording_id"],
        "job_id": job["id"],
        "submissions": [],
        "output": None,
        "assembly_sha256": None,
    }


def assembly(segments):
    """The exact payload hash that Store creation computes for these parsed passages."""
    try:
        value = copy.deepcopy(segments)
        pop_hints(value)
        return encode_payload(value, {})[1]
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        _invalid()


def _write(store, job, *, submission=None, output=None):
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
        params = current["params"]
        value = validate(params[KEYS[1]], current) if KEYS[1] in params else _empty(current)
        if submission is not None:
            body, checkpoint = submission
            # A committed result is final; the POST journal already fences repeat submissions.
            if (
                row["state"] == "completed"
                or value["output"] is not None
                or body["attempt"] != len(value["submissions"])
            ):
                _invalid()
            if len(value["submissions"]) >= MAX_SUBMISSIONS:
                raise ValueError("This job reached its native submission evidence limit.")
            value["submissions"].append({"descriptor": body, "request": checkpoint})
        if output is not None:
            match, checkpoint, assembled = output
            observed = (
                "not_captured"
                if checkpoint is None
                else {
                    "match": match,
                    "response": checkpoint,
                }
            )
            if row["state"] == "completed" or not value["submissions"]:
                _invalid()
            if value["output"] is not None:
                # A reused preserved file never replaces the recorded observation.
                if value["assembly_sha256"] != assembled or (
                    checkpoint is not None and canonical(value["output"]) != canonical(observed)
                ):
                    _invalid()
                return value
            value["output"], value["assembly_sha256"] = observed, assembled
        params[KEYS[1]] = validate(value, current)
        db.execute(
            "UPDATE jobs SET params=?,updated=? WHERE id=?",
            (json.dumps(params), now(), job["id"]),
        )
    return value


def _seal(path):
    with Path(path).open("r+b") as stream:
        os.fsync(stream.fileno())


def request_path(directory, job_id, attempt):
    return Path(directory) / f"ucloud-{job_id}.request-{attempt:04d}.json"


def response_path(directory, job_id):
    return Path(directory) / f"ucloud-{job_id}.json"


def begin_submission(store, job, *, submitted, remote_name, selector):
    """Coordinator only, after inputs are verified and before POST. Returns the document.

    `submitted` is this job's projection of the exact specification about to be POSTed.
    """
    from . import audio_inputs

    current = store.job(job["id"])
    if not enabled(current):
        return None
    params = current["params"]
    source = audio_inputs.validate(params["audio_input"])
    value = validate(params[KEYS[1]], current) if KEYS[1] in params else _empty(current)
    number = len(value["submissions"])
    body = {
        "format": DESCRIPTOR,
        "representation": "credential_free_submission",
        "policy": POLICY,
        "generation": uuid.uuid4().hex,
        "recording_id": current["recording_id"],
        "job_id": current["id"],
        "attempt": number,
        "scope": "batch" if params.get("native_batch_id") else "single",
        "batch_id": params.get("native_batch_id"),
        "endpoint_sha256": _endpoint(params["settings"]),
        "source_sha256": digest(source),
        "input": {
            "remote_name": remote_name,
            "sha256": source["sha256"],
            "bytes": source["bytes"],
            "format": source["format"],
        },
        "submission": copy.deepcopy(submitted),
        "output_selector": dict(selector),
    }
    if ALLOCATION_KEY in params:
        body[ALLOCATION_KEY] = validate_allocation(params)
    body = _descriptor(body, current, number)
    directory = Path(store.directory(current["recording_id"]))
    path = request_path(directory, current["id"], number)
    raw = canonical(body, MAX_DESCRIPTOR_BYTES)
    if path.exists():
        # A descriptor written before a failed record keeps its original generation.
        if path.is_symlink() or path.stat().st_size > MAX_DESCRIPTOR_BYTES:
            _invalid()
        previous_raw = path.read_bytes()
        try:
            previous = _descriptor(json.loads(previous_raw), current, number)
        except (ValueError, RecursionError):
            _invalid()
        if previous_raw != canonical(previous, MAX_DESCRIPTOR_BYTES) or any(
            canonical(previous[key]) != canonical(body[key]) for key in body if key != "generation"
        ):
            _invalid()
        body = previous
    else:
        pending = path.with_name(path.name + ".pending")
        with pending.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    _seal(path)
    captured = artifacts.capture_file(path, directory, role="request", format="json")
    checkpoint = artifacts.make_checkpoint([captured], producer=_producer(body, "preparation"))
    return _write(store, current, submission=(body, checkpoint))


def write_output(path, data):
    """Policy jobs retain the exact downloaded bytes, durably, before observation."""
    pending = Path(path).with_name(Path(path).name + ".pending")
    with pending.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def observe_output(store, job, remote_name):
    """Hash the descriptor and downloaded output outside writer transactions."""
    current = store.job(job["id"])
    value = validate(current["params"][KEYS[1]], current)
    body = value["submissions"][-1]["descriptor"]
    directory = Path(store.directory(current["recording_id"]))
    path = response_path(directory, current["id"])
    _seal(path)
    request = artifacts.capture_file(
        request_path(directory, current["id"], body["attempt"]),
        directory,
        role="request",
        format="json",
    )
    output = artifacts.capture_file(path, directory, role="raw_response", format="json")
    match = {"remote_name": remote_name, **body["output_selector"]}
    _match(match, body)
    return match, artifacts.make_checkpoint(
        [request, output], producer=_producer(body, "transcription")
    )


def record_output(store, job, observed, segments):
    """Publish the observation, or an explicit gap for a reused preserved file, with assembly."""
    match, checkpoint = observed if observed is not None else (None, None)
    return _write(store, job, output=(match, checkpoint, assembly(segments)))


def _owner(db, parent):
    """The trusted remote journal: the batch row for members, the job row otherwise."""
    params = parent["params"]
    batch_id = params.get("native_batch_id")
    if not batch_id:
        return params.get("ucloud_run", {})
    row = db.execute(
        "SELECT params,members FROM native_batch_runs WHERE id=?", (batch_id,)
    ).fetchone()
    if row is None:
        _invalid()
    owner, members = _decoded(row[0], dict), _decoded(row[1], list)
    if (
        canonical(owner.get("settings")) != canonical(params.get("settings"))
        or (ALLOCATION_KEY in owner) != (ALLOCATION_KEY in params)
        or validate_allocation(owner) != validate_allocation(params)
    ) or not any(
        isinstance(member, dict)
        and member.get("job_id") == parent["id"]
        and member.get("recording_id") == parent["recording_id"]
        for member in members
    ):
        _invalid()
    return owner.get("ucloud_run", {})


def validate_ownership(db, value, parent):
    """Bind the document to the accepted allocation journal and its saved output hash."""
    parent = _job(parent)
    value = validate(value, parent)
    run = _owner(db, parent)
    if (
        not isinstance(run, dict)
        or value["output"] is None
        or not run.get("job_id")
        or run.get("artifact_submission_sha256") != submission_sha256(value)
    ):
        _invalid()
    if value["output"] != "not_captured" and parent["params"].get("native_batch_id"):
        received = value["output"]["response"]["artifacts"]["raw_response"]["sha256"]
        if run.get("raw_hashes", {}).get(parent["id"]) != received:
            _invalid()
    return value


def for_creation(db, parent, recording_id, supplied):
    """Fixed Store validator: persisted rows only, no files, callbacks or network."""
    if parent is None:
        if supplied is not None:
            _invalid()
        return None
    parent = _job(parent)
    params = parent["params"]
    if supplied is None:
        # Omitted evidence cannot silently discard a present observation.
        if KEYS[1] in params:
            _invalid()
        return None
    if KEYS[0] not in params or parent["recording_id"] != recording_id:
        _invalid()
    stored = validate_ownership(db, params.get(KEYS[1]), parent)
    if canonical(stored) != canonical(supplied):
        _invalid()
    return stored


def bind(value, *, creation):
    """Bind only in the transaction allocating the immutable sequence-one version."""
    if value["assembly_sha256"] != creation["payload_sha256"]:
        _invalid()
    value = copy.deepcopy(value)
    value["creation"] = creation
    for item in value["submissions"]:
        item["request"] = artifacts.bind_creation(
            item["request"],
            producer=_producer(item["descriptor"], "preparation"),
            creation=creation,
        )
    if isinstance(value["output"], dict):
        value["output"]["response"] = artifacts.bind_creation(
            value["output"]["response"],
            producer=_producer(value["submissions"][-1]["descriptor"], "transcription"),
            creation=creation,
        )
    canonical(value, MAX_BOUND_BYTES)
    return value


def unbound(value, *, recording_id, job_id, creation):
    """Recover the creation-time transport from a bound copy; owner rows are separate."""
    value = _structure(value, recording_id=recording_id, job_id=job_id, creation=creation)
    value.pop("creation")
    for item in value["submissions"]:
        item["request"].pop("creation")
    if isinstance(value["output"], dict):
        value["output"]["response"].pop("creation")
    return _structure(value, recording_id=recording_id, job_id=job_id)


def public_projection(db, value, *, parent, recording_id, creation):
    """Hashes, sizes and bounded choices only; no IDs, paths, names, endpoint or text."""
    parent = _job(parent)
    transport = unbound(value, recording_id=recording_id, job_id=parent["id"], creation=creation)
    if for_creation(db, parent, recording_id, transport) is None:
        _invalid()
    submissions = []
    for item in transport["submissions"]:
        body, request = item["descriptor"], item["request"]["artifacts"]["request"]
        specification = body["submission"]["specification"]
        submissions.append(
            {
                "attempt": body["attempt"] + 1,
                "request": {"sha256": request["sha256"], "bytes": request["bytes"]},
                "application": copy.deepcopy(specification.get("application")),
                "product_id": (specification.get("product") or {}).get("id"),
                "recorded_parameters": sorted(specification["parameters"]),
                "withheld_parameters": list(body["submission"]["withheld_parameters"]),
                "withheld_fields": list(body["submission"]["withheld_fields"]),
            }
        )
    last = transport["submissions"][-1]["descriptor"]
    output = transport["output"]
    observed = output["response"]["artifacts"]["raw_response"] if output != "not_captured" else None
    return {
        "format": FORMAT,
        "policy": transport["policy"],
        "scope": "shared_batch" if last["scope"] == "batch" else "single",
        "request_representation": "credential_free_submission",
        "submissions": submissions,
        "input": {key: last["input"][key] for key in ("sha256", "bytes", "format")},
        "output_selector": copy.deepcopy(last["output_selector"]),
        "output": {"sha256": observed["sha256"], "bytes": observed["bytes"]}
        if observed
        else "not_captured",
        "output_representation": "downloaded_output_bytes",
        "assembly_sha256": transport["assembly_sha256"],
        "remote_execution": "not_checked",
        "environment": "not_captured",
        "retained_files": "not_checked",
    }


def inventory(value, *, recording_id, job_id, creation=None):
    """Fixed leaves directly beneath the trusted recording directory; no receipt paths."""
    verified = (
        unbound(value, recording_id=recording_id, job_id=job_id, creation=creation)
        if creation is not None
        else _structure(value, recording_id=recording_id, job_id=job_id)
    )
    files = []
    for item in verified["submissions"]:
        request = item["request"]["artifacts"]["request"]
        files.append(
            {
                "filename": f"ucloud-{job_id}.request-{item['descriptor']['attempt']:04d}.json",
                "role": "request",
                "sha256": request["sha256"],
                "bytes": request["bytes"],
            }
        )
    if isinstance(verified["output"], dict):
        output = verified["output"]["response"]["artifacts"]["raw_response"]
        files.append(
            {
                "filename": f"ucloud-{job_id}.json",
                "role": "raw_response",
                "sha256": output["sha256"],
                "bytes": output["bytes"],
            }
        )
    return files
