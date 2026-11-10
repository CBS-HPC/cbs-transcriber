"""A11: frozen backup ownership cannot switch clients or approval during Trash."""

import copy
import hashlib
import json
import shutil
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_portable_cloud import Memory, retention_pair
from test_portable_cloud import sealed as sealed
from test_portable_cloud import storage as storage

from cbs_transcriber import portable_cloud as cloud
from cbs_transcriber import providers
from cbs_transcriber.portable_capture import canonical


@pytest.fixture
def connection(tmp_path, monkeypatch):
    store = SimpleNamespace(root=tmp_path)
    settings = {
        "provider_type": "ucloud",
        "base_url": "https://cloud.example",
        "model": "large-v3",
        "ucloud_transcriber": {
            "project_id": "project",
            "application": {"name": "transcriber", "version": "1.12"},
            "storage_path": "/drive/transcriber",
        },
    }
    value = {
        "provider_id": "d" * 32,
        "parent": "/drive/backups",
        "allowed_transfer_origins": ["https://storage.example"],
        "whole_library_approved": True,
    }
    monkeypatch.setattr(providers, "profile", lambda *args: {"settings": settings})
    frozen = cloud.save_target(
        store,
        value,
        expected_server="https://cloud.example",
        expected_project_id="project",
        expected_revision=cloud._target_bytes(store)[1],
    )[0]
    credentials = SimpleNamespace(get=lambda scope: "synthetic-key")
    return store, settings, value, frozen, credentials


@pytest.mark.parametrize("change", ["server", "project"])
def test_connection_uses_one_checked_provider_snapshot(connection, monkeypatch, change):
    store, settings, _, frozen, credentials = connection
    seen, reads = [], []
    replacement = copy.deepcopy(settings)
    if change == "server":
        replacement["base_url"] = "https://unpreviewed.example"
    else:
        replacement["ucloud_transcriber"]["project_id"] = "unpreviewed-project"

    def changing(*args):
        reads.append(True)
        return {"settings": settings if len(reads) == 1 else replacement}

    monkeypatch.setattr(providers, "profile", changing)
    monkeypatch.setattr(
        cloud.native, "client_for", lambda actual, key: seen.append(actual) or Memory()
    )
    with cloud._connection(store, credentials, frozen, upload=False):
        pass
    assert len(reads) == 1
    assert seen[0].base_url == frozen["server"]
    assert seen[0].ucloud_transcriber.project_id == frozen["project_id"]


def test_read_only_recovery_keeps_exact_identity_after_upload_approval_revocation(
    connection, monkeypatch
):
    store, _, value, frozen, credentials = connection
    cloud.save_target(
        store,
        {**value, "whole_library_approved": False},
        expected_server="https://cloud.example",
        expected_project_id="project",
        expected_revision=cloud._target_bytes(store)[1],
    )
    clients = []
    monkeypatch.setattr(
        cloud.native, "client_for", lambda settings, key: clients.append(settings) or Memory()
    )
    with cloud._connection(store, credentials, frozen, upload=False):
        pass
    with pytest.raises(ValueError, match="changed|Approve"):
        cloud._connection(store, credentials, frozen, upload=True)
    assert len(clients) == 1 and clients[0].base_url == frozen["server"]


@pytest.mark.parametrize("field", ["server", "project_id", "whole_library_approved"])
def test_retention_rejects_target_change_after_confirmed_preview(
    sealed, storage, monkeypatch, field
):
    retention, first, _, moved = retention_pair(sealed, storage, monkeypatch)
    store, target, _ = storage
    shown = retention.preview(store, None)
    preview = retention.preview

    def change_after_preview(*args, **kwargs):
        result = preview(*args, **kwargs)
        target[field] = False if field == "whole_library_approved" else "unpreviewed-target"
        target["fingerprint"] = "e" * 64
        return result

    monkeypatch.setattr(retention, "preview", change_after_preview)
    with pytest.raises(ValueError, match="storage|preview|changed"):
        retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=True)
    assert not moved
    saved = json.loads(
        (store.root / "portable-transfers" / (first["operation_id"] + ".json")).read_text()
    )
    assert saved["state"] == "complete"


def test_retention_rechecks_after_client_creation_and_retains_intent(sealed, storage, monkeypatch):
    retention, first, _, moved = retention_pair(sealed, storage, monkeypatch)
    store, target, memory = storage
    shown = retention.preview(store, None)
    preview = retention.preview
    rechecked = []

    def record_recheck(*args, **kwargs):
        result = preview(*args, **kwargs)
        rechecked.append(True)
        return result

    @contextmanager
    def change_on_open(*args, **kwargs):
        if rechecked:
            target["whole_library_approved"] = False
            target["fingerprint"] = "e" * 64
        yield memory

    monkeypatch.setattr(retention, "preview", record_recheck)
    monkeypatch.setattr(cloud, "_connection", change_on_open)
    with pytest.raises(ValueError, match="storage|preview|changed"):
        retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=True)
    assert not moved and first["remote_folder"] in memory.folders
    intents = list((store.root / "portable-retention-events").glob("*/intent.json"))
    assert len(intents) == 1
    assert not (intents[0].parent / (first["backup_id"] + ".json")).exists()


