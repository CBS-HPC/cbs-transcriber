"""Read-only remote observations release legacy runtime slots only with terminal proof."""

import json
import sqlite3
import threading
import time

import httpx
from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api
from . import providers, runtime_store
from . import ucloud_native as native
from .store import now

REFRESH_SECONDS = 15
_refresh_gate = threading.Lock()
_last_refresh = {}
_STATES = api.TERMINAL | {"IN_QUEUE", "RUNNING", "PENDING", "PREPARING", "CANCELING", "CANCELLING"}


def _identity(job):
    params = job["params"]
    if (
        job["kind"] != "enhance"
        or not isinstance(params.get("ucloud_api"), dict)
        or params.get("runtime_batch_id")
        or params.get("native_batch_id")
        or not isinstance(params.get("ucloud_run", {}).get("job_id"), str)
        or not params["ucloud_run"]["job_id"]
    ):
        return None
    return params["ucloud_api"], params["ucloud_run"]["job_id"]


def _merge(store, identifier, identity, **receipt):
    """Merge after remote I/O; a caller snapshot can never replace newer receipts."""
    from . import restored_owners

    restored_owners.require_job(store, identifier)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("The legacy runtime job no longer exists.")
        current = dict(row)
        current["params"] = json.loads(current["params"])
        if _identity(current) != identity:
            raise ValueError("The owned runtime allocation changed during its status check.")
        params = current["params"]
        run = params["ucloud_run"]
        if run.get("remote_state") in api.TERMINAL:
            receipt.pop(
                "remote_state", None
            )  # An older RUNNING response cannot reopen a terminal job.
        run.update(receipt)
        db.execute(
            "UPDATE jobs SET params=?,updated=? WHERE id=?",
            (json.dumps(params), now(), identifier),
        )
    current = store.job(identifier)
    if current["params"]["ucloud_run"].get("remote_state") in api.TERMINAL:
        runtime_store.release_capacity(store, identifier, legacy=True)
        current = store.job(identifier)
    return current


def refresh(store, credentials, job, *, client=None):
    """Return a fresh job; status failures retain capacity and a curated diagnostic.

    Neither a missing remote ID, a terminate acknowledgement, nor a 404 proves absence.
    Existing terminal receipts are sufficient proof and require no additional network call.
    """
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    current = store.job(job["id"])
    identity = _identity(current)
    if identity is None:
        return current
    if client is not None and _identity(job) != identity:
        raise ValueError("The owned runtime allocation changed before its status check.")
    stamp = now()
    receipt = {"capacity_refresh_attempted_at": stamp}
    if current["params"]["ucloud_run"].get("remote_state") in api.TERMINAL:
        return _merge(store, current["id"], identity, **receipt, capacity_refresh_error=None)
    try:
        if client is None:
            _, settings = api.frozen(current)
            key = credentials.get(providers.credential_scope(settings))
            if not key:
                return _merge(
                    store,
                    current["id"],
                    identity,
                    **receipt,
                    capacity_refresh_error="credentials_unavailable",
                )
            with native.client_for(settings, key) as owned:
                state = api.remote_state(owned, identity[1])
        else:
            state = api.remote_state(client, identity[1])
        if not isinstance(state, str) or state not in _STATES:
            raise ValueError("Invalid remote allocation state")
    except (ValueError, KeyError, TypeError, OSError, UCloudAPIError, httpx.HTTPError):
        return _merge(
            store, current["id"], identity, **receipt, capacity_refresh_error="status_unavailable"
        )
    return _merge(
        store,
        current["id"],
        identity,
        **receipt,
        remote_state=state,
        remote_observed_at=stamp,
        capacity_refresh_error=None,
    )


def drain(store, credentials, job, *, client, checkpoint, wait=None, max_reads=6):
    """Bound post-Stop observation; return the fresh job, retaining unconfirmed slots.

    `wait(seconds)` may be a cancellation Event's wait method. A truthy result ends
    polling without equating local cancellation with remote termination.
    """
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    if type(max_reads) is not int or not 1 <= max_reads <= 6:
        raise ValueError("Use between one and six bounded allocation status reads.")
    wait = wait or time.sleep
    current = store.job(job["id"])
    for index in range(max_reads):
        current = refresh(store, credentials, current, client=client)
        run = current["params"].get("ucloud_run", {})
        if _identity(current) is None:
            return current
        checkpoint(
            **{
                key: run[key]
                for key in (
                    "remote_state",
                    "remote_observed_at",
                    "capacity_reserved",
                    "capacity_released_at",
                    "capacity_refresh_attempted_at",
                    "capacity_refresh_error",
                )
                if key in run
            }
        )
        if run.get("remote_state") in api.TERMINAL or run.get("capacity_refresh_error"):
            break
        if index + 1 < max_reads and wait(1):
            break
    return store.job(current["id"])


def refresh_blocker(store, credentials):
    """At most one old legacy owner per 15 seconds; never hold the queue lock here."""
    if not _refresh_gate.acquire(blocking=False):
        return None
    try:
        key = str(store.root)
        moment = time.monotonic()
        if moment - _last_refresh.get(key, float("-inf")) < REFRESH_SECONDS:
            return None
        _last_refresh[key] = moment
        terminal = sorted(api.TERMINAL)
        from . import restored_owners

        with store.connection() as db:
            owned = restored_owners.sql_exclusion(db, "job", "jobs.id")
        rows = store.all(
            "WITH safe_jobs AS (SELECT id,kind,created,CASE WHEN json_valid(params) "
            "THEN params ELSE 'null' END AS params FROM jobs) "
            "SELECT id FROM safe_jobs jobs WHERE " + owned + " AND kind='enhance' "
            "AND json_type(params,'$.ucloud_api')='object' "
            "AND json_extract(params,'$.runtime_batch_id') IS NULL "
            "AND json_extract(params,'$.native_batch_id') IS NULL "
            "AND json_type(params,'$.ucloud_run.job_id')='text' "
            "AND json_extract(params,'$.ucloud_run.job_id')!='' "
            "AND (json_extract(params,'$.ucloud_run.capacity_reserved')=1 "
            "OR COALESCE(json_extract(params,'$.ucloud_run.remote_state'),'') NOT IN ("
            + ",".join("?" for _ in terminal)
            + ")) ORDER BY COALESCE("
            "json_extract(params,'$.ucloud_run.capacity_refresh_attempted_at'),"
            "json_extract(params,'$.ucloud_run.remote_observed_at'),created),created,id LIMIT 1",
            terminal,
        )
        return refresh(store, credentials, store.job(rows[0]["id"])) if rows else None
    except (KeyError, ValueError, OSError, sqlite3.Error):
        return None  # An unavailable owner/library must not end an idle coordinator thread.
    finally:
        _refresh_gate.release()
