"""HTTP routes for complete backups, isolated restore and manual UCloud retention (ADR 0096).

Synthetic libraries only. UCloud control/transfer calls are replaced by in-memory fakes; no
token, recording, remote transfer or paid job is used.
"""

import hashlib
import json
import os
import threading
from pathlib import Path

import pytest
from admission_support import write_original_wav
from conftest import MemoryCredentials
from fastapi.testclient import TestClient
from test_portable_cloud import Memory
from test_portable_restore import destination

from cbs_transcriber import jobs, libraries
from cbs_transcriber import portable_cloud as cloud
from cbs_transcriber import portable_transport as transport
from cbs_transcriber.app import create_app
from cbs_transcriber.providers import Settings, credential_scope
from cbs_transcriber.store import Store, new_id, now

TOKEN = "SYNTHETIC_UCLOUD_TOKEN_CANARY"
HEADERS = {"X-CBS-Request": "1"}


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    """Restores register a recent library; never touch this computer's real registry."""
    path = tmp_path / "registry" / "libraries.json"
    monkeypatch.setattr(libraries, "registry_path", lambda: path)
    return path


@pytest.fixture
def served(tmp_path, credentials):
    """The serving lease is what the real app's lifespan holds while its workers run.

    These tests disable workers for determinism, so the test holds the same lease for the
    app's lifetime instead. Handlers only observe the lease; they never re-enter it.
    """
    app = create_app(tmp_path / "library", start_worker=False, credentials=credentials)
    store = app.state.store
    with store.library_lock(), TestClient(app, headers=HEADERS) as client:
        yield client


def recording_with_original(store, name="Synthetic backup"):
    project = store.create_project(name)
    recording = store.create_recording(project["id"], "tiny.wav")
    write_original_wav(store, recording)
    return recording


def ucloud_provider(store, credentials, *, token=TOKEN):
    settings = Settings.model_validate(
        {
            "provider_type": "ucloud",
            "base_url": "https://cloud.example.test",
            "model": "large-v3",
            "capability": "words_speakers",
            "language": "da",
            "approved_for": ["protected"],
            "ucloud_transcriber": {
                "project_id": "synthetic-project",
                "application": {"name": "transcriber", "version": "1.12"},
                "storage_path": "/synthetic-drive/transcriber",
            },
        }
    )
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, "Synthetic UCloud", settings.model_dump_json(), now()),
    )
    if token:
        credentials.set(credential_scope(settings), token, False)
    return identifier


def openai_provider(store):
    settings = Settings.model_validate(
        {"provider_type": "openai", "base_url": "https://api.openai.com/v1", "model": "whisper-1"}
    )
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, "Synthetic OpenAI", settings.model_dump_json(), now()),
    )
    return identifier


def target(provider_id, *, approved=True, **changes):
    return {
        "provider_id": provider_id,
        "parent": "/drive/backups",
        "allowed_transfer_origins": ["https://storage.example"],
        "whole_library_approved": approved,
        "expected_server": "https://cloud.example.test",
        "expected_project_id": "synthetic-project",
        "expected_revision": hashlib.sha256(b"").hexdigest(),
        **changes,
    }


def capture(client):
    response = client.post("/api/library/portable-backups", json={})
    assert response.status_code == 201, response.text
    return response.json()


def current_revision(client, body):
    return {
        **body,
        "expected_revision": client.get("/api/library/portable-storage").json()["storage_revision"],
    }


def test_simple_backup_uses_unique_existing_connection_and_project_drive(
    served, credentials, monkeypatch
):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    store = served.app.state.store
    first = ucloud_provider(store, credentials)
    second = ucloud_provider(store, credentials)
    monkeypatch.setattr(portable_simple.native, "client_for", lambda *args: nullcontext(object()))
    monkeypatch.setattr(
        portable_simple,
        "list_writable_drives",
        lambda client: [{"id": "synthetic-drive", "specification": {"title": "Research drive"}}],
    )
    result = served.get("/api/library/portable-simple/locations")
    assert result.status_code == 200, result.text
    result = result.json()
    assert result["connection"]["provider_id"] in {first, second}
    assert result["connection"]["project_id"] == "synthetic-project"
    assert result["locations"] == [
        {"id": "/synthetic-drive/CBS-Transcriber-Backups", "label": "Research drive"}
    ]
    assert result["selected"] == "/synthetic-drive/CBS-Transcriber-Backups"
    assert result["target"] is None


def test_simple_backup_refuses_ambiguous_projects_and_invalid_drives(
    served, credentials, monkeypatch
):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    store = served.app.state.store
    first = ucloud_provider(store, credentials)
    assert served.put("/api/library/portable-storage", json=target(first)).status_code == 200
    other = ucloud_provider(store, credentials)
    row = store.one("SELECT value FROM providers WHERE id=?", (other,))
    changed = json.loads(row["value"])
    changed["ucloud_transcriber"]["project_id"] = "another-project"
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(changed), other))
    response = served.get("/api/library/portable-simple/locations")
    assert response.status_code == 400
    assert "More than one UCloud project" in response.json()["detail"]

    store.execute("DELETE FROM providers WHERE id=?", (other,))
    monkeypatch.setattr(portable_simple.native, "client_for", lambda *args: nullcontext(object()))
    for invalid in ("../escape", "x" * 201, "bad\x7f"):
        monkeypatch.setattr(
            portable_simple, "list_writable_drives", lambda client, bad=invalid: [{"id": bad}]
        )
        assert served.get("/api/library/portable-simple/locations").status_code == 400
    assert first


