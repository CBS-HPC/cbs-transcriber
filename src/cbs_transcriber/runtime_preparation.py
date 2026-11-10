"""Explicit library-level CBS Runtime preparation; never owns recording inputs.

The SQLite owner is the submission authority. A lost POST reply requires reconciliation;
Retry creates a successor and cannot turn uncertainty into permission to submit again.
"""

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager, nullcontext
from types import SimpleNamespace
from urllib.parse import quote

from . import enhancement_api as api
from . import providers, runtime_compute, runtime_store
from . import ucloud_native as native
from .enhancement import local_settings
from .store import new_id, now

POLICY = "cbs-runtime-preparation-v1"
PREFIX = "runtime-preparation:"
RECENT_ACTIVITY = 200
_CONTROL_LOCK = threading.Lock()
_CONTROLS = set()
DDL = """CREATE TABLE IF NOT EXISTS runtime_preparations (
    id TEXT PRIMARY KEY,
    connection_fingerprint TEXT NOT NULL,
    frozen TEXT NOT NULL,
    journal TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    epoch INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
    hidden_updated TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
)"""


class Conflict(ValueError):
    """An explicit action needs fresh saved settings or resolution of its prior owner."""


class ControlBusy(Conflict):
    """Another request owns this preparation's short-lived control slot."""


class Interrupted(Exception):
    pass


@contextmanager
def _control(identifier):
    """Logical same-owner control slot; never serialize unrelated network operations."""
    with _CONTROL_LOCK:
        if identifier in _CONTROLS:
            raise ControlBusy("Wait for this preparation's current control request.")
        _CONTROLS.add(identifier)
    try:
        yield
    finally:
        with _CONTROL_LOCK:
            _CONTROLS.remove(identifier)


def migrate(store):
    with store.library_lock(), store.connection() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= 16:
            return
        if version != 15:
            raise RuntimeError("Runtime preparation must follow the schema15 migration.")
        if store.initial_version == 15:
            (store.root / "backups").mkdir(exist_ok=True)
            with closing(
                sqlite3.connect(store.root / "backups" / f"schema-15-{new_id()}.sqlite3")
            ) as copy:
                db.backup(copy)
        db.execute("BEGIN IMMEDIATE")
        db.execute(DDL)
        db.execute("PRAGMA user_version=16")


def fingerprint(chosen):
    return runtime_store._digest(_identity(chosen))


def _identity(chosen):
    # Capture public recipe bytes outside queue/SQLite writer locks.
    identity = {
        "policy": POLICY,
        **chosen,
        "runtime_id": api.runtime_id(),
        "recipe_sha256": recipe_hash(),
    }
    if (chosen["settings"].get("ucloud_transcriber", {}).get("product") or {}).get(
        "id"
    ) == native.MIG_PRODUCT["id"]:
        identity["connection_policy"] = "native-connection-v1"
    return identity


def recipe_hash():
    return runtime_store._digest(
        {
            path.name: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for path in (*api.WORKER_FILES, api.SUPERVISOR, api.REQUIREMENTS)
        }
    )


