"""Server-owned project deletion tasks shown in Activity (ADR 0190).

Delete hands off at once: the dialog closes and this owner stops the captured jobs, removes
the project from the library and then removes its files, independently of any page or tab.
Only that project is inactive meanwhile. **Keep project** wins until the SQLite Delete commit,
the point of no return; jobs already stopped stay stopped. Closing the app before that point
keeps the project and leaves a note in Activity; nothing resumes on startup.

Small receipts in the library's `project-deletions` folder carry those notes across a
restart. They are observational only: fresh database facts decide every outcome, and no
receipt ever stops, deletes or releases anything. Backups and portable captures copy the
database, never these receipts, so a restored library carries no deletion authority.
"""

import hashlib
import json
import logging
import os
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import Annotated
from uuid import uuid4

import anyio
from fastapi import HTTPException
from fastapi import Path as PathParameter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import deletions
from .store import now

FORMAT = "cbs-project-deletion-v1"
FOLDER = "project-deletions"
MAX_ACTIVE = 8
MAX_RECEIPTS = 64
MAX_RECEIPT_BYTES = 16 * 1024
WAIT_SECONDS = 0.2
_HEX = re.compile(r"[0-9a-f]{32}\Z")
STATES = ("running", "kept", "interrupted", "failed", "deleted", "unresolved")
PHASES = ("waiting", "stopping", "removing_library", "removing_files", "settled")
# The Activity list's existing state vocabulary (finished cards can be cleared).
ACTIVITY_STATES = {
    "running": "running",
    "kept": "cancelled",
    "interrupted": "interrupted",
    "failed": "failed",
    "deleted": "completed",
    "unresolved": "interrupted",
}
PHASE_MESSAGES = {
    "waiting": "Waiting for another project deletion to finish…",
    "stopping": "Stopping jobs…",
    "removing_library": "Removing from the library…",
    "removing_files": "Removing files…",
}
DELETING = (
    "This project is being deleted. Follow it in Activity, where Keep project stops the "
    "deletion until it can no longer be undone."
)
KEEPING = "Keeping the project…"
KEPT = "Deletion stopped. The project was kept; jobs that were already stopped stay stopped."
INTERRUPTED = (
    "The app closed before this deletion finished. The project was kept; jobs that were "
    "already stopped stay stopped. Choose Delete again to delete it."
)
UNRESOLVED = (
    "File recovery for this deletion is pending. Use Retry on its local file cleanup card in "
    "Activity before changing this library."
)
UNREADABLE = "This deletion note could not be read. Nothing was deleted or kept because of it."
TOO_LATE = "It is too late to keep this project: it has already been removed from the library."
_MUTATIONS = {"POST", "PUT", "PATCH", "DELETE"}
_KEEP_PATH = re.compile(r"/api/project-deletion-tasks/[0-9a-f]{32}/keep\Z")
# Mutation paths whose identifier resolves to one project; bodies are checked in routes.
_SCOPES = (
    (re.compile(r"/api/projects/([0-9a-f]{32})(?:/|\Z)"), None),
    (
        re.compile(r"/api/recordings/([0-9a-f]{32})(?:/|\Z)"),
        "SELECT project_id FROM recordings WHERE id=?",
    ),
    (
        re.compile(r"/api/collections/([0-9a-f]{32})(?:/|\Z)"),
        "SELECT project_id FROM collections WHERE id=?",
    ),
    (
        re.compile(r"/api/(?:jobs|input-intents|input-inspections)/([0-9a-f]{32})(?:/|\Z)"),
        "SELECT r.project_id FROM jobs j JOIN recordings r ON r.id=j.recording_id WHERE j.id=?",
    ),
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class StartRequest(BaseModel):
    # Strict: a retyped replay is refused, never coerced into the same accepted digest.
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    confirm_shared: bool = False


class KeepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def install(app, tasks):
    """Routes and the project fence. Install after the capture gate so a refusal is outer."""
    task_id = Annotated[str, PathParameter(pattern=r"^[0-9a-f]{32}$")]

    @app.middleware("http")
    async def project_deletion_fence(request, call_next):
        if request.method in _MUTATIONS and tasks.fenced():
            refusal = await anyio.to_thread.run_sync(
                tasks.refusal, request.method, request.url.path
            )
            if refusal is not None:
                return JSONResponse({"detail": refusal, "code": "project_deleting"}, 409)
        return await call_next(request)

    @app.post("/api/projects/{identifier}/deletion-tasks", status_code=202)
    def start_project_deletion(identifier: task_id, body: StartRequest):
        summary, _ = tasks.start(
            identifier,
            operation_id=body.operation_id,
            fingerprint=body.fingerprint,
            confirm_shared=body.confirm_shared,
        )
        return summary

    @app.get("/api/project-deletion-tasks/{identifier}")
    def project_deletion(identifier: task_id):
        return tasks.get(identifier)

    @app.post("/api/project-deletion-tasks/{identifier}/keep")
    def keep_project(identifier: task_id, body: KeepRequest):
        # Exempt from the capture and recovery admission gates: it only signals (K5).
        summary = tasks.keep(identifier, body.project_id)
        return JSONResponse(summary, 202 if summary["state"] == "running" else 200)

    @app.post("/api/project-deletion-tasks/{identifier}/dismiss")
    def dismiss_project_deletion(identifier: task_id):
        return tasks.dismiss(identifier)


def keep_path(path):
    """The exact Keep signal that must reach its task during staging (ADR 0190)."""
    return _KEEP_PATH.match(path) is not None


@dataclass
class Task:
    identifier: str
    project_id: str
    project_name: str
    operation_id: str
    digest: str
    created: str
    updated: str
    # The live captured operation; None for a receipt from an earlier run (never executable).
    operation: object = None
    state: str = "running"
    phase: str = "waiting"
    revision: int = 1
    committed: bool | None = False
    message: str = ""
    note: str = ""
    keep_requested: bool = False
    closing: bool = False
    committing: bool = False
    local_cleanup: bool = False
    remote_pending: bool = False
    readable: bool = True
    decision: threading.Lock = field(default_factory=threading.Lock, repr=False)


class ProjectDeletionTasks:
    """One executor deletes accepted projects in order; other projects stay usable."""

    def __init__(self, store, queue, owner):
        self.store, self.queue, self.deletions = store, queue, owner
        self.lock = threading.Condition(threading.RLock())
        # Serializes admission, so an exact replay that races its own first request finds
        # the same task and shutdown never misses a task accepted while it begins.
        self.admission = threading.Lock()
        self.tasks = {}
        self.inactive = {}
        self.waiting = deque()
        self.thread = None
        self.closed = False
        # Seam for reciprocal admission fences, e.g. a running backup capture (T161). Each
        # callable returns a refusal or None; a refusal stops a new task before any Stop.
        self.inhibitors = []

    # ---------- Project fence ----------

    def fenced(self):
        return bool(self.inactive)

    def deleting(self, project_id):
        """The inactive projection the project list shows, or None."""
        with self.lock:
            identifier = self.inactive.get(project_id)
            task = self.tasks.get(identifier)
            if task is None:
                return None
            return {"task_id": task.identifier, "phase": task.phase}

    def require_active(self, project_id):
        if project_id in self.inactive:
            raise HTTPException(409, DELETING)

    def require_idle(self, *, project_id=None):
        """Library-wide actions and the deleting project's own moves wait for its task."""
        with self.lock:
            if self.inactive and (project_id is None or project_id in self.inactive):
                raise HTTPException(
                    409, "Wait for the project deletion in Activity to finish or keep it."
                )

    def refusal(self, method, path):
        """A mutation addressed to an inactive project's resources, else None."""
        if method not in _MUTATIONS or not self.inactive or keep_path(path):
            return None
        for pattern, query in _SCOPES:
            match = pattern.match(path)
            if match is None:
                continue
            if query is None:
                if path.endswith("/deletion-tasks"):
                    return None  # The start route answers an exact replay itself.
                project = match.group(1)
            else:
                rows = self.store.all(query, (match.group(1),))
                project = rows[0]["project_id"] if rows else None
            return DELETING if project in self.inactive else None
        return None

    # ---------- Admission ----------

    def start(self, project_id, *, operation_id, fingerprint=None, confirm_shared=False):
        """Accept one previewed and confirmed deletion; return (summary, created)."""
        with self.admission:
            return self._start(project_id, operation_id, fingerprint, confirm_shared)

    def _start(self, project_id, operation_id, fingerprint, confirm_shared):
        digest = _digest(
            {
                "project": project_id,
                "operation": operation_id,
                "fingerprint": fingerprint,
                "confirm_shared": bool(confirm_shared),
            }
        )
        with self.lock:
            if self.closed:
                raise HTTPException(409, "This library is closing. Nothing was deleted.")
            for task in self.tasks.values():
                if task.operation_id == operation_id and task.project_id == project_id:
                    # A lost reply: the same request finds the same task, never a second one.
                    if task.digest != digest:
                        raise HTTPException(
                            409, "This deletion was already accepted with other confirmations."
                        )
                    return self.summary(task), False
            if project_id in self.inactive:
                raise HTTPException(409, DELETING)
            if sum(task.state == "running" for task in self.tasks.values()) >= MAX_ACTIVE:
                raise HTTPException(
                    409, "Too many project deletions are running. Wait for one to finish."
                )
            for inhibitor in self.inhibitors:
                refusal = inhibitor()
                if refusal:
                    raise HTTPException(409, refusal)
            self._prune()
            if len(self.tasks) >= MAX_RECEIPTS:
                raise HTTPException(
                    409, "Dismiss earlier project deletion notes in Activity, then try again."
                )
            identifier = uuid4().hex
            # Reserve the fence first so no second task can accept this project meanwhile.
            self.inactive[project_id] = identifier
        operation = None
        try:
            operation = self.deletions.accept(
                "project",
                project_id,
                operation_id=operation_id,
                confirm_shared=confirm_shared,
                fingerprint=fingerprint,
                task=identifier,
            )
            stamp = now()
            task = Task(
                identifier,
                project_id,
                operation.target_name,
                operation_id,
                digest,
                stamp,
                stamp,
                operation=operation,
            )
            try:
                self._write(task)  # Durable before 202: a crash afterwards leaves its note.
            except OSError:
                raise HTTPException(
                    409, "The deletion could not be recorded in this library. Nothing changed."
                ) from None
        except BaseException:
            if operation is not None:
                with self.queue.lock:
                    operation.task = None
            with self.lock:
                if self.inactive.get(project_id) == identifier:
                    del self.inactive[project_id]
            raise
        with self.lock:
            self.tasks[identifier] = task
            self.waiting.append(task)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(
                    target=self._work, name="cbs-project-deletions", daemon=True
                )
                self.thread.start()
            self.lock.notify_all()
            return self.summary(task), True

    # ---------- Controls ----------

    def keep(self, identifier, project_id):
        """Signal Keep only; the executor rolls back and records the outcome (K5)."""
        with self.lock:
            task = self.tasks.get(identifier)
            if task is None or task.project_id != project_id:
                raise HTTPException(404, "This deletion is no longer listed. Refresh Activity.")
            if task.state != "running":
                return self.summary(task)
            with task.decision:
                if task.committing or task.committed:
                    raise HTTPException(
                        409,
                        {"code": "deletion_committed", "message": TOO_LATE, **self.summary(task)},
                    )
                task.keep_requested = True
            self._touch(task)
            self.lock.notify_all()
            return self.summary(task)

    def dismiss(self, identifier):
        with self.lock:
            task = self.tasks.get(identifier)
            if task is None:
                raise HTTPException(404, "This deletion is no longer listed. Refresh Activity.")
            if task.state == "running":
                raise HTTPException(409, "This deletion is still running.")
            self._unlink(identifier)
            del self.tasks[identifier]
            return {"ok": True}

    def get(self, identifier):
        with self.lock:
            task = self.tasks.get(identifier)
            if task is None:
                raise HTTPException(404, "This deletion is no longer listed. Refresh Activity.")
            return self.summary(task)

    # ---------- Executor ----------

    def _work(self):
        while True:
            with self.lock:
                while not self.waiting and not self.closed:
                    self.lock.wait()
                if not self.waiting:
                    return
                task = self.waiting.popleft()
            try:
                self._run(task)
            except BaseException:  # noqa: BLE001 - never leave a project inactive by a defect.
                logging.getLogger(__name__).exception("Project deletion task stopped")
                self._settle_from_facts(task)

    def _wants_keep(self, task):
        return task.keep_requested or task.closing

    def _decide(self, task, *, final):
        """Inside the Delete writer: Keep or the commit, never both (K4)."""
        with task.decision:
            if task.keep_requested or task.closing:
                raise deletions.Kept()
            if final:
                task.committing = True

    def _pause(self, task):
        with self.lock:
            if not self._wants_keep(task):
                self.lock.wait(WAIT_SECONDS)

    def _run(self, task):
        operation = task.operation
        if self._wants_keep(task):
            return self._kept(task)
        self._phase(task, "stopping")
        try:
            self.deletions.stop_for_task(operation, lambda: not self._wants_keep(task))
        except HTTPException as error:
            return self._settle(task, "failed", deletions._refusal(error))
        while True:
            if self._wants_keep(task):
                return self._kept(task)
            try:
                if self.deletions.drained(operation):
                    break
            except HTTPException as error:
                return self._settle(task, "failed", deletions._refusal(error))
            self._pause(task)
        self._phase(task, "removing_library")
        while True:
            if self._wants_keep(task):
                return self._kept(task)
            outcome = self.deletions.finish_task(
                operation,
                partial(self._decide, task),
                on_commit=partial(self._committed, task),
                remote=not self.closed,
            )
            if outcome["status"] != "waiting":
                break
            self._note(task, outcome["message"])
            self._pause(task)
        status = outcome["status"]
        if status == "kept":
            return self._kept(task)
        if status == "failed":
            return self._settle(task, "failed", outcome["message"])
        if status == "unresolved":
            return self._settle(task, "unresolved", UNRESOLVED, committed=None)
        task.local_cleanup = bool(outcome.get("local_cleanup"))
        task.remote_pending = any(
            item["cleanup_state"] == "pending" for item in outcome.get("remote", [])
        )
        message = "Project deleted."
        if task.local_cleanup:
            message += " Some files still need removing: see Local file cleanup in Activity."
        if task.remote_pending:
            message += " Moving its UCloud working folder to UCloud Trash continues in Activity."
        return self._settle(task, "deleted", message, committed=True)

    def _phase(self, task, phase):
        with self.lock:
            task.phase, task.note = phase, ""
            self._touch(task)
        self._persist(task)

    def _note(self, task, note):
        with self.lock:
            if task.note != note:
                task.note = str(note)[:300]
                self._touch(task)

    def _committed(self, task):
        """The point of no return has passed: the project rows are gone."""
        with self.lock:
            task.committed, task.phase, task.note = True, "removing_files", ""
            self._touch(task)
        self._persist(task)

    def _kept(self, task):
        if task.keep_requested:
            return self._settle(task, "kept", KEPT)
        return self._settle(task, "interrupted", INTERRUPTED)

    def _settle(self, task, state, message, *, committed=False):
        with self.lock:
            task.state, task.phase, task.message, task.note = state, "settled", message, ""
            task.committed = committed
            self._touch(task)
            # Reactivated (or gone) together with the observable outcome.
            if self.inactive.get(task.project_id) == task.identifier:
                del self.inactive[task.project_id]
            self.lock.notify_all()
        if task.operation is not None:
            self.deletions.forget(task.operation)
        self._persist(task)

    def _settle_from_facts(self, task):
        try:
            exists = self._project_exists(task.project_id)
        except Exception:  # noqa: BLE001
            exists = None
        if exists is False:
            self._settle(task, "deleted", "Project deleted.", committed=True)
        elif exists is True:
            self._settle(task, "failed", "The deletion stopped unexpectedly. The project was kept.")
        else:
            self._settle(task, "unresolved", UNRESOLVED, committed=None)

    def _project_exists(self, project_id):
        return bool(self.store.all("SELECT id FROM projects WHERE id=?", (project_id,)))

    def _touch(self, task):
        task.revision += 1
        task.updated = now()

    # ---------- Shutdown and startup ----------

    def close(self):
        """Seal admission and keep every project not yet past its point of no return (S1)."""
        with self.admission, self.lock:
            self.closed = True
            for task in self.tasks.values():
                if task.state == "running":
                    with task.decision:
                        task.closing = True
            self.lock.notify_all()
            thread = self.thread
        if thread is not None:
            thread.join()

    def recover(self):
        """Settle an earlier run's receipts from fresh database facts; resume nothing (S2)."""
        with self.lock:
            for task in self._read_all():
                if task.readable and task.state == "running":
                    try:
                        exists = self._project_exists(task.project_id)
                    except Exception:  # noqa: BLE001
                        exists = None
                    if exists is True:
                        task.state, task.message, task.committed = "interrupted", INTERRUPTED, False
                    elif exists is False:
                        task.state, task.committed = "deleted", True
                        task.message = (
                            "Project deleted. The app closed while its files were being "
                            "removed; any remaining file cleanup is shown in Activity."
                        )
                    else:
                        task.state, task.message, task.committed = "unresolved", UNRESOLVED, None
                    task.phase = "settled"
                    self._touch(task)
                    self._persist(task)
                self.tasks[task.identifier] = task

    # ---------- Activity ----------

    def summary(self, task):
        return {
            "id": task.identifier,
            "project_id": task.project_id,
            "project_name": task.project_name,
            "state": task.state,
            "phase": task.phase,
            "revision": task.revision,
            "committed": task.committed,
            "can_keep": task.state == "running"
            and not (task.committing or task.committed or task.keep_requested or task.closing),
            "keep_requested": task.keep_requested,
            "project_inactive": self.inactive.get(task.project_id) == task.identifier,
            "cleanup_pending": task.local_cleanup or task.remote_pending,
            "local_cleanup_pending": task.local_cleanup,
            "remote_cleanup_pending": task.remote_pending,
            "can_dismiss": task.state != "running",
            "message": self._message(task),
            "created": task.created,
            "updated": task.updated,
            "status_url": f"/api/project-deletion-tasks/{task.identifier}",
        }

    def _message(self, task):
        if task.state != "running":
            return task.message
        if task.keep_requested:
            return KEEPING
        if task.closing:
            return "The app is closing; keeping the project…"
        return task.note or PHASE_MESSAGES[task.phase]

    def activity(self):
        with self.lock:
            return [
                {
                    "id": "project-deletion:" + task.identifier,
                    "kind": "project-delete",
                    "state": ACTIVITY_STATES[task.state],
                    "recording_id": None,
                    "recording_name": task.project_name or "Deleted project",
                    "message": self._message(task),
                    "progress": None,
                    "created": task.created,
                    "updated": task.updated,
                    "project_deletion": self.summary(task),
                }
                for task in self.tasks.values()
            ]

    # ---------- Receipts ----------

    def _folder(self):
        folder = self.store.root / FOLDER
        if folder.is_symlink() or folder.is_junction():
            raise OSError("The project deletion notes folder must not be a link.")
        return folder

    def _persist(self, task):
        try:
            self._write(task)
        except OSError:
            pass  # The in-memory card stays accurate; the next change writes again.

    def _write(self, task):
        record = {
            "format": FORMAT,
            "id": task.identifier,
            "project_id": task.project_id,
            "project_name": task.project_name[:240],
            "operation_sha256": hashlib.sha256(task.operation_id.encode()).hexdigest(),
            "request_sha256": task.digest,
            "state": task.state,
            "phase": task.phase,
            "revision": task.revision,
            "committed": task.committed,
            "message": task.message[:500],
            "local_cleanup": task.local_cleanup,
            "remote_pending": task.remote_pending,
            "created": task.created,
            "updated": task.updated,
        }
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        folder = self._folder()
        folder.mkdir(exist_ok=True)
        temporary = folder / f".{task.identifier}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(raw.encode("utf-8"))
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
        """Bound retained notes: drop the oldest finished deleted/kept notes first."""
        removable = [
            task
            for task in self.tasks.values()
            if task.state in {"deleted", "kept"} and task.readable
        ]
        removable.sort(key=lambda task: task.updated)
        while len(self.tasks) >= MAX_RECEIPTS and removable:
            task = removable.pop(0)
            self._unlink(task.identifier)
            del self.tasks[task.identifier]

    def _read_all(self):
        try:
            folder = self._folder()
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
        except FileNotFoundError:
            return []
        except OSError:
            return []
        tasks = []
        for entry in entries:
            name = entry.name
            if not name.endswith(".json") or not _HEX.match(name[:-5]):
                continue
            if len(tasks) >= MAX_RECEIPTS * 2:
                break
            tasks.append(self._parse(folder / name, name[:-5]))
        tasks.sort(key=lambda task: (task.created, task.identifier))
        return tasks

    def _parse(self, path, identifier):
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError
            with path.open("rb") as stream:
                raw = stream.read(MAX_RECEIPT_BYTES + 1)
            if len(raw) > MAX_RECEIPT_BYTES:
                raise ValueError
            record = json.loads(raw.decode("utf-8"))
            text = (str,)
            if (
                not isinstance(record, dict)
                or record.get("format") != FORMAT
                or record.get("id") != identifier
                or not isinstance(record.get("project_id"), str)
                or not _HEX.match(record["project_id"])
                or record.get("state") not in STATES
                or record.get("phase") not in PHASES
                or record.get("committed") not in (True, False, None)
                or type(record.get("revision")) is not int
                or not all(
                    isinstance(record.get(key), text) and len(record[key]) <= limit
                    for key, limit in (
                        ("project_name", 240),
                        ("message", 500),
                        ("created", 64),
                        ("updated", 64),
                        ("request_sha256", 64),
                    )
                )
                or not all(
                    isinstance(record.get(key), bool) for key in ("local_cleanup", "remote_pending")
                )
            ):
                raise ValueError
        except (OSError, ValueError, UnicodeDecodeError, RecursionError):
            stamp = now()
            return Task(
                identifier,
                "",
                "Unreadable deletion note",
                "",
                "",
                stamp,
                stamp,
                state="failed",
                phase="settled",
                message=UNREADABLE,
                committed=None,
                readable=False,
            )
        return Task(
            identifier,
            record["project_id"],
            record["project_name"],
            "",
            record["request_sha256"],
            record["created"],
            record["updated"],
            state=record["state"],
            phase=record["phase"],
            revision=record["revision"],
            committed=record["committed"],
            message=record["message"],
            local_cleanup=record["local_cleanup"],
            remote_pending=record["remote_pending"],
        )
