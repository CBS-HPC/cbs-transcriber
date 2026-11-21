"""Server-owned local library backups shown in Activity (ADR 0192).

Make backup hands off at once: this owner reserves capture admission before its 202 reply,
waits for earlier changes, then runs the existing complete capture (ADR 0096) with progress
hooks. Stop is a signal that wins until the publication boundary, the point where the
independent creator reference is about to be written; after it the backup is finished or
its unresolved files are reported, never erased. A stopped or failed capture removes only
its own identity-bound staging folder, or names it when that cannot be proved safe.

Small notes in the library's `backup-tasks` folder carry outcomes and obligations across a
restart. They are observational: nothing resumes, recaptures or deletes because of a note.
Captures copy the database and managed files, never these notes, so a restored library
carries no backup task state.
"""

import errno
import hashlib
import json
import logging
import os
import re
import stat
import threading
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import HTTPException
from fastapi import Path as PathParameter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import portable_capture
from . import portable_transport as transport
from .store import now

FORMAT = "cbs-backup-task-v1"
FOLDER = "backup-tasks"
MAX_NOTES = 32
MAX_NOTE_BYTES = 32 * 1024
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_OBJECT = re.compile(r"(?:[0-9a-f]{64}|\.[0-9a-f]{32}\.pending)\Z")
_STAGING_FILES = frozenset(
    {"manifest.json", "COMMITTED.json"}
    | {
        name + suffix
        for name in (".snapshot.sqlite3", ".compacted.sqlite3")
        for suffix in ("", "-journal", "-wal", "-shm")
    }
)
_PUBLISHED = ("objects", "manifest.json", "COMMITTED.json")
STATES = ("running", "ready", "stopped", "failed", "interrupted", "attention")
PHASES = ("waiting", "snapshot", "inventory", "copy", "verify", "publishing", "settled")
ACTIVITY_STATES = {
    "running": "running",
    "ready": "completed",
    "stopped": "cancelled",
    "failed": "failed",
    "interrupted": "interrupted",
    "attention": "interrupted",
}
PHASE_MESSAGES = {
    "waiting": "Waiting for changes in progress to finish…",
    "snapshot": "Copying the library database…",
    "inventory": "Listing recordings and evidence files…",
    "copy": "Copying files…",
    "verify": "Checking the copied backup…",
    "publishing": "Saving the finished backup…",
}
LABELS = {
    "scope": "whole_library",
    "credentials": "excluded",
    "history_provenance": "domain_transform_validation_not_checked",
}
BUSY = "A backup is already being made. Follow it in Activity."
TOO_LATE = "It is too late to stop this backup: it is being saved. Its outcome appears in Activity."
INTERRUPTED = "The app closed before this backup finished. No backup was made."
UNREADABLE = "This backup note could not be read. Nothing was copied or removed because of it."
RESTART_PUBLISHING = (
    "The app closed while this backup was being saved. Its files were kept. "
    "Check again to confirm whether the backup is complete."
)
_MUTATIONS = {"POST", "PUT", "PATCH", "DELETE"}
_STOP_PATH = re.compile(r"/api/library/backup-tasks/[0-9a-f]{32}/stop\Z")
_CONTROL_PATH = re.compile(
    r"/api/library/backup-tasks/[0-9a-f]{32}/(?:stop|dismiss|remove-partial|check)\Z"
)


def stop_path(path):
    """The exact Stop signal; it must reach its task while capture excludes mutations."""
    return _STOP_PATH.match(path) is not None


