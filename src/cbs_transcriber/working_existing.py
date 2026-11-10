"""Designate a proven untouched sole existing result as Working (ADR 0141).

ADR 0133 designates a new first result when it is created. Results saved earlier, or by legacy
unmarked jobs, stay unassigned. At startup this pass proves each candidate inside one SQLite
writer transaction and appends the same system `working_auto_first` event through
`result_roles.move_automatic`. It never writes versions, projections, jobs or authorities.

Project snapshots then follow through the ordinary `Store.snapshot_project` path, only for a
verified ID-owned, link-free project folder. Staleness is derived from the snapshot's actual
Working roles versus SQLite, so a snapshot interrupted after the database commit is retried at
the next start regardless of file timestamps. Unverified or failed updates stay visible as the
project's snapshot warning. No new persistence marker exists.
"""

import json
import os
import sqlite3
import stat
import time
from pathlib import Path

from . import result_roles
from .history import encode_payload
from .transcripts import MAX_TRANSCRIPT_BYTES

PAGE = 128  # candidates examined in one writer transaction
PAGE_BYTES = 8 * 1024 * 1024  # transcript bytes proved in one writer transaction
STARTUP_SECONDS = 10  # designation budget; remaining candidates continue at the next start
SNAPSHOT_SECONDS = 10  # project snapshot budget; remaining projects stay visibly pending
DISCOVERY_SECONDS = 2  # bound for finding projects with automatic designations
SNAPSHOT_CHECK_BYTES = MAX_TRANSCRIPT_BYTES  # larger snapshots require an ordinary explicit save
BUSY_MILLISECONDS = 1000  # never wait the ordinary 15 s for another writer at startup
CREATIONS = ("provider_result", "imported_result")
UNVERIFIED = (
    "Working versions were set in the library database, but the project folder or existing "
    "snapshot could not be verified within the supported checks, so project.json and exports "
    "were not updated. Check the folder and snapshot, then save again."
)
PENDING = (
    "Working versions were set in the library database, but project.json and exports are not "
    "updated yet. They are retried at the next start, or save again."
)
# Evidence errors leave one recording for explicit review; they never mean "absent".
SKIPPED = (
    ValueError,
    TypeError,
    KeyError,
    RuntimeError,
    OverflowError,
    RecursionError,
    UnicodeError,
    sqlite3.IntegrityError,
)
# A pass that cannot complete is retried later; it never blocks startup.
ABORTED = (sqlite3.Error, OSError, *SKIPPED)

# Recordings with an active result and no role row or event at all, in key order. Any
# earlier decision, including an explicit clear or keep, excludes the recording forever.
_CANDIDATES = (
    "SELECT r.id FROM recordings r WHERE r.id>? AND r.active_transcript_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM recording_result_roles q WHERE q.recording_id=r.id) "
    "AND NOT EXISTS (SELECT 1 FROM result_role_events e WHERE e.recording_id=r.id) "
    "ORDER BY r.id LIMIT ?"
)
# Processing that may still create a result, or that carries frozen ADR 0133 authority
# (including stopped intents awaiting Prepare and retryable failed jobs).
_BLOCKING = (
    "SELECT DISTINCT j.recording_id FROM jobs j WHERE j.state NOT IN "
    "('completed','failed','cancelled') OR typeof(j.state)!='text' "
    "UNION SELECT a.recording_id FROM working_job_authorities a "
    "LEFT JOIN jobs j ON j.id=a.job_id WHERE j.state IS NOT 'completed'"
)


def _unassigned():
    return {"revision": 0, "working": None}


def library_refusal(store, db):
    """Restored or unverifiable libraries are never designated (no per-recording guessing)."""
    try:
        os.lstat(store.root / "preservation")
    except FileNotFoundError:
        pass
    except OSError:
        return "restore_evidence_unreadable"
    else:
        # Every supported complete restore retains this tree, even with no restored jobs.
        # Any entry, including a link or reparse point, counts as restore evidence.
        return "restored_library"
    from .store import SCHEMA_VERSION

    if SCHEMA_VERSION != 18 or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        return "unsupported_schema"
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restored_owners'"
    ).fetchone():
        return "restore_evidence_unreadable"
    if db.execute("SELECT 1 FROM restored_owners LIMIT 1").fetchone():
        return "restored_library"
    return None


def _bounded(db, deadline):
    """Every SQLite statement stops at the deadline and lock waits stay short."""
    db.execute(f"PRAGMA busy_timeout={BUSY_MILLISECONDS}")
    if deadline is not None:
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 2000)