def test_simple_backup_disambiguates_duplicate_drive_names(served, credentials, monkeypatch):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    ucloud_provider(served.app.state.store, credentials)
    monkeypatch.setattr(portable_simple.native, "client_for", lambda *args: nullcontext(object()))
    monkeypatch.setattr(
        portable_simple,
        "list_writable_drives",
        lambda client: [
            {"id": "drive-a", "specification": {"title": "Research"}},
            {"id": "drive-b", "specification": {"title": "Research"}},
        ],
    )
    result = served.get("/api/library/portable-simple/locations").json()
    assert [item["label"] for item in result["locations"]] == [
        "Research (drive-a)",
        "Research (drive-b)",
    ]
    assert result["selected"] is None


def test_simple_backup_requires_confirmation_and_fences_changed_settings(
    served, credentials, monkeypatch
):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    store = served.app.state.store
    ucloud_provider(store, credentials)
    monkeypatch.setattr(portable_simple.native, "client_for", lambda *args: nullcontext(object()))
    monkeypatch.setattr(
        portable_simple, "list_writable_drives", lambda client: [{"id": "synthetic-drive"}]
    )
    shown = served.get("/api/library/portable-simple/locations").json()
    body = {
        "connection": shown["connection"],
        "revision": shown["storage_revision"],
        "location": shown["selected"],
        "confirmed": False,
    }
    assert served.post("/api/library/portable-simple/prepare", json=body).status_code == 400
    assert not (store.root / "portable-cloud-settings.json").exists()
    body["confirmed"] = True
    saved = served.post("/api/library/portable-simple/prepare", json=body)
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert saved["whole_library_approved"] is True
    assert saved["allowed_transfer_origins"] == ["https://cloud.example.test"]
    assert served.post("/api/library/portable-simple/prepare", json=body).status_code == 409


def test_simple_backup_prepares_dedicated_folder_and_can_revoke(served, credentials, monkeypatch):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    store = served.app.state.store
    ucloud_provider(store, credentials)
    monkeypatch.setattr(portable_simple.native, "client_for", lambda *args: nullcontext(object()))
    monkeypatch.setattr(
        portable_simple, "list_writable_drives", lambda client: [{"id": "synthetic-drive"}]
    )
    seen = []
    monkeypatch.setattr(
        portable_simple,
        "prepare_storage_directory",
        lambda client, path, *, create: seen.append((path, create)) or (),
    )
    shown = served.get("/api/library/portable-simple/locations").json()
    prepared = served.post(
        "/api/library/portable-simple/prepare",
        json={
            "connection": shown["connection"],
            "revision": shown["storage_revision"],
            "location": shown["selected"],
            "confirmed": True,
        },
    ).json()
    snapshot = {
        "expected_revision": prepared["storage_revision"],
        "expected_target_fingerprint": prepared["fingerprint"],
    }
    assert served.post("/api/library/portable-simple/folder", json=snapshot).json() == {
        "ready": True
    }
    assert seen == [("/synthetic-drive/CBS-Transcriber-Backups", True)]
    assert (
        prepared["restore_target_identity"]
        == served.get("/api/library/portable-storage").json()["restore_target_identity"]
    )
    stopped = served.post("/api/library/portable-simple/stop", json=snapshot)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["whole_library_approved"] is False
    assert stopped.json()["restore_target_identity"] == prepared["restore_target_identity"]
    assert (
        stopped.json()["restore_target_identity"]
        == served.get("/api/library/portable-storage").json()["restore_target_identity"]
    )
    assert served.post("/api/library/portable-simple/folder", json=snapshot).status_code == 409


def test_stop_during_folder_check_prevents_later_metadata_or_create(
    served, credentials, monkeypatch
):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    store = served.app.state.store
    provider = ucloud_provider(store, credentials)
    saved = served.put("/api/library/portable-storage", json=target(provider)).json()
    snapshot = {
        "expected_revision": saved["storage_revision"],
        "expected_target_fingerprint": saved["fingerprint"],
    }
    calls = []

    class Client:
        def retrieve_file(self, path):
            calls.append("retrieve")
            portable_simple.revoke(
                store,
                revision=snapshot["expected_revision"],
                fingerprint=snapshot["expected_target_fingerprint"],
            )
            return {}

        def create_folder(self, path):
            calls.append("create")

    monkeypatch.setattr(
        portable_simple.portable_cloud, "_connection", lambda *a, **k: nullcontext(Client())
    )

    def checking(client, path, *, create):
        client.retrieve_file(path)
        client.create_folder(path)

    monkeypatch.setattr(portable_simple, "prepare_storage_directory", checking)
    result = served.post("/api/library/portable-simple/folder", json=snapshot)
    assert result.status_code == 409
    assert calls == ["retrieve"]


def test_creator_admission_survives_lost_reply_and_can_abandon_before_upload(
    served, credentials, monkeypatch
):
    store = served.app.state.store
    recording_with_original(store)
    saved = capture(served)
    provider = ucloud_provider(store, credentials)
    assert served.put("/api/library/portable-storage", json=target(provider)).status_code == 200
    snapshot = reviewed_storage(served)
    read = transport._read

    def receipt_only(path, *args, **kwargs):
        assert not Path(path).is_relative_to(Path(saved["folder"]))
        return read(path, *args, **kwargs)

    monkeypatch.setattr(transport, "_read", receipt_only)
    body = {**snapshot, "backup_id": saved["backup_id"], "operation_id": "1" * 32}
    assert served.post("/api/library/portable-simple/admit", json=body).status_code == 200
    shown = served.get("/api/library/portable-operations").json()["uploads"]
    assert len(shown) == 1 and shown[0]["state"] == "validating"
    duplicate = served.post(
        "/api/library/portable-simple/admit", json={**body, "operation_id": "2" * 32}
    )
    assert duplicate.status_code == 400 and "unfinished upload" in duplicate.json()["detail"]
    abandon = f"/api/library/portable-simple/{'1' * 32}/abandon"
    assert served.post(abandon, json={"confirmed": False}).status_code == 400
    from cbs_transcriber.portable_restore import operation_lock

    with operation_lock(store.root / "portable-transfers" / (("1" * 32) + ".admission.lock")):
        running = served.post(abandon, json={"confirmed": True})
        assert running.status_code == 400 and "already running" in running.json()["detail"]
    assert served.post(abandon, json={"confirmed": True}).json()["state"] == "abandoned"
    assert Path(saved["folder"]).exists()
    assert (
        served.post(
            "/api/library/portable-simple/admit", json={**body, "operation_id": "2" * 32}
        ).status_code
        == 200
    )


