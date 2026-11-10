"""Internal source inspection lifecycle (ADR 0082); no scheduling or provider admission."""

import copy
import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import audio_inputs
from .media import Cancelled
from .store import new_id, now

KIND = "source-inspect"
VERSION = 1
MAX_CACHE_BYTES = 65536
_ID = re.compile(r"[0-9a-f]{32}")
_SHA = re.compile(r"[0-9a-f]{64}")
_FIELDS = {
    "version",
    "generation",
    "state",
    "inspection_job_id",
    "epoch",
    "updated_at",
    "cancel_requested",
    "anchor",
    "receipt",
    "receipt_sha256",
    "error",
}
_ERRORS = {
    "cancelled": "Original inspection was cancelled.",
    "source_changed": "The original differs from its observed bytes. Preserve the recording.",
    "decode_failed": (
        "Original inspection could not validate this source within its supported limits."
    ),
    # Reading can fail transiently (locks, load, a moved file); that says nothing about
    # the source's format. Existing decode_failed caches keep their exact message.
    "read_failed": (
        "The original could not be read. Check that the file is still available, "
        "then retry the inspection."
    ),
}


class InspectionError(ValueError):
    """Missing or contradictory inspection proof must not be silently replaced."""


class StaleInspection(InspectionError):
    """This operation no longer owns the generation and epoch it observed."""


def _invalid():
    raise InspectionError("Original inspection evidence is invalid; preserve the library.")


def _lease(store):
    # The lifespan thread owns the RLock; workers only check its same-Store lease depth.
    if store._lease_depth <= 0:
        raise RuntimeError(
            "Original inspection requires the caller-held library lease until drain."
        )


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def _canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, RecursionError):
        _invalid()


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _parse(raw):
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CACHE_BYTES:
            _invalid()
        depth, quoted, escaped = 0, False, False
        for char in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "[{":
                depth += 1
                if depth > 12:
                    _invalid()
            elif char in "]}":
                depth -= 1
                if depth < 0:
                    _invalid()
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _invalid())
    except (ValueError, TypeError, RecursionError, UnicodeError):
        _invalid()


def _anchor(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"sha256", "bytes", "format"}
        or not isinstance(value["sha256"], str)
        or not _SHA.fullmatch(value["sha256"])
        or type(value["bytes"]) is not int
        or not 0 < value["bytes"] <= audio_inputs.MAX_BYTES
        or not isinstance(value["format"], str)
        or value["format"] not in audio_inputs._FORMATS
    ):
        _invalid()
    return copy.deepcopy(value)


def _validate(value, recording_id, filename):
    if (
        not isinstance(value, dict)
        or set(value) != _FIELDS
        or type(value["version"]) is not int
        or value["version"] != VERSION
        or not _identifier(value["generation"])
        or not _identifier(value["inspection_job_id"])
        or type(value["epoch"]) is not int
        or not 1 <= value["epoch"] <= 2**53
        or not isinstance(value["state"], str)
        or value["state"] not in {"pending", "running", "ready", "error"}
        or type(value["cancel_requested"]) is not bool
        or not isinstance(value["updated_at"], str)
        or not 0 < len(value["updated_at"]) <= 40
    ):
        _invalid()
    try:
        stamp = datetime.fromisoformat(value["updated_at"])
        if stamp.utcoffset() is None:
            _invalid()
    except ValueError:
        _invalid()
    anchor = _anchor(value["anchor"]) if value["anchor"] is not None else None
    if anchor and anchor["format"] != Path(filename).suffix.lower().lstrip("."):
        _invalid()
    if value["state"] == "ready":
        try:
            receipt = audio_inputs.validate(value["receipt"])
        except (ValueError, TypeError, RecursionError, OverflowError):
            _invalid()
        if (
            not anchor
            or receipt["recording_id"] != recording_id
            or {key: receipt[key] for key in anchor} != anchor
            or receipt["duration_basis"] != "decoded_pcm_frames"
            or value["receipt_sha256"] != _digest(receipt)
            or value["error"] is not None
            or value["cancel_requested"]
        ):
            _invalid()
    elif value["receipt"] is not None or value["receipt_sha256"] is not None:
        _invalid()
    if value["state"] == "error":
        error = value["error"]
        if (
            not isinstance(error, dict)
            or set(error) != {"code", "message"}
            or not isinstance(error["code"], str)
            or error["code"] not in _ERRORS
            or error["message"] != _ERRORS[error["code"]]
            or value["cancel_requested"] != (error["code"] == "cancelled")
        ):
            _invalid()
    elif value["error"] is not None:
        _invalid()
    if value["state"] == "pending" and value["cancel_requested"]:
        _invalid()
    return value


