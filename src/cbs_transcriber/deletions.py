"""Short-lived, finish-only deletion requests.

Confirming Delete is also explicit consent to move the deleted files' owned UCloud working
folders to UCloud Trash (ADR 0101). Remote failures never keep local data; the owner journal
outlives the deleted rows and stays in Activity until the folder is confirmed in Trash.
"""

import hashlib
import json
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import HTTPException
from ucloud_workflow import UCloudAPIError

from . import (
    deletion_files,
    input_admission,
    native_batch,
    original_inspection,
    remote_jobs,
    remote_release,
    restored_owners,
    runtime_store,
)
from .store import now

OWNER_KINDS = {"native_batch_id": "native_batch", "runtime_batch_id": "runtime_batch"}
SINGLE_REMOTE = (
    "Jobs were cancelled where possible; recordings were kept. A single UCloud job still "
    "needs recovery, remote cleanup or reconciliation in Activity before deleting."
)

MAX_OPERATIONS = 128
MAX_RECORDINGS = 2000
MAX_JOBS = 20000
MAX_CONTROL_BYTES = 32 * 1024 * 1024
OPERATION_SECONDS = 1800


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _conflict(message):
    raise HTTPException(409, message)


def _needs_stop(batch, key):
    run = batch["journal"] if key == "runtime_batch_id" else batch["params"].get("ucloud_run", {})
    return batch["state"] in {"queued", "running"} or bool(
        run.get("job_id") and run.get("remote_state") not in remote_jobs.TERMINAL
    )


@dataclass
class Operation:
    identifier: str
    kind: str
    target: str
    recordings: tuple
    metadata: str
    jobs: dict
    allowed: set
    batches: dict
    affected: tuple
    target_name: str = ""
    cancel_count: int = 0
    impact: tuple = ()
    blockers: tuple = ()
    fingerprint: str = ""
    created: float = field(default_factory=time.monotonic)
    started: bool = False
    cancelling: bool = False
    controls: dict = field(default_factory=dict)
    cancelled: set = field(default_factory=set)
    complete: bool = False
    invalidated: bool = False
    releasing: bool = False
    released: bool = False
    reply: dict | None = None