def test_simple_backup_replaces_deleted_profile_only_after_explicit_confirmation(
    served, credentials, monkeypatch
):
    from contextlib import nullcontext

    from cbs_transcriber import portable_simple

    store = served.app.state.store
    old = ucloud_provider(store, credentials)
    response = served.put("/api/library/portable-storage", json=target(old))
    assert response.status_code == 200, response.text
    store.execute("DELETE FROM providers WHERE id=?", (old,))
    replacement = ucloud_provider(store, credentials)
    monkeypatch.setattr(portable_simple.native, "client_for", lambda *args: nullcontext(object()))
    monkeypatch.setattr(
        portable_simple, "list_writable_drives", lambda client: [{"id": "synthetic-drive"}]
    )
    shown = served.get("/api/library/portable-simple/locations").json()
    assert shown["connection"]["provider_id"] == replacement
    assert "previous backup connection is unavailable" in shown["problem"]
    body = {
        "connection": shown["connection"],
        "revision": shown["storage_revision"],
        "location": shown["selected"],
        "confirmed": True,
    }
    saved = served.post("/api/library/portable-simple/prepare", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["provider_id"] == replacement


def test_backup_volume_hint_is_conservative_and_validated(served, monkeypatch, tmp_path):
    from cbs_transcriber import portable_capture

    library = served.app.state.store.root
    destination = tmp_path / "backup-parent"
    assert served.get("/api/storage").json()["default_backup_parent"] == str(library / "backups")
    monkeypatch.setattr(portable_capture, "_local_volume", lambda path: 17)
    assert served.post("/api/library/portable-backup-volume-hint", json={}).json() == {
        "relationship": "same_volume"
    }
    response = served.post(
        "/api/library/portable-backup-volume-hint", json={"destination": str(destination)}
    )
    assert response.json() == {"relationship": "same_volume"}

    monkeypatch.setattr(
        portable_capture, "_local_volume", lambda path: 17 if path == library else 18
    )
    assert served.post("/api/library/portable-backup-volume-hint", json={}).json() == {
        "relationship": "unknown"
    }
    assert served.post(
        "/api/library/portable-backup-volume-hint", json={"destination": str(destination)}
    ).json() == {"relationship": "unknown"}
    assert (
        served.post(
            "/api/library/portable-backup-volume-hint", json={"destination": "x" * 4001}
        ).status_code
        == 422
    )


def test_local_volume_observation_uses_existing_ancestor_and_refuses_unknown(tmp_path, monkeypatch):
    from cbs_transcriber import portable_capture

    if portable_capture.os.name != "nt":
        assert portable_capture._local_volume(tmp_path) is None
        return
    volume = portable_capture._local_volume(tmp_path)
    if volume is None:
        pytest.skip("The temporary directory is not on a known local Windows volume")
    assert portable_capture._local_volume(tmp_path / "not-yet-created" / "backup") == volume
    assert portable_capture._local_volume(tmp_path / "not-yet-created" / ".." / "backup") is None
    assert portable_capture._local_volume(Path("relative-folder")) is None
    assert portable_capture._local_volume(Path(r"\\server\share\backup")) is None
    linked = tmp_path / "redirected"
    linked.mkdir()
    original_lstat = Path.lstat

    def redirected_lstat(path):
        if path == linked:
            from types import SimpleNamespace

            return SimpleNamespace(
                st_mode=original_lstat(path).st_mode,
                st_file_attributes=0x400,
                st_dev=volume,
            )
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", redirected_lstat)
    assert portable_capture._local_volume(linked) is None
    monkeypatch.setattr(portable_capture, "_local_volume", lambda path: None)
    assert portable_capture.backup_volume_hint(tmp_path, str(tmp_path / "backup")) == "unknown"


def test_storage_settings_validate_scope_and_never_store_tokens(served, credentials):
    store = served.app.state.store
    shown = served.get("/api/library/portable-storage").json()
    assert shown == {
        "target": None,
        "restore_target_identity": None,
        "storage_problem": None,
        "storage_revision": hashlib.sha256(b"").hexdigest(),
        "retention": {"keep_last": 3, "minimum_age_days": 30, "holds": []},
        "institutional_approval": "not_checked",
    }
    ucloud = ucloud_provider(store, credentials)
    for invalid in (
        {**target(ucloud), "extra": True},
        target("not-a-provider-id"),
        target(ucloud, approved="true"),  # Consent must be an explicit boolean.
        target(ucloud, allowed_transfer_origins=[]),
        {key: value for key, value in target(ucloud).items() if key != "whole_library_approved"},
    ):
        response = served.put("/api/library/portable-storage", json=invalid)
        assert response.status_code == 422
        assert "Invalid request fields" in response.json()["detail"]
    refusals = [
        (target(openai_provider(store)), "connected UCloud profile"),
        (target(ucloud, parent="drive/backups"), "existing UCloud folder"),
        (target(ucloud, allowed_transfer_origins=["http://storage.example"]), "HTTPS"),
        (target(ucloud, allowed_transfer_origins=["https://storage.example/path"]), "HTTPS"),
        (target(ucloud, allowed_transfer_origins=["https://u:p@storage.example"]), "HTTPS"),
    ]
    for body, message in refusals:
        response = served.put("/api/library/portable-storage", json=body)
        assert response.status_code == 400 and message in response.json()["detail"]
    missing = served.put("/api/library/portable-storage", json=target("a" * 32))
    assert missing.status_code == 404
    assert served.get("/api/library/portable-storage").json()["target"] is None

    origins = ["https://Storage.Example", "https://storage.example", "https://b.example"]
    saved = served.put(
        "/api/library/portable-storage",
        json=target(ucloud, approved=False, allowed_transfer_origins=origins),
    )
    assert saved.status_code == 200
    record = saved.json()
    record.pop("storage_revision")
    restore_identity = record.pop("restore_target_identity")
    assert record["whole_library_approved"] is False
    assert record["allowed_transfer_origins"] == ["https://b.example", "https://storage.example"]
    assert record["server"] == "https://cloud.example.test"
    assert (record["project_id"], record["drive_id"]) == ("synthetic-project", "drive")
    assert record["institutional_approval"] == "not_checked"
    loaded = served.get("/api/library/portable-storage").json()
    assert loaded["target"] == record
    assert loaded["restore_target_identity"] == restore_identity
    approved = served.put(
        "/api/library/portable-storage", json=current_revision(served, target(ucloud))
    ).json()
    assert approved["whole_library_approved"] is True
    assert approved["fingerprint"] != record["fingerprint"]
    raw = (store.root / cloud.TARGET_FILE).read_bytes()
    assert TOKEN.encode() not in raw and TOKEN not in json.dumps(approved)


def test_missing_backup_connection_is_reported_without_changing_saved_target(served, credentials):
    store = served.app.state.store
    original = ucloud_provider(store, credentials)
    assert served.put("/api/library/portable-storage", json=target(original)).status_code == 200
    path = store.root / cloud.TARGET_FILE
    before = path.read_bytes()
    store.execute("DELETE FROM providers WHERE id=?", (original,))

    shown = served.get("/api/library/portable-storage")
    assert shown.status_code == 200
    assert shown.json()["target"] is None
    assert "no longer exists" in shown.json()["storage_problem"]
    assert path.read_bytes() == before
    with pytest.raises(cloud.MissingBackupConnection):
        cloud.load_target(store)

    replacement = ucloud_provider(store, credentials)
    result = served.put(
        "/api/library/portable-storage",
        json=current_revision(served, target(replacement, approved=False)),
    )
    assert result.status_code == 200
    assert result.json()["provider_id"] == replacement
    assert result.json()["whole_library_approved"] is False


def test_unavailable_target_returns_revision_of_the_same_observation(
    served, credentials, monkeypatch
):
    store = served.app.state.store
    provider = ucloud_provider(store, credentials)
    assert served.put("/api/library/portable-storage", json=target(provider)).status_code == 200
    path = store.root / cloud.TARGET_FILE
    before = path.read_bytes()
    expected = hashlib.sha256(before).hexdigest()

    def replaced_during_resolution(*_):
        newer = json.loads(before)
        newer["write_id"] = "f" * 32
        cloud._journal(path, newer)
        raise cloud.MissingBackupConnection("Synthetic removal")

    monkeypatch.setattr(cloud, "_target", replaced_during_resolution)
    shown = served.get("/api/library/portable-storage")
    assert shown.status_code == 200 and shown.json()["target"] is None
    assert shown.json()["storage_revision"] == expected
    assert hashlib.sha256(path.read_bytes()).hexdigest() != expected


@pytest.mark.parametrize("changed", ["server", "project", "kind"])
def test_changed_profile_does_not_reapprove_an_unseen_backup_destination(
    served, credentials, changed
):
    store = served.app.state.store
    provider = ucloud_provider(store, credentials)
    body = target(provider)
    assert served.put("/api/library/portable-storage", json=body).status_code == 200
    body = current_revision(served, body)
    path = store.root / cloud.TARGET_FILE
    before = path.read_bytes()
    row = store.one("SELECT value FROM providers WHERE id=?", (provider,))
    saved = json.loads(row["value"])
    if changed == "server":
        saved["base_url"] = "https://changed.example.test"
    elif changed == "project":
        saved["ucloud_transcriber"]["project_id"] = "changed-project"
    else:
        saved["provider_type"] = "custom"
        saved["ucloud_transcriber"] = None
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(saved), provider))

    stale = served.put("/api/library/portable-storage", json=body)
    assert stale.status_code == (400 if changed == "kind" else 409)
    assert path.read_bytes() == before
    shown = served.get("/api/library/portable-storage")
    assert shown.status_code == 200 and shown.json()["storage_problem"]
    if changed == "kind":
        assert shown.json()["target"] is None
    else:
        assert shown.json()["target"]["whole_library_approved"] is False
    assert path.read_bytes() == before
    if changed != "kind":
        reviewed = {
            **body,
            "expected_server": saved["base_url"],
            "expected_project_id": saved["ucloud_transcriber"]["project_id"],
        }
        approved = served.put("/api/library/portable-storage", json=reviewed)
        assert approved.status_code == 200 and approved.json()["whole_library_approved"] is True


