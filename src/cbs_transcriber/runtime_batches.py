"""CBS Runtime allocation ownership; new batches never rewrite frozen singleton jobs."""

import hashlib
import json
import math
import os
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api
from . import providers, runtime_capture, runtime_store
from . import ucloud_native as native
from .media import Cancelled
from .runtime_batch_remote import ERROR_CODES, OUTPUT_NAMES, TERMINAL_PHASES
from .store import now

POLL_SECONDS = api.POLL_SECONDS
HEARTBEAT_SECONDS = 15
# ponytail: rare filesystem retry actions serialize; inference coordinators stay independent.
_ACTIONS_LOCK = threading.RLock()

PROTOCOL = "cbs-runtime-batch-v1"
LIMITS = {
    "max_members": 100,
    "idle_grace_seconds": 60,
    "owner_absence_seconds": 300,
    "late_cutoff_seconds": 120,
    "gpu_concurrency": 1,
    "resident_models": "single-slot-v1",
    "cohort_max_members": 8,
    "cohort_wait_seconds": 2,
}
CODE_FILES = (
    *api.WORKER_FILES,
    api.SUPERVISOR,
    Path(__file__).with_name("runtime_batch_remote.py"),
)


def _local_failure(stage, error):
    error_class = next(
        (
            kind.__name__
            for kind in (ValueError, OSError, KeyError, TypeError, RuntimeError)
            if isinstance(error, kind)
        ),
        "Exception",
    )
    return runtime_store.local_failure_message(stage, error_class)


def source_bytes(path):
    return path.read_bytes().replace(b"\r\n", b"\n")


def code_hashes():
    return {path.name: hashlib.sha256(source_bytes(path)).hexdigest() for path in CODE_FILES}


def freeze_plan(settings, credentials):
    """Read-only Terminal metadata is resolved before queue/SQLite locks, never at POST."""
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        application = api.script_application(client)
    return {
        "identity": {
            "protocol": PROTOCOL,
            "input_protocol": "cbs-original-transport-v1",
            "application": application,
            "code_hashes": code_hashes(),
            "model_revisions": {
                **{name: list(value) for name, value in api.MODELS.items()},
                **{
                    "whisper/" + name: list(api.MODELS[key])
                    for name, key in api.WHISPER_MODELS.items()
                },
            },
            "recipe_hash": api.requirements_hash(),
            "limits": dict(LIMITS),
        }
    }


def require_code(identity):
    if (
        identity.get("protocol") != PROTOCOL
        or identity.get("code_hashes") != code_hashes()
        or identity.get("recipe_hash") != api.requirements_hash()
        or identity.get("runtime_id") != api.runtime_id()
    ):
        raise ValueError(
            "This runtime batch uses another worker or runtime version. Collect existing outputs "
            "or start a new run; its frozen allocation will not be replaced."
        )


def legacy_readers(store, settings, runtime_id):
    """An old uploaded supervisor cannot participate in the new shared-use lock."""
    result = []
    for row in store.all(
        "WITH safe_jobs AS (SELECT id,kind,CASE WHEN json_valid(params) "
        "THEN params ELSE 'null' END AS params FROM jobs) "
        "SELECT id,params FROM safe_jobs WHERE kind='enhance' "
        "AND json_extract(params,'$.ucloud_api') IS NOT NULL "
        "AND json_extract(params,'$.runtime_batch_id') IS NULL"
    ):
        from . import restored_owners

        with store.connection() as db:
            if restored_owners.is_restored(db, "job", row["id"]):
                continue
        params = json.loads(row["params"])
        chosen, run = params.get("ucloud_api", {}), params.get("ucloud_run", {})
        frozen = chosen.get("settings", {})
        setup = frozen.get("ucloud_transcriber") or {}
        if (
            frozen.get("base_url") != settings.base_url
            or setup.get("project_id") != settings.ucloud_transcriber.project_id
            or setup.get("storage_path") != settings.ucloud_transcriber.storage_path
            or chosen.get("runtime_id", api.runtime_id()) != runtime_id
        ):
            continue
        uncertain = (
            not run.get("job_id")
            and (run.get("submission_intent") or run.get("submission_attempted"))
            and not run.get("reconciled_not_created")
        )
        if uncertain or (run.get("job_id") and run.get("remote_state") not in api.TERMINAL):
            result.append(row["id"])
    return result


def require_preparation_idle(store, settings, client):
    if not api.stamped(client, settings) and legacy_readers(store, settings, api.runtime_id()):
        raise ValueError(
            "An older UCloud job may still use this runtime. Finish or reconcile it before "
            "rebuilding the shared runtime. No new allocation was submitted."
        )


def ensure_use_lock(client, settings, folder, guard):
    """Create once with REJECT; never replace the inode held by running readers."""
    target = api.runtime_folder(settings) + "/.in-use"

    def require_file():
        metadata = native.cloud_call(client.retrieve_file, target)
        status = metadata.get("status") or {}
        kind = status.get("type", metadata.get("type"))
        if kind != "FILE" or any(
            status.get(key) or metadata.get(key)
            for key in ("isSymlink", "isSymbolicLink", "symlink", "symbolicLink")
        ):
            raise ValueError("The shared runtime lock must be a regular file.")

    if api.listed(client, target):
        return require_file()
    path = folder / "runtime-use-lock"
    path.write_bytes(b"\n")
    try:
        native.upload(
            client,
            settings.base_url,
            path,
            target,
            guard,
            lambda **_: None,
            conflict_policy="REJECT",
        )
    except (ValueError, UCloudAPIError, httpx.HTTPError):
        # A concurrent create or a lost confirmation may have established it already.
        if not api.listed(client, target):
            raise ValueError(
                "The shared UCloud runtime lock could not be created safely."
            ) from None
    require_file()


def remote_folder(settings, batch_id):
    return settings.ucloud_transcriber.storage_path + "/cbs-runtime-batch-" + batch_id


def control_folder(store, batch_id):
    path = store.root / "runtime-batches" / batch_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def hash_file(path):
    with path.open("rb") as stream:
        return {
            "size": path.stat().st_size,
            "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
        }


def durable_write(path, data):
    if path.exists() and path.read_bytes() == data:
        return
    pending = path.with_name(path.name + ".pending")
    with pending.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def json_bytes(value):
    return runtime_store.canonical(value).encode("utf-8")


def job_batch(store, job):
    return runtime_store.for_job(store, job)


def _settings(batch):
    return providers.Settings.model_validate(batch["identity"]["settings"])


def _scope(batch):
    return {
        "ok": True,
        "runtime_batch_id": batch["id"],
        "affected_jobs": [m["job_id"] for m in batch["members"]],
    }


def _client(credentials, batch):
    settings = _settings(batch)
    key = credentials.get(providers.credential_scope(settings))
    native.require_setup(settings, key)
    return native.client_for(settings, key)


def _status(client, batch):
    status = api.read_json(
        client,
        _settings(batch).base_url,
        remote_folder(_settings(batch), batch["id"]) + "/batch-status.json",
    )
    if status and (
        status.get("protocol") != PROTOCOL
        or status.get("batch_id") != batch["id"]
        or status.get("generation") != batch["id"]
    ):
        raise ValueError("The remote runtime status belongs to another allocation.")
    return status or {}


def _observe(store, batch, client):
    state = api.remote_state(client, batch["journal"]["job_id"])
    batch = runtime_store.checkpoint(
        store,
        batch["id"],
        batch["epoch"],
        journal_update={"remote_state": state, "remote_observed_at": now()},
    )
    if state in api.TERMINAL:
        runtime_store.release_capacity(store, batch["id"], epoch=batch["epoch"])
    return batch, state


def cancel(store, credentials, batch_id, *, allowed_job_ids=None):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    batch = runtime_store.stop(
        store, batch_id, allowed_job_ids=allowed_job_ids
    )  # Intent wins before any network request, including the exact shared scope check.
    if batch["state"] == "running":
        return _scope(batch)  # The sole transport owner drains its transfers and stops.
    if batch["journal"].get("job_id"):
        with _client(credentials, batch) as client:
            batch, state = _observe(store, batch, client)
            if state not in api.TERMINAL:
                native.cloud_call(client.terminate_job, batch["journal"]["job_id"])
                batch = runtime_store.checkpoint(
                    store,
                    batch_id,
                    batch["epoch"],
                    journal_update={"termination_requested": True},
                )
    elif not runtime_store._uncertain(batch["journal"]):
        batch = runtime_store.checkpoint(
            store,
            batch_id,
            batch["epoch"],
            journal_update={"no_post": True},
        )
        runtime_store.release_capacity(store, batch_id, epoch=batch["epoch"])
    runtime_store.checkpoint(
        store,
        batch_id,
        batch["epoch"],
        state="completed"
        if all(m["state"] == "completed" for m in batch["members"])
        else "cancelled",
        message="CBS Runtime batch stopped; saved results kept.",
    )
    batch = runtime_store.get(store, batch_id)
    if (
        _public_only(store, batch)
        and _settings(batch).ucloud_transcriber.cleanup_after_success
        and (
            not batch["journal"].get("job_id")
            or batch["journal"].get("remote_state") in api.TERMINAL
        )
    ):
        retry_cleanup(store, credentials, batch_id)
    return _scope(batch)


def retry(store, credentials, batch_id):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    with _ACTIONS_LOCK:
        return _retry(store, credentials, batch_id)


