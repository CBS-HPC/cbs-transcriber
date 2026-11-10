"""Checksummed portable packages on UCloud storage; no compute job or permanent deletion.

Each upload owns one generated whole folder. Download-back SHA256 verification precedes its
commit marker. Durable journals freeze the destination; resuming cannot retarget old bytes.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

from . import portable_semantics
from . import portable_transport as transport
from . import ucloud_native as native
from .portable_capture import _parents, canonical, durable
from .portable_restore import _journal, operation_lock

TARGET_FILE = "portable-cloud-settings.json"
TARGET_FORMAT = "cbs-portable-cloud-settings-v3"
TARGET_FIELDS = {"provider_id", "parent", "allowed_transfer_origins", "whole_library_approved"}


class MissingBackupConnection(KeyError):
    """The saved destination refers to a provider that no longer exists."""


class UnavailableBackupConnection(ValueError):
    """The provider is no longer a connected UCloud profile."""


class StaleBackupConnection(ValueError):
    """The provider identity differs from the one shown when approval was given."""


class StaleBackupStorage(ValueError):
    """Backup storage was changed after the settings were displayed."""


def _origin(value):
    if type(value) is not str or len(value) > 512:
        raise ValueError("The HTTPS transfer origin is invalid or too long.")
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Use an HTTPS transfer origin without a path or credentials.")
    return "https://" + parsed.netloc.lower()


def _target(store, value):
    from .providers import Settings, profile

    if type(value) is not dict or set(value) != TARGET_FIELDS:
        raise ValueError("The backup storage settings are invalid.")
    transport._hex(value["provider_id"], 32)
    try:
        saved_profile = profile(store, value["provider_id"])
    except KeyError as exc:
        raise MissingBackupConnection("The saved backup connection no longer exists.") from exc
    settings = Settings.model_validate(saved_profile["settings"])
    if (
        settings.provider_type != "ucloud"
        or settings.ucloud_transcriber is None
        or not settings.ucloud_transcriber.project_id
    ):
        raise UnavailableBackupConnection("Choose a connected UCloud profile for backup storage.")
    parent = value["parent"]
    if type(parent) is not str or len(parent) > 2000:
        raise ValueError("Choose an existing UCloud project-drive backup folder.")
    native.NativeSetup.safe_path(parent)
    if not parent:
        raise ValueError("Choose an existing UCloud project-drive backup folder.")
    origins = value["allowed_transfer_origins"]
    if (
        type(origins) is not list
        or not 1 <= len(origins) <= 20
        or any(type(item) is not str for item in origins)
    ):
        raise ValueError("Approve the exact HTTPS storage-transfer origins.")
    origins = sorted({_origin(item) for item in origins})
    if type(value["whole_library_approved"]) is not bool:
        raise ValueError("Explicitly choose approval for the whole library backup scope.")
    record = {
        **value,
        "allowed_transfer_origins": origins,
        "server": settings.base_url,
        "project_id": settings.ucloud_transcriber.project_id,
        "drive_id": parent.split("/")[1],
        "institutional_approval": "not_checked",
    }
    record["fingerprint"] = hashlib.sha256(canonical(record)).hexdigest()
    return record, settings


def _approval_identity(record):
    return {
        key: record[key]
        for key in (
            "provider_id",
            "server",
            "project_id",
            "drive_id",
            "parent",
            "allowed_transfer_origins",
        )
    }


def _target_bytes(store):
    path = store.root / TARGET_FILE
    if not path.exists():
        return None, hashlib.sha256(b"").hexdigest()
    raw, _ = transport._read(path, store.root, maximum=64 * 1024, collect=True)
    return raw, hashlib.sha256(raw).hexdigest()


def _check_unfinished_targets(store, proposed, *, backup_id=None, operation_id=None):
    """Prevent target changes from stranding admitted uploads; caller holds settings lock."""
    root = store.root / "portable-transfers"
    if not root.exists():
        return
    names, _ = transport._entries(root, store.root, maximum=20000, guard=None)
    used = 0
    for name in names:
        if not name.endswith(".json"):
            continue
        transport._hex(name[:-5], 32)
        raw, _ = transport._read(root / name, root, maximum=16 * 1024**2, collect=True)
        used += len(raw)
        if used > 64 * 1024**2:
            raise ValueError("Review earlier backup operations before changing locations.")
        record = transport._canonical(raw)
        if not isinstance(record, dict) or record.get("format") != "cbs-portable-transfer-v1":
            raise ValueError("Review earlier backup operations before changing locations.")
        try:
            if record["operation_id"] != name[:-5]:
                raise ValueError("Invalid operation identity")
            transport._hex(record["backup_id"], 32)
            transport._hex(record["commit_sha256"], 64)
            transport._hex(record["manifest_sha256"], 64)
            frozen = record["target"]
            expected = TARGET_FIELDS | {
                "server",
                "project_id",
                "drive_id",
                "institutional_approval",
                "fingerprint",
            }
            if not isinstance(frozen, dict) or set(frozen) != expected:
                raise ValueError("Invalid frozen target")
            unsigned = {key: value for key, value in frozen.items() if key != "fingerprint"}
            if hashlib.sha256(canonical(unsigned)).hexdigest() != frozen["fingerprint"]:
                raise ValueError("Invalid target fingerprint")
            if record["remote_folder"] != frozen["parent"] + "/cbs-backup-" + record["backup_id"]:
                raise ValueError("Invalid remote identity")
            local = record["local_package"]
            if not isinstance(local, str) or len(local) > 4000 or not Path(local).is_absolute():
                raise ValueError("Invalid local package")
            verified = record["verified"]
            if not isinstance(verified, list) or len(verified) > transport.LIMITS["objects"] + 1:
                raise ValueError("Invalid verification list")
            if any(not isinstance(item, str) for item in verified) or len(set(verified)) != len(
                verified
            ):
                raise ValueError("Invalid verification entry")
            for item in verified:
                if item != "manifest.json":
                    if not item.startswith("objects/"):
                        raise ValueError("Invalid verification entry")
                    transport._hex(item[len("objects/") :], 64)
            state = record["state"]
            if state not in {
                "validating",
                "abandoned",
                "creating",
                "folder_uncertain",
                "owned",
                "uploading",
                "complete",
                "trashed",
                "retention_uncertain",
            }:
                raise ValueError("Invalid operation state")
            if state in {"complete", "trashed"}:
                completed_at = record["completed_at"]
                if (
                    not isinstance(completed_at, str)
                    or not completed_at
                    or len(completed_at) > 64
                    or datetime.fromisoformat(completed_at.replace("Z", "+00:00")).utcoffset()
                    is None
                    or "manifest.json" not in verified
                    or not any(item.startswith("objects/") for item in verified)
                ):
                    raise ValueError("Missing verification evidence")
            if state == "abandoned" and (
                verified
                or not isinstance(record.get("abandoned_at"), str)
                or len(record["abandoned_at"]) > 64
                or datetime.fromisoformat(record["abandoned_at"].replace("Z", "+00:00")).utcoffset()
                is None
            ):
                raise ValueError("Invalid abandoned admission")
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                "Review earlier backup operations before changing locations."
            ) from None
        if backup_id == record["backup_id"] and operation_id != record["operation_id"]:
            same_folder = all(
                frozen[key] == proposed[key] for key in ("server", "project_id", "parent")
            )
            if state in {"complete", "trashed"} and same_folder:
                raise ValueError("This backup already has a completed upload at this location.")
            if state not in {"complete", "trashed", "abandoned"}:
                raise ValueError(
                    "This backup already has an unfinished upload. Resume the earlier upload."
                )
        if state in {"complete", "trashed", "abandoned"}:
            continue
        try:
            same = _approval_identity(frozen) == _approval_identity(proposed)
        except (KeyError, TypeError):
            same = False
        if not same:
            raise ValueError(
                "Resolve earlier UCloud backup operations before changing the location."
            )


def save_target(store, value, *, expected_server, expected_project_id, expected_revision):
    path = store.root / TARGET_FILE
    with operation_lock(store.root / "portable-cloud-settings.lock"):
        _, revision = _target_bytes(store)
        if revision != expected_revision:
            raise StaleBackupStorage("Backup storage changed. Reopen Library and review it again.")
        record, _ = _target(store, value)
        if (record["server"], record["project_id"]) != (expected_server, expected_project_id):
            raise StaleBackupConnection(
                "The UCloud server or project changed. Reopen Library and review the destination."
            )
        try:
            current = _load_target(store)[0]
        except (MissingBackupConnection, UnavailableBackupConnection):
            # A deleted profile can be replaced only if no frozen upload prevents it.
            current = None
        revoking = (
            not record["whole_library_approved"]
            and current is not None
            and _approval_identity(current) == _approval_identity(record)
        )
        if not revoking:
            _check_unfinished_targets(store, record)
        saved = {
            "format": TARGET_FORMAT,
            "settings": value,
            "approved_target": _approval_identity(record)
            if record["whole_library_approved"]
            else None,
            "write_id": uuid.uuid4().hex,
        }
        _journal(path, saved)
        return record, hashlib.sha256(canonical(saved)).hexdigest()


def _load_target(store):
    raw, revision = _target_bytes(store)
    if raw is None:
        return None, None, None, revision
    saved = json.loads(raw)
    if type(saved) is dict and set(saved) == TARGET_FIELDS:
        value, approved = saved, None  # Legacy approval has no resolved destination evidence.
    elif type(saved) is dict and (
        (
            set(saved) == {"format", "settings", "approved_target"}
            and saved["format"] == "cbs-portable-cloud-settings-v2"
        )
        or (
            set(saved) == {"format", "settings", "approved_target", "write_id"}
            and saved["format"] == TARGET_FORMAT
            and isinstance(saved["write_id"], str)
            and len(saved["write_id"]) == 32
            and all(c in "0123456789abcdef" for c in saved["write_id"])
        )
    ):
        value, approved = saved["settings"], saved["approved_target"]
    else:
        raise ValueError("The saved backup storage settings are invalid.")
    try:
        record, settings = _target(store, value)
    except (MissingBackupConnection, UnavailableBackupConnection) as exc:
        exc.storage_revision = revision
        raise
    problem = None
    if record["whole_library_approved"] and approved != _approval_identity(record):
        # Preserve saved choices and frozen transfers, but never infer consent for a
        # changed provider identity or backfill a legacy approval from today's profile.
        record["whole_library_approved"] = False
        del record["fingerprint"]
        record["fingerprint"] = hashlib.sha256(canonical(record)).hexdigest()
        problem = "The saved backup destination changed. Review and approve it again."
    return record, settings, problem, revision


def load_target(store):
    return _load_target(store)[0]


def checked_target(store, expected_revision, expected_fingerprint):
    """Bind an action to the exact destination and settings the caller reviewed."""
    try:
        target, _, _, revision = _load_target(store)
    except (MissingBackupConnection, UnavailableBackupConnection):
        raise StaleBackupStorage(
            "The backup connection is unavailable. Reopen Library and review it again."
        ) from None
    if (
        target is None
        or revision != expected_revision
        or target["fingerprint"] != expected_fingerprint
    ):
        raise StaleBackupStorage("Backup storage changed. Reopen Library and review it again.")
    return target


def _connection(store, credentials, frozen, *, upload):
    from .providers import credential_scope

    # The client must use the same provider row that established this target identity.
    current, settings, _, _ = _load_target(store)
    same = current is not None and all(
        current[key] == value
        for key, value in frozen.items()
        if key not in {"fingerprint", "whole_library_approved"}
    )
    if not same or (upload and current["fingerprint"] != frozen["fingerprint"]):
        raise ValueError("Backup storage changed. The frozen transfer cannot be redirected.")
    if upload and current["whole_library_approved"] is not True:
        raise ValueError(
            "Approve this exact destination for the whole library, "
            "including Protected data, before uploading."
        )
    key = credentials.get(credential_scope(settings))
    if not key:
        raise ValueError("Reconnect the UCloud token in Settings.")
    return native.client_for(settings, key)


def _endpoint(target, url):
    parsed = urlparse(url)
    if (
        _origin("https://" + parsed.netloc) not in target["allowed_transfer_origins"]
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "This UCloud transfer destination is not supported without further investigation. "
            "The local backup and upload operation are retained for recovery."
        )


def _download(
    client,
    target,
    remote,
    *,
    size=None,
    maximum=None,
    digest=None,
    destination=None,
    guard=None,
    collect=False,
):
    """Separate transfer client: no control Authorization, redirects or unbounded buffers."""
    transport._guard(guard)
    response = native.cloud_call(
        client.request, "POST", "/api/files/download", json={"items": [{"id": remote}]}
    )
    endpoint = native.transfer_url(
        target["server"], native.one_response(response.json())["endpoint"]
    )
    _endpoint(target, endpoint)
    maximum = size if size is not None else maximum
    if type(maximum) is not int or maximum < 0:
        raise ValueError("The backup download has no bounded byte policy.")
    temporary = (
        destination.with_name(destination.name + ".pending") if destination is not None else None
    )
    if temporary is not None and temporary.exists():
        transport._node(temporary, destination.parent.resolve())
        temporary.unlink()
    actual, count, chunks, output = hashlib.sha256(), 0, [], None
    try:
        if temporary is not None:
            output = temporary.open("xb")
        with httpx.Client(timeout=120, follow_redirects=False) as separate:
            transport._guard(guard)
            with separate.stream(
                "GET", endpoint, headers={"Accept-Encoding": "identity"}
            ) as incoming:
                if not incoming.is_success:
                    raise ValueError("The UCloud backup download was refused or redirected.")
                if incoming.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("The backup transfer must supply exact unencoded file bytes.")
                for chunk in incoming.iter_raw(1024 * 1024):
                    transport._guard(guard)
                    count += len(chunk)
                    if count > maximum:
                        raise ValueError(
                            "The UCloud backup object exceeds its declared byte limit."
                        )
                    actual.update(chunk)
                    if output is not None:
                        output.write(chunk)
                    if collect:
                        chunks.append(chunk)
        if (size is not None and count != size) or (
            digest is not None and actual.hexdigest() != digest
        ):
            raise ValueError("The UCloud backup object failed its download checksum.")
        if output is not None:
            import os

            output.flush()
            os.fsync(output.fileno())
            output.close()
            output = None
            os.link(temporary, destination, follow_symlinks=False)
        return b"".join(chunks) if collect else {"sha256": actual.hexdigest(), "size": count}
    except httpx.HTTPError:
        raise ValueError("The UCloud backup transfer was interrupted. Resume explicitly.") from None
    finally:
        if output is not None:
            output.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _names(client, remote, *, guard=None):
    found, seen, token = set(), set(), None
    for _ in range(500):
        transport._guard(guard)
        page = native.cloud_call(client.browse_files, remote, items_per_page=250, next_page=token)
        items = page.get("items")
        if type(items) is not list:
            raise ValueError("The UCloud backup inventory is malformed.")
        for item in items:
            identifier = item.get("id") if type(item) is dict else None
            if (
                type(identifier) is not str
                or str(PurePosixPath(identifier).parent) != remote
                or identifier in found
            ):
                raise ValueError("The UCloud backup inventory is malformed.")
            found.add(identifier)
            if len(found) > transport.LIMITS["objects"]:
                raise ValueError("The UCloud backup inventory exceeds its supported limit.")
        token = page.get("next")
        if not token:
            return {PurePosixPath(item).name for item in found}
        if type(token) is not str or token in seen:
            raise ValueError("The UCloud backup pagination is malformed.")
        seen.add(token)
    raise ValueError("The UCloud backup inventory exceeds its supported page limit.")


def upload_package(
    store,
    credentials,
    package_root,
    *,
    expected_commit_sha256,
    operation_id=None,
    guard=None,
    expected_target=None,
    expected_revision=None,
):
    """Record the exact request before potentially lengthy package verification."""
    identifier = operation_id or uuid.uuid4().hex
    transport._hex(identifier, 32)
    transport._hex(expected_commit_sha256, 64)
    root = _parents(Path(package_root))
    transport._node(root, root, directory=True)
    raw, _ = transport._read(
        root / "COMMITTED.json",
        root,
        maximum=transport.LIMITS["commit_bytes"],
        expected_hash=expected_commit_sha256,
        collect=True,
        guard=guard,
    )
    marker = transport._canonical(raw)
    transport._record(
        marker, {"format", "backup_id", "manifest_size", "manifest_sha256"}, "commit fields"
    )
    if marker["format"] != "cbs-library-backup-commit-v2":
        raise ValueError("Invalid backup commit format.")
    transport._hex(marker["backup_id"], 32)
    transport._hex(marker["manifest_sha256"], 64)
    transport._integer(marker["manifest_size"], transport.LIMITS["manifest_bytes"], minimum=1)
    transfers = _parents(store.root / "portable-transfers")
    transfers.mkdir(exist_ok=True)
    journal = transfers / (identifier + ".json")
    # The outer lease covers validation too. Order is admission, operation, settings.
    with operation_lock(transfers / (identifier + ".admission.lock")):
        with operation_lock(store.root / "portable-cloud-settings.lock"):
            target = (
                checked_target(store, expected_revision, expected_target["fingerprint"])
                if expected_target is not None
                else load_target(store)
            )
            if expected_target is not None and target != expected_target:
                raise StaleBackupStorage(
                    "Backup storage changed. Reopen Library and review it again."
                )
            if not target or target["whole_library_approved"] is not True:
                raise ValueError(
                    "Approve this exact destination for the whole library before uploading."
                )
            binding = {
                "format": "cbs-portable-transfer-v1",
                "operation_id": identifier,
                "backup_id": marker["backup_id"],
                "commit_sha256": expected_commit_sha256,
                "manifest_sha256": marker["manifest_sha256"],
                "target": target,
                "remote_folder": target["parent"] + "/cbs-backup-" + marker["backup_id"],
                "local_package": str(root),
            }
            if journal.exists():
                existing, _ = transport._read(
                    journal, transfers, maximum=16 * 1024**2, collect=True
                )
                existing = transport._canonical(existing)
                if any(existing.get(key) != value for key, value in binding.items()):
                    raise ValueError(
                        "This backup transfer belongs to another package or destination."
                    )
                if existing.get("state") == "abandoned":
                    raise ValueError("This local backup check was abandoned. Start a new upload.")
            else:
                _check_unfinished_targets(
                    store, target, backup_id=marker["backup_id"], operation_id=identifier
                )
                _journal(journal, {**binding, "state": "validating", "verified": []})
        return _upload_package_locked(
            store,
            credentials,
            root,
            expected_commit_sha256=expected_commit_sha256,
            operation_id=identifier,
            guard=guard,
            expected_target=expected_target,
            expected_revision=expected_revision,
        )


def admit_creator_upload(store, *, backup_id, operation_id, revision, fingerprint):
    """Freeze an app-owned creator receipt before accessing package files."""
    transport._hex(backup_id, 32)
    transport._hex(operation_id, 32)
    receipts = _parents(store.root / "portable-captures")
    raw, _ = transport._read(
        receipts / (backup_id + ".json"), receipts, maximum=64 * 1024, collect=True
    )
    receipt = transport._canonical(raw)
    if not isinstance(receipt, dict) or receipt.get("backup_id") != backup_id:
        raise ValueError("The saved backup receipt is invalid.")
    transport._hex(receipt.get("commit_sha256"), 64)
    transport._hex(receipt.get("manifest_sha256"), 64)
    folder = receipt.get("folder")
    if (
        not isinstance(folder, str)
        or len(folder) > 4000
        or not Path(folder).is_absolute()
        or Path(folder).name != "portable-" + backup_id
    ):
        raise ValueError("The saved backup receipt has an invalid folder.")
    transfers = _parents(store.root / "portable-transfers")
    transfers.mkdir(exist_ok=True)
    journal = transfers / (operation_id + ".json")
    with operation_lock(transfers / (operation_id + ".admission.lock")):
        with operation_lock(store.root / "portable-cloud-settings.lock"):
            target = checked_target(store, revision, fingerprint)
            if not target["whole_library_approved"]:
                raise ValueError("Confirm the whole-library backup before upload.")
            binding = {
                "format": "cbs-portable-transfer-v1",
                "operation_id": operation_id,
                "backup_id": backup_id,
                "commit_sha256": receipt["commit_sha256"],
                "manifest_sha256": receipt["manifest_sha256"],
                "target": target,
                "remote_folder": target["parent"] + "/cbs-backup-" + backup_id,
                "local_package": folder,
            }
            if journal.exists():
                old, _ = transport._read(journal, transfers, maximum=16 * 1024**2, collect=True)
                old = transport._canonical(old)
                if (
                    any(old.get(key) != value for key, value in binding.items())
                    or old.get("state") == "abandoned"
                ):
                    raise ValueError(
                        "This backup transfer belongs to another package or destination."
                    )
            else:
                _check_unfinished_targets(
                    store, target, backup_id=backup_id, operation_id=operation_id
                )
                _journal(journal, {**binding, "state": "validating", "verified": []})
    return {"operation_id": operation_id}


def abandon_validating_upload(store, operation_id):
    """Settle a local-only admission only while its worker lease is held."""
    transport._hex(operation_id, 32)
    transfers = _parents(store.root / "portable-transfers")
    with operation_lock(transfers / (operation_id + ".admission.lock")):
        with operation_lock(store.root / "portable-cloud-settings.lock"):
            path = transfers / (operation_id + ".json")
            raw, _ = transport._read(path, transfers, maximum=16 * 1024**2, collect=True)
            record = transport._canonical(raw)
            if (
                record.get("operation_id") != operation_id
                or record.get("state") != "validating"
                or record.get("verified") != []
            ):
                raise ValueError(
                    "This backup may have reached UCloud and cannot be abandoned here."
                )
            record["state"] = "abandoned"
            record["abandoned_at"] = datetime.now(UTC).isoformat()
            _journal(path, record)
    return {"state": "abandoned"}


def _upload_package_locked(
    store,
    credentials,
    package_root,
    *,
    expected_commit_sha256,
    operation_id=None,
    guard=None,
    expected_target=None,
    expected_revision=None,
):
    capture = transport._capture_transport(
        package_root, expected_commit_sha256=expected_commit_sha256, guard=guard
    )
    manifest = transport._canonical(capture.manifest_bytes)
    database = manifest["database"]
    portable_semantics.inspect_database(
        capture.root / "objects" / database["object"],
        expected_sha256=database["object"],
        expected_size=database["size"],
        manifest=manifest,
        guard=guard,
    )
    target = (
        checked_target(store, expected_revision, expected_target["fingerprint"])
        if expected_target is not None
        else load_target(store)
    )
    if expected_target is not None and target != expected_target:
        raise StaleBackupStorage("Backup storage changed. Reopen Library and review it again.")
    if target is None:
        raise ValueError("Configure explicit whole-library backup storage first.")
    if target["whole_library_approved"] is not True:
        # Refuse before journaling: a journal frozen to the unapproved settings could never
        # be resumed after the same destination is approved.
        raise ValueError(
            "Approve this exact destination for the whole library, "
            "including Protected data, before uploading."
        )
    identifier = operation_id or uuid.uuid4().hex
    transport._hex(identifier, 32)
    root = _parents(store.root / "portable-transfers")
    root.mkdir(exist_ok=True)
    journal = root / (identifier + ".json")
    remote = target["parent"] + "/cbs-backup-" + capture.backup_id
    binding = {
        "format": "cbs-portable-transfer-v1",
        "operation_id": identifier,
        "backup_id": capture.backup_id,
        "commit_sha256": expected_commit_sha256,
        "manifest_sha256": capture.manifest_sha256,
        "target": target,
        "remote_folder": remote,
        "local_package": str(capture.root),
    }
    with operation_lock(root / (identifier + ".lock")):
        # Lock order: operation, then settings. Only identity checks and local journal
        # writes happen here; transfer and folder requests remain outside this lock.
        with operation_lock(store.root / "portable-cloud-settings.lock"):
            current = (
                checked_target(store, expected_revision, target["fingerprint"])
                if expected_target is not None
                else load_target(store)
            )
            if current != target:
                raise StaleBackupStorage("Backup storage changed before upload began.")
            _check_unfinished_targets(
                store, target, backup_id=capture.backup_id, operation_id=identifier
            )
            if journal.exists():
                raw, _ = transport._read(journal, root, maximum=16 * 1024**2, collect=True)
                state = json.loads(raw)
                if any(state.get(key) != value for key, value in binding.items()):
                    raise ValueError(
                        "This backup transfer belongs to another package or destination."
                    )
                if state["state"] == "folder_uncertain":
                    raise ValueError(
                        "The remote folder creation outcome is uncertain. "
                        "Review that exact folder before reconciliation."
                    )
                if state["state"] in {"trashed", "retention_uncertain"}:
                    raise ValueError(
                        "This remote backup has a retention action. "
                        "Resolve that action instead of resuming upload."
                    )
                if state["state"] == "validating":
                    state["state"] = "creating"
                    _journal(journal, state)
                if state["state"] == "complete" and set(state["verified"]) != {
                    "manifest.json",
                    *("objects/" + name for name in capture.object_names),
                }:
                    raise ValueError("The completed backup verification record is incomplete.")
            else:
                _check_unfinished_targets(
                    store, target, backup_id=capture.backup_id, operation_id=identifier
                )
                state = {**binding, "state": "creating", "verified": []}
                _journal(journal, state)

        def approved():
            transport._guard(guard)
            try:
                current = (
                    checked_target(store, expected_revision, target["fingerprint"])
                    if expected_target is not None
                    else load_target(store)
                )
            except StaleBackupStorage:
                raise StaleBackupStorage(
                    "Backup settings changed. No further requests will start; "
                    "an in-flight request may have sent data. Reopen Library to review."
                ) from None
            if (
                current is None
                or current["fingerprint"] != target["fingerprint"]
                or not current["whole_library_approved"]
            ):
                raise ValueError(
                    "Backup upload approval changed or was revoked. "
                    "No further requests will be started; an in-flight request may have sent data."
                )

        with _connection(store, credentials, target, upload=True) as client:
            if state["state"] == "creating":
                approved()
                state["state"] = "folder_uncertain"
                _journal(journal, state)
                native.cloud_call(client.create_folder, remote)
                state["state"] = "owned"
                _journal(journal, state)
                approved()
                native.cloud_call(client.create_folder, remote + "/objects")
            elif state["state"] != "complete" and "objects" not in _names(
                client, remote, guard=approved
            ):
                approved()
                native.cloud_call(client.create_folder, remote + "/objects")
            for item in manifest["objects"] + [
                {
                    "sha256": capture.manifest_sha256,
                    "size": len(capture.manifest_bytes),
                    "name": "manifest.json",
                }
            ]:
                relative = item.get("name", "objects/" + item["sha256"])
                path = capture.root / relative
                approved()
                if relative not in state["verified"]:
                    native.upload(
                        client,
                        target["server"],
                        path,
                        remote + "/" + relative,
                        approved,
                        lambda **_: None,
                        endpoint_guard=lambda url: _endpoint(target, url),
                    )
                _download(
                    client,
                    target,
                    remote + "/" + relative,
                    size=item["size"],
                    digest=item["sha256"],
                    guard=approved,
                )
                if relative not in state["verified"]:
                    state["verified"].append(relative)
                    state["state"] = "uploading"
                    _journal(journal, state)
            remote_names = _names(client, remote, guard=approved)
            if remote_names - {"objects", "manifest.json", "COMMITTED.json"} or _names(
                client, remote + "/objects", guard=approved
            ) != set(capture.object_names):
                raise ValueError(
                    "The remote backup contains an unexpected file; no commit was published."
                )
            transport._recheck(capture, guard)
            approved()
            marker = capture.root / "COMMITTED.json"
            if "COMMITTED.json" not in remote_names:
                if state["state"] == "complete":
                    raise ValueError(
                        "The previously completed backup is missing its commit marker."
                    )
                native.upload(
                    client,
                    target["server"],
                    marker,
                    remote + "/COMMITTED.json",
                    approved,
                    lambda **_: None,
                    endpoint_guard=lambda url: _endpoint(target, url),
                )
            _download(
                client,
                target,
                remote + "/COMMITTED.json",
                size=marker.stat().st_size,
                digest=expected_commit_sha256,
                guard=approved,
            )
            state["state"] = "complete"
            state.setdefault("completed_at", datetime.now(UTC).isoformat())
            _journal(journal, state)
        return state


def folder_preview(store, credentials, operation_id):
    transport._hex(operation_id, 32)
    root = _parents(store.root / "portable-transfers")
    raw, _ = transport._read(
        root / (operation_id + ".json"), root, maximum=16 * 1024**2, collect=True
    )
    record = json.loads(raw)
    if record.get("state") != "folder_uncertain" or record.get("verified") != []:
        raise ValueError("Only an uncertain empty-folder creation may be reconciled here.")
    target = record["target"]
    remote = target["parent"] + "/cbs-backup-" + transport._hex(record["backup_id"], 32)
    if record["remote_folder"] != remote:
        raise ValueError("The frozen backup folder ownership is invalid.")
    with _connection(store, credentials, target, upload=False) as client:
        listed = PurePosixPath(remote).name in _names(client, target["parent"])
        if listed:
            names = _names(client, remote)
            if names - {"objects"} or ("objects" in names and _names(client, remote + "/objects")):
                raise ValueError(
                    "The uncertain folder is not empty. It cannot be claimed by this operation."
                )
    preview = {
        "operation_id": operation_id,
        "remote_folder": remote,
        "target_fingerprint": target["fingerprint"],
        "observed": "empty_folder" if listed else "not_listed",
    }
    return {**preview, "fingerprint": hashlib.sha256(canonical(preview)).hexdigest()}


def reconcile_folder(store, credentials, operation_id, *, fingerprint, confirmed):
    if confirmed is not True:
        raise ValueError("Confirm the exact empty-folder preview before reconciling creation.")
    root = _parents(store.root / "portable-transfers")
    transport._hex(operation_id, 32)
    with operation_lock(root / (operation_id + ".lock")):
        preview = folder_preview(store, credentials, operation_id)
        if preview["fingerprint"] != fingerprint:
            raise ValueError("The uncertain folder preview changed. Review it again.")
        path = root / (operation_id + ".json")
        raw, _ = transport._read(path, root, maximum=16 * 1024**2, collect=True)
        record = json.loads(raw)
        record["state"] = "owned" if preview["observed"] == "empty_folder" else "creating"
        record["folder_reconciliation"] = preview
        _journal(path, record)
        return record


def _remote_manifest(client, target, remote_folder, expected_commit_sha256, guard):
    """Validate the independently trusted commit and its manifest before reading objects."""
    if (
        not remote_folder.startswith(target["parent"] + "/cbs-backup-")
        or PurePosixPath(remote_folder).parent.as_posix() != target["parent"]
    ):
        raise ValueError("Choose an exact backup folder in the configured drive location.")
    transport._hex(remote_folder.rsplit("cbs-backup-", 1)[1], 32)
    transport._hex(expected_commit_sha256, 64)
    marker = _download(
        client,
        target,
        remote_folder + "/COMMITTED.json",
        maximum=transport.LIMITS["commit_bytes"],
        digest=expected_commit_sha256,
        collect=True,
        guard=guard,
    )
    commit = transport._canonical(marker)
    transport._record(
        commit, {"format", "backup_id", "manifest_size", "manifest_sha256"}, "commit fields"
    )
    if (
        commit["format"] != "cbs-library-backup-commit-v2"
        or remote_folder != target["parent"] + "/cbs-backup-" + commit["backup_id"]
    ):
        raise ValueError("The remote backup commit identity is invalid.")
    size = transport._integer(
        commit["manifest_size"], transport.LIMITS["manifest_bytes"], minimum=1
    )
    manifest_bytes = _download(
        client,
        target,
        remote_folder + "/manifest.json",
        size=size,
        digest=transport._hex(commit["manifest_sha256"], 64),
        collect=True,
        guard=guard,
    )
    manifest = transport._canonical(manifest_bytes)
    sizes, _ = transport._manifest(manifest, commit)
    return marker, commit, manifest_bytes, sizes


def inspect_remote_package(client, target, remote_folder, *, expected_commit_sha256, guard=None):
    """Check each remote transport object without storing a copy or authorizing restoration."""
    _, commit, _, sizes = _remote_manifest(
        client, target, remote_folder, expected_commit_sha256, guard
    )
    for digest, size in sizes.items():
        _download(
            client,
            target,
            remote_folder + "/objects/" + digest,
            size=size,
            digest=digest,
            guard=guard,
        )
    return {
        "backup_id": commit["backup_id"],
        "commit_sha256": expected_commit_sha256,
        "verification": "transport_integrity_only",
    }


def download_package(
    client, target, remote_folder, *, expected_commit_sha256, destination, guard=None
):
    """Download sealed bytes using an independently supplied trusted commit reference."""
    marker, commit, manifest_bytes, sizes = _remote_manifest(
        client, target, remote_folder, expected_commit_sha256, guard
    )
    root = _parents(Path(destination))
    root.mkdir(exist_ok=True)
    objects = root / "objects"
    objects.mkdir(exist_ok=True)
    transport._node(objects, root, directory=True)
    for digest, size in sizes.items():
        path = objects / digest
        if path.exists():
            transport._read(path, root, maximum=size, size=size, expected_hash=digest, guard=guard)
        else:
            _download(
                client,
                target,
                remote_folder + "/objects/" + digest,
                size=size,
                digest=digest,
                destination=path,
                guard=guard,
            )
    for name, raw in (("manifest.json", manifest_bytes), ("COMMITTED.json", marker)):
        path = root / name
        if path.exists():
            transport._read(
                path,
                root,
                maximum=len(raw),
                size=len(raw),
                expected_hash=hashlib.sha256(raw).hexdigest(),
                guard=guard,
            )
        else:
            durable(path, raw)
    transport.inspect_transport(root, expected_commit_sha256=expected_commit_sha256, guard=guard)
    return {
        "folder": str(root),
        "backup_id": commit["backup_id"],
        "commit_sha256": expected_commit_sha256,
    }