def third_backup(sealed, storage):
    store, _, _ = storage
    root = sealed[0].with_name("third-sealed")
    shutil.copytree(sealed[0], root)
    manifest = dict(sealed[2], backup_id="e" * 32)
    raw = canonical(manifest)
    (root / "manifest.json").write_bytes(raw)
    marker = canonical(
        {
            "format": "cbs-library-backup-commit-v2",
            "backup_id": "e" * 32,
            "manifest_size": len(raw),
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    (root / "COMMITTED.json").write_bytes(marker)
    result = cloud.upload_package(
        store,
        None,
        root,
        expected_commit_sha256=hashlib.sha256(marker).hexdigest(),
        operation_id="3" * 32,
    )
    result["completed_at"] = "2022-01-01T00:00:00+00:00"
    cloud._journal(store.root / "portable-transfers" / (result["operation_id"] + ".json"), result)


def test_partial_retention_preserves_success_and_resumes_with_fresh_revoked_approval_preview(
    sealed, storage, monkeypatch
):
    retention, first, second, moved = retention_pair(sealed, storage, monkeypatch)
    third_backup(sealed, storage)
    store, target, memory = storage
    shown = retention.preview(store, None)
    assert shown["eligible_operations"] == [second["operation_id"], first["operation_id"]]
    trash = cloud.native.trash_owned_folder

    def revoke_after_first(client, folder):
        trash(client, folder)
        if len(moved) == 1:
            target.update(whole_library_approved=False, fingerprint="e" * 64)

    monkeypatch.setattr(cloud.native, "trash_owned_folder", revoke_after_first)
    with pytest.raises(ValueError, match="storage|approval|changed"):
        retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=True)
    assert moved == [second["remote_folder"]]
    [intent] = (store.root / "portable-retention-events").glob("*/intent.json")
    assert (intent.parent / (second["backup_id"] + ".json")).exists()
    assert not (intent.parent / (first["backup_id"] + ".json")).exists()
    saved_intent = json.loads(intent.read_text())
    assert saved_intent["target"]["whole_library_approved"] is True
    record_path = store.root / "portable-transfers" / (first["operation_id"] + ".json")
    assert json.loads(record_path.read_text())["state"] == "complete"
    uploads = list(memory.uploads)
    fresh = retention.retry_preview(store, None, intent.parent.name, first["backup_id"])
    assert fresh["target"]["whole_library_approved"] is False
    result = retention.retry(
        store,
        None,
        intent.parent.name,
        first["backup_id"],
        fingerprint=fresh["fingerprint"],
        confirmed=True,
    )
    assert result["state"] == "moved_to_trash"
    assert moved == [second["remote_folder"], first["remote_folder"]]
    assert memory.uploads == uploads and target["whole_library_approved"] is False
    assert json.loads(record_path.read_text())["state"] == "trashed"
    assert (sealed[0] / "COMMITTED.json").exists()


def test_retention_retry_rejects_late_target_change_without_rewriting_receipt(
    sealed, storage, monkeypatch
):
    retention, first, _, moved = retention_pair(sealed, storage, monkeypatch)
    store, target, _ = storage
    trash = cloud.native.trash_owned_folder

    def uncertain(*args, **kwargs):
        raise OSError("Synthetic lost Trash reply")

    monkeypatch.setattr(cloud.native, "trash_owned_folder", uncertain)
    shown = retention.preview(store, None)
    event = retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=True)
    assert event["outcomes"][0]["state"] == "trash_outcome_uncertain"
    monkeypatch.setattr(cloud.native, "trash_owned_folder", trash)
    preview = retention.retry_preview
    fresh = preview(store, None, event["event_id"], first["backup_id"])

    def change_after_preview(*args, **kwargs):
        result = preview(*args, **kwargs)
        target.update(server="https://unpreviewed.example", fingerprint="e" * 64)
        return result

    monkeypatch.setattr(retention, "retry_preview", change_after_preview)
    with pytest.raises(ValueError, match="storage|approval|changed"):
        retention.retry(
            store,
            None,
            event["event_id"],
            first["backup_id"],
            fingerprint=fresh["fingerprint"],
            confirmed=True,
        )
    assert not moved
    record_path = store.root / "portable-transfers" / (first["operation_id"] + ".json")
    assert json.loads(record_path.read_text())["state"] == "retention_uncertain"
