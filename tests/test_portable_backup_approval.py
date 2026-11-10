"""Whole-library approval belongs to its saved resolved destination, never a mutable ID."""

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_portable_cloud import Memory
from test_portable_cloud import sealed as sealed
from test_portable_retention_identity import connection as connection

from cbs_transcriber import portable_cloud as cloud
from cbs_transcriber import portable_transport as transport
from cbs_transcriber import providers


def test_target_change_is_fenced_against_journal_admission_and_revocation_is_allowed(
    connection, sealed, monkeypatch
):
    store, _, value, frozen, credentials = connection
    revision = cloud._target_bytes(store)[1]
    entered, release = threading.Event(), threading.Event()
    original_journal = cloud._journal

    def gated_journal(path, state):
        if path.name == "1" * 32 + ".json":
            entered.set()
            assert release.wait(10)
        return original_journal(path, state)

    monkeypatch.setattr(cloud, "_journal", gated_journal)
    monkeypatch.setattr(cloud.portable_semantics, "inspect_database", lambda *args, **kwargs: {})

    def no_network(*args, **kwargs):
        raise ValueError("synthetic stop after journal")

    monkeypatch.setattr(cloud, "_connection", no_network)
    kwargs = {"expected_server": frozen["server"], "expected_project_id": frozen["project_id"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(
            cloud.upload_package,
            store,
            credentials,
            sealed[0],
            expected_commit_sha256=sealed[1],
            operation_id="1" * 32,
            expected_target=frozen,
            expected_revision=revision,
        )
        assert entered.wait(10)
        try:
            with pytest.raises(ValueError, match="already running"):
                cloud.save_target(
                    store, {**value, "parent": "/drive/other"}, expected_revision=revision, **kwargs
                )
        finally:
            release.set()
        with pytest.raises(ValueError, match="synthetic stop"):
            future.result(timeout=10)
    with pytest.raises(ValueError, match="Resolve earlier"):
        cloud.save_target(
            store, {**value, "parent": "/drive/other"}, expected_revision=revision, **kwargs
        )
    _, revoked = cloud.save_target(
        store, {**value, "whole_library_approved": False}, expected_revision=revision, **kwargs
    )
    assert cloud.load_target(store)["whole_library_approved"] is False
    approved, _ = cloud.save_target(store, value, expected_revision=revoked, **kwargs)
    assert approved == frozen


def test_corrupt_complete_journal_cannot_authorize_target_change_but_can_revoke(connection):
    store, _, value, frozen, _ = connection
    revision = cloud._target_bytes(store)[1]
    root = store.root / "portable-transfers"
    root.mkdir()
    (root / ("1" * 32 + ".json")).write_text('{"state":"complete"}', encoding="utf-8")
    kwargs = {"expected_server": frozen["server"], "expected_project_id": frozen["project_id"]}
    with pytest.raises(ValueError, match="Review earlier"):
        cloud.save_target(
            store, {**value, "parent": "/drive/other"}, expected_revision=revision, **kwargs
        )
    revoked, _ = cloud.save_target(
        store, {**value, "whole_library_approved": False}, expected_revision=revision, **kwargs
    )
    assert revoked["whole_library_approved"] is False


@pytest.mark.parametrize(
    "updates",
    [
        {"verified": ["manifest.json"], "completed_at": "2026-11-04T12:00:00+00:00"},
        {"verified": ["manifest.json", "objects/" + "a" * 64]},
        {"verified": ["manifest.json", "objects/" + "a" * 64], "completed_at": "2026-11-04"},
    ],
)
def test_plausible_truncated_terminal_journal_cannot_authorize_target_change(connection, updates):
    store, _, value, frozen, _ = connection
    root = store.root / "portable-transfers"
    root.mkdir()
    record = {
        "format": "cbs-portable-transfer-v1",
        "operation_id": "1" * 32,
        "backup_id": "b" * 32,
        "commit_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
        "target": frozen,
        "remote_folder": frozen["parent"] + "/cbs-backup-" + "b" * 32,
        "local_package": str(store.root / "synthetic-package"),
        "state": "complete",
        **updates,
    }
    (root / ("1" * 32 + ".json")).write_bytes(cloud.canonical(record))
    with pytest.raises(ValueError, match="Review earlier"):
        cloud.save_target(
            store,
            {**value, "parent": "/drive/other"},
            expected_server=frozen["server"],
            expected_project_id=frozen["project_id"],
            expected_revision=cloud._target_bytes(store)[1],
        )


def test_duplicate_key_journal_cannot_authorize_target_change(connection):
    store, _, value, frozen, _ = connection
    root = store.root / "portable-transfers"
    root.mkdir()
    (root / ("1" * 32 + ".json")).write_text(
        '{"state":"creating","state":"complete"}', encoding="utf-8"
    )
    with pytest.raises(ValueError):
        cloud.save_target(
            store,
            {**value, "parent": "/drive/other"},
            expected_server=frozen["server"],
            expected_project_id=frozen["project_id"],
            expected_revision=cloud._target_bytes(store)[1],
        )


def test_deleted_backup_profile_can_only_be_replaced_without_unfinished_journal(
    connection, monkeypatch
):
    store, settings, value, frozen, _ = connection
    old_id = value["provider_id"]
    replacement = "e" * 32

    def profile(_store, identifier):
        if identifier == old_id:
            raise KeyError(identifier)
        assert identifier == replacement
        return {"settings": settings}

    monkeypatch.setattr(providers, "profile", profile)
    revision = cloud._target_bytes(store)[1]
    kwargs = {"expected_server": frozen["server"], "expected_project_id": frozen["project_id"]}
    root = store.root / "portable-transfers"
    root.mkdir()
    journal = root / ("1" * 32 + ".json")
    journal.write_text('{"state":"creating"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Review earlier"):
        cloud.save_target(
            store, {**value, "provider_id": replacement}, expected_revision=revision, **kwargs
        )
    journal.unlink()  # synthetic fixture only
    updated, _ = cloud.save_target(
        store, {**value, "provider_id": replacement}, expected_revision=revision, **kwargs
    )
    assert updated["provider_id"] == replacement


def test_concurrent_saves_and_approval_aba_are_fenced(connection):
    store, _, value, _, _ = connection
    initial = cloud._target_bytes(store)[1]
    kwargs = {"expected_server": "https://cloud.example", "expected_project_id": "project"}
    with cloud.operation_lock(store.root / "portable-cloud-settings.lock"):
        with pytest.raises(ValueError, match="already running"):
            cloud.save_target(
                store,
                {**value, "whole_library_approved": False},
                expected_revision=initial,
                **kwargs,
            )
    _, revoked = cloud.save_target(
        store, {**value, "whole_library_approved": False}, expected_revision=initial, **kwargs
    )
    with pytest.raises(cloud.StaleBackupStorage):
        cloud.save_target(store, value, expected_revision=initial, **kwargs)
    assert cloud.load_target(store)["whole_library_approved"] is False
    _, approved = cloud.save_target(store, value, expected_revision=revoked, **kwargs)
    assert approved != initial
    with pytest.raises(cloud.StaleBackupStorage):
        cloud.save_target(
            store, {**value, "whole_library_approved": False}, expected_revision=initial, **kwargs
        )
    assert cloud.load_target(store)["whole_library_approved"] is True


def test_previous_bound_settings_remain_readable_without_rewriting(connection):
    store, _, _, frozen, _ = connection
    path = store.root / cloud.TARGET_FILE
    saved = json.loads(path.read_bytes())
    saved["format"] = "cbs-portable-cloud-settings-v2"
    del saved["write_id"]
    cloud._journal(path, saved)
    before = path.read_bytes()
    assert cloud.load_target(store) == frozen
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["server", "project"])
def test_approved_destination_cannot_follow_a_changed_provider(connection, monkeypatch, field):
    store, settings, _, _, credentials = connection
    original = (store.root / cloud.TARGET_FILE).read_bytes()
    changed = copy.deepcopy(settings)
    if field == "server":
        changed["base_url"] = "https://unapproved.example"
    else:
        changed["ucloud_transcriber"]["project_id"] = "unapproved-project"
    monkeypatch.setattr(providers, "profile", lambda *args: {"settings": changed})
    current = cloud.load_target(store)
    assert current["whole_library_approved"] is False
    assert (store.root / cloud.TARGET_FILE).read_bytes() == original

    def forbidden(*args, **kwargs):
        pytest.fail("Unapproved identity must refuse before credentials/client creation")

    monkeypatch.setattr(credentials, "get", forbidden)
    monkeypatch.setattr(cloud.native, "client_for", forbidden)
    with pytest.raises(ValueError, match="Approve"):
        cloud._connection(store, credentials, current, upload=True)


def test_unrelated_model_change_preserves_the_exact_storage_approval(connection, monkeypatch):
    store, settings, _, frozen, _ = connection
    changed = {**settings, "model": "medium", "language": "en"}
    monkeypatch.setattr(providers, "profile", lambda *args: {"settings": changed})
    assert cloud.load_target(store) == frozen


@pytest.mark.parametrize("approved", [True, False])
def test_legacy_settings_keep_choices_but_never_invent_approval_binding(connection, approved):
    store, _, value, _, _ = connection
    legacy = {**value, "whole_library_approved": approved}
    cloud._journal(store.root / cloud.TARGET_FILE, legacy)
    before = (store.root / cloud.TARGET_FILE).read_bytes()
    current = cloud.load_target(store)
    assert current["whole_library_approved"] is False
    assert current["provider_id"] == value["provider_id"] and current["parent"] == value["parent"]
    assert (store.root / cloud.TARGET_FILE).read_bytes() == before
    assert (
        cloud.save_target(
            store,
            value,
            expected_server="https://cloud.example",
            expected_project_id="project",
            expected_revision=cloud._target_bytes(store)[1],
        )[0]["whole_library_approved"]
        is True
    )
    assert cloud.load_target(store)["whole_library_approved"] is True


def test_explicit_save_can_approve_new_identity_without_changing_old_receipt(
    connection, monkeypatch
):
    store, settings, value, frozen, credentials = connection
    changed = {**settings, "base_url": "https://explicitly-approved.example"}
    monkeypatch.setattr(providers, "profile", lambda *args: {"settings": changed})
    assert cloud.load_target(store)["whole_library_approved"] is False
    fresh = cloud.save_target(
        store,
        value,
        expected_server=changed["base_url"],
        expected_project_id="project",
        expected_revision=cloud._target_bytes(store)[1],
    )[0]
    assert fresh["whole_library_approved"] is True
    assert fresh["server"] != frozen["server"]
    assert cloud.load_target(store) == fresh
    monkeypatch.setattr(cloud.native, "client_for", lambda *args: pytest.fail("Wrong identity"))
    with pytest.raises(ValueError, match="changed"):
        cloud._connection(store, credentials, frozen, upload=False)


@pytest.mark.parametrize("field", ["parent", "allowed_transfer_origins"])
def test_persisted_choice_changes_do_not_expand_saved_approval(connection, field):
    store, _, _, frozen, _ = connection
    path = store.root / cloud.TARGET_FILE
    saved = json.loads(path.read_text())
    assert saved["approved_target"]["server"] == frozen["server"]
    saved["settings"][field] = (
        "/other-drive/backups" if field == "parent" else ["https://unapproved-transfer.example"]
    )
    cloud._journal(path, saved)
    assert cloud.load_target(store)["whole_library_approved"] is False


def test_legacy_frozen_upload_resumes_only_after_explicit_exact_target_reapproval(
    connection, sealed, monkeypatch
):
    store, _, value, frozen, credentials = connection
    package, reference, _ = sealed
    captured = transport._capture_transport(package, expected_commit_sha256=reference)
    remote = frozen["parent"] + "/cbs-backup-" + captured.backup_id
    memory = Memory()
    memory.folders.update({remote, remote + "/objects"})
    for path in package.rglob("*"):
        if path.is_file():
            memory.files[remote + "/" + path.relative_to(package).as_posix()] = path.read_bytes()
    monkeypatch.setattr(cloud.native, "client_for", lambda *args: memory)
    monkeypatch.setattr(
        cloud.native, "upload", lambda *args, **kwargs: pytest.fail("Never reupload")
    )
    monkeypatch.setattr(cloud.portable_semantics, "inspect_database", lambda *args, **kwargs: {})

    def read(client, target, name, *, size, digest, **kwargs):
        raw = client.files[name]
        assert len(raw) == size and hashlib.sha256(raw).hexdigest() == digest
        return {"size": size, "sha256": digest}

    monkeypatch.setattr(cloud, "_download", read)
    journal = store.root / "portable-transfers" / ("1" * 32 + ".json")
    journal.parent.mkdir()
    receipt = {
        "format": "cbs-portable-transfer-v1",
        "operation_id": "1" * 32,
        "backup_id": captured.backup_id,
        "commit_sha256": reference,
        "manifest_sha256": captured.manifest_sha256,
        "target": frozen,
        "remote_folder": remote,
        "local_package": str(captured.root),
        "state": "complete",
        "verified": ["objects/" + name for name in captured.object_names] + ["manifest.json"],
        "completed_at": "2026-10-29T12:00:00+00:00",
    }
    cloud._journal(journal, receipt)
    before = journal.read_bytes()
    cloud._journal(store.root / cloud.TARGET_FILE, value)  # Unbound legacy settings.
    assert cloud.load_target(store)["whole_library_approved"] is False
    with cloud._connection(store, credentials, frozen, upload=False):
        pass  # Recovery of that exact old destination stays available.
    with pytest.raises(ValueError, match="Approve"):
        cloud.upload_package(
            store, credentials, package, expected_commit_sha256=reference, operation_id="1" * 32
        )
    assert journal.read_bytes() == before
    assert (
        cloud.save_target(
            store,
            value,
            expected_server=frozen["server"],
            expected_project_id=frozen["project_id"],
            expected_revision=cloud._target_bytes(store)[1],
        )[0]
        == frozen
    )
    resumed = cloud.upload_package(
        store, credentials, package, expected_commit_sha256=reference, operation_id="1" * 32
    )
    assert resumed == receipt and journal.read_bytes() == before