def test_capture_route_works_in_a_running_app_with_workers(tmp_path):
    """The real server holds the lease from its lifespan; no test-only lease here."""
    app = create_app(tmp_path / "library", credentials=MemoryCredentials())
    store = app.state.store
    recording = recording_with_original(store)
    with TestClient(app, headers=HEADERS) as client:
        assert store._lease_depth == 1  # Held by the lifespan while workers run.
        result = capture(client)
        listed = client.get("/api/library/portable-backups").json()["captures"]
    assert result["scope"] == "whole_library" and result["credentials"] == "excluded"
    assert result["history_provenance"] == "domain_transform_validation_not_checked"
    assert result["originals"] == 1 and result["legacy_gaps"] >= 0
    folder = Path(result["folder"])
    assert folder.parent == store.root / "backups"
    transport.inspect_transport(folder, expected_commit_sha256=result["commit_sha256"])
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    [original] = [item for item in manifest["files"] if item["role"] == "original"]
    assert original["recording_id"] == recording["id"]
    # The creator receipt survives a lost reply; it is the capture result without route labels.
    labels = {"scope", "credentials", "history_provenance"}
    assert listed == [{key: value for key, value in result.items() if key not in labels}]
    assert not app.state.queue.capture_paused


def test_lost_capture_reply_recovers_only_a_matching_published_package(served):
    store = served.app.state.store
    recording_with_original(store)
    result = capture(served)
    identifier = result["backup_id"]
    url = f"/api/library/portable-backups/{identifier}/verify"

    recovered = served.get(url)
    assert recovered.status_code == 200
    assert recovered.json()["commit_sha256"] == result["commit_sha256"]
    assert recovered.json()["verification"] == "transport_integrity_only"
    assert recovered.json()["history_provenance"] == "domain_transform_validation_not_checked"

    # Receipts are written before publication. A missing marker and altered object must
    # never turn a partial or damaged package into a usable recovered receipt.
    folder = Path(result["folder"])
    marker = folder / "COMMITTED.json"
    saved_marker = marker.read_bytes()
    marker.unlink()
    assert served.get(url).status_code == 400
    assert len(served.get("/api/library/portable-backups").json()["captures"]) == 1
    marker.write_bytes(saved_marker)
    obj = next((folder / "objects").iterdir())
    obj.write_bytes(b"damaged synthetic backup")
    assert served.get(url).status_code == 400
    assert obj.exists() and marker.exists()
    assert served.get(f"/api/library/portable-backups/{'a' * 32}/verify").status_code == 400


