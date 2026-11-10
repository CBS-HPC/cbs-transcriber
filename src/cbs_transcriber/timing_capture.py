"""Per-unit local timing observations; source projections and application stay distinct."""

import copy
import hashlib
import json
import math
import re
import uuid

from . import local_asr_capture as local
from . import provenance_artifacts as artifacts
from .history import encode_payload
from .transcripts import MAX_TRANSCRIPT_BYTES

POLICY = "local-timing-v1"
FORMAT = "cbs-local-timing-capture-v1"
MAX_BYTES = 2 * MAX_TRANSCRIPT_BYTES + 1024 * 1024


def _invalid():
    raise ValueError(
        "The local timing artifact evidence changed or is invalid. Preserve its files."
    )


def _encoded(value):
    try:
        data = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(data) > MAX_BYTES:
            _invalid()
        return data
    except (TypeError, ValueError, RecursionError):
        _invalid()


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _units(params):
    source = params["source_snapshot"]
    if "timing_units" in params:
        units = params["timing_units"]
    else:
        selected = [row for row in source["segments"] if row["id"] == params.get("segment_id")]
        if len(selected) != 1:
            _invalid()
        row = selected[0]
        units = [{"ids": [row["id"]], "start": row["start"], "end": row["end"]}]
    known = {row["id"] for row in source["segments"]}
    seen = set()
    if not isinstance(units, list) or not 0 < len(units) <= 1000:
        _invalid()
    for unit in units:
        if (
            not isinstance(unit, dict)
            or set(unit) != {"ids", "start", "end"}
            or not isinstance(unit["ids"], list)
            or not unit["ids"]
            or any(
                not isinstance(identifier, str) or identifier not in known or identifier in seen
                for identifier in unit["ids"]
            )
            or any(
                type(unit[key]) not in (int, float) or not math.isfinite(unit[key])
                for key in ("start", "end")
            )
            or not 0 <= unit["start"] < unit["end"] <= 6 * 3600
            or unit["end"] - unit["start"] > 60
        ):
            _invalid()
        if len(set(unit["ids"])) != len(unit["ids"]):
            _invalid()
        seen.update(unit["ids"])
    return units


def eligible(params):
    if not isinstance(params, dict) or not isinstance(params.get("enhancement"), dict):
        return False
    options = params.get("enhancement", {})
    return (
        params.get("realign") is True
        and options.get("execution") == "local"
        and options.get("align") is True
        and options.get("diarize") is False
        and options.get("transcribe") is False
        and options.get("retranscribe") is False
        and isinstance(params.get("source_snapshot"), dict)
        and ("timing_units" in params or "segment_id" in params)
        and not any(
            key in params
            for key in (
                "ucloud_api",
                "cloud_settings",
                "runtime_batch_id",
                "native_batch_id",
                "repair_cloud",
            )
        )
    )


def freeze(params):
    """Only explicit fresh enqueue calls this; frozen retries never acquire the policy."""
    if not eligible(params):
        return params
    if "timing_artifact_capture" in params:
        _invalid()
    _source(params)
    _units(params)
    return {**params, "timing_artifact_policy": POLICY}


def _source(params):
    source = params["source_snapshot"]
    if (
        not isinstance(source, dict)
        or any(
            not isinstance(source.get(key), str) or not re.fullmatch(r"[a-f0-9]{32}", source[key])
            for key in ("id", "source_version_id")
        )
        or not isinstance(source.get("source_payload_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", source["source_payload_sha256"])
        or not isinstance(source.get("segments"), list)
        or not source["segments"]
        or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str)
            for row in source["segments"]
        )
    ):
        _invalid()
    _, projection = encode_payload(source["segments"], source["speakers"])
    return {
        "result_id": source["id"],
        "version_id": source["source_version_id"],
        "payload_sha256": source["source_payload_sha256"],
        "projection_sha256": projection,
    }


def validate(value, job):
    params = job["params"] if isinstance(job["params"], dict) else json.loads(job["params"])
    if (
        job["kind"] != "enhance"
        or not eligible(params)
        or params.get("timing_artifact_policy") != POLICY
    ):
        _invalid()
    _encoded(value)
    if (
        not isinstance(value, dict)
        or set(value) != {"format", "source", "units", "assembled_output_sha256"}
        or value["format"] != FORMAT
        or value["source"] != _source(params)
        or not isinstance(value["units"], dict)
    ):
        _invalid()
    units = _units(params)
    for index, unit in value["units"].items():
        if index not in {f"{i:05d}" for i in range(len(units))}:
            _invalid()
        if unit == "not_captured":
            continue
        if (
            not isinstance(unit, dict)
            or set(unit) != {"unit_sha256", "capture"}
            or unit["unit_sha256"] != _hash(units[int(index)])
        ):
            _invalid()
        captured = local.validate(
            unit["capture"], recording_id=job["recording_id"], job_id=job["id"]
        )
        if captured["checkpoints"].keys() - {"alignment"}:
            _invalid()
    if value["assembled_output_sha256"] is not None:
        if set(value["units"]) != {f"{i:05d}" for i in range(len(units))} or value[
            "assembled_output_sha256"
        ] != _hash(params.get("realign_output")):
            _invalid()
    return copy.deepcopy(value)


