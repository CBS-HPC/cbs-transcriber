"""Explicit local backup, isolated restore and UCloud storage actions."""

import hashlib
import json
import os
import stat
import tempfile
import threading
import uuid
from contextlib import nullcontext
from functools import partial
from pathlib import Path

import anyio
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from . import libraries, portable_cloud, portable_retention, portable_simple, portable_transport
from .portable_capture import CaptureGate, backup_volume_hint, capture_library, captures
from .portable_restore import restore_library

_RESTORE_INDEX_LOCK = threading.RLock()


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptureRequest(Body):
    destination: str | None = Field(default=None, max_length=4000)


class PackageRequest(Body):
    folder: str = Field(min_length=1, max_length=4000)
    commit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class StorageSnapshot(Body):
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_target_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class SimpleConnection(Body):
    provider_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    server: str = Field(min_length=1, max_length=2048)
    project_id: str = Field(min_length=1, max_length=200)


class SimpleSelection(Body):
    connection: SimpleConnection
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    location: str = Field(min_length=1, max_length=2000)
    confirmed: bool = Field(strict=True)


class UploadRequest(PackageRequest, StorageSnapshot):
    pass


class CreatorAdmission(StorageSnapshot):
    backup_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ConfirmAbandon(Body):
    confirmed: bool = Field(strict=True)


class RestoreRequest(PackageRequest):
    target: str = Field(min_length=1, max_length=4000)
    cloud: bool = Field(default=False, strict=True)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_target_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class RestoreIndexEntry(Body):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    folder: str = Field(min_length=1, max_length=4000)
    commit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target: str = Field(min_length=1, max_length=4000)
    cloud: bool = Field(strict=True)
    target_identity: str | None = Field(pattern=r"^[a-f0-9]{64}$")


class BackupCheckRequest(Body):
    folder: str = Field(min_length=1, max_length=4000)
    commit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cloud: bool = Field(default=False, strict=True)
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    expected_target_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    receipt_server: str | None = Field(default=None, max_length=2048)
    receipt_project_id: str | None = Field(default=None, max_length=200)


class CloudTarget(Body):
    provider_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    parent: str = Field(min_length=1, max_length=2000)
    allowed_transfer_origins: list[str] = Field(min_length=1, max_length=20)
    whole_library_approved: bool = Field(strict=True)
    expected_server: str = Field(min_length=1, max_length=2048)
    expected_project_id: str = Field(min_length=1, max_length=200)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class RetentionPolicy(Body):
    keep_last: int = Field(ge=1, le=1000, strict=True)
    minimum_age_days: int = Field(ge=1, le=36500, strict=True)
    holds: list[str] = Field(default_factory=list, max_length=10000)


class ApplyRetention(Body):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool = Field(strict=True)


def _restore_target_identity(target):
    if target is None:
        return None
    value = {
        key: val
        for key, val in target.items()
        if key not in {"fingerprint", "whole_library_approved", "storage_revision"}
    }
    return hashlib.sha256(portable_cloud.canonical(value)).hexdigest()