def _params(cache):
    return {
        "inspection": {
            "version": VERSION,
            "generation": cache["generation"],
            "epoch": cache["epoch"],
        }
    }


def _load(db, recording_id):
    if not _identifier(recording_id):
        _invalid()
    row = db.execute(
        "SELECT filename,length(CAST(original_input AS BLOB)) AS size FROM recordings WHERE id=?",
        (recording_id,),
    ).fetchone()
    if row is None:
        raise KeyError(recording_id)
    if row["size"] is None:
        from . import restored_owners

        if db.execute(
            "SELECT 1 FROM jobs WHERE recording_id=? AND kind=? AND "
            + restored_owners.sql_exclusion(db, "job", "jobs.id")
            + " LIMIT 1",
            (recording_id, KIND),
        ).fetchone():
            _invalid()
        return None
    if row["size"] > MAX_CACHE_BYTES:
        _invalid()
    raw = db.execute(
        "SELECT original_input FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()[0]
    cache = _validate(_parse(raw), recording_id, row["filename"])
    job = db.execute(
        "SELECT recording_id,kind,state,length(CAST(params AS BLOB)) AS size,"
        "length(CAST(chunks AS BLOB)) AS chunks_size "
        "FROM jobs WHERE id=?",
        (cache["inspection_job_id"],),
    ).fetchone()
    expected_state = {"pending": "queued", "running": "running", "ready": "completed"}.get(
        cache["state"]
    )
    if expected_state is None:
        expected_state = "cancelled" if cache["error"]["code"] == "cancelled" else "failed"
    if (
        job is None
        or job["recording_id"] != recording_id
        or job["kind"] != KIND
        or job["state"] != expected_state
        or job["size"] > MAX_CACHE_BYTES
        or job["chunks_size"] != 2
    ):
        _invalid()
    job = db.execute(
        "SELECT params,chunks FROM jobs WHERE id=?", (cache["inspection_job_id"],)
    ).fetchone()
    if _canonical(_parse(job["params"])) != _canonical(_params(cache)) or job["chunks"] != "[]":
        _invalid()
    return cache


def read(store, recording_id):
    """Read verified metadata only; never touch a source or decoder."""
    with store.connection() as db:
        db.execute("BEGIN")
        return _load(db, recording_id)


def _write(db, recording_id, cache):
    from . import restored_owners

    restored_owners.require_owned(db, "job", cache["inspection_job_id"])
    recording = db.execute("SELECT filename FROM recordings WHERE id=?", (recording_id,)).fetchone()
    if recording is None:
        raise KeyError(recording_id)
    _validate(cache, recording_id, recording["filename"])
    raw = _canonical(cache)
    if len(raw.encode("utf-8")) > MAX_CACHE_BYTES:
        _invalid()
    db.execute("UPDATE recordings SET original_input=? WHERE id=?", (raw, recording_id))
    state = {"pending": "queued", "running": "running", "ready": "completed"}.get(cache["state"])
    if state is None:
        state = "cancelled" if cache["error"]["code"] == "cancelled" else "failed"
    message = {
        "pending": "Waiting for original inspection.",
        "running": "Inspecting original audio.",
        "ready": "Original audio inspection complete.",
    }.get(cache["state"], cache["error"]["message"] if cache["error"] else "")
    if cache["state"] == "running" and cache["cancel_requested"]:
        message = "Cancelling original inspection; waiting for I/O to stop."
    db.execute(
        "UPDATE jobs SET state=?,params=?,message=?,progress=?,updated=? WHERE id=?",
        (
            state,
            _canonical(_params(cache)),
            message,
            1 if state == "completed" else 0,
            cache["updated_at"],
            cache["inspection_job_id"],
        ),
    )


def ensure(store, recording_id):
    """Create/reuse one internal job without reading media; terminal states need explicit Retry."""
    _lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        return _ensure(db, recording_id)


def _ensure(db, recording_id):
    """Metadata-only composition seam; caller owns its lease and write transaction."""
    cache = _load(db, recording_id)
    from . import restored_owners

    if cache is not None and not restored_owners.is_restored(db, "job", cache["inspection_job_id"]):
        return cache
    previous_anchor = cache["anchor"] if cache else None
    stamp, job_id = now(), new_id()
    restored_owners.require_owned(db, "job", job_id)
    cache = {
        "version": VERSION,
        "generation": new_id(),
        "state": "pending",
        "inspection_job_id": job_id,
        "epoch": 1,
        "updated_at": stamp,
        "cancel_requested": False,
        "anchor": previous_anchor,
        "receipt": None,
        "receipt_sha256": None,
        "error": None,
    }
    db.execute(
        "INSERT INTO jobs(id,recording_id,kind,created,updated) VALUES (?,?,?,?,?)",
        (job_id, recording_id, KIND, stamp, stamp),
    )
    _write(db, recording_id, cache)
    return cache


def _token(recording_id, cache):
    return {
        "recording_id": recording_id,
        "job_id": cache["inspection_job_id"],
        "generation": cache["generation"],
        "epoch": cache["epoch"],
    }


def _current(db, token):
    from . import restored_owners

    if (
        not isinstance(token, dict)
        or set(token) != {"recording_id", "job_id", "generation", "epoch"}
        or any(not _identifier(token[key]) for key in ("recording_id", "job_id", "generation"))
        or type(token["epoch"]) is not int
        or not 1 <= token["epoch"] <= 2**53
    ):
        _invalid()
    restored_owners.require_owned(db, "job", token["job_id"])
    cache = _load(db, token["recording_id"])
    if cache is None or _token(token["recording_id"], cache) != token:
        raise StaleInspection("Original inspection changed; refresh its current state.")
    return cache


def claim(store, job_id=None):
    from . import restored_owners

    _lease(store)
    if job_id is not None and not _identifier(job_id):
        _invalid()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id,recording_id FROM jobs WHERE kind=? AND state='queued' "
            + "AND "
            + restored_owners.sql_exclusion(db, "job", "jobs.id")
            + " "
            + ("AND id=? " if job_id is not None else "")
            + "ORDER BY created,id LIMIT 1",
            (KIND, job_id) if job_id is not None else (KIND,),
        ).fetchone()
        if row is None:
            return None
        cache = _load(db, row["recording_id"])
        if cache is None or cache["inspection_job_id"] != row["id"]:
            _invalid()
        cache.update(state="running", epoch=cache["epoch"] + 1, updated_at=now())
        _write(db, row["recording_id"], cache)
        return _token(row["recording_id"], cache)


