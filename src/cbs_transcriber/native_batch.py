"""One durable native allocation with independently committed recording results."""

import hashlib
import json
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import PurePosixPath

import httpx
from ucloud_workflow import UCloudAPIError
from ucloud_workflow.jobs import extract_job_id

from . import native_capture, providers
from . import ucloud_native as native
from .media import Cancelled
from .store import now
from .transcripts import MAX_TRANSCRIPT_BYTES, parse_transcript

TERMINAL = {"SUCCESS", "FAILURE", "EXPIRED", "ABORTED", "CANCELLED", "SUSPENDED"}
TRANSFER_WORKERS = 4
MONITOR_BACKOFF = (2, 5, 10)
LOG_POLL_SECONDS = 15
LOG_READ_SECONDS = 3
LOG_BYTES = 64 * 1024
LOG_JSON_BYTES = 16 * 1024
LOG_MEMBER_LIMIT = 64
RECONNECTING = "Temporary UCloud connection problem · reconnecting to the same allocation"
MONITOR_INTERRUPTED = (
    "UCloud monitoring time limit reached. The recorded allocation may "
    "still be running; reconnect this batch to resume checking the same UCloud job."
)
CONTINUED_RUNTIME_NOTICE = (
    "Other work in a shared CBS Runtime batch continues; "
    "stop it from its own Activity card if needed."
)
# Whisper language tokens from the pinned faster-whisper tokenizer; no inference import required.
OUTPUT_LANGUAGES = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha "
    "haw he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr "
    "ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg "
    "th tk tl tr tt uk ur uz vi yi yo zh yue".split()
)


class MonitoringInterrupted(ValueError):
    """The bounded monitor stopped; this does not establish an ASR or allocation failure."""


def _poll_job(client, identifier, worker, guard, reconnecting, deadline):
    """Retry only this read, within the existing monitor budget and with cancellable waits."""
    attempt = 0
    while True:
        guard()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MonitoringInterrupted(MONITOR_INTERRUPTED)
        try:
            return client.retrieve_job(identifier, request_timeout=min(30, remaining))
        except UCloudAPIError as exc:
            if exc.status_code not in {502, 503, 504}:
                raise ValueError(
                    f"UCloud request failed (HTTP {exc.status_code}). Check token, project, "
                    "template permissions and provider availability."
                ) from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            pass
        guard()
        reconnecting()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MonitoringInterrupted(MONITOR_INTERRUPTED)
        delay = MONITOR_BACKOFF[attempt] if attempt < len(MONITOR_BACKOFF) else 30
        attempt += 1
        worker.cancel_event.wait(min(delay, remaining))
        guard()


def _log_timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}", value
    ):
        raise ValueError("Unsupported log timestamp")
    datetime.strptime(value, "%Y-%m-%d %H:%M:%S,%f")
    return value


def _log_observation(value, stage):
    """Filter the observational journal before projecting any of it into Activity."""
    if not isinstance(value, dict) or value.get("stage") not in (
        stage if isinstance(stage, tuple) else (stage,)
    ):
        return None
    try:
        reported = _log_timestamp(value.get("reported_at"))
        observed = value["observed_at"]
        if not isinstance(observed, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", observed
        ):
            return None
        datetime.fromisoformat(observed)
        digest = value["event_sha256"]
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return {
        "stage": value["stage"],
        "reported_at": reported,
        "observed_at": observed,
        "event_sha256": digest,
    }


def _log_progress(run, members):
    value = run.get("log_progress")
    if not isinstance(value, dict):
        return {"files": {}, "batch": None}
    known = {member["job_id"] for member in members}
    files = value.get("files", {})
    checked = {}
    if isinstance(files, dict) and len(files) <= LOG_MEMBER_LIMIT:
        for identifier, observation in files.items():
            clean = _log_observation(observation, ("started", "processed_reported"))
            if identifier in known and clean is not None:
                checked[identifier] = clean
    return {"files": checked, "batch": _log_observation(value.get("batch"), "model_load_reported")}


def _log_note(observation):
    if observation is None:
        return ""
    message = {
        "started": "Log reported: processing started",
        "processed_reported": "Log reported: processing finished",
        "model_load_reported": "Last log: model loading reported",
    }[observation["stage"]]
    if observation["reported_at"] is not None:
        return (
            message + " · log timestamp " + observation["reported_at"] + " (timezone unspecified)"
        )
    return message + " · observed at " + observation["observed_at"]


def _parse_log(data, members, model):
    """Recognize events, never infer per-file execution order, percentages or completion."""
    known = {member["filename"]: member["job_id"] for member in members}
    files, batch = {}, None
    for line in data.decode("utf-8").splitlines()[-512:]:
        if not line or len(line) > 2048:
            continue
        prefix = re.fullmatch(r"(?:(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - )?(.*)", line)
        if prefix is None:
            continue
        try:
            reported = _log_timestamp(prefix[1])
        except ValueError:
            continue
        text = prefix[2]
        started = re.fullmatch(
            r"Starting ([a-f0-9]{32}\.[A-Za-z0-9]{1,10}) duration: "
            r"[0-9]{1,9}(?:\.[0-9]{1,6})? seconds on device: "
            r"(?:cuda(?::[0-9]{1,2})?|cpu) model: ([A-Za-z0-9][A-Za-z0-9._/-]{0,199})",
            text,
        )
        loading = re.fullmatch(
            r"Loading ([A-Za-z0-9][A-Za-z0-9._/-]{0,199}) (?:to|took) "
            r"[0-9]{1,3}:[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?",
            text,
        )
        processed = re.fullmatch(
            r"Processed ([a-f0-9]{32}\.[A-Za-z0-9]{1,10}) it took "
            r"[0-9]{1,3}:[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?",
            text,
        )
        observation = {
            "reported_at": reported,
            "event_sha256": hashlib.sha256(line.encode()).hexdigest(),
        }
        if (started is not None and started[1] in known and started[2] == model) or (
            processed is not None and processed[1] in known
        ):
            identifier = known[started[1] if started is not None else processed[1]]
            files.pop(identifier, None)
            files[identifier] = {
                "stage": "started" if started is not None else "processed_reported",
                **observation,
            }
            if len(files) > LOG_MEMBER_LIMIT:
                files.pop(next(iter(files)))
        elif loading is not None and loading[1] == model:
            # Worker threads may interleave: this line has no file identity.
            batch = {"stage": "model_load_reported", **observation}
    return {"files": files, "batch": batch}