def control_path(path):
    """Task controls touch only the task's note and its own destination folders."""
    return _CONTROL_PATH.match(path) is not None


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _exists(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


class StartRequest(BaseModel):
    # Strict: a retyped replay is refused, never coerced into the same accepted digest.
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    destination: str | None = Field(default=None, max_length=4000)


def install(app, tasks):
    task_id = Annotated[str, PathParameter(pattern=r"^[0-9a-f]{32}$")]

    @app.post("/api/library/backup-tasks", status_code=202)
    def start_backup(body: StartRequest):
        summary, _ = tasks.start(body.operation_id, body.destination)
        return summary

    @app.get("/api/library/backup-tasks")
    def backup_tasks():
        return {"tasks": tasks.summaries()}

    @app.get("/api/library/backup-tasks/{identifier}")
    def backup_task(identifier: task_id):
        return tasks.get(identifier)

    @app.post("/api/library/backup-tasks/{identifier}/stop")
    def stop_backup(identifier: task_id):
        # Exempt from capture and recovery admission: it only signals its own task.
        summary = tasks.stop(identifier)
        return JSONResponse(summary, 202 if summary["state"] == "running" else 200)

    @app.post("/api/library/backup-tasks/{identifier}/dismiss")
    def dismiss_backup(identifier: task_id):
        return tasks.dismiss(identifier)

    @app.post("/api/library/backup-tasks/{identifier}/remove-partial")
    def remove_partial_backup(identifier: task_id):
        return tasks.remove_partial(identifier)

    @app.post("/api/library/backup-tasks/{identifier}/check")
    def check_backup_task(identifier: task_id):
        return tasks.check(identifier)


@dataclass
class Task:
    identifier: str
    backup_id: str
    operation: str
    digest: str
    parent: str
    created: str
    updated: str
    state: str = "running"
    phase: str = "waiting"
    revision: int = 1
    published: bool = False
    staging: tuple | None = None
    kept: list = field(default_factory=list)
    removed: bool = False
    message: str = ""
    receipt: dict | None = None
    files_total: int | None = None
    files_done: int = 0
    bytes_total: int | None = None
    bytes_done: int = 0
    stop_requested: bool = False
    closing: bool = False
    cleaning: bool = False
    readable: bool = True
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    reservation: object = field(default=None, repr=False)
    exits: object = field(default=None, repr=False)
    thread: object = field(default=None, repr=False)

    @property
    def pending(self):
        return portable_capture.pending_path(self.parent, self.backup_id)

    @property
    def target(self):
        return Path(self.parent) / ("portable-" + self.backup_id)


class _Observer(portable_capture.Observer):
    """Bounded, in-memory progress; the note is written only at phase boundaries."""

    def __init__(self, owner, task):
        self.owner, self.task = owner, task

    def phase(self, name):
        self.owner._phase(self.task, name)

    def staged(self, path):
        info = path.lstat()
        with self.owner.lock:
            self.task.staging = (info.st_dev, info.st_ino)
            self.owner._touch(self.task)
        # Identity first, so a crash afterwards can still prove which folder this task owns.
        self.owner._persist(self.task)

    def totals(self, files, size):
        with self.owner.lock:
            task = self.task
            task.files_total, task.bytes_total = files, size
            task.files_done = task.bytes_done = 0
            self.owner._touch(task)

    def copied(self, size):
        with self.owner.lock:
            task = self.task
            before = self.owner._percent(task)
            task.bytes_done += size
            if self.owner._percent(task) != before:
                self.owner._touch(task)

    def copied_file(self):
        with self.owner.lock:
            self.task.files_done += 1
            self.owner._touch(self.task)

    def publish(self):
        self.owner._latch(self.task)


class BackupTasks:
    """One server-owned local backup at a time, bound to the serving Store and its lease."""

    def __init__(self, store, queue, capture_guard, *, acquire_file_lease=False):
        self.store, self.queue = store, queue
        self.capture_guard, self.acquire_file_lease = capture_guard, acquire_file_lease
        self.gate = None
        self.lock = threading.Condition(threading.RLock())
        # Serializes admission so an exact replay racing its own first request, and shutdown,
        # both see one settled decision.
        self.admission = threading.Lock()
        self.tasks = {}
        self.closed = False

    def attach(self, gate):
        self.gate = gate

    # ---------- Admission ----------

    def active(self):
        with self.lock:
            return any(task.state == "running" for task in self.tasks.values())

    def start(self, operation_id, destination):
        """Accept one backup request; return (summary, created)."""
        destination = destination.strip() if isinstance(destination, str) else None
        destination = destination or None
        operation = hashlib.sha256(operation_id.encode()).hexdigest()
        digest = _digest({"operation": operation_id, "destination": destination})
        with self.admission:
            with self.lock:
                if self.closed:
                    raise HTTPException(409, "This library is closing. No backup was started.")
                for task in self.tasks.values():
                    if task.operation == operation:
                        # A lost reply: the same request finds the same task, never a second one.
                        if task.digest != digest:
                            raise HTTPException(
                                409, "This backup request was already accepted with another folder."
                            )
                        return self.summary(task), False
                if any(task.state == "running" for task in self.tasks.values()):
                    raise HTTPException(409, BUSY)
                self._prune()
                if len(self.tasks) >= MAX_NOTES:
                    raise HTTPException(
                        409, "Dismiss earlier backup notes in Activity, then try again."
                    )
            if self.gate.busy():
                raise HTTPException(409, BUSY)
            with self._lease():
                refusal = portable_capture.admission_refusal(self.store, self.queue)
            if refusal:
                raise HTTPException(409, refusal)
            parent = portable_capture._parents(
                Path(destination).expanduser() if destination else self.store.root / "backups"
            )
            backup_id = uuid4().hex
            portable_capture._windows_path(
                portable_capture.pending_path(parent, backup_id) / "objects" / ("a" * 64)
            )
            stamp = now()
            task = Task(uuid4().hex, backup_id, operation, digest, str(parent), stamp, stamp)
            exits = ExitStack()
            written = False
            try:
                # The deletion/HF/export fence first: an inactive project refuses here.
                exits.enter_context(self.capture_guard())
                reservation = self.gate.reserve()
                exits.callback(reservation.release)
                task.reservation, task.exits = reservation, exits
                try:
                    self._write(task)  # Durable before 202: a crash afterwards leaves its note.
                except OSError:
                    raise HTTPException(
                        409, "The backup could not be recorded in this library. Nothing was copied."
                    ) from None
                written = True
                with self.lock:
                    self.tasks[task.identifier] = task
                    task.thread = threading.Thread(
                        target=self._run, args=(task,), name="cbs-library-backup", daemon=True
                    )
                    try:
                        task.thread.start()
                    except BaseException:
                        del self.tasks[task.identifier]
                        raise
            except BaseException:
                exits.close()
                if written:
                    self._unlink(task.identifier)
                raise
            return self.summary(task), True

    # ---------- Controls ----------

    def _task(self, identifier):
        task = self.tasks.get(identifier)
        if task is None:
            raise HTTPException(404, "This backup is no longer listed. Refresh Activity.")
        return task

    def get(self, identifier):
        with self.lock:
            return self.summary(self._task(identifier))

    def summaries(self):
        with self.lock:
            return [self.summary(task) for task in self.tasks.values()]

    def stop(self, identifier):
        """Signal Stop; refused once the publication boundary has been passed."""
        with self.lock:
            task = self._task(identifier)
            if task.state != "running":
                return self.summary(task)
            if task.published:
                raise HTTPException(
                    409, {"code": "backup_publishing", "message": TOO_LATE, **self.summary(task)}
                )
            if not task.stop_requested:
                task.stop_requested = True
                task.cancel.set()
                self._touch(task)
            summary = self.summary(task)
        if self.gate is not None:
            self.gate.wake()
        return summary

    def dismiss(self, identifier):
        with self.lock:
            task = self._task(identifier)
            if task.state == "running":
                raise HTTPException(409, "This backup is still running.")
            if task.state == "attention":
                raise HTTPException(409, "Use Check again first: this backup's files were kept.")
            if task.kept:
                task.kept = [path for path in task.kept if _exists(Path(path))]
                if task.kept:
                    raise HTTPException(
                        409,
                        "Remove the incomplete copy first, or delete the folder named on this "
                        "card yourself.",
                    )
                self._touch(task)
            self._unlink(identifier)
            del self.tasks[identifier]
            return {"ok": True}

    def remove_partial(self, identifier):
        """Remove a settled task's own unpublished staging folder, identity-bound."""
        with self.lock:
            task = self._task(identifier)
            if task.state == "running" or task.published or not task.kept:
                raise HTTPException(409, "This backup has no incomplete copy to remove.")
            if task.cleaning:
                raise HTTPException(409, "The incomplete copy is already being removed.")
            task.cleaning = True
        try:
            outcome = self._remove_staging(task)
        finally:
            with self.lock:
                task.cleaning = False
        with self.lock:
            if outcome == "kept":
                self._touch(task)
                raise HTTPException(
                    409,
                    "The app could not confirm that this folder holds only its own incomplete "
                    "copy, so nothing was removed. Delete it yourself if it is not needed.",
                )
            task.kept, task.removed = [], outcome == "removed"
            task.message = task.message.split(" An incomplete copy", 1)[0]
            if outcome == "removed":
                task.message += " The incomplete copy was removed."
            self._touch(task)
        self._persist(task)
        return self.get(identifier)

    def check(self, identifier):
        """Settle an uncertain publication: finish only this task's own moves, then verify."""
        with self.lock:
            task = self._task(identifier)
            if task.state != "attention" or task.cleaning:
                raise HTTPException(409, "This backup does not need checking.")
            task.cleaning = True
        try:
            state, message, kept, receipt = self._reconcile(task)
        finally:
            with self.lock:
                task.cleaning = False
        self._settle(task, state, message, kept=kept, receipt=receipt)
        return self.get(identifier)

    # ---------- Worker ----------

    def _guard(self, task):
        if task.cancel.is_set():
            raise portable_capture.Cancelled()

    def _lease(self):
        # Borrow the serving lease; only a no-worker app takes its own, never re-entering
        # another thread's thread-bound lease lock.
        if self.acquire_file_lease and not self.store._lease_depth:
            return self.store.library_lock()
        return nullcontext()

    def _run(self, task):
        outcome, result = None, None
        try:
            with self._lease():
                result = portable_capture.capture_library(
                    self.store,
                    self.queue,
                    task.parent,
                    gate=self.gate,
                    guard=lambda: self._guard(task),
                    cancelled=task.cancel.is_set,
                    observer=_Observer(self, task),
                    identifier=task.backup_id,
                    reservation=task.reservation,
                )
        except portable_capture.Cancelled:
            outcome = "cancelled"
        except BaseException as error:  # noqa: BLE001 - the card reports a curated outcome.
            outcome = error
            if not isinstance(error, (ValueError, TimeoutError, OSError)):
                logging.getLogger(__name__).exception("Library backup stopped unexpectedly")
        finally:
            # Library admission (gate, deletion/HF/export fences) is released exactly once,
            # before the destination is inspected or cleaned.
            task.exits.close()
        try:
            if outcome is None:
                self._finish(task, result)
            elif task.published:
                self._settle(
                    task,
                    "attention",
                    "The backup was being saved when it stopped unexpectedly. Its files were "
                    "kept. Check again to confirm whether the backup is complete.",
                )
            else:
                self._abandon(task, None if outcome == "cancelled" else outcome)
        except BaseException:  # noqa: BLE001 - never leave a running card behind by a defect.
            logging.getLogger(__name__).exception("Library backup outcome could not be settled")
            if task.published:
                self._settle(task, "attention", RESTART_PUBLISHING)
            else:
                self._settle(task, "failed", "No backup was made: the backup stopped unexpectedly.")

    def _finish(self, task, result):
        """Ready only for the intended published target matching its creator reference."""
        try:
            self._published_matches(task, result)
        except (OSError, ValueError):
            return self._settle(
                task,
                "attention",
                "The backup was saved, but its final check did not match. Its files were kept. "
                "Check again to confirm whether the backup is complete.",
            )
        self._settle(
            task,
            "ready",
            f"Backup saved and checked: {result['originals']} original(s), "
            f"{result['files']} file(s). Save its receipt outside your backup storage.",
            receipt=dict(result),
        )

    def _published_matches(self, task, result):
        target = task.target
        if (
            result.get("backup_id") != task.backup_id
            or result.get("folder") != str(target)
            or _exists(task.pending)
        ):
            raise ValueError("The published backup is not where it was expected.")
        root = target.lstat()
        if not stat.S_ISDIR(root.st_mode) or _linked(root):
            raise ValueError("The published backup is not a folder.")
        transport._read(
            target / "COMMITTED.json",
            target,
            maximum=64 * 1024,
            expected_hash=result["commit_sha256"],
        )
        manifest, _ = transport._read(
            target / "manifest.json",
            target,
            maximum=transport.LIMITS["manifest_bytes"],
            expected_hash=result["manifest_sha256"],
            collect=True,
        )
        objects = json.loads(manifest)["objects"]
        names, _ = transport._entries(
            target / "objects", target, maximum=transport.LIMITS["objects"], guard=None
        )
        if set(names) != {item["sha256"] for item in objects}:
            raise ValueError("The published backup objects do not match its manifest.")
        for item in objects:
            if transport._node(target / "objects" / item["sha256"], target).st_size != item["size"]:
                raise ValueError("A published backup object has an unexpected size.")
        saved = next(
            (
                item
                for item in portable_capture.captures(self.store)
                if item["backup_id"] == task.backup_id
            ),
            None,
        )
        if saved != result:
            raise ValueError("The creator receipt does not match the published backup.")

    def _abandon(self, task, error):
        """Before publication: remove only this task's own staging, or name it."""
        with self.lock:
            task.cleaning = True
            self._touch(task)
        try:
            outcome = self._remove_staging(task)
        finally:
            with self.lock:
                task.cleaning = False
        kept = [str(task.pending)] if outcome == "kept" else []
        if error is None and task.closing and not task.stop_requested:
            state, message = "interrupted", INTERRUPTED
        elif error is None:
            state = "stopped"
            message = (
                "Backup stopped. No backup was made."
                if outcome != "none"
                else "Backup stopped before anything was copied. No backup was made."
            )
        else:
            state, message = "failed", "No backup was made: " + self._reason(error)
        if outcome == "removed":
            message += " The incomplete copy was removed."
        elif outcome == "kept":
            message += self._kept_note(task)
        task.removed = outcome == "removed"
        self._settle(task, state, message, kept=kept)

    @staticmethod
    def _kept_note(task):
        return (
            f" An incomplete copy remains at {task.pending}. It is not a backup; "
            "use Remove incomplete copy, or delete it yourself."
        )

    @staticmethod
    def _reason(error):
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            return "there is not enough free disk space for this backup."
        if isinstance(error, PermissionError):
            return "the backup folder could not be written. Check that you can write to it."
        if isinstance(error, (ValueError, TimeoutError)):
            text = str(error).strip()[:300]
            return text or "the backup could not be completed."
        if isinstance(error, OSError):
            return "the backup folder could not be written. Check the folder and free space."
        return "the backup stopped unexpectedly."

    def _remove_staging(self, task):
        """'none', 'removed' or 'kept'. Never follows links or removes unexpected names."""
        pending = task.pending
        try:
            info = pending.lstat()
        except FileNotFoundError:
            return "none"
        except OSError:
            return "kept"
        if (
            task.staging is None
            or (info.st_dev, info.st_ino) != tuple(task.staging)
            or not stat.S_ISDIR(info.st_mode)
            or _linked(info)
        ):
            return "kept"
        try:
            files, folders = [], []
            for name in os.listdir(pending):
                path = pending / name
                entry = path.lstat()
                if _linked(entry):
                    return "kept"
                if name == "objects" and stat.S_ISDIR(entry.st_mode):
                    children = os.listdir(path)
                    if len(children) > transport.LIMITS["objects"] + 1024:
                        return "kept"
                    for child in children:
                        inner = (path / child).lstat()
                        if (
                            not _OBJECT.match(child)
                            or not stat.S_ISREG(inner.st_mode)
                            or _linked(inner)
                        ):
                            return "kept"
                        files.append(path / child)
                    folders.append(path)
                elif name in _STAGING_FILES and stat.S_ISREG(entry.st_mode):
                    files.append(path)
                else:
                    return "kept"
            for path in files:
                path.unlink()
            for path in folders:
                path.rmdir()
            pending.rmdir()
        except OSError:
            return "kept"
        return "removed"

    def _reconcile(self, task):
        """Returns (state, message, kept, receipt) from fresh observations."""
        pending, target = task.pending, task.target
        try:
            receipt = next(
                (
                    item
                    for item in portable_capture.captures(self.store)
                    if item["backup_id"] == task.backup_id
                ),
                None,
            )
        except (OSError, ValueError):
            return "attention", "Saved backup receipts could not be read. Try again.", [], None
        if receipt is None:
            kept = [str(path) for path in (pending, target) if _exists(path)]
            note = (
                " Its files were kept at " + " and ".join(kept) + "; delete them yourself."
                if kept
                else ""
            )
            return (
                "failed",
                "No backup was made: it stopped before it was saved." + note,
                kept,
                None,
            )
        try:
            if _exists(pending):
                info = pending.lstat()
                if (
                    task.staging is None
                    or (info.st_dev, info.st_ino) != tuple(task.staging)
                    or not stat.S_ISDIR(info.st_mode)
                    or _linked(info)
                ):
                    raise ValueError("unconfirmed staging")
                if not _exists(target):
                    target.mkdir()  # Exclusive; never replaces an existing folder.
                root = target.lstat()
                if not stat.S_ISDIR(root.st_mode) or _linked(root):
                    raise ValueError("unexpected target")
                for name in _PUBLISHED:
                    if _exists(pending / name):
                        if _exists(target / name):
                            raise ValueError("conflicting target")
                        (pending / name).rename(target / name)
                pending.rmdir()  # Only when nothing else remains.
            verified = portable_capture.verify_capture(self.store, task.backup_id)
        except (OSError, ValueError):
            kept = [str(path) for path in (pending, target) if _exists(path)]
            return (
                "failed",
                "This backup could not be confirmed and should not be used. Its files were kept"
                + (" at " + " and ".join(kept) if kept else "")
                + ". Delete them yourself and make a new backup.",
                kept,
                None,
            )
        return (
            "ready",
            f"Backup saved and checked: {verified['originals']} original(s), "
            f"{verified['files']} file(s). Save its receipt outside your backup storage.",
            [],
            dict(verified),
        )

    # ---------- State ----------

    def _latch(self, task):
        """The publication boundary: Stop and this latch have exactly one winner."""
        with self.lock:
            if task.cancel.is_set():
                raise portable_capture.Cancelled()
            task.published, task.phase = True, "publishing"
            self._touch(task)
            try:
                # Durable before the creator reference exists, or publication is refused.
                self._write(task)
            except OSError:
                task.published, task.phase = False, "verify"
                self._touch(task)
                raise

    def _phase(self, task, phase):
        with self.lock:
            if task.phase != phase:
                task.phase = phase
                self._touch(task)
        self._persist(task)

    @staticmethod
    def _percent(task):
        if not task.bytes_total:
            return None
        return min(99, task.bytes_done * 100 // task.bytes_total)

    def _touch(self, task):
        task.revision += 1
        task.updated = now()

    def _settle(self, task, state, message, *, kept=None, receipt=None):
        with self.lock:
            task.state, task.phase, task.message = state, "settled", message[:1000]
            if kept is not None:
                task.kept = kept
            if receipt is not None:
                task.receipt = receipt
            self._touch(task)
            self.lock.notify_all()
        self._persist(task)

    # ---------- Shutdown and startup ----------

    def close(self):
        """Seal admission; stop every backup not yet past its publication boundary."""
        with self.admission:
            with self.lock:
                self.closed = True
                running = [task for task in self.tasks.values() if task.state == "running"]
                for task in running:
                    task.closing = True
                    if not task.published:
                        task.cancel.set()
                self.lock.notify_all()
            if self.gate is not None:
                self.gate.wake()
        for task in running:
            if task.thread is not None:
                task.thread.join()

    def recover(self):
        """Settle an earlier run's notes from fresh observations; resume nothing."""
        with self.lock:
            for task in self._read_all():
                if task.readable and task.state == "running":
                    if task.published:
                        task.state, task.message = "attention", RESTART_PUBLISHING
                        task.kept = [str(p) for p in (task.pending, task.target) if _exists(p)]
                    else:
                        task.state, task.message = "interrupted", INTERRUPTED
                        task.kept = [str(task.pending)] if _exists(task.pending) else []
                        if task.kept:
                            task.message += self._kept_note(task)
                    task.phase = "settled"
                    self._touch(task)
                    self._persist(task)
                self.tasks[task.identifier] = task

    # ---------- Activity ----------

    def summary(self, task):
        running = task.state == "running"
        progress = None
        if task.state == "ready":
            progress = 1
        elif running and task.phase == "copy" and task.bytes_total:
            progress = min(0.99, task.bytes_done / task.bytes_total)
        return {
            "id": task.identifier,
            "backup_id": task.backup_id,
            "state": task.state,
            "phase": task.phase,
            "revision": task.revision,
            "published": task.published,
            "stop_requested": task.stop_requested,
            "can_stop": running
            and not task.published
            and not task.stop_requested
            and not task.closing
            and not task.cleaning,
            "can_dismiss": task.state not in {"running", "attention"} and not task.kept,
            "can_remove_partial": not running
            and not task.published
            and bool(task.kept)
            and task.staging is not None
            and not task.cleaning,
            "can_check": task.state == "attention" and not task.cleaning,
            "kept_paths": list(task.kept),
            "destination": task.parent,
            "files_total": task.files_total,
            "files_done": task.files_done,
            "bytes_total": task.bytes_total,
            "bytes_done": task.bytes_done,
            "progress": progress,
            "receipt": {**task.receipt, **LABELS} if task.receipt else None,
            "message": self._message(task),
            "created": task.created,
            "updated": task.updated,
            "status_url": f"/api/library/backup-tasks/{task.identifier}",
        }

    def _message(self, task):
        if task.state != "running":
            return task.message
        if task.cleaning:
            return "Removing the incomplete copy…"
        if task.stop_requested:
            return "Stopping the backup…"
        if task.closing:
            return "The app is closing; stopping the backup…"
        if task.phase == "copy" and task.files_total:
            return f"Copying files: {task.files_done} of {task.files_total}…"
        return PHASE_MESSAGES.get(task.phase, "Making a backup…")

    def activity(self):
        with self.lock:
            cards = []
            for task in self.tasks.values():
                summary = self.summary(task)
                cards.append(
                    {
                        "id": "backup:" + task.identifier,
                        "kind": "backup",
                        "state": ACTIVITY_STATES[task.state],
                        "recording_id": None,
                        "recording_name": "Library backup",
                        "message": summary["message"],
                        "progress": summary["progress"],
                        "created": task.created,
                        "updated": task.updated,
                        "backup_task": summary,
                    }
                )
            return cards

    # ---------- Notes ----------

    def _folder(self):
        folder = self.store.root / FOLDER
        if folder.is_symlink() or folder.is_junction():
            raise OSError("The backup notes folder must not be a link.")
        return folder

    def _persist(self, task):
        try:
            self._write(task)
        except OSError:
            pass  # The in-memory card stays accurate; the next change writes again.

    def _write(self, task):
        with self.lock:
            record = {
                "format": FORMAT,
                "id": task.identifier,
                "backup_id": task.backup_id,
                "operation_sha256": task.operation,
                "request_sha256": task.digest,
                "parent": task.parent,
                "state": task.state,
                "phase": task.phase,
                "revision": task.revision,
                "published": task.published,
                "staging": list(task.staging) if task.staging else None,
                "kept": list(task.kept),
                "removed": task.removed,
                "message": task.message[:1000],
                "receipt": task.receipt,
                "files_total": task.files_total,
                "files_done": task.files_done,
                "bytes_total": task.bytes_total,
                "bytes_done": task.bytes_done,
                "created": task.created,
                "updated": task.updated,
            }
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        data = raw.encode("utf-8")
        if len(data) > MAX_NOTE_BYTES:
            raise OSError("The backup note is too large.")
        folder = self._folder()
        folder.mkdir(exist_ok=True)
        temporary = folder / f".{task.identifier}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, folder / f"{task.identifier}.json")
        finally:
            temporary.unlink(missing_ok=True)

    def _unlink(self, identifier):
        try:
            (self._folder() / f"{identifier}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def _prune(self):
        """Bound retained notes: drop the oldest finished notes with no obligation first."""
        removable = [
            task
            for task in self.tasks.values()
            if task.state in {"ready", "stopped", "failed", "interrupted"}
            and task.readable
            and not task.kept
        ]
        removable.sort(key=lambda task: task.updated)
        while len(self.tasks) >= MAX_NOTES and removable:
            task = removable.pop(0)
            self._unlink(task.identifier)
            del self.tasks[task.identifier]

    def _read_all(self):
        try:
            folder = self._folder()
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except OSError:
            return []
        tasks = []
        for entry in entries:
            name = entry.name
            if not name.endswith(".json") or not _HEX.match(name[:-5]):
                continue
            if len(tasks) >= MAX_NOTES * 2:
                break
            tasks.append(self._parse(folder / name, name[:-5]))
        tasks.sort(key=lambda task: (task.created, task.identifier))
        return tasks

    def _parse(self, path, identifier):
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError
            with path.open("rb") as stream:
                raw = stream.read(MAX_NOTE_BYTES + 1)
            if len(raw) > MAX_NOTE_BYTES:
                raise ValueError
            record = json.loads(raw.decode("utf-8"))
            staging = record.get("staging") if isinstance(record, dict) else None
            counts = ("files_total", "files_done", "bytes_total", "bytes_done")
            if (
                not isinstance(record, dict)
                or record.get("format") != FORMAT
                or record.get("id") != identifier
                or not isinstance(record.get("backup_id"), str)
                or not _HEX.match(record["backup_id"])
                or record.get("state") not in STATES
                or record.get("phase") not in PHASES
                or type(record.get("revision")) is not int
                or type(record.get("published")) is not bool
                or type(record.get("removed")) is not bool
                or not (
                    staging is None
                    or (
                        type(staging) is list
                        and len(staging) == 2
                        and all(type(value) is int for value in staging)
                    )
                )
                or not isinstance(record.get("parent"), str)
                or not 0 < len(record["parent"]) <= 4000
                or not Path(record["parent"]).is_absolute()
                or type(record.get("kept")) is not list
                or len(record["kept"]) > 2
                or not all(isinstance(item, str) and len(item) <= 4100 for item in record["kept"])
                or not (
                    record.get("receipt") is None
                    or (
                        isinstance(record["receipt"], dict)
                        and record["receipt"].get("backup_id") == record["backup_id"]
                        and isinstance(record["receipt"].get("commit_sha256"), str)
                        and re.fullmatch(r"[0-9a-f]{64}", record["receipt"]["commit_sha256"])
                    )
                )
                or not all(
                    record.get(key) is None or type(record.get(key)) is int for key in counts
                )
                or not all(
                    isinstance(record.get(key), str) and len(record[key]) <= limit
                    for key, limit in (
                        ("message", 1000),
                        ("created", 64),
                        ("updated", 64),
                        ("request_sha256", 64),
                        ("operation_sha256", 64),
                    )
                )
            ):
                raise ValueError
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            stamp = now()
            return Task(
                identifier,
                "0" * 32,
                "",
                "",
                str(self.store.root),
                stamp,
                stamp,
                state="failed",
                phase="settled",
                message=UNREADABLE,
                readable=False,
            )
        return Task(
            identifier,
            record["backup_id"],
            record["operation_sha256"],
            record["request_sha256"],
            record["parent"],
            record["created"],
            record["updated"],
            state=record["state"],
            phase=record["phase"],
            revision=record["revision"],
            published=record["published"],
            staging=tuple(staging) if staging else None,
            kept=list(record["kept"]),
            removed=record["removed"],
            message=record["message"],
            receipt=record.get("receipt"),
            files_total=record.get("files_total"),
            files_done=record.get("files_done") or 0,
            bytes_total=record.get("bytes_total"),
            bytes_done=record.get("bytes_done") or 0,
        )