class Deletions:
    """Coordinates HTTP continuations without a durable or automatic deletion queue."""

    def __init__(self, store, queue, *, acquire_file_lease=False):
        self.store, self.queue = store, queue
        self.operations = {}
        self.acquire_file_lease = acquire_file_lease
        self.local_files = {}
        self._file_blocked = False
        self._file_action_lock = threading.RLock()
        self.staging = nullcontext

    @contextmanager
    def _file_action(self):
        if not self._file_action_lock.acquire(blocking=False):
            _conflict("Wait for the current local file operation to finish, then retry.")
        try:
            with self._file_lease():
                yield
        finally:
            self._file_action_lock.release()

    def _file_lease(self):
        # Production borrows the lifespan lease; worker threads must never re-enter
        # its RLock. No-worker/test apps take their own transient operation lease.
        return (
            self.store.library_lock()
            if self.acquire_file_lease and not self.store._lease_depth
            else nullcontext()
        )

    @property
    def file_recovery_blocked(self):
        # This is read by async middleware: never block the event loop on the claim lock.
        return self._file_blocked

    def _remember_files(self, summary):
        with self.queue.lock:
            if summary["state"] == "complete":
                self.local_files.pop(summary["id"], None)
            else:
                self.local_files[summary["id"]] = summary
            self._file_blocked = any(
                item.get("committed") is not True for item in self.local_files.values()
            )
        return summary

    def recover_files(self):
        with self._file_action():
            for summary in deletion_files.recover(self.store):
                self._remember_files(summary)

    def retry_files(self, identifier):
        with self._file_action():
            return self._remember_files(deletion_files.retry(self.store, identifier))

    def require_files_idle(self):
        with self.queue.lock:
            if self.local_files:
                _conflict(
                    "Finish local file recovery or cleanup in Activity before moving storage "
                    "or switching libraries."
                )

    def file_activity(self):
        with self.queue.lock:
            return [
                {
                    "id": "local-deletion-" + item["id"],
                    "kind": "local-delete",
                    "state": "completed" if item.get("committed") is True else "interrupted",
                    "recording_id": None,
                    "recording_name": item.get("target_name") or "Deleted project or recording",
                    "message": item["message"],
                    "progress": 1 if item.get("committed") is True else 0,
                    "created": item.get("created", ""),
                    "updated": item.get("updated", item.get("created", "")),
                    "local_deletion": item,
                }
                for item in self.local_files.values()
            ]

    def invalidate_jobs(self, identifiers):
        """An explicit Retry revokes older deletion consent, even if it finishes quickly."""
        identifiers = set(identifiers)
        with self.queue.lock:
            for operation in self.operations.values():
                if identifiers & operation.allowed:
                    operation.invalidated = True

    def _recordings(self, kind, identifier):
        if kind == "recording":
            self.store.one("SELECT id FROM recordings WHERE id=?", (identifier,))
            return (identifier,)
        self.store.one("SELECT id FROM projects WHERE id=?", (identifier,))
        rows = self.store.all(
            "SELECT id FROM recordings WHERE project_id=? ORDER BY id LIMIT ?",
            (identifier, MAX_RECORDINGS + 1),
        )
        if len(rows) > MAX_RECORDINGS:
            _conflict("Delete recordings in smaller groups before deleting this project.")
        return tuple(row["id"] for row in rows)

    def _metadata(self, kind, identifier, recordings):
        projects = {}
        rows, results, reviews = [], [], []
        has_reviews = self.store.one("PRAGMA user_version")["user_version"] >= 19
        for recording_id in recordings:
            row = self.store.one(
                "SELECT id,project_id,name,filename,kind,created,revision,notes_revision,"
                "collection_id,provider_id,active_transcript_id,language_override,"
                "language_revision FROM recordings WHERE id=?",
                (recording_id,),
            )
            row["result_role"] = self.store.all(
                "SELECT working_version_id,revision FROM recording_result_roles "
                "WHERE recording_id=?",
                (recording_id,),
            )
            rows.append(row)
            projects[row["project_id"]] = self._project_metadata(row["project_id"])
            results.extend(
                self.store.all(
                    "SELECT t.id,t.updated,(SELECT v.id FROM result_versions v "
                    "WHERE v.result_id=t.id ORDER BY v.sequence DESC LIMIT 1) AS version_id,"
                    "(SELECT trashed_at FROM result_trash WHERE result_id=t.id) AS trashed_at "
                    "FROM transcript_results t WHERE t.recording_id=? ORDER BY t.id LIMIT ?",
                    (recording_id, MAX_JOBS + 1 - len(results)),
                )
            )
            if len(results) > MAX_JOBS:
                _conflict("This deletion has too much result history. Keep the recordings.")
            if has_reviews:
                reviews.extend(
                    self.store.all(
                        "SELECT p.result_id,CASE WHEN length(CAST(p.segment_id AS BLOB))<=800 "
                        "THEN p.segment_id END AS segment_id,p.passage_sha256,p.status,"
                        "p.revision,CASE WHEN length(CAST(p.updated AS BLOB))<=64 "
                        "THEN p.updated END AS updated FROM passage_reviews p "
                        "JOIN transcript_results t ON t.id=p.result_id WHERE t.recording_id=? "
                        "ORDER BY p.result_id COLLATE BINARY,p.segment_id COLLATE BINARY LIMIT ?",
                        (recording_id, MAX_JOBS + 1 - len(reviews)),
                    )
                )
                if len(reviews) > MAX_JOBS or any(
                    row["segment_id"] is None or row["updated"] is None for row in reviews
                ):
                    _conflict(
                        "This deletion has too much passage review metadata. Keep the recordings."
                    )
        if kind == "project":
            projects[identifier] = self._project_metadata(identifier)
        return _digest([rows, results, projects, reviews])

    def _project_metadata(self, identifier):
        return self.store.one(
            "SELECT id,name,classification,storage_dir,notes_revision,default_language,"
            "language_revision FROM projects WHERE id=?",
            (identifier,),
        )

    def _job(self, identifier, *, max_bytes=MAX_CONTROL_BYTES):
        # Completed chunks and reviewed text are not deletion-control metadata.
        row = self.store.one(
            "SELECT id,recording_id,kind,state,created,"
            "length(CAST(params AS BLOB)) AS params_bytes,"
            "CASE WHEN length(CAST(params AS BLOB))<=? THEN params ELSE NULL END AS params "
            "FROM jobs WHERE id=?",
            (max_bytes, identifier),
        )
        if row["params"] is None:
            _conflict("This job's control metadata is too large. Its recording was kept.")
        row["params"] = json.loads(row["params"])
        if not isinstance(row["params"], dict):
            _conflict("A saved job cannot be verified. Its recording was kept.")
        return row

    @staticmethod
    def _job_identity(job):
        params = job["params"]
        frozen = {
            key: params[key]
            for key in (
                "settings",
                "provider_id",
                "options",
                "enhancement",
                "source",
                "audio_input",
                "native_batch_id",
                "native_allocation",
                "runtime_batch_id",
                "parent_transcription_id",
                "followup",
            )
            if key in params
        }
        if "input_intent" in params:
            intent = params["input_intent"]
            frozen["intent"] = {
                key: intent[key] for key in ("request_id", "accepted_sha256", "generation")
            }
        return _digest([job["id"], job["recording_id"], job["kind"], job["created"], frozen])

    def _jobs(self, recordings):
        result = {}
        used = 0
        for identifier in recordings:
            with self.store.connection() as db:
                operational = restored_owners.sql_exclusion(db, "job", "jobs.id")
            for row in self.store.all(
                "SELECT id FROM jobs WHERE recording_id=? AND "
                + operational
                + " ORDER BY id LIMIT ?",
                (identifier, MAX_JOBS + 1 - len(result)),
            ):
                result[row["id"]] = self._job(row["id"], max_bytes=MAX_CONTROL_BYTES - used)
                used += result[row["id"]]["params_bytes"]
                if len(result) > MAX_JOBS or used > MAX_CONTROL_BYTES:
                    _conflict("This deletion has too much job history. Keep the recordings.")
        return result

    def _scope(self, recordings):
        jobs = self._jobs(recordings)
        allowed, batches, affected = set(jobs), {}, set()
        used = sum(job["params_bytes"] for job in jobs.values())
        pending = list(jobs.values())
        while pending:
            job = pending.pop()
            followup = job["params"].get("followup")
            if followup:
                child_id = followup["id"]
                if child_id not in allowed:
                    allowed.add(child_id)
                    found = self.store.all("SELECT id FROM jobs WHERE id=?", (child_id,))
                    if found:
                        child = self._job(child_id, max_bytes=MAX_CONTROL_BYTES - used)
                        if (
                            child["recording_id"] != job["recording_id"]
                            or child["params"].get("parent_transcription_id") != job["id"]
                        ):
                            _conflict("A saved follow-up no longer belongs to this recording.")
                        jobs[child_id] = child
                        used += child["params_bytes"]
                        pending.append(child)
            for key in ("native_batch_id", "runtime_batch_id"):
                batch_id = job["params"].get(key)
                if not batch_id or (key, batch_id) in batches:
                    continue
                batch = (
                    native_batch.batch_for_job(self.store, job)
                    if key == "native_batch_id"
                    else runtime_store.for_job(self.store, job)
                )
                members = tuple(
                    sorted(
                        (member["job_id"], member["recording_id"]) for member in batch["members"]
                    )
                )
                batches[key, batch_id] = members
                journal = (
                    batch["journal"]
                    if key == "runtime_batch_id"
                    else batch["params"].get("ucloud_run", {})
                )
                settled = journal.get("cleanup_state") in {"trashed", "deleted"} and (
                    journal.get("remote_state") in remote_jobs.TERMINAL
                )
                # A completed allocation is still checked for obligations before deletion,
                # but stopping it must not cancel unrelated successful descendants.
                # Settled failed/interrupted owners retain historical members whose jobs
                # may already be deleted; they have no shared work left to cancel either.
                if not _needs_stop(batch, key) and (
                    settled or batch["state"] not in {"queued", "running", "interrupted", "failed"}
                ):
                    continue
                deleted = (
                    native_batch.deleted_members(self.store, batch)
                    if key == "native_batch_id"
                    else runtime_store.deleted_members(self.store, batch)
                )
                for job_id, recording_id in members:
                    if job_id in deleted:
                        allowed.add(job_id)  # Immutable shared Stop scope still includes it.
                        continue
                    affected.add(recording_id)
                    if job_id not in allowed:
                        allowed.add(job_id)
                        child = self._job(job_id, max_bytes=MAX_CONTROL_BYTES - used)
                        jobs[job_id] = child
                        used += child["params_bytes"]
                        pending.append(child)
            if len(allowed) > MAX_JOBS or used > MAX_CONTROL_BYTES:
                _conflict("This shared cancellation is too large. Stop it in Activity first.")
        return jobs, allowed, batches, tuple(sorted(affected - set(recordings)))

    def _impact(self, recordings, batches):
        """Owned UCloud folders this Delete moves to Trash, or keeps for other files."""
        entries = []
        for (key, identifier), members in sorted(batches.items()):
            deleting = sorted(job for job, recording in members if recording in recordings)
            entry = remote_release.impact(self.store, OWNER_KINDS[key], identifier, deleting)
            if entry:
                entries.append(entry)
        return tuple(entries)

    def _blockers(self, jobs):
        """Single-job journals live in the job rows themselves, so they still keep recordings."""
        names = set()
        for job in jobs.values():
            if (
                job["state"] not in {"queued", "running"}
                and not any(job["params"].get(key) for key in OWNER_KINDS)
                and remote_jobs.unresolved_for(self.store, job)
            ):
                names.add(job["recording_id"])
        return tuple(
            self.store.one("SELECT id,name FROM recordings WHERE id=?", (identifier,))
            for identifier in sorted(names)
        )

    def _capture(self, kind, identifier):
        recordings = self._recordings(kind, identifier)
        jobs, allowed, batches, affected = self._scope(recordings)
        operation = Operation(
            uuid4().hex,
            kind,
            identifier,
            recordings,
            self._metadata(kind, identifier, recordings),
            {key: self._job_identity(job) for key, job in jobs.items()},
            allowed,
            batches,
            affected,
        )
        operation.target_name = self.store.one(
            "SELECT name FROM "
            + ("projects" if kind == "project" else "recordings")
            + " WHERE id=?",
            (identifier,),
        )["name"][:240]
        operation.controls = {
            key: control for key, job in jobs.items() if (control := self._control(job)) is not None
        }
        operation.impact = self._impact(set(recordings), batches)
        operation.blockers = self._blockers(jobs)
        operation.fingerprint = _digest(
            [operation.identifier, list(affected), list(operation.impact)]
        )
        operation.cancel_count = sum(job["state"] in {"queued", "running"} for job in jobs.values())
        self.operations[operation.identifier] = operation
        return operation

    def _summary(self, operation):
        return {
            "operation_id": operation.identifier,
            "fingerprint": operation.fingerprint,
            "cancel_jobs": operation.cancel_count,
            "affected_recordings": [
                self.store.one("SELECT id,name FROM recordings WHERE id=?", (affected,))
                for affected in operation.affected
            ],
            "remote_folders": list(operation.impact),
            "remote_blockers": list(operation.blockers),
        }

    def preview(self, kind, identifier):
        """Capture one operation and describe everything Delete will do beyond local removal."""
        with self.queue.lock:
            self._expire()
            if len(self.operations) >= MAX_OPERATIONS:
                _conflict("Too many pending deletion requests. Finish one or try again later.")
            return self._summary(self._capture(kind, identifier))

    def _validate(self, operation):
        try:
            if operation.invalidated:
                raise ValueError
            if (
                self._recordings(operation.kind, operation.target) != operation.recordings
                or self._metadata(operation.kind, operation.target, operation.recordings)
                != operation.metadata
            ):
                raise ValueError
            jobs = self._jobs(operation.recordings)
            if set(jobs) - set(operation.jobs):
                raise ValueError
            for job_id, fingerprint in operation.jobs.items():
                job = self._job(job_id)
                if self._job_identity(job) != fingerprint:
                    raise ValueError
                if operation.started and job["state"] == "queued":
                    raise ValueError
            for (key, identifier), expected in operation.batches.items():
                batch = (
                    self.store.native_batch(identifier)
                    if key == "native_batch_id"
                    else runtime_store.get(self.store, identifier)
                )
                if (
                    tuple(
                        sorted(
                            (member["job_id"], member["recording_id"])
                            for member in batch["members"]
                        )
                    )
                    != expected
                ):
                    raise ValueError
            for job_id in operation.cancelled:
                if self._job(job_id)["state"] in {"queued", "running"}:
                    raise ValueError
            for job_id, expected in operation.controls.items():
                if self._control(self._job(job_id)) != expected:
                    raise ValueError
        except (KeyError, ValueError, TypeError):
            _conflict(
                "This deletion changed or newer work started. Recordings were kept. "
                "Review them and choose Delete again."
            )

    def _control(self, job):
        if job["kind"] == "source-inspect":
            cache = original_inspection.read(self.store, job["recording_id"])
            return ("source", cache["generation"], cache["epoch"], cache["cancel_requested"])
        intent = job["params"].get("input_intent")
        if intent is not None:
            return ("intent", intent["generation"], intent["epoch"], intent["cancel_requested"])
        return None

    def _cancel(self, operation):
        # Capture before releasing the lock. Generic cancellation performs any network I/O
        # outside that lock and fences shared ownership against this complete allowlist.
        with self.queue.lock:
            if operation.started:
                return
            self._validate(operation)
            generic = []
            for identifier in sorted(operation.jobs):
                job = self._job(identifier)
                shared_active = any(
                    _needs_stop(
                        (
                            self.store.native_batch(batch_id)
                            if key == "native_batch_id"
                            else runtime_store.get(self.store, batch_id)
                        ),
                        key,
                    )
                    for key in ("native_batch_id", "runtime_batch_id")
                    if (batch_id := job["params"].get(key))
                )
                if job["state"] not in {"queued", "running"} and not shared_active:
                    continue
                if job["kind"] == "source-inspect":
                    cache = original_inspection.read(self.store, job["recording_id"])
                    original_inspection.cancel(
                        self.store, identifier, generation=cache["generation"], epoch=cache["epoch"]
                    )
                    operation.controls[identifier] = self._control(self._job(identifier))
                elif "input_intent" in job["params"]:
                    intent = job["params"]["input_intent"]
                    input_admission.cancel(
                        self.store, identifier, epoch=intent["epoch"], phase=intent["phase"]
                    )
                    operation.controls[identifier] = self._control(self._job(identifier))
                else:
                    generic.append(identifier)
            operation.started = True
            operation.cancelling = True
        try:
            handled = set()
            for identifier in generic:
                job = self._job(identifier)
                allocation = next(
                    (
                        (key, job["params"][key])
                        for key in ("native_batch_id", "runtime_batch_id")
                        if job["params"].get(key)
                    ),
                    None,
                )
                if allocation in handled:
                    continue
                try:
                    self.queue.cancel(identifier, allowed_job_ids=operation.allowed)
                except runtime_store.StopScopeChanged:
                    raise
                except (ValueError, OSError, UCloudAPIError, httpx.HTTPError):
                    if allocation is None:
                        raise
                    # The owner's Stop intent is durable; the release stage retries the
                    # remote termination before any Trash request and journals failures.
                if allocation:
                    handled.add(allocation)
            self.queue.notify()
        finally:
            with self.queue.lock:
                operation.cancelling = False
                for identifier in operation.jobs:
                    if self._job(identifier)["state"] == "cancelled":
                        operation.cancelled.add(identifier)

    def _pending(self, operation):
        if operation.cancelling:
            return True
        identifiers = operation.allowed
        if identifiers & self.queue.coordinator.control_job_ids:
            return True
        batches = {identifier for _, identifier in operation.batches}
        if any(
            worker.active_id in identifiers
            or set(worker.active_ids) & identifiers
            or worker.active_batch_id in batches
            for worker in self.queue.workers
        ):
            return True
        if any(self.queue.busy(identifier) for identifier in operation.recordings):
            return True
        return bool(input_admission.obligations(self.store, recording_ids=operation.recordings))

    def _single_remote(self, operation):
        """Single-job UCloud journals cascade with their job rows, so they still block."""
        try:
            for identifier in operation.recordings:
                if any(
                    not any(job["params"].get(key) for key in OWNER_KINDS)
                    and remote_jobs.unresolved_for(self.store, job)
                    for job in self._jobs((identifier,)).values()
                ):
                    raise ValueError("Unresolved remote work remains.")
        except ValueError:
            _conflict(SINGLE_REMOTE)

    def _release(self, operation):
        """Delete's consent: stop, then Trash each owned folder; journals outlive the rows."""
        recordings = set(operation.recordings)
        consented = {(entry["owner_kind"], entry["owner_id"]): entry for entry in operation.impact}
        target = {"kind": operation.kind, "name": operation.target_name}
        for (key, identifier), members in sorted(operation.batches.items()):
            kind = OWNER_KINDS[key]
            with self.queue.control_owner(kind, identifier):
                with self.queue.lock:
                    self._validate(operation)
                self._release_owner(
                    operation, kind, identifier, members, recordings, consented, target
                )

    def _release_owner(self, operation, kind, identifier, members, recordings, consented, target):
        deleting = sorted(job for job, recording in members if recording in recordings)
        current = remote_release.impact(self.store, kind, identifier, deleting)
        if current is None:
            if remote_release._restored(self.store, kind, identifier):
                return
            # Nothing remote remains; still record which files left the allocation.
            if kind == "runtime_batch":
                batch = runtime_store.get(self.store, identifier)
                if batch["state"] in {"queued", "running"} or batch["journal"].get(
                    "cleanup_state"
                ) in {"trashed", "deleted"}:
                    return
                runtime_store.record_deleted_members(self.store, identifier, deleting)
            else:
                batch = self.store.native_batch(identifier)
                if batch["params"].get("ucloud_run", {}).get("cleanup_state") in {
                    "trashed",
                    "deleted",
                }:
                    return
                native_batch.record_deleted_members(self.store, identifier, deleting)
            return
        expected = consented.get((kind, identifier))
        if expected is None or any(
            current[field] != expected[field]
            for field in ("folder", "server", "remote_project", "files", "other_files")
        ):
            raise ValueError("The UCloud folder identity changed after confirmation.")
        # Only folders shown in the confirmed preview move to Trash.
        action = (
            "trash" if expected["action"] == "trash" and current["action"] == "trash" else "retain"
        )
        remote_release.release_for_deletion(
            self.store,
            self.queue.credentials,
            kind,
            identifier,
            action=action,
            deleting=deleting,
            target=target,
        )

    def _delete(self, operation):
        with self._file_action():
            return self._delete_with_lease(operation)

    def _delete_with_lease(self, operation):
        # The SQLite writer excludes concurrent metadata edits too. No cancellation,
        # waiting, decoder or remote request runs inside these locks.
        with self.queue.lock, self.store.snapshot_lock:
            self._validate(operation)
            if self._pending(operation):
                return 202, {
                    "ok": False,
                    "status": "cancelling",
                    "operation_id": operation.identifier,
                    "message": "Cancelling jobs; recordings are kept until active work stops.",
                }
            if operation.releasing:
                return 202, {
                    "ok": False,
                    "status": "releasing",
                    "operation_id": operation.identifier,
                    "message": "Moving UCloud working folders to UCloud Trash…",
                }
            self._single_remote(operation)
            release = not operation.released
            operation.releasing = release
        if release:
            # Network requests run outside the claim, snapshot and SQLite writer locks.
            try:
                self._release(operation)
            except (KeyError, ValueError):
                _conflict(
                    "A UCloud allocation changed while deleting. Recordings were kept. "
                    "Review Activity before choosing Delete again."
                )
            finally:
                with self.queue.lock:
                    operation.releasing = False
            operation.released = True
        plan = None
        outcome = None
        failure = None
        # Drain admitted HTTP writes before taking queue/snapshot locks. Settlement
        # below runs after promotion; recursive cleanup never excludes unrelated Stop.
        with self.staging():
            try:
                with self.queue.lock, self.store.snapshot_lock:
                    with self.store.connection() as db:
                        db.execute("BEGIN IMMEDIATE")
                        self._validate(operation)
                        if self._pending(operation):
                            return 202, {
                                "ok": False,
                                "status": "cancelling",
                                "operation_id": operation.identifier,
                                "message": "Cancelling jobs; recordings are kept "
                                "until active work stops.",
                            }
                        self._single_remote(operation)
                        paths = []
                        for identifier in operation.recordings:
                            recording = self.store.one(
                                "SELECT project_id FROM recordings WHERE id=?", (identifier,)
                            )
                            base = self.store.project_folder(recording["project_id"]).resolve()
                            for path in (
                                self.store.directory(identifier),
                                base / "exports" / identifier,
                            ):
                                if (
                                    path.is_symlink()
                                    or path.is_junction()
                                    or path.parent.is_symlink()
                                    or path.parent.is_junction()
                                    or not path.resolve().is_relative_to(base)
                                    or path.resolve() == base
                                ):
                                    _conflict(
                                        "Managed file ownership changed. The recordings were kept."
                                    )
                                paths.append(path)
                        project_file = None
                        if operation.kind == "project":
                            project = self.store.one(
                                "SELECT * FROM projects WHERE id=?", (operation.target,)
                            )
                            if project["storage_dir"]:
                                project_file = Path(project["storage_dir"]) / "project.json"
                        plan = deletion_files.prepare(
                            self.store,
                            operation.identifier,
                            operation.kind,
                            operation.target,
                            operation.recordings,
                            paths,
                            project_file,
                        )
                        self._remember_files(
                            {
                                "id": operation.identifier,
                                "state": "recovery_pending",
                                "committed": None,
                                "busy": True,
                                "target_kind": operation.kind,
                                "target_name": operation.target_name,
                                "created": now(),
                                "message": "Staging files for deletion…",
                            }
                        )
                        deletion_files.stage(self.store, plan)
                        for identifier in operation.recordings:
                            db.execute("DELETE FROM recordings WHERE id=?", (identifier,))
                        if operation.kind == "project":
                            db.execute("DELETE FROM projects WHERE id=?", (operation.target,))
                    operation.complete = True
                    self._remember_files(
                        {
                            "id": operation.identifier,
                            "state": "pending",
                            "committed": True,
                            "busy": True,
                            "target_kind": operation.kind,
                            "target_name": operation.target_name,
                            "created": now(),
                            "message": "Removing files for the completed deletion…",
                        }
                    )
            except Exception as error:
                failure = error
                # A lost COMMIT reply must be classified before admission reopens.
                for summary in deletion_files.journal_status(self.store):
                    self._remember_files(summary)
                    if summary["id"] == operation.identifier and summary.get("committed") is True:
                        operation.complete = True
        if failure is not None:
            error = failure
            # The transaction is CLOSED before establishing its outcome. In particular,
            # COMMIT may have succeeded before raising: never guess a rename-back.
            if plan is None:
                if isinstance(error, HTTPException):
                    raise error
                self.recover_files()
                _conflict("Files could not be staged safely. Check Activity and retry Delete.")
            outcome = self._remember_files(deletion_files.settle(self.store, plan))
            if outcome.get("committed") is not True:
                _conflict(
                    "The files could not be staged for deletion. "
                    "Close apps using the folder and retry Delete."
                    if outcome["state"] == "complete"
                    else "File recovery is pending. Use Retry in Activity "
                    "before changing this library."
                )
            operation.complete = True
        # Recursive cleanup is outside queue/snapshot/writer locks. Its journal remains
        # independently actionable when project and job rows have already gone.
        if outcome is None:
            outcome = self._remember_files(deletion_files.settle(self.store, plan))
        operation.reply = {"ok": True}
        if outcome["state"] != "complete":
            operation.reply["local_cleanup"] = outcome
        if operation.kind == "recording":
            self.store.snapshot_project(recording["project_id"])
        return 200, operation.reply

    def _expire(self):
        now = time.monotonic()
        self.operations = {
            key: value
            for key, value in self.operations.items()
            if now - value.created < OPERATION_SECONDS
        }

    def request(
        self, kind, identifier, *, operation_id=None, confirm_shared=False, fingerprint=None
    ):
        with self.queue.lock:
            self._expire()
            if operation_id:
                operation = self.operations.get(operation_id)
                if operation is None or (operation.kind, operation.target) != (kind, identifier):
                    _conflict("This deletion expired. Recordings were kept; choose Delete again.")
                if operation.complete:
                    return 200, operation.reply or {"ok": True}
                self._validate(operation)
            else:
                if len(self.operations) >= MAX_OPERATIONS:
                    _conflict("Too many pending deletion requests. Finish one or try again later.")
                operation = self._capture(kind, identifier)
            if (
                not operation.started
                and operation.affected
                and (not operation_id or not confirm_shared)
            ):
                raise HTTPException(
                    409,
                    {
                        **self._summary(operation),
                        "code": "confirm_shared_cancellation",
                        "message": "Stopping this shared allocation cancels other recordings.",
                    },
                )
            if (
                not operation.started
                and any(entry["action"] == "trash" for entry in operation.impact)
                and (not operation_id or fingerprint != operation.fingerprint)
            ):
                # Confirming Delete is consent to Trash exactly these owned folders. Folders
                # kept for other files' unsaved work are listed but need no Trash consent.
                raise HTTPException(
                    409,
                    {
                        **self._summary(operation),
                        "code": "confirm_remote_cleanup",
                        "message": "Delete also moves these UCloud working folders to UCloud "
                        "Trash. Empty Trash in UCloud to free the space.",
                    },
                )
        if not operation.started:
            try:
                self._cancel(operation)
            except ValueError:
                _conflict(
                    "The captured jobs could not be cancelled safely. Recordings were kept. "
                    "Review Activity before choosing Delete again."
                )
        return self._delete(operation)