def _read_log(settings, key, folder, guard):
    """Bound log I/O independently of mandatory guard work; OS/DNS remain soft limits."""
    deadline = time.monotonic() + LOG_READ_SECONDS
    guard_error = None

    def remaining():
        nonlocal deadline, guard_error
        checking = time.monotonic()
        try:
            guard()
        except Exception as exc:
            guard_error = exc
            raise
        # A batch guard freshly validates every member. Its local DB/CPU work is not
        # network time; charging it repeatedly can exhaust the budget before any log arrives.
        deadline += time.monotonic() - checking
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ValueError("Log read budget exhausted")
        return min(LOG_READ_SECONDS, seconds)

    def body(response, limit):
        if (
            not response.is_success
            or response.headers.get("content-encoding", "identity") != "identity"
        ):
            raise ValueError("Log response unavailable")
        data = bytearray()
        chunks = iter(response.iter_raw())  # No aggregation that could hide slow trickles.
        while True:
            remaining()
            try:
                block = next(chunks)
            except StopIteration:
                break
            remaining()
            if len(block) > limit - len(data):
                raise ValueError("Log response exceeds its budget")
            data.extend(block)
        return bytes(data)

    def control_json(client, method, path, **kwargs):
        with client.stream(method, path, timeout=remaining(), **kwargs) as response:
            value = json.loads(body(response, LOG_JSON_BYTES))
        if not isinstance(value, dict):
            raise ValueError("Unsupported log metadata")
        return value

    path = folder + "/out/transcribe.log"
    try:
        # The released SDK buffers request bodies; mirror only its exact wire headers here.
        with httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            headers={
                "Authorization": "Bearer " + key,
                "Project": settings.ucloud_transcriber.project_id,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=LOG_READ_SECONDS,
            follow_redirects=False,
        ) as control:
            metadata = control_json(control, "GET", "/api/files/retrieve", params={"id": path})
            status = metadata.get("status", {})
            size = status.get("sizeInBytes") if isinstance(status, dict) else None
            if (
                metadata.get("id") != path
                or not isinstance(status, dict)
                or status.get("type") != "FILE"
                or type(size) is not int
                or not 0 < size <= 1024**4
            ):
                return None
            start = max(0, size - LOG_BYTES)
            session = native.one_response(
                control_json(control, "POST", "/api/files/download", json={"items": [{"id": path}]})
            )
            if not isinstance(session.get("endpoint"), str):
                return None
            endpoint = native.transfer_url(settings.base_url, session["endpoint"])
            with httpx.Client(timeout=LOG_READ_SECONDS, follow_redirects=False) as transfer:
                with transfer.stream(
                    "GET",
                    endpoint,
                    headers={"Range": f"bytes={start}-{size - 1}", "Accept-Encoding": "identity"},
                    timeout=remaining(),
                ) as response:
                    if response.status_code == 206:
                        if (
                            response.headers.get("content-range")
                            != f"bytes {start}-{size - 1}/{size}"
                        ):
                            return None  # Growing, replaced or malformed range response.
                    elif response.status_code != 200 or start != 0:
                        return None  # Do not consume an ignored large tail request.
                    data = body(response, size - start)
            if len(data) != size - start:
                return None
            if start:
                data = data.split(b"\n", 1)[1] if b"\n" in data else b""
            # Neither a cut first line nor an unfinished final line grants an observation.
            if not data.endswith(b"\n"):
                data = data.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in data else b""
            return data
    except Cancelled:
        raise
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        RecursionError,
        httpx.HTTPError,
        httpx.InvalidURL,
        httpx.StreamError,
    ):
        if guard_error is not None:
            raise guard_error from None
        return None


def _merge_log_progress(previous, observed):
    def newer(current, incoming):
        if current is None:
            return True
        if current["event_sha256"] == incoming["event_sha256"]:
            return False
        # Do not replace a timestamped event with an older/undated rotated observation.
        return current["reported_at"] is None or (
            incoming["reported_at"] is not None
            and incoming["reported_at"] >= current["reported_at"]
        )

    merged = {"files": dict(previous["files"]), "batch": previous["batch"]}
    for identifier, observation in observed["files"].items():
        if not newer(merged["files"].get(identifier), observation):
            continue
        merged["files"].pop(identifier, None)
        merged["files"][identifier] = {**observation, "observed_at": now()}
        if len(merged["files"]) > LOG_MEMBER_LIMIT:
            merged["files"].pop(next(iter(merged["files"])))
    if observed["batch"] is not None and newer(merged["batch"], observed["batch"]):
        merged["batch"] = {**observed["batch"], "observed_at": now()}
    return merged


def uncertain(run):
    return bool(
        not run.get("job_id")
        and (run.get("submission_attempted") or run.get("submission_intent"))
        and not run.get("reconciled_not_created")
    )


def unresolved(batch):
    run = batch["params"].get("ucloud_run", {})
    return bool(
        batch["state"] in {"queued", "running"}
        or uncertain(run)
        or run.get("cleanup_state") == "pending"
        or (
            run.get("job_id")
            and batch["state"] != "completed"
            and run.get("remote_state") not in TERMINAL
            and not run.get("termination_requested")
            and not run.get("cancellation_requested")
        )
    )


def _deleted(db, batch):
    """Members released by an explicit Delete whose job rows are gone (ADR 0101)."""
    listed = batch["params"].get("ucloud_run", {}).get("deleted_members") or []
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise ValueError("The UCloud batch's deleted-member receipt is malformed.")
    listed = set(listed)
    return {
        member["job_id"]
        for member in batch["members"]
        if member["job_id"] in listed
        and db.execute("SELECT id FROM jobs WHERE id=?", (member["job_id"],)).fetchone() is None
    }


def deleted_members(store, batch):
    with store.connection() as db:
        return _deleted(db, batch)


def _surviving_members(store, batch):
    deleted = deleted_members(store, batch)
    return [member for member in batch["members"] if member["job_id"] not in deleted]


def _deleted_list(run, job_ids):
    listed = run.get("deleted_members") or []
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise ValueError("The UCloud batch's deleted-member receipt is malformed.")
    return sorted(set(listed) | set(job_ids))


def folder_for(batch):
    settings = providers.Settings.model_validate(batch["params"]["settings"])
    return settings.ucloud_transcriber.storage_path + "/cbs-batch-" + batch["id"]


def confirm_release(store, identifier, *, target, deleted_job_ids):
    """Record Delete's consent for the exact owned folder before any remote request."""
    from . import restored_owners

    restored_owners.require_owner(store, "native_batch", identifier)
    batch = store.native_batch(identifier)
    if batch["state"] in {"queued", "running"}:
        raise ValueError("Stop the UCloud batch before releasing its folder.")
    if not set(deleted_job_ids) <= {member["job_id"] for member in batch["members"]}:
        raise ValueError("The deleted files do not belong to this UCloud batch.")
    run = batch["params"].get("ucloud_run", {})
    update = {
        "release_decision": run.get("release_decision")
        or {"kind": "deletion", "decided_at": now(), "target": target},
        "deleted_members": _deleted_list(run, deleted_job_ids),
        "cancel_requested": True,
    }
    if run.get("cleanup_state") not in native.CLEANED and (
        run.get("owned_folder") or run.get("root_created")
    ):
        update.update(cleanup_state="pending", cleanup_stage="release_confirmed")
    return store.update_native_batch(identifier, run_update=update)


def record_deleted_members(store, identifier, job_ids):
    """Delete of some files only: unsaved work of the other files keeps the allocation."""
    from . import restored_owners

    restored_owners.require_owner(store, "native_batch", identifier)
    batch = store.native_batch(identifier)
    if not set(job_ids) <= {member["job_id"] for member in batch["members"]}:
        raise ValueError("The deleted files do not belong to this UCloud batch.")
    run = batch["params"].get("ucloud_run", {})
    return store.update_native_batch(
        identifier, run_update={"deleted_members": _deleted_list(run, job_ids)}
    )


