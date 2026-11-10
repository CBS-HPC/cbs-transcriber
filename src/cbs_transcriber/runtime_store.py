"""SQLite ownership and fenced receipts for CBS Runtime allocations; no remote I/O."""

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from .history import encode_payload
from .store import new_id, now

SETTING = 8
PROTOCOL = "cbs-runtime-batch-v1"
TERMINAL = {"SUCCESS", "FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"}
ATTEMPT_TERMINAL = {"complete", "failed", "cancelled"}
MEMBER_TERMINAL = {"completed", "failed", "cancelled"}
STATES = {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
ADMISSION = {"open": 0, "draining": 1, "sealed": 2, "closed": 3}
MAX_CONTROL_BYTES = 65536
LOCAL_FAILURE_STAGES = {
    "preparation": "input preparation",
    "validation": "input validation",
    "publication": "input publication",
    "result": "result validation or saving",
    "coordinator": "batch coordination",
}
LOCAL_FAILURE_CLASSES = {
    "ValueError",
    "OSError",
    "KeyError",
    "TypeError",
    "RuntimeError",
    "Exception",
}


def local_failure_message(stage, error_class):
    if stage not in LOCAL_FAILURE_STAGES or error_class not in LOCAL_FAILURE_CLASSES:
        raise ValueError("Unknown local runtime failure category.")
    message = f"Local {LOCAL_FAILURE_STAGES[stage]} failed ({error_class})."
    return message + (
        " This attempt did not publish new input." if stage in {"preparation", "validation"} else ""
    )


LOCAL_FAILURE_MESSAGES = {
    local_failure_message(stage, error_class)
    for stage in LOCAL_FAILURE_STAGES
    for error_class in LOCAL_FAILURE_CLASSES
}
SETUP_UPLOAD_FILES = {
    "enhancement_worker.py",
    "enhancement_remote.py",
    "remote_audio_input.py",
    "danish_models.py",
    "danish_whisper_models.json",
    "runtime_batch_remote.py",
    "ucloud_supervisor.sh",
    "supervisor.sh",
    "batch.json",
    "requirements.txt",
}
SETUP_UPLOAD_CODES = {
    "upload_interrupted",
    "upload_confirmation_timeout",
    "upload_verification_failed",
    "setup_upload_failed",
}
JOURNAL_FIELDS = {
    "run_name",
    "owned_folder",
    "folder_created",
    "scripts_uploaded",
    "script_hashes",
    "prepare_runtime",
    "prepared",
    "submission_intent",
    "submission_attempted",
    "submission_attempted_at",
    "job_id",
    "remote_state",
    "remote_observed_at",
    "reconciled_not_created",
    "reconciled_at",
    "readiness_generation",
    "readiness_hash",
    "started_at",
    "hard_deadline",
    "deadline_at",
    "late_admission_deadline",
    "stop_intent",
    "stop_requested_at",
    "stop_acknowledged",
    "termination_requested",
    "last_remote_revision",
    "published_membership_revision",
    "close_intent",
    "close_acknowledged",
    "cleanup_state",
    "cleanup_error",
    "cleanup_stage",
    "cleanup_error_code",
    "cleanup_http_status",
    "cleanup_attempted_at",
    "cleanup_epoch",
    "capacity_reserved",
    "capacity_released_at",
    "no_post",
    "setup_upload_failure",
    "accepting",
    "protocol_generation",
    "discard_confirmed",
    "discarded_at",
    "retained_reason",
    "recovery_requested",
    "recovery_epoch",
    "release_decision",
    "deleted_members",
}
MONOTONIC = {"stop_intent", "close_intent", "discard_confirmed"}
INPUT_FIELDS = {
    "artifact_origin",
    "files",
    "manifest_sha256",
    "request_sha256",
    "audio_sha256",
    "publish_intent",
    "published",
    "carried_checkpoints",
    "membership_revision",
}
REMOTE_FIELDS = {
    "artifact_checkpoints",
    "artifact_files",
    "generation",
    "accepted",
    "accepted_at",
    "status_revision",
    "outcome",
    "files",
    "worker_gone",
    "error_code",
    "complete",
    "manifest_sha256",
}


class StaleRuntimeAttempt(ValueError):
    """The validated owner/epoch now points at another task attempt."""


class StopScopeChanged(ValueError):
    """A parent action cannot stop runtime work outside its explicitly allowed member set."""


def canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError):
        raise ValueError("The frozen runtime identity must contain finite JSON values.") from None


def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def require_not_discarded(batch):
    if batch["journal"].get("discard_confirmed"):
        raise ValueError("This runtime run was explicitly abandoned; start a new run instead.")


def _deleted(db, batch):
    """Members released by an explicit Delete whose local job row is gone (ADR 0101)."""
    listed = batch["journal"].get("deleted_members") or []
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise ValueError("The runtime owner's deleted-member receipt is malformed.")
    listed = set(listed)
    return {
        member["job_id"]
        for member in batch["members"]
        if member["job_id"] in listed
        and db.execute("SELECT 1 FROM jobs WHERE id=?", (member["job_id"],)).fetchone() is None
    }


def deleted_members(store, batch):
    with store.connection() as db:
        return _deleted(db, batch)


def discard_snapshot(db, identifier):
    """One bounded DB identity for consent and its final transaction; no private text in it."""
    batch = _get(db, identifier)
    deleted = _deleted(db, batch)
    attempts = [
        dict(row)
        for row in db.execute(
            "SELECT a.* FROM runtime_batch_attempts a JOIN runtime_batch_members m "
            "ON m.job_id=a.job_id WHERE m.batch_id=? ORDER BY m.ordinal,a.attempt",
            (identifier,),
        )
    ]
    if not attempts or len(attempts) > 1000:
        raise ValueError("The runtime disposition attempt inventory is missing or too large.")
    jobs, results = [], []
    for member in batch["members"]:
        if member["job_id"] in deleted:
            # Its recording was deleted with explicit consent; nothing local remains to bind.
            jobs.append({"id": member["job_id"], "deleted": True})
            continue
        _for_job(db, member["job_id"])
        row = db.execute("SELECT * FROM jobs WHERE id=?", (member["job_id"],)).fetchone()
        jobs.append({"id": row["id"], "sha256": _digest(dict(row))})
        for result in db.execute(
            "SELECT * FROM transcript_results WHERE job_id=? ORDER BY id", (member["job_id"],)
        ):
            versions = [
                dict(v)
                for v in db.execute(
                    "SELECT id,payload_sha256,sequence FROM result_versions "
                    "WHERE result_id=? ORDER BY sequence",
                    (result["id"],),
                )
            ]
            trash = [
                dict(r)
                for r in db.execute("SELECT * FROM result_trash WHERE result_id=?", (result["id"],))
            ]
            results.append(
                {
                    "id": result["id"],
                    "sha256": _digest(dict(result)),
                    "versions": versions,
                    "trash": trash,
                }
            )
    # Status observation/diagnostics do not invalidate an otherwise identical consent preview.
    journal = {
        k: batch["journal"].get(k)
        for k in (
            "job_id",
            "run_name",
            "owned_folder",
            "folder_created",
            "submission_intent",
            "submission_attempted",
            "reconciled_not_created",
            "stop_intent",
            "close_intent",
            "recovery_requested",
            "recovery_epoch",
            "discard_confirmed",
        )
    }
    snapshot = {
        k: batch[k]
        for k in (
            "id",
            "compatibility_hash",
            "coordinator_epoch",
            "state",
            "admission",
            "membership_revision",
            "seal_revision",
            "seal_digest",
            "members",
        )
    }
    snapshot.update(journal=journal, attempts=attempts, jobs=jobs, results=results)
    return _digest(snapshot), batch, attempts


def confirm_discard(
    store, identifier, snapshot_sha256, evidence, fingerprint, *, decided_by="researcher"
):
    """Fence every producer/retry and commit consent before any owned-folder Trash request."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        digest, batch, _ = discard_snapshot(db, identifier)
        require_not_discarded(batch)
        if digest != snapshot_sha256 or batch["state"] in {"queued", "running"}:
            raise ValueError("The runtime run changed. Preview the retained files again.")
        journal = batch["journal"]
        if batch["admission"] not in {"sealed", "closed"} or journal.get("recovery_requested"):
            raise ValueError("Finish or stop runtime recovery before discarding retained files.")
        remote = evidence.get("remote", {})
        if (
            remote.get("state") not in TERMINAL
            or remote.get("job_id") != str(journal.get("job_id"))
            or remote.get("owned_folder") != journal.get("owned_folder")
        ):
            raise ValueError(
                "A runtime disposition needs the exact fresh terminal ownership proof."
            )
        stamp = now()
        journal.update(
            discard_confirmed=True,
            discarded_at=stamp,
            discard_decision={
                "fingerprint": fingerprint,
                "confirmed_at": stamp,
                "evidence": evidence,
                "snapshot_sha256": snapshot_sha256,
                # "automatic" only after a fresh terminal, nothing-unsaved/preserved check.
                **({"decided_by": decided_by} if decided_by != "researcher" else {}),
            },
            recovery_requested=False,
            capacity_reserved=False,
            capacity_released_at=stamp,
            remote_state=evidence["remote"]["state"],
            remote_observed_at=stamp,
            cleanup_state="pending",
            cleanup_stage="discard_confirmed",
        )
        db.execute(
            "UPDATE runtime_batch_runs SET journal=?,admission='closed',coordinator_epoch="
            "coordinator_epoch+1,updated=? WHERE id=?",
            (canonical(journal), stamp, identifier),
        )
        return _get(db, identifier)


def _settle_members(db, batch, job_ids):
    """A stopped owner's selected unfinished members cannot be recovered or replayed later."""
    stamp = now()
    for member in batch["members"]:
        if member["job_id"] not in job_ids or member["state"] in MEMBER_TERMINAL:
            continue
        db.execute(
            "UPDATE runtime_batch_members SET state='cancelled',updated=? WHERE job_id=?",
            (stamp, member["job_id"]),
        )
        db.execute(
            "UPDATE runtime_batch_attempts SET state='cancelled',updated=? WHERE job_id=? "
            "AND attempt=? AND state NOT IN ('complete','failed','cancelled')",
            (stamp, member["job_id"], member["attempt"]),
        )
        db.execute(
            "UPDATE jobs SET state='cancelled',updated=? WHERE id=? "
            "AND state NOT IN ('completed','cancelled')",
            (stamp, member["job_id"]),
        )


