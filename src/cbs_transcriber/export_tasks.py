"""Bounded, server-lifetime preparation of immutable local ZIP downloads.

These are derived downloads, not ASR jobs or library backups. A dialog/page may close
without losing them; server shutdown cancels preparation and removes its private files.
"""

import errno
import json
import os
import re
import stat
import tempfile
import threading
from concurrent.futures import CancelledError
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import anyio
from starlette.responses import StreamingResponse

from . import exports
from .store import now

MAX_READY = 3
MAX_RECORDS = 16
MAX_SELECTION_BYTES = 2 * 1024 * 1024


class Conflict(ValueError):
    """A current export or a source-changing operation owns admission."""


@dataclass
class _Task:
    id: str
    scope: str
    target: str
    project_id: str
    recording_ids: tuple
    options: dict
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    state: str = "queued"
    message: str = "Preparing the selected export…"
    progress: float = 0
    name: str = ""
    path: Path | None = None
    identity: tuple | None = None
    path_changed_ns: int = 0
    size: int = 0
    readers: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)
    prepared: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    seen: set = field(default_factory=set)


def _identity(info):
    # Python 3.13 on Windows gives lstat/fstat different ctime meanings. Compare
    # the common identity here and compare pathname ctime only with pathname ctime.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class Download:
    """One reserved file descriptor; concurrent downloads have independent offsets."""

    def __init__(self, owner, task, stream):
        self.owner, self.identifier, self.stream = owner, task.id, stream
        self.filename, self.size = f"{task.scope}-{task.target}.zip", task.size
        self._closed, self._lock = False, threading.Lock()

    def chunks(self):
        try:
            while block := self.stream.read(64 * 1024):
                yield block
        finally:
            self.close()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.stream.close()
        finally:
            self.owner._release_download(self.identifier)


class DownloadResponse(StreamingResponse):
    """Release reservations even if the client disappears before headers are sent."""

    def __init__(self, delivery):
        self.delivery = delivery
        super().__init__(
            delivery.chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{delivery.filename}"',
                "Content-Length": str(delivery.size),
            },
        )

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # StreamingResponse's BackgroundTask is skipped on an early ASGI send
            # error, and a never-started generator does not execute its finally.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.delivery.close)


class _Build:
    def __init__(self, owner, task, archive):
        self.owner, self.task, self.archive = owner, task, archive

    def guard(self):
        if self.task.cancel.is_set():
            raise CancelledError()

    def prepared(self, name):
        self.guard()
        with self.owner._lock:
            self.task.name = name
            self.task.state = "running"
            self.task.message = "Preparing the selected export…"
            self.task.updated = now()
        self.task.prepared.set()

    def progress(self, stage, recording_id, completed=None, total=None):
        self.guard()
        with self.owner._lock:
            task = self.task
            task.seen.add(recording_id)
            count, maximum = len(task.seen), max(1, len(task.recording_ids))
            detail = {
                "preparing": "Preparing",
                "reading": "Copying original recording",
                "archiving": "Writing original recording to ZIP",
            }[stage]
            message = f"{detail} · recording {count} of {maximum}"
            fraction = completed / total if total and completed is not None else 0
            progress = min(0.99, (count - 1 + fraction / 2) / maximum)
            if stage == "archiving":
                progress = min(0.99, (count - 0.5 + fraction / 2) / maximum)
            progress = max(task.progress, progress)
            # Polling needs useful stages, not a database write or notification per MB.
            if message != task.message or int(progress * 100) != int(task.progress * 100):
                task.message, task.progress, task.updated = message, progress, now()


