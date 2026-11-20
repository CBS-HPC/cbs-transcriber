"""Server-lifetime HF access and configured preparation tasks (ADR 0173).

Only primary HF Save starts work. Local children are drained before this owner releases
its serving lease; accepted UCloud work belongs to the existing durable preparation owner.
"""

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from . import enhancement, huggingface_access
from . import huggingface_prepare_worker as helper
from .store import now

PREFIX = "hf-setup:"
MAX_RECORDS = 16
MAX_PLAN_BYTES = 64 * 1024
POLL_SECONDS = 0.2
CHILD_SECONDS = 3600
HELPER = Path(helper.__file__).resolve()
PUBLIC_WORKER = HELPER.with_name("enhancement_worker.py")
ACTIVE = {"queued", "running"}
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
STAGE_LABELS = {
    "access": "Hugging Face access",
    "speaker_files": "Pinned speaker files",
    "local_public": "Local word-timing models",
    "ucloud": "UCloud models",
}
CACHE_ENVIRONMENT = ("HF_HOME", "HUGGINGFACE_HUB_CACHE", "TORCH_HOME", "NLTK_DATA")


class Conflict(ValueError):
    """A task, frozen choice or exclusive library action prevents this request."""


class Stopped(Exception):
    pass


def _copy(value, limit=MAX_PLAN_BYTES):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(raw) > limit:
        raise Conflict("The configured model plan is too large.")
    return json.loads(raw)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _source_digest(path):
    with path.open("rb") as stream:
        raw = stream.read(helper.MAX_SOURCE + 1)
    if len(raw) > helper.MAX_SOURCE:
        raise Conflict("The model preparation source is unavailable.")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class _Task:
    id: str
    fingerprint: str
    plan: dict
    plan_key: str
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    state: str = "queued"
    stage: str = "queued"
    progress: float = 0
    message: str = "Waiting to check Hugging Face access."
    outcomes: dict = field(default_factory=dict)
    cancel: threading.Event = field(default_factory=threading.Event)
    stop_reason: str | None = None
    user_stop_requested: bool = False
    remote_child: dict | None = None
    receipt: dict | None = None


