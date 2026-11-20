"""Server-lifetime public dictionary downloads, shown as Activity cards (ADR 0187).

Automatic downloads cover each dictionary language chosen as a project default or recording
override while checking and automatic downloads are on. One download runs at a time through
the spelling service's existing install path and cache lock; no transcript text, vocabulary or
credential is sent. Nothing starts at startup, and a hidden ``spelling`` feature starts nothing.
Tasks are not persisted: shutdown stops a running download and discards its partial files.
"""

import threading
from dataclasses import dataclass, field
from uuid import uuid4

from . import spelling
from .store import now

PREFIX = "spelling-dictionary:"
KIND = "spelling_dictionary"
MAX_RECORDS = 16
ACTIVE = frozenset({"queued", "running"})
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


class Conflict(spelling.Conflict):
    """The task state does not allow this control (HTTP 409 like other spelling conflicts)."""


class _Stopped(Exception):
    pass


@dataclass
class _Task:
    id: str
    language: str
    name: str
    consent: bool
    download_bytes: int
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    state: str = "queued"
    progress: float = 0
    message: str = ""
    stop_reason: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


class DictionaryTasks:
    """At most one running download; queued languages wait in request order."""

    def __init__(self, service, *, enabled=True, start_worker=True):
        self.service, self.enabled, self.start_worker = service, enabled, start_worker
        self._lock = threading.RLock()
        self._tasks, self._thread, self._closed = {}, None, False
        # Automatic triggers never restart a language that failed during this server's
        # lifetime; Retry or an explicit download does (no download loops while offline).
        self._failed = set()

    # ----- projection -------------------------------------------------------------------
    def _public(self, task):
        return {
            "id": PREFIX + task.id,
            "kind": KIND,
            "recording_id": None,
            "project_id": None,
            "recording_name": f"{task.name} spelling dictionary",
            "state": task.state,
            "progress": task.progress,
            "message": task.message,
            "created": task.created,
            "updated": task.updated,
            "continuation": "server_lifetime",
            "spelling_dictionary": {
                "id": task.id,
                "language": task.language,
                "bytes": task.download_bytes,
                "can_retry": task.state in {"failed", "cancelled", "interrupted"},
                "can_dismiss": task.state in TERMINAL,
            },
        }

    def activity(self):
        with self._lock:
            return [self._public(task) for task in self._tasks.values()]

    # ----- admission --------------------------------------------------------------------
    def _active(self, language):
        return any(
            task.language == language and task.state in ACTIVE and not task.cancel.is_set()
            for task in self._tasks.values()
        )

    def _add_locked(self, language, consent):
        while len(self._tasks) >= MAX_RECORDS:
            oldest = next(
                (key for key, task in self._tasks.items() if task.state in TERMINAL), None
            )
            if oldest is None:
                raise Conflict("Wait for the current dictionary downloads to finish.")
            self._tasks.pop(oldest)
        entry = self.service.catalog[language]
        task = _Task(
            uuid4().hex,
            language,
            entry["name"],
            consent,
            sum(item["bytes"] for item in entry["files"].values()),
        )
        task.message = f"Waiting to download the public {task.name} dictionary."
        self._tasks[task.id] = task
        return task

    def _launch_locked(self):
        if not self.start_worker or self._thread is not None or self._closed:
            return
        self._thread = threading.Thread(target=self._work, name="spelling-dictionary", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            raise

    def _queue(self, languages, *, consent):
        """Queue each language that is neither installed nor already waiting."""
        missing = [language for language in languages if not self.service.ready(language)]
        with self._lock:
            if self._closed:
                raise Conflict("The spelling service is closing.")
            queued = []
            for language in missing:
                if self._active(language):
                    continue
                if consent:
                    self._failed.discard(language)
                elif language in self._failed:
                    continue
                queued.append(self._add_locked(language, consent))
            if queued:
                self._launch_locked()
            return queued

    def request(self, settings_revision, consent=False, language=None, recording_id=None):
        """The browser's explicit or first-use request; returns the tasks for its languages."""
        if not self.enabled:
            raise spelling.AdditionsDisabled()
        if type(consent) is not bool:
            raise ValueError("Choose whether to download this dictionary.")
        if language is not None and recording_id is not None:
            raise ValueError("Choose one dictionary language.")
        preferences = self.service.settings()
        if preferences["revision"] != settings_revision:
            raise spelling.Conflict("Spelling preferences changed. Read them again.")
        if not consent and not (preferences["enabled"] and preferences["auto_download"]):
            raise spelling.Conflict("Confirm downloading this public spelling dictionary first.")
        if recording_id is not None:
            language = self.service.recording_language(recording_id)
            if language not in self.service.catalog:
                raise ValueError("No pinned dictionary is available for the recording language.")
        if language is not None and language not in self.service.catalog:
            raise ValueError("Choose an available spelling dictionary.")
        languages = [language] if language else self.service.selected_languages()
        self._queue(languages, consent=consent)
        with self._lock:
            return {
                "tasks": [
                    self._public(task)
                    for task in self._tasks.values()
                    if task.language in languages
                ],
                "ready": [item for item in languages if self.service.ready(item)],
            }

    def automatic(self):
        """After a preference or language save: never raises into the saving request."""
        if not self.enabled:
            return []
        try:
            preferences = self.service.settings()
            if not (preferences["enabled"] and preferences["auto_download"]):
                return []
            return [
                self._public(task)
                for task in self._queue(self.service.selected_languages(), consent=False)
            ]
        except (ValueError, OSError, KeyError):
            return []

    def preferences_changed(self):
        """Stop automatic work that the saved preferences no longer allow, then top up."""
        try:
            preferences = self.service.settings()
        except (ValueError, OSError):
            return []
        allowed = preferences["enabled"] and preferences["auto_download"]
        with self._lock:
            for task in self._tasks.values():
                if task.state in ACTIVE and not task.consent and not allowed:
                    self._stop_locked(task, "preferences")
        return self.automatic()

    # ----- controls ---------------------------------------------------------------------
    def _task(self, identifier):
        if identifier.startswith(PREFIX):
            identifier = identifier[len(PREFIX) :]
        task = self._tasks.get(identifier)
        if task is None:
            raise KeyError(identifier)
        return task

    def retry(self, identifier):
        """An explicit Retry is the user's consent for this one public download."""
        if not self.enabled:
            raise spelling.AdditionsDisabled()
        with self._lock:
            task = self._task(identifier)
            if task.state not in {"failed", "cancelled", "interrupted"}:
                raise Conflict("Only a stopped or failed dictionary download can be retried.")
            if self._closed:
                raise Conflict("The spelling service is closing.")
            if self._active(task.language):
                raise Conflict("This dictionary is already being downloaded.")
            self._tasks.pop(task.id)
            self._failed.discard(task.language)
        queued = self._queue([task.language], consent=True)
        with self._lock:
            if queued:
                return self._public(queued[0])
            return {"ready": self.service.ready(task.language)}

    def dismiss(self, identifier):
        with self._lock:
            task = self._task(identifier)
            if task.state not in TERMINAL:
                raise Conflict("Wait for this dictionary download to finish first.")
            self._tasks.pop(task.id)
        return {"ok": True}

    def _stop_locked(self, task, reason):
        if task.state not in ACTIVE:
            return
        task.stop_reason, task.updated = reason, now()
        task.cancel.set()
        if task.state == "queued":
            self._finish(task, *self._stopped(task))

    @staticmethod
    def _stopped(task):
        if task.stop_reason == "shutdown":
            return "interrupted", "The download stopped when the app closed. Choose Retry."
        return "cancelled", "Stopped: automatic dictionary downloads or spelling were turned off."

    def _finish(self, task, state, message):
        task.state, task.message, task.updated = state, message, now()
        if state == "completed":
            task.progress = 1

    # ----- worker -----------------------------------------------------------------------
    def _next(self):
        with self._lock:
            if self._closed:
                return None
            task = next((item for item in self._tasks.values() if item.state == "queued"), None)
            if task is not None:
                task.state, task.updated = "running", now()
                task.message = (
                    f"Downloading the public {task.name} dictionary. No transcript text is sent."
                )
            return task

    def _work(self):
        try:
            while (task := self._next()) is not None:
                self._run(task)
        finally:
            with self._lock:
                self._thread = None
                if not self._closed and any(t.state == "queued" for t in self._tasks.values()):
                    self._launch_locked()

    def run_pending(self):
        """Tests and no-worker apps: process queued downloads in the calling thread."""
        while (task := self._next()) is not None:
            self._run(task)

    def _run(self, task):
        def guard():
            if task.cancel.is_set():
                raise _Stopped()

        def progress(value):
            with self._lock:
                if value > task.progress:
                    task.progress, task.updated = min(0.99, value), now()

        try:
            self.service.install_language(
                task.language, consent=task.consent, guard=guard, progress=progress
            )
        except _Stopped:
            with self._lock:
                self._finish(task, *self._stopped(task))
            return
        except spelling.Conflict:
            with self._lock:
                task.stop_reason = task.stop_reason or "preferences"
                self._finish(task, *self._stopped(task))
            return
        except spelling.CacheNeedsRemoval:
            failure = (
                "A damaged copy of this dictionary is in the way. Remove it in Settings → "
                "Spelling, then choose Retry."
            )
        except Exception:
            # Errors can contain private paths; Activity shows a fixed explanation.
            failure = (
                "The public dictionary could not be downloaded. Check the internet "
                "connection, then choose Retry."
            )
        else:
            # A stop that arrives after publication keeps the verified files: completed.
            with self._lock:
                self._finish(
                    task, "completed", f"Ready. Spelling can now check {task.name} passages."
                )
            return
        with self._lock:
            if task.cancel.is_set():
                self._finish(task, *self._stopped(task))
            else:
                self._failed.add(task.language)
                self._finish(task, "failed", failure)

    def close(self):
        with self._lock:
            self._closed = True
            for task in self._tasks.values():
                self._stop_locked(task, "shutdown")
            thread = self._thread
        if thread is not None:
            thread.join()