def release_info(batch):
    """Activity projection of a Delete-consented release; no credentials or text bodies."""
    run = batch["params"].get("ucloud_run", {})
    decision = run.get("release_decision")
    if not isinstance(decision, dict):
        return None
    settings = providers.Settings.model_validate(batch["params"]["settings"])
    target = decision.get("target") if isinstance(decision.get("target"), dict) else {}
    return {
        "owner_kind": "native_batch",
        "id": batch["id"],
        "kind": "deletion",
        "target_kind": target.get("kind")
        if target.get("kind") in {"project", "recording"}
        else None,
        "target_name": str(target.get("name") or "")[:240],
        "folder": folder_for(batch),
        "server": settings.base_url,
        "cleanup_state": run.get("cleanup_state"),
        "submission_uncertain": uncertain(run),
        "waiting_for_stop": bool(run.get("job_id") and run.get("remote_state") not in TERMINAL),
        "can_retry": run.get("cleanup_state") not in native.CLEANED
        and not uncertain(run)
        and batch["state"] not in {"queued", "running"},
    }


def retained_activity(store):
    """Released allocations whose files were all deleted stay visible until Trash is confirmed."""
    from . import restored_owners

    cards = []
    rows = store.all(
        "SELECT id FROM native_batch_runs WHERE json_extract(CASE WHEN json_valid(params) "
        "THEN params ELSE '{}' END,'$.ucloud_run.release_decision') IS NOT NULL "
        "AND coalesce(json_extract(CASE WHEN json_valid(params) THEN params ELSE '{}' END,"
        "'$.ucloud_run.cleanup_state'),'') NOT IN ('trashed','deleted') "
        "ORDER BY updated DESC"
    )
    for row in rows:
        with store.connection() as db:
            if restored_owners.is_restored(db, "native_batch", row["id"]):
                continue
        batch = store.native_batch(row["id"])
        members = [member["job_id"] for member in batch["members"]]
        if not unresolved(batch) or store.all(
            "SELECT id FROM jobs WHERE id IN (" + ",".join("?" for _ in members) + ")", members
        ):
            continue  # Remaining files show this allocation through their own job rows.
        run = batch["params"].get("ucloud_run", {})
        release = release_info(batch)
        label = (
            "UCloud cleanup for a deleted recording"
            if release["target_kind"] == "recording"
            else "UCloud cleanup for a deleted project"
        )
        cards.append(
            {
                "id": "native-batch-" + batch["id"],
                "kind": "transcribe",
                "recording_id": None,
                "recording_name": label,
                "project_id": batch["project_id"],
                "provider_name": "UCloud Transcriber batch",
                "model": batch["params"].get("settings", {}).get("model"),
                "state": batch["state"],
                "progress": batch["progress"],
                "message": batch["message"],
                "created": batch["created"],
                "updated": batch["updated"],
                "ucloud_server": release["server"],
                "ucloud_job_id": run.get("job_id"),
                "ucloud_remote_state": run.get("remote_state"),
                "ucloud_submission_uncertain": uncertain(run),
                "ucloud_stop_requested": bool(run.get("termination_requested")),
                "ucloud_cancel_requested": bool(run.get("cancel_requested")),
                "ucloud_cleanup_state": run.get("cleanup_state"),
                "ucloud_cleanup_error": run.get("cleanup_error"),
                "ucloud_cleanup_stage": run.get("cleanup_stage"),
                "ucloud_cleanup_error_code": run.get("cleanup_error_code"),
                "ucloud_cleanup_http_status": run.get("cleanup_http_status"),
                "ucloud_cleanup_attempted_at": run.get("cleanup_attempted_at"),
                "ucloud_batch_unresolved": True,
                "remote_release": release,
            }
        )
    return cards


def batch_for_job(store, job, *, connection=None):
    identifier = job["params"].get("native_batch_id")
    if not identifier:
        return None
    batch = (
        store.native_batch(identifier)
        if connection is None
        else store._native_batch(
            connection.execute(
                "SELECT * FROM native_batch_runs WHERE id=?", (identifier,)
            ).fetchone()
        )
    )
    if not any(
        m["job_id"] == job["id"] and m["recording_id"] == job["recording_id"]
        for m in batch["members"]
    ):
        raise ValueError("This job does not belong to the recorded UCloud batch.")
    for key in ("settings", "provider_id"):
        if job["params"].get(key) != batch["params"].get(key):
            raise ValueError("The UCloud batch's frozen provider identity disagrees.")
    if ("native_allocation" in job["params"]) != ("native_allocation" in batch["params"]) or job[
        "params"
    ].get("native_allocation") != batch["params"].get("native_allocation"):
        raise ValueError("The UCloud batch's frozen provider identity disagrees.")
    native_capture.validate_allocation(job["params"])
    return batch


def status_fields(store, job, *, connection=None):
    batch = batch_for_job(store, job, connection=connection)
    if batch is None:
        return {}
    run = batch["params"].get("ucloud_run", {})
    continued = run.get("continued_runtime_batches", [])
    message = batch["message"]
    if continued and CONTINUED_RUNTIME_NOTICE not in message:
        message = (message + " " + CONTINUED_RUNTIME_NOTICE).strip()
    identifiers = [member["job_id"] for member in batch["members"]]
    query = (
        "SELECT j.id AS job_id,j.recording_id,j.state,j.progress,j.message, "
        "r.name AS recording_name, "
        "(SELECT t.id FROM transcript_results t WHERE t.job_id=j.id "
        "AND t.id NOT IN (SELECT result_id FROM result_trash)) AS result_id "
        "FROM jobs j JOIN recordings r ON r.id=j.recording_id WHERE j.id IN ("
        + ",".join("?" for _ in identifiers)
        + ")"
    )
    member_rows = (
        store.all(query, identifiers)
        if connection is None
        else [dict(row) for row in connection.execute(query, identifiers).fetchall()]
    )
    members = {row["job_id"]: row for row in member_rows}
    read_errors = _output_errors(run, batch["members"])
    log_progress = _log_progress(run, batch["members"])
    if batch["state"] != "completed" and (note := _log_note(log_progress["batch"])):
        message = (message + ". " + note).strip(". ")
    for member in members.values():
        if member["state"] == "failed" and member["result_id"] is None:
            error = _output_error_message(read_errors.get(member["job_id"]))
            if error:
                member["message"] = error
        note = _log_note(log_progress["files"].get(member["job_id"]))
        if member["state"] != "completed" and note:
            member["message"] = (member["message"] + ". " + note).strip(". ")
    return {
        "ucloud_batch_id": batch["id"],
        "ucloud_batch_count": len(batch["members"]),
        "ucloud_batch_state": batch["state"],
        "ucloud_batch_progress": batch["progress"],
        "ucloud_batch_message": message,
        "ucloud_batch_created": batch["created"],
        "ucloud_batch_updated": batch["updated"],
        "ucloud_batch_unresolved": unresolved(batch),
        "ucloud_batch_continued_runtime_batches": continued,
        "ucloud_batch_members": [
            members.get(
                member["job_id"],
                {
                    "job_id": member["job_id"],
                    "recording_id": member["recording_id"],
                    "recording_name": "Removed recording",
                    "state": "removed",
                    "progress": 0,
                    "message": "This recording is no longer in the library.",
                    "result_id": None,
                },
            )
            for member in batch["members"]
        ],
        "ucloud_job_id": run.get("job_id"),
        "ucloud_remote_state": run.get("remote_state"),
        "ucloud_cleanup_state": run.get("cleanup_state"),
        "ucloud_cleanup_error": run.get("cleanup_error"),
        "ucloud_cleanup_stage": run.get("cleanup_stage"),
        "ucloud_cleanup_error_code": run.get("cleanup_error_code"),
        "ucloud_cleanup_http_status": run.get("cleanup_http_status"),
        "ucloud_cleanup_attempted_at": run.get("cleanup_attempted_at"),
        "ucloud_submission_uncertain": uncertain(run),
        "ucloud_stop_requested": bool(run.get("termination_requested")),
        "ucloud_cancel_requested": bool(run.get("cancellation_requested")),
        "remote_release": release_info(batch),
    }