def _raw(db, identifier):
    row = db.execute("SELECT * FROM runtime_preparations WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise KeyError(identifier)
    return dict(row)


def _get(db, identifier, *, owned=False):
    value = _raw(db, identifier)
    if owned:
        _owned(value)  # Restored rows are inert even if their old evidence is unreadable.
    return _decode(value)


def _decode(raw):
    value = dict(raw)
    identifier = value["id"]
    try:
        if re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
            raise ValueError()
        for field in ("frozen", "journal"):
            raw = value[field]
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
                raise ValueError()
            value[field] = json.loads(raw)
            if type(value[field]) is not dict:
                raise ValueError()
            runtime_store.canonical(value[field])  # Refuse nonfinite observations too.
        frozen, journal = value["frozen"], value["journal"]
        if (
            frozen.get("policy") != POLICY
            or type(frozen.get("settings")) is not dict
            or not isinstance(frozen.get("runtime_id"), str)
            or not 0 < len(frozen["runtime_id"]) <= 200
            or not isinstance(frozen.get("provider_id"), str)
            or not isinstance(frozen.get("recipe_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", frozen["recipe_sha256"])
            or journal.get("run_name") != "CBS runtime preparation " + identifier
            or value["state"] not in runtime_store.STATES
            or type(value["epoch"]) is not int
            or value["epoch"] < 0
            or type(value["progress"]) not in (int, float)
            or not math.isfinite(value["progress"])
            or not 0 <= value["progress"] <= 1
        ):
            raise ValueError()
        settings = providers.Settings.model_validate(frozen["settings"])
        if settings.provider_type != "ucloud" or settings.ucloud_transcriber is None:
            raise ValueError()
        if "connection_policy" in frozen:
            if frozen["connection_policy"] != "native-connection-v1":
                raise ValueError()
            native.require_connection(settings)
        elif (settings.ucloud_transcriber.product or {}).get("id") != native.COMPUTE_ID:
            raise ValueError()  # Preserve the exact pre-feature frozen preparation gate.
        _target(frozen)
        if journal.get("job_id") is not None and (
            not isinstance(journal["job_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", journal["job_id"])
        ):
            raise ValueError()
        if value["connection_fingerprint"] != runtime_store._digest(
            {
                key: frozen.get(key)
                for key in (
                    "policy",
                    "provider_id",
                    "settings",
                    "runtime_id",
                    "recipe_sha256",
                    *(["connection_policy"] if "connection_policy" in frozen else []),
                )
            }
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError, RecursionError):
        raise Conflict(
            "This preparation's frozen connection evidence is invalid. Preserve it."
        ) from None
    return value


def get(store, identifier, *, owned=False):
    with store.connection() as db:
        return _get(db, identifier, owned=owned)


def _owned(value):
    if value["archived"]:
        raise Conflict("This restored preparation is historical and cannot control UCloud.")


def unresolved(value):
    if value.get("unverified"):
        return not value["archived"]
    return not value["archived"] and (
        value["state"] in {"queued", "running"}
        or runtime_store._reserved(value["journal"])
        or value["journal"].get("cleanup_state") == "pending"
    )


def obligations(store):
    with store.connection() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_preparations'"
        ).fetchone():
            return []
        values = []
        for row in db.execute("SELECT * FROM runtime_preparations WHERE archived=0"):
            try:
                value = _get(db, row["id"])
            except Conflict:
                value = {**dict(row), "unverified": True}
            if unresolved(value):
                values.append(value)
        return values


def _target(frozen):
    settings = frozen["settings"]
    setup = settings["ucloud_transcriber"]
    return (settings["base_url"], setup["project_id"], setup["storage_path"], frozen["runtime_id"])


def _quarantine(db, row):
    # Only operational state changes. Keep malformed frozen/journal bytes as evidence.
    db.execute(
        "UPDATE runtime_preparations SET state='failed',epoch=epoch+1,message=?,updated=? "
        "WHERE id=?",
        (
            "Preparation metadata is unverified. Preserve its evidence before further controls.",
            now(),
            row["id"],
        ),
    )


def _current(store, credentials, expected):
    if not local_settings(store).prepare_ucloud_models:
        raise Conflict("Save automatic UCloud preparation as enabled before preparing the runtime.")
    chosen = api.connection(store, credentials, [])
    providers.require_enabled(providers.Settings.model_validate(chosen["settings"]))
    if fingerprint(chosen) != expected:
        raise Conflict(
            "The UCloud connection changed. Reopen UCloud settings to load the current "
            "UCloud connection, then check again."
        )
    return chosen


def _insert(db, identity, expected, *, retry_of=None):
    identifier, stamp = new_id(), now()
    frozen = {**identity, "retry_of": retry_of}
    journal = {"run_name": "CBS runtime preparation " + identifier}
    db.execute(
        "INSERT INTO runtime_preparations(id,connection_fingerprint,frozen,journal,message,"
        "created,updated) VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            expected,
            runtime_store.canonical(frozen),
            runtime_store.canonical(journal),
            "Waiting to prepare the UCloud runtime…",
            stamp,
            stamp,
        ),
    )
    return _get(db, identifier)


def request(store, credentials, expected):
    """Only the explicit HTTP action calls this. Readiness errors create no operation."""
    chosen = _current(store, credentials, expected)
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        prepared = api.stamped(client, settings)
    if prepared:
        return {
            "prepared": True,
            "runtime_id": api.runtime_id(),
            "operation": None,
            "deduplicated": False,
        }
    _current(store, credentials, expected)
    identity = _identity(chosen)
    target, value, latest = _target(identity), None, None
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT * FROM runtime_preparations WHERE archived=0 ORDER BY created DESC,id DESC"
        ):
            try:
                candidate = _get(db, row["id"])
            except Conflict:
                # An unknown live owner cannot establish that another allocation is safe.
                raise Conflict(
                    "Resolve unverified preparation evidence in Activity "
                    "before starting another allocation."
                ) from None
            if _target(candidate["frozen"]) == target and (
                candidate["state"] in {"queued", "running"}
                or runtime_store._reserved(candidate["journal"])
            ):
                value = candidate
                break
            if latest is None and candidate["connection_fingerprint"] == expected:
                latest = candidate
        deduplicated = value is not None
        if value is None:
            # A fresh explicit click may replace a settled owner when the runtime is
            # missing again. Preserve all prior evidence and create a visible successor.
            value = _insert(db, identity, expected, retry_of=latest["id"] if latest else None)
    return {
        "prepared": False,
        "runtime_id": api.runtime_id(),
        "operation": card(value),
        "deduplicated": deduplicated,
    }