def _for_job(db, job_id, generation, epoch):
    from . import restored_owners

    restored_owners.require_owned(db, "job", job_id)
    row = db.execute("SELECT recording_id,kind FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    if row["kind"] != KIND:
        _invalid()
    token = {
        "recording_id": row["recording_id"],
        "job_id": job_id,
        "generation": generation,
        "epoch": epoch,
    }
    return token, _current(db, token)


def _error(cache, code):
    cache.update(
        state="error",
        error={"code": code, "message": _ERRORS[code]},
        receipt=None,
        receipt_sha256=None,
        cancel_requested=code == "cancelled",
        updated_at=now(),
    )


def cancel(store, job_id, *, generation, epoch):
    _lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        token, cache = _for_job(db, job_id, generation, epoch)
        if cache["state"] == "ready" or cache["state"] == "error":
            return cache
        if cache["state"] == "pending":
            cache["epoch"] += 1
            _error(cache, "cancelled")
        else:
            cache.update(cancel_requested=True, updated_at=now())
        _write(db, token["recording_id"], cache)
        return cache


def retry(store, job_id, *, generation, epoch):
    _lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        token, cache = _for_job(db, job_id, generation, epoch)
        if cache["state"] != "error":
            raise InspectionError("Only a failed or cancelled original inspection can be retried.")
        cache.update(
            state="pending",
            epoch=cache["epoch"] + 1,
            updated_at=now(),
            cancel_requested=False,
            error=None,
        )
        _write(db, token["recording_id"], cache)
        return cache


def recover(store, *, job_ids=None):
    """Explicit restart recovery under the new server's lease; never called by queue startup."""
    _lease(store)
    if job_ids is not None and (
        not isinstance(job_ids, list)
        or not 1 <= len(job_ids) <= 100
        or any(not _identifier(identifier) for identifier in job_ids)
    ):
        _invalid()
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            "SELECT id,recording_id FROM jobs WHERE kind=? AND state='running'"
            + (
                " AND id IN (" + ",".join("?" for _ in job_ids) + ")" if job_ids is not None else ""
            ),
            (KIND, *(job_ids or [])),
        ).fetchall()
        recovered = 0
        for row in rows:
            from . import restored_owners

            if restored_owners.is_restored(db, "job", row["id"]):
                continue
            cache = _load(db, row["recording_id"])
            if cache is None or cache["inspection_job_id"] != row["id"]:
                _invalid()
            cache.update(epoch=cache["epoch"] + 1, updated_at=now())
            if cache["cancel_requested"]:
                _error(cache, "cancelled")
            else:
                cache["state"] = "pending"
            _write(db, row["recording_id"], cache)
            recovered += 1
        return recovered