def retry_batch(queue, job, replace=False):
    """A retry reuses the entire frozen allocation and never repeats successful imports."""
    from . import restored_owners

    restored_owners.require_job(queue.store, job["id"])
    with queue.lock:
        batch = batch_for_job(queue.store, job)
        if batch is None or batch["state"] not in {"failed", "cancelled", "interrupted"}:
            raise ValueError("This UCloud batch cannot be retried while work is active.")
        if batch["params"].get("ucloud_run", {}).get("release_decision"):
            raise ValueError(
                "This UCloud batch's folder was released when its recordings were deleted. "
                "Start a new run instead."
            )
        settings = providers.Settings.model_validate(batch["params"]["settings"])
        members = _surviving_members(queue.store, batch)
        own_ids = {m["job_id"] for m in members}
        pending = []
        for member in members:
            child = queue.store.job(member["job_id"])
            batch_for_job(queue.store, child)
            if child["state"] == "completed":
                continue
            recording = queue.store.recording(member["recording_id"])
            providers.require_approval(
                queue.store, recording["project_id"], child["params"].get("provider_id"), settings
            )
            from .input_admission import uses_original

            original = uses_original(child, recording)
            if recording["status"] != "ready" and not original:
                raise ValueError("Finish media preparation before retrying the UCloud batch.")
            if recording["segments"] and not replace:
                raise ValueError("Confirm adding results for this UCloud batch on retry.")
            others = queue.store.all(
                "SELECT id,kind FROM jobs WHERE recording_id=? AND state IN ('queued','running')",
                (recording["id"],),
            )
            if any(
                row["id"] not in own_ids
                and not (original and row["kind"] in {"media", "source-inspect"})
                for row in others
            ):
                raise ValueError("Wait for this recording's active work before retrying the batch.")
            pending.append(child)
        if not pending:
            raise ValueError("All batch results are saved. Retry remote cleanup separately.")
        # Cancellation requests apply to the previous attempt; ownership/POST receipts stay intact.
        queue.store.update_native_batch(batch["id"], run_update={"cancel_requested": False})
        queue.store.finish_native_batch(batch["id"], "queued", "Queued to resume UCloud batch")
    queue.notify()
    return {"ok": True, "ucloud_batch_id": batch["id"], "affected_jobs": sorted(own_ids)}


def cancel_batch(queue, job):
    """A native allocation can only be stopped as a whole, with explicit shared scope."""
    from . import restored_owners

    restored_owners.require_job(queue.store, job["id"])
    store = queue.store
    batch = batch_for_job(store, job)
    if batch is None:
        raise ValueError("This job has no shared UCloud batch.")
    members = _surviving_members(store, batch)
    identifiers = {m["job_id"] for m in members}
    owner = next(
        (
            worker
            for worker in queue.workers
            if worker.active_id in identifiers
            or getattr(worker, "active_batch_id", None) == batch["id"]
        ),
        None,
    )
    if owner is not None:
        owner.cancel_event.set()
        store.update_native_batch(
            batch["id"], run_update={"cancel_requested": True}, message="Stopping UCloud batch…"
        )
    elif batch["state"] != "completed" or unresolved(batch):
        settings = providers.Settings.model_validate(batch["params"]["settings"])
        run = batch["params"].get("ucloud_run", {})
        if run.get("job_id") and run.get("remote_state") not in TERMINAL:
            native.terminate_owned(settings, queue.credentials, run["job_id"])
        receipt = {"cancel_requested": True}
        if run.get("job_id"):
            receipt["termination_requested"] = True
        # Partial/abandoned output is retained; success-only cleanup must not delete it.
        if batch["state"] != "completed" and run.get("cleanup_state") == "pending":
            receipt["cleanup_state"] = "retained"
        store.update_native_batch(batch["id"], run_update=receipt)
        store.finish_native_batch(
            batch["id"], "cancelled", "UCloud batch stopped; saved results kept"
        )
    from . import runtime_store

    followups = {}
    for member in members:
        parent = store.job(member["job_id"])
        followup = parent["params"].get("followup")
        if followup:
            followups[followup["id"]] = (parent["id"], parent["recording_id"])
    continued, handled = set(), set()
    for identifier, (parent_id, recording_id) in followups.items():
        if not store.all(
            "SELECT id FROM jobs WHERE id=? AND state IN ('queued','running')", (identifier,)
        ):
            continue
        child = store.job(identifier)
        runtime = runtime_store.for_job(store, child)
        if runtime:
            if (
                child["recording_id"] != recording_id
                or child["params"].get("parent_transcription_id") != parent_id
            ):
                raise ValueError("The runtime follow-up belongs to another producing job.")
            if runtime["id"] in handled:
                continue
            handled.add(runtime["id"])
            if any(member["job_id"] not in followups for member in runtime["members"]):
                continued.add(runtime["id"])
                continue
            try:
                # The transaction rechecks containment before Stop closes late admission.
                runtime_store.stop(store, runtime["id"], allowed_job_ids=set(followups))
            except runtime_store.StopScopeChanged:
                continued.add(runtime["id"])
                continue
        queue.cancel(identifier)
    if continued:
        message = store.native_batch(batch["id"])["message"]
        if CONTINUED_RUNTIME_NOTICE not in message:
            message = (message + " " + CONTINUED_RUNTIME_NOTICE).strip()
        store.update_native_batch(
            batch["id"],
            run_update={"continued_runtime_batches": sorted(continued)},
            message=message,
        )
    return {
        "ok": True,
        "ucloud_batch_id": batch["id"],
        "affected_jobs": sorted(identifiers),
        "continued_runtime_batches": sorted(continued),
        **({"message": CONTINUED_RUNTIME_NOTICE} if continued else {}),
    }


class OutputReadError(ValueError):
    """Curated output-read failure, without provider response text or transfer URLs."""

    def __init__(self, stage, http_status, attempts):
        self.http_status = http_status
        self.details = {"stage": stage, "http_status": http_status, "attempts": attempts}
        super().__init__(_output_error_message(self.details))