def _update(db, value, *, journal=None, **fields):
    _owned(value)
    if journal is not None:
        value["journal"].update(journal)
        fields["journal"] = runtime_store.canonical(value["journal"])
    fields["updated"] = now()
    db.execute(
        "UPDATE runtime_preparations SET "
        + ",".join(key + "=?" for key in fields)
        + " WHERE id=? AND epoch=? AND archived=0",
        (*fields.values(), value["id"], value["epoch"]),
    )
    return _get(db, value["id"])


def checkpoint(store, identifier, epoch, *, journal=None, **fields):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = _get(db, identifier, owned=True)
        if value["epoch"] != epoch:
            raise Interrupted()
        if value["state"] in {"completed", "cancelled"} and fields.get("state") != value["state"]:
            fields = {
                key: item
                for key, item in fields.items()
                if key not in {"state", "message", "progress"}
            }
        if (
            journal
            and value["journal"].get("remote_state") in api.TERMINAL
            and journal.get("remote_state") not in api.TERMINAL
        ):
            journal = {key: item for key, item in journal.items() if key != "remote_state"}
        return _update(db, value, journal=journal, **fields)


def claim(store):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT * FROM runtime_preparations WHERE state='queued' AND archived=0 "
            "ORDER BY created,id"
        ).fetchall():
            with _CONTROL_LOCK:
                if row["id"] in _CONTROLS:
                    continue
            try:
                value = _get(db, row["id"])
            except Conflict:
                _quarantine(db, row)
                continue
            return _update(db, value, state="running", epoch=value["epoch"] + 1)
        return None


def recover(store):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for (identifier,) in db.execute(
            "SELECT id FROM runtime_preparations WHERE state='running' AND archived=0"
        ).fetchall():
            try:
                value = _get(db, identifier)
            except Conflict:
                _quarantine(db, _raw(db, identifier))
                continue
            # Only observation of a recorded allocation resumes automatically. Even a
            # pre-POST interruption needs another explicit Retry before a paid submission.
            state = "queued" if value["journal"].get("job_id") else "interrupted"
            _update(
                db,
                value,
                state=state,
                epoch=value["epoch"] + 1,
                message="Preparation was interrupted; its allocation evidence was retained.",
            )


def begin_submission(store, identifier, epoch):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        value = _get(db, identifier)
        _owned(value)
        if value["epoch"] != epoch or value["state"] != "running":
            raise Interrupted()
        journal = value["journal"]
        if journal.get("stop_intent"):
            raise Interrupted()
        if journal.get("job_id") or runtime_store._uncertain(journal):
            raise Conflict("Reconcile the recorded allocation before another submission.")
        for row in db.execute(
            "SELECT id FROM runtime_preparations WHERE archived=0 AND id!=?", (identifier,)
        ):
            try:
                other = _get(db, row[0])
            except Conflict:
                raise Conflict(
                    "Resolve unverified preparation evidence before another allocation."
                ) from None
            if _target(other["frozen"]) == _target(value["frozen"]) and runtime_store._reserved(
                other["journal"]
            ):
                raise Conflict("Another preparation's allocation for this runtime is unresolved.")
        if runtime_store._capacity(db) >= runtime_store._settings(db)["max_allocations"]:
            return False
        _update(
            db,
            value,
            journal={
                "capacity_reserved": True,
                "submission_intent": True,
                "submission_attempted_at": now(),
                "reconciled_not_created": False,
                "no_post": False,
            },
        )
        return True


def _release(journal):
    if journal.get("remote_state") in api.TERMINAL or (
        not journal.get("job_id") and not runtime_store._uncertain(journal)
    ):
        return {"capacity_reserved": False, "capacity_released_at": now()}
    return {}