def test_check_external_local_receipt_does_not_restore_or_remove_partial(served):
    store = served.app.state.store
    recording_with_original(store)
    saved = capture(served)
    body = {"folder": saved["folder"], "commit_sha256": saved["commit_sha256"]}
    checked = served.post("/api/library/portable-backup-check", json=body)
    assert checked.status_code == 200
    assert checked.json()["verification"] == "transport_integrity_only"
    marker = Path(saved["folder"]) / "COMMITTED.json"
    marker.unlink()
    assert served.post("/api/library/portable-backup-check", json=body).status_code == 400
    assert Path(saved["folder"]).is_dir()
    assert served.get("/api/library/portable-backups").json()["captures"]


def test_check_cloud_receipt_fences_storage_and_receipt_identity(served, monkeypatch):
    from contextlib import nullcontext

    target = {"server": "https://cloud.example", "project_id": "project"}
    calls = []
    monkeypatch.setattr(cloud, "checked_target", lambda *args: target)
    monkeypatch.setattr(cloud, "_connection", lambda *args, **kwargs: nullcontext(object()))
    monkeypatch.setattr(
        cloud,
        "inspect_remote_package",
        lambda client, frozen, folder, **kwargs: (
            calls.append((frozen, folder, kwargs))
            or {"backup_id": "a" * 32, "verification": "transport_integrity_only"}
        ),
    )
    body = {
        "folder": "/drive/backups/cbs-backup-" + "a" * 32,
        "commit_sha256": "b" * 64,
        "cloud": True,
        "expected_revision": "c" * 64,
        "expected_target_fingerprint": "d" * 64,
        "receipt_server": "https://cloud.example",
        "receipt_project_id": "project",
    }
    assert served.post("/api/library/portable-backup-check", json=body).status_code == 200
    assert len(calls) == 1
    assert (
        served.post(
            "/api/library/portable-backup-check", json={**body, "receipt_project_id": "other"}
        ).status_code
        == 400
    )
    assert len(calls) == 1
    monkeypatch.setattr(
        cloud,
        "checked_target",
        lambda *args: (_ for _ in ()).throw(cloud.StaleBackupStorage("changed")),
    )
    assert served.post("/api/library/portable-backup-check", json=body).status_code == 409
    assert len(calls) == 1


def test_capture_refuses_queued_running_and_active_work_then_reopens(served):
    store, queue = served.app.state.store, served.app.state.queue
    recording = recording_with_original(store)
    job = store.add_job(recording["id"], "media")
    for state in ("queued", "running"):
        store.execute("UPDATE jobs SET state=? WHERE id=?", (state, job["id"]))
        refused = served.post("/api/library/portable-backups", json={})
        assert refused.status_code == 400
        assert "Finish or cancel queued and running jobs" in refused.json()["detail"]
    store.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))
    worker = queue.workers[1]
    worker.active_id = job["id"]
    try:
        refused = served.post("/api/library/portable-backups", json={})
    finally:
        worker.active_id = None
    assert refused.status_code == 400 and "active work" in refused.json()["detail"]
    assert served.get("/api/library/portable-backups").json() == {"captures": []}
    backups = store.root / "backups"
    assert not backups.exists() or not any(backups.iterdir())  # No refused partial debris.
    # The gate and queue admission are released after each refusal.
    assert not queue.capture_paused
    policy = {"keep_last": 2, "minimum_age_days": 10, "holds": []}
    assert served.put("/api/library/portable-retention", json=policy).status_code == 200
    assert capture(served)["originals"] == 1


def test_idle_runtime_capacity_observation_delays_but_never_refuses_capture(served, monkeypatch):
    """Regression: idle Runtime lanes briefly mark an observation every half second."""
    queue = served.app.state.queue
    recording_with_original(served.app.state.store)
    lane = queue.workers[-1]
    assert lane.kind == "runtime-batch"
    lane.active_ids = [jobs.CAPACITY_OBSERVATION]

    def finish_observation():
        assert queue.capture_paused  # New claims and observations are already paused.
        with queue.lock:
            lane.active_ids = []

    timer = threading.Timer(0.3, finish_observation)
    timer.start()
    try:
        assert capture(served)["originals"] == 1
    finally:
        timer.join()
    assert not queue.capture_paused

    monkeypatch.setattr(jobs, "OBSERVATION_SETTLE_SECONDS", 0.2)
    lane.active_ids = [jobs.CAPACITY_OBSERVATION]
    try:
        slow = served.post("/api/library/portable-backups", json={})
    finally:
        lane.active_ids = []
    assert slow.status_code == 400 and "still being checked" in slow.json()["detail"]
    assert not queue.capture_paused
    assert len(served.get("/api/library/portable-backups").json()["captures"]) == 1