def _structure(db, recording_id):
    """Identifier-only checks: return (result_id, version_id) or a skip reason string.

    Columns stored after a transcript body are read only for the single remaining version,
    so permanently ineligible recordings never load transcript bodies at each startup.
    """
    recording = db.execute(
        "SELECT active_transcript_id FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    if recording is None or recording["active_transcript_id"] is None:
        return "no_active_result"
    # Trashed results count: a removed alternative means this was never the only choice.
    results = db.execute(
        "SELECT id FROM transcript_results WHERE recording_id=? LIMIT 2", (recording_id,)
    ).fetchall()
    if len(results) != 1:
        return "results"
    result_id = results[0]["id"]
    if result_id != recording["active_transcript_id"]:
        return "projection"
    if db.execute("SELECT 1 FROM result_trash WHERE result_id=?", (result_id,)).fetchone():
        return "trash"
    versions = db.execute(
        "SELECT id,sequence,predecessor_id,source_version_id FROM result_versions "
        "WHERE result_id=? LIMIT 2",
        (result_id,),
    ).fetchall()
    if len(versions) != 1:
        return "versions"
    version = versions[0]
    action = db.execute("SELECT action FROM result_versions WHERE id=?", (version["id"],))
    if (
        type(version["sequence"]) is not int
        or version["sequence"] != 1
        or version["predecessor_id"] is not None
        or version["source_version_id"] is not None
        or action.fetchone()[0] not in CREATIONS
    ):
        # Legacy observations, manual first saves and derived results are not proven here.
        return "not_creation"
    return result_id, version["id"]


def _bounded_text(row, *names):
    return all(
        row[f"{name}_type"] == "text"
        and type(row[f"{name}_size"]) is int
        and row[f"{name}_size"] <= MAX_TRANSCRIPT_BYTES
        for name in names
    )


def _sized(*columns):
    return ",".join(
        f"typeof({c}) AS {c}_type,length(CAST({c} AS BLOB)) AS {c}_size" for c in columns
    )


def _untouched(segments):
    """Every passage still carries its producer baseline: never edited text/words/timing."""
    if not isinstance(segments, list) or not segments:
        return False
    for segment in segments:
        if not isinstance(segment, dict) or segment.get("edited") is not False:
            return False
        for current, original in (
            ("text", "original_text"),
            ("words", "original_words"),
            ("word_timing", "original_word_timing"),
        ):
            if (
                current not in segment
                or original not in segment
                or segment[current] != segment[original]
            ):
                return False
    return True


def _evidence(db, recording_id, result_id, version_id):
    """Return (bytes, None) when both projections equal an untouched creation payload."""
    recording = db.execute(
        f"SELECT {_sized('segments', 'speakers')} FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    result = db.execute(
        f"SELECT {_sized('segments', 'speakers')} FROM transcript_results WHERE id=?",
        (result_id,),
    ).fetchone()
    version = db.execute(
        f"SELECT {_sized('payload')} FROM result_versions WHERE id=?", (version_id,)
    ).fetchone()
    if not (
        _bounded_text(recording, "segments", "speakers")
        and _bounded_text(result, "segments", "speakers")
        and _bounded_text(version, "payload")
    ):
        raise ValueError("The saved transcript evidence is invalid or too large.")
    payload = db.execute(
        "SELECT payload FROM result_versions WHERE id=?", (version_id,)
    ).fetchone()[0]
    saved = db.execute(
        "SELECT segments,speakers FROM transcript_results WHERE id=?", (result_id,)
    ).fetchone()
    mirror = db.execute(
        "SELECT segments,speakers FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    segments = json.loads(saved["segments"])
    if not _untouched(segments):
        return 0, "edited"
    # The saved result and the review projection both equal the exact creation payload.
    if encode_payload(segments, json.loads(saved["speakers"]))[0] != payload:
        return 0, "projection"
    if (mirror["segments"], mirror["speakers"]) != (saved["segments"], saved["speakers"]) and (
        encode_payload(json.loads(mirror["segments"]), json.loads(mirror["speakers"]))[0] != payload
    ):
        return 0, "projection"
    used = (
        recording["segments_size"]
        + recording["speakers_size"]
        + result["segments_size"]
        + result["speakers_size"]
        + version["payload_size"]
    )
    return used, None


def _designate(db, store, recording_id, blocked, summary):
    """Prove and write one recording under a savepoint of the caller's writer transaction."""
    used = 0
    db.execute("SAVEPOINT existing_working")
    try:
        found = _structure(db, recording_id)
        if isinstance(found, str):
            reason = found
        elif recording_id in blocked:
            reason = "pending_processing"
        elif result_roles.read(db, store, recording_id, verify_tail=True) != _unassigned():
            reason = "role"
        else:
            result_id, version_id = found
            used, reason = _evidence(db, recording_id, result_id, version_id)
            # The same writer, action and system actor as an ADR 0133 first result. It
            # re-reads the role and verifies the version's observation, size and hash.
            if reason is None and not result_roles.move_automatic(
                db, store, recording_id, version_id, _unassigned(), "working_auto_first"
            ):
                reason = "role"
        db.execute("RELEASE existing_working")
    except SKIPPED:
        db.execute("ROLLBACK TO existing_working")
        db.execute("RELEASE existing_working")
        reason = "invalid_evidence"
    if reason is None:
        summary["designated"] += 1
        project = db.execute("SELECT project_id FROM recordings WHERE id=?", (recording_id,))
        summary["projects"].add(project.fetchone()[0])
    else:
        summary["skipped"][reason] = summary["skipped"].get(reason, 0) + 1
    return used


def _summary():
    return {
        "designated": 0,
        "skipped": {},
        "pages": 0,
        "job_scans": 0,
        "incomplete": False,
        "refused": None,
        "aborted": None,
        "projects": set(),
    }


def designate_existing(store, *, page=PAGE, page_bytes=PAGE_BYTES, seconds=None):
    """Database pass only; idempotent and safe beside concurrent writers. Returns a summary.

    Each page is one BEGIN IMMEDIATE transaction that rechecks every fence it relies on and
    commits atomically. Unsettled processing is collected by one job-table scan, repeated
    only when another connection committed since the previous page (PRAGMA data_version,
    read at the start of each writer transaction). With a time budget, lock waits are short
    and every SQLite statement, including that scan, is interrupted at the deadline; an
    interrupted page rolls back and the remaining candidates continue at the next pass.
    """
    summary = _summary()
    deadline = None if seconds is None else time.monotonic() + seconds
    after, blocked, seen = "", None, None
    try:
        with store.connection() as db:
            _bounded(db, deadline)
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    summary["incomplete"] = True
                    return summary
                committed = (
                    summary["designated"],
                    set(summary["projects"]),
                    dict(summary["skipped"]),
                )
                db.execute("BEGIN IMMEDIATE")
                try:
                    summary["refused"] = library_refusal(store, db)
                    rows = (
                        db.execute(_CANDIDATES, (after, page)).fetchall()
                        if summary["refused"] is None
                        else []
                    )
                    if not rows:
                        db.rollback()
                        return summary
                    version = db.execute("PRAGMA data_version").fetchone()[0]
                    if blocked is None or version != seen:
                        blocked, seen = {row[0] for row in db.execute(_BLOCKING)}, version
                        summary["job_scans"] += 1
                    summary["pages"] += 1
                    used = 0
                    for row in rows:
                        used += _designate(db, store, row["id"], blocked, summary)
                        after = row["id"]
                        if used >= page_bytes or (
                            deadline is not None and time.monotonic() >= deadline
                        ):
                            break
                    db.set_progress_handler(None, 0)  # Never interrupt the commit itself.
                    db.commit()
                    _bounded(db, deadline)
                except BaseException:
                    # This page committed nothing; earlier pages are complete and durable.
                    db.set_progress_handler(None, 0)
                    db.rollback()
                    summary["designated"], summary["projects"], summary["skipped"] = committed
                    raise
    except ABORTED as error:
        summary["aborted"] = type(error).__name__
        summary["incomplete"] = deadline is not None
        return summary


def _redirect(info):
    from .deletion_files import _redirect as redirect

    return redirect(info)


def _owned_folder(db, project_id, storage_dir):
    """Prove the project's ID-owned, link-free folder and every snapshot output target."""
    from .store import containment_path

    if not isinstance(storage_dir, str) or not 0 < len(storage_dir) <= 4096:
        return False
    if "\x00" in storage_dir:
        return False
    folder = Path(storage_dir)
    if not folder.is_absolute() or os.path.abspath(folder) != storage_dir:
        return False
    # create_project and project moves always name the folder after the project's ID,
    # and no other project may claim the same folder.
    if (
        folder.name != project_id[:8]
        or db.execute(
            "SELECT 1 FROM projects WHERE storage_dir=? AND id<>? LIMIT 1",
            (storage_dir, project_id),
        ).fetchone()
    ):
        return False
    try:
        for item in (folder, *folder.parents):
            if _redirect(item.lstat()):
                return False
        if not stat.S_ISDIR(folder.lstat().st_mode) or containment_path(folder) != folder:
            return False
        targets = [
            (folder / "project.json", False),
            (folder / "project.pending", False),
            (folder / "exports", True),
        ]
        for recording_id, result_id in db.execute(
            "SELECT t.recording_id,t.id FROM transcript_results t JOIN recordings r "
            "ON r.id=t.recording_id WHERE r.project_id=? "
            "AND t.id NOT IN (SELECT result_id FROM result_trash)",
            (project_id,),
        ):
            output = folder / "exports" / recording_id
            targets += [
                (output, True),
                (output / f"{result_id}.txt", False),
                (output / f"{result_id}.pending", False),
            ]
        for path, directory in targets:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            kind = stat.S_ISDIR if directory else stat.S_ISREG
            if _redirect(info) or not kind(info.st_mode):
                return False
    except (OSError, ValueError):
        return False
    return True


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate project snapshot key.")
        value[key] = item
    return value


def _stale(db, store, project_id, storage_dir):
    """Compare actual roles; None means existing bytes cannot authorize a rewrite.

    Call only after _owned_folder. File timestamps are never evidence of successful
    publication: a copied or touched stale snapshot may have a newer timestamp.
    """
    path = Path(storage_dir) / "project.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    if _redirect(info) or not stat.S_ISREG(info.st_mode) or info.st_size > SNAPSHOT_CHECK_BYTES:
        return None
    try:
        with path.open("rb") as source:
            raw = source.read(SNAPSHOT_CHECK_BYTES + 1)
        if len(raw) > SNAPSHOT_CHECK_BYTES:
            return None
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            not isinstance(value, dict)
            or type(value.get("format_version")) is not int
            or value["format_version"] != 1
            or not isinstance(value.get("project"), dict)
            or value["project"].get("id") != project_id
            or not isinstance(value.get("recordings"), list)
        ):
            return None
        saved = {}
        for recording in value["recordings"]:
            if not isinstance(recording, dict) or not isinstance(recording.get("id"), str):
                return None
            identifier = recording["id"]
            if identifier in saved:
                return None
            role = recording.get("result_roles")
            if role is not None:
                result_roles.validate_frozen(role)
            saved[identifier] = role
        current = {
            row[0]: result_roles.read(db, store, row[0])
            for row in db.execute("SELECT id FROM recordings WHERE project_id=?", (project_id,))
        }
        return saved != current
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None


def reconcile_snapshots(store, *, touched=(), seconds=None):
    """Refresh stale snapshots of projects with automatic first designations.

    The ordinary snapshot writer runs only for a verified owned folder under the snapshot
    lock (project moves hold it too). Refused, failed or unreached projects keep a visible
    snapshot warning and are retried at the next start or by any save in the project.
    Legacy projects without a storage folder need no snapshot and get no warning.
    """
    deadline = None if seconds is None else time.monotonic() + seconds
    summary = {"refreshed": 0, "current": 0, "refused": 0, "failed": 0, "pending": 0}
    try:
        with store.connection() as db:
            # Discovery has its own short statement bound; the budget governs writes below.
            _bounded(db, time.monotonic() + DISCOVERY_SECONDS)
            db.execute("BEGIN")
            if library_refusal(store, db) is not None:
                return summary
            rows = db.execute(
                "SELECT DISTINCT r.project_id FROM result_role_events e "
                "JOIN recordings r ON r.id=e.recording_id WHERE e.action='working_auto_first' "
                "AND e.actor_kind='system' ORDER BY r.project_id"
            ).fetchall()
    except ABORTED:
        rows = [(project_id,) for project_id in sorted(touched)]
    for project_id in sorted({row[0] for row in rows} | set(touched)):
        try:
            with store.snapshot_lock:
                with store.connection() as db:
                    db.execute(f"PRAGMA busy_timeout={BUSY_MILLISECONDS}")
                    db.execute("BEGIN")
                    project = db.execute(
                        "SELECT storage_dir FROM projects WHERE id=?", (project_id,)
                    ).fetchone()
                    if project is None or not project["storage_dir"]:
                        continue
                    storage_dir = project["storage_dir"]
                    if deadline is not None and time.monotonic() >= deadline:
                        store.snapshot_errors[project_id] = PENDING
                        summary["pending"] += 1
                        continue
                    owned = _owned_folder(db, project_id, storage_dir)
                    stale = _stale(db, store, project_id, storage_dir) if owned else None
                    if stale is False:
                        summary["current"] += 1
                        continue
                if stale is None:
                    store.snapshot_errors[project_id] = UNVERIFIED
                    summary["refused"] += 1
                    continue
                # The same snapshot writer as an ordinary Working designation.
                store.snapshot_project(project_id)
                if store.snapshot_errors.get(project_id):
                    summary["failed"] += 1
                else:
                    summary["refreshed"] += 1
        except KeyError:
            continue  # Deleted meanwhile; nothing remains to describe.
        except ABORTED:
            store.snapshot_errors[project_id] = PENDING
            summary["failed"] += 1
    return summary


def startup(store, *, seconds=STARTUP_SECONDS, snapshot_seconds=SNAPSHOT_SECONDS):
    """Run after file recovery, inside the library lease and before workers start."""
    designation = designate_existing(store, seconds=seconds)
    snapshots = reconcile_snapshots(
        store, touched=designation.pop("projects"), seconds=snapshot_seconds
    )
    return {**designation, "snapshots": snapshots}