def card(value):
    journal, archived = value["journal"], bool(value["archived"])
    uncertain = runtime_store._uncertain(journal)
    remote_open = bool(journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL)
    active = not archived and value["state"] in {"queued", "running"}
    pending = unresolved(value)
    return {
        "id": PREFIX + value["id"],
        "runtime_preparation_id": value["id"],
        "kind": "runtime-preparation",
        "recording_id": None,
        "project_id": None,
        "recording_name": "UCloud runtime preparation",
        "members": [],
        **{key: value[key] for key in ("state", "progress", "message", "created", "updated")},
        "archived": archived,
        "runtime_id": value["frozen"]["runtime_id"],
        "run_name": journal["run_name"],
        "can_cancel": not archived and (remote_open or (active and not journal.get("stop_intent"))),
        "can_retry": not archived
        and not active
        and not uncertain
        and value["state"] != "completed",
        "can_reconcile": not archived and not active and uncertain,
        "can_cleanup": not archived
        and not active
        and not uncertain
        and not remote_open
        and journal.get("cleanup_state") == "pending",
        "can_remove": not active and not pending,
        "needs_attention": not archived
        and (pending or value["state"] in {"failed", "interrupted"}),
        "ucloud_job_id": journal.get("job_id"),
        "ucloud_job_url": (
            value["frozen"]["settings"]["base_url"].rstrip("/")
            + "/app/jobs/properties/"
            + quote(journal["job_id"], safe="")
            if journal.get("job_id") and not archived
            else None
        ),
        "ucloud_state": journal.get("remote_state"),
        "ucloud_submission_uncertain": uncertain,
        "ucloud_cleanup_state": journal.get("cleanup_state"),
        "ucloud_stop_pending": bool(journal.get("stop_intent") and remote_open),
    }


def activity(store):
    from .jobs import activity_cleared_at

    cleared = activity_cleared_at(store)
    with store.connection() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_preparations'"
        ).fetchone():
            return []
        cards = []
        for index, row in enumerate(
            db.execute("SELECT * FROM runtime_preparations ORDER BY updated DESC,id DESC")
        ):
            raw = dict(row)
            if raw["archived"]:
                item, retained = _inert_card(raw), False
            else:
                try:
                    value = _decode(raw)
                    item, retained = card(value), unresolved(value)
                except Conflict:
                    item, retained = _inert_card(raw), True
            if retained or (
                index < RECENT_ACTIVITY
                and (
                    raw["hidden_updated"] != raw["updated"]
                    and (not cleared or raw["updated"] > cleared)
                )
            ):
                cards.append(item)
    return cards


def _inert_card(raw):
    """Do not decode historical or unverified proof just to render local Activity."""
    archived = bool(raw["archived"])
    return {
        "id": PREFIX + raw["id"],
        "runtime_preparation_id": raw["id"],
        "kind": "runtime-preparation",
        "recording_id": None,
        "project_id": None,
        "recording_name": "UCloud runtime preparation",
        "members": [],
        "state": "archived" if archived else "failed",
        "progress": 0,
        "message": (
            "Historical preparation; UCloud controls are disabled."
            if archived
            else "Preparation metadata is unverified. "
            "Preserve its evidence before further controls."
        ),
        "created": raw["created"],
        "updated": raw["updated"],
        "archived": archived,
        "needs_attention": not archived,
        "can_remove": archived,
        "can_cancel": False,
        "can_retry": False,
        "can_reconcile": False,
        "can_cleanup": False,
        "run_name": None,
        "runtime_id": None,
        "ucloud_job_id": None,
        "ucloud_job_url": None,
        "ucloud_state": None,
        "ucloud_submission_uncertain": False,
        "ucloud_cleanup_state": None,
        "preparation_unverified": not archived,
    }


