"""Retrieval-only startup admission for already submitted native jobs (ADR 0174)."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

import httpx
from ucloud_workflow import UCloudAPIError

from . import native_capture, providers, restored_owners
from .media import Cancelled

POLICY = "native-startup-reconnect-v1"
ATTEMPT = "startup_reconnect"
FAILURE = "native_last_failure"
STOP = "stop_requested"
MAX_OWNERS = 1000
MAX_MEMBERS = 1000
MAX_METADATA_BYTES = 8 * 1024**2
STOP_FLAGS = (STOP, "cancel_requested", "cancellation_requested", "termination_requested")
EXCLUDED = (*native_capture.EXCLUDED, "followup", "replacement_required", "new_run_required")
JOURNAL_IDENTITY = (
    "job_id",
    "submission_attempted",
    "submission_intent",
    "reconciled_at",
    "reconciled_not_created",
    "owned_folder",
    "folders_created",
    "root_created",
    "directory_parameter",
    "output_model",
    "output_language",
    "output_application",
    "artifact_submission_sha256",
    # Early selections (ADR 0183): files dropped before the sealed submission. Absent on
    # older batches, which therefore keep their exact earlier identity.
    "membership_sealed",
    "dropped_members",
)
NOTICE = "Reconnecting to the same UCloud job; saved results are kept."
REFUSED = (
    "Automatic UCloud reconnection could not verify this allocation. Use Activity to recover it."
)


@dataclass(frozen=True)
class Authority:
    kind: str
    identifier: str
    remote_id: str
    generation: str
    lifecycle: str
    job_ids: tuple


def _object(raw):
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError(REFUSED)
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError(REFUSED)
    return raw


def _row(db, table, identifier):
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError(REFUSED)
    row = dict(row)
    row["params"] = _object(row["params"])
    return row


def _run(params):
    run = params.get("ucloud_run")
    if not isinstance(run, dict):
        raise ValueError(REFUSED)
    return run


def _negative(run, *, allow_stop=False):
    # Malformed negatives cannot become a positive permission.
    return (
        (not allow_stop and any(key in run and run[key] is not False for key in STOP_FLAGS))
        or any(
            run.get(key)
            for key in (
                "release_decision",
                "deleted_members",
                "replacement_required",
                "new_run_required",
            )
        )
        or run.get("cleanup_state") in {"trashed", "deleted"}
    )


def _frozen(job):
    params = job["params"]
    if job["kind"] != "transcribe" or any(key in params for key in EXCLUDED):
        raise ValueError(REFUSED)
    settings = providers.Settings.model_validate(params["settings"])
    if settings.provider_type != "ucloud" or settings.ucloud_transcriber is None:
        raise ValueError(REFUSED)
    from . import ucloud_native

    ucloud_native.require_setup(settings, "metadata-only")
    native_capture.validate_allocation(params)
    frozen = {
        key: value
        for key, value in params.items()
        if key not in {"ucloud_run", native_capture.KEYS[1]}
    }
    if native_capture.enabled(job):
        captured = native_capture.validate(params[native_capture.KEYS[1]], job)
        if not captured["submissions"]:
            raise ValueError(REFUSED)
        frozen["native_submissions"] = captured["submissions"]
    return frozen, settings


def _submitted(run):
    if run.get("submission_attempted") is True:
        return True
    value = run.get("reconciled_at")
    if not isinstance(value, str) or not 0 < len(value) <= 80 or any(ord(c) < 32 for c in value):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _producer_result(db, job, remote_id):
    rows = db.execute(
        "SELECT id,recording_id,source FROM transcript_results WHERE job_id=? LIMIT 2",
        (job["id"],),
    ).fetchall()
    if not rows:
        return None
    if (
        len(rows) != 1
        or rows[0]["recording_id"] != job["recording_id"]
        or _object(rows[0]["source"]).get("ucloud_job_id") != remote_id
    ):
        raise ValueError(REFUSED)
    return rows[0]


def _observe(db, kind, identifier, *, allow_stop=False):
    """One metadata snapshot; no source bodies, files, decoder, runtime or network."""
    from . import audio_inputs

    if kind not in {"job", "native_batch"}:
        raise ValueError(REFUSED)
    restored_owners.require_owned(db, kind, identifier)
    owner = _row(db, "jobs" if kind == "job" else "native_batch_runs", identifier)
    params, run = owner["params"], _run(owner["params"])
    remote_id = run.get("job_id")
    if (
        not isinstance(remote_id, str)
        or not remote_id.strip()
        or len(remote_id) > 200
        or any(ord(char) < 32 for char in remote_id)
        or not _submitted(run)
        or run.get("reconciled_not_created") is True
        or _negative(run, allow_stop=allow_stop)
        or owner["state"] == "cancelled"
    ):
        raise ValueError(REFUSED)
    if kind == "job":
        if "native_batch_id" in params:
            raise ValueError(REFUSED)
        members = [{"job_id": identifier, "recording_id": owner["recording_id"]}]
    else:
        members = json.loads(owner["members"])
        if not isinstance(members, list) or not 2 <= len(members) <= MAX_MEMBERS:
            raise ValueError(REFUSED)
        # Files dropped before an early selection's sealed submission never joined the
        # allocation (ADR 0183); a submitted early batch must carry its exact seal.
        dropped = run.get("dropped_members", [])
        if "native_group" in params and run.get("membership_sealed") is not True:
            raise ValueError(REFUSED)
        if (
            not isinstance(dropped, list)
            or not all(isinstance(item, str) for item in dropped)
            or len(set(dropped)) != len(dropped)
            or not set(dropped) < {m.get("job_id") for m in members if isinstance(m, dict)}
        ):
            raise ValueError(REFUSED)
        members = [m for m in members if not isinstance(m, dict) or m["job_id"] not in dropped]
    jobs, identities, seen, project_id = [], [], set(), owner.get("project_id")
    for member in members:
        if (
            not isinstance(member, dict)
            or set(member)
            != (
                {"job_id", "recording_id"}
                if kind == "job"
                else {"job_id", "recording_id", "filename"}
            )
            or not all(isinstance(value, str) and value for value in member.values())
        ):
            raise ValueError(REFUSED)
        restored_owners.require_owned(db, "job", member["job_id"])
        child = _row(db, "jobs", member["job_id"])
        if child["recording_id"] != member["recording_id"] or child["state"] == "cancelled":
            raise ValueError(REFUSED)
        if child["recording_id"] in seen:
            raise ValueError(REFUSED)
        seen.add(child["recording_id"])
        frozen, settings = _frozen(child)
        child_run = child["params"].get("ucloud_run", {})
        if not isinstance(child_run, dict) or _negative(child_run, allow_stop=allow_stop):
            raise ValueError(REFUSED)
        if kind == "native_batch" and (
            child["params"].get("native_batch_id") != identifier
            or any(
                child["params"].get(key) != params.get(key)
                for key in ("settings", "provider_id", "native_allocation")
            )
        ):
            raise ValueError(REFUSED)
        recording = db.execute(
            "SELECT id,project_id,filename,revision,active_transcript_id "
            "FROM recordings WHERE id=?",
            (child["recording_id"],),
        ).fetchone()
        if recording is None:
            raise ValueError(REFUSED)
        if project_id is None:
            project_id = recording["project_id"]
        if (
            recording["project_id"] != project_id
            or not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone()
        ):
            raise ValueError(REFUSED)
        if "audio_input" in child["params"]:
            source = audio_inputs.validate(child["params"]["audio_input"])
            if source["recording_id"] != child["recording_id"]:
                raise ValueError(REFUSED)
        if kind == "native_batch":
            from pathlib import PurePosixPath

            if (
                member["filename"]
                != child["id"] + PurePosixPath(recording["filename"]).suffix.lower()
            ):
                raise ValueError(REFUSED)
        if native_capture.enabled(child) and run.get(
            "artifact_submission_sha256"
        ) != native_capture.submission_sha256(child["params"][native_capture.KEYS[1]]):
            raise ValueError(REFUSED)
        if child["state"] == "completed" and _producer_result(db, child, remote_id) is None:
            raise ValueError(REFUSED)
        jobs.append(child)
        identities.append(
            {
                "member": member,
                "project_id": project_id,
                "filename": recording["filename"],
                "params": frozen,
            }
        )
    expected_folder = (
        settings.ucloud_transcriber.storage_path
        + ("/cbs-" if kind == "job" else "/cbs-batch-")
        + identifier
    )
    if run.get("owned_folder") != expected_folder or run.get("folders_created") is not True:
        raise ValueError(REFUSED)
    if kind == "native_batch":
        actual = {
            row[0]
            for row in db.execute(
                "SELECT id FROM jobs WHERE json_extract(CASE WHEN json_valid(params) "
                "THEN params END,'$.native_batch_id')=?",
                (identifier,),
            )
        }
        if actual != {job["id"] for job in jobs}:
            raise ValueError(REFUSED)
    generation = hashlib.sha256(
        native_capture.canonical(
            {
                "kind": kind,
                "owner": identifier,
                "params": {
                    key: value
                    for key, value in params.items()
                    if key not in {"ucloud_run", native_capture.KEYS[1]}
                },
                "submission": {key: run[key] for key in JOURNAL_IDENTITY if key in run},
                "members": identities,
            },
            MAX_METADATA_BYTES,
        )
    ).hexdigest()
    return owner, run, jobs, generation


def _write_run(db, kind, identifier, params, run):
    params = {**params, "ucloud_run": run}
    table = "jobs" if kind == "job" else "native_batch_runs"
    db.execute(
        f"UPDATE {table} SET params=? WHERE id=?",
        (json.dumps(params, ensure_ascii=False), identifier),
    )


def stop_row(db, row):
    """Called in the same writer transaction that accepts a singleton Stop."""
    row = dict(row)
    if row["kind"] != "transcribe":
        return
    params = _object(row["params"])
    settings = params.get("settings")
    if (
        row.get("state") == "completed"
        or not isinstance(settings, dict)
        or settings.get("provider_type") != "ucloud"
    ):
        return
    run = params.get("ucloud_run", {})
    if not isinstance(run, dict):
        raise ValueError(REFUSED)
    _write_run(db, "job", row["id"], params, {**run, STOP: True})


def stop_batch(store, identifier):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        restored_owners.require_owned(db, "native_batch", identifier)
        owner = _row(db, "native_batch_runs", identifier)
        _write_run(
            db, "native_batch", identifier, owner["params"], {**_run(owner["params"]), STOP: True}
        )


def preserve_legacy_stops(db):
    # Human-readable legacy text is negative compatibility evidence only.
    for table, kind in (("jobs", "job"), ("native_batch_runs", "native_batch")):
        safe = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
        native = (
            f"AND kind='transcribe' AND json_extract({safe},'$.settings.provider_type')='ucloud' "
            if kind == "job"
            else ""
        )
        db.execute(
            f"UPDATE {table} SET params=json_set(params,'$.ucloud_run.{STOP}',json('true')) "
            f"WHERE state='running' AND json_type({safe})='object' "
            f"AND json_type({safe},'$.ucloud_run')='object' {native}"
            "AND (message LIKE 'Cancelling%' OR message LIKE 'Stopping UCloud%') AND "
            + restored_owners.sql_exclusion(db, kind, table + ".id")
        )


def _saved(db, job, run):
    """An exact producer result may finish locally, including a result already in Trash."""
    from .history import decode_payload

    result = _producer_result(db, job, run["job_id"])
    if result is None:
        return False
    creation = db.execute(
        "SELECT * FROM result_versions WHERE result_id=? ORDER BY sequence LIMIT 1", (result["id"],)
    ).fetchone()
    if creation is None or creation["action"] != "provider_result":
        raise ValueError(REFUSED)
    decode_payload(creation["payload"], creation["payload_sha256"])
    if native_capture.enabled(job):
        value = native_capture.validate_ownership(db, job["params"][native_capture.KEYS[1]], job)
        context = _object(creation["context"])
        expected = native_capture.bind(
            value,
            creation={
                "result_id": result["id"],
                "version_id": creation["id"],
                "payload_sha256": creation["payload_sha256"],
            },
        )
        if context.get("native_artifacts") != expected:
            raise ValueError(REFUSED)
    return True


def admit(store, db, lifecycle):
    """Caller holds the serving lease and BEGIN IMMEDIATE after generic recovery."""
    from .store import now

    admitted = {}
    candidates = [
        ("native_batch", row[0])
        for row in db.execute(
            "SELECT id FROM native_batch_runs WHERE state IN ('interrupted','failed','queued') "
            "AND json_extract(CASE WHEN json_valid(params) THEN params END, "
            "'$.ucloud_run.job_id') IS NOT NULL ORDER BY updated DESC,id LIMIT ?",
            (MAX_OWNERS,),
        )
    ]
    candidates += [
        ("job", row[0])
        for row in db.execute(
            "SELECT id FROM jobs WHERE kind='transcribe' "
            "AND state IN ('interrupted','failed','queued') "
            "AND json_extract(CASE WHEN json_valid(params) THEN params END, "
            "'$.native_batch_id') IS NULL AND json_extract(CASE WHEN json_valid(params) "
            "THEN params END,'$.ucloud_run.job_id') IS NOT NULL ORDER BY updated DESC,id LIMIT ?",
            (MAX_OWNERS,),
        )
    ]
    for kind, identifier in candidates:
        try:
            owner, run, jobs, generation = _observe(db, kind, identifier)
            proof = {
                "policy": POLICY,
                "kind": kind,
                "identifier": identifier,
                "remote_id": run["job_id"],
                "generation": generation,
            }
            previous = run.get(ATTEMPT)
            if previous is not None:
                if (
                    not isinstance(previous, dict)
                    or set(previous) != set(proof) | {"lifecycle"}
                    or not isinstance(previous["lifecycle"], str)
                    or previous != {**proof, "lifecycle": previous["lifecycle"]}
                ):
                    raise ValueError(REFUSED)
                if previous["lifecycle"] == lifecycle:
                    if owner["state"] == "queued" and all(
                        job["state"] in {"queued", "completed"} for job in jobs
                    ):
                        token = Authority(
                            kind,
                            identifier,
                            run["job_id"],
                            generation,
                            lifecycle,
                            tuple(job["id"] for job in jobs),
                        )
                        admitted.update({job["id"]: token for job in jobs})
                    continue
            retrievable = owner["state"] != "failed" or run.get(FAILURE) in [
                {**proof, "reason": reason}
                for reason in ("monitor_transient_exhausted", "download_transient_exhausted")
            ]
            pending, finalized = [], []
            own_ids = {job["id"] for job in jobs}
            for job in jobs:
                if job["state"] == "completed":
                    # Completed siblings are never re-imported or modified.
                    continue
                if job["state"] not in {"interrupted", "failed", "queued"}:
                    raise ValueError(REFUSED)
                if _saved(db, job, run):
                    finalized.append(job)
                    continue
                for other in db.execute(
                    "SELECT id,kind FROM jobs WHERE recording_id=? "
                    "AND state IN ('queued','running')",
                    (job["recording_id"],),
                ):
                    if other["id"] not in own_ids and other["kind"] not in {
                        "media",
                        "source-inspect",
                    }:
                        raise ValueError(REFUSED)
                pending.append(job)
            if not pending and not finalized:
                continue  # Cleanup-only is a separate explicit operation.
            for job in finalized:
                db.execute(
                    "UPDATE jobs SET state='completed',progress=1,message='Complete',updated=? "
                    "WHERE id=?",
                    (now(), job["id"]),
                )
            if pending and not retrievable:
                continue  # A proven local finalizer grants no remote recovery permission.
            if not pending:
                if kind == "native_batch" and run.get("remote_state") in {
                    "SUCCESS",
                    "FAILURE",
                    "EXPIRED",
                    "ABORTED",
                    "CANCELLED",
                    "SUSPENDED",
                }:
                    db.execute(
                        "UPDATE native_batch_runs SET state='completed',progress=1,"
                        "message='UCloud batch complete',updated=? WHERE id=?",
                        (now(), identifier),
                    )
                continue
            attempt = {**proof, "lifecycle": lifecycle}
            _write_run(db, kind, identifier, owner["params"], {**run, ATTEMPT: attempt})
            for job in pending:
                db.execute(
                    "UPDATE jobs SET state='queued',message=?,updated=? WHERE id=?",
                    (NOTICE, now(), job["id"]),
                )
            if kind == "native_batch":
                db.execute(
                    "UPDATE native_batch_runs SET state='queued',message=?,updated=? WHERE id=?",
                    (NOTICE, now(), identifier),
                )
            authority = Authority(
                kind,
                identifier,
                run["job_id"],
                generation,
                lifecycle,
                tuple(job["id"] for job in jobs),
            )
            admitted.update({job["id"]: authority for job in jobs})
        except (ValueError, KeyError, TypeError, RecursionError):
            # Refuse each unverifiable owner independently; preserve its original evidence.
            table = "jobs" if kind == "job" else "native_batch_runs"
            db.execute(
                f"UPDATE {table} SET state='interrupted',message=? WHERE id=? AND state='queued'",
                (REFUSED, identifier),
            )
    return admitted


def authority(worker, job):
    return getattr(worker.coordinator, "native_reconnect", {}).get(job["id"])


def guard(store, token, *, connection=None, allow_stop=False):
    if token is None:
        return
    if connection is None:
        with store.connection() as db:
            return guard(store, token, connection=db, allow_stop=allow_stop)
    current = _row(
        connection, "jobs" if token.kind == "job" else "native_batch_runs", token.identifier
    )
    if not allow_stop and any(_run(current["params"]).get(flag) is True for flag in STOP_FLAGS):
        raise Cancelled()
    owner, run, jobs, generation = _observe(
        connection, token.kind, token.identifier, allow_stop=allow_stop
    )
    if (
        run["job_id"] != token.remote_id
        or generation != token.generation
        or tuple(job["id"] for job in jobs) != token.job_ids
    ):
        raise ValueError(REFUSED)
    expected = {
        "policy": POLICY,
        "kind": token.kind,
        "identifier": token.identifier,
        "remote_id": token.remote_id,
        "generation": token.generation,
        "lifecycle": token.lifecycle,
    }
    if run.get(ATTEMPT) != expected or owner["state"] not in {"queued", "running", "completed"}:
        raise ValueError(REFUSED)


def checkpoint(store, token, values):
    """Merge a reconnecting singleton journal under the same Stop/identity fence."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        guard(store, token, connection=db, allow_stop=values == {"cancellation_requested": True})
        owner = _row(db, "jobs", token.identifier)
        _write_run(
            db, "job", token.identifier, owner["params"], {**_run(owner["params"]), **values}
        )
    return store.job(token.identifier)["params"]