def assert_params_preserved(previous, proposed):
    for key in ("timing_artifact_policy", "timing_artifact_capture"):
        if _encoded(previous.get(key)) != _encoded(proposed.get(key)):
            _invalid()
    captured = previous.get("timing_artifact_capture")
    if captured is not None:
        for key in (
            "enhancement",
            "source_snapshot",
            "timing_units",
            "segment_id",
            "segment_ids",
            "audio_input",
        ):
            if _encoded(previous.get(key)) != _encoded(proposed.get(key)):
                _invalid()
        if captured.get("assembled_output_sha256") is not None and _hash(
            previous.get("realign_output")
        ) != _hash(proposed.get("realign_output")):
            _invalid()


def _publish(store, job, value, *, output=None, message=None):
    from .store import now

    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        from . import restored_owners

        restored_owners.require_owned(db, "job", job["id"])
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if row is None or row["recording_id"] != job["recording_id"] or row["state"] != "running":
            _invalid()
        params = json.loads(row["params"])
        if output is not None:
            params["realign_output"] = output
        owning = {**dict(row), "params": params}
        value = validate(value, owning)
        previous = params.get("timing_artifact_capture")
        if previous is not None:
            if previous["source"] != value["source"]:
                _invalid()
            for index, before in previous["units"].items():
                after = value["units"].get(index)
                if before == "not_captured" or after == "not_captured":
                    if before != after:
                        _invalid()
                    continue
                old, new = before["capture"], after["capture"]
                if (
                    before["unit_sha256"] != after["unit_sha256"]
                    or old["origin"] != new["origin"]
                    or any(
                        new["checkpoints"].get(stage) != receipt
                        for stage, receipt in old["checkpoints"].items()
                    )
                ):
                    _invalid()
            if previous["assembled_output_sha256"] is not None and previous != value:
                _invalid()
        params["timing_artifact_capture"] = value
        db.execute(
            "UPDATE jobs SET params=?,message=COALESCE(?,message),updated=? WHERE id=?",
            (json.dumps(params, ensure_ascii=False, allow_nan=False), message, now(), row["id"]),
        )
    job["params"] = params
    return value


def enabled(job):
    return job["params"].get("timing_artifact_policy") == POLICY


def capture_enabled(job, folder, index):
    captured = job["params"].get("timing_artifact_capture", {}).get("units", {}).get(f"{index:05d}")
    return (
        enabled(job)
        and captured != "not_captured"
        and (isinstance(captured, dict) or not (folder / "alignment.json").exists())
    )


def verify_request(job, folder, index, expected_bytes):
    unit = job["params"].get("timing_artifact_capture", {}).get("units", {}).get(f"{index:05d}")
    if isinstance(unit, dict):
        local.verify_request(
            {**job, "params": {"producer_artifact_capture": unit["capture"]}},
            folder,
            expected_bytes,
        )
        local.verify_files(unit["capture"], folder)


def begin_unit(store, job, folder, index):
    from .enhancement_worker import language_binding

    value = job["params"].get("timing_artifact_capture") or {
        "format": FORMAT,
        "source": _source(job["params"]),
        "units": {},
        "assembled_output_sha256": None,
    }
    value = validate(value, job)
    key = f"{index:05d}"
    previous = value["units"].get(key)
    if isinstance(previous, dict):
        local.verify_files(previous["capture"], folder)
        local._worker_origin(folder, previous["capture"])
        return previous["capture"]
    if previous == "not_captured" or (folder / "alignment.json").exists():
        return None
    local.seal(folder / "request.json")
    local.seal(folder / "audio.wav")
    request = artifacts.capture_file(folder / "request.json", folder, role="request", format="json")
    origin = {
        "generation": uuid.uuid4().hex,
        "recording_id": job["recording_id"],
        "job_id": job["id"],
        "audio_binding": language_binding(folder),
    }
    origin["request_checkpoint"] = artifacts.make_checkpoint(
        [request], producer=local._producer(origin, "preparation")
    )
    captured = {"format": local.FORMAT, "origin": origin, "checkpoints": {}}
    value["units"][key] = {"unit_sha256": _hash(_units(job["params"])[index]), "capture": captured}
    _publish(store, job, value)
    local._worker_origin(folder, captured)
    return captured