def cancel(store, credentials, identifier):
    # Borrow the serving Store's lease; never re-enter its RLock from an HTTP/queue worker.
    with nullcontext() if store._lease_depth else store.library_lock(), _control(identifier):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            value = _get(db, identifier, owned=True)
            if not card(value)["can_cancel"]:
                if value["journal"].get("stop_intent"):
                    return card(value)
                raise Conflict("This preparation is no longer active. Refresh Activity.")
            journal = value["journal"]
            remote_open = bool(
                journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL
            )
            state = (
                "queued"
                if remote_open
                else value["state"]
                if value["state"] == "running"
                else "cancelled"
            )
            value = _update(
                db,
                value,
                state=state,
                journal={"stop_intent": True},
                message="Stopping this preparation; remote confirmation may be pending…",
            )
        if not value["journal"].get("job_id"):
            return card(value)
        try:
            with _client(credentials, value) as (client, _):
                value = _observe_stop(
                    store,
                    client,
                    identifier,
                    value["epoch"],
                    value["journal"]["job_id"],
                    explicit=True,
                )
        except Exception:
            # A failed or lost control reply does not prove termination or a free slot.
            value = get(store, identifier, owned=True)
            if value["journal"].get("remote_state") not in api.TERMINAL:
                value = checkpoint(
                    store,
                    identifier,
                    value["epoch"],
                    message="Stop is pending. Check this recorded allocation again in Activity.",
                )
        return card(value)


def _observe_stop(store, client, identifier, epoch, remote_id, *, explicit=False):
    """Caller owns the same-owner control slot. Observe/retry only this recorded allocation."""
    value = get(store, identifier, owned=True)
    if value["epoch"] != epoch or value["journal"].get("job_id") != remote_id:
        raise Interrupted()
    journal = value["journal"]
    if not journal.get("stop_intent"):
        raise Conflict("This preparation has no Stop intent.")
    if journal.get("remote_state") in api.TERMINAL:
        return checkpoint(
            store,
            identifier,
            epoch,
            state="cancelled",
            journal=_release(journal),
            message="Preparation stopped; its recorded allocation has ended.",
        )
    state = api.remote_state(client, remote_id)
    value = checkpoint(
        store,
        identifier,
        epoch,
        journal={"remote_state": state, **_release({**journal, "remote_state": state})},
    )
    state = value["journal"]["remote_state"]
    if (
        state not in api.TERMINAL
        and not journal.get("termination_requested")
        and (not journal.get("termination_intent") or explicit)
    ):
        attempts = journal.get("termination_attempts", 0)
        if type(attempts) is not int or attempts < 0:
            raise Conflict("The preparation's Stop receipt is unverified; preserve its evidence.")
        value = checkpoint(
            store,
            identifier,
            epoch,
            journal={
                "termination_intent": True,
                "termination_job_id": remote_id,
                "termination_epoch": epoch,
                "termination_attempts": attempts + 1,
                "termination_response": "unknown",
                "termination_attempted_at": now(),
                "termination_uncertain_seen": bool(
                    journal.get("termination_uncertain_seen")
                    or (
                        journal.get("termination_intent")
                        and journal.get("termination_response") == "unknown"
                    )
                ),
            },
        )
        native.cloud_call(client.terminate_job, remote_id)
        value = checkpoint(
            store,
            identifier,
            epoch,
            journal={"termination_requested": True, "termination_response": "accepted"},
        )
        state = api.remote_state(client, remote_id)
    if state in api.TERMINAL:
        return checkpoint(
            store,
            identifier,
            epoch,
            state="cancelled",
            journal={
                "remote_state": state,
                **_release({**value["journal"], "remote_state": state}),
            },
            message="Preparation stopped; its recorded allocation has ended.",
        )
    return checkpoint(
        store, identifier, epoch, message="Stop requested; UCloud terminal confirmation is pending."
    )


def retry(store, credentials, identifier):
    with _control(identifier):
        value = get(store, identifier, owned=True)
        _owned(value)
        if not card(value)["can_retry"]:
            raise Conflict("Resolve this preparation's allocation before retrying.")
        if (
            value["journal"].get("job_id")
            and value["journal"].get("remote_state") not in api.TERMINAL
        ):
            return card(
                checkpoint(
                    store,
                    identifier,
                    value["epoch"],
                    state="queued",
                    message="Reconnecting to the same preparation allocation…",
                )
            )
        _current(store, credentials, value["connection_fingerprint"])
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            fresh = _get(db, identifier)
            if fresh != value:
                raise Conflict("Preparation changed. Refresh Activity before retrying.")
            existing = db.execute(
                "SELECT id FROM runtime_preparations WHERE archived=0 AND "
                "json_extract(CASE WHEN json_valid(frozen) THEN frozen END,'$.retry_of')=? "
                "ORDER BY created DESC LIMIT 1",
                (identifier,),
            ).fetchone()
            if not existing:
                for row in db.execute("SELECT id FROM runtime_preparations WHERE archived=0"):
                    try:
                        _get(db, row[0])
                    except Conflict:
                        raise Conflict(
                            "Resolve unverified preparation evidence before another allocation."
                        ) from None
            successor = (
                _update(db, _get(db, existing[0]))
                if existing
                else _insert(
                    db, value["frozen"], value["connection_fingerprint"], retry_of=identifier
                )
            )
        return card(successor)


