"""Delete-consented and automatic release of owned UCloud working folders (ADR 0101).

Only an allocation's exact owned folder moves to UCloud Trash, and only after the remote job
is observed terminal; absence is confirmed by listing the parent. Nothing is permanently
deleted, Trash is never emptied, nothing is submitted again, and restored (archival) owners
are never touched. Uncertain submissions and owners whose remote state cannot be observed
stay visible in Activity for an explicit decision.
"""

import os
import threading
import time

import httpx
from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api
from . import native_batch, providers, restored_owners, runtime_store
from . import runtime_batches as batches
from . import ucloud_native as native
from .store import now

ERRORS = (ValueError, OSError, KeyError, UCloudAPIError, httpx.HTTPError)
RETRY_SECONDS = 600
MAX_CANDIDATES = 200
_TICK = threading.Lock()
_ATTEMPTS = {}
_NOTES = {}

UNCERTAIN = (
    "This UCloud submission is uncertain. Find the UCloud job or confirm that none was "
    "created; its working folder then moves to UCloud Trash."
)
WAITING = "Waiting for the UCloud job to stop; its working folder moves to Trash once it ends."


def enabled():
    """Tests opt out; the running app always re-evaluates retained obligations."""
    return not os.environ.get("CBS_TRANSCRIBER_NO_REMOTE_RELEASE")


def _restored(store, kind, identifier):
    with store.connection() as db:
        return restored_owners.is_restored(db, kind, identifier)


def _note(store, kind, identifier, text):
    key = (str(store.root), kind, identifier)
    if text is None:
        _NOTES.pop(key, None)
    else:
        _NOTES[key] = text[:300]


def note(store, kind, identifier):
    """Why an automatic release is waiting; in memory only, no journal churn."""
    return _NOTES.get((str(store.root), kind, identifier))


# ---------- Delete impact ----------


def runtime_impact(store, batch_id, deleting):
    """What Delete does to one CBS Runtime owner, before any cancellation (preview)."""
    if _restored(store, "runtime_batch", batch_id):
        return None
    batch = runtime_store.get(store, batch_id)
    journal = batch["journal"]
    if journal.get("cleanup_state") in native.CLEANED:
        return None
    settings = batches._settings(batch)
    uncertain = runtime_store._uncertain(journal)
    live = bool(journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL)
    created = bool(journal.get("folder_created") or journal.get("owned_folder"))
    if not (created or uncertain or live or batch["state"] == "running"):
        return None
    gone = runtime_store.deleted_members(store, batch)
    others = [m for m in batch["members"] if m["job_id"] not in set(deleting) | gone]
    saved = all(m["state"] == "completed" and m["result_id"] for m in others)
    return {
        "owner_kind": "runtime_batch",
        "owner_id": batch_id,
        "label": "CBS Runtime batch",
        "folder": batches.remote_folder(settings, batch_id),
        "server": settings.base_url,
        "remote_project": settings.ucloud_transcriber.project_id
        if settings.ucloud_transcriber
        else None,
        "action": "trash" if saved else "retain",
        "stop_remote": live or batch["state"] == "running",
        "submission_uncertain": uncertain,
        "files": len(batch["members"]),
        "other_files": len(others),
    }


def native_impact(store, batch_id, deleting):
    """What Delete does to one native Transcriber allocation (preview)."""
    if _restored(store, "native_batch", batch_id):
        return None
    batch = store.native_batch(batch_id)
    run = batch["params"].get("ucloud_run", {})
    if run.get("cleanup_state") in native.CLEANED:
        return None
    settings = providers.Settings.model_validate(batch["params"]["settings"])
    uncertain = native_batch.uncertain(run)
    live = bool(run.get("job_id") and run.get("remote_state") not in api.TERMINAL)
    created = bool(run.get("owned_folder") or run.get("root_created"))
    if not (created or uncertain or live or batch["state"] == "running"):
        return None
    gone = native_batch.deleted_members(store, batch)
    others = [m for m in batch["members"] if m["job_id"] not in set(deleting) | gone]
    saved = all(
        store.all(
            "SELECT id FROM transcript_results WHERE job_id=? AND id NOT IN "
            "(SELECT result_id FROM result_trash)",
            (m["job_id"],),
        )
        for m in others
    )
    return {
        "owner_kind": "native_batch",
        "owner_id": batch_id,
        "label": "UCloud Transcriber batch",
        "folder": native_batch.folder_for(batch),
        "server": settings.base_url,
        "remote_project": settings.ucloud_transcriber.project_id
        if settings.ucloud_transcriber
        else None,
        "action": "trash" if saved else "retain",
        "stop_remote": live or batch["state"] == "running",
        "submission_uncertain": uncertain,
        "files": len(batch["members"]),
        "other_files": len(others),
    }