def failure(store, kind, identifier, error=None, *, phase=None):
    """Latest typed reason replaces stale reasons; UI text never grants authority."""
    from .native_batch import MonitoringInterrupted, OutputReadError

    reason = None
    if isinstance(error, MonitoringInterrupted):
        reason = "monitor_transient_exhausted"
    elif (
        isinstance(error, OutputReadError)
        and error.details["attempts"] == 3
        and error.details["http_status"] in {None, 502, 503, 504}
    ):
        reason = "download_transient_exhausted"
    elif phase in {"monitor", "download"} and (
        isinstance(error, UCloudAPIError)
        and error.status_code in {502, 503, 504}
        or isinstance(
            error, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
        )
    ):
        reason = phase + "_transient_exhausted"
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            owner, run, _, generation = _observe(db, kind, identifier)
        except (ValueError, KeyError, TypeError):
            return
        receipt = (
            None
            if reason is None
            else {
                "policy": POLICY,
                "kind": kind,
                "identifier": identifier,
                "remote_id": run["job_id"],
                "generation": generation,
                "reason": reason,
            }
        )
        _write_run(db, kind, identifier, owner["params"], {**run, FAILURE: receipt})


def download_single(client, settings, folder, job, recording, guard, wait):
    """Use the existing bounded read transport, never a singleton upload/setup path."""
    from pathlib import PurePosixPath

    from .native_batch import _download, _output_paths

    filename = "recording" + PurePosixPath(recording["filename"]).suffix.lower()
    selector = {"model": settings.model.rsplit("/", 1)[-1], "language": settings.language}
    if native_capture.enabled(job):
        descriptor = native_capture.validate(job["params"][native_capture.KEYS[1]], job)[
            "submissions"
        ][-1]["descriptor"]
        filename = descriptor["input"]["remote_name"]
        selector = descriptor["output_selector"]
    paths = _output_paths(client, folder, guard=guard, wait=wait)
    return _download(
        client,
        settings.base_url,
        paths,
        folder,
        filename,
        selector["model"],
        selector["language"],
        with_name=True,
        guard=guard,
        wait=wait,
    )