def _retry(store, credentials, batch_id):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    batch = runtime_store.get(store, batch_id)
    if batch["journal"].get("release_decision"):
        raise ValueError(
            "This batch's UCloud folder was released when its recordings were deleted. "
            "Start a new run instead."
        )
    if batch["journal"].get("discard_confirmed"):
        raise ValueError(
            "Remote recovery was explicitly abandoned for this batch. "
            "Retained local evidence and saved results are unchanged; start a new run if needed."
        )
    if batch["state"] == "running":
        eligible = _retryable(batch)
        recoverable = _local_recoverable(store, batch)
        if not eligible and not recoverable:
            raise ValueError("No failed runtime task has confirmed worker termination.")
        for member in recoverable:
            runtime_store.queue_result_recovery(
                store,
                batch_id,
                member["job_id"],
                batch["epoch"],
                task_id=member["task_id"],
            )
        if eligible:
            providers.require_approval(
                store, batch["project_id"], batch["identity"]["provider_id"], _settings(batch)
            )
        for member in eligible:
            carried = member["remote_receipt"].get("files", {})
            if carried:
                runtime_store._verify_files(
                    store, member["job_id"], member["recording_id"], carried
                )
            runtime_store.retry_member(
                store,
                batch_id,
                member["job_id"],
                batch["epoch"],
                task_id=member["task_id"],
                carried_checkpoints=carried,
            )
        return _scope(batch)
    if batch["state"] not in {"failed", "interrupted", "cancelled"}:
        raise ValueError("This runtime allocation is active; wait for its current work.")
    if runtime_store._uncertain(batch["journal"]):
        raise ValueError("Reconcile this uncertain runtime submission before retrying.")
    if not batch["journal"].get("job_id"):
        if not runtime_store.setup_retryable(batch):
            raise ValueError(
                "This allocation has no recoverable remote job or proven safe setup retry. "
                "Start a fresh transcription request."
            )
        require_code(batch["identity"])
        providers.require_approval(
            store, batch["project_id"], batch["identity"]["provider_id"], _settings(batch)
        )
        runtime_store.queue_setup_retry(store, batch_id, batch["epoch"])
        return _scope(batch)
    with _client(credentials, batch) as client:
        batch, state = _observe(store, batch, client)
        status = _status(client, batch)
    deleted = runtime_store.deleted_members(store, batch)
    if not status and not all(
        m["output_receipt"] for m in batch["members"] if m["job_id"] not in deleted
    ):
        raise ValueError("The owned runtime status is unavailable; retry reconnecting later.")
    if status and not batch["journal"].get("readiness_generation"):
        batch = runtime_store.checkpoint(
            store,
            batch_id,
            batch["epoch"],
            journal_update={"readiness_generation": status["generation"]},
        )
    runtime_store.queue_recovery(
        store,
        batch_id,
        batch["epoch"],
        remote_status={
            "job_id": batch["journal"]["job_id"],
            "generation": status.get("generation", batch_id),
            "state": state,
            "accepting": state not in api.TERMINAL and status.get("phase") in {"ready", "running"},
        },
    )
    return _scope(batch)


def _retryable(batch):
    if batch["state"] != "running" or not runtime_store._admitting(batch):
        return []
    return [
        m
        for m in batch["members"]
        if not m["result_id"]
        and m["attempt_state"] in {"failed", "interrupted"}
        and m["remote_receipt"].get("worker_gone") is True
        and m["remote_receipt"].get("outcome") in {"failed", "interrupted"}
    ]


def _local_recoverable(store, batch):
    if batch["state"] != "running":
        return []
    result = []
    for member in batch["members"]:
        if (
            member["state"] not in {"failed", "interrupted"}
            or member["attempt_state"] != "complete"
            or member["result_id"]
        ):
            continue
        rows = store.all(
            "SELECT json_extract(params,'$.runtime_output_receipt') AS receipt "
            "FROM jobs WHERE id=?",
            (member["job_id"],),
        )
        receipt = json.loads(rows[0]["receipt"]) if rows and rows[0]["receipt"] else {}
        if all(
            receipt.get(k) == v
            for k, v in {
                "batch_id": batch["id"],
                "task_id": member["task_id"],
                "attempt": member["attempt"],
            }.items()
        ):
            result.append(member)
    return result


def reconcile(store, credentials, batch_id, *, confirm_not_created=False):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    batch = runtime_store.get(store, batch_id)
    if batch["state"] == "running" or not runtime_store._uncertain(batch["journal"]):
        raise ValueError("Only an inactive uncertain runtime submission can be reconciled.")
    matches, cursor = [], None
    with _client(credentials, batch) as client:
        for _ in range(100):
            page = native.cloud_call(client.browse_jobs, items_per_page=100, next_page=cursor)
            for item in page.get("items", []):
                if item.get("specification", {}).get("name") == batch["journal"]["run_name"]:
                    matches.append(item)
            cursor = page.get("next")
            if not cursor:
                break
        else:
            raise ValueError(
                "Runtime job discovery exceeded its bound; inspect UCloud before reconciling."
            )
        if len(matches) > 1:
            raise ValueError(
                "Several remote jobs match this unique runtime run; allocation remains uncertain."
            )
        if matches:
            remote = native.cloud_call(client.retrieve_job, matches[0]["id"])
            spec = remote.get("specification", {})
            if (
                spec.get("name") != batch["journal"]["run_name"]
                or spec.get("application") != batch["identity"]["application"]
                or spec.get("product") != batch["identity"]["runtime_product"]
            ):
                raise ValueError("The discovered remote job does not match this runtime identity.")
            update = {
                "job_id": str(remote.get("id", matches[0]["id"])),
                "remote_state": remote.get("status", {}).get("state"),
                "reconciled_at": now(),
            }
        elif confirm_not_created:
            update = {"reconciled_not_created": True, "no_post": True, "reconciled_at": now()}
        else:
            raise ValueError(
                "No exact runtime run was found. Confirm no allocation was created "
                "before releasing its reservation."
            )
    batch = runtime_store.checkpoint(store, batch_id, batch["epoch"], journal_update=update)
    if update.get("reconciled_not_created"):
        runtime_store.release_capacity(store, batch_id, epoch=batch["epoch"])
    return _scope(batch)


def require_no_obligations(store, project_id=None, recording_ids=None):
    if runtime_store.obligations(store, project_id, recording_ids):
        raise ValueError(
            "Finish, stop or reconcile this project's CBS Runtime batches "
            "and remote cleanup in Activity first."
        )


def _expected(options):
    return [
        name + ".json"
        for flag, name in (
            ("transcribe", "transcription"),
            ("retranscribe", "retranscription"),
            ("align", "alignment"),
            ("diarize", "diarization"),
        )
        if getattr(options, flag)
    ]


def _membership(store, batch):
    data = json_bytes(runtime_store.membership(store, batch["id"]))
    if len(data) > runtime_store.MAX_CONTROL_BYTES:
        raise ValueError("The runtime membership exceeds the control-file limit.")
    return data