def impact(store, kind, identifier, deleting):
    return (runtime_impact if kind == "runtime_batch" else native_impact)(
        store, identifier, deleting
    )


# ---------- Delete-consented release ----------


def release_for_deletion(store, credentials, kind, identifier, *, action, deleting, target):
    """Record the decision durably, then try the remote steps; failures stay journaled."""
    if _restored(store, kind, identifier):
        return None
    if kind == "runtime_batch":
        with batches._ACTIONS_LOCK:
            batch = runtime_store.get(store, identifier)
            if batch["journal"].get("cleanup_state") in native.CLEANED:
                return batch["journal"]["cleanup_state"]
            if action != "trash":
                runtime_store.record_deleted_members(store, identifier, deleting)
                return "retained"
            runtime_store.confirm_release(
                store, identifier, target=target, deleted_job_ids=deleting
            )
            return _finish_runtime(store, credentials, identifier)
    batch = store.native_batch(identifier)
    if batch["params"].get("ucloud_run", {}).get("cleanup_state") in native.CLEANED:
        return batch["params"]["ucloud_run"]["cleanup_state"]
    if action != "trash":
        native_batch.record_deleted_members(store, identifier, deleting)
        return "retained"
    native_batch.confirm_release(store, identifier, target=target, deleted_job_ids=deleting)
    return _finish_native(store, credentials, identifier)


def _runtime_failure(store, identifier, exc, stage):
    diagnostic = native.cleanup_diagnostic(exc, stage)
    diagnostic["cleanup_state"] = "pending"
    batch = runtime_store.get(store, identifier)
    runtime_store.checkpoint(
        store,
        identifier,
        batch["epoch"],
        journal_update={**diagnostic, "cleanup_attempted_at": now()},
    )
    return "pending"


