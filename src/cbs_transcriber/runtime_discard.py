"""Explicit retained-run abandonment; read/preserve first, durable consent before Trash."""

import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path

import httpx
from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api
from . import runtime_batches as batches
from . import runtime_store as storage
from . import ucloud_native as native
from .history import decode_payload
from .runtime_batch_remote import AUDIO_LIMIT, ERROR_CODES, OUTPUT_NAMES, _parse, _receipt, _safe
from .store import now


def _hash(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _file(path, base, expected=None):
    _safe(path)
    if not path.resolve().is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError("A required local runtime source or preserved file is missing.")
    before = path.stat()
    result = batches.hash_file(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("A local runtime source changed during preservation checks.")
    if expected is not None and result != expected:
        raise ValueError("A preserved runtime file differs from its exact receipt.")
    return result


def _archive(store, batch_id, task_id, name, raw):
    if not re.fullmatch(r"[a-f0-9]{32}", task_id) or "/" in name or "\\" in name:
        raise ValueError("Invalid runtime preservation path.")
    base = store.root / "runtime-batches" / batch_id / "discard-evidence"
    folder = base / task_id
    _safe(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    _safe(path)
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("A preserved runtime artifact changed; the remote files were retained.")
    batches.durable_write(path, raw)
    return _file(path, base, _hash(raw))


def _read(client, server, folder, name, limit=api.MAX_STATUS_BYTES):
    raw = api.read_remote(client, server, folder + "/" + name, limit)
    if raw is None:
        return None, None
    try:
        value = _parse(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError(
            "A retained runtime control file is malformed; nothing was discarded."
        ) from None
    if not isinstance(value, dict):
        raise ValueError("A retained runtime control file has an invalid shape.")
    return value, raw


def _terminal(client, batch):
    remote = native.cloud_call(client.retrieve_job, batch["journal"]["job_id"])
    if (
        not isinstance(remote, dict)
        or not isinstance(remote.get("specification"), dict)
        or not isinstance(remote.get("status"), dict)
    ):
        raise ValueError("The runtime allocation returned malformed ownership metadata.")
    spec, state = remote["specification"], remote["status"].get("state")
    folder = batches.remote_folder(batches._settings(batch), batch["id"])
    resources = spec.get("resources")
    if (
        str(remote.get("id")) != str(batch["journal"]["job_id"])
        or spec.get("name") != "CBS Runtime batch " + batch["id"]
        or spec.get("name") != batch["journal"].get("run_name")
        or spec.get("application") != batch["identity"]["application"]
        or spec.get("product") != batch["identity"]["runtime_product"]
        or not isinstance(resources, list)
        or sum(
            isinstance(r, dict)
            and r.get("type") == "file"
            and r.get("path") == folder
            and r.get("readOnly") is False
            for r in resources
        )
        != 1
    ):
        raise ValueError("The fresh remote allocation does not match the exact owned runtime run.")
    if state not in api.TERMINAL:
        raise ValueError("Stop the runtime allocation and wait for terminal confirmation first.")
    return {"job_id": str(remote["id"]), "state": state, "owned_folder": folder}


def _eligible(batch):
    journal = batch["journal"]
    if (
        batch["state"] in {"queued", "running"}
        or batch["admission"] not in {"sealed", "closed"}
        or journal.get("recovery_requested")
        or storage._uncertain(journal)
        or not journal.get("job_id")
        or journal.get("folder_created") is not True
        or journal.get("owned_folder")
        != batches.remote_folder(batches._settings(batch), batch["id"])
    ):
        raise ValueError("Discard requires an inactive, closed, exactly owned runtime allocation.")


def _originals(store, batch):
    result = {}
    deleted = storage.deleted_members(store, batch)
    for member in batch["members"]:
        if member["job_id"] in deleted:
            continue
        storage.for_job(store, member["job_id"])
        recording = store.recording(member["recording_id"])
        base = store.directory(recording["id"])
        name = "original" + Path(recording["filename"]).suffix.lower()
        receipt = _file(base / name, base)
        if not receipt["size"]:
            raise ValueError("Preserve a nonempty local original before discarding remote files.")
        result[member["job_id"]] = {"file": name, **receipt}
        if member["result_id"]:
            storage._verify_files(
                store,
                member["job_id"],
                member["recording_id"],
                member["output_receipt"].get("files"),
            )
            versions = store.all(
                "SELECT payload,payload_sha256 FROM result_versions WHERE result_id=?",
                (member["result_id"],),
            )
            if not versions:
                raise ValueError("A saved runtime result has no preserved research version.")
            for version in versions:
                decode_payload(version["payload"], version["payload_sha256"])
    return result


def _attempt(store, client, batch, attempt, member):
    """Inventory exact prior attempts too; current stage files may have been replaced on retry."""
    task = attempt["task_id"]
    if not isinstance(task, str) or not re.fullmatch(r"[a-f0-9]{32}", task):
        raise ValueError("The retained runtime task has an invalid owned identifier.")
    folder = batches.remote_folder(batches._settings(batch), batch["id"]) + "/tasks/" + task
    server = batches._settings(batch).base_url
    recorded = json.loads(attempt["input_receipt"])
    binding = {
        "protocol": storage.PROTOCOL,
        "batch_id": batch["id"],
        "identity_hash": batch["compatibility_hash"],
        "task_id": task,
        "job_id": member["job_id"],
        "attempt": attempt["attempt"],
    }
    accepted, accepted_raw = _read(client, server, folder, "accepted.json")
    outcome, outcome_raw = _read(client, server, folder, "outcome.json")
    ready, ready_raw = _read(client, server, folder, "ready.json")
    inputs, inputs_raw = _read(client, server, folder, "inputs.json")
    archive = {}
    if (
        accepted
        or ready
        or (outcome and outcome.get("state") != "not_dispatched")
        or inputs
        or recorded.get("publish_intent")
    ):
        if (
            not recorded.get("publish_intent")
            or not isinstance(recorded.get("files"), dict)
            or not {"request.json", "audio.wav"} <= recorded["files"].keys()
        ):
            raise ValueError("Published runtime inputs have no exact local identity receipt.")
        expected = recorded.get("manifest_sha256")
        if inputs is not None:
            if (
                any(inputs.get(k) != v for k, v in binding.items())
                or _hash(inputs_raw)["sha256"] != expected
                or inputs.get("files") != recorded["files"]
                or not isinstance(inputs.get("expected_outputs"), list)
                or not all(isinstance(n, str) for n in inputs["expected_outputs"])
                or set(inputs["expected_outputs"]) != storage._required_stages(batch["identity"])
            ):
                raise ValueError("The runtime input manifest differs from its frozen receipt.")
        elif accepted or ready:
            raise ValueError(
                "Published runtime inputs are incomplete; retain the run for recovery."
            )
        if ready and (
            ready.get("protocol") != storage.PROTOCOL
            or ready.get("batch_id") != batch["id"]
            or ready.get("task_id") != task
            or ready.get("inputs_sha256") != expected
            or ready.get("inputs_size") != len(inputs_raw)
        ):
            raise ValueError("A runtime ready descriptor differs from its exact input manifest.")
        if accepted and (
            any(accepted.get(k) != v for k, v in binding.items())
            or accepted.get("inputs_sha256") != expected
            or accepted.get("inputs_size") != len(inputs_raw)
        ):
            raise ValueError("A runtime acceptance receipt belongs to another input.")
        base = store.directory(member["recording_id"]) / "enhancements" / member["job_id"]
        for name, expected_file in recorded["files"].items():
            if name not in {"request.json", "audio.wav", *OUTPUT_NAMES}:
                raise ValueError("The runtime input receipt includes an unexpected filename.")
            # Retries share audio but may have a different request/checkpoint. Preserve the
            # latter from that task's immutable remote directory, never overwrite the current one.
            if name == "audio.wav":
                if not _receipt(expected_file, AUDIO_LIMIT):
                    raise ValueError("Invalid runtime audio input receipt.")
                _file(base / name, base, expected_file)
            else:
                if not _receipt(expected_file, api.MAX_STAGE_BYTES):
                    raise ValueError("Invalid runtime bounded input receipt.")
                raw = api.read_remote(client, server, folder + "/" + name, api.MAX_STAGE_BYTES)
                if raw is None:
                    _file(base / name, base, expected_file)
                    raw = (base / name).read_bytes()
                if _hash(raw) != expected_file:
                    raise ValueError("A frozen runtime input differs from its preserved receipt.")
                archive["input-" + name] = _archive(store, batch["id"], task, "input-" + name, raw)
    required = storage._required_stages(batch["identity"])
    if (
        outcome
        and outcome.get("state") == "not_dispatched"
        and (
            ready
            or inputs
            or recorded.get("publish_intent")
            or api.read_remote(client, server, folder + "/request.json", api.MAX_STAGE_BYTES)
            is not None
            or api.read_remote(client, server, folder + "/audio.wav", api.MAX_STAGE_BYTES)
            is not None
        )
    ):
        raise ValueError("An undispatched runtime task cannot have private input publication.")
    if outcome:
        if (
            any(outcome.get(k) != v for k, v in binding.items())
            or set(outcome)
            != set(binding) | {"inputs_sha256", "inputs_size", "state", "error_code", "outputs"}
            or outcome.get("state") not in {"complete", "failed", "interrupted", "not_dispatched"}
            or not isinstance(outcome.get("outputs"), dict)
            or not set(outcome["outputs"]) <= required
            or not all(_receipt(r, api.MAX_STAGE_BYTES) for r in outcome["outputs"].values())
            or (outcome["state"] == "complete" and outcome.get("error_code") is not None)
            or (outcome["state"] != "complete" and outcome.get("error_code") not in ERROR_CODES)
            or (outcome["state"] == "complete" and set(outcome["outputs"]) != required)
            or (
                outcome["state"] != "not_dispatched"
                and (
                    not accepted
                    or outcome.get("inputs_sha256") != accepted.get("inputs_sha256")
                    or outcome.get("inputs_size") != accepted.get("inputs_size")
                )
            )
            or (
                outcome["state"] == "not_dispatched"
                and (
                    accepted
                    or outcome.get("inputs_sha256") is not None
                    or outcome.get("inputs_size") is not None
                    or outcome["outputs"]
                )
            )
        ):
            raise ValueError("A terminal runtime output lacks its exact accepted input identity.")
    for name in sorted(required):
        raw = api.read_remote(client, server, folder + "/" + name, api.MAX_STAGE_BYTES)
        expected_file = outcome.get("outputs", {}).get(name) if outcome else None
        if expected_file is not None and (raw is None or _hash(raw) != expected_file):
            raise ValueError("A successful runtime output is missing or differs from its receipt.")
        if raw is not None:
            archive[name] = _archive(store, batch["id"], task, name, raw)
    for name, raw in (
        ("accepted.json", accepted_raw),
        ("outcome.json", outcome_raw),
        ("ready.json", ready_raw),
        ("inputs.json", inputs_raw),
    ):
        if raw is not None:
            archive[name] = _archive(store, batch["id"], task, name, raw)
    if outcome and outcome["state"] == "complete" and not member["result_id"]:
        if batch["journal"].get("discard_confirmed"):
            raise ValueError(
                "New complete output appeared after abandonment and was preserved locally. "
                "Retained remote files need an explicit recovery review."
            )
        raise ValueError(
            "A complete runtime result is recoverable. Reconnect and save it before discard."
        )
    return {
        "task_id": task,
        "attempt": attempt["attempt"],
        "job_id": member["job_id"],
        "outcome": outcome.get("state") if outcome else "unconfirmed",
        "files": archive,
        "verified_stages": sorted(outcome["outputs"]) if outcome else [],
    }


def _prepare(store, client, identifier):
    with store.connection() as db:
        db.execute("BEGIN")
        digest, batch, attempts = storage.discard_snapshot(db, identifier)
    _eligible(batch)
    remote = _terminal(client, batch)
    originals = _originals(store, batch)
    deleted = storage.deleted_members(store, batch)
    members = {m["job_id"]: m for m in batch["members"]}
    # A deleted recording's files were released by its Delete (ADR 0101); none are archived.
    attempts = [a for a in attempts if a["job_id"] not in deleted]
    archives = [_attempt(store, client, batch, a, members[a["job_id"]]) for a in attempts]
    if _originals(store, batch) != originals:
        raise ValueError("A local original changed during preservation; preview again.")
    for attempt in attempts:
        receipt = json.loads(attempt["input_receipt"]).get("files", {}).get("audio.wav")
        if receipt:
            member = members[attempt["job_id"]]
            base = store.directory(member["recording_id"]) / "enhancements" / member["job_id"]
            _file(base / "audio.wav", base, receipt)
    evidence = {
        "remote": remote,
        "originals": originals,
        "attempts": archives,
        "missing_results": [
            m["job_id"]
            for m in batch["members"]
            if not m["result_id"] and m["job_id"] not in deleted
        ],
    }
    with store.connection() as db:
        current, _, _ = storage.discard_snapshot(db, identifier)
    if current != digest:
        raise ValueError("The runtime run changed while preserving files; preview again.")
    fingerprint = storage._digest({"snapshot": digest, "evidence": evidence})
    return batch, digest, evidence, fingerprint


def _summary(store, batch, evidence, fingerprint):
    members = []
    deleted = storage.deleted_members(store, batch)
    for member in batch["members"]:
        members.append(
            {
                "job_id": member["job_id"],
                "recording_name": "Removed recording"
                if member["job_id"] in deleted
                else store.recording(member["recording_id"])["name"],
                "state": member["state"],
                "result_saved": bool(member["result_id"]),
                "partial_stages": sum(
                    len(a["verified_stages"])
                    for a in evidence["attempts"]
                    if a["job_id"] == member["job_id"]
                ),
                "unverified_stages": sum(
                    name in OUTPUT_NAMES and name not in a["verified_stages"]
                    for a in evidence["attempts"]
                    if a["job_id"] == member["job_id"]
                    for name in a["files"]
                ),
            }
        )
    return {
        "runtime_batch_id": batch["id"],
        "fingerprint": fingerprint,
        "members": members,
        "missing_results": evidence["missing_results"],
        "count": len(members),
        "already_confirmed": bool(batch["journal"].get("discard_confirmed")),
    }


def _preserved(store, batch, decision):
    if _originals(store, batch) != decision["evidence"]["originals"]:
        raise ValueError("A local original changed after confirmation; remote files were retained.")
    base = store.root / "runtime-batches" / batch["id"] / "discard-evidence"
    for attempt in decision["evidence"]["attempts"]:
        for name, receipt in attempt["files"].items():
            _file(base / attempt["task_id"] / name, base, receipt)
    with store.connection() as db:
        db.execute("BEGIN")
        _, _, attempts = storage.discard_snapshot(db, batch["id"])
    deleted = storage.deleted_members(store, batch)
    members = {m["job_id"]: m for m in batch["members"]}
    for attempt in attempts:
        if attempt["job_id"] in deleted:
            continue
        audio = json.loads(attempt["input_receipt"]).get("files", {}).get("audio.wav")
        if audio:
            member = members[attempt["job_id"]]
            folder = store.directory(member["recording_id"]) / "enhancements" / member["job_id"]
            _file(folder / "audio.wav", folder, audio)


def _unchanged_remote(store, client, batch, decision):
    """A failed Trash request never authorizes dropping newly visible remote outputs."""
    with store.connection() as db:
        db.execute("BEGIN")
        _, current, attempts = storage.discard_snapshot(db, batch["id"])
    deleted = storage.deleted_members(store, current)
    members = {m["job_id"]: m for m in current["members"]}
    inventory = [
        _attempt(store, client, current, a, members[a["job_id"]])
        for a in attempts
        if a["job_id"] not in deleted
    ]
    if inventory != decision["evidence"]["attempts"]:
        raise ValueError(
            "Remote runtime evidence changed after confirmation; "
            "new files were preserved and the run retained."
        )


@contextmanager
def _record_refusal(store, identifier):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    try:
        yield
    except (ValueError, OSError, UCloudAPIError, httpx.HTTPError) as exc:
        batch = storage.get(store, identifier)
        if (
            batch["journal"].get("discard_decision")
            and batch["journal"].get("cleanup_state") not in native.CLEANED
        ):
            storage.checkpoint(
                store,
                identifier,
                batch["epoch"],
                journal_update={
                    **native.cleanup_diagnostic(exc, "discard_validation"),
                    "cleanup_state": "pending",
                    "cleanup_attempted_at": now(),
                },
            )
        if not isinstance(exc, ValueError):
            diagnostic = native.cleanup_diagnostic(exc, "discard_validation")
            raise ValueError(diagnostic["cleanup_error"]) from None
        raise


def preview(store, credentials, batch_id):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    with _record_refusal(store, batch_id):
        return _preview(store, credentials, batch_id)


def _preview(store, credentials, batch_id):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    with batches._ACTIONS_LOCK:
        batch = storage.get(store, batch_id)
        _eligible(batch)
        with batches._client(credentials, batch) as client:
            decision = batch["journal"].get("discard_decision")
            if decision:
                _terminal(client, batch)
                _preserved(store, batch, decision)
                if batch["journal"].get(
                    "cleanup_state"
                ) not in native.CLEANED and native.folder_listed(
                    client, batch["journal"]["owned_folder"]
                ):
                    _unchanged_remote(store, client, batch, decision)
                return _summary(store, batch, decision["evidence"], decision["fingerprint"])
            batch, _, evidence, fingerprint = _prepare(store, client, batch_id)
            return _summary(store, batch, evidence, fingerprint)


def confirm(store, credentials, batch_id, *, fingerprint, confirmed, decided_by="researcher"):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    with _record_refusal(store, batch_id):
        return _confirm(
            store,
            credentials,
            batch_id,
            fingerprint=fingerprint,
            confirmed=confirmed,
            decided_by=decided_by,
        )


def _confirm(store, credentials, batch_id, *, fingerprint, confirmed, decided_by="researcher"):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    if (
        confirmed is not True
        or not isinstance(fingerprint, str)
        or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
    ):
        raise ValueError(
            "Preview the whole runtime batch and explicitly confirm its exact fingerprint."
        )
    with batches._ACTIONS_LOCK:
        batch = storage.get(store, batch_id)
        _eligible(batch)
        decision = batch["journal"].get("discard_decision")
        if decision and decision["fingerprint"] != fingerprint:
            raise ValueError("This confirmation belongs to another runtime disposition preview.")
        with batches._client(credentials, batch) as client:
            if decision:
                _terminal(client, batch)
                _preserved(store, batch, decision)
                if batch["journal"].get("cleanup_state") in native.CLEANED:
                    return {
                        "cleanup_state": batch["journal"]["cleanup_state"],
                        **batches._scope(batch),
                    }
                if native.folder_listed(client, batch["journal"]["owned_folder"]):
                    _unchanged_remote(store, client, batch, decision)
                else:
                    storage.checkpoint(
                        store,
                        batch_id,
                        batch["epoch"],
                        journal_update={
                            "cleanup_state": "trashed",
                            "cleanup_stage": "complete",
                            "cleanup_error": None,
                            "cleanup_error_code": None,
                            "cleanup_http_status": None,
                            "cleanup_attempted_at": now(),
                        },
                    )
                    return {"cleanup_state": "trashed", **batches._scope(batch)}
            else:
                batch, digest, evidence, current = _prepare(store, client, batch_id)
                if current != fingerprint:
                    raise ValueError("The runtime run or preserved files changed; preview again.")
                # Researcher decisions keep their exact earlier receipt shape.
                automatic = {} if decided_by == "researcher" else {"decided_by": decided_by}
                batch = storage.confirm_discard(
                    store, batch_id, digest, evidence, fingerprint, **automatic
                )
                _preserved(store, batch, batch["journal"]["discard_decision"])
            try:
                native.trash_owned_folder(client, batch["journal"]["owned_folder"])
            except (ValueError, OSError, UCloudAPIError, httpx.HTTPError) as exc:
                diagnostic = native.cleanup_diagnostic(exc, "trash")
                storage.checkpoint(
                    store,
                    batch_id,
                    batch["epoch"],
                    journal_update={
                        **diagnostic,
                        "cleanup_state": "pending",
                        "cleanup_attempted_at": now(),
                    },
                )
                return {"cleanup_state": "pending", **batches._scope(batch)}
        storage.checkpoint(
            store,
            batch_id,
            batch["epoch"],
            journal_update={
                "cleanup_state": "trashed",
                "cleanup_stage": "complete",
                "cleanup_error": None,
                "cleanup_error_code": None,
                "cleanup_http_status": None,
                "cleanup_attempted_at": now(),
            },
        )
        return {"cleanup_state": "trashed", **batches._scope(batch)}