def complete_unit(store, job, folder, index):
    value = validate(job["params"]["timing_artifact_capture"], job)
    captured = value["units"][f"{index:05d}"]["capture"]
    local.verify_files(captured, folder)
    if "alignment" not in captured["checkpoints"]:
        local.seal(folder / "alignment.json")
        captures = [
            artifacts.capture_file(folder / filename, folder, role=role, format="json")
            for role, filename in (("request", "request.json"), ("output", "alignment.json"))
        ]
        environment = folder / "alignment.environment.json"
        if environment.exists():
            local.seal(environment)
            captures.append(
                artifacts.capture_file(environment, folder, role="environment", format="json")
            )
        captured["checkpoints"]["alignment"] = artifacts.make_checkpoint(
            captures, producer=local._producer(captured["origin"], "alignment")
        )
        _publish(store, job, value)
    return captured


def verify_files(job, folder, index):
    value = validate(job["params"]["timing_artifact_capture"], job)
    unit = value["units"].get(f"{index:05d}")
    if isinstance(unit, dict):
        local.verify_files(unit["capture"], folder)


def publish_output(store, job, output, message):
    value = copy.deepcopy(job["params"].get("timing_artifact_capture")) or {
        "format": FORMAT,
        "source": _source(job["params"]),
        "units": {},
        "assembled_output_sha256": None,
    }
    for index in range(len(_units(job["params"]))):
        value["units"].setdefault(f"{index:05d}", "not_captured")
    value["assembled_output_sha256"] = _hash(output)
    return _publish(store, job, value, output=output, message=message)


def for_application(parent, recording_id, result_id, applied_ids):
    params = json.loads(parent["params"]) if isinstance(parent["params"], str) else parent["params"]
    if params.get("timing_artifact_policy") is None:
        return None
    if (
        parent["recording_id"] != recording_id
        or params.get("source_snapshot", {}).get("id") != result_id
    ):
        _invalid()
    value = validate(params.get("timing_artifact_capture"), {**dict(parent), "params": params})
    allowed = {identifier for unit in _units(params) for identifier in unit["ids"]}
    if (
        value["assembled_output_sha256"] is None
        or not isinstance(applied_ids, list)
        or not applied_ids
        or len(set(applied_ids)) != len(applied_ids)
        or any(identifier not in allowed for identifier in applied_ids)
    ):
        _invalid()
    return {**value, "applied_ids": list(applied_ids), "application_job_id": parent["id"]}


def bind(value, *, creation):
    value = copy.deepcopy(value)
    value["creation"] = creation
    for unit in value["units"].values():
        if isinstance(unit, dict):
            unit["capture"] = local.bind(unit["capture"], creation=creation)
    return value


def validate_bound(value, job, *, creation):
    """Prove exact application row and immutable job transport without any artifact reads."""
    _encoded(value)
    if not isinstance(value, dict) or set(value) != {
        "format",
        "source",
        "units",
        "assembled_output_sha256",
        "applied_ids",
        "application_job_id",
        "creation",
    }:
        _invalid()
    if not isinstance(value["units"], dict):
        _invalid()
    value = copy.deepcopy(value)
    if value["application_job_id"] != job["id"] or value["creation"] != creation:
        _invalid()
    historical_references(value, job)
    for unit in value["units"].values():
        if isinstance(unit, dict):
            unit["capture"] = local.unbound(
                unit["capture"],
                recording_id=job["recording_id"],
                job_id=job["id"],
                creation=creation,
            )
    params = job["params"] if isinstance(job["params"], dict) else json.loads(job["params"])
    ids = value.pop("applied_ids")
    value.pop("application_job_id")
    value.pop("creation")
    checked = validate(value, job)
    if checked != params.get("timing_artifact_capture"):
        _invalid()
    receipt = params.get("realign_apply_receipt", {})
    if (
        params.get("realign_applied") != "applied"
        or receipt.get("version")
        != {"id": creation["version_id"], "payload_sha256": creation["payload_sha256"]}
        or receipt.get("applied_ids") != ids
    ):
        _invalid()
    return checked