def _deleted_list(journal, job_ids):
    listed = journal.get("deleted_members") or []
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise ValueError("The runtime owner's deleted-member receipt is malformed.")
    return sorted(set(listed) | set(job_ids))


def confirm_release(store, identifier, *, target, deleted_job_ids):
    """Record Delete's consent and fence recovery before any owned-folder request (ADR 0101).

    The decision is immutable and independent of the deleted recordings: with the owner's
    frozen server/project identity it keeps the owned folder and stage until Trash is confirmed.
    """
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        if batch["state"] in {"queued", "running"}:
            raise ValueError("Stop the CBS Runtime batch before releasing its UCloud folder.")
        if batch["journal"].get("recovery_requested"):
            raise ValueError("Stop pending recovery before releasing its UCloud folder.")
        if not set(deleted_job_ids) <= {member["job_id"] for member in batch["members"]}:
            raise ValueError("The deleted files do not belong to this CBS Runtime batch.")
        journal, stamp = batch["journal"], now()
        decision = journal.get("release_decision") or {
            "kind": "deletion",
            "decided_at": stamp,
            "target": target,
        }
        journal.update(
            # The same monotonic fence as ADR 0066: no recovery, retry or producer commit.
            discard_confirmed=True,
            stop_intent=True,
            stop_requested_at=journal.get("stop_requested_at", stamp),
            release_decision=decision,
            deleted_members=_deleted_list(journal, deleted_job_ids),
            recovery_requested=False,
        )
        if journal.get("cleanup_state") not in {"trashed", "deleted"} and (
            journal.get("owned_folder") or journal.get("folder_created")
        ):
            journal.update(cleanup_state="pending", cleanup_stage="release_confirmed")
        _settle_members(db, batch, set(deleted_job_ids))
        db.execute(
            "UPDATE runtime_batch_runs SET journal=?,admission='closed',coordinator_epoch="
            "coordinator_epoch+1,updated=? WHERE id=?",
            (canonical(journal), stamp, identifier),
        )
        return _get(db, identifier)