def test_mutations_wait_for_capture_and_capture_waits_for_mutations(served):
    gate = served.app.state.portable_capture_gate
    policy = {"keep_last": 4, "minimum_age_days": 7, "holds": []}
    replies = {}

    def put():
        replies["put"] = served.put("/api/library/portable-retention", json=policy)

    with gate.capture():
        writer = threading.Thread(target=put)
        writer.start()
        writer.join(0.5)
        assert writer.is_alive() and "put" not in replies
        # Read-only browsing continues; the waiting mutation never blocks the event loop.
        assert served.get("/api/library/portable-storage").json()["retention"]["keep_last"] == 3
    writer.join(10)
    assert replies["put"].status_code == 200
    assert served.get("/api/library/portable-storage").json()["retention"]["keep_last"] == 4

    recording_with_original(served.app.state.store)
    gate.enter_mutation()  # An in-flight mutation that a capture must wait for.
    try:
        backup = threading.Thread(
            target=lambda: replies.update(
                capture=served.post("/api/library/portable-backups", json={})
            )
        )
        backup.start()
        backup.join(0.5)
        assert backup.is_alive() and "capture" not in replies
    finally:
        gate.leave_mutation()
    backup.join(30)
    assert replies["capture"].status_code == 201


def test_cross_origin_mutations_are_rejected_without_waiting_for_capture(served):
    store = served.app.state.store
    recording_with_original(store)
    evil = served.post(
        "/api/library/portable-backups",
        json={},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403
    unheadered = TestClient(served.app).put(
        "/api/library/portable-retention",
        json={"keep_last": 1, "minimum_age_days": 1, "holds": []},
    )
    assert unheadered.status_code == 403
    assert served.get("/api/library/portable-backups").json() == {"captures": []}
    assert served.get("/api/library/portable-storage").json()["retention"]["keep_last"] == 3

    replies = {}
    with served.app.state.portable_capture_gate.capture():
        hostile = threading.Thread(
            target=lambda: replies.update(
                reply=served.post(
                    "/api/library/prepare-restored-playback",
                    json={},
                    headers={"Origin": "https://evil.example"},
                )
            )
        )
        hostile.start()
        hostile.join(5)
        # Same-origin protection answers first; a hostile page never occupies admission.
        assert not hostile.is_alive() and replies["reply"].status_code == 403


def test_interrupted_restore_is_discoverable_and_bound_before_restore(
    served, tmp_path, monkeypatch
):
    from cbs_transcriber import portable_routes

    def interrupted(*args, **kwargs):
        raise ValueError("synthetic interruption")

    monkeypatch.setattr(portable_routes, "restore_library", interrupted)
    body = {
        "folder": str(tmp_path / "backup"),
        "commit_sha256": "a" * 64,
        "target": str(tmp_path / "target"),
        "operation_id": "b" * 32,
    }
    assert served.post("/api/library/portable-restore", json=body).status_code == 400
    records = served.get("/api/library/portable-operations").json()["restores"]
    assert records == [{**body, "cloud": False, "target_identity": None}]
    changed = served.post("/api/library/portable-restore", json={**body, "target": "elsewhere"})
    assert changed.status_code == 400 and "different choices" in changed.text
    assert served.get("/api/library/portable-operations").json()["restores"] == records

    # Crash after publication but before removing the exact owned temp link.
    root = served.app.state.store.root / "portable-restore-index"
    final = root / (body["operation_id"] + ".json")
    pending = root / (".pending-" + body["operation_id"] + "-synthetic")
    os.link(final, pending)
    assert served.get("/api/library/portable-operations").json()["restores"] == records
    assert not pending.exists()


def test_restore_destination_identity_ignores_upload_approval_only():
    from cbs_transcriber.portable_routes import _restore_target_identity

    target = {
        "server": "https://cloud.example",
        "project_id": "p",
        "parent": "/drive/backups",
        "allowed_transfer_origins": ["https://storage.example"],
        "fingerprint": "a" * 64,
        "whole_library_approved": True,
    }
    same = {**target, "fingerprint": "b" * 64, "whole_library_approved": False}
    assert _restore_target_identity(target) == _restore_target_identity(same)
    assert _restore_target_identity(target) != _restore_target_identity(
        {**same, "parent": "/other"}
    )


def test_restore_route_creates_registered_isolated_library(served, tmp_path, registry):
    store = served.app.state.store
    recording = recording_with_original(store)
    original = (store.directory(recording["id"]) / "original.wav").read_bytes()
    saved = capture(served)
    request = {"folder": saved["folder"], "commit_sha256": saved["commit_sha256"]}

    wrong = served.post(
        "/api/library/portable-restore",
        json={**request, "commit_sha256": "0" * 64, "target": str(destination(tmp_path, "w"))},
    )
    assert wrong.status_code == 400 and not destination(tmp_path, "w").exists()
    for body, message in (
        ({**request, "target": str(store.root / "nested")}, "outside the open library"),
        ({**request, "folder": "relative", "target": str(destination(tmp_path, "r"))}, "absolute"),
        ({**request, "cloud": True, "target": str(destination(tmp_path, "c"))}, "Configure"),
    ):
        response = served.post("/api/library/portable-restore", json=body)
        assert response.status_code == 400 and message in response.json()["detail"], body
    assert (
        served.post(
            "/api/library/portable-restore",
            json={**request, "cloud": "yes", "target": str(destination(tmp_path, "s"))},
        ).status_code
        == 422
    )

    chosen = destination(tmp_path, "restored")
    restored = served.post("/api/library/portable-restore", json={**request, "target": str(chosen)})
    assert restored.status_code == 201, restored.text
    body = restored.json()
    assert body["state"] == "complete" and body["registered"] is True
    assert body["registration_error"] is None and Path(body["target"]) == chosen
    assert (body["historical_jobs"], body["providers"]) == ("read_only", "disconnected")
    assert body["playback"] == "requires_explicit_preparation"
    assert str(chosen.resolve()) in json.loads(registry.read_text(encoding="utf-8"))["recent"]
    assert libraries.load()["current"] is None  # The open library is not switched.

    again = served.post("/api/library/portable-restore", json={**request, "target": str(chosen)})
    assert again.status_code == 400 and "already exists" in again.json()["detail"]
    # A lost reply is recovered with the same operation reference, before the copy is opened.
    resumed = served.post(
        "/api/library/portable-restore",
        json={**request, "target": str(chosen), "operation_id": body["operation_id"]},
    )
    assert resumed.status_code == 201 and resumed.json()["state"] == "complete"
    copy = Store(chosen)
    assert (copy.directory(recording["id"]) / "original.wav").read_bytes() == original
    assert (store.directory(recording["id"]) / "original.wav").read_bytes() == original

    # Playback preparation is a separate explicit local action in the opened copy.
    reopened = create_app(chosen, start_worker=False, credentials=MemoryCredentials())
    with reopened.state.store.library_lock(), TestClient(reopened, headers=HEADERS) as other:
        assert other.post("/api/library/prepare-restored-playback", json={}).json() == {"queued": 1}
        assert other.post("/api/library/prepare-restored-playback", json={}).json() == {"queued": 0}
        [fresh] = reopened.state.store.all(
            "SELECT id FROM jobs WHERE kind='media' AND state='queued'"
        )
        with reopened.state.store.connection() as db:
            assert not db.execute(
                "SELECT 1 FROM restored_owners WHERE owner_id=?", (fresh["id"],)
            ).fetchone()


class Remote:
    """Mocked control and transfer seams; the real _connection approval checks still run."""

    def __init__(self, monkeypatch):
        self.memory = Memory()
        self.memory.folders.add("/drive")
        self.keys, self.moved = [], []
        monkeypatch.setattr(cloud.native, "client_for", self.client_for)
        monkeypatch.setattr(cloud.native, "upload", self.upload)
        monkeypatch.setattr(cloud, "_download", self.download)
        monkeypatch.setattr(cloud.native, "trash_owned_folder", self.trash)

    def client_for(self, settings, key):
        self.keys.append(key)
        return self.memory

    def upload(self, client, server, path, remote, guard, update, **kwargs):
        guard()
        kwargs["endpoint_guard"]("https://storage.example/transient")
        self.memory.files[remote] = path.read_bytes()
        self.memory.uploads.append(remote)

    def download(self, client, frozen, remote, *, size=None, maximum=None, digest=None, **_):
        raw = self.memory.files[remote]
        if (size is not None and len(raw) != size) or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("checksum")
        return {"size": len(raw), "sha256": digest}

    def trash(self, client, folder):
        self.moved.append(folder)
        memory = self.memory
        memory.folders = {item for item in memory.folders if not item.startswith(folder)}
        memory.files = {key: raw for key, raw in memory.files.items() if not key.startswith(folder)}


def reviewed_storage(client):
    storage = client.get("/api/library/portable-storage").json()
    return {
        "expected_revision": storage["storage_revision"],
        "expected_target_fingerprint": storage["target"]["fingerprint"],
    }


def upload(client, saved, operation_id=None):
    body = {
        "folder": saved["folder"],
        "commit_sha256": saved["commit_sha256"],
        **reviewed_storage(client),
    }
    if operation_id:
        body["operation_id"] = operation_id
    return client.post("/api/library/portable-upload", json=body)


def test_stale_tab_cannot_upload_restore_or_preview_another_destination(
    served, credentials, tmp_path
):
    store = served.app.state.store
    ucloud = ucloud_provider(store, credentials)
    assert served.put("/api/library/portable-storage", json=target(ucloud)).status_code == 200
    stale = reviewed_storage(served)
    changed = current_revision(served, target(ucloud, parent="/other/backups"))
    assert served.put("/api/library/portable-storage", json=changed).status_code == 200
    package = {"folder": "synthetic", "commit_sha256": "a" * 64, **stale}
    upload_response = served.post("/api/library/portable-upload", json=package)
    restore_response = served.post(
        "/api/library/portable-restore",
        json={**package, "target": str(destination(tmp_path, "stale")), "cloud": True},
    )
    preview_response = served.post("/api/library/portable-retention/preview", json=stale)
    assert [
        upload_response.status_code,
        restore_response.status_code,
        preview_response.status_code,
    ] == [409, 409, 409]
    assert not destination(tmp_path, "stale").exists()
    # Restoring identical destination fields does not revive a stale approval.
    assert (
        served.put(
            "/api/library/portable-storage", json=current_revision(served, target(ucloud))
        ).status_code
        == 200
    )
    assert served.post("/api/library/portable-upload", json=package).status_code == 409
    reviewed = reviewed_storage(served)
    store.execute("DELETE FROM providers WHERE id=?", (ucloud,))
    deleted = {**package, **reviewed}
    assert served.post("/api/library/portable-upload", json=deleted).status_code == 409


def test_upload_operations_and_manual_retention_routes(served, credentials, monkeypatch):
    store = served.app.state.store
    remote = Remote(monkeypatch)
    recording_with_original(store)
    first = capture(served)
    storage = served.get("/api/library/portable-storage").json()
    assert storage["target"] is None
    unconfigured = served.post(
        "/api/library/portable-upload",
        json={
            "folder": first["folder"],
            "commit_sha256": first["commit_sha256"],
            "expected_revision": storage["storage_revision"],
            "expected_target_fingerprint": "0" * 64,
        },
    )
    assert unconfigured.status_code == 409
    assert not remote.memory.uploads and not remote.keys

    ucloud = ucloud_provider(store, credentials)
    served.put("/api/library/portable-storage", json=target(ucloud, approved=False))
    refused = upload(served, first, "1" * 32)
    assert refused.status_code == 400 and "whole library" in refused.json()["detail"]
    assert not remote.memory.uploads and not remote.keys
    # Regression: no journal is frozen to the unapproved settings, so the same operation
    # reference resumes once this exact destination is approved.
    assert not (store.root / "portable-transfers" / ("1" * 32 + ".json")).exists()

    served.put("/api/library/portable-storage", json=current_revision(served, target(ucloud)))
    uploaded = upload(served, first, "1" * 32)
    assert uploaded.status_code == 200, uploaded.text
    result = uploaded.json()
    assert result["state"] == "complete" and result["operation_id"] == "1" * 32
    assert result["remote_folder"] == "/drive/backups/cbs-backup-" + first["backup_id"]
    assert remote.memory.uploads[-1] == result["remote_folder"] + "/COMMITTED.json"
    assert set(remote.keys) == {TOKEN}
    assert TOKEN not in json.dumps(result)

    second = capture(served)
    assert upload(served, second, "2" * 32).json()["state"] == "complete"
    operations = served.get("/api/library/portable-operations").json()
    assert operations["restores"] == []
    assert sorted(item["operation_id"] for item in operations["uploads"]) == ["1" * 32, "2" * 32]
    assert {item["state"] for item in operations["uploads"]} == {"complete"}
    assert {item["local_package"] for item in operations["uploads"]} == {
        first["folder"],
        second["folder"],
    }
    for operation, date in (
        ("1" * 32, "2020-01-01T00:00:00+00:00"),
        ("2" * 32, "2021-01-01T00:00:00+00:00"),
    ):
        path = store.root / "portable-transfers" / (operation + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["completed_at"] = date
        cloud._journal(path, record)

    policy = {"keep_last": 1, "minimum_age_days": 30, "holds": []}
    saved_policy = served.put("/api/library/portable-retention", json=policy).json()
    assert saved_policy["mode"] == "manual_trash_only"
    invalid = {"keep_last": 0, "minimum_age_days": 30, "holds": []}
    assert served.put("/api/library/portable-retention", json=invalid).status_code == 422
    held = {**policy, "holds": ["not-an-id"]}
    assert served.put("/api/library/portable-retention", json=held).status_code == 400

    shown = served.post(
        "/api/library/portable-retention/preview", json=reviewed_storage(served)
    ).json()
    assert shown["eligible_operations"] == ["1" * 32]
    assert {row["backup_id"]: row["reason"] for row in shown["rows"]} == {
        first["backup_id"]: None,
        second["backup_id"]: "recent_copy_kept",
    }
    unconfirmed = served.post(
        "/api/library/portable-retention/apply",
        json={"fingerprint": shown["fingerprint"], "confirmed": False},
    )
    assert unconfirmed.status_code == 400 and not remote.moved
    stale = served.post(
        "/api/library/portable-retention/apply",
        json={"fingerprint": "0" * 64, "confirmed": True},
    )
    assert stale.status_code == 400 and "preview changed" in stale.json()["detail"]
    assert not remote.moved
    applied = served.post(
        "/api/library/portable-retention/apply",
        json={"fingerprint": shown["fingerprint"], "confirmed": True},
    ).json()
    assert remote.moved == [result["remote_folder"]]
    assert applied["permanent_erasure"] is False
    assert applied["outcomes"] == [{"backup_id": first["backup_id"], "state": "moved_to_trash"}]
    assert (Path(first["folder"]) / "COMMITTED.json").is_file()  # Local package retained.
    [event] = served.get("/api/library/portable-retention/events").json()["events"]
    assert event["event_id"] == applied["event_id"] and event["outcomes"] == applied["outcomes"]
    states = {
        item["operation_id"]: item["state"]
        for item in served.get("/api/library/portable-operations").json()["uploads"]
    }
    assert states == {"1" * 32: "trashed", "2" * 32: "complete"}
    resume = upload(served, first, "1" * 32)
    assert resume.status_code == 400 and "retention action" in resume.json()["detail"]


def test_changed_storage_cannot_redirect_or_download_without_matching_target(
    served, credentials, monkeypatch, tmp_path
):
    store = served.app.state.store
    remote = Remote(monkeypatch)
    recording_with_original(store)
    saved = capture(served)
    ucloud = ucloud_provider(store, credentials)
    served.put("/api/library/portable-storage", json=target(ucloud))
    assert upload(served, saved, "3" * 32).json()["state"] == "complete"
    before = list(remote.memory.uploads)
    served.put(
        "/api/library/portable-storage",
        json=current_revision(served, target(ucloud, parent="/other/backups")),
    )
    moved = upload(served, saved, "3" * 32)
    assert moved.status_code == 400 and "another package or destination" in moved.json()["detail"]
    assert remote.memory.uploads == before
    elsewhere = served.post(
        "/api/library/portable-restore",
        json={
            "folder": "/drive/backups/cbs-backup-" + saved["backup_id"],
            "commit_sha256": saved["commit_sha256"],
            "target": str(destination(tmp_path, "cloud")),
            "cloud": True,
            **reviewed_storage(served),
        },
    )
    assert elsewhere.status_code == 400 and "configured drive location" in elsewhere.text
    assert not destination(tmp_path, "cloud").exists()