def historical_references(value, job):
    """Check bounded scalar references before a report uses them as database lookup keys."""
    try:
        _encoded(value)
        params = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
        if not isinstance(value, dict) or value.get("application_job_id") != job["id"]:
            _invalid()
        references = [value["creation"], value["source"]]
        for reference in references:
            if (
                any(
                    not isinstance(reference.get(key), str)
                    or not re.fullmatch(r"[a-f0-9]{32}", reference[key])
                    for key in ("result_id", "version_id")
                )
                or not isinstance(reference.get("payload_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", reference["payload_sha256"])
            ):
                _invalid()
        receipt = params["realign_apply_receipt"]
        if receipt.get("format") != "cbs-local-timing-apply-v1":
            _invalid()
        for name in ("version", "previous"):
            reference = receipt[name]
            if (
                not isinstance(reference, dict)
                or set(reference) != {"id", "payload_sha256"}
                or not isinstance(reference["id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", reference["id"])
                or not isinstance(reference["payload_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", reference["payload_sha256"])
            ):
                _invalid()
        if references[0]["result_id"] != references[1]["result_id"] or receipt["version"] != {
            "id": references[0]["version_id"],
            "payload_sha256": references[0]["payload_sha256"],
        }:
            _invalid()
        return [
            *references,
            {
                "result_id": references[0]["result_id"],
                "version_id": receipt["previous"]["id"],
                "payload_sha256": receipt["previous"]["payload_sha256"],
            },
        ]
    except (KeyError, TypeError, AttributeError, RecursionError):
        _invalid()


def verify_historical(row, captured):
    """Reconstruct the complete saved successor using only budgeted trusted snapshot rows."""
    from .local_timing import _successor

    evidence = row["context"]["timing_artifacts"]
    job = row["timing_artifact_parent"]
    params = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
    try:
        applied = captured[evidence["creation"]["version_id"]]
        previous_ref = params["realign_apply_receipt"]["previous"]
        previous = captured[previous_ref["id"]]
        source = captured[evidence["source"]["version_id"]]
        creation = {
            "result_id": applied["result_id"],
            "version_id": applied["id"],
            "payload_sha256": applied["payload_sha256"],
        }
        validate_bound(evidence, job, creation=creation)
        if (
            applied["recording_id"] != row["recording_id"]
            or applied["action"] != "timing_realign"
            or applied["result_id"] != row["result_id"]
            or applied["context"].get("job_id") != job["id"]
            or applied["context"].get("timing_artifacts") != evidence
            or applied["predecessor_id"] != previous["id"]
            or previous["result_id"] != applied["result_id"]
            or previous["payload_sha256"] != previous_ref["payload_sha256"]
            or previous["sequence"] + 1 != applied["sequence"]
            or applied["source_version_id"] != previous["source_version_id"]
            or source["result_id"] != row["result_id"]
            or source["payload_sha256"] != evidence["source"]["payload_sha256"]
            or row["context"]["timing_artifacts"] != evidence
        ):
            _invalid()
        frozen = {passage["id"]: passage for passage in params["source_snapshot"]["segments"]}
        original = {passage["id"]: passage for passage in source["payload"]["segments"]}
        if params["source_snapshot"]["speakers"] != source["payload"]["speakers"] or any(
            _hash(original.get(identifier)) != _hash(passage)
            for identifier, passage in frozen.items()
        ):
            _invalid()
        output = params["realign_output"]
        outputs = output.get("passages") if "passages" in output else {params["segment_id"]: output}
        if not isinstance(outputs, dict) or not outputs or outputs.keys() - frozen.keys():
            _invalid()
        successor, ids, skipped = _successor(previous["payload"]["segments"], frozen, outputs)
        if (
            encode_payload(successor, previous["payload"]["speakers"])[1]
            != applied["payload_sha256"]
            or ids != evidence["applied_ids"]
            or skipped != params["realign_apply_receipt"]["skipped_ids"]
        ):
            _invalid()
    except (KeyError, TypeError, RecursionError):
        _invalid()


def public_projection(row, version_aliases):
    evidence = row["context"]["timing_artifacts"]
    job = row["timing_artifact_parent"]
    creation = evidence["creation"]
    return {
        "format": FORMAT,
        "source_version": version_aliases[evidence["source"]["version_id"]],
        "application_version": version_aliases[creation["version_id"]],
        "source_projection_sha256": evidence["source"]["projection_sha256"],
        "assembled_output_sha256": evidence["assembled_output_sha256"],
        "applied_passages": len(evidence["applied_ids"]),
        "units": [
            {"unit": f"unit-{int(index) + 1:03}", "observation": "not_captured"}
            if unit == "not_captured"
            else {
                "unit": f"unit-{int(index) + 1:03}",
                "observation": local.public_projection(
                    unit["capture"],
                    recording_id=row["recording_id"],
                    job_id=job["id"],
                    creation=creation,
                ),
            }
            for index, unit in sorted(evidence["units"].items())
        ],
        "retained_files": "not_checked",
    }


def inventory(value, job, *, creation=None):
    """Pure fixed relative paths, derived only from the trusted producing job's unit mode."""
    if creation is None:
        validate(value, job)
    else:
        validate_bound(value, job, creation=creation)
    params = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
    files = []
    for index, unit in sorted(value["units"].items()):
        if isinstance(unit, dict):
            prefix = f"units/{index}/" if "timing_units" in params else ""
            for item in local.inventory(
                unit["capture"],
                recording_id=job["recording_id"],
                job_id=job["id"],
                creation=creation,
            ):
                files.append({**item, "filename": prefix + item["filename"]})
    return files