@contextmanager
def _client(credentials, value):
    _owned(value)
    settings = providers.Settings.model_validate(value["frozen"]["settings"])
    key = credentials.get(providers.credential_scope(settings))
    native.require_setup(settings, key)
    with native.client_for(settings, key) as client:
        yield client, settings


def reconcile(store, credentials, identifier, *, confirm_not_created=False, fingerprint=None):
    with _control(identifier):
        value = get(store, identifier, owned=True)
        if not card(value)["can_reconcile"]:
            raise Conflict("Only an inactive uncertain preparation can be reconciled.")
        matches, cursor = [], None
        with _client(credentials, value) as (client, _):
            for _ in range(100):
                page = native.cloud_call(client.browse_jobs, items_per_page=100, next_page=cursor)
                if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                    raise Conflict(
                        "UCloud job discovery was incomplete; allocation remains uncertain."
                    )
                matches += [
                    item
                    for item in page["items"]
                    if isinstance(item, dict)
                    and item.get("specification", {}).get("name") == value["journal"]["run_name"]
                ]
                cursor = page.get("next")
                if not cursor:
                    break
            else:
                raise Conflict("Job discovery exceeded its bound; allocation remains uncertain.")
            if len(matches) > 1:
                raise Conflict("Several allocations match this preparation; keep it unresolved.")
            expected = value["journal"].get("specification")
            if matches:
                remote = native.cloud_call(client.retrieve_job, matches[0]["id"])
                if not expected or remote.get("specification") != expected:
                    raise Conflict("The discovered allocation does not match this preparation.")
                update = {
                    "job_id": str(remote["id"]),
                    "remote_state": remote.get("status", {}).get("state"),
                }
                status = "found"
            else:
                observed = runtime_store._digest(
                    {"id": identifier, "epoch": value["epoch"], "journal": value["journal"]}
                )
                if not confirm_not_created:
                    return {
                        "status": "not-found",
                        "requires_confirmation": True,
                        "fingerprint": observed,
                        "operation": card(value),
                    }
                if fingerprint != observed:
                    raise Conflict("Absence confirmation is stale. Reconcile again.")
                update = {"reconciled_not_created": True, "no_post": True}
                status = "not-found"
        update.update(_release({**value["journal"], **update}))
        fresh = checkpoint(
            store,
            identifier,
            value["epoch"],
            journal=update,
            **({"state": "queued"} if status == "found" else {}),
        )
        return {"status": status, "requires_confirmation": False, "operation": card(fresh)}


def cleanup(store, credentials, identifier):
    with _control(identifier):
        value = get(store, identifier, owned=True)
        _owned(value)
        if not card(value)["can_cleanup"]:
            raise Conflict("Confirm this preparation's terminal state before cleanup.")
        with _client(credentials, value) as (client, settings):
            remote = api.run_folder(settings, identifier)
            if value["journal"].get("owned_folder") != remote:
                raise Conflict("This preparation has no exact owned run-folder receipt.")
            # The owner never publishes private inputs. Shared runtime/model roots are not
            # accepted here. Keep the obligation if terminal status or Trash cannot be proved.
            if value["journal"].get("job_id"):
                state = api.remote_state(client, value["journal"]["job_id"])
                if state not in api.TERMINAL:
                    raise Conflict("The preparation allocation is still active.")
            native.trash_owned_folder(client, remote)
        return card(
            checkpoint(store, identifier, value["epoch"], journal={"cleanup_state": "trashed"})
        )


def hide(store, identifier):
    with _control(identifier), store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        raw = _raw(db, identifier)
        if raw["archived"]:
            db.execute(
                "UPDATE runtime_preparations SET hidden_updated=updated WHERE id=?", (identifier,)
            )
            return {"hidden": True}
        value = _get(db, identifier, owned=True)
        if not card(value)["can_remove"]:
            raise Conflict("Unresolved preparation stays in Activity.")
        db.execute(
            "UPDATE runtime_preparations SET hidden_updated=updated WHERE id=?", (identifier,)
        )
    return {"hidden": True}