def _output_error_message(value):
    stages = {"metadata": "output listing", "session": "download session", "download": "download"}
    if (
        not isinstance(value, dict)
        or set(value) != {"stage", "http_status", "attempts"}
        or not isinstance(value.get("stage"), str)
        or value["stage"] not in stages
        or type(value.get("attempts")) is not int
        or not 1 <= value["attempts"] <= 3
        or (
            value.get("http_status") is not None
            and (type(value["http_status"]) is not int or not 100 <= value["http_status"] <= 599)
        )
    ):
        return ""
    cause = (
        f"HTTP {value['http_status']}" if value["http_status"] is not None else "connection error"
    )
    return (
        f"Transcript {stages[value['stage']]} failed after {value['attempts']} attempt(s) "
        f"({cause}). "
        "Choose Retry failed to retrieve this output from the same UCloud job. "
        "Saved results are kept."
    )


def _output_errors(run, members):
    value = run.get("output_read_errors")
    if not isinstance(value, dict) or len(value) > LOG_MEMBER_LIMIT:
        return {}
    known = {member["job_id"] for member in members}
    return {
        identifier: dict(error)
        for identifier, error in value.items()
        if identifier in known and _output_error_message(error)
    }


def _read_output(read, stage, *, guard=None, wait=None):
    """At most three attempts of one read; never replay submission or another mutation."""
    guard_error = None

    def check():
        nonlocal guard_error
        try:
            if guard is not None:
                guard()
        except Exception as exc:
            guard_error = exc
            raise

    for attempt in range(3):
        check()
        try:
            return read(check)
        except (UCloudAPIError, httpx.HTTPError, httpx.StreamError) as exc:
            if guard_error is not None:
                raise guard_error from None
            status = (
                exc.status_code
                if isinstance(exc, UCloudAPIError)
                else exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            if type(status) is not int or not 100 <= status <= 599:
                status = None
            transient = status in {502, 503, 504} or isinstance(
                exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
            )
            check()
            if not transient or attempt == 2:
                raise OutputReadError(stage, status, attempt + 1) from None
            (wait or time.sleep)(MONITOR_BACKOFF[attempt])
            check()


def _output_paths(client, folder, *, guard=None, wait=None):
    paths, seen, token = set(), set(), None
    for _ in range(50):
        page = _read_output(
            lambda check, page_token=token: client.browse_files(
                folder, items_per_page=100, next_page=page_token
            ),
            "metadata",
            guard=guard,
            wait=wait,
        )
        items = page.get("items")
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError("UCloud returned malformed batch output metadata.")
        for item in items:
            path = item.get("id") if isinstance(item, dict) else None
            if not isinstance(path, str) or len(path) > 4000 or path in paths:
                raise ValueError("UCloud returned ambiguous batch output metadata.")
            if PurePosixPath(path).parent != PurePosixPath(folder):
                raise ValueError("A UCloud batch output lies outside its owned output folder.")
            paths.add(path)
        token = page.get("next")
        if token is None or token == "":
            return paths
        if not isinstance(token, str) or len(token) > 4000 or token in seen:
            raise ValueError("UCloud returned invalid output pagination.")
        seen.add(token)
    raise ValueError("UCloud batch output listing exceeds the supported size.")


def _download(
    client,
    server,
    paths,
    folder,
    filename,
    model="",
    language="",
    *,
    with_name=False,
    guard=None,
    published=False,
    wait=None,
):
    legacy_names = {PurePosixPath(filename).stem + ".json", filename + ".json"}
    names = set(legacy_names)
    if model == "parakeet":
        # Transcriber 1.12 Parakeet omits the language suffix; use its frozen API token.
        names.add(PurePosixPath(filename).stem + "_parakeet.json")
    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", model):
        # Whisper Batch 1.12 includes the actual model and detected language in each output name.
        # An explicit language matches only itself; auto permits known Whisper tokens only.
        languages = {language} if language else OUTPUT_LANGUAGES
        names.update(
            f"{PurePosixPath(filename).stem}_{model}_{code}.json"
            for code in languages
            if code in OUTPUT_LANGUAGES
        )
    candidates = [path for path in paths if PurePosixPath(path).name in names]
    if len(candidates) != 1 or (published and PurePosixPath(candidates[0]).name in legacy_names):
        raise ValueError("This batch input has no unambiguous original JSON transcript.")
    if PurePosixPath(candidates[0]).parent != PurePosixPath(folder):
        raise ValueError("The batch transcript lies outside its owned output folder.")
    session = native.one_response(
        _read_output(
            lambda check: client.request(
                "POST", "/api/files/download", json={"items": [{"id": candidates[0]}]}
            ).json(),
            "session",
            guard=guard,
            wait=wait,
        )
    )
    endpoint = native.transfer_url(server, session["endpoint"])

    def transfer(check):
        with httpx.Client(timeout=120, follow_redirects=False) as transport:
            with transport.stream("GET", endpoint) as response:
                if not response.is_success:
                    raise UCloudAPIError(
                        "Transcript download refused", status_code=response.status_code
                    )
                data = bytearray()
                for block in response.iter_bytes():
                    check()
                    data.extend(block)
                    if len(data) > MAX_TRANSCRIPT_BYTES:
                        raise ValueError("UCloud transcript exceeds the supported import size.")
        return bytes(data)

    data = _read_output(transfer, "download", guard=guard, wait=wait)
    return (data, PurePosixPath(candidates[0]).name) if with_name else data


def _transfers(members, transfer, receive, guard):
    """Bound accepted work and drain it before returning; only this thread receives results."""
    remaining, pending, error = iter(members), {}, None
    with ThreadPoolExecutor(max_workers=TRANSFER_WORKERS) as pool:

        def fill():
            while len(pending) < TRANSFER_WORKERS:
                guard()
                member = next(remaining, None)
                if member is None:
                    break
                pending[pool.submit(transfer, member)] = member

        try:
            fill()
        except Exception as exc:
            error = exc
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            while done:
                future = done.pop()
                member = pending.pop(future)
                try:
                    receive(member, future)
                except Exception as exc:
                    if error is None or isinstance(exc, Cancelled):
                        error = exc
                # Release completed byte buffers before admitting replacements.
                del future
            if error is None:
                try:
                    fill()
                except Exception as exc:
                    error = exc
    if error is not None:
        raise error


def _raw_path(store, member):
    return store.directory(member["recording_id"]) / ("ucloud-" + member["job_id"] + ".json")


def _sync_raw(path):
    with path.open("r+b") as saved:
        os.fsync(saved.fileno())
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def cleanup_batch(store, credentials, identifier):
    from . import restored_owners

    restored_owners.require_owner(store, "native_batch", identifier)
    batch = store.native_batch(identifier)
    params, run = batch["params"], batch["params"].get("ucloud_run", {})
    if run.get("cleanup_state") in native.CLEANED:
        return run["cleanup_state"]
    stage = "preconditions"
    attempted = {"cleanup_attempted_at": now()}
    try:
        settings = providers.Settings.model_validate(params["settings"])
        folder = settings.ucloud_transcriber.storage_path + "/cbs-batch-" + identifier
        if (
            not settings.ucloud_transcriber.cleanup_after_success
            or run.get("remote_state") != "SUCCESS"
            or not run.get("job_id")
            or run.get("owned_folder") != folder
            or not run.get("folders_created")
        ):
            raise native.CleanupError(
                "Batch cleanup requires a successful run and its exact owned folder.",
                stage,
                "ownership_or_success_missing",
            )
        stage = "local_validation"
        paths = []
        deleted = deleted_members(store, batch)
        for member in batch["members"]:
            if member["job_id"] in deleted:
                continue  # Its recording was deleted with explicit consent (ADR 0101).
            path = _raw_path(store, member)
            if not store.all(
                "SELECT id FROM transcript_results WHERE job_id=?", (member["job_id"],)
            ):
                raise native.CleanupError(
                    "Save every batch transcript before remote cleanup.", stage, "result_missing"
                )
            if not path.is_file():
                raise native.CleanupError(
                    "A saved local response is missing. Shared remote files were not moved.",
                    stage,
                    "local_response_missing",
                )
            if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
                raise native.CleanupError(
                    "A saved local response exceeds the import limit. Remote files were not moved.",
                    stage,
                    "local_response_invalid",
                )
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != run.get("raw_hashes", {}).get(member["job_id"]):
                raise native.CleanupError(
                    "A local response failed its integrity check. Remote files were not moved.",
                    stage,
                    "local_response_hash_mismatch",
                )
            try:
                parse_transcript(data, "transcript.json")
            except ValueError:
                raise native.CleanupError(
                    "A saved local response is invalid. Shared remote files were not moved.",
                    stage,
                    "local_response_invalid",
                ) from None
            paths.append(path)
        store.update_native_batch(identifier, run_update={**attempted, "cleanup_state": "pending"})
        stage = "local_flush"
        for path in paths:
            _sync_raw(path)
        stage = "setup"
        key = credentials.get(providers.credential_scope(settings))
        native.require_setup(settings, key)
        with native.client_for(settings, key) as client:
            stage = "trash"
            native.trash_owned_folder(client, folder)
    except (OSError, ValueError, UCloudAPIError, httpx.HTTPError) as exc:
        store.update_native_batch(
            identifier,
            run_update={**attempted, **native.cleanup_diagnostic(exc, stage)},
        )
        if stage in {"preconditions", "local_validation"}:
            raise
        return "pending"
    store.update_native_batch(
        identifier,
        run_update={
            **attempted,
            "cleanup_state": "trashed",
            "cleanup_stage": "complete",
            "cleanup_error": None,
            "cleanup_error_code": None,
            "cleanup_http_status": None,
        },
    )
    return "trashed"


def run_batch(worker, job):
    """Claim once, reconnect once, then commit each member's result independently."""
    from . import restored_owners

    restored_owners.require_job(worker.store, job["id"])
    store = worker.store
    batch = batch_for_job(store, job)
    members = _surviving_members(store, batch)
    identifier = batch["id"]
    if getattr(worker, "active_batch_id", None) != identifier:
        if not store.claim_native_batch(identifier):
            return
    params = batch["params"]
    settings = providers.Settings.model_validate(params["settings"])
    from . import native_allocation

    execution = providers.Settings.model_validate(
        native_allocation.effective(params["settings"], params)
    )
    key = worker.credentials.get(providers.credential_scope(settings))
    run = params.setdefault("ucloud_run", {})
    folder = settings.ucloud_transcriber.storage_path + "/cbs-batch-" + identifier

    def checkpoint(**values):
        current = store.update_native_batch(identifier, run_update=values)
        run.clear()
        run.update(current["params"]["ucloud_run"])

    def guard():
        latest = store.native_batch(identifier)["params"]["ucloud_run"]
        if (
            worker.cancel_event.is_set()
            or worker.stopping.is_set()
            or latest.get("cancel_requested")
        ):
            raise Cancelled()
        for member in members:
            child = store.job(member["job_id"])
            batch_for_job(store, child)
            # A streamed block needs current authorization metadata, not transcript/Working bodies.
            recording = store.one(
                "SELECT id,project_id,status,filename FROM recordings WHERE id=?",
                (member["recording_id"],),
            )
            from .input_admission import uses_original

            if recording["project_id"] != batch["project_id"] or (
                recording["status"] != "ready" and not uses_original(child, recording)
            ):
                raise ValueError("A UCloud batch recording changed. Check the project and media.")
            providers.require_approval(
                store, recording["project_id"], child["params"].get("provider_id"), settings
            )

    def reconnecting():
        if store.native_batch(identifier)["message"] != RECONNECTING:
            store.update_native_batch(identifier, message=RECONNECTING)
        for member in members:
            child = store.job(member["job_id"])
            if child["state"] != "completed" and child["message"] != RECONNECTING:
                store.update_job(member["job_id"], message=RECONNECTING)

    try:
        native.require_setup(settings, key)
        with native.client_for(settings, key) as client:
            try:
                if not run.get("job_id"):
                    if uncertain(run):
                        raise ValueError(
                            "UCloud batch submission outcome is unknown. Reconcile this batch "
                            "in Activity; retry cannot allocate twice."
                        )
                    template, parameters = native.template_info(client, execution)
                    specification, input_field = native.build_directory_specification(
                        execution,
                        template,
                        parameters,
                        folder,
                        identifier,
                        run.get("directory_parameter"),
                    )
                    submitted = specification["parameters"]
                    setup = settings.ucloud_transcriber
                    checkpoint(
                        directory_parameter=input_field,
                        output_model=submitted[setup.model_parameter]["value"],
                        output_language=submitted.get(setup.language_parameter, {}).get(
                            "value", ""
                        ),
                        output_application={
                            field: specification["application"][field]
                            for field in ("name", "version")
                        },
                    )
                    native.verify_storage(
                        client,
                        settings.ucloud_transcriber.storage_path,
                        create=not settings.ucloud_transcriber.template_job_id,
                    )
                    guard()
                    if not run.get("root_created"):
                        try:
                            client.create_folder(folder)
                        except UCloudAPIError as exc:
                            if exc.status_code == 409:
                                raise ValueError(
                                    "The batch folder exists without an ownership receipt. "
                                    "No upload started."
                                ) from None
                            raise
                        checkpoint(root_created=True, owned_folder=folder)
                    if run.get("owned_folder") != folder:
                        raise ValueError("The UCloud batch folder ownership disagrees.")
                    if not run.get("folders_created"):
                        for subfolder in ("input", "out"):
                            try:
                                client.create_folder(folder + "/" + subfolder)
                            except UCloudAPIError as exc:
                                if exc.status_code != 409:
                                    raise
                        checkpoint(folders_created=True)
                    waiting = []
                    for member in members:
                        uploaded = member["job_id"] in run.get("uploaded", [])
                        store.update_job(
                            member["job_id"],
                            message="Uploaded · waiting for UCloud"
                            if uploaded
                            else "Waiting to upload",
                            progress=0.25 if uploaded else 0.05,
                        )
                        if not uploaded:
                            waiting.append(member)

                    def upload_member(member):
                        guard()
                        store.update_job(member["job_id"], message="Uploading original to UCloud…")
                        suffix = PurePosixPath(member["filename"]).suffix
                        original = store.directory(member["recording_id"]) / ("original" + suffix)
                        child = store.job(member["job_id"])
                        if "audio_input" in child["params"]:
                            from .audio_inputs import require_native, verify_original

                            require_native(child["params"]["audio_input"])
                            recording = store.recording(member["recording_id"])
                            original = verify_original(
                                store, recording, child["params"]["audio_input"]
                            )
                        # SDK control clients are not shared across transfer threads.
                        with native.client_for(settings, key) as transfer_client:
                            native.upload(
                                transfer_client,
                                settings.base_url,
                                original,
                                folder + "/input/" + member["filename"],
                                guard,
                                lambda job_id=member["job_id"], **fields: store.update_job(
                                    job_id, **fields
                                ),
                            )
                        if "audio_input" in child["params"]:
                            verify_original(store, recording, child["params"]["audio_input"])

                    def uploaded_member(member, future):
                        future.result()
                        # Keep a successful receipt even when a sibling failed or Stop arrived.
                        checkpoint(uploaded=[*run.get("uploaded", []), member["job_id"]])
                        store.update_job(
                            member["job_id"], message="Uploaded · waiting for UCloud", progress=0.25
                        )
                        store.update_native_batch(
                            identifier, progress=0.25 * len(run["uploaded"]) / len(members)
                        )

                    _transfers(waiting, upload_member, uploaded_member, guard)
                    guard()
                    # Each member records its own copy of the shared submission before the
                    # POST (ADR 0100); the batch row journals only the accepted projection hash.
                    submitted = native_capture.projection(
                        specification, parameters, native.capture_owned(settings, input_field)
                    )
                    journaled = None
                    for member in members:
                        child = store.job(member["job_id"])
                        if not native_capture.enabled(child):
                            continue
                        evidence = native_capture.begin_submission(
                            store,
                            child,
                            submitted=submitted,
                            remote_name=member["filename"],
                            selector={
                                "model": run["output_model"],
                                "language": run["output_language"],
                            },
                        )
                        journaled = native_capture.submission_sha256(evidence)
                    if journaled is not None:
                        checkpoint(artifact_submission_sha256=journaled)
                    for member in members:
                        if store.job(member["job_id"])["state"] != "completed":
                            store.update_job(
                                member["job_id"], message="Waiting for transcript", progress=0.3
                            )
                    checkpoint(submission_attempted=True, reconciled_not_created=False)
                    remote_id = extract_job_id(native.cloud_call(client.submit_job, specification))
                    checkpoint(job_id=remote_id)
                paths = set()
                output_model = run.get("output_model", settings.model)
                output_language = run.get("output_language", settings.language)

                def download_member(member, *, fresh=False, transfer_guard=guard):
                    transfer_guard()
                    raw = _raw_path(store, member)
                    if not fresh and raw.exists():
                        # A preserved file without an observation stays an explicit gap.
                        if raw.stat().st_size > MAX_TRANSCRIPT_BYTES:
                            raise ValueError("Saved UCloud response exceeds the import limit.")
                        data, name = raw.read_bytes(), None
                    else:
                        store.update_job(
                            member["job_id"], message="Downloading transcript…", progress=0.7
                        )
                        with native.client_for(settings, key) as transfer_client:
                            data, name = _download(
                                transfer_client,
                                settings.base_url,
                                paths,
                                folder + "/out",
                                member["filename"],
                                output_model,
                                output_language,
                                with_name=True,
                                guard=transfer_guard,
                                wait=worker.cancel_event.wait,
                                **({"published": True} if fresh else {}),
                            )
                    return data, name, parse_transcript(data, "transcript.json")

                def note_output_error(member, error):
                    errors = _output_errors(run, members)
                    if member["job_id"] in errors or len(errors) < LOG_MEMBER_LIMIT:
                        errors[member["job_id"]] = error.details
                        checkpoint(output_read_errors=errors)
                    store.update_job(member["job_id"], state="failed", message=str(error))

                def clear_output_error(member):
                    errors = _output_errors(run, members)
                    if member["job_id"] in errors:
                        del errors[member["job_id"]]
                        checkpoint(output_read_errors=errors)

                def save_download(member, future):
                    try:
                        data, name, segments = future.result()
                        child = store.job(member["job_id"])
                        raw = _raw_path(store, member)
                        capture = native_capture.enabled(child)
                        observed = None
                        store.update_job(
                            member["job_id"], message="Saving transcript…", progress=0.9
                        )
                        if name is not None and capture:
                            native_capture.write_output(raw, data)
                            observed = native_capture.observe_output(store, child, name)
                        elif name is not None:
                            pending = raw.with_suffix(".pending")
                            pending.write_bytes(data)
                            pending.replace(raw)
                        evidence = (
                            native_capture.record_output(store, child, observed, segments)
                            if capture
                            else None
                        )
                        hashes = {
                            **run.get("raw_hashes", {}),
                            member["job_id"]: hashlib.sha256(data).hexdigest(),
                        }
                        with worker.lock:
                            # Preserve accepted bytes/capture on Stop before considering an import.
                            checkpoint(
                                raw_hashes=hashes,
                                **(
                                    {"cleanup_state": "pending"}
                                    if settings.ucloud_transcriber.cleanup_after_success
                                    and run.get("cleanup_state") not in native.CLEANED
                                    else {}
                                ),
                            )
                            clear_output_error(member)
                            guard()
                            store.add_result(
                                member["recording_id"],
                                segments,
                                {},
                                child["params"]["provider_name"],
                                {
                                    "kind": "provider",
                                    "provider_id": child["params"].get("provider_id"),
                                    **settings.model_dump(),
                                    **(
                                        {"native_allocation": params["native_allocation"]}
                                        if "native_allocation" in params
                                        else {}
                                    ),
                                    "ucloud_job_id": run["job_id"],
                                    "ucloud_batch_id": identifier,
                                    "ucloud_input_filename": member["filename"],
                                },
                                job_id=member["job_id"],
                                native_artifacts=evidence,
                            )
                            store.update_job(
                                member["job_id"], state="completed", progress=1, message="Complete"
                            )
                    except OutputReadError as exc:
                        note_output_error(member, exc)
                        raise
                    except (ValueError, OSError, KeyError, json.JSONDecodeError):
                        clear_output_error(member)
                        store.update_job(
                            member["job_id"],
                            state="failed",
                            message="Batch output could not be imported. Retry this batch.",
                        )
                        raise

                stable_outputs = {}

                def import_published(observed):
                    """Fresh publication evidence only; historical log notes grant no authority.

                    The known publisher logs Processed after its output writers close. Require
                    its exact submitted application plus two fresh, identical, parseable named
                    outputs. Keep one pending child so terminal monitoring/recovery retains its
                    existing owner; early results never authorize allocation cleanup.
                    """
                    nonlocal paths
                    application = run.get("output_application")
                    if not (
                        isinstance(application, dict)
                        and (application.get("name"), application.get("version"))
                        == ("transcriber", "1.12")
                    ):
                        return
                    pending = [
                        member
                        for member in members
                        if store.job(member["job_id"])["state"] != "completed"
                    ]
                    eligible = [
                        member
                        for member in pending
                        if observed["files"].get(member["job_id"], {}).get("stage")
                        == "processed_reported"
                        and not _raw_path(store, member).exists()
                    ]
                    known = {member["job_id"] for member in eligible}
                    for job_id in set(stable_outputs) - known:
                        stable_outputs.pop(job_id)
                    if len(pending) < 2 or not eligible:
                        return
                    guard()
                    listing_guard_error = None

                    def checked_listing_guard():
                        nonlocal listing_guard_error
                        try:
                            guard()
                        except Exception as exc:
                            listing_guard_error = exc
                            raise

                    try:
                        paths = _output_paths(
                            client,
                            folder + "/out",
                            guard=checked_listing_guard,
                            wait=worker.cancel_event.wait,
                        )
                    except (ValueError, OSError, UCloudAPIError, httpx.HTTPError, RuntimeError):
                        if listing_guard_error is not None:
                            raise listing_guard_error from None
                        # Optional read failure can defer retrieval, never bypass a current guard.
                        guard()
                        stable_outputs.clear()
                        return
                    guard()

                    def read_published(member):
                        guard_error = None

                        def checked_guard():
                            nonlocal guard_error
                            try:
                                guard()
                            except Exception as exc:
                                guard_error = exc
                                raise

                        try:
                            # Previously accepted raw belongs to terminal recovery; a newer
                            # remote file must never replace it before capture validation.
                            if _raw_path(store, member).exists():
                                return None
                            return download_member(member, fresh=True, transfer_guard=checked_guard)
                        except (
                            ValueError,
                            OSError,
                            KeyError,
                            UCloudAPIError,
                            httpx.HTTPError,
                            RuntimeError,
                        ):
                            if guard_error is not None:
                                raise
                            guard()
                            return None

                    pending_count = len(pending)

                    def receive_published(member, future):
                        nonlocal pending_count
                        value = future.result()
                        job_id = member["job_id"]
                        if value is None:
                            stable_outputs.pop(job_id, None)
                            return
                        data, name, _ = value
                        fingerprint = (
                            observed["files"][job_id]["event_sha256"],
                            name,
                            hashlib.sha256(data).hexdigest(),
                        )
                        previous = stable_outputs.get(job_id)
                        stable_outputs[job_id] = fingerprint
                        if (
                            previous != fingerprint
                            or pending_count <= 1
                            or _raw_path(store, member).exists()
                        ):
                            return
                        # Reuse durable native capture, producer idempotency and the normal
                        # worker/Working fences. Only transfer/parse failures are soft above.
                        save_download(member, future)
                        pending_count -= 1
                        stable_outputs.pop(job_id, None)

                    _transfers(eligible, read_published, receive_published, guard)

                deadline = time.monotonic() + settings.ucloud_transcriber.hours * 3600 + 600
                next_log_check = 0
                while True:
                    guard()
                    remote = _poll_job(client, run["job_id"], worker, guard, reconnecting, deadline)
                    state = remote.get("status", {}).get("state")
                    if state != run.get("remote_state"):
                        checkpoint(remote_state=state)
                    if (
                        (state == "RUNNING" or state in TERMINAL)
                        and time.monotonic() >= next_log_check
                        and run.get("owned_folder") == folder
                        and run.get("folders_created")
                    ):
                        data = _read_log(settings, key, folder, guard)
                        next_log_check = time.monotonic() + LOG_POLL_SECONDS
                        observed = {"files": {}, "batch": None}
                        if data is not None:
                            try:
                                observed = _parse_log(
                                    data, members, run.get("output_model", settings.model)
                                )
                            except (ValueError, UnicodeError):
                                observed = {"files": {}, "batch": None}
                            previous = _log_progress(run, members)
                            merged = _merge_log_progress(previous, observed)
                            if merged != previous:
                                checkpoint(log_progress=merged)
                        if state == "RUNNING":
                            import_published(observed)
                    if state in TERMINAL:
                        message, progress = "Waiting to download transcript", 0.6
                    elif state == "RUNNING":
                        message, progress = "Processing on UCloud · shared batch", 0.4
                    else:
                        message, progress = "Waiting for UCloud allocation", 0.3
                    # Reconnect can inherit stale upload labels even when the state is unchanged.
                    for member in members:
                        child = store.job(member["job_id"])
                        if child["state"] != "completed" and (
                            child["message"] != message or child["progress"] != progress
                        ):
                            store.update_job(member["job_id"], message=message, progress=progress)
                    if state in TERMINAL:
                        store.update_native_batch(
                            identifier, progress=0.6, message="Downloading UCloud transcripts"
                        )
                        break
                    if time.monotonic() >= deadline:
                        raise MonitoringInterrupted(MONITOR_INTERRUPTED)
                    store.update_native_batch(
                        identifier,
                        progress=0.4,
                        message="UCloud batch · " + str(state or "waiting"),
                    )
                    worker.cancel_event.wait(5)
                guard()
                try:
                    paths = _output_paths(
                        client, folder + "/out", guard=guard, wait=worker.cancel_event.wait
                    )
                except OutputReadError as exc:
                    for member in members:
                        if store.job(member["job_id"])["state"] != "completed":
                            note_output_error(member, exc)
                    raise
                waiting = []
                for member in members:
                    if store.job(member["job_id"])["state"] == "completed":
                        continue
                    store.update_job(
                        member["job_id"], message="Waiting to download transcript", progress=0.6
                    )
                    waiting.append(member)
                try:
                    _transfers(waiting, download_member, save_download, guard)
                except (ValueError, OSError, KeyError, json.JSONDecodeError):
                    unsaved = sum(
                        store.job(member["job_id"])["state"] != "completed" for member in members
                    )
                    raise ValueError(
                        f"{unsaved} of {len(members)} batch outputs could not be "
                        "imported. Saved results are kept; retry reconnects to this allocation."
                    ) from None
                if state != "SUCCESS":
                    # All validated outputs remain useful even when the remote wrapper failed later.
                    checkpoint(cleanup_state="retained")
                elif settings.ucloud_transcriber.cleanup_after_success:
                    if run.get("cleanup_state") not in native.CLEANED:
                        cleanup_batch(store, worker.credentials, identifier)
                else:
                    checkpoint(cleanup_state="retained")
            except Cancelled:
                if run.get("job_id") and not worker.stopping.is_set():
                    if run.get("remote_state") not in TERMINAL:
                        native.cloud_call(client.terminate_job, run["job_id"])
                    checkpoint(cancellation_requested=True)
                raise
        store.finish_native_batch(identifier, "completed", "UCloud batch complete")
    except Cancelled:
        store.finish_native_batch(
            identifier,
            "interrupted" if worker.stopping.is_set() else "cancelled",
            "Stopped. Retry explicitly to reconnect to the same UCloud batch; saved results kept.",
        )
        raise
    except MonitoringInterrupted as exc:
        store.finish_native_batch(identifier, "interrupted", str(exc))
        raise
    except (ValueError, OSError, KeyError, json.JSONDecodeError):
        store.finish_native_batch(identifier, "failed", "UCloud batch failed; saved results kept.")
        raise
    except (UCloudAPIError, httpx.HTTPError, RuntimeError):
        store.finish_native_batch(
            identifier, "failed", "UCloud batch connection failed; retry explicitly."
        )
        raise ValueError(
            "UCloud batch operation failed. Check this allocation before retrying."
        ) from None