class Coordinator:
    """One allocation transport owner; member threads only prepare, merge and save locally."""

    def __init__(self, worker, batch):
        from . import restored_owners

        restored_owners.require_owner(worker.store, "runtime_batch", batch["id"])
        from .enhancement import EnhancementOptions

        self.worker, self.store, self.batch = worker, worker.store, batch
        self.id, self.epoch = batch["id"], batch["epoch"]
        self.settings = _settings(batch)
        self.options = EnhancementOptions.model_validate(batch["identity"]["options"])
        self.folder, self.remote = (
            control_folder(self.store, self.id),
            remote_folder(self.settings, self.id),
        )
        self.prepare = batch["journal"].get("prepare_runtime", False)
        self.server = self.settings.base_url
        self.dispatches, self.pending, self.futures = queue.Queue(), {}, {}
        self.children, self.started = {}, set()
        self.local_failures = {}
        self.heartbeat_at, self.heartbeat_revision, self.membership_raw = 0, 0, None
        self.idle_at, self.ready, self.state, self.status = None, False, None, {}
        self.aborting = False

    def fresh(self):
        self.batch = runtime_store.get(self.store, self.id)
        if self.batch["epoch"] != self.epoch:
            raise ValueError("This runtime coordinator no longer owns the allocation.")
        return self.batch

    def checkpoint(self, **values):
        self.batch = runtime_store.checkpoint(
            self.store, self.id, self.epoch, journal_update=values
        )

    def message(self, text, progress=0.1):
        self.batch = runtime_store.checkpoint(
            self.store, self.id, self.epoch, message=text, progress=progress
        )

    def guard(self):
        if self.worker.stopping.is_set():
            raise Cancelled()
        if self.fresh()["journal"].get("stop_intent"):
            raise Cancelled()
        providers.require_approval(
            self.store,
            self.batch["project_id"],
            self.batch["identity"]["provider_id"],
            self.settings,
        )
        if (
            self.batch["journal"].get("job_id")
            and time.monotonic() - self.heartbeat_at >= HEARTBEAT_SECONDS
        ):
            self.renew()

    def project_status(self):
        phase = self.status.get("phase")
        accepting = self.state == "RUNNING" and phase in {"ready", "running"}
        if phase:
            fields = {}
            if not accepting and self.batch["admission"] == "open":
                fields["admission"] = "draining"
            self.batch = runtime_store.checkpoint(
                self.store,
                self.id,
                self.epoch,
                journal_update={"accepting": accepting},
                **fields,
            )

    def renew(self, *, require_accepting=True):
        self.heartbeat_at = time.monotonic()
        self.heartbeat_revision += 1
        self.control(
            "heartbeat.json",
            {
                "protocol": PROTOCOL,
                "batch_id": self.id,
                "epoch": self.epoch,
                "revision": self.heartbeat_revision,
            },
        )
        self.batch, self.state = _observe(self.store, self.fresh(), self.client)
        self.status = _status(self.client, self.batch)
        self.project_status()
        if require_accepting and (
            self.state in api.TERMINAL or self.status.get("phase") not in {"ready", "running"}
        ):
            raise ValueError("The runtime allocation is no longer accepting private inputs.")

    def upload(self, path, target, *, private=False):
        from .jobs import preparation_slot

        self.guard()
        if private:
            self.batch, self.state = _observe(self.store, self.fresh(), self.client)
            self.status = _status(self.client, self.batch)
            self.project_status()
            if self.state != "RUNNING" or self.status.get("phase") not in {"ready", "running"}:
                raise ValueError("The runtime allocation stopped accepting private inputs.")
            if not self.readiness():
                raise api.RuntimeNotReady("Runtime readiness is unavailable before private upload.")
            self.checkpoint(cleanup_state="pending")
        with preparation_slot(getattr(self.worker, "runtime_transfer_gate", None), self.guard):
            native.upload(self.client, self.server, path, target, self.guard, lambda **_: None)

    def setup_upload(self, path, target):
        """Keep a bounded public-file diagnostic; transfer exceptions may contain credentials."""
        if (
            path.name not in runtime_store.SETUP_UPLOAD_FILES
            or target != self.remote + "/" + path.name
        ):
            raise ValueError("Runtime setup upload requires an exact public control file.")
        try:
            self.upload(path, target)
        except (OSError, ValueError, UCloudAPIError, httpx.HTTPError) as exc:
            message = str(exc)
            if message == "UCloud upload interrupted. No job was started; retry explicitly.":
                code = "upload_interrupted"
            elif message == "UCloud upload confirmation timed out. No job was started.":
                code = "upload_confirmation_timeout"
            elif message == "UCloud uploaded file size could not be verified. No job was started.":
                code = "upload_verification_failed"
            else:
                code = "setup_upload_failed"
            self.checkpoint(
                setup_upload_failure={
                    "stage": "setup_script_upload",
                    "file": path.name,
                    "code": code,
                    "observed_at": now(),
                }
            )
            raise

    def control(self, name, value, *, guarded=False, setup=False):
        raw = value if isinstance(value, bytes) else json_bytes(value)
        if len(raw) > runtime_store.MAX_CONTROL_BYTES:
            raise ValueError("Oversized runtime control publication.")
        path = self.folder / name
        durable_write(path, raw)
        if guarded:
            (self.setup_upload if setup else self.upload)(path, self.remote + "/" + name)
        else:
            # Owned stop/heartbeat/seal controls remain available after approval revocation.
            native.upload(
                self.client,
                self.server,
                path,
                self.remote + "/" + name,
                lambda: None,
                lambda **_: None,
            )
        return raw

    def submit(self):
        if self.batch["journal"].get("job_id"):
            return
        if runtime_store._uncertain(self.batch["journal"]):
            raise ValueError(
                "Runtime allocation outcome is uncertain. Reconcile it in Activity; "
                "no second allocation was submitted."
            )
        require_code(self.batch["identity"])
        self.guard()
        prepared = api.stamped(self.client, self.settings)
        if not prepared:
            if not self.prepare:
                raise ValueError(api.FAILURES["runtime-missing"])
            require_preparation_idle(self.store, self.settings, self.client)
            api.ensure_folder(
                self.client, self.settings.ucloud_transcriber.storage_path + "/cbs-runtime"
            )
            api.ensure_folder(self.client, api.runtime_folder(self.settings))
        # The helper only checks/prepares verified model files, never a singleton journal.
        from .jobs import preparation_slot

        with preparation_slot(getattr(self.worker, "runtime_transfer_gate", None), self.guard):
            api.Run.prepare_speakers(self)
        ensure_use_lock(self.client, self.settings, self.folder, self.guard)
        if not self.batch["journal"].get("owned_folder"):
            try:
                self.client.create_folder(self.remote)
            except UCloudAPIError as exc:
                if exc.status_code == 409:
                    raise ValueError(
                        "The runtime folder exists without an ownership receipt; "
                        "nothing was uploaded."
                    ) from None
                raise
            self.checkpoint(owned_folder=self.remote, folder_created=True)
        if not self.batch["journal"].get("scripts_uploaded"):
            api.ensure_folder(self.client, self.remote + "/tasks")
            for source in CODE_FILES:
                path = self.folder / source.name
                durable_write(path, source_bytes(source))
                self.setup_upload(path, self.remote + "/" + source.name)
            self.control(
                "supervisor.sh",
                api.supervisor_script(
                    self.settings,
                    self.id,
                    self.prepare,
                    api.prefetch_models(self.options),
                    batch=True,
                ).encode(),
                guarded=True,
                setup=True,
            )
            self.control(
                "batch.json",
                {
                    "protocol": PROTOCOL,
                    "batch_id": self.id,
                    "identity_hash": self.batch["compatibility_hash"],
                    "code_hashes": self.batch["identity"]["code_hashes"],
                    "limits": {
                        **self.batch["identity"]["limits"],
                        "wall_seconds": self.options.timeout_minutes * 60,
                    },
                },
                guarded=True,
                setup=True,
            )
            if self.prepare:
                durable_write(self.folder / "requirements.txt", api.requirements_bytes())
                self.setup_upload(
                    self.folder / "requirements.txt", self.remote + "/requirements.txt"
                )
            self.checkpoint(scripts_uploaded=True)
        spec = api.build_script_specification(
            self.settings,
            self.batch["identity"]["application"],
            self.id,
            self.options,
            self.prepare,
            runtime_product=self.batch["identity"]["runtime_product"],
        )
        spec["name"] = "CBS Runtime batch " + self.id
        spec["parameters"][api.SCRIPT_PARAMETER]["path"] = self.remote + "/supervisor.sh"
        spec["resources"][0]["path"] = self.remote
        self.guard()
        self.checkpoint(
            submission_intent=True,
            submission_attempted=True,
            submission_attempted_at=now(),
            reconciled_not_created=False,
        )
        self.checkpoint(job_id=api.extract_job_id(native.cloud_call(self.client.submit_job, spec)))

    def dispatch(self, job, recording, folder, options, validate):
        item = SimpleNamespace(
            job=job,
            recording=recording,
            folder=folder,
            options=options,
            validate=validate,
            done=threading.Event(),
            error=None,
        )
        self.dispatches.put(item)
        item.done.wait()  # Owner always settles queued waiters before joining children.
        if item.error:
            raise item.error

    def launch(self, executor, *, local_only=False):
        from .enhancement import run_enhancement

        batch = self.fresh()
        deleted = runtime_store.deleted_members(self.store, batch)
        for member in batch["members"]:
            job_id = member["job_id"]
            if job_id in deleted:
                continue
            if local_only and not self.has_output(job_id):
                continue
            if (
                (member["task_id"] in self.started and member["state"] != "queued")
                or any(value[0] == job_id for value in self.futures.values())
                or member["state"] == "completed"
                or len(self.futures) >= 2
            ):
                continue
            if member["state"] == "cancelled" and not member["input_receipt"].get("publish_intent"):
                continue
            if member["state"] == "queued":
                if runtime_store.claim_member(self.store, self.id, job_id, self.epoch) is None:
                    continue
            if member["attempt"] > 1 and not member["input_receipt"].get("publish_intent"):
                try:
                    self.prepare_retry(member)
                except (OSError, ValueError):
                    self.task_checkpoint(
                        member, state="failed", member_state="failed", error_code="processing"
                    )
                    self.started.add(member["task_id"])
                    continue
            child = SimpleNamespace(
                store=self.store,
                credentials=self.worker.credentials,
                lock=self.worker.lock,
                stopping=self.worker.stopping,
                cancel_event=threading.Event(),
                runtime_dispatch=self.dispatch,
                runtime_preparation_gate=getattr(self.worker, "runtime_preparation_gate", None),
            )
            self.children[job_id] = child
            job = self.store.job(job_id)
            future = executor.submit(
                run_enhancement, child, job, self.store.recording(member["recording_id"])
            )
            self.futures[future] = (job_id, member["task_id"])
            self.started.add(member["task_id"])

    def prepare_retry(self, member):
        base = (
            self.store.directory(member["recording_id"]) / "enhancements" / member["job_id"]
        ).resolve()
        archive = (base / "attempts" / str(member["attempt"] - 1)).resolve()
        if not archive.is_relative_to(base):
            raise ValueError("The prior runtime attempt folder is invalid.")
        archive.mkdir(parents=True, exist_ok=True)
        carried = member["input_receipt"].get("carried_checkpoints", {})
        paths = []
        for name in {*OUTPUT_NAMES, "outcome.json", "audio.preparation.json"}:
            path = base / name
            if not path.exists():
                if name in carried:
                    raise ValueError("A carried runtime checkpoint is missing.")
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(base):
                raise ValueError("A prior runtime checkpoint escaped its member folder.")
            if name in carried and hash_file(path) != carried[name]:
                raise ValueError("A carried runtime checkpoint changed.")
            copy = archive / name
            if copy.exists() and hash_file(copy) != hash_file(path):
                raise ValueError("Prior runtime attempt evidence already exists with other bytes.")
            durable_write(copy, path.read_bytes())
            paths.append((name, path))
        # Copies are durable before clearing current unverified stages; CAS already won and
        # the owning coordinator has drained the prior child before starting this attempt.
        for name, path in paths:
            if name not in carried:
                path.unlink()

    def settle_item(self, task_id, error=None):
        item = self.pending.pop(task_id)
        try:
            if error and not isinstance(error, Cancelled) and not self.aborting:
                member = self.member(item.job["id"])
                stage = (
                    "result"
                    if member["remote_receipt"].get("outcome")
                    or member["input_receipt"].get("published")
                    else "publication"
                    if member["input_receipt"].get("publish_intent")
                    else "validation"
                )
                self.local_failures[task_id] = _local_failure(stage, error)
        except Exception:
            # Optional diagnostics cannot replace the original error or orphan its waiter.
            pass
        finally:
            item.error = error
            item.done.set()

    def member(self, job_id):
        return next(m for m in self.fresh()["members"] if m["job_id"] == job_id)

    def has_output(self, job_id):
        member = self.member(job_id)
        receipt = self.store.job(job_id)["params"].get("runtime_output_receipt") or {}
        return all(
            receipt.get(k) == v
            for k, v in {
                "batch_id": self.id,
                "task_id": member["task_id"],
                "attempt": member["attempt"],
            }.items()
        )

    def cancel_preparation(self):
        if not self.fresh()["journal"].get("stop_intent"):
            return
        for job_id, child in self.children.items():
            member = self.member(job_id)
            if (
                not member["input_receipt"].get("publish_intent")
                and not member["remote_receipt"].get("accepted")
                and not member["remote_receipt"].get("outcome")
                and not self.has_output(job_id)
            ):
                child.cancel_event.set()

    def task_checkpoint(self, member, **fields):
        return runtime_store.checkpoint_attempt(
            self.store, self.id, member["job_id"], self.epoch, member["task_id"], **fields
        )

    def artifact_binding(self, member):
        return {
            "batch_id": self.id,
            "task_id": member["task_id"],
            "attempt": member["attempt"],
            "identity_hash": self.batch["compatibility_hash"],
        }

    def artifact_collection(self, item, member, files):
        """Observe this real collection before its terminal CAS; never recapture on recovery."""
        origin = member["input_receipt"].get("artifact_origin")
        if origin is None:
            return {}
        known = member["remote_receipt"]
        if (
            "artifact_checkpoints" in known
            or member["attempt_state"] in runtime_store.ATTEMPT_TERMINAL
        ):
            if any(
                files.get(name) != value for name, value in known.get("artifact_files", {}).items()
            ):
                raise ValueError(
                    "The stored Runtime artifact observations differ from recovered files."
                )
            runtime_capture.validate_checkpoints(
                known.get("artifact_checkpoints", {}), origin, files=files
            )
            return {}
        checkpoints = runtime_capture.collect(origin, item.folder, files=files)
        return {"artifact_files": files, "artifact_checkpoints": checkpoints}

    def receipt(self, item, member, files):
        required = set(_expected(item.options))
        if not required <= files.keys() or any(
            name not in OUTPUT_NAMES and name not in {"outcome.json", "audio.preparation.json"}
            for name in files
        ):
            raise ValueError(
                "The runtime output receipt omits a required stage or includes an unknown file."
            )
        for name, expected in files.items():
            path = item.folder / name
            if path.is_symlink() or not path.is_file() or hash_file(path) != expected:
                raise ValueError("A runtime output differs from its durable receipt.")
        item.validate()
        current = self.member(member["job_id"])
        artifacts = runtime_capture.document(
            self.artifact_binding(current), current["input_receipt"], current["remote_receipt"]
        )
        if artifacts is None:
            item.job.pop("_runtime_artifacts", None)
        else:
            item.job["_runtime_artifacts"] = artifacts
        receipt = {
            "batch_id": self.id,
            "task_id": member["task_id"],
            "attempt": member["attempt"],
            "epoch": self.epoch,
            "files": files,
        }
        # Merge only this mutable receipt, never a child snapshot of the frozen job params.
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            params = json.loads(
                db.execute("SELECT params FROM jobs WHERE id=?", (member["job_id"],)).fetchone()[0]
            )
            params["runtime_output_receipt"] = receipt
            updated = db.execute(
                "UPDATE jobs SET params=?,updated=? WHERE id=? AND EXISTS "
                "(SELECT 1 FROM runtime_batch_runs b JOIN runtime_batch_members m "
                "ON m.batch_id=b.id JOIN runtime_batch_attempts a "
                "ON a.job_id=m.job_id AND a.attempt=m.current_attempt "
                "WHERE b.id=? AND b.coordinator_epoch=? AND m.job_id=jobs.id AND a.task_id=?)",
                (
                    runtime_store.canonical(params),
                    now(),
                    member["job_id"],
                    self.id,
                    self.epoch,
                    member["task_id"],
                ),
            ).rowcount
            if updated != 1:
                raise ValueError(
                    "The runtime output coordinator or task changed before finalization."
                )
        item.job["params"]["runtime_output_receipt"] = receipt
        self.settle_item(member["task_id"])

    def cached(self, item, member):
        receipt = (
            self.store.job(member["job_id"])["params"].get("runtime_output_receipt")
            or member["output_receipt"]
        )
        if not receipt:
            return False
        if any(
            receipt.get(k) != v
            for k, v in {
                "batch_id": self.id,
                "task_id": member["task_id"],
                "attempt": member["attempt"],
            }.items()
        ):
            raise ValueError("The local runtime output belongs to another attempt.")
        self.receipt(item, member, receipt["files"])
        return True

    def publish(self, item, member):
        require_code(self.batch["identity"])
        self.guard()
        from . import audio_inputs, remote_audio_input

        descriptor = remote_audio_input.for_job(item.job["params"])
        paths = {name: item.folder / name for name in ("request.json", "audio.wav")}
        if descriptor is not None:
            expected = {
                "protocol": remote_audio_input.PROTOCOL,
                "decoder": remote_audio_input.DECODER,
            }
            if api.read_json(self.client, self.server, self.remote + "/decoder.json") != expected:
                raise api.RuntimeNotReady(
                    "The runtime lacks the pinned original decoder. "
                    "Check UCloud runtime preparation."
                )
            if remote_audio_input.read(item.folder / remote_audio_input.DESCRIPTOR) != descriptor:
                raise ValueError("The staged original transport differs from its frozen source.")
            paths = {
                "request.json": item.folder / "request.json",
                remote_audio_input.DESCRIPTOR: item.folder / remote_audio_input.DESCRIPTOR,
                remote_audio_input.payload_name(descriptor): audio_inputs.verify_original(
                    self.store, item.recording, item.job["params"]["audio_input"]
                ),
            }
        files = {name: hash_file(path) for name, path in paths.items()}
        for name, expected in member["input_receipt"].get("carried_checkpoints", {}).items():
            if name not in _expected(item.options) or hash_file(item.folder / name) != expected:
                raise ValueError("A carried runtime checkpoint differs from its prior receipt.")
            files[name] = expected
            paths[name] = item.folder / name
        inputs = json_bytes(
            {
                "protocol": PROTOCOL,
                "batch_id": self.id,
                "identity_hash": self.batch["compatibility_hash"],
                "task_id": member["task_id"],
                "job_id": member["job_id"],
                "attempt": member["attempt"],
                "files": files,
                "expected_outputs": _expected(item.options),
            }
        )
        digest = hashlib.sha256(inputs).hexdigest()
        origin = runtime_capture.origin(
            item.job,
            item.folder,
            binding=self.artifact_binding(member),
            manifest_sha256=digest,
            files=files,
        )
        self.task_checkpoint(
            member,
            state="publishing",
            member_state="uploading",
            input_update={
                "files": files,
                "manifest_sha256": digest,
                "publish_intent": True,
                **({"artifact_origin": origin} if origin is not None else {}),
            },
        )
        task_remote = self.remote + "/tasks/" + member["task_id"]
        api.ensure_folder(self.client, task_remote)
        for name in files:
            self.upload(paths[name], task_remote + "/" + name, private=True)
        if descriptor is not None:
            audio_inputs.verify_original(
                self.store, item.recording, item.job["params"]["audio_input"]
            )
        path = self.folder / (member["task_id"] + "-inputs.json")
        durable_write(path, inputs)
        self.upload(path, task_remote + "/inputs.json", private=True)
        ready = json_bytes(
            {
                "protocol": PROTOCOL,
                "batch_id": self.id,
                "task_id": member["task_id"],
                "inputs_sha256": digest,
                "inputs_size": len(inputs),
            }
        )
        durable_write(path, ready)
        self.upload(path, task_remote + "/ready.json")
        self.task_checkpoint(member, input_update={"published": True})

    def publication(self, member):
        """Recover the commit marker, never rewrite a task whose publication was attempted."""
        folder = self.remote + "/tasks/" + member["task_id"]
        digest = member["input_receipt"].get("manifest_sha256")
        binding = {
            "protocol": PROTOCOL,
            "batch_id": self.id,
            "identity_hash": self.batch["compatibility_hash"],
            "task_id": member["task_id"],
            "job_id": member["job_id"],
            "attempt": member["attempt"],
        }
        accepted = api.read_json(self.client, self.server, folder + "/accepted.json")
        if accepted:
            if (
                not digest
                or accepted.get("inputs_sha256") != digest
                or any(accepted.get(k) != v for k, v in binding.items())
                or type(accepted.get("inputs_size")) is not int
                or not 0 < accepted["inputs_size"] <= runtime_store.MAX_CONTROL_BYTES
            ):
                raise api.RuntimeNotReady(
                    "The runtime task acceptance has a conflicting frozen input."
                )
            state = (
                None if member["attempt_state"] in runtime_store.ATTEMPT_TERMINAL else "accepted"
            )
            self.task_checkpoint(
                member,
                state=state,
                member_state="accepted" if state == "accepted" else None,
                input_update={"published": True},
                remote_update={"accepted": True, "generation": self.id, "manifest_sha256": digest},
            )
            return True
        ready = api.read_json(self.client, self.server, folder + "/ready.json")
        raw = api.read_remote(
            self.client, self.server, folder + "/inputs.json", runtime_store.MAX_CONTROL_BYTES
        )
        if ready and raw:
            if (
                set(ready) != {"protocol", "batch_id", "task_id", "inputs_sha256", "inputs_size"}
                or ready.get("protocol") != PROTOCOL
                or ready.get("batch_id") != self.id
                or ready.get("task_id") != member["task_id"]
                or not digest
                or ready.get("inputs_sha256") != digest
                or ready.get("inputs_size") != len(raw)
                or hashlib.sha256(raw).hexdigest() != digest
            ):
                raise api.RuntimeNotReady(
                    "The runtime ready marker has a conflicting frozen input."
                )
            value = json.loads(raw)
            if (
                any(value.get(k) != v for k, v in binding.items())
                or value.get("files") != member["input_receipt"].get("files")
                or set(value.get("expected_outputs", [])) != set(_expected(self.options))
            ):
                raise api.RuntimeNotReady(
                    "The runtime manifest differs from this task's frozen inputs."
                )
            self.task_checkpoint(member, input_update={"published": True})
            return True
        return False

    def collect(self, item, member):
        folder = self.remote + "/tasks/" + member["task_id"]
        raw_outcome = api.read_remote(self.client, self.server, folder + "/outcome.json")
        if raw_outcome is None:
            return False
        try:
            outcome = json.loads(raw_outcome)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(outcome, dict) or not outcome:
            return False
        binding = {
            "protocol": PROTOCOL,
            "batch_id": self.id,
            "identity_hash": self.batch["compatibility_hash"],
            "task_id": member["task_id"],
            "job_id": member["job_id"],
            "attempt": member["attempt"],
        }
        if any(outcome.get(key) != value for key, value in binding.items()):
            raise ValueError("The runtime output belongs to another input or recording.")
        expected_digest = member["input_receipt"].get("manifest_sha256")
        if (
            outcome.get("state") != "not_dispatched"
            and expected_digest
            and outcome.get("inputs_sha256") != expected_digest
        ):
            raise ValueError("The runtime output refers to a different input manifest.")
        state, outputs = outcome.get("state"), outcome.get("outputs")
        if state not in {"complete", "failed", "interrupted", "not_dispatched"}:
            raise ValueError("The runtime task returned an invalid terminal state.")
        if state == "not_dispatched" and (
            outcome.get("inputs_sha256") is not None
            or outcome.get("inputs_size") is not None
            or api.read_json(self.client, self.server, folder + "/accepted.json")
        ):
            raise api.RuntimeNotReady("An accepted runtime task cannot be marked undispatched.")
        if state != "not_dispatched":
            accepted = api.read_json(self.client, self.server, folder + "/accepted.json")
            if (
                not member["input_receipt"].get("publish_intent")
                or not expected_digest
                or not accepted
                or accepted.get("inputs_sha256") != expected_digest
                or any(accepted.get(k) != v for k, v in binding.items())
                or outcome.get("inputs_sha256") != expected_digest
                or outcome.get("inputs_size") != accepted.get("inputs_size")
            ):
                raise ValueError("The runtime output lacks its exact accepted input receipt.")
        if (
            not isinstance(outputs, dict)
            or not set(outputs) <= set(_expected(item.options))
            or (state == "complete" and set(outputs) != set(_expected(item.options)))
        ):
            raise ValueError("The runtime returned an unexpected stage set.")
        local = {}
        from . import remote_audio_input

        descriptor = remote_audio_input.for_job(item.job["params"])
        if descriptor is not None:
            expected = outcome.get("preparation")
            if expected is None and outputs:
                raise ValueError(
                    "The runtime output lacks its compressed original preparation receipt."
                )
            if expected is not None:
                if (
                    not isinstance(expected, dict)
                    or set(expected) != {"size", "sha256"}
                    or type(expected["size"]) is not int
                    or not 0 < expected["size"] <= remote_audio_input.MAX_JSON
                ):
                    raise ValueError("The runtime preparation receipt is invalid.")
                data = api.read_remote(
                    self.client,
                    self.server,
                    folder + "/" + remote_audio_input.PREPARATION,
                    remote_audio_input.MAX_JSON,
                )
                if (
                    data is None
                    or len(data) != expected["size"]
                    or hashlib.sha256(data).hexdigest() != expected["sha256"]
                ):
                    raise ValueError(
                        "The runtime preparation receipt differs from its terminal hash."
                    )
                value = remote_audio_input.validate_preparation(json.loads(data), descriptor)
                if data != remote_audio_input.encoded(value):
                    raise ValueError(
                        "The runtime preparation receipt is not canonical captured JSON."
                    )
                if value["request_sha256"] != hash_file(item.folder / "request.json")["sha256"]:
                    raise ValueError("Remote preparation belongs to another request.")
                durable_write(item.folder / remote_audio_input.PREPARATION, data)
                local[remote_audio_input.PREPARATION] = expected
                self.save_preparation(item, member, value)
        for name, expected in outputs.items():
            if (
                self.state not in api.TERMINAL
                and time.monotonic() - self.heartbeat_at >= HEARTBEAT_SECONDS
            ):
                self.renew(require_accepting=False)
            if (
                not isinstance(expected, dict)
                or set(expected) != {"size", "sha256"}
                or type(expected["size"]) is not int
                or not 0 <= expected["size"] <= api.MAX_STAGE_BYTES
                or not isinstance(expected["sha256"], str)
                or len(expected["sha256"]) != 64
            ):
                raise ValueError("Invalid runtime output hash receipt.")
            data = api.read_remote(
                self.client, self.server, folder + "/" + name, api.MAX_STAGE_BYTES
            )
            if (
                data is None
                or len(data) != expected["size"]
                or hashlib.sha256(data).hexdigest() != expected["sha256"]
            ):
                raise ValueError("A runtime stage output is missing or differs from its hash.")
            durable_write(item.folder / name, data)
            local[name] = expected
        name = "outcome.json"
        durable_write(
            item.folder / name,
            raw_outcome
            if member["input_receipt"].get("artifact_origin") is not None
            else json_bytes(outcome),
        )
        local[name] = hash_file(item.folder / name)
        if state != "complete":
            verified = self.partial_stages(item, outputs)
            code = (
                outcome.get("error_code")
                if outcome.get("error_code") in ERROR_CODES
                else "processing"
            )
            terminal = (
                "cancelled"
                if self.fresh()["journal"].get("stop_intent")
                else ("failed" if state in {"failed", "not_dispatched"} else "interrupted")
            )
            artifact_files = {
                **verified,
                **{
                    name: receipt
                    for name, receipt in local.items()
                    if name in {"outcome.json", "audio.preparation.json"}
                },
            }
            artifact_update = self.artifact_collection(item, member, artifact_files)
            self.task_checkpoint(
                member,
                state=terminal,
                member_state=terminal,
                error_code=code,
                remote_update={
                    "outcome": state,
                    "worker_gone": True,
                    "error_code": code,
                    "files": verified,
                    **artifact_update,
                },
            )
            self.settle_item(
                member["task_id"],
                Cancelled()
                if terminal == "cancelled"
                else ValueError(
                    api.FAILURES[code]
                    if code == "input_preparation"
                    else "CBS Runtime task did not finish ("
                    + code
                    + "). Verified completed stages are retained."
                ),
            )
            return True
        if member["input_receipt"].get("artifact_origin") is not None:
            item.validate()
        artifact_update = self.artifact_collection(item, member, local)
        self.task_checkpoint(
            member,
            state="complete",
            member_state="collecting",
            remote_update={
                "outcome": "complete",
                "worker_gone": True,
                "complete": True,
                "files": outputs,
                **artifact_update,
            },
        )
        self.receipt(item, member, local)
        return True

    def save_preparation(self, item, member, value):
        """Merge observed remote PCM evidence under the same owner/attempt fence as output."""
        from . import audio_inputs, remote_audio_input

        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            from . import restored_owners

            restored_owners.require_owned(db, "job", member["job_id"])
            restored_owners.require_owned(db, "runtime_batch", self.id)
            row = db.execute(
                "SELECT j.params FROM jobs j JOIN runtime_batch_members m ON m.job_id=j.id "
                "JOIN runtime_batch_runs b ON b.id=m.batch_id JOIN runtime_batch_attempts a "
                "ON a.job_id=j.id AND a.attempt=m.current_attempt "
                "WHERE j.id=? AND b.id=? AND b.coordinator_epoch=? AND a.task_id=?",
                (member["job_id"], self.id, self.epoch, member["task_id"]),
            ).fetchone()
            if row is None:
                raise ValueError("The preparation coordinator or task changed before preservation.")
            params = json.loads(row["params"])
            remote_audio_input.validate_preparation(value, remote_audio_input.for_job(params))
            previous = params.get("remote_audio_preparation")
            if previous is not None and previous != value:
                raise ValueError("A frozen job already owns another remote preparation receipt.")
            receipt = audio_inputs.validate_evidence(
                {
                    "schema": 1,
                    "policy": audio_inputs.POLICY,
                    "source": params["audio_input"],
                    "window": value["window"],
                    "prepared": value["prepared"],
                },
                params["audio_input"],
            )
            receipts = params.get("audio_preparations", [])
            if not isinstance(receipts, list) or len(receipts) > 200:
                raise ValueError("The saved audio preparation evidence is invalid.")
            receipts = [
                audio_inputs.validate_evidence(item, params["audio_input"]) for item in receipts
            ]
            if any(
                item.get("window") == receipt["window"] and item != receipt for item in receipts
            ):
                raise ValueError("A frozen job already owns another prepared WAV for this scope.")
            params["remote_audio_preparation"] = value
            if receipt not in receipts:
                if len(receipts) == 200:
                    raise ValueError("Too many audio preparation windows for one job.")
                params["audio_preparations"] = [*receipts, receipt]
            db.execute(
                "UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), member["job_id"])
            )
        item.job["params"] = params

    def partial_stages(self, item, outputs):
        from .enhancement import (
            asr_language_evidence,
            merge_output,
            read_output,
            retranscribed_source,
            retranscription_inputs,
            transcription_source,
        )

        automatic = item.options.language == "auto"
        converted = item.options.whisper_model in api.DANISH_CHOICES
        source = None if automatic else item.job["params"].get("source_snapshot")
        verified = {}
        for name in (
            "transcription.json",
            "retranscription.json",
            "alignment.json",
            "diarization.json",
        ):
            if name not in outputs:
                continue
            try:
                if name == "transcription.json":
                    if automatic or converted:
                        asr_language_evidence(
                            item.folder,
                            item.options,
                            job=item.job,
                            duration=item.recording["duration"],
                            include_alignment=False,
                        )
                    source = transcription_source(item.folder, allow_empty=automatic)
                elif name == "retranscription.json":
                    selected = retranscription_inputs(
                        source, item.job["params"]["segment_ids"], item.recording["duration"]
                    )
                    source = retranscribed_source(item.folder, selected)
                elif source is None:
                    raise ValueError("A stage is missing its verified source.")
                else:
                    align = name == "alignment.json"
                    if automatic and align:
                        asr_language_evidence(
                            item.folder,
                            item.options,
                            job=item.job,
                            duration=item.recording["duration"],
                        )
                    merge_output(
                        source,
                        read_output(item.folder / name) if align else None,
                        None if align else read_output(item.folder / name),
                        item.options.model_copy(update={"align": align, "diarize": not align}),
                        item.recording["duration"],
                        item.job["id"][:8],
                    )
            except (ValueError, KeyError, TypeError):
                # Keep the raw failed stage, without advertising it as reusable inference.
                (item.folder / name).replace(item.folder / ("invalid-" + name))
            else:
                verified[name] = outputs[name]
        return verified

    def process_pending(self, *, local_only=False):
        while True:
            try:
                item = self.dispatches.get_nowait()
            except queue.Empty:
                break
            member = self.member(item.job["id"])
            self.pending[member["task_id"]] = item
        for task_id, item in list(self.pending.items()):
            member = self.member(item.job["id"])
            try:
                if local_only and not self.has_output(item.job["id"]):
                    continue
                if self.cached(item, member):
                    continue
                if local_only:
                    raise ValueError("The local runtime output receipt is unavailable.")
                if self.fresh()["journal"].get("stop_intent") and not member["input_receipt"].get(
                    "publish_intent"
                ):
                    self.settle_item(task_id, Cancelled())
                    continue
                if self.collect(item, member):
                    continue
                if member["input_receipt"].get("publish_intent") and not member[
                    "input_receipt"
                ].get("published"):
                    if not self.publication(member):
                        raise api.Transient(
                            "Runtime input publication is uncertain. No inputs were rewritten; "
                            "reconnect to inspect the same task or stop it "
                            "before starting a new run."
                        )
                    member = self.member(item.job["id"])
                elif member["input_receipt"].get("published") and not member["remote_receipt"].get(
                    "accepted"
                ):
                    self.publication(member)
                    member = self.member(item.job["id"])
                if self.state in api.TERMINAL or self.status.get("phase") in TERMINAL_PHASES:
                    raise ValueError(
                        "The runtime allocation ended without this task's output; "
                        "start a new run for inference."
                    )
                if self.fresh()["journal"].get("stop_intent"):
                    if not member["input_receipt"].get("publish_intent"):
                        self.settle_item(task_id, Cancelled())
                elif self.ready and not member["input_receipt"].get("published"):
                    self.publish(item, member)
                elif (
                    member["remote_receipt"].get("accepted")
                    and self.status.get("running_task_id") == task_id
                ):
                    if member["attempt_state"] != "running":
                        self.task_checkpoint(member, state="running", member_state="running")
                    progress = (
                        api.read_json(
                            self.client,
                            self.server,
                            self.remote + "/tasks/" + task_id + "/progress.json",
                        )
                        or {}
                    )
                    fraction = progress.get("fraction")
                    fraction = (
                        min(0.95, max(0.1, fraction))
                        if type(fraction) in {int, float} and math.isfinite(fraction)
                        else 0.5
                    )
                    self.store.execute(
                        "UPDATE jobs SET message=?,progress=?,updated=? "
                        "WHERE id=? AND state='running'",
                        (
                            api.STAGES.get(progress.get("stage"), "UCloud · processing…"),
                            fraction,
                            now(),
                            member["job_id"],
                        ),
                    )
            except (api.Transient, api.RuntimeNotReady):
                raise
            except (ValueError, OSError) as exc:
                current = self.member(item.job["id"])
                if current["input_receipt"].get("publish_intent") and not current[
                    "input_receipt"
                ].get("published"):
                    raise api.Transient(
                        "Runtime input publication was interrupted. Reconnect to the same task."
                    ) from None
                self.settle_item(task_id, exc)

    def completed_futures(self):
        for future, (job_id, task_id) in list(self.futures.items()):
            if not future.done():
                continue
            del self.futures[future]
            error = future.exception()
            member = self.member(job_id)
            if member["task_id"] != task_id:
                continue  # A failed old child cannot downgrade its newly admitted retry.
            if member["state"] == "queued":
                continue  # Explicit local result recovery outranks the old child's failure.
            if error and member["state"] != "completed":
                state = (
                    "interrupted"
                    if self.aborting
                    else (
                        "cancelled"
                        if isinstance(error, Cancelled) and self.batch["journal"].get("stop_intent")
                        else "failed"
                    )
                )
                terminal = (
                    member["attempt_state"]
                    if member["attempt_state"] in runtime_store.ATTEMPT_TERMINAL
                    else "interrupted"
                    if member["input_receipt"].get("publish_intent")
                    and not member["remote_receipt"].get("outcome")
                    else state
                )
                try:
                    code = member.get("attempt_error_code") or "processing"
                    stage = (
                        "result"
                        if member["attempt_state"] == "complete"
                        or member["output_receipt"]
                        or member["input_receipt"].get("published")
                        else "publication"
                        if member["input_receipt"].get("publish_intent")
                        else "preparation"
                    )
                    local_failure = (
                        (self.local_failures.pop(task_id, None) or _local_failure(stage, error))
                        if (
                            state == "failed"
                            and code == "processing"
                            and member["remote_receipt"].get("outcome") in {None, "complete"}
                        )
                        else None
                    )
                    self.task_checkpoint(
                        member,
                        state=terminal,
                        member_state=state,
                        error_code=code,
                        local_failure=local_failure,
                    )
                except runtime_store.StaleRuntimeAttempt:
                    current = self.member(job_id)  # Rechecks the same owner and epoch.
                    if current["attempt"] > member["attempt"] and current["task_id"] != task_id:
                        continue
                    raise

    def readiness(self):
        doctor = api.read_json(self.client, self.server, self.remote + "/doctor.json")
        cache = api.read_json(self.client, self.server, self.remote + "/cache.json")
        if doctor is None or cache is None:
            return False
        if doctor.get("version") != api.WHISPERX_VERSION or not doctor.get("ready"):
            raise api.RuntimeNotReady(api.FAILURES["runtime"])
        from .runtime_compute import require_gpu_memory

        require_gpu_memory(doctor)
        if any(
            not (cache.get("models") or {}).get(name) for name in api.required_models(self.options)
        ) or (self.options.align and not cache.get("punkt_tab")):
            raise api.RuntimeNotReady(
                "The runtime lacks a pinned model. Check UCloud runtime preparation."
            )
        self.checkpoint(
            readiness_generation=self.id,
            readiness_hash=hashlib.sha256(
                json_bytes({"doctor": doctor, "cache": cache})
            ).hexdigest(),
        )
        return True

    def controls(self):
        batch = self.fresh()
        data = _membership(self.store, batch)
        if data != self.membership_raw:
            self.control("membership.json", data)
            self.membership_raw = data
            self.checkpoint(published_membership_revision=json.loads(data)["revision"])
        if time.monotonic() - self.heartbeat_at >= HEARTBEAT_SECONDS:
            self.heartbeat_revision += 1
            self.control(
                "heartbeat.json",
                {
                    "protocol": PROTOCOL,
                    "batch_id": self.id,
                    "epoch": self.epoch,
                    "revision": self.heartbeat_revision,
                },
            )
            self.heartbeat_at = time.monotonic()
        if batch["journal"].get("stop_intent"):
            self.control(
                "stop.json",
                {
                    "protocol": PROTOCOL,
                    "batch_id": self.id,
                    "revision": batch["membership_revision"],
                },
            )
        elif batch["seal_revision"] is not None:
            if hashlib.sha256(data).hexdigest() != batch["seal_digest"]:
                raise ValueError("The sealed runtime task set changed.")
            self.control(
                "seal.json",
                {
                    "protocol": PROTOCOL,
                    "batch_id": self.id,
                    "revision": batch["seal_revision"],
                    "membership_sha256": batch["seal_digest"],
                },
            )

    def local_recovery_pending(self):
        batch = self.fresh()
        deleted = runtime_store.deleted_members(self.store, batch)
        return any(
            m["job_id"] not in deleted
            and m["state"] != "completed"
            and (m["task_id"] not in self.started or m["state"] == "queued")
            and not (m["state"] == "cancelled" and not m["input_receipt"].get("publish_intent"))
            and self.has_output(m["job_id"])
            for m in batch["members"]
        ) or any(self.has_output(job_id) for job_id, _ in self.futures.values())

    def loop(self, executor):
        transient = 0
        while True:
            if self.worker.stopping.is_set():
                raise Cancelled()
            try:
                self.fresh()
                self.cancel_preparation()
                self.batch, self.state = _observe(self.store, self.batch, self.client)
                self.status = _status(self.client, self.batch)
                self.project_status()
                if self.local_recovery_pending():
                    # Existing outputs need their frozen validation and local save only.
                    # Drain them before consulting bootstrap readiness or starting fresh work.
                    if self.state not in api.TERMINAL:
                        self.controls()
                    self.launch(executor, local_only=True)
                    self.process_pending(local_only=True)
                    self.completed_futures()
                    self.worker.stopping.wait(POLL_SECONDS)
                    continue
                if self.state in api.TERMINAL and all(
                    m["state"] == "completed" for m in self.fresh()["members"]
                ):
                    return
                supervisor = (
                    api.read_json(self.client, self.server, self.remote + "/supervisor.json") or {}
                )
                if supervisor.get("generation") not in {None, self.id}:
                    raise ValueError("The supervisor generation disagrees with this allocation.")
                if supervisor.get("phase") == "failed":
                    raise api.RuntimeNotReady(
                        api.FAILURES.get(
                            supervisor.get("code"), "The UCloud runtime failed readiness."
                        )
                    )
                if not self.batch["journal"].get("started_at") and self.state == "RUNNING":
                    started = datetime.now(UTC)
                    deadline = started + timedelta(minutes=self.options.timeout_minutes)
                    self.checkpoint(
                        started_at=started.isoformat(),
                        hard_deadline=deadline.isoformat(),
                        late_admission_deadline=(
                            deadline
                            - timedelta(
                                seconds=self.batch["identity"]["limits"]["late_cutoff_seconds"]
                            )
                        ).isoformat(),
                    )
                if self.state not in api.TERMINAL:
                    self.controls()
                self.ready = False
                self.launch(executor)
                self.process_pending()
                needs_upload = any(
                    not self.member(item.job["id"])["input_receipt"].get("published")
                    for item in self.pending.values()
                )
                if (
                    needs_upload
                    and self.state == "RUNNING"
                    and self.status.get("phase") in {"ready", "running"}
                    and not self.fresh()["journal"].get("stop_intent")
                ):
                    self.ready = self.readiness()
                self.process_pending()
                self.completed_futures()
                if (
                    self.fresh()["journal"].get("stop_intent")
                    and self.state not in api.TERMINAL
                    and not self.batch["journal"].get("termination_requested")
                ):
                    native.cloud_call(self.client.terminate_job, self.batch["journal"]["job_id"])
                    self.checkpoint(termination_requested=True)
                    self.batch, self.state = _observe(self.store, self.batch, self.client)
                active = (
                    self.pending
                    or self.futures
                    or any(
                        m["state"] not in runtime_store.MEMBER_TERMINAL
                        for m in self.fresh()["members"]
                    )
                )
                if self.state in api.TERMINAL and not self.futures and not self.pending:
                    return
                if not active:
                    self.idle_at = self.idle_at or time.monotonic()
                    if (
                        self.batch["admission"] in {"open", "draining"}
                        and time.monotonic() - self.idle_at
                        >= self.batch["identity"]["limits"]["idle_grace_seconds"]
                    ):
                        runtime_store.seal(
                            self.store,
                            self.id,
                            self.epoch,
                            json.loads(self.membership_raw)["revision"],
                            membership_bytes=self.membership_raw,
                        )
                else:
                    self.idle_at = None
                completed = sum(m["state"] == "completed" for m in self.batch["members"])
                self.message(
                    f"CBS Runtime · {completed} of {len(self.batch['members'])} results saved "
                    f"· {self.batch['admission']}",
                    completed / max(1, len(self.batch["members"])),
                )
                transient = 0
            except api.Transient:
                transient += 1
                if transient > api.MAX_TRANSIENT:
                    raise
            self.worker.stopping.wait(POLL_SECONDS)

    def execute(self):
        with _client(self.worker.credentials, self.batch) as self.client:
            executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="runtime-member")
            failure = None
            try:
                self.submit()
                self.loop(executor)
            except BaseException as exc:
                failure = exc
                if not isinstance(exc, (api.Transient, Cancelled)) and self.fresh()["journal"].get(
                    "job_id"
                ):
                    try:
                        self.batch, state = _observe(self.store, self.batch, self.client)
                        if state not in api.TERMINAL:
                            native.cloud_call(
                                self.client.terminate_job, self.batch["journal"]["job_id"]
                            )
                            self.checkpoint(termination_requested=True)
                    except (ValueError, UCloudAPIError, httpx.HTTPError):
                        pass
            finally:
                self.aborting = failure is not None
                for job_id, child in self.children.items():
                    if not self.has_output(job_id):
                        child.cancel_event.set()
                # A preparing child can enqueue after this point; drain until all futures settle.
                while self.futures:
                    while True:
                        try:
                            item = self.dispatches.get_nowait()
                        except queue.Empty:
                            break
                        item.error = failure or Cancelled()
                        item.done.set()
                    for task_id in list(self.pending):
                        self.settle_item(task_id, failure or Cancelled())
                    self.completed_futures()
                    if self.futures:
                        time.sleep(0.01)
                executor.shutdown(wait=True)
            batch = self.fresh()
            if (
                batch["journal"].get("discard_confirmed")
                or batch["journal"].get("cleanup_state") in native.CLEANED
            ):
                return
            if failure:
                state = (
                    "interrupted" if isinstance(failure, (Cancelled, api.Transient)) else "failed"
                )
                if not batch["journal"].get("submission_intent"):
                    self.checkpoint(no_post=True)
                    runtime_store.release_capacity(self.store, self.id, epoch=self.epoch)
                message = (
                    str(failure)
                    if isinstance(failure, ValueError)
                    else "The runtime coordinator stopped. Reconnect explicitly; "
                    "saved results are retained."
                )
                interrupted = runtime_store.interrupt(
                    self.store, self.id, self.epoch, message[:700]
                )
                if state == "failed":
                    runtime_store.checkpoint(self.store, self.id, interrupted["epoch"], state=state)
                if interrupted["journal"].get("stop_intent"):
                    cancel(self.store, self.worker.credentials, self.id)
                return
            complete = all(m["state"] == "completed" for m in batch["members"])
            final_state = (
                "completed"
                if complete
                else "cancelled"
                if batch["journal"].get("stop_intent")
                else "failed"
            )
            if batch["admission"] in {"open", "draining"}:
                # A terminal allocation cannot admit more files; freeze the exact local
                # membership before closing it, without claiming remote publication.
                runtime_store.seal(self.store, self.id, self.epoch, batch["membership_revision"])
            runtime_store.checkpoint(
                self.store,
                self.id,
                self.epoch,
                state=final_state,
                admission="closed",
                message="All runtime results saved."
                if complete
                else "Runtime allocation ended before accepting any file. No input was published."
                if not batch["journal"].get("stop_intent") and _public_only(self.store, batch)
                else "Runtime batch ended; saved results kept.",
                progress=1 if complete else batch["progress"],
            )
        if complete and self.settings.ucloud_transcriber.cleanup_after_success:
            retry_cleanup(self.store, self.worker.credentials, self.id)