class Run:
    def __init__(self, worker, value):
        self.worker, self.store, self.value = worker, worker.store, value
        self.identifier, self.epoch = value["id"], value["epoch"]

    def checkpoint(self, **fields):
        self.value = checkpoint(self.store, self.identifier, self.epoch, **fields)
        return self.value

    def guard(self, *, submitting=False):
        if self.worker.stopping.is_set():
            raise Interrupted()
        fresh = get(self.store, self.identifier)
        _owned(fresh)
        if fresh["epoch"] != self.epoch:
            raise Interrupted()
        if fresh["journal"].get("stop_intent"):
            raise Interrupted()
        if submitting:
            _current(self.store, self.worker.credentials, fresh["connection_fingerprint"])

    def execute(self):
        try:
            with _client(self.worker.credentials, self.value) as (self.client, self.settings):
                if not self.value["journal"].get("job_id"):
                    if (
                        self.value["frozen"].get("runtime_id") != api.runtime_id()
                        or self.value["frozen"].get("recipe_sha256") != recipe_hash()
                    ):
                        raise Conflict("The runtime recipe changed. Start a new preparation.")
                    if self.value["journal"].get("stop_intent"):
                        raise Interrupted()
                    self.submit()
                if self.value["state"] != "completed":
                    self.poll()
        except Interrupted:
            fresh = get(self.store, self.identifier)
            if fresh["epoch"] != self.epoch:
                return
            if fresh["state"] in {"completed", "cancelled"}:
                return
            stopping = self.worker.stopping.is_set()
            self.checkpoint(
                state="interrupted" if stopping else "cancelled",
                journal=_release(fresh["journal"]),
                message="Preparation stopped locally; recorded remote obligations are retained.",
            )
        except Exception:
            fresh = get(self.store, self.identifier)
            if fresh["state"] in {"completed", "cancelled"}:
                return
            self.checkpoint(
                state="failed",
                message=(
                    "UCloud preparation failed. Its allocation evidence was kept; inspect Activity "
                    "and reconcile an uncertain submission before retrying."
                ),
            )

    def submit(self):
        self.guard(submitting=True)
        if runtime_store._uncertain(self.value["journal"]):
            raise Conflict("Submission outcome is uncertain; reconcile it before retrying.")
        if api.stamped(self.client, self.settings):
            self.checkpoint(
                state="completed",
                progress=1,
                message="The base UCloud runtime is already prepared.",
            )
            return
        compute = runtime_compute.freeze(self.settings, self.worker.credentials)
        product = runtime_compute.frozen_product(compute)
        if product != runtime_compute.MIG_PRODUCT:
            raise Conflict("Preparation requires the exact funded CBS Runtime MIG identity.")
        application = api.script_application(self.client)
        # The existing specification builder consumes only the allocation duration here;
        # no recording-level ASR/alignment request is manufactured.
        options = SimpleNamespace(timeout_minutes=1)
        specification = api.build_script_specification(
            self.settings,
            application,
            self.identifier,
            options,
            True,
            runtime_product=product,
        )
        specification["name"] = self.value["journal"]["run_name"]
        self.checkpoint(journal={"specification": specification, **compute})
        from . import runtime_batches

        runtime_batches.require_preparation_idle(self.store, self.settings, self.client)
        self.guard(submitting=True)
        api.ensure_folder(
            self.client, self.settings.ucloud_transcriber.storage_path + "/cbs-runtime"
        )
        api.ensure_folder(self.client, api.runtime_folder(self.settings))
        base = self.store.root / "runtime-preparations"
        base.mkdir(exist_ok=True)
        if base.is_symlink() or base.resolve() != self.store.root / "runtime-preparations":
            raise Conflict("The local preparation staging folder is not owned by this library.")
        folder = base / self.identifier
        folder.mkdir()  # New owner, new staging folder; never adopt or overwrite a collision.
        runtime_batches.ensure_use_lock(
            self.client, self.settings, folder, lambda: self.guard(submitting=True)
        )
        remote = api.run_folder(self.settings, self.identifier)
        self.guard(submitting=True)
        self.client.create_folder(remote)  # conflicts never establish ownership
        self.checkpoint(journal={"owned_folder": remote, "cleanup_state": "pending"})
        files = [(path, path.name) for path in api.WORKER_FILES]
        supervisor = folder / "supervisor.sh"
        supervisor.write_text(
            api.supervisor_script(self.settings, self.identifier, True, prepare_only=True),
            encoding="utf-8",
            newline="\n",
        )
        requirements = folder / "requirements.txt"
        requirements.write_bytes(api.requirements_bytes())
        files += [(supervisor, "supervisor.sh"), (requirements, "requirements.txt")]
        self.checkpoint(message="Uploading public runtime preparation scripts…", progress=0.1)
        for path, name in files:
            self.guard(submitting=True)
            native.upload(
                self.client,
                self.settings.base_url,
                path,
                remote + "/" + name,
                lambda: self.guard(submitting=True),
                lambda **_: None,
            )
        self.checkpoint(journal={"scripts_uploaded": True})
        while True:
            self.guard(submitting=True)
            if begin_submission(self.store, self.identifier, self.epoch):
                self.checkpoint(journal={"deadline_at": time.time() + api.PREPARE_SECONDS + 300})
                # Committing intent accepts this one request. Stop can record its intent
                # during the call; drain the reply and then stop only that recorded ID.
                remote_id = api.extract_job_id(
                    native.cloud_call(self.client.submit_job, specification)
                )
                self.checkpoint(journal={"job_id": remote_id})
                return
            self.checkpoint(
                message="Waiting for a free UCloud runtime allocation slot…", progress=0.15
            )
            self.worker.stopping.wait(api.POLL_SECONDS)

    def poll(self):
        if self.value["state"] == "completed":
            return
        remote = api.run_folder(self.settings, self.identifier)
        while True:
            if self.worker.stopping.is_set():
                raise Interrupted()
            fresh = get(self.store, self.identifier)
            if fresh["epoch"] != self.epoch:
                raise Interrupted()
            run = fresh["journal"]
            deadline = run.get("deadline_at")
            if type(deadline) in (int, float) and time.time() > deadline:
                fresh = self.checkpoint(
                    journal={"stop_intent": True},
                    message="Preparation exceeded its time limit; stopping its allocation…",
                )
                run = fresh["journal"]
            if run.get("stop_intent"):
                try:
                    with _control(self.identifier):
                        self.value = _observe_stop(
                            self.store, self.client, self.identifier, self.epoch, run["job_id"]
                        )
                except ControlBusy:
                    self.worker.stopping.wait(api.POLL_SECONDS)
                    continue
                if self.value["state"] == "cancelled":
                    return
                self.worker.stopping.wait(api.POLL_SECONDS)
                continue
            state = api.remote_state(self.client, run["job_id"])
            update = {"remote_state": state, **_release({**run, "remote_state": state})}
            self.checkpoint(journal=update)
            status = (
                api.read_json(self.client, self.settings.base_url, remote + "/supervisor.json")
                or {}
            )
            if status and status.get("generation") != self.identifier:
                raise Conflict("The preparation status belongs to another run.")
            phase = status.get("phase")
            if state in api.TERMINAL:
                if run.get("stop_intent"):
                    self.checkpoint(
                        state="cancelled",
                        message="UCloud preparation stopped; public run files await cleanup.",
                    )
                    return
                if phase == "failed":
                    code = status.get("code")
                    reason = (
                        api.FAILURES.get(code)
                        if isinstance(code, str)
                        and code in {"runtime-busy", "runtime-prepare", "runtime"}
                        else None
                    )
                    if reason:
                        self.checkpoint(state="failed", message=reason)
                        return
                if phase != "finished" or state != "SUCCESS":
                    raise Conflict("The UCloud preparation allocation did not finish successfully.")
                if self.value["frozen"].get("runtime_id") != api.runtime_id():
                    raise Conflict("This allocation prepared an earlier runtime recipe.")
                doctor = (
                    api.read_json(self.client, self.settings.base_url, remote + "/doctor.json")
                    or {}
                )
                if doctor.get("ready") is not True or doctor.get("version") != api.WHISPERX_VERSION:
                    raise Conflict("The prepared runtime failed its package readiness check.")
                runtime_compute.require_gpu_memory(doctor)
                if not api.stamped(self.client, self.settings):
                    raise Conflict("The prepared runtime stamp is unavailable.")
                self.checkpoint(
                    state="completed",
                    progress=1,
                    journal={"prepared": True, "doctor_verified": True},
                    message="Base UCloud runtime prepared and CUDA checked; "
                    "public run files await cleanup.",
                )
                return
            self.checkpoint(
                message=(
                    "Preparing the UCloud runtime (20+ minutes can be needed)…"
                    if phase == "preparing"
                    else "Waiting for UCloud preparation to finish…"
                ),
                progress=0.5 if phase == "preparing" else 0.25,
            )
            self.worker.stopping.wait(api.POLL_SECONDS)