def _finish_runtime(store, credentials, identifier):
    """Stop if needed, confirm terminal, then Trash the exact owned folder."""
    batch = runtime_store.get(store, identifier)
    journal = batch["journal"]
    if journal.get("cleanup_state") in native.CLEANED:
        return journal["cleanup_state"]
    if batch["state"] in {"queued", "running"}:
        raise ValueError("Wait for the CBS Runtime batch to stop before releasing its folder.")
    if runtime_store._uncertain(journal):
        return _runtime_failure(
            store,
            identifier,
            native.CleanupError(UNCERTAIN, "reconcile", "submission_uncertain"),
            "reconcile",
        )
    folder = batches.remote_folder(batches._settings(batch), identifier)
    if journal.get("owned_folder") not in (None, folder):
        raise ValueError("The runtime owner's folder receipt disagrees with its identity.")
    if not journal.get("job_id") and not (
        journal.get("owned_folder") or journal.get("folder_created")
    ):
        return "none"  # Nothing was allocated or created remotely for this owner.
    stage = "setup"
    try:
        with batches._client(credentials, batch) as client:
            if journal.get("job_id"):
                stage = "terminal_check"
                state = api.remote_state(client, journal["job_id"])
                if state not in api.TERMINAL and not journal.get("termination_requested"):
                    native.cloud_call(client.terminate_job, journal["job_id"])
                    batch = runtime_store.checkpoint(
                        store,
                        identifier,
                        batch["epoch"],
                        journal_update={"termination_requested": True},
                    )
                    state = api.remote_state(client, journal["job_id"])
                batch = runtime_store.checkpoint(
                    store,
                    identifier,
                    batch["epoch"],
                    journal_update={"remote_state": state, "remote_observed_at": now()},
                )
                if state not in api.TERMINAL:
                    raise native.CleanupError(WAITING, stage, "remote_not_terminal")
                if batch["journal"].get("capacity_reserved"):
                    runtime_store.release_capacity(store, identifier, epoch=batch["epoch"])
            if not (journal.get("owned_folder") or journal.get("folder_created")):
                return "none"  # Nothing was created remotely for this owner.
            stage = "trash"
            native.trash_owned_folder(client, folder)
    except ERRORS as exc:
        return _runtime_failure(store, identifier, exc, stage)
    batch = runtime_store.get(store, identifier)
    runtime_store.checkpoint(
        store,
        identifier,
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
    return "trashed"


def _native_client(settings, key):
    return native.client_for(settings, key)


def _finish_native(store, credentials, identifier):
    batch = store.native_batch(identifier)
    run = batch["params"].get("ucloud_run", {})
    if run.get("cleanup_state") in native.CLEANED:
        return run["cleanup_state"]
    if batch["state"] in {"queued", "running"}:
        raise ValueError("Wait for the UCloud batch to stop before releasing its folder.")
    attempted = {"cleanup_attempted_at": now()}
    if native_batch.uncertain(run):
        diagnostic = native.cleanup_diagnostic(
            native.CleanupError(UNCERTAIN, "reconcile", "submission_uncertain"), "reconcile"
        )
        store.update_native_batch(identifier, run_update={**diagnostic, **attempted})
        return "pending"
    folder = native_batch.folder_for(batch)
    if run.get("owned_folder") not in (None, folder):
        raise ValueError("The UCloud batch folder receipt disagrees with its identity.")
    settings = providers.Settings.model_validate(batch["params"]["settings"])
    stage = "setup"
    try:
        key = credentials.get(providers.credential_scope(settings))
        native.require_setup(settings, key)
        with _native_client(settings, key) as client:
            if run.get("job_id"):
                stage = "terminal_check"
                state = api.remote_state(client, run["job_id"])
                if state not in api.TERMINAL and not run.get("termination_requested"):
                    native.cloud_call(client.terminate_job, run["job_id"])
                    store.update_native_batch(
                        identifier, run_update={"termination_requested": True}
                    )
                    state = api.remote_state(client, run["job_id"])
                store.update_native_batch(identifier, run_update={"remote_state": state})
                if state not in api.TERMINAL:
                    raise native.CleanupError(WAITING, stage, "remote_not_terminal")
            if not (run.get("owned_folder") or run.get("root_created")):
                return "none"
            stage = "trash"
            native.trash_owned_folder(client, folder)
    except ERRORS as exc:
        diagnostic = native.cleanup_diagnostic(exc, stage)
        diagnostic["cleanup_state"] = "pending"
        store.update_native_batch(identifier, run_update={**diagnostic, **attempted})
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


def retry(store, credentials, kind, identifier):
    """Explicit Activity Retry of a Delete-consented release; never a new allocation."""
    if kind not in {"runtime_batch", "native_batch"}:
        raise KeyError(kind)
    restored_owners.require_owner(store, kind, identifier)
    if kind == "runtime_batch":
        with batches._ACTIONS_LOCK:
            if not runtime_store.get(store, identifier)["journal"].get("release_decision"):
                raise ValueError("Only a deleted project's UCloud folder can be released here.")
            state = _finish_runtime(store, credentials, identifier)
    else:
        if (
            not store.native_batch(identifier)["params"]
            .get("ucloud_run", {})
            .get("release_decision")
        ):
            raise ValueError("Only a deleted project's UCloud folder can be released here.")
        state = _finish_native(store, credentials, identifier)
    _note(store, kind, identifier, None)
    return {"ok": True, "cleanup_state": state, "owner_kind": kind, "owner_id": identifier}


# ---------- Automatic release of old obligations ----------


def _auto_runtime(store, credentials, identifier):
    from . import runtime_discard as discard

    restored_owners.require_owner(store, "runtime_batch", identifier)
    with batches._ACTIONS_LOCK:
        batch = runtime_store.get(store, identifier)
        journal = batch["journal"]
        if (
            batch["state"] in {"queued", "running"}
            or journal.get("recovery_requested")
            or journal.get("cleanup_state") in native.CLEANED
        ):
            return None
        if journal.get("release_decision"):
            return _finish_runtime(store, credentials, identifier)
        decision = journal.get("discard_decision")
        if decision:
            # Consent (explicit or automatic) is recorded; only its Trash step is retried.
            return discard.confirm(
                store,
                credentials,
                identifier,
                fingerprint=decision["fingerprint"],
                confirmed=True,
            )["cleanup_state"]
        if runtime_store._uncertain(journal):
            raise ValueError(UNCERTAIN)
        settings = batches._settings(batch)
        if not (
            settings.ucloud_transcriber.cleanup_after_success
            or journal.get("cleanup_state") == "pending"
        ):
            raise ValueError("This connection keeps run files on UCloud after processing.")
        if not journal.get("job_id"):
            if batches._public_only(store, batch):
                return batches.retry_cleanup(store, credentials, identifier)["cleanup_state"]
            raise ValueError("No UCloud job is recorded; review this allocation in Activity.")
        with batches._client(credentials, batch) as client:
            state = api.remote_state(client, journal["job_id"])
        if state != journal.get("remote_state"):
            batch = runtime_store.checkpoint(
                store,
                identifier,
                batch["epoch"],
                journal_update={"remote_state": state, "remote_observed_at": now()},
            )
        if state not in api.TERMINAL:
            raise ValueError(
                "The UCloud job has not ended. Reconnect to continue it or stop it in Activity."
            )
        gone = runtime_store.deleted_members(store, batch)
        if batch["admission"] not in {"sealed", "closed"}:
            # A terminal allocation can accept nothing; close admission before disposition.
            batch = runtime_store.stop(store, identifier)
        if all(
            m["state"] == "completed" and m["result_id"]
            for m in batch["members"]
            if m["job_id"] not in gone
        ):
            return batches.retry_cleanup(store, credentials, identifier)["cleanup_state"]
        # Preserve every available bounded output locally (ADR 0066 collector) before Trash.
        # A complete result that is not yet saved refuses here and keeps its Retry path.
        plan = discard.preview(store, credentials, identifier)
        return discard.confirm(
            store,
            credentials,
            identifier,
            fingerprint=plan["fingerprint"],
            confirmed=True,
            decided_by="automatic",
        )["cleanup_state"]


def _auto_native(store, credentials, identifier):
    restored_owners.require_owner(store, "native_batch", identifier)
    batch = store.native_batch(identifier)
    run = batch["params"].get("ucloud_run", {})
    if batch["state"] in {"queued", "running"} or run.get("cleanup_state") in native.CLEANED:
        return None
    if run.get("release_decision"):
        return _finish_native(store, credentials, identifier)
    if native_batch.uncertain(run):
        raise ValueError(UNCERTAIN)
    settings = providers.Settings.model_validate(batch["params"]["settings"])
    if run.get("job_id") and run.get("remote_state") not in api.TERMINAL:
        key = credentials.get(providers.credential_scope(settings))
        native.require_setup(settings, key)
        with _native_client(settings, key) as client:
            state = api.remote_state(client, run["job_id"])
        if state not in api.TERMINAL:
            raise ValueError("The UCloud job has not ended; it is left running.")
        batch = store.update_native_batch(identifier, run_update={"remote_state": state})
        run = batch["params"]["ucloud_run"]
    if run.get("cleanup_state") != "pending":
        return None
    gone = native_batch.deleted_members(store, batch)
    if not all(
        store.all("SELECT id FROM transcript_results WHERE job_id=?", (m["job_id"],))
        for m in batch["members"]
        if m["job_id"] not in gone
    ):
        raise ValueError("A batch transcript is not saved yet. Retry the batch to import it.")
    return native_batch.cleanup_batch(store, credentials, identifier)


def candidates(store):
    """Owners whose obligation might settle without a researcher decision; bounded."""
    found = []
    for batch in runtime_store.obligations(store):
        if batch["state"] not in {"queued", "running"}:
            found.append(("runtime_batch", batch["id"]))
    for row in store.all(
        "SELECT id FROM native_batch_runs WHERE state NOT IN ('queued','running') "
        "AND json_valid(params) AND coalesce(json_extract(params,'$.ucloud_run.cleanup_state'),'') "
        "NOT IN ('trashed','deleted') AND (json_extract(params,'$.ucloud_run.cleanup_state')="
        "'pending' OR json_type(params,'$.ucloud_run.release_decision')='object') "
        "ORDER BY updated,id LIMIT ?",
        (MAX_CANDIDATES,),
    ):
        if _restored(store, "native_batch", row["id"]):
            continue
        try:
            batch = store.native_batch(row["id"])
        except ERRORS:
            continue
        if native_batch.unresolved(batch):
            found.append(("native_batch", row["id"]))
    return found[: MAX_CANDIDATES * 2]


def release_one(store, credentials, kind, identifier):
    """One automatic evaluation; reasons it must wait are kept in memory for Activity."""
    try:
        state = (_auto_runtime if kind == "runtime_batch" else _auto_native)(
            store, credentials, identifier
        )
    except ERRORS as exc:
        _note(store, kind, identifier, str(exc) or "The owner could not be checked.")
        return None
    except Exception:  # noqa: BLE001 - a malformed journal must not stop the Runtime lane.
        _note(store, kind, identifier, "This allocation's saved receipts could not be checked.")
        return None
    _note(store, kind, identifier, None)
    return state


def tick(store, credentials, queue=None, *, clock=time.monotonic):
    """Evaluate at most one due owner; called from the idle Runtime lane outside locks."""
    if not _TICK.acquire(blocking=False):
        return None
    try:
        moment = clock()
        try:
            found = candidates(store)
        except Exception:  # noqa: BLE001 - keep the lane alive; Activity shows the owner.
            return None
        for kind, identifier in found:
            key = (str(store.root), kind, identifier)
            last = _ATTEMPTS.get(key)
            if last is not None and moment - last < RETRY_SECONDS:
                continue
            try:
                if queue is not None:
                    # A reservation, not an observation: Stop/Retry/Delete cannot begin
                    # between this check and the destructive remote request.
                    with queue.control_owner(kind, identifier):
                        _ATTEMPTS[key] = moment
                        return kind, identifier, release_one(store, credentials, kind, identifier)
            except Exception:  # noqa: BLE001
                continue
            _ATTEMPTS[key] = moment
            return kind, identifier, release_one(store, credentials, kind, identifier)
        return None
    finally:
        _TICK.release()