class HuggingFaceTasks:
    """One local preparation at a time; no durable parent or secret persistence.

    Cloud callbacks freeze a secret-free configuration plan, request an existing durable
    owner, read its projection and invoke its existing Stop control. They never invent a
    second remote allocation owner. A missing adapter is a needs-setup outcome, not readiness.
    """

    def __init__(
        self,
        store,
        credentials,
        *,
        start_worker=True,
        acquire_file_lease=False,
        freeze_cloud_plan=None,
        request_cloud=None,
        cloud_status=None,
        cancel_cloud=None,
    ):
        self.store, self.credentials = store, credentials
        self.start_worker, self.acquire_file_lease = start_worker, acquire_file_lease
        self.freeze_cloud_plan = freeze_cloud_plan
        self.request_cloud, self.cloud_status, self.cancel_cloud = (
            request_cloud,
            cloud_status,
            cancel_cloud,
        )
        self._lock = threading.RLock()
        self._tasks, self._receipts = {}, {}
        self._pending, self._active, self._thread = None, None, None
        self._accepting, self._closed, self._inhibitors = True, False, 0

    def _plan(self):
        local = enhancement.local_settings(self.store).model_dump()
        cloud = {"status": "needs_setup"}
        if not local["prepare_ucloud_models"]:
            cloud = {"status": "disabled"}
        elif self.freeze_cloud_plan is not None:
            cloud = self.freeze_cloud_plan()
        if type(cloud) is not dict or cloud.get("status") not in {
            "ready",
            "needs_setup",
            "disabled",
            "unavailable",
        }:
            raise Conflict("The configured UCloud model plan is unavailable.")
        return _copy(
            {
                "local": local,
                "cloud": cloud,
                "cache_environment": {key: os.environ.get(key) for key in CACHE_ENVIRONMENT},
                "helper_sha256": _source_digest(HELPER),
                "worker_sha256": _source_digest(PUBLIC_WORKER),
            }
        )

    def _fingerprint(self):
        return huggingface_access.token_fingerprint(self.credentials.get(enhancement.HF_SCOPE))

    def _current(self, task):
        return task.fingerprint == self._fingerprint() and task.plan_key == _digest(self._plan())

    def _token(self, task):
        """Bind the actual plaintext read once to this task before any handoff."""
        with self._lock:
            if task.cancel.is_set():
                raise Stopped()
            if self._closed:
                task.stop_reason = "shutdown"
                task.cancel.set()
                raise Stopped()
            token = self.credentials.get(enhancement.HF_SCOPE)
            if huggingface_access.token_fingerprint(
                token
            ) != task.fingerprint or task.plan_key != _digest(self._plan()):
                task.stop_reason = "superseded"
                task.cancel.set()
                raise Stopped()
            return token

    def _guard(self, task):
        self._token(task)

    def _public(self, task):
        return {
            "id": PREFIX + task.id,
            "kind": "hf_setup",
            "recording_id": None,
            "project_id": None,
            "recording_name": "Hugging Face setup",
            "state": task.state,
            "stage": task.stage,
            "progress": task.progress,
            "message": task.message,
            "created": task.created,
            "updated": task.updated,
            "cancellable": task.state in ACTIVE and not task.cancel.is_set(),
            "retryable": task.state in {"failed", "cancelled", "interrupted"},
            "stage_outcomes": [
                {"stage": key, "label": STAGE_LABELS[key], **_copy(value)}
                for key, value in task.outcomes.items()
            ],
            "remote_child": _copy(task.remote_child) if task.remote_child else None,
            "continuation": "server_lifetime",
        }

    def activity(self):
        from .jobs import activity_cleared_at

        cleared = activity_cleared_at(self.store)
        with self._lock:
            return [
                self._public(task)
                for task in self._tasks.values()
                if task.state in ACTIVE
                or task.state in {"failed", "interrupted"}
                or any(
                    value["outcome"] in {"failed", "unavailable"}
                    for value in task.outcomes.values()
                )
                or not cleared
                or task.updated > cleared
            ]

    def status(self):
        """Offline current-token/plan projection; never scans models or starts work."""
        with self._lock:
            token = self.credentials.get(enhancement.HF_SCOPE)
            fingerprint = huggingface_access.token_fingerprint(token)
            try:
                plan_key = _digest(self._plan()) if token else None
            except (ValueError, OSError):
                plan_key = None
            receipt = self._receipts.get(
                (fingerprint, plan_key), huggingface_access.token_state(token)
            )
            current = next(
                (
                    task
                    for task in reversed(self._tasks.values())
                    if task.fingerprint == fingerprint and task.plan_key == plan_key
                ),
                None,
            )
            return _copy(receipt) | {
                "preparation_task": self._public(current) if current else None,
            }

    def save(self, hf_token, remember=True):
        """Serialize credential write and admission, without a crash-atomic claim."""
        if hf_token is None:
            return self.status()
        token = hf_token.strip()
        if len(token) > 4096 or any(c.isspace() or ord(c) < 32 for c in token):
            raise ValueError("Use a Hugging Face access token without spaces or line breaks.")
        with self._lock:
            if self._closed or not self._accepting or self._inhibitors:
                raise Conflict("Wait for the current library operation before saving this token.")
            # Existing credential persistence remains the only secret store. If persistence
            # raises after changing its session value, invalidate stale work in finally.
            try:
                self.credentials.set(enhancement.HF_SCOPE, token, remember)
            except Exception:
                raise Conflict(
                    "Token storage did not complete. Check the saved status before trying again."
                ) from None
            finally:
                self._invalidate_locked()
            return self._request_locked()

    def check(self, hf_token=None):
        """Explicit saved-token recheck, with no credential write or inline download."""
        current = self.credentials.get(enhancement.HF_SCOPE)
        proposed = hf_token.strip() if hf_token else current
        if proposed != current:
            raise Conflict("Save this Hugging Face token before checking it.")
        with self._lock:
            if self._closed or not self._accepting or self._inhibitors:
                raise Conflict("Wait for the current library operation before checking this token.")
            # Re-read under admission: another primary Save may have won the lock.
            if self.credentials.get(enhancement.HF_SCOPE) != current:
                raise Conflict("The saved Hugging Face token changed. Check again.")
            if not current:
                raise Conflict("Save a Hugging Face token before checking it.")
            return self._request_locked()

    def _request_locked(self):
        token = self.credentials.get(enhancement.HF_SCOPE)
        if not token:
            return self.status()
        plan = self._plan()
        fingerprint = huggingface_access.token_fingerprint(token)
        plan_key = _digest(plan)
        for task in self._tasks.values():
            if (
                task.state in ACTIVE
                and not task.cancel.is_set()
                and task.fingerprint == fingerprint
                and task.plan_key == plan_key
            ):
                return self.status()
        self._supersede_locked()
        self._add_locked(fingerprint, plan, plan_key)
        self._launch_locked()
        return self.status()

    def _invalidate_locked(self):
        if not any(task.state in ACTIVE for task in self._tasks.values()):
            return
        current = self._fingerprint()
        try:
            plan_key = _digest(self._plan())
        except (ValueError, OSError):
            plan_key = None
        for task in self._tasks.values():
            if task.state in ACTIVE and (task.fingerprint != current or task.plan_key != plan_key):
                self._stop_locked(task, "superseded")

    def invalidate(self):
        """Alias credential writers call this in finally; unchanged credentials survive."""
        with self._lock:
            self._invalidate_locked()

    def _supersede_locked(self):
        for task in self._tasks.values():
            if task.state in ACTIVE:
                self._stop_locked(task, "superseded")

    def _add_locked(self, fingerprint, plan, plan_key):
        while len(self._tasks) >= MAX_RECORDS:
            oldest = next(
                (key for key, task in self._tasks.items() if task.state in TERMINAL), None
            )
            if oldest is None:
                raise Conflict("Wait for earlier Hugging Face setup to finish.")
            self._tasks.pop(oldest)
        task = _Task(uuid4().hex, fingerprint, plan, plan_key)
        self._tasks[task.id], self._pending = task, task.id
        return task

    def _launch_locked(self):
        if not self.start_worker or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._work, name="hf-setup", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._thread = None
            task = self._tasks[self._pending]
            task.state, task.message, task.updated = "failed", "HF setup could not start.", now()
            self._pending = None
            raise

    def _work(self):
        try:
            lease = self.store.library_lock() if self.acquire_file_lease else nullcontext()
            with lease:
                while True:
                    with self._lock:
                        identifier, self._pending = self._pending, None
                        if identifier is None or self._closed:
                            return
                        task = self._tasks[identifier]
                        self._active = identifier
                    self._run(task)
                    with self._lock:
                        self._active = None
                        if self._pending is None:
                            return
        finally:
            with self._lock:
                self._active, self._thread = None, None
                if self._pending is not None and not self._closed:
                    self._launch_locked()

    def _stage(self, task, key, outcome, message, progress):
        with self._lock:
            self._guard(task)
            task.outcomes[key] = {"outcome": outcome, "message": message}
            task.stage = "check" if key == "access" else "cloud" if key == "ucloud" else "local"
            task.state, task.message, task.progress, task.updated = (
                "running",
                message,
                progress,
                now(),
            )

    def _remember(self, task, result):
        self._guard(task)
        receipt = huggingface_access.token_state(self.credentials.get(enhancement.HF_SCOPE))
        for key in ("token_status", "speaker_model_access", "checked_at"):
            if key in result:
                receipt[key] = result[key]
        if receipt["token_status"] not in {
            "missing",
            "unchecked",
            "valid",
            "invalid",
            "unavailable",
        }:
            raise Conflict("The Hugging Face access response is unavailable.")
        if receipt["speaker_model_access"] not in {"unknown", "allowed", "denied", "unavailable"}:
            raise Conflict("The Hugging Face access response is unavailable.")
        if receipt["checked_at"] is not None and (
            not isinstance(receipt["checked_at"], str) or len(receipt["checked_at"]) > 80
        ):
            raise Conflict("The Hugging Face access response is unavailable.")
        with self._lock:
            self._guard(task)
            task.receipt = receipt
            self._receipts[(task.fingerprint, task.plan_key)] = receipt
            while len(self._receipts) > MAX_RECORDS:
                self._receipts.pop(next(iter(self._receipts)))

    def _run(self, task):
        try:
            self._guard(task)
            self._stage(task, "access", "running", "Checking token and speaker-model access.", 0.05)
            token = self._token(task)
            checked = huggingface_access.check_token(token)
            self._remember(task, checked)
            if (
                checked.get("token_status") != "valid"
                or checked.get("speaker_model_access") != "allowed"
            ):
                self._stage(
                    task, "access", "failed", "Token or speaker-model access was not granted.", 0.1
                )
                self._finish(task, "failed", "Check the HF token and accepted Community-1 terms.")
                return
            self._stage(
                task, "access", "complete", "Token and speaker-model access confirmed.", 0.15
            )
            local, cloud = task.plan["local"], task.plan["cloud"]
            local_ready, targets = False, None
            if local["download_models"]:
                inspected = self._child(task, "inspect", local["runtime_python"])
                local_ready = inspected.get("status") == "ready"
                targets = inspected.get("cache_targets")
                if not local_ready:
                    self._stage(
                        task,
                        "local_public",
                        "unavailable",
                        "The selected local runtime is unavailable or unsupported.",
                        0.2,
                    )
            else:
                self._stage(
                    task, "local_public", "skipped", "Local model downloads are disabled.", 0.2
                )
            need_speakers = local_ready or cloud["status"] == "ready"
            speakers_ready = False
            if need_speakers:
                self._guard(task)
                self._stage(
                    task,
                    "speaker_files",
                    "running",
                    "Preparing verified speaker files on this computer.",
                    0.25,
                )
                result = self._child(task, "speakers", sys.executable)
                speakers_ready = result.get("status") == "ready"
                self._stage(
                    task,
                    "speaker_files",
                    "complete" if speakers_ready else "failed",
                    "Speaker files verified."
                    if speakers_ready
                    else "Speaker-file preparation failed; existing caches were retained.",
                    0.4,
                )
                if speakers_ready:
                    self._remember(task, checked)
                    with self._lock:
                        self._guard(task)
                        task.receipt["speaker_model_local"] = "verified"
                        task.receipt["speaker_model_error"] = None
            else:
                self._stage(
                    task,
                    "speaker_files",
                    "skipped",
                    "No supported configured place currently requests speaker files.",
                    0.4,
                )
            if local_ready:
                self._guard(task)
                self._stage(
                    task,
                    "local_public",
                    "running",
                    "Preparing pinned local word-timing models.",
                    0.45,
                )
                result = self._child(task, "public", local["runtime_python"], cache_targets=targets)
                self._stage(
                    task,
                    "local_public",
                    "complete" if result.get("status") == "ready" else "failed",
                    "Pinned local word-timing models prepared."
                    if result.get("status") == "ready"
                    else "Local model preparation failed; existing caches were retained.",
                    0.6,
                )
            self._guard(task)
            if cloud["status"] == "ready" and speakers_ready:
                self._cloud(task)
            else:
                outcome = (
                    "failed"
                    if cloud["status"] == "ready"
                    else "unavailable"
                    if cloud["status"] == "unavailable"
                    else "skipped"
                )
                message = (
                    "UCloud needs verified speaker files first."
                    if cloud["status"] == "ready"
                    else "UCloud automatic preparation is disabled."
                    if cloud["status"] == "disabled"
                    else "Enable a CBS Runtime model in Settings before UCloud model preparation."
                    if cloud["status"] == "needs_setup"
                    and cloud.get("reason") == "no_runtime_models"
                    else "UCloud preparation needs a checked configured connection."
                    if cloud["status"] == "needs_setup"
                    else "The configured UCloud preparation plan is unavailable."
                )
                self._stage(task, "ucloud", outcome, message, 0.9)
            self._guard(task)
            failed = any(value["outcome"] == "failed" for value in task.outcomes.values())
            partial = any(
                value["outcome"] in {"skipped", "unavailable"} for value in task.outcomes.values()
            )
            self._finish(
                task,
                "failed" if failed else "completed",
                "HF access checked; some preparation stages need attention."
                if failed or partial
                else "HF access and configured model preparation completed.",
            )
        except Stopped:
            self._settle_stop(task)
        except Exception:
            # Provider/Hub exception text can contain credentials; expose curated text only.
            self._finish(
                task,
                "failed",
                "HF setup could not finish. Existing caches and UCloud evidence were retained.",
            )

    def _child(self, task, mode, interpreter, *, cache_targets=None):
        self._guard(task)
        path = Path(interpreter)
        if not path.is_absolute() or not path.is_file():
            return {"status": "unavailable", "code": "runtime_missing"}
        nonce = uuid4().hex
        request = {
            "protocol": helper.PROTOCOL,
            "mode": mode,
            "nonce": nonce,
            "package_path": str(HELPER.parent),
            "helper_sha256": task.plan["helper_sha256"],
            "worker_sha256": task.plan["worker_sha256"],
            "device": task.plan["local"]["device"],
        }
        if mode == "speakers":
            request["token"] = self._token(task)
        if cache_targets is not None:
            request["cache_targets"] = cache_targets
        raw = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(raw) > helper.MAX_REQUEST:
            raise Conflict("The local preparation request is too large.")
        environment = enhancement.child_environment(mode != "inspect")
        environment["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        started = []
        process = subprocess.Popen(
            [str(path), "-I", "-B", str(HELPER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, io_failed = [], threading.Event()

            def write():
                try:
                    process.stdin.write(raw)
                    process.stdin.close()
                except (OSError, ValueError):
                    io_failed.set()

            def read():
                try:
                    output.append(process.stdout.read(helper.MAX_REPLY + 1))
                except (OSError, ValueError):
                    io_failed.set()

            for target in (write, read):
                thread = threading.Thread(target=target)
                thread.start()
                started.append(thread)
            deadline = time.monotonic() + CHILD_SECONDS
            while process.poll() is None:
                self._guard(task)
                if time.monotonic() > deadline or output and len(output[0]) > helper.MAX_REPLY:
                    raise Conflict("The local preparation process exceeded its limit.")
                task.cancel.wait(POLL_SECONDS)
            self._guard(task)
        finally:
            with ExitStack() as cleanup:
                cleanup.callback(process.stdout.close)
                cleanup.callback(process.stdin.close)
                for thread in started:
                    cleanup.callback(thread.join)
                cleanup.callback(enhancement.stop_process, process)
        if (
            process.returncode
            or io_failed.is_set()
            or not output
            or len(output[0]) > helper.MAX_REPLY
        ):
            raise Conflict("The local preparation process did not return a valid response.")
        result = json.loads(output[0])
        if (
            type(result) is not dict
            or result.get("protocol") != helper.PROTOCOL
            or result.get("nonce") != nonce
            or result.get("helper_sha256") != request["helper_sha256"]
            or result.get("worker_sha256") != request["worker_sha256"]
            or result.get("status") not in {"ready", "unavailable", "failed"}
        ):
            raise Conflict("The local preparation process did not return an owned response.")
        return result

    def _cloud(self, task):
        if any(callback is None for callback in (self.request_cloud, self.cloud_status)):
            self._stage(
                task, "ucloud", "unavailable", "UCloud model preparation is unavailable.", 0.9
            )
            return
        self._guard(task)
        self._stage(task, "ucloud", "running", "Checking configured UCloud models.", 0.65)
        result = self.request_cloud(_copy(task.plan["cloud"]), lambda: self._guard(task))
        # Drain an accepted request and retain its exact owner before observing Stop or
        # token changes. The durable owner remains visible after this parent disappears.
        operation = result.get("operation")
        if operation is not None:
            identifier = operation.get("runtime_preparation_id")
            if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
                raise Conflict("The UCloud preparation owner response is unavailable.")
            with self._lock:
                task.remote_child = {
                    "id": "runtime-preparation:" + identifier,
                    "runtime_preparation_id": identifier,
                    "created_by_task": result.get("deduplicated") is False,
                    "state": operation.get("state", "queued"),
                    "retained": True,
                }
        self._guard(task)
        if operation is None:
            if result.get("prepared") is not True:
                raise Conflict("The configured UCloud models have not been verified.")
            self._stage(
                task, "ucloud", "complete", "Configured UCloud models are already prepared.", 0.95
            )
            return
        while True:
            self._guard(task)
            observed = self.cloud_status(identifier)
            state = observed.get("state")
            if state not in ACTIVE | TERMINAL:
                raise Conflict("The durable UCloud preparation state is unavailable.")
            with self._lock:
                task.remote_child["state"] = state
            if state not in ACTIVE:
                self._stage(
                    task,
                    "ucloud",
                    "complete" if state == "completed" else "failed",
                    "Configured UCloud models prepared."
                    if state == "completed"
                    else "UCloud preparation needs attention in its Activity card.",
                    0.95,
                )
                return
            progress = observed.get("progress", 0)
            if type(progress) in (int, float) and math.isfinite(progress) and 0 <= progress <= 1:
                with self._lock:
                    task.progress = 0.65 + progress * 0.3
                    task.updated = now()
            task.cancel.wait(POLL_SECONDS)

    def _finish(self, task, state, message):
        with self._lock:
            if state == "completed":
                self._guard(task)
            task.state, task.stage, task.message, task.updated = state, "terminal", message, now()
            if state == "completed":
                task.progress = 1

    def _settle_stop(self, task):
        child = task.remote_child
        if task.user_stop_requested and child and child["created_by_task"] and self.cancel_cloud:
            try:
                self.cancel_cloud(child["runtime_preparation_id"])
            except Exception:
                pass  # The durable owner's own pending/uncertain evidence remains authoritative.
        with self._lock:
            for value in task.outcomes.values():
                if value["outcome"] == "running":
                    value["outcome"] = "cancelled"
            message = (
                "Local setup stopped; the UCloud preparation retains its own Activity controls."
                if child
                else "HF setup superseded by changed credentials or settings."
                if task.stop_reason == "superseded"
                else "HF setup stopped."
            )
            self._finish(
                task, "interrupted" if task.stop_reason == "shutdown" else "cancelled", message
            )

    def _stop_locked(self, task, reason):
        if task.state not in ACTIVE:
            return
        if reason == "user":
            task.user_stop_requested = True
        task.stop_reason, task.updated = reason, now()
        task.cancel.set()
        if self._active != task.id:
            if self._pending == task.id:
                self._pending = None
            self._finish(task, "cancelled", "HF setup stopped before preparation started.")
        else:
            task.message = "Stopping local HF setup; accepted work is being drained."

    def _task(self, identifier):
        if identifier.startswith(PREFIX):
            identifier = identifier[len(PREFIX) :]
        if identifier not in self._tasks:
            raise KeyError(identifier)
        return self._tasks[identifier]

    def cancel(self, identifier):
        with self._lock:
            task = self._task(identifier)
            self._stop_locked(task, "user")
            return self._public(task)

    def retry(self, identifier):
        with self._lock:
            task = self._task(identifier)
            if task.state not in {"failed", "cancelled", "interrupted"}:
                raise Conflict("Only stopped or failed HF setup can be retried.")
            if self._closed or not self._accepting or self._inhibitors:
                raise Conflict("Wait for the current library operation before retrying HF setup.")
            if not self._current(task):
                raise Conflict("The credential or configured plan changed. Save the token again.")
            if any(item.state in ACTIVE for item in self._tasks.values()):
                raise Conflict("Wait for current HF setup to stop before retrying.")
            successor = self._add_locked(task.fingerprint, _copy(task.plan), task.plan_key)
            self._launch_locked()
            return self._public(successor)

    def require_idle(self, **_):
        with self._lock:
            if self._active is not None or self._pending is not None:
                raise Conflict(
                    "Wait for or stop HF setup in Activity before changing this library."
                )

    @contextmanager
    def exclusive(self):
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

    def stop_admission(self):
        with self._lock:
            self._accepting = False

    def close(self):
        with self._lock:
            self._closed, self._accepting = True, False
            for task in self._tasks.values():
                self._stop_locked(task, "shutdown")
            thread = self._thread
        if thread is not None:
            thread.join()