class ExportTasks:
    """One active export and three retained ready downloads, bound to one serving Store."""

    def __init__(self, store, *, acquire_file_lease=False):
        self.store, self.acquire_file_lease = store, acquire_file_lease
        self._lock = threading.RLock()
        self._tasks, self._active, self._inhibitors = {}, None, 0
        self._closed = False
        self._accepting = True
        self._temporary = tempfile.TemporaryDirectory(prefix="cbs-transcriber-exports-")
        self._root = Path(self._temporary.name).resolve()

    def _public(self, task):
        ready = task.state == "completed" and task.path is not None
        return {
            "id": "export:" + task.id,
            "kind": "export",
            "recording_id": None,
            "recording_name": ("Project export" if task.scope == "project" else "Recording export")
            + (" · " + task.name if task.name else ""),
            "state": task.state,
            "message": task.message,
            "progress": task.progress,
            "created": task.created,
            "updated": task.updated,
            "export_task": {
                "id": task.id,
                "ready": ready,
                "can_cancel": task.state in {"queued", "running"} and not task.cancel.is_set(),
                "can_discard": (
                    task.state not in {"queued", "running"}
                    and self._active != task.id
                    and not task.readers
                ),
                "download_url": f"/api/export-tasks/{task.id}/download" if ready else None,
                "bytes": task.size if ready else None,
            },
        }

    def activity(self):
        with self._lock:
            return [self._public(task) for task in self._tasks.values()]

    def start(self, project_id, **options):
        return self._start("project", project_id, project_id, **options)

    def start_recording(self, recording_id, **options):
        project = self.store.one("SELECT project_id FROM recordings WHERE id=?", (recording_id,))
        return self._start("recording", recording_id, project["project_id"], **options)

    def _start(self, scope, target, project_id, **options):
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", target):
            raise ValueError("Choose an existing project or recording to export.")
        encoded = json.dumps(options, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_SELECTION_BYTES:
            raise ValueError("Choose fewer recordings for this export.")
        options = json.loads(encoded)  # The caller's mutable selection is never retained.
        with self._lock:
            if self._closed or not self._accepting or self._inhibitors:
                raise Conflict("Wait for the current library operation before preparing an export.")
            if self._active:
                raise Conflict("An export is already being prepared. Open Activity to follow it.")
            if sum(task.state == "completed" for task in self._tasks.values()) >= MAX_READY:
                raise Conflict("Discard a prepared download in Activity before preparing another.")
            while len(self._tasks) >= MAX_RECORDS:
                oldest = next(
                    (
                        key
                        for key, task in self._tasks.items()
                        if task.state in {"failed", "cancelled"} and task.path is None
                    ),
                    None,
                )
                if oldest is None:
                    raise Conflict(
                        "Remove an earlier export from Activity before preparing another."
                    )
                self._tasks.pop(oldest)
            selected = tuple(options.get("recording_ids", ())) if scope == "project" else (target,)
            task = _Task(uuid4().hex, scope, target, project_id, selected, options)
            self._tasks[task.id], self._active = task, task.id
            task.thread = threading.Thread(target=self._run, args=(task,), name="export-download")
            try:
                task.thread.start()
            except BaseException:
                self._tasks.pop(task.id)
                self._active = None
                raise
        # This waits only for metadata/selection checks and the SQLite read snapshot,
        # never for media copying or ZIP rendering. Failure also releases the waiter.
        task.prepared.wait()
        with self._lock:
            return self._public(task)

    def _run(self, task):
        archive = None
        try:
            lease = self.store.library_lock() if self.acquire_file_lease else nullcontext()
            with lease:
                task.path = self._root / (task.id + ".zip")
                archive = task.path.open("x+b")
                build = _Build(self, task, archive)
                build.guard()
                if task.scope == "project":
                    exports.project_archive(
                        self.store, task.target, background=build, **task.options
                    )
                else:
                    exports.recording_archive(
                        self.store, task.target, background=build, **task.options
                    )
                build.guard()
                archive.close()
                archive = None
                info = task.path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("The prepared export is unavailable.")
                with self._lock:
                    task.size, task.identity = info.st_size, _identity(info)
                    task.path_changed_ns = info.st_ctime_ns
                    task.state, task.progress = "completed", 1
                    task.message, task.updated = "Ready to download.", now()
        except CancelledError:
            with self._lock:
                task.state, task.message = "cancelled", "Export preparation cancelled."
                task.updated = now()
        except BaseException as error:
            with self._lock:
                task.state = "failed"
                task.message = (
                    exports.NO_RESULTS_MESSAGE
                    if type(error) is ValueError and str(error) == exports.NO_RESULTS_MESSAGE
                    else "Not enough free disk space to prepare this export."
                    if isinstance(error, OSError) and error.errno == errno.ENOSPC
                    else "Export preparation failed. Check the selection and local files."
                )
                task.updated = now()
        finally:
            try:
                if archive is not None:
                    archive.close()
            except OSError:
                with self._lock:
                    task.state = "failed"
                    task.message = "Export preparation failed while writing the local ZIP."
            finally:
                with self._lock:
                    if task.state != "completed" and task.path is not None:
                        try:
                            task.path.unlink(missing_ok=True)
                        except OSError:
                            pass  # Still private and not downloadable; close retries cleanup.
                        else:
                            task.path = None
                    if self._active == task.id:
                        self._active = None
                task.prepared.set()

    def _task(self, identifier):
        task = self._tasks.get(identifier)
        if task is None:
            raise KeyError(identifier)
        return task

    def require_idle(self, *, project_id=None, recording_ids=None):
        with self._lock:
            if not self._active:
                return
            active = self._tasks[self._active]
            if (
                project_id is None
                and recording_ids is None
                or active.project_id == project_id
                or bool(set(recording_ids or ()) & set(active.recording_ids))
            ):
                raise Conflict("Wait for or cancel export preparation in Activity first.")

    @contextmanager
    def exclusive(self):
        """Fence capture/file staging/switch admission without blocking ordinary edits."""
        with self._lock:
            self.require_idle()
            if self._closed:
                raise Conflict("This library is closing.")
            self._inhibitors += 1
        try:
            yield
        finally:
            with self._lock:
                self._inhibitors -= 1

    def cancel(self, identifier):
        with self._lock:
            task = self._task(identifier)
            if task.state in {"queued", "running"}:
                task.cancel.set()
                task.message, task.updated = "Stopping export preparation…", now()
            return self._public(task)

    def discard(self, identifier):
        with self._lock:
            task = self._task(identifier)
            if task.state in {"queued", "running"} or self._active == task.id or task.readers:
                raise Conflict("Wait for export preparation or its download to finish first.")
            if task.path is not None:
                try:
                    task.path.unlink(missing_ok=True)
                except OSError:
                    raise Conflict("The prepared export could not be removed. Try again.") from None
            self._tasks.pop(identifier)
        return {"ok": True}

    def open_download(self, identifier):
        with self._lock:
            task = self._task(identifier)
            if self._closed or task.state != "completed" or task.path is None:
                raise Conflict("This export is not ready to download.")
            try:
                before = task.path.lstat()
            except OSError:
                raise Conflict("The prepared export is unavailable. Prepare it again.") from None
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or getattr(before, "st_file_attributes", 0) & 0x400
                or _identity(before) != task.identity
                or before.st_ctime_ns != task.path_changed_ns
            ):
                raise Conflict("The prepared export changed. Discard it and prepare it again.")
            try:
                descriptor = os.open(
                    task.path,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError:
                raise Conflict("The prepared export is unavailable. Prepare it again.") from None
            try:
                opened = os.fstat(descriptor)
                if _identity(opened) != task.identity or opened.st_nlink != 1:
                    raise Conflict("The prepared export changed. Prepare it again.")
                stream = os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
            task.readers += 1
            return Download(self, task, stream)

    def _release_download(self, identifier):
        with self._lock:
            task = self._task(identifier)
            task.readers -= 1
            self._cleanup_if_closed()

    def _cleanup_if_closed(self):
        if self._closed and not any(task.readers for task in self._tasks.values()):
            self._temporary.cleanup()

    def stop_admission(self):
        """Seal starts after an accepted library switch, before lifespan shutdown runs."""
        with self._lock:
            self._accepting = False

    def close(self):
        with self._lock:
            self._closed = True
            self._accepting = False
            for task in self._tasks.values():
                if task.state in {"queued", "running"}:
                    task.cancel.set()
            threads = [task.thread for task in self._tasks.values() if task.thread]
        for thread in threads:
            thread.join()
        with self._lock:
            self._cleanup_if_closed()