def run(worker, batch):
    from . import restored_owners

    restored_owners.require_owner(worker.store, "runtime_batch", batch["id"])
    Coordinator(worker, batch).execute()


def _public_only(store, batch):
    journal = batch["journal"]
    attempts = store.all(
        "SELECT a.input_receipt,a.remote_receipt FROM runtime_batch_attempts a "
        "JOIN runtime_batch_members m ON m.job_id=a.job_id WHERE m.batch_id=?",
        (batch["id"],),
    )
    unpublished = all(
        not json.loads(a["input_receipt"]).get("publish_intent")
        and not json.loads(a["remote_receipt"]).get("accepted")
        and not json.loads(a["remote_receipt"]).get("outcome")
        for a in attempts
    )
    return bool(
        (journal.get("job_id") or journal.get("no_post"))
        and not runtime_store._uncertain(journal)
        and journal.get("owned_folder")
        and journal.get("folder_created")
        and bool(attempts)
        and bool(batch["members"])
        and unpublished
        and all(
            m["state"] in {"failed", "cancelled"}
            and not m["input_receipt"].get("publish_intent")
            and not m["remote_receipt"].get("accepted")
            and not m["remote_receipt"].get("outcome")
            for m in batch["members"]
        )
    )


def retry_cleanup(store, credentials, batch_id):
    from . import restored_owners

    restored_owners.require_owner(store, "runtime_batch", batch_id)
    batch = runtime_store.get(store, batch_id)
    journal, stage = batch["journal"], "preconditions"
    if journal.get("cleanup_state") in native.CLEANED:
        return {"cleanup_state": journal["cleanup_state"], **_scope(batch)}
    try:
        settings = _settings(batch)
        expected = remote_folder(settings, batch_id)
        if (
            batch["state"] == "running"
            or batch["admission"] not in {"sealed", "closed"}
            or (not journal.get("job_id") and not _public_only(store, batch))
            or journal.get("owned_folder") != expected
            or not journal.get("folder_created")
        ):
            raise native.CleanupError(
                "Runtime cleanup requires a closed owned allocation.",
                stage,
                "ownership_or_close_missing",
            )
        stage = "local_validation"
        deleted = runtime_store.deleted_members(store, batch)
        for member in [] if _public_only(store, batch) else batch["members"]:
            if member["job_id"] in deleted:
                continue  # Its recording was deleted with explicit consent (ADR 0101).
            if member["state"] != "completed" or not member["result_id"]:
                raise native.CleanupError(
                    "Save every runtime result before moving shared files to Trash.",
                    stage,
                    "result_missing",
                )
            runtime_store._verify_files(
                store,
                member["job_id"],
                member["recording_id"],
                member["output_receipt"].get("files"),
            )
        stage = "terminal_check"
        with _client(credentials, batch) as client:
            if journal.get("job_id"):
                batch, state = _observe(store, batch, client)
                if state not in api.TERMINAL:
                    raise native.CleanupError(
                        "The runtime allocation is still writing; remote files were retained.",
                        stage,
                        "remote_not_terminal",
                    )
            runtime_store.checkpoint(
                store,
                batch_id,
                batch["epoch"],
                state=batch["state"] if _public_only(store, batch) else "completed",
                admission="closed",
                journal_update={
                    "cleanup_state": "pending",
                    "cleanup_attempted_at": now(),
                    "cleanup_epoch": batch["epoch"],
                },
            )
            stage = "trash"
            native.trash_owned_folder(client, expected)
    except (OSError, ValueError, UCloudAPIError, httpx.HTTPError) as exc:
        diagnostic = native.cleanup_diagnostic(exc, stage)
        # A refused precondition cannot invent a new pending cleanup obligation.
        if stage == "trash":
            diagnostic["cleanup_state"] = "pending"
        elif "cleanup_state" in journal:
            diagnostic["cleanup_state"] = journal["cleanup_state"]
        else:
            diagnostic.pop("cleanup_state", None)
        runtime_store.checkpoint(
            store,
            batch_id,
            batch["epoch"],
            journal_update={**diagnostic, "cleanup_attempted_at": now()},
        )
        if stage in {"preconditions", "local_validation", "terminal_check"}:
            raise
        return {"cleanup_state": "pending", **_scope(batch)}
    runtime_store.checkpoint(
        store,
        batch_id,
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
    return {"cleanup_state": "trashed", **_scope(batch)}


def _historical_activity(store, identifier):
    """Present archived SQL metadata without interpreting any operational JSON receipt."""
    batch = store.one(
        "SELECT id,project_id,substr(state,1,32) AS state,progress,"
        "substr(created,1,80) AS created,substr(updated,1,80) AS updated,"
        "(SELECT COUNT(*) FROM runtime_batch_members WHERE batch_id=runtime_batch_runs.id) "
        "AS member_count FROM runtime_batch_runs WHERE id=?",
        (identifier,),
    )
    members = []
    for member in store.all(
        "SELECT m.job_id,m.recording_id,substr(m.state,1,32) AS state,"
        "substr(r.name,1,240) AS name,r.id AS present_recording,"
        "CASE WHEN t.id IS NOT NULL AND x.result_id IS NULL THEN t.id END AS result_id "
        "FROM runtime_batch_members m LEFT JOIN recordings r ON r.id=m.recording_id "
        "AND r.project_id=? LEFT JOIN transcript_results t ON t.id=m.result_id "
        "AND t.recording_id=m.recording_id LEFT JOIN result_trash x ON x.result_id=t.id "
        "WHERE m.batch_id=? ORDER BY m.ordinal LIMIT 100",
        (batch["project_id"], identifier),
    ):
        members.append(
            {
                "job_id": member["job_id"],
                "recording_id": member["present_recording"],
                "recording_name": str(member["name"])[:240]
                if member["present_recording"]
                else "Removed recording",
                "removed": member["present_recording"] is None,
                "state": "historical",
                "archived_state": str(member["state"])[:32],
                "progress": 0,
                "message": "Restored history.",
                "result_id": member["result_id"],
                "status_label": "Restored history",
                "stage": None,
                "stage_label": None,
                "reason_code": None,
                "reason": None,
            }
        )
    progress = batch["progress"]
    progress = (
        min(1, max(0, progress))
        if isinstance(progress, (int, float)) and math.isfinite(progress)
        else 0
    )
    message = "Restored history. Source allocations cannot be controlled from this library."
    counts = dict.fromkeys(
        ("completed", "failed", "cancelled", "interrupted", "waiting", "running", "collecting"), 0
    )
    counts.update(total=batch["member_count"], unknown=batch["member_count"])
    info = {
        "id": identifier,
        "state": "historical",
        "archived_state": str(batch["state"])[:32],
        "progress": progress,
        "message": message,
        "created": str(batch["created"])[:80],
        "updated": str(batch["updated"])[:80],
        "admission": "historical",
        "members": members,
        "member_count": batch["member_count"],
        "file_progress": {
            "counts": counts,
            "active_files": [],
        },
        "unresolved": False,
        "can_retry": False,
        "can_cancel": False,
        "can_cleanup": False,
        "can_discard": False,
        "discard_confirmed": False,
        "submission_uncertain": False,
        "retryable_members": 0,
    }
    return {
        "id": "runtime-batch-" + identifier,
        "kind": "enhance",
        "recording_id": None,
        "recording_name": f"CBS Runtime batch ({batch['member_count']} files)",
        "project_id": batch["project_id"],
        "provider_name": "CBS Runtime · Restored history",
        "model": None,
        "execution": "ucloud",
        "runtime_batch_id": identifier,
        "runtime_batch": info,
        "state": "historical",
        "archived_state": info["archived_state"],
        "progress": progress,
        "message": message,
        "created": info["created"],
        "updated": info["updated"],
        "ucloud_job_id": None,
        "ucloud_server": None,
        "ucloud_submission_uncertain": False,
        "ucloud_stop_requested": False,
        "ucloud_cancel_requested": False,
        "ucloud_cleanup_state": "historical",
        "restored_history": True,
    }


def activity(store):
    from . import remote_release

    obligations = {b["id"] for b in runtime_store.obligations(store)}
    result = []
    stages = {
        message: (stage, message.removeprefix("UCloud · ").removesuffix("…"))
        for stage, message in api.STAGES.items()
    }
    labels = {
        "queued": "Waiting in local queue",
        "preparing": "Preparing input",
        "uploading": "Uploading input",
        "accepted": "Waiting for inference",
        "running": "Processing",
        "collecting": "Saving locally",
        "completed": "Saved locally",
        "failed": "Failed",
        "cancelled": "Cancelled",
        "interrupted": "Interrupted",
    }
    reasons = {
        "stopped": "Stopped before completion.",
        "interrupted": "Processing was interrupted; saved results are retained.",
        "not_dispatched": "Inference did not start for this file.",
        "owner_absent": "The runtime lost contact with its coordinator.",
        "timeout": "The runtime task timed out.",
        "memory": "The runtime ran out of memory.",
        "runtime": "The prepared runtime could not run this task.",
        "dependencies": "The runtime has missing or incompatible dependencies.",
        "cache": "Required model files are not prepared.",
        "access": "Model access was denied.",
        "ownership_conflict": "The task's ownership receipts disagree.",
        "input_changed": "The prepared input changed.",
        "invalid_output": "The runtime output could not be validated.",
        "processing": "Processing did not finish. No specific cause was reported.",
    }
    recent = store.all("SELECT id FROM runtime_batch_runs ORDER BY created DESC LIMIT 200")
    listed = {row["id"] for row in recent}
    for row in [*recent, *({"id": identifier} for identifier in sorted(obligations - listed))]:
        from . import restored_owners

        with store.connection() as db:
            historical = restored_owners.is_restored(db, "runtime_batch", row["id"])
        if historical:
            result.append(_historical_activity(store, row["id"]))
            continue
        batch = runtime_store.get(store, row["id"])
        journal = batch["journal"]
        scripts_only = _public_only(store, batch)
        members = []
        counts = dict.fromkeys(
            (
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "waiting",
                "running",
                "collecting",
                "unknown",
            ),
            0,
        )
        active_files = []
        for member in batch["members"]:
            rows = store.all(
                "SELECT r.name,j.progress,j.message FROM jobs j JOIN recordings r "
                "ON r.id=j.recording_id WHERE j.id=?",
                (member["job_id"],),
            )
            live = (
                rows[0]
                if rows
                else {
                    "name": "Removed recording",
                    "progress": 0,
                    "message": "This recording is no longer in the library.",
                }
            )
            visible = member["result_id"] and store.all(
                "SELECT id FROM transcript_results WHERE id=? AND id NOT IN "
                "(SELECT result_id FROM result_trash)",
                (member["result_id"],),
            )
            phase = member["state"]
            receipt = member["output_receipt"]
            if phase == "completed" and (
                not member["result_id"]
                or member["attempt_state"] != "complete"
                or not isinstance(receipt, dict)
                or any(
                    receipt.get(key) != expected
                    for key, expected in (
                        ("batch_id", batch["id"]),
                        ("task_id", member["task_id"]),
                        ("attempt", member["attempt"]),
                    )
                )
            ):
                phase = "unknown"
            # Accepted/uploaded inputs are not inference. Both running receipts are written
            # only after the supervisor identifies this exact accepted task as its worker.
            active = (
                phase == "running"
                and member["attempt_state"] == "running"
                and member["remote_receipt"].get("accepted") is True
            )
            if phase == "running" and not active:
                phase = "unknown"
            category = (
                "waiting" if phase in {"queued", "preparing", "uploading", "accepted"} else phase
            )
            counts[category if category in counts else "unknown"] += 1
            stage, stage_label = stages.get(live["message"], (None, "stage not reported"))
            stage_label = stage_label[:1].upper() + stage_label[1:]
            if not active:
                stage, stage_label = None, None
            elif rows:
                active_files.append(
                    {
                        "job_id": member["job_id"],
                        "recording_name": live["name"],
                        "stage": stage,
                        "stage_label": stage_label,
                    }
                )
            codes = [
                code
                for code in (
                    member["remote_receipt"].get("error_code"),
                    member["attempt_error_code"],
                )
                if isinstance(code, str) and code in ERROR_CODES
            ]
            reason_code = next((code for code in codes if code != "processing"), None)
            reason_code = reason_code or ("processing" if codes else None)
            reason = (
                reasons.get(reason_code)
                if phase in {"failed", "cancelled", "interrupted"}
                else None
            )
            if phase == "cancelled" and reason_code in {None, "processing"}:
                reason = "Cancelled before completion."
            if (
                phase == "failed"
                and reason_code in {None, "processing"}
                and live["message"] in runtime_store.LOCAL_FAILURE_MESSAGES
            ):
                reason = live["message"]
            members.append(
                {
                    "job_id": member["job_id"],
                    "recording_id": member["recording_id"],
                    "recording_name": live["name"],
                    "removed": not bool(rows),
                    "state": member["state"],
                    "progress": live["progress"],
                    "message": live["message"],
                    "result_id": member["result_id"] if visible else None,
                    "status_label": labels.get(phase, "Status unavailable"),
                    "stage": stage,
                    "stage_label": stage_label,
                    "reason_code": reason_code,
                    "reason": reason,
                }
            )
        uncertain = runtime_store._uncertain(journal)
        discarded = bool(journal.get("discard_confirmed"))
        setup_retry = runtime_store.setup_retryable(batch)
        release = _release_info(batch, uncertain)
        info = {
            k: batch[k]
            for k in ("id", "state", "progress", "message", "created", "updated", "admission")
        }
        info.update(
            members=members,
            member_count=len(members),
            file_progress={
                "counts": {"total": len(members), **counts},
                "active_files": active_files,
            },
            unresolved=batch["id"] in obligations,
            can_retry=batch["state"] in {"failed", "interrupted", "cancelled"}
            and (bool(journal.get("job_id")) or setup_retry)
            and not uncertain
            and not discarded,
            retry_kind="setup" if setup_retry else "reconnect",
            setup_upload_failure=journal.get("setup_upload_failure"),
            can_cancel=not journal.get("stop_intent")
            and not discarded
            and (
                batch["state"] in {"queued", "running"}
                or any(m["state"] not in runtime_store.MEMBER_TERMINAL for m in batch["members"])
                or bool(journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL)
            ),
            can_cleanup=(bool(journal.get("job_id")) or scripts_only)
            and batch["state"] != "running"
            and journal.get("cleanup_state") not in native.CLEANED
            and (all(m["state"] == "completed" for m in members) or scripts_only),
            can_discard=batch["state"] not in {"queued", "running"}
            and batch["admission"] in {"sealed", "closed"}
            and bool(journal.get("job_id"))
            and not uncertain
            and bool(journal.get("folder_created"))
            and journal.get("owned_folder") == remote_folder(_settings(batch), batch["id"])
            and journal.get("cleanup_state") not in native.CLEANED
            and not scripts_only
            and any(not m["result_id"] for m in batch["members"]),
            discard_confirmed=discarded,
            submission_uncertain=uncertain,
            retryable_members=0
            if discarded
            else len(_retryable(batch)) + len(_local_recoverable(store, batch)),
            release=release,
            auto_release_note=None
            if journal.get("cleanup_state") in native.CLEANED
            else remote_release.note(store, "runtime_batch", batch["id"]),
        )
        if release and release["kind"] == "deletion":
            # Delete's decision replaces every ordinary action; only its Trash step remains.
            info.update(can_retry=False, can_cancel=False, can_cleanup=False, can_discard=False)
        owner = {
            k: batch[k]
            for k in ("state", "progress", "message", "created", "updated", "project_id")
        }
        if discarded and "reconnect" in batch["message"].casefold():
            info["message"] = owner["message"] = "Runtime batch ended; saved results kept."
        owner.update(
            id="runtime-batch-" + batch["id"],
            kind="enhance",
            recording_id=None,
            recording_name=(
                "UCloud cleanup for a deleted recording"
                if release["target_kind"] == "recording"
                else "UCloud cleanup for a deleted project"
            )
            if release and release["kind"] == "deletion"
            else f"CBS Runtime batch ({len(members)} files)",
            provider_name=_label(batch),
            model=batch["identity"]["options"]["whisper_model"]
            if batch["identity"]["operation"] != "enhancement"
            else None,
            execution="ucloud",
            runtime_batch_id=batch["id"],
            runtime_batch=info,
            ucloud_job_id=journal.get("job_id"),
            ucloud_server=batch["identity"]["settings"]["base_url"],
            ucloud_submission_uncertain=uncertain,
            ucloud_stop_requested=bool(journal.get("termination_requested")),
            ucloud_cancel_requested=bool(journal.get("stop_intent")),
            remote_release=release,
        )
        for field in (
            "remote_state",
            "cleanup_state",
            "cleanup_error",
            "cleanup_stage",
            "cleanup_error_code",
            "cleanup_http_status",
            "cleanup_attempted_at",
        ):
            owner["ucloud_" + field] = journal.get(field)
        result.append(owner)
    return result


def _release_info(batch, uncertain):
    """Activity projection of a Delete or automatic release; no credentials or text bodies."""
    journal = batch["journal"]
    decision = journal.get("release_decision")
    discard = journal.get("discard_decision")
    if isinstance(decision, dict):
        target = decision.get("target") if isinstance(decision.get("target"), dict) else {}
        kind = "deletion"
    elif isinstance(discard, dict) and discard.get("decided_by") == "automatic":
        target, kind = {}, "automatic"
    else:
        return None
    return {
        "owner_kind": "runtime_batch",
        "id": batch["id"],
        "kind": kind,
        "target_kind": target.get("kind")
        if target.get("kind") in {"project", "recording"}
        else None,
        "target_name": str(target.get("name") or "")[:240],
        "folder": remote_folder(_settings(batch), batch["id"]),
        "server": batch["identity"]["settings"]["base_url"],
        "cleanup_state": journal.get("cleanup_state"),
        "submission_uncertain": uncertain,
        "waiting_for_stop": bool(
            journal.get("job_id") and journal.get("remote_state") not in api.TERMINAL
        ),
        "can_retry": kind == "deletion"
        and journal.get("cleanup_state") not in native.CLEANED
        and not uncertain
        and batch["state"] not in {"queued", "running"},
    }


def _label(batch):
    options, operation = batch["identity"]["options"], batch["identity"]["operation"]
    capabilities = (
        "word timing and speaker labels"
        if options["align"] and options["diarize"]
        else ("word timing" if options["align"] else "speaker labels")
    )
    if operation == "enhancement":
        return "CBS Runtime · " + capabilities
    model = options["whisper_model"]
    action = (
        "passage re-transcription" if operation == "passage_retranscription" else "transcription"
    )
    return f"CBS Runtime WhisperX {model} · {action} ({capabilities})"