class _Cancellation:
    def __init__(self, store, token, event):
        self.store, self.token, self.event = store, token, event
        self.next_poll, self.cancelled = 0, False

    def is_set(self):
        if self.cancelled or self.event.is_set():
            return True
        if time.monotonic() >= self.next_poll:
            _lease(self.store)
            with self.store.connection() as db:
                db.execute("BEGIN")
                cache = _current(db, self.token)
                self.cancelled = cache["cancel_requested"] or cache["state"] != "running"
            self.next_poll = time.monotonic() + 0.05
        return self.cancelled

    def wait(self, timeout):
        return self.event.wait(timeout) or self.is_set()


def run(store, token, cancel=None):
    """Drain one claimed inspector; caller must retain its existing lease until return."""
    _lease(store)
    token = copy.deepcopy(token)
    cancellation = _Cancellation(store, token, cancel if cancel is not None else threading.Event())

    def observe(identity):
        _lease(store)
        identity = _anchor(identity)
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cache = _current(db, token)
            if cache["state"] != "running":
                raise Cancelled()
            if cache["anchor"] is not None and cache["anchor"] != identity:
                raise _ChangedSource()
            cache.update(anchor=identity, updated_at=now())
            _write(db, token["recording_id"], cache)
            cancelled = cache["cancel_requested"]
        # A completed observation survives Cancel; raising inside the transaction rolls it back.
        if cancelled or cancellation.event.is_set():
            raise Cancelled()

    try:
        if cancellation.is_set():
            raise Cancelled()
        recording = store.one(
            "SELECT id,filename,duration FROM recordings WHERE id=?", (token["recording_id"],)
        )
        receipt = audio_inputs.freeze(store, recording, cancellation, identity_observer=observe)
        error = None
    except StaleInspection:
        return None
    except Cancelled:
        receipt, error = None, "cancelled"
    except _ChangedSource:
        receipt, error = None, "source_changed"
    except OSError:
        receipt, error = None, "read_failed"
    except ValueError:
        receipt, error = None, "decode_failed"
    _lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            cache = _current(db, token)
        except StaleInspection:
            return None
        if cache["state"] != "running":
            return None
        if cache["cancel_requested"] or cancellation.event.is_set():
            error = "cancelled"
        if error:
            _error(cache, error)
        else:
            receipt = audio_inputs.validate(receipt)
            if (
                receipt["recording_id"] != token["recording_id"]
                or cache["anchor"] is None
                or {key: receipt[key] for key in cache["anchor"]} != cache["anchor"]
                or receipt["duration_basis"] != "decoded_pcm_frames"
            ):
                _invalid()
            cache.update(
                state="ready", receipt=receipt, receipt_sha256=_digest(receipt), updated_at=now()
            )
        _write(db, token["recording_id"], cache)
        return cache


class _ChangedSource(Exception):
    pass


def fail(store, token):
    """A processor may settle an unexpected failure only after its owned I/O has drained."""
    _lease(store)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            cache = _current(db, token)
        except StaleInspection:
            return None
        if cache["state"] != "running":
            return None
        _error(cache, "cancelled" if cache["cancel_requested"] else "decode_failed")
        _write(db, token["recording_id"], cache)
        return cache