def _restore_index_recover_links(root, store_root):
    """Finish publishing a linked entry if a process stopped before unlinking its temp."""
    entries, _ = portable_transport._entries(root, store_root, maximum=20000, guard=None)
    names = [root / name for name in entries]
    published = {}
    for path in names:
        if path.name.endswith(".json"):
            info = path.lstat()
            if (
                len(path.stem) == 32
                and all(c in "0123456789abcdef" for c in path.stem)
                and stat.S_ISREG(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400
                and info.st_nlink == 2
            ):
                published[(info.st_dev, info.st_ino)] = path
    for path in names:
        if not path.name.startswith(".pending-"):
            continue
        info = path.lstat()
        linked = published.get((info.st_dev, info.st_ino))
        if (
            linked is not None
            and path.name.startswith(".pending-" + linked.stem + "-")
            and stat.S_ISREG(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400
            and info.st_nlink == 2
        ):
            path.unlink()


def _restore_index(store, identifier, binding):
    """Publish immutable recovery choices before any remote or restore side effect."""
    root = store.root / "portable-restore-index"
    root.mkdir(exist_ok=True)
    portable_transport._node(root, store.root, directory=True)
    path = root / (identifier + ".json")
    raw = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 32768:
        raise ValueError("Restore choices are too large to retain for recovery.")
    temporary = None
    with _RESTORE_INDEX_LOCK:
        _restore_index_recover_links(root, store.root)
        try:
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".pending-" + identifier + "-", delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            portable_transport._node(root, store.root, directory=True)
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing, _ = portable_transport._read(
                    path, store.root, maximum=32768, collect=True
                )
                if existing != raw:
                    raise ValueError(
                        "The restore reference belongs to different choices."
                    ) from None
            else:
                temporary.unlink()
                temporary = None
                portable_transport._node(path, store.root)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def install(app, store, queue, credentials, *, mutation_allowed=None, capture_guard=None):
    gate = CaptureGate()
    # Blocked admission must leave endpoint workers free to drain earlier writes.
    admission_limiter = anyio.CapacityLimiter(1)
    app.state.portable_capture_gate = gate

    @app.middleware("http")
    async def mutation_admission(request, call_next):
        capture = request.method == "POST" and request.url.path == "/api/library/portable-backups"
        mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if not mutation or capture:
            return await call_next(request)
        # Waiting on a Condition must never block the serving event loop. Shield acquisition
        # so cancellation cannot leave an acquired mutation slot without its finalizer.
        with anyio.CancelScope(shield=True):
            slot = await anyio.to_thread.run_sync(
                partial(
                    gate.enter_mutation,
                    partial(mutation_allowed, request) if mutation_allowed else None,
                ),
                limiter=admission_limiter,
            )
        if slot is None:
            return JSONResponse(
                {"detail": "Finish local file recovery in Activity before changing this library."},
                409,
            )
        try:
            with gate.mutation(slot):
                return await call_next(request)
        finally:
            gate.leave_mutation()

    @app.post("/api/library/portable-backups", status_code=201)
    def capture(body: CaptureRequest):
        with capture_guard() if capture_guard else nullcontext():
            result = capture_library(store, queue, body.destination, gate=gate)
        return {
            **result,
            "scope": "whole_library",
            "credentials": "excluded",
            "history_provenance": "domain_transform_validation_not_checked",
        }

    @app.get("/api/library/portable-backups")
    def captured_packages():
        return {"captures": captures(store)}

    @app.post("/api/library/portable-backup-volume-hint")
    def capture_volume_hint(body: CaptureRequest):
        return {"relationship": backup_volume_hint(store.root, body.destination)}

    @app.get("/api/library/portable-backups/{backup_id}/verify")
    def verify_capture(backup_id: str):
        # The creator reference is written before publication. Its presence alone is not
        # evidence that the package was completely published or is still intact.
        portable_transport._hex(backup_id, 32)
        receipt = next((item for item in captures(store) if item["backup_id"] == backup_id), None)
        if receipt is None:
            raise ValueError("No saved backup receipt exists for this library.")
        folder = receipt.get("folder")
        if (
            not isinstance(folder, str)
            or not Path(folder).is_absolute()
            or Path(folder).name != "portable-" + backup_id
        ):
            raise ValueError("The saved backup receipt has an invalid folder.")
        observation = portable_transport.inspect_transport(
            folder, expected_commit_sha256=receipt["commit_sha256"]
        )
        if (
            observation["backup_id"] != backup_id
            or observation["manifest_sha256"] != receipt.get("manifest_sha256")
            or observation["file_mapping_count"] != receipt.get("files")
            or observation["legacy_gap_count"] != receipt.get("legacy_gaps")
            or observation["unique_object_bytes"] != receipt.get("unique_bytes")
        ):
            raise ValueError("The backup does not match its saved receipt.")
        return {
            **receipt,
            "scope": "whole_library",
            "credentials": "excluded",
            "history_provenance": "domain_transform_validation_not_checked",
            "verification": "transport_integrity_only",
        }

    @app.post("/api/library/portable-backup-check")
    def check_backup(body: BackupCheckRequest):
        if body.cloud:
            if not body.expected_revision or not body.expected_target_fingerprint:
                raise ValueError("Configure and review UCloud backup storage before checking.")
            try:
                target = portable_cloud.checked_target(
                    store, body.expected_revision, body.expected_target_fingerprint
                )
            except portable_cloud.StaleBackupStorage as exc:
                raise HTTPException(409, str(exc)) from None
            if (body.receipt_server and body.receipt_server != target["server"]) or (
                body.receipt_project_id and body.receipt_project_id != target["project_id"]
            ):
                raise ValueError("The receipt belongs to a different UCloud server or project.")
            with portable_cloud._connection(store, credentials, target, upload=False) as client:
                return portable_cloud.inspect_remote_package(
                    client, target, body.folder, expected_commit_sha256=body.commit_sha256
                )
        observation = portable_transport.inspect_transport(
            Path(body.folder).expanduser(), expected_commit_sha256=body.commit_sha256
        )
        return {"backup_id": observation["backup_id"], "verification": "transport_integrity_only"}

    @app.get("/api/library/portable-storage")
    def storage():
        try:
            target, _, problem, revision = portable_cloud._load_target(store)
        except portable_cloud.MissingBackupConnection as exc:
            target = None
            revision = exc.storage_revision
            problem = (
                "The saved backup connection no longer exists. "
                "Choose and approve a destination again."
            )
        except portable_cloud.UnavailableBackupConnection as exc:
            target = None
            revision = exc.storage_revision
            problem = "The saved backup connection is no longer a connected UCloud profile."
        return {
            "target": target,
            "restore_target_identity": _restore_target_identity(target),
            "storage_problem": problem,
            "storage_revision": revision,
            "retention": portable_retention.load_policy(store),
            "institutional_approval": "not_checked",
        }

    @app.get("/api/library/portable-simple/locations")
    def simple_locations():
        return portable_simple.locations(store, credentials)

    @app.post("/api/library/portable-simple/prepare")
    def simple_prepare(body: SimpleSelection):
        try:
            target = portable_simple.prepare(
                store,
                credentials,
                connection=body.connection.model_dump(),
                revision=body.revision,
                location=body.location,
                confirmed=body.confirmed,
            )
            return {**target, "restore_target_identity": _restore_target_identity(target)}
        except portable_cloud.StaleBackupStorage as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/portable-simple/folder")
    def simple_folder(body: StorageSnapshot):
        try:
            return portable_simple.ensure_folder(
                store,
                credentials,
                revision=body.expected_revision,
                fingerprint=body.expected_target_fingerprint,
            )
        except portable_cloud.StaleBackupStorage as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/portable-simple/stop")
    def simple_stop(body: StorageSnapshot):
        try:
            target = portable_simple.revoke(
                store, revision=body.expected_revision, fingerprint=body.expected_target_fingerprint
            )
            return {**target, "restore_target_identity": _restore_target_identity(target)}
        except portable_cloud.StaleBackupStorage as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/portable-simple/admit")
    def simple_admit(body: CreatorAdmission):
        try:
            return portable_cloud.admit_creator_upload(
                store,
                backup_id=body.backup_id,
                operation_id=body.operation_id,
                revision=body.expected_revision,
                fingerprint=body.expected_target_fingerprint,
            )
        except portable_cloud.StaleBackupStorage as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/portable-simple/{operation_id}/abandon")
    def simple_abandon(operation_id: str, body: ConfirmAbandon):
        if body.confirmed is not True:
            raise ValueError("Confirm abandoning this local backup check.")
        return portable_cloud.abandon_validating_upload(store, operation_id)

    @app.put("/api/library/portable-storage")
    def save_storage(body: CloudTarget):
        value = body.model_dump(
            exclude={"expected_server", "expected_project_id", "expected_revision"}
        )
        try:
            target, revision = portable_cloud.save_target(
                store,
                value,
                expected_server=body.expected_server,
                expected_project_id=body.expected_project_id,
                expected_revision=body.expected_revision,
            )
            return {
                **target,
                "storage_revision": revision,
                "restore_target_identity": _restore_target_identity(target),
            }
        except (portable_cloud.StaleBackupConnection, portable_cloud.StaleBackupStorage) as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/portable-upload")
    def upload(body: UploadRequest):
        try:
            target = portable_cloud.checked_target(
                store, body.expected_revision, body.expected_target_fingerprint
            )
            result = portable_cloud.upload_package(
                store,
                credentials,
                body.folder,
                expected_commit_sha256=body.commit_sha256,
                operation_id=body.operation_id,
                expected_target=target,
                expected_revision=body.expected_revision,
            )
        except portable_cloud.StaleBackupStorage as exc:
            raise HTTPException(409, str(exc)) from None
        return {
            key: result[key]
            for key in (
                "operation_id",
                "backup_id",
                "commit_sha256",
                "manifest_sha256",
                "remote_folder",
                "target",
                "state",
                "completed_at",
            )
        }

    @app.post("/api/library/portable-upload/{operation_id}/folder-preview")
    def folder_preview(operation_id: str):
        return portable_cloud.folder_preview(store, credentials, operation_id)

    @app.post("/api/library/portable-upload/{operation_id}/reconcile-folder")
    def reconcile_folder(operation_id: str, body: ApplyRetention):
        return portable_cloud.reconcile_folder(
            store, credentials, operation_id, fingerprint=body.fingerprint, confirmed=body.confirmed
        )

    @app.post("/api/library/portable-restore", status_code=201)
    def restore(body: RestoreRequest):
        identifier = body.operation_id or uuid.uuid4().hex
        source = Path(body.folder).expanduser()
        target = None
        if body.cloud:
            if not body.expected_revision or not body.expected_target_fingerprint:
                raise ValueError("Configure and review UCloud backup storage before downloading.")
            try:
                target = portable_cloud.checked_target(
                    store, body.expected_revision, body.expected_target_fingerprint
                )
            except portable_cloud.StaleBackupStorage as exc:
                raise HTTPException(409, str(exc)) from None
        _restore_index(
            store,
            identifier,
            {
                "operation_id": identifier,
                "folder": body.folder,
                "commit_sha256": body.commit_sha256,
                "target": body.target,
                "cloud": body.cloud,
                "target_identity": _restore_target_identity(target),
            },
        )
        if body.cloud:
            root = store.root / "portable-downloads"
            root.mkdir(exist_ok=True)
            source = root / identifier
            with portable_cloud._connection(store, credentials, target, upload=False) as client:
                portable_cloud.download_package(
                    client,
                    target,
                    body.folder,
                    expected_commit_sha256=body.commit_sha256,
                    destination=source,
                )
        result = restore_library(
            source,
            expected_commit_sha256=body.commit_sha256,
            target=body.target,
            operation_id=identifier,
            live_root=store.root,
            register=libraries.register,
        )
        return {
            "operation_id": identifier,
            "target": result["target"],
            "state": result["state"],
            "registered": result["registered"],
            "registration_error": result["registration_error"],
            "historical_jobs": "read_only",
            "history_provenance": "domain_transform_validation_not_checked",
            "providers": "disconnected",
            "playback": "requires_explicit_preparation",
        }

    @app.post("/api/library/prepare-restored-playback")
    def playback():
        from . import restored_owners

        created = []
        with queue.lock:
            for recording in store.all(
                "SELECT id FROM recordings WHERE status='preparing' ORDER BY id"
            ):
                with store.connection() as db:
                    exclusion = restored_owners.sql_exclusion(db, "job", "jobs.id")
                    pending = db.execute(
                        "SELECT 1 FROM jobs WHERE recording_id=? AND kind='media' "
                        "AND state IN ('queued','running') AND " + exclusion + " LIMIT 1",
                        (recording["id"],),
                    ).fetchone()
                if pending is None:
                    created.append(store.add_job(recording["id"], "media")["id"])
        queue.notify()
        return {"queued": len(created)}

    @app.put("/api/library/portable-retention")
    def save_retention(body: RetentionPolicy):
        return portable_retention.save_policy(store, body.model_dump())

    @app.post("/api/library/portable-retention/preview")
    def retention_preview(body: StorageSnapshot):
        try:
            target = portable_cloud.checked_target(
                store, body.expected_revision, body.expected_target_fingerprint
            )
            return portable_retention.preview(
                store, credentials, expected_target=target, expected_revision=body.expected_revision
            )
        except portable_cloud.StaleBackupStorage as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/library/portable-retention/apply")
    def retention_apply(body: ApplyRetention):
        return portable_retention.apply(
            store, credentials, fingerprint=body.fingerprint, confirmed=body.confirmed
        )

    @app.get("/api/library/portable-retention/events")
    def retention_events():
        return {"events": portable_retention.events(store)}

    @app.post("/api/library/portable-retention/{event_id}/{backup_id}/retry-preview")
    def retry_preview(event_id: str, backup_id: str):
        return portable_retention.retry_preview(store, credentials, event_id, backup_id)

    @app.post("/api/library/portable-retention/{event_id}/{backup_id}/retry")
    def retry_retention(event_id: str, backup_id: str, body: ApplyRetention):
        return portable_retention.retry(
            store,
            credentials,
            event_id,
            backup_id,
            fingerprint=body.fingerprint,
            confirmed=body.confirmed,
        )

    @app.get("/api/library/portable-operations")
    def operations():
        result = {"uploads": [], "restores": []}
        restore_root = store.root / "portable-restore-index"
        if restore_root.exists():
            with _RESTORE_INDEX_LOCK:
                names, _ = portable_transport._entries(
                    restore_root, store.root, maximum=20000, guard=None
                )
                _restore_index_recover_links(restore_root, store.root)
                for name in sorted(names):
                    if not name.endswith(".json"):
                        continue
                    identifier = name[:-5]
                    portable_transport._hex(identifier, 32)
                    raw, _ = portable_transport._read(
                        restore_root / name, store.root, maximum=32768, collect=True
                    )
                    record = RestoreIndexEntry.model_validate_json(raw)
                    if record.operation_id != identifier or record.cloud != bool(
                        record.target_identity
                    ):
                        raise ValueError("Invalid restore recovery index.")
                    result["restores"].append(record.model_dump())
        root = store.root / "portable-transfers"
        if root.exists():
            names, _ = portable_transport._entries(root, store.root, maximum=20000, guard=None)
            for name in sorted(names):
                if not name.endswith(".json"):
                    continue
                portable_transport._hex(name[:-5], 32)
                raw, _ = portable_transport._read(
                    root / name, store.root, maximum=16 * 1024**2, collect=True
                )
                record = json.loads(raw)
                result["uploads"].append(
                    {
                        key: record[key]
                        for key in (
                            "operation_id",
                            "backup_id",
                            "state",
                            "remote_folder",
                            "commit_sha256",
                            "local_package",
                        )
                    }
                )
        return result