def record_deleted_members(store, identifier, job_ids):
    """Delete of some files only: other files' unsaved work keeps the owner (ADR 0101)."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        if batch["state"] in {"queued", "running"}:
            raise ValueError("Stop the CBS Runtime batch before deleting its files.")
        if not set(job_ids) <= {member["job_id"] for member in batch["members"]}:
            raise ValueError("The deleted files do not belong to this CBS Runtime batch.")
        journal = batch["journal"]
        journal["deleted_members"] = _deleted_list(journal, job_ids)
        _settle_members(db, batch, set(job_ids))
        db.execute(
            "UPDATE runtime_batch_runs SET journal=?,coordinator_epoch=coordinator_epoch+1,"
            "updated=? WHERE id=?",
            (canonical(journal), now(), identifier),
        )
        return _get(db, identifier)


def migrate(store):
    """Back up schema 11 before one additive transaction; caller holds the library lease."""
    with store.library_lock(), store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 12:
            return
        if store.initial_version == 11:
            (store.root / "backups").mkdir(exist_ok=True)
            backup = sqlite3.connect(store.root / "backups" / f"schema-11-{new_id()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_batch_runs (id TEXT PRIMARY KEY, "
            "project_id TEXT NOT NULL, "
            "compatibility_hash TEXT NOT NULL, identity TEXT NOT NULL, "
            "state TEXT NOT NULL DEFAULT 'queued', admission TEXT NOT NULL DEFAULT 'open', "
            "membership_revision INTEGER NOT NULL DEFAULT 0, seal_revision INTEGER, "
            "seal_digest TEXT, coordinator_epoch INTEGER NOT NULL DEFAULT 0, "
            "journal TEXT NOT NULL DEFAULT '{}', message TEXT NOT NULL DEFAULT '', "
            "progress REAL NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS runtime_batch_compatible ON runtime_batch_runs "
            "(compatibility_hash,admission,state,created,id)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_batch_members (job_id TEXT PRIMARY KEY, "
            "batch_id TEXT NOT NULL "
            "REFERENCES runtime_batch_runs(id), recording_id TEXT NOT NULL, ordinal INTEGER "
            "NOT NULL, source_identity TEXT NOT NULL, current_attempt INTEGER NOT NULL DEFAULT 1, "
            "state TEXT NOT NULL DEFAULT 'queued', result_id TEXT, "
            "output_receipt TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, "
            "updated TEXT NOT NULL, UNIQUE(batch_id,ordinal))"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS runtime_members_recording "
            "ON runtime_batch_members(recording_id,batch_id)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS runtime_batch_attempts (job_id TEXT NOT NULL REFERENCES "
            "runtime_batch_members(job_id), attempt INTEGER NOT NULL, "
            "task_id TEXT NOT NULL UNIQUE, "
            "state TEXT NOT NULL DEFAULT 'reserved', input_receipt TEXT NOT NULL DEFAULT '{}', "
            "remote_receipt TEXT NOT NULL DEFAULT '{}', error_code TEXT, created TEXT NOT NULL, "
            "updated TEXT NOT NULL, PRIMARY KEY(job_id,attempt))"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS runtime_one_live_attempt "
            "ON runtime_batch_attempts(job_id) "
            "WHERE state NOT IN ('complete','failed','cancelled')"
        )
        db.execute("PRAGMA user_version=12")


def _get(db, identifier):
    row = db.execute("SELECT * FROM runtime_batch_runs WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise KeyError("Unknown CBS Runtime batch.")
    batch = dict(row)
    for key in ("identity", "journal"):
        batch[key] = json.loads(batch[key])
    if _digest(batch["identity"]) != batch["compatibility_hash"]:
        raise ValueError("The runtime allocation's immutable identity hash disagrees.")
    batch["epoch"] = batch["coordinator_epoch"]
    batch["members"] = []
    for row in db.execute(
        "SELECT m.*,a.task_id,a.state AS attempt_state,a.input_receipt,a.remote_receipt,"
        "a.error_code AS attempt_error_code "
        "FROM runtime_batch_members m JOIN runtime_batch_attempts a ON a.job_id=m.job_id "
        "AND a.attempt=m.current_attempt WHERE m.batch_id=? ORDER BY m.ordinal",
        (identifier,),
    ):
        member = dict(row)
        for key in ("source_identity", "output_receipt", "input_receipt", "remote_receipt"):
            member[key] = json.loads(member[key])
        member["attempt"] = member["current_attempt"]
        batch["members"].append(member)
    if (
        len(batch["members"])
        != db.execute(
            "SELECT COUNT(*) FROM runtime_batch_members WHERE batch_id=?", (identifier,)
        ).fetchone()[0]
    ):
        raise ValueError("A runtime member has no current durable task attempt.")
    return batch


def get(store, batch_id):
    with store.connection() as db:
        db.execute("BEGIN")
        return _get(db, batch_id)


def identity_for(project_id, params):
    """Build compatibility from normalized execution identity, never member source IDs."""
    from .enhancement import EnhancementOptions
    from .providers import Settings
    from .runtime_compute import frozen_product

    api = params.get("ucloud_api", {})
    plan = api.get("batch_plan")
    if not isinstance(plan, dict) or not isinstance(plan.get("identity"), dict):
        raise ValueError("This runtime job has no frozen batch plan.")
    frozen = plan["identity"]
    if frozen.get("protocol") != PROTOCOL:
        raise ValueError("This runtime batch protocol is unsupported.")
    options = EnhancementOptions.model_validate(params.get("enhancement", {}))
    if options.execution != "ucloud" or options.device != "cuda" or options.allow_downloads:
        raise ValueError("Runtime batches require frozen UCloud/CUDA execution without downloads.")
    settings = Settings.model_validate(api.get("settings", {}))
    if (
        settings.provider_type != "ucloud"
        or not api.get("provider_id")
        or not api.get("runtime_id")
    ):
        raise ValueError("This runtime batch has an incomplete frozen connection.")
    product = frozen_product(api)
    if product is None:
        raise ValueError("New runtime batches require the separately frozen MIG identity.")
    limits = frozen.get("limits", {})
    if (
        type(limits.get("max_members")) is not int
        or not 1 <= limits["max_members"] <= 100
        or limits.get("gpu_concurrency") != 1
    ):
        raise ValueError("Invalid frozen runtime allocation limits.")
    for key in ("idle_grace_seconds", "owner_absence_seconds", "late_cutoff_seconds"):
        value = limits.get(key)
        if type(value) is not int or not 0 <= value <= 86400:
            raise ValueError("Invalid frozen runtime allocation limits.")
    application = frozen.get("application", {})
    if not isinstance(application, dict) or not all(
        isinstance(application.get(key), str) and application[key] for key in ("name", "version")
    ):
        raise ValueError("Freeze the exact Terminal application before enqueueing runtime work.")
    operation = (
        "transcription"
        if params.get("transcription")
        else "passage_retranscription"
        if params.get("retranscription")
        else "enhancement"
    )
    if (
        options.transcribe != (operation == "transcription")
        or options.retranscribe != (operation == "passage_retranscription")
        or params.get("realign")
        or params.get("timing_units") is not None
    ):
        raise ValueError("This operation does not belong in a runtime batch.")
    result = {
        **frozen,
        "project_id": project_id,
        "operation": operation,
        "provider_id": api["provider_id"],
        "settings": settings.model_dump(),
        "runtime_id": api["runtime_id"],
        "runtime_product": product,
        "runtime_compute_policy": api["runtime_compute_policy"],
        "options": options.model_dump(
            exclude={"runtime_python", "prepare_runtime", "allow_downloads", "download_models"}
        ),
    }
    return json.loads(canonical(result))


def _source(db, recording, params):
    result = {"recording_id": recording["id"]}
    if "audio_input" in params:
        from .audio_inputs import effective_recording, validate

        result["audio_input"] = validate(params["audio_input"])
        recording = effective_recording({"params": params}, dict(recording))
        if (
            params.get("ucloud_api", {})
            .get("batch_plan", {})
            .get("identity", {})
            .get("input_protocol")
        ):
            from .remote_audio_input import for_job

            result["runtime_audio_input"] = for_job(params)
    if params.get("transcription"):
        result.update(
            original=True, duration=recording["duration"], revision=params.get("revision")
        )
        return result
    source = params.get("source_snapshot")
    if not isinstance(source, dict):
        raise ValueError("Runtime enhancement needs its exact frozen source.")
    payload, digest = encode_payload(source.get("segments"), source.get("speakers"))
    row = db.execute(
        "SELECT v.payload,v.payload_sha256 FROM result_versions v JOIN transcript_results r "
        "ON r.id=v.result_id WHERE v.id=? AND v.result_id=? AND r.recording_id=?",
        (source.get("source_version_id"), source.get("id"), recording["id"]),
    ).fetchone()
    if (
        row is None
        or row["payload"] != payload
        or row["payload_sha256"] != digest
        or source.get("source_payload_sha256") != digest
    ):
        raise ValueError("The runtime member's source version/hash is not its exact frozen input.")
    result.update(
        source_result_id=source["id"],
        source_version_id=source["source_version_id"],
        source_payload_sha256=digest,
    )
    selected = params.get("segment_ids")
    if params.get("segment_id") is not None:
        selected = [params["segment_id"]]
    if selected is not None:
        if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
            raise ValueError("The runtime member's selected passage IDs are invalid.")
        passages = {s["id"]: s for s in source["segments"]}
        if any(i not in passages for i in selected):
            raise ValueError("The runtime member's selected passage IDs are invalid.")
        result["selected"] = [
            {"id": i, "start": passages[i].get("start"), "end": passages[i].get("end")}
            for i in selected
        ]
    return result


def _for_job(db, job):
    if isinstance(job, str):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        if row is None:
            raise KeyError("Unknown runtime member job.")
        job = dict(row)
    else:
        # Caller snapshots cannot replace current persisted identity.
        job = dict(job)
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if row is None:
            raise KeyError("Unknown runtime member job.")
        persisted = dict(row)
        supplied = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
        if job["recording_id"] != persisted["recording_id"] or supplied.get(
            "runtime_batch_id"
        ) != json.loads(persisted["params"]).get("runtime_batch_id"):
            raise ValueError("The runtime job pointer disagrees with its durable membership.")
        job = persisted
    params = json.loads(job["params"]) if isinstance(job["params"], str) else job["params"]
    row = db.execute("SELECT * FROM runtime_batch_members WHERE job_id=?", (job["id"],)).fetchone()
    pointer = params.get("runtime_batch_id")
    if not pointer and row is None:
        return None
    if not pointer or row is None or row["batch_id"] != pointer:
        raise ValueError("The runtime job pointer disagrees with its durable membership.")
    recording = db.execute("SELECT * FROM recordings WHERE id=?", (job["recording_id"],)).fetchone()
    batch = _get(db, pointer)
    if (
        recording is None
        or row["recording_id"] != job["recording_id"]
        or identity_for(recording["project_id"], params) != batch["identity"]
        or canonical(_source(db, recording, params)) != row["source_identity"]
    ):
        raise ValueError("The runtime member's frozen ownership or source identity disagrees.")
    return batch


def for_job(store, job_or_id):
    with store.connection() as db:
        db.execute("BEGIN")
        return _for_job(db, job_or_id)


def _uncertain(journal):
    return bool(
        not journal.get("job_id")
        and (journal.get("submission_intent") or journal.get("submission_attempted"))
        and not journal.get("reconciled_not_created")
    )


def _admitting(batch):
    journal = batch["journal"]
    if (
        batch["admission"] != "open"
        or batch["state"] not in {"queued", "running"}
        or journal.get("stop_intent")
        or journal.get("discard_confirmed")
        or journal.get("close_intent")
        or journal.get("termination_requested")
        or journal.get("remote_state") in TERMINAL
        or _uncertain(journal)
        or journal.get("cleanup_state") in {"retained", "trashed", "deleted"}
        or journal.get("accepting") is False
    ):
        return False
    deadline = journal.get("late_admission_deadline")
    if deadline and now() >= deadline:
        return False
    deadline = journal.get("deadline_at", journal.get("hard_deadline"))
    if deadline and datetime.fromisoformat(now()) >= datetime.fromisoformat(deadline) - timedelta(
        seconds=batch["identity"]["limits"]["late_cutoff_seconds"]
    ):
        return False
    return True


def _check_profile(db, recording, identity, *, frozen_followup=False, accepted_intent=False):
    from .providers import Settings

    row = db.execute(
        "SELECT value FROM providers WHERE id=?", (identity["provider_id"],)
    ).fetchone()
    if row is None:
        raise ValueError("The runtime connection changed after preflight; start the request again.")
    current = Settings.model_validate_json(row["value"]).model_dump()
    omitted = {"enabled", "approved_for"} if frozen_followup else set()
    if not accepted_intent and {k: v for k, v in current.items() if k not in omitted} != {
        k: v for k, v in identity["settings"].items() if k not in omitted
    }:
        raise ValueError("The runtime connection changed after preflight; start the request again.")
    if not (frozen_followup or accepted_intent) and not current["enabled"]:
        raise ValueError("This runtime model is disabled for new work.")
    project = db.execute(
        "SELECT classification FROM projects WHERE id=?", (recording["project_id"],)
    ).fetchone()
    if (
        project["classification"] != "standard"
        and project["classification"] not in current["approved_for"]
    ):
        raise ValueError("This project requires an explicitly approved runtime connection.")


def _membership(db, batch, extra=None):
    if batch["seal_revision"] is not None and batch["journal"].get("sealed_membership"):
        return batch["journal"]["sealed_membership"]
    tasks = []
    for row in db.execute(
        "SELECT a.*,m.ordinal FROM runtime_batch_attempts a JOIN runtime_batch_members m "
        "ON m.job_id=a.job_id WHERE m.batch_id=? ORDER BY m.ordinal,a.attempt",
        (batch["id"],),
    ):
        undispatched = row["state"] in {"failed", "cancelled"} and not json.loads(
            row["input_receipt"]
        ).get("publish_intent")
        task = {
            "task_id": row["task_id"],
            "job_id": row["job_id"],
            "attempt": row["attempt"],
            "ordinal": row["ordinal"],
            "disposition": "not_dispatched" if undispatched else "reserved",
        }
        if undispatched:
            task["error_code"] = row["error_code"] or "not_dispatched"
        tasks.append(task)
    current = {m["job_id"]: m["task_id"] for m in batch["members"]}
    if extra is not None:
        tasks.append(
            {
                "job_id": extra["job_id"],
                "task_id": extra["task_id"],
                "attempt": extra["attempt"],
                "ordinal": extra["ordinal"],
                "disposition": "reserved",
            }
        )
        tasks.sort(key=lambda t: (t["ordinal"], t["attempt"]))
        current[extra["job_id"]] = extra["task_id"]
    return {
        "protocol": PROTOCOL,
        "batch_id": batch["id"],
        "revision": batch["membership_revision"] + (extra is not None),
        "tasks": tasks,
        "current_attempts": current,
    }


def membership(store, identifier):
    with store.connection() as db:
        db.execute("BEGIN")
        return _membership(db, _get(db, identifier))


def _control_size(db, batch, extra):
    return len(canonical(_membership(db, batch, extra)).encode("utf-8"))


def attach(db, recording, job_id, kind, params):
    """Attach inside the job/result transaction. Legacy snapshots are deliberately ignored."""
    from . import restored_owners

    restored_owners.require_owned(db, "job", job_id)
    api = params.get("ucloud_api", {})
    if not isinstance(api, dict):
        raise ValueError("The runtime connection snapshot is invalid.")
    if api.get("batch_plan") is None:
        if params.get("runtime_batch_id"):
            raise ValueError("A runtime member cannot be inserted without its frozen batch plan.")
        return
    if kind != "enhance" or params.get("native_batch_id"):
        raise ValueError("Only CBS Runtime enhancement jobs can join runtime batches.")
    identity = identity_for(recording["project_id"], params)
    from . import input_admission

    accepted_intent = input_admission.authorizes_attachment(db, recording, job_id, params)
    if recording["status"] != "ready" and not input_admission.uses_original(
        {"params": params}, recording
    ):
        raise ValueError("Finish media preparation before enqueueing runtime work.")
    source = _source(db, recording, params)
    frozen_followup = params.get("parent_transcription_id")
    if (
        frozen_followup
        and not db.execute(
            "SELECT 1 FROM transcript_results r JOIN jobs j ON j.id=r.job_id WHERE r.id=? "
            "AND r.recording_id=? AND j.id=? AND j.kind='transcribe'",
            (source.get("source_result_id"), recording["id"], frozen_followup),
        ).fetchone()
    ):
        raise ValueError("A runtime follow-up must belong to its exact producing transcription.")
    _check_profile(
        db,
        recording,
        identity,
        frozen_followup=bool(frozen_followup),
        accepted_intent=accepted_intent,
    )
    for row in db.execute(
        "SELECT id,params FROM jobs WHERE recording_id=? AND kind='enhance' "
        "AND state IN ('queued','running') AND id!=?",
        (recording["id"], job_id),
    ):
        from . import restored_owners

        if restored_owners.is_restored(db, "job", row["id"]):
            continue
        active = json.loads(row["params"])
        if identity["operation"] == "transcription":
            duplicate = active.get("transcription") and active.get("provider_id") == params.get(
                "provider_id", api["provider_id"]
            )
        else:
            duplicate = (
                not active.get("transcription")
                and active.get("enhancement", {}).get("execution") == "ucloud"
            )
        if duplicate:
            raise ValueError("This recording already has active work for this runtime operation.")
    encoded, digest = canonical(identity), _digest(identity)
    attempt = {"job_id": job_id, "task_id": new_id(), "attempt": 1}
    batch = None
    for row in db.execute(
        "SELECT id FROM runtime_batch_runs WHERE compatibility_hash=? AND admission='open' "
        "AND state IN ('queued','running') ORDER BY created,id",
        (digest,),
    ):
        from . import restored_owners

        if restored_owners.is_restored(db, "runtime_batch", row["id"]):
            continue
        candidate = _get(db, row["id"])
        if (
            candidate["identity"] == identity
            and _admitting(candidate)
            and len(candidate["members"]) < identity["limits"]["max_members"]
            and _control_size(db, candidate, {**attempt, "ordinal": len(candidate["members"])})
            <= MAX_CONTROL_BYTES
        ):
            batch = candidate
            break
    if batch is None:
        identifier, stamp = new_id(), now()
        restored_owners.require_owned(db, "runtime_batch", identifier)
        journal = {
            "run_name": "CBS Runtime batch " + identifier,
            "prepare_runtime": bool(api.get("prepare_runtime")),
        }
        db.execute(
            "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,journal,"
            "created,updated) VALUES (?,?,?,?,?,?,?)",
            (
                identifier,
                recording["project_id"],
                digest,
                encoded,
                canonical(journal),
                stamp,
                stamp,
            ),
        )
        batch = _get(db, identifier)
    if _control_size(db, batch, {**attempt, "ordinal": len(batch["members"])}) > MAX_CONTROL_BYTES:
        raise ValueError("This runtime membership cannot fit its bounded control-file contract.")
    params["runtime_batch_id"] = batch["id"]
    stamp = now()
    db.execute(
        "INSERT INTO runtime_batch_members(job_id,batch_id,recording_id,ordinal,source_identity,"
        "created,updated) VALUES (?,?,?,?,?,?,?)",
        (
            job_id,
            batch["id"],
            recording["id"],
            len(batch["members"]),
            canonical(source),
            stamp,
            stamp,
        ),
    )
    db.execute(
        "INSERT INTO runtime_batch_attempts(job_id,attempt,task_id,created,updated) "
        "VALUES (?,1,?,?,?)",
        (job_id, attempt["task_id"], stamp, stamp),
    )
    db.execute(
        "UPDATE runtime_batch_runs SET membership_revision=membership_revision+1,updated=? "
        "WHERE id=?",
        (stamp, batch["id"]),
    )


def _fence(batch, epoch):
    if type(epoch) is not int or batch["coordinator_epoch"] != epoch:
        raise ValueError("This runtime coordinator no longer owns the allocation.")


def settings(store):
    with store.connection() as db:
        return _settings(db)


def _settings(db):
    row = db.execute("SELECT value FROM settings WHERE id=?", (SETTING,)).fetchone()
    return {"max_allocations": json.loads(row[0])["max_allocations"] if row else 2}


def save_settings(store, max_allocations):
    if type(max_allocations) is not int or not 1 <= max_allocations <= 4:
        raise ValueError("Choose between one and four CBS Runtime allocations.")
    with store.connection() as db:
        db.execute(
            "INSERT INTO settings(id,value) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET "
            "value=excluded.value",
            (SETTING, canonical({"max_allocations": max_allocations})),
        )
    return settings(store)


def _reserved(journal):
    return bool(
        journal.get("capacity_reserved")
        or _uncertain(journal)
        or (journal.get("job_id") and journal.get("remote_state") not in TERMINAL)
    )


def _capacity(db):
    from . import restored_owners

    owned_batches = restored_owners.sql_exclusion(db, "runtime_batch", "runtime_batch_runs.id")
    owned_jobs = restored_owners.sql_exclusion(db, "job", "jobs.id")
    count = sum(
        _reserved(json.loads(row["journal"]))
        for row in db.execute("SELECT journal FROM runtime_batch_runs WHERE " + owned_batches)
    )
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_preparations'").fetchone():
        for row in db.execute("SELECT journal FROM runtime_preparations WHERE archived=0"):
            try:
                journal = json.loads(row[0])
                if type(journal) is not dict:
                    raise ValueError()
                canonical(journal)
            except (ValueError, TypeError, RecursionError):
                count += 1  # Unreadable live allocation evidence cannot prove a free slot.
            else:
                count += _reserved(journal)
    for row in db.execute(
        "WITH safe_jobs AS (SELECT id,kind,CASE WHEN json_valid(params) THEN params "
        "ELSE 'null' END AS params FROM jobs WHERE " + owned_jobs + ") "
        "SELECT id,params FROM safe_jobs WHERE kind='enhance' "
        "AND json_extract(params,'$.ucloud_api') IS NOT NULL "
        "AND json_extract(params,'$.runtime_batch_id') IS NULL"
    ):
        count += _reserved(json.loads(row["params"]).get("ucloud_run", {}))
    return count


def reserve_capacity(store, identifier, *, epoch=None, legacy=False):
    """Reserve once before POST, sharing the transaction with legacy singleton receipts."""
    from . import restored_owners

    restored_owners.require_owner(store, "job" if legacy else "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if legacy:
            row = db.execute(
                "SELECT params FROM jobs WHERE id=? AND kind='enhance'", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError(identifier)
            params = json.loads(row[0])
            if "ucloud_api" not in params or params.get("runtime_batch_id"):
                raise ValueError("Capacity applies only to a legacy API runtime singleton.")
            journal = params.setdefault("ucloud_run", {})
        else:
            batch = _get(db, identifier)
            _fence(batch, epoch)
            journal = batch["journal"]
            if journal.get("stop_intent") or batch["admission"] == "closed":
                return False
        if _reserved(journal):
            return True
        if _capacity(db) >= _settings(db)["max_allocations"]:
            return False
        journal["capacity_reserved"] = True
        if legacy:
            db.execute(
                "UPDATE jobs SET params=?,updated=? WHERE id=?",
                (canonical(params), now(), identifier),
            )
        else:
            db.execute(
                "UPDATE runtime_batch_runs SET journal=?,updated=? WHERE id=?",
                (canonical(journal), now(), identifier),
            )
    return True


def begin_legacy_submission(store, job_id):
    """Reserve the singleton slot and its POST intent together; never send a request here."""
    from . import restored_owners

    restored_owners.require_job(store, job_id)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT params FROM jobs WHERE id=? AND kind='enhance'", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        params = json.loads(row["params"])
        api = params.get("ucloud_api")
        if (
            not isinstance(api, dict)
            or params.get("runtime_batch_id")
            or api.get("batch_plan") is not None
            or db.execute(
                "SELECT 1 FROM runtime_batch_members WHERE job_id=?", (job_id,)
            ).fetchone()
        ):
            raise ValueError("Submission capacity applies only to a legacy API runtime singleton.")
        journal = params.setdefault("ucloud_run", {})
        if journal.get("job_id"):
            raise ValueError(
                "Reconnect to this singleton's recorded allocation; never submit another."
            )
        if _uncertain(journal):
            raise ValueError(
                "UCloud allocation outcome is uncertain; reconcile before another POST."
            )
        if not _reserved(journal) and _capacity(db) >= _settings(db)["max_allocations"]:
            return False
        journal.update(
            capacity_reserved=True,
            submission_intent=True,
            submission_attempted_at=now(),
            reconciled_not_created=False,
            no_post=False,
        )
        db.execute(
            "UPDATE jobs SET params=?,updated=? WHERE id=?", (canonical(params), now(), job_id)
        )
    return True


def release_capacity(store, identifier, *, epoch=None, legacy=False):
    from . import restored_owners

    restored_owners.require_owner(store, "job" if legacy else "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if legacy:
            row = db.execute("SELECT params FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            params = json.loads(row[0])
            journal = params.setdefault("ucloud_run", {})
        else:
            batch = _get(db, identifier)
            _fence(batch, epoch)
            journal = batch["journal"]
        if not (
            journal.get("remote_state") in TERMINAL
            or (
                not journal.get("job_id")
                and (
                    journal.get("reconciled_not_created")
                    or (journal.get("no_post") and not _uncertain(journal))
                )
            )
        ):
            raise ValueError(
                "Capacity remains reserved until allocation absence or termination is known."
            )
        journal.update(capacity_reserved=False, capacity_released_at=now())
        if legacy:
            db.execute(
                "UPDATE jobs SET params=?,updated=? WHERE id=?",
                (canonical(params), now(), identifier),
            )
        else:
            db.execute(
                "UPDATE runtime_batch_runs SET journal=?,updated=? WHERE id=?",
                (canonical(journal), now(), identifier),
            )


def _claim_members(db, batch, *, recovering=False):
    from . import restored_owners

    restored_owners.require_owned(db, "runtime_batch", batch["id"])
    stamp = now()
    for member in batch["members"]:
        if member["state"] != "queued":
            continue
        _for_job(db, member["job_id"])
        if (
            db.execute(
                "UPDATE jobs SET state='running',updated=? WHERE id=? AND "
                "(state='queued' OR (state='interrupted' AND ?))",
                (stamp, member["job_id"], recovering),
            ).rowcount
            != 1
        ):
            raise ValueError("A runtime member changed before its coordinator claimed it.")
        db.execute(
            "UPDATE runtime_batch_members SET state=?,updated=? WHERE job_id=?",
            (
                "collecting" if member["attempt_state"] == "complete" else "preparing",
                stamp,
                member["job_id"],
            ),
        )
        if member["attempt_state"] != "complete":
            db.execute(
                "UPDATE runtime_batch_attempts SET state='preparing',updated=? "
                "WHERE job_id=? AND attempt=?",
                (stamp, member["job_id"], member["attempt"]),
            )


def claim(store):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT id FROM runtime_batch_runs WHERE state IN ('interrupted','failed','cancelled') "
            "AND json_extract(CASE WHEN json_valid(journal) THEN journal ELSE '{}' END,"
            "'$.recovery_requested')=1 ORDER BY created,id"
        ):
            from . import restored_owners

            if restored_owners.is_restored(db, "runtime_batch", row["id"]):
                continue
            batch = _get(db, row["id"])
            journal = batch["journal"]
            if journal.get("discard_confirmed"):
                continue
            if journal.get("recovery_epoch") != batch["coordinator_epoch"]:
                continue
            if not journal.get("job_id") or _uncertain(journal):
                continue
            journal["recovery_requested"] = False
            if not journal.get("stop_intent") and journal.get("remote_state") not in TERMINAL:
                _claim_members(db, batch, recovering=True)
            for member in batch["members"]:
                if member["state"] not in MEMBER_TERMINAL:
                    db.execute(
                        "UPDATE jobs SET state='running',updated=? WHERE id=? "
                        "AND state!='completed'",
                        (now(), member["job_id"]),
                    )
                    if member["state"] == "interrupted":
                        db.execute(
                            "UPDATE runtime_batch_members SET state='preparing',updated=? "
                            "WHERE job_id=?",
                            (now(), member["job_id"]),
                        )
            db.execute(
                "UPDATE runtime_batch_runs SET state='running',coordinator_epoch="
                "coordinator_epoch+1,journal=?,updated=? WHERE id=?",
                (canonical(journal), now(), batch["id"]),
            )
            return _get(db, batch["id"])
        if _capacity(db) >= _settings(db)["max_allocations"]:
            return None
        for row in db.execute(
            "SELECT id FROM runtime_batch_runs WHERE state='queued' "
            "AND admission='open' ORDER BY created,id"
        ):
            from . import restored_owners

            if restored_owners.is_restored(db, "runtime_batch", row["id"]):
                continue
            batch = _get(db, row["id"])
            if (
                not _admitting(batch)
                or _uncertain(batch["journal"])
                or batch["journal"].get("job_id")
            ):
                continue
            journal = {**batch["journal"], "capacity_reserved": True}
            _claim_members(db, batch)
            db.execute(
                "UPDATE runtime_batch_runs SET state='running',coordinator_epoch="
                "coordinator_epoch+1,journal=?,updated=? WHERE id=?",
                (canonical(journal), now(), batch["id"]),
            )
            return _get(db, batch["id"])
    return None


def claim_member(store, identifier, job_id, epoch):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        if batch["state"] != "running":
            return None
        member = next((m for m in batch["members"] if m["job_id"] == job_id), None)
        if member is None:
            raise ValueError("This job does not belong to the runtime allocation.")
        if batch["journal"].get("stop_intent") and member["attempt_state"] != "complete":
            return None
        _for_job(db, job_id)
        if member["state"] != "queued":
            return None
        _claim_members(db, {**batch, "members": [member]})
        return next(m for m in _get(db, identifier)["members"] if m["job_id"] == job_id)


def checkpoint(store, identifier, epoch, journal_update=None, **fields):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    allowed = {"state", "admission", "message", "progress"}
    if fields.keys() - allowed or (journal_update or {}).keys() - JOURNAL_FIELDS:
        raise ValueError("Unknown runtime batch receipt field.")
    if "setup_upload_failure" in (journal_update or {}):
        failure = journal_update["setup_upload_failure"]
        if (
            not isinstance(failure, dict)
            or set(failure) != {"stage", "file", "code", "observed_at"}
            or failure.get("stage") != "setup_script_upload"
            or not isinstance(failure.get("file"), str)
            or failure.get("file") not in SETUP_UPLOAD_FILES
            or not isinstance(failure.get("code"), str)
            or failure.get("code") not in SETUP_UPLOAD_CODES
            or not isinstance(failure.get("observed_at"), str)
            or not 1 <= len(failure["observed_at"]) <= 40
        ):
            raise ValueError("Runtime setup failures require bounded public-file diagnostics.")
        try:
            observed = datetime.fromisoformat(failure["observed_at"])
        except ValueError:
            raise ValueError("Runtime setup failure time is invalid.") from None
        if observed.utcoffset() is None:
            raise ValueError("Runtime setup failure time requires a timezone.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        journal, update = batch["journal"], journal_update or {}
        for key in MONOTONIC:
            if journal.get(key) and update.get(key) is False:
                raise ValueError("A runtime owner intent cannot be cleared.")
        for key in (
            "job_id",
            "owned_folder",
            "run_name",
            "readiness_generation",
            "started_at",
            "deadline_at",
            "hard_deadline",
            "late_admission_deadline",
        ):
            if journal.get(key) is not None and key in update and update[key] != journal[key]:
                raise ValueError("An immutable runtime ownership receipt changed.")
        if (
            journal.get("cleanup_state") in {"trashed", "deleted"}
            and update.get("cleanup_state", journal["cleanup_state"]) != journal["cleanup_state"]
        ):
            raise ValueError("Terminal runtime cleanup cannot be reopened.")
        if fields.get("state", batch["state"]) not in STATES:
            raise ValueError("Unknown runtime batch state.")
        admission = fields.get("admission", batch["admission"])
        if admission not in ADMISSION or ADMISSION[admission] < ADMISSION[batch["admission"]]:
            raise ValueError("Runtime admission cannot reopen.")
        if (
            admission in {"sealed", "closed"}
            and batch["admission"] == "open"
            and not (update.get("stop_intent") or batch["seal_digest"])
        ):
            raise ValueError("Seal the exact runtime task set before closing admission.")
        if "progress" in fields and (
            type(fields["progress"]) not in {float, int}
            or not math.isfinite(fields["progress"])
            or not 0 <= fields["progress"] <= 1
        ):
            raise ValueError("Invalid runtime progress.")
        journal.update(update)
        if update.get("submission_intent") or update.get("submission_attempted"):
            journal["reconciled_not_created"] = False
            journal["no_post"] = False
        if update.get("capacity_reserved") is False and not (
            journal.get("remote_state") in TERMINAL
            or (
                not journal.get("job_id")
                and (
                    journal.get("reconciled_not_created")
                    or (journal.get("no_post") and not _uncertain(journal))
                )
            )
        ):
            raise ValueError(
                "Runtime capacity cannot be released without a terminal/absence receipt."
            )
        if fields.get("state") == "completed" and (
            admission == "open"
            or journal.get("remote_state") not in TERMINAL
            or any(m["state"] != "completed" or not m["result_id"] for m in batch["members"])
        ):
            raise ValueError(
                "A runtime batch completes only after seal, saved results and remote termination."
            )
        if journal.get("stop_intent") or journal.get("cleanup_state") in {"trashed", "deleted"}:
            fields["admission"] = "closed"
        fields.update(journal=canonical(journal), updated=now())
        db.execute(
            "UPDATE runtime_batch_runs SET "
            + ",".join(f"{key}=?" for key in fields)
            + " WHERE id=?",
            (*fields.values(), identifier),
        )
        return _get(db, identifier)


def seal(store, identifier, epoch, membership_revision, *, membership_bytes=None):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        if batch["seal_revision"] is not None:
            if (
                membership_bytes is not None
                and hashlib.sha256(membership_bytes).hexdigest() != batch["seal_digest"]
            ):
                raise ValueError("The sealed runtime membership bytes cannot change.")
            return batch if batch["seal_revision"] == membership_revision else None
        if (
            batch["membership_revision"] != membership_revision
            or batch["admission"] not in {"open", "draining"}
            or batch["journal"].get("stop_intent")
        ):
            return None
        envelope = _membership(db, batch)
        if membership_bytes is None:
            membership_bytes = canonical(envelope).encode("utf-8")
        if (
            not isinstance(membership_bytes, bytes)
            or not membership_bytes
            or len(membership_bytes) > MAX_CONTROL_BYTES
        ):
            raise ValueError("Runtime membership exceeds the bounded control-file contract.")
        try:
            decoded = json.loads(membership_bytes)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("The runtime membership control file is invalid JSON.") from None
        if decoded != envelope:
            raise ValueError(
                "The runtime membership bytes do not match its exact durable attempts."
            )
        digest = hashlib.sha256(membership_bytes).hexdigest()
        journal = {**batch["journal"], "sealed_membership": envelope}
        db.execute(
            "UPDATE runtime_batch_runs SET admission='sealed',seal_revision=?,seal_digest=?,"
            "journal=?,updated=? WHERE id=?",
            (membership_revision, digest, canonical(journal), now(), identifier),
        )
        return _get(db, identifier)


def stop(store, identifier, *, allowed_job_ids=None):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    allowed_job_ids = frozenset(allowed_job_ids) if allowed_job_ids is not None else None
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        if allowed_job_ids is not None and any(
            member["job_id"] not in allowed_job_ids for member in batch["members"]
        ):
            raise StopScopeChanged(
                "The CBS Runtime batch includes other work. Stop it separately in Activity."
            )
        journal = {
            **batch["journal"],
            "stop_intent": True,
            "stop_requested_at": batch["journal"].get("stop_requested_at", now()),
        }
        for member in batch["members"]:
            if (
                member["state"] in {"queued", "preparing", "interrupted"}
                and not member["input_receipt"].get("publish_intent")
                and not member["remote_receipt"].get("accepted")
                and not member["remote_receipt"].get("outcome")
            ):
                db.execute(
                    "UPDATE runtime_batch_members SET state='cancelled',updated=? WHERE job_id=?",
                    (now(), member["job_id"]),
                )
                db.execute(
                    "UPDATE runtime_batch_attempts SET state='cancelled',updated=? WHERE "
                    "job_id=? AND attempt=?",
                    (now(), member["job_id"], member["attempt"]),
                )
                db.execute(
                    "UPDATE jobs SET state='cancelled',updated=? WHERE id=? AND state!='completed'",
                    (now(), member["job_id"]),
                )
                if batch["seal_revision"] is None:
                    db.execute(
                        "UPDATE runtime_batch_runs SET membership_revision=membership_revision+1 "
                        "WHERE id=?",
                        (identifier,),
                    )
        db.execute(
            "UPDATE runtime_batch_runs SET admission='closed',journal=?,updated=? WHERE id=?",
            (canonical(journal), now(), identifier),
        )
        return _get(db, identifier)


def obligations(store, project_id=None, recording_ids=None):
    with store.connection() as db:
        db.execute("BEGIN")
        sql, args = "SELECT DISTINCT b.id FROM runtime_batch_runs b", []
        where = []
        if project_id is not None:
            where.append("b.project_id=?")
            args.append(project_id)
        if recording_ids is not None:
            if not recording_ids:
                return []
            where.append(
                "EXISTS (SELECT 1 FROM runtime_batch_members m WHERE m.batch_id=b.id "
                "AND m.recording_id IN (" + ",".join("?" for _ in recording_ids) + "))"
            )
            args.extend(recording_ids)
        if where:
            sql += " WHERE " + " AND ".join(where)
        result = []
        for row in db.execute(sql + " ORDER BY b.created,b.id", args):
            from . import restored_owners

            if restored_owners.is_restored(db, "runtime_batch", row["id"]):
                continue
            batch = _get(db, row["id"])
            journal = batch["journal"]
            if (
                batch["state"] in {"queued", "running"}
                or _reserved(journal)
                or journal.get("cleanup_state") in {"pending", "retained"}
                or (
                    not (
                        (journal.get("discard_decision") or journal.get("release_decision"))
                        and journal.get("cleanup_state") in {"trashed", "deleted"}
                    )
                    and any(m["state"] not in MEMBER_TERMINAL for m in batch["members"])
                )
                or (
                    journal.get("owned_folder")
                    and journal.get("cleanup_state") not in {"trashed", "deleted"}
                )
            ):
                result.append(batch)
        return result


def checkpoint_attempt(
    store,
    identifier,
    job_id,
    epoch,
    task_id,
    *,
    state=None,
    input_update=None,
    remote_update=None,
    error_code=None,
    member_state=None,
    local_failure=None,
):
    """Fence one task's publication/status, preserving immutable bytes and terminal evidence."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    if (input_update or {}).keys() - INPUT_FIELDS or (remote_update or {}).keys() - REMOTE_FIELDS:
        raise ValueError("Unknown runtime task receipt field.")
    states = {
        "reserved",
        "preparing",
        "publishing",
        "accepted",
        "running",
        "complete",
        "failed",
        "cancelled",
        "interrupted",
    }
    if state is not None and state not in states:
        raise ValueError("Unknown runtime task state.")
    if member_state is not None and member_state not in {
        "queued",
        "preparing",
        "uploading",
        "accepted",
        "running",
        "collecting",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }:
        raise ValueError("Unknown runtime member state.")
    if error_code is not None and (
        not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_-]{1,80}", error_code)
    ):
        raise ValueError("Runtime failures require a curated error code.")
    if local_failure is not None and (
        not isinstance(local_failure, str)
        or local_failure not in LOCAL_FAILURE_MESSAGES
        or member_state != "failed"
    ):
        raise ValueError("Local runtime failures require a curated failed-stage message.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        _for_job(db, job_id)
        member = next((m for m in batch["members"] if m["job_id"] == job_id), None)
        if member is None or member["task_id"] != task_id:
            raise StaleRuntimeAttempt(
                "This runtime task does not belong to the current member attempt."
            )
        if member["state"] == "completed" and member_state not in {None, "completed"}:
            raise ValueError("A saved runtime result cannot be downgraded.")
        if member["attempt_state"] in ATTEMPT_TERMINAL and state not in {
            None,
            member["attempt_state"],
        }:
            raise ValueError("A terminal runtime task cannot be reopened.")
        had_publish_intent = bool(member["input_receipt"].get("publish_intent"))
        inputs, remote = member["input_receipt"], member["remote_receipt"]
        previous_inputs, previous_remote = copy.deepcopy(inputs), copy.deepcopy(remote)
        for target, update in ((inputs, input_update or {}), (remote, remote_update or {})):
            for key, value in update.items():
                if key in target and target[key] != value:
                    if member["attempt_state"] in ATTEMPT_TERMINAL:
                        raise ValueError("A terminal runtime task receipt cannot change.")
                    # Status may advance; request/acceptance identities may never change.
                    if key not in {
                        "artifact_checkpoints",
                        "artifact_files",
                        "status_revision",
                        "outcome",
                        "error_code",
                        "complete",
                        "worker_gone",
                    }:
                        raise ValueError("A frozen runtime task receipt changed.")
                target[key] = value
        if (
            "artifact_origin" in inputs
            or "artifact_checkpoints" in remote
            or "artifact_files" in remote
        ):
            from . import runtime_capture

            job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            job["params"] = json.loads(job["params"])
            runtime_capture.check_update(
                job,
                {
                    "batch_id": identifier,
                    "task_id": task_id,
                    "attempt": member["attempt"],
                    "identity_hash": batch["compatibility_hash"],
                },
                previous_inputs,
                previous_remote,
                inputs,
                remote,
                terminal=member["attempt_state"] in ATTEMPT_TERMINAL,
            )
        if (
            batch["journal"].get("stop_intent")
            and input_update
            and input_update.get("publish_intent")
            and not had_publish_intent
        ):
            raise ValueError("This runtime allocation was stopped before publication.")
        next_state = state or member["attempt_state"]
        if (
            not had_publish_intent
            and next_state in {"failed", "cancelled"}
            and (
                member["attempt_state"] not in {"failed", "cancelled"}
                or (error_code is not None and error_code != member["attempt_error_code"])
            )
            and batch["seal_revision"] is None
        ):
            db.execute(
                "UPDATE runtime_batch_runs SET membership_revision=membership_revision+1 "
                "WHERE id=?",
                (identifier,),
            )
        db.execute(
            "UPDATE runtime_batch_attempts SET state=?,input_receipt=?,remote_receipt=?,"
            "error_code=COALESCE(?,error_code),updated=? WHERE job_id=? AND attempt=?",
            (
                state or member["attempt_state"],
                canonical(inputs),
                canonical(remote),
                error_code,
                now(),
                job_id,
                member["attempt"],
            ),
        )
        if member_state is not None:
            if member_state == "completed" and not member["result_id"]:
                raise ValueError("Only an atomic saved-result receipt completes a runtime member.")
            db.execute(
                "UPDATE runtime_batch_members SET state=?,updated=? WHERE job_id=?",
                (member_state, now(), job_id),
            )
            if member_state in {"failed", "cancelled", "interrupted"}:
                db.execute(
                    "UPDATE jobs SET state=?,message=?,updated=? WHERE id=? AND state!='completed'",
                    (
                        member_state,
                        local_failure or error_code or "Runtime task interrupted",
                        now(),
                        job_id,
                    ),
                )
        db.execute("UPDATE runtime_batch_runs SET updated=? WHERE id=?", (now(), identifier))
        return next(m for m in _get(db, identifier)["members"] if m["job_id"] == job_id)


def reconnect(store, identifier, epoch, *, remote_status):
    """Claim explicit recovery only against the exact freshly retrieved remote owner receipt."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    if not isinstance(remote_status, dict):
        raise ValueError("Retrieve the recorded runtime allocation before reconnecting.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        journal = batch["journal"]
        require_not_discarded(batch)
        if batch["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("This runtime coordinator is already active or resolved.")
        if (
            not journal.get("job_id")
            or remote_status.get("job_id") != journal["job_id"]
            or remote_status.get("generation") != journal.get("readiness_generation")
        ):
            raise ValueError("The retrieved runtime allocation identity/generation disagrees.")
        journal.update(
            remote_state=remote_status.get("state"),
            remote_observed_at=now(),
            accepting=remote_status.get("accepting") is True,
        )
        # Local 'open' from before shutdown is not authority to publish new inputs.
        if batch["admission"] == "open" and not (
            remote_status.get("accepting") is True and _admitting({**batch, "state": "running"})
        ):
            db.execute(
                "UPDATE runtime_batch_runs SET admission='draining' WHERE id=?", (identifier,)
            )
        journal["accepting"] = remote_status.get("accepting") is True
        db.execute(
            "UPDATE runtime_batch_runs SET state='running',coordinator_epoch="
            "coordinator_epoch+1,journal=?,updated=? WHERE id=?",
            (canonical(journal), now(), identifier),
        )
        return _get(db, identifier)


def queue_recovery(store, identifier, epoch, *, remote_status):
    """Request explicit manager-owned recovery after a fresh exact remote status read."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    if not isinstance(remote_status, dict):
        raise ValueError("Retrieve the recorded runtime allocation before reconnecting.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        journal = batch["journal"]
        require_not_discarded(batch)
        if batch["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("This runtime coordinator is already active or resolved.")
        if (
            not journal.get("job_id")
            or remote_status.get("job_id") != journal["job_id"]
            or remote_status.get("generation") != journal.get("readiness_generation")
        ):
            raise ValueError("The retrieved runtime allocation identity/generation disagrees.")
        journal.update(
            remote_state=remote_status.get("state"),
            remote_observed_at=now(),
            accepting=remote_status.get("accepting") is True,
            recovery_requested=True,
            recovery_epoch=epoch,
        )
        if batch["admission"] == "open" and not _admitting({**batch, "state": "running"}):
            db.execute(
                "UPDATE runtime_batch_runs SET admission='draining' WHERE id=?", (identifier,)
            )
        db.execute(
            "UPDATE runtime_batch_runs SET journal=?,updated=? WHERE id=?",
            (canonical(journal), now(), identifier),
        )
        return _get(db, identifier)


def setup_retryable(batch):
    """Absence of a remote ID alone never proves that allocation POST was not attempted."""
    journal = batch["journal"]
    storage = (batch["identity"]["settings"].get("ucloud_transcriber") or {}).get("storage_path")
    owned = journal.get("owned_folder")
    folder_safe = (not owned and not journal.get("folder_created")) or (
        journal.get("folder_created") is True
        and isinstance(storage, str)
        and owned == storage + "/cbs-runtime-batch-" + batch["id"]
    )
    return bool(
        batch["state"] in {"failed", "interrupted"}
        and batch["admission"] == "open"
        and batch["seal_revision"] is None
        and journal.get("no_post") is True
        and folder_safe
        and not _uncertain(journal)
        and not any(
            journal.get(key)
            for key in (
                "job_id",
                "submission_intent",
                "submission_attempted",
                "submission_attempted_at",
                "reconciled_not_created",
                "reconciled_at",
                "remote_state",
                "remote_observed_at",
                "readiness_generation",
                "readiness_hash",
                "started_at",
                "hard_deadline",
                "deadline_at",
                "late_admission_deadline",
                "published_membership_revision",
                "stop_intent",
                "stop_requested_at",
                "stop_acknowledged",
                "close_intent",
                "close_acknowledged",
                "termination_requested",
                "discard_confirmed",
                "discard_decision",
                "discarded_at",
                "release_decision",
                "cleanup_state",
                "recovery_requested",
                "capacity_reserved",
            )
        )
        and bool(batch["members"])
        and all(
            member["attempt"] == 1
            and member["attempt_state"] in {"reserved", "preparing", "interrupted"}
            and member["state"] in {"queued", "preparing", "interrupted"}
            and member["result_id"] is None
            and all(
                isinstance(member[key], dict) and not member[key]
                for key in ("output_receipt", "input_receipt", "remote_receipt")
            )
            for member in batch["members"]
        )
    )


def queue_setup_retry(store, identifier, epoch):
    """Explicitly resume only untouched initial tasks after a proven pre-POST failure."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        if not setup_retryable(batch):
            raise ValueError(
                "This runtime setup cannot be retried safely. Reconnect or reconcile any "
                "recorded allocation; otherwise start a fresh transcription request."
            )
        attempts = db.execute(
            "SELECT COUNT(*) FROM runtime_batch_attempts a JOIN runtime_batch_members m "
            "ON m.job_id=a.job_id WHERE m.batch_id=?",
            (identifier,),
        ).fetchone()[0]
        if attempts != len(batch["members"]):
            raise ValueError("A prior runtime attempt prevents retrying setup.")
        for member in batch["members"]:
            _for_job(db, member["job_id"])
            job = db.execute(
                "SELECT state,params FROM jobs WHERE id=?", (member["job_id"],)
            ).fetchone()
            if (
                job["state"] not in {"queued", "interrupted"}
                or "runtime_output_receipt" in json.loads(job["params"])
                or db.execute(
                    "SELECT 1 FROM transcript_results WHERE job_id=?", (member["job_id"],)
                ).fetchone()
            ):
                raise ValueError("A runtime member changed before setup could be retried.")
        stamp = now()
        for member in batch["members"]:
            db.execute(
                "UPDATE runtime_batch_members SET state='queued',updated=? WHERE job_id=?",
                (stamp, member["job_id"]),
            )
            db.execute(
                "UPDATE jobs SET state='queued',progress=0,message='Explicit setup upload retry',"
                "updated=? WHERE id=?",
                (stamp, member["job_id"]),
            )
        db.execute(
            "UPDATE runtime_batch_runs SET state='queued',coordinator_epoch="
            "coordinator_epoch+1,progress=0,message='Waiting to retry setup upload',"
            "updated=? WHERE id=?",
            (stamp, identifier),
        )
        return _get(db, identifier)


def queue_result_recovery(store, identifier, job_id, epoch, *, task_id):
    """Retry only the local save for the exact completed task while its owner is still live."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        require_not_discarded(batch)
        _for_job(db, job_id)
        member = next((m for m in batch["members"] if m["job_id"] == job_id), None)
        if batch["state"] != "running" or member is None or member["task_id"] != task_id:
            raise ValueError("The live runtime result-recovery owner or task changed.")
        if (
            member["attempt_state"] != "complete"
            or member["state"] not in {"failed", "interrupted", "queued"}
            or member["result_id"]
            or db.execute("SELECT 1 FROM transcript_results WHERE job_id=?", (job_id,)).fetchone()
        ):
            raise ValueError(
                "Only an unsaved completed runtime task can retry its local result save."
            )
        row = db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()
        params = json.loads(row["params"])
        receipt = params.get("runtime_output_receipt")
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"batch_id", "task_id", "attempt", "epoch", "files"}
            or receipt["batch_id"] != identifier
            or receipt["task_id"] != task_id
            or type(receipt["attempt"]) is not int
            or receipt["attempt"] != member["attempt"]
            or not isinstance(receipt["files"], dict)
            or not _required_stages(batch["identity"]).issubset(receipt["files"])
        ):
            raise ValueError("The completed runtime task has no matching durable output receipt.")
        remote = member["remote_receipt"]
        if (
            remote.get("outcome") != "complete"
            or not isinstance(remote.get("files"), dict)
            or not _required_stages(batch["identity"]).issubset(remote["files"])
            or any(receipt["files"].get(name) != value for name, value in remote["files"].items())
        ):
            raise ValueError("The cached runtime outputs differ from the task's terminal receipt.")
        _verify_files(store, job_id, member["recording_id"], receipt["files"])
        params["runtime_output_receipt"] = {**receipt, "epoch": epoch}
        db.execute(
            "UPDATE jobs SET state='queued',message='Retrying cached runtime result save',params=?,"
            "updated=? WHERE id=?",
            (canonical(params), now(), job_id),
        )
        db.execute(
            "UPDATE runtime_batch_members SET state='queued',updated=? WHERE job_id=?",
            (now(), job_id),
        )
        db.execute("UPDATE runtime_batch_runs SET updated=? WHERE id=?", (now(), identifier))
        return next(m for m in _get(db, identifier)["members"] if m["job_id"] == job_id)


def retry_member(store, identifier, job_id, epoch, *, task_id, carried_checkpoints=None):
    """Explicit processing retry only; uncertain publication stays on its original task."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    carried = carried_checkpoints or {}
    canonical(carried)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        require_not_discarded(batch)
        _for_job(db, job_id)
        member = next((m for m in batch["members"] if m["job_id"] == job_id), None)
        if member is None or member["task_id"] != task_id:
            raise ValueError("The runtime retry task changed; reload before retrying.")
        if not _admitting(batch) or batch["state"] != "running":
            raise ValueError(
                "This runtime allocation is closed; start a new run for new inference."
            )
        if (
            member["result_id"]
            or db.execute("SELECT 1 FROM transcript_results WHERE job_id=?", (job_id,)).fetchone()
        ):
            raise ValueError(
                "This runtime result is already saved; recover outputs or cleanup only."
            )
        remote = member["remote_receipt"]
        if member["attempt_state"] != "failed" and not (
            member["attempt_state"] == "interrupted" and remote.get("worker_gone") is True
        ):
            raise ValueError("Reconnect the same runtime task until its worker outcome is known.")
        if not (
            remote.get("outcome") in {"failed", "cancelled"} or remote.get("worker_gone") is True
        ):
            raise ValueError("A runtime retry requires confirmed terminal processing evidence.")
        previous = remote.get("files", {})
        if not isinstance(carried, dict) or any(previous.get(k) != v for k, v in carried.items()):
            raise ValueError(
                "Retry checkpoints must match the prior task's verified output hashes."
            )
        if carried:
            _verify_files(store, job_id, member["recording_id"], carried)
        attempt, task = member["attempt"] + 1, new_id()
        replacement = {**member, "attempt": attempt, "task_id": task}
        if _control_size(db, batch, replacement) > MAX_CONTROL_BYTES:
            raise ValueError("This runtime control file is full; start a new run.")
        # An interrupted attempt becomes terminal only after confirmed worker absence.
        if member["attempt_state"] == "interrupted":
            db.execute(
                "UPDATE runtime_batch_attempts SET state='failed',updated=? WHERE job_id=? "
                "AND attempt=?",
                (now(), job_id, member["attempt"]),
            )
        db.execute(
            "INSERT INTO runtime_batch_attempts(job_id,attempt,task_id,input_receipt,created,"
            "updated) VALUES (?,?,?,?,?,?)",
            (job_id, attempt, task, canonical({"carried_checkpoints": carried}), now(), now()),
        )
        db.execute(
            "UPDATE runtime_batch_members SET current_attempt=?,state='queued',updated=? "
            "WHERE job_id=?",
            (attempt, now(), job_id),
        )
        params = json.loads(
            db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        )
        params.pop("runtime_output_receipt", None)
        db.execute(
            "UPDATE jobs SET state='queued',progress=0,message='Explicit runtime retry',"
            "params=?,updated=? "
            "WHERE id=?",
            (canonical(params), now(), job_id),
        )
        db.execute(
            "UPDATE runtime_batch_runs SET membership_revision=membership_revision+1,updated=? "
            "WHERE id=?",
            (now(), identifier),
        )
        return next(m for m in _get(db, identifier)["members"] if m["job_id"] == job_id)


def _verify_files(store, job_id, recording_id, files):
    if not isinstance(files, dict) or not files or len(files) > 32:
        raise ValueError("A runtime result needs its exact durable output files.")
    base = (store.directory(recording_id) / "enhancements" / job_id).resolve()
    for name, receipt in files.items():
        if (
            not isinstance(name, str)
            or "\\" in name
            or not re.fullmatch(r"[a-zA-Z0-9_./-]{1,240}", name)
            or any(p in {"", ".", ".."} for p in name.split("/"))
            or PurePosixPath(name).is_absolute()
        ):
            raise ValueError("Runtime output filenames must stay inside their exact member folder.")
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"size", "sha256"}
            or type(receipt.get("size")) is not int
            or not 0 <= receipt["size"] <= 268435456
            or not isinstance(receipt.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", receipt["sha256"])
        ):
            raise ValueError("Runtime output file receipts require exact sizes and SHA-256 hashes.")
        path = base / name
        if not path.resolve().is_relative_to(base) or path.is_symlink():
            raise ValueError("Runtime output files cannot escape their member folder.")
        try:
            if path.stat().st_size != receipt["size"]:
                raise ValueError("A runtime output file size differs from its verified receipt.")
            with path.open("r+b") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != receipt["sha256"]:
                    raise ValueError(
                        "A runtime output file hash differs from its verified receipt."
                    )
                os.fsync(stream.fileno())
            if path.suffix == ".json":
                json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError(
                "A required durable runtime output file is missing or invalid."
            ) from None


def _required_stages(identity):
    required = {"alignment.json"} if identity["options"]["align"] else set()
    if identity["options"]["diarize"]:
        required.add("diarization.json")
    if identity["operation"] == "transcription":
        required.add("transcription.json")
    if identity["operation"] == "passage_retranscription":
        required.add("retranscription.json")
    return required


def record_result(db, store, job, result_id, receipt=None, *, existing=False):
    """Join the producing-job result receipt in the caller's transaction, even after Stop."""
    from . import restored_owners

    restored_owners.require_owned(db, "job", job["id"]) if job is not None else None
    if job is None:
        if receipt is not None:
            raise ValueError("A runtime result receipt requires its producing job.")
        return
    batch = _for_job(db, dict(job))
    if batch is None:
        if receipt is not None:
            raise ValueError("This producing job does not belong to a runtime allocation.")
        return
    require_not_discarded(batch)
    member = next(m for m in batch["members"] if m["job_id"] == job["id"])
    supplied = receipt is not None
    receipt = (
        receipt
        or json.loads(job["params"]).get("runtime_output_receipt")
        or member["output_receipt"]
    )
    if not isinstance(receipt, dict) or set(receipt) != {
        "batch_id",
        "task_id",
        "attempt",
        "epoch",
        "files",
    }:
        raise ValueError("A runtime result needs its exact batch/task output receipt.")
    if (
        receipt["batch_id"] != batch["id"]
        or receipt["task_id"] != member["task_id"]
        or type(receipt["attempt"]) is not int
        or receipt["attempt"] != member["attempt"]
    ):
        raise ValueError("This runtime result belongs to another member attempt.")
    if supplied or not member["result_id"]:
        _fence(batch, receipt["epoch"])
    receipt = {**receipt, "epoch": batch["coordinator_epoch"]}
    if member["result_id"] not in {None, result_id}:
        raise ValueError("This runtime member already owns another saved result.")
    required = _required_stages(batch["identity"])
    if not isinstance(receipt["files"], dict) or not required.issubset(receipt["files"]):
        raise ValueError("The runtime result receipt omits a required completed stage.")
    recorded = member["output_receipt"]
    durable = (
        existing
        and recorded
        and all(
            recorded.get(key) == receipt[key] for key in ("batch_id", "task_id", "attempt", "files")
        )
    )
    if not durable:
        _verify_files(store, job["id"], job["recording_id"], receipt["files"])
    db.execute(
        "UPDATE runtime_batch_members SET state='completed',result_id=?,output_receipt=?,"
        "updated=? WHERE job_id=?",
        (result_id, canonical(receipt), now(), job["id"]),
    )
    db.execute(
        "UPDATE runtime_batch_attempts SET state='complete',updated=? WHERE job_id=? AND attempt=?",
        (now(), job["id"], member["attempt"]),
    )
    db.execute(
        "UPDATE jobs SET state='completed',progress=1,message='Complete',updated=? WHERE id=?",
        (now(), job["id"]),
    )
    db.execute("UPDATE runtime_batch_runs SET updated=? WHERE id=?", (now(), batch["id"]))


def recover(db):
    """Lease-protected startup marks lost ownership; never infer a POST or replay a task."""
    from . import restored_owners

    stamp = now()
    db.execute(
        "UPDATE runtime_batch_runs SET state='interrupted',coordinator_epoch="
        "coordinator_epoch+1,message='Reconnect to the recorded CBS Runtime allocation.',"
        "updated=? WHERE state='running' AND "
        + restored_owners.sql_exclusion(db, "runtime_batch", "runtime_batch_runs.id"),
        (stamp,),
    )
    db.execute(
        "UPDATE runtime_batch_members SET state='interrupted',updated=? WHERE state NOT IN "
        "('queued','completed','failed','cancelled') AND "
        + restored_owners.sql_exclusion(db, "job", "runtime_batch_members.job_id")
        + " AND "
        + restored_owners.sql_exclusion(db, "runtime_batch", "runtime_batch_members.batch_id"),
        (stamp,),
    )
    db.execute(
        "UPDATE runtime_batch_attempts SET state='interrupted',updated=? WHERE state NOT IN "
        "('reserved','complete','failed','cancelled') AND "
        + restored_owners.sql_exclusion(db, "job", "runtime_batch_attempts.job_id"),
        (stamp,),
    )


def interrupt(store, identifier, epoch, message):
    """Fence one lost coordinator and its unfinished claims without inventing task outcomes."""
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        batch = _get(db, identifier)
        _fence(batch, epoch)
        if batch["journal"].get("discard_confirmed") or batch["journal"].get("cleanup_state") in {
            "trashed",
            "deleted",
        }:
            return batch
        stamp = now()
        db.execute(
            "UPDATE runtime_batch_runs SET state='interrupted',coordinator_epoch="
            "coordinator_epoch+1,message=?,updated=? WHERE id=?",
            (message[:700], stamp, identifier),
        )
        for member in batch["members"]:
            if member["state"] in {"preparing", "uploading", "accepted", "running", "collecting"}:
                db.execute(
                    "UPDATE runtime_batch_members SET state='interrupted',updated=? WHERE job_id=?",
                    (stamp, member["job_id"]),
                )
            db.execute(
                "UPDATE jobs SET state='interrupted',message=?,updated=? WHERE id=? "
                "AND state IN ('queued','running')",
                (message[:700], stamp, member["job_id"]),
            )
        return _get(db, identifier)