def explicit_retry(queue, job):
    """Only the already-validated manual Retry endpoint may reset T141 negative intent."""
    params = job["params"]
    settings = params.get("settings")
    if (
        job["kind"] != "transcribe"
        or not isinstance(settings, dict)
        or settings.get("provider_type") != "ucloud"
        or params.get("native_batch_id")
        or "ucloud_run" not in params
    ):
        return False
    from .store import now

    with queue.store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        restored_owners.require_owned(db, "job", job["id"])
        current = _row(db, "jobs", job["id"])
        if (
            current["state"] not in {"failed", "cancelled", "interrupted"}
            or current["recording_id"] != job["recording_id"]
        ):
            raise ValueError("This job changed before Retry was accepted. Refresh Activity.")
        # Earlier endpoint validation may update its historical revision only. All other
        # frozen data and the latest journal/Stop intent must still match that observation.
        observed = dict(params)
        observed["revision"] = current["params"].get("revision")
        if native_capture.canonical(observed) != native_capture.canonical(current["params"]):
            raise ValueError("This job changed before Retry was accepted. Refresh Activity.")
        native_capture.assert_params_preserved(current["params"], params)
        run = _run(current["params"])
        proposed = {**params, "ucloud_run": {**run, STOP: False, ATTEMPT: None, FAILURE: None}}
        db.execute(
            "UPDATE jobs SET params=?,state='queued',message='Queued for retry',updated=? "
            "WHERE id=?",
            (json.dumps(proposed, ensure_ascii=False), now(), job["id"]),
        )
    queue.native_reconnect.pop(job["id"], None)
    return True
