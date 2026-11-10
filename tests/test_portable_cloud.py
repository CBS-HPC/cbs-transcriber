"""Storage-only transfers against tiny synthetic bytes and mocked UCloud transports."""

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from cbs_transcriber import portable_cloud as cloud
from cbs_transcriber.portable_capture import canonical

REAL_DOWNLOAD = cloud._download


def test_unfinished_upload_blocks_new_operation_for_same_backup(sealed, storage, monkeypatch):
    store, _, memory = storage
    store.root.mkdir()

    def lost_reply(path):
        memory.folders.add(path)
        raise ValueError("lost reply")

    monkeypatch.setattr(memory, "create_folder", lost_reply)
    with pytest.raises(ValueError, match="lost reply"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    with pytest.raises(ValueError, match="unfinished upload"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="2" * 32
        )
    assert not (store.root / "portable-transfers" / ("2" * 32 + ".json")).exists()


def test_preverification_intent_is_recoverable_and_blocks_duplicate(sealed, storage, monkeypatch):
    import threading

    store, _, _ = storage
    store.root.mkdir()
    entered, release = threading.Event(), threading.Event()
    capture = cloud.transport._capture_transport

    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        raise ValueError("synthetic interruption during verification")

    monkeypatch.setattr(cloud.transport, "_capture_transport", paused)
    errors = []

    def run():
        try:
            cloud.upload_package(
                store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
            )
        except ValueError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(10)
    path = store.root / "portable-transfers" / ("1" * 32 + ".json")
    assert json.loads(path.read_text())["state"] == "validating"
    with pytest.raises(ValueError, match="already running"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    with pytest.raises(ValueError, match="unfinished upload"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="2" * 32
        )
    release.set()
    worker.join(10)
    assert not worker.is_alive() and errors == ["synthetic interruption during verification"]
    monkeypatch.setattr(cloud.transport, "_capture_transport", capture)
    result = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    assert result["state"] == "complete"


def test_completed_backup_rejects_new_upload_to_same_folder_after_origin_change(sealed, storage):
    store, target, memory = storage
    store.root.mkdir()
    cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    before = list(memory.uploads)
    target["allowed_transfer_origins"] = ["https://different.example"]
    unsigned = {key: value for key, value in target.items() if key != "fingerprint"}
    target["fingerprint"] = hashlib.sha256(canonical(unsigned)).hexdigest()
    with pytest.raises(ValueError, match="already has a completed upload"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="2" * 32
        )
    assert memory.uploads == before


def test_completed_backup_recovery_refuses_missing_verification_entries(sealed, storage):
    store, _, memory = storage
    store.root.mkdir()
    cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    path = store.root / "portable-transfers" / ("1" * 32 + ".json")
    journal = json.loads(path.read_text())
    journal["verified"] = ["manifest.json"]
    cloud._journal(path, journal)
    before = list(memory.uploads)
    with pytest.raises(ValueError, match="Review earlier backup operations"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    assert memory.uploads == before


def test_completed_backup_recovery_never_republishes_missing_marker(sealed, storage):
    store, _, memory = storage
    store.root.mkdir()
    result = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    del memory.files[result["remote_folder"] + "/COMMITTED.json"]
    before = list(memory.uploads)
    with pytest.raises(ValueError, match="missing its commit marker"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    assert memory.uploads == before


def test_revocation_between_folder_requests_stops_second_request(sealed, storage, monkeypatch):
    store, target, memory = storage
    store.root.mkdir()
    create = memory.create_folder

    def revoke_after_first(path):
        create(path)
        target["whole_library_approved"] = False

    monkeypatch.setattr(memory, "create_folder", revoke_after_first)
    with pytest.raises(ValueError, match="approval changed or was revoked"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    assert len(memory.folders) == 2
    assert not any(path.endswith("/objects") for path in memory.folders)


def test_names_checks_guard_before_each_pagination_request():
    calls = []

    class Paged:
        def browse_files(self, path, **kwargs):
            calls.append(path)
            return {"items": [], "next": "second"}

    def guard():
        if calls:
            raise ValueError("approval revoked")

    with pytest.raises(ValueError, match="approval revoked"):
        cloud._names(Paged(), "/drive/backups", guard=guard)
    assert len(calls) == 1


def test_revocation_after_download_session_does_not_start_get(storage, monkeypatch):
    _, target, _ = storage
    stopped = False

    class Response:
        def json(self):
            return {"responses": [{"endpoint": "https://storage.example/result"}]}

    class Client:
        def request(self, *args, **kwargs):
            nonlocal stopped
            stopped = True
            return Response()

    class Separate:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            pytest.fail("GET started after revocation")

    def guard():
        if stopped:
            raise ValueError("stopped")

    monkeypatch.setattr(cloud.httpx, "Client", Separate)
    with pytest.raises(ValueError, match="stopped"):
        REAL_DOWNLOAD(Client(), target, "/drive/file", maximum=10, guard=guard)


@pytest.fixture
def sealed(tmp_path):
    root = tmp_path / "sealed"
    (root / "objects").mkdir(parents=True)
    raw = b"synthetic database bytes; semantic admission is mocked in transfer tests"
    digest = hashlib.sha256(raw).hexdigest()
    (root / "objects" / digest).write_bytes(raw)
    manifest = {
        "format": "cbs-library-backup-v2",
        "backup_id": "a" * 32,
        "source_library_id": "b" * 32,
        "created_at": "2026-10-29T12:00:00Z",
        "scope": "whole_library",
        "inventory_policy": "portable-files-v1",
        "app_version": "0.1.0",
        "schema_version": 13,
        "database": {
            "object": digest,
            "size": len(raw),
            "schema_fingerprint": "c" * 64,
            "tables": {"settings": {"rows": 0, "sha256": "d" * 64}},
        },
        "objects": [{"sha256": digest, "size": len(raw)}],
        "files": [],
        "legacy_gaps": [],
        "exclusions": "portable-exclusions-v1",
    }
    raw_manifest = canonical(manifest)
    (root / "manifest.json").write_bytes(raw_manifest)
    marker = canonical(
        {
            "format": "cbs-library-backup-commit-v2",
            "backup_id": "a" * 32,
            "manifest_size": len(raw_manifest),
            "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        }
    )
    (root / "COMMITTED.json").write_bytes(marker)
    return root, hashlib.sha256(marker).hexdigest(), manifest


class Memory:
    def __init__(self):
        self.folders = {"/drive/backups"}
        self.files = {}
        self.uploads = []
        self.readbacks = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def create_folder(self, path):
        if path in self.folders:
            raise ValueError("conflict")
        self.folders.add(path)

    def browse_files(self, path, **_):
        return {
            "items": [
                {"id": name}
                for name in sorted(self.folders | set(self.files))
                if name.rsplit("/", 1)[0] == path
            ]
        }


@pytest.fixture
def storage(tmp_path, monkeypatch):
    target = {
        "parent": "/drive/backups",
        "server": "https://cloud.example",
        "project_id": "project",
        "provider_id": "a" * 32,
        "drive_id": "drive",
        "institutional_approval": "not_checked",
        "allowed_transfer_origins": ["https://storage.example"],
        "whole_library_approved": True,
    }
    target["fingerprint"] = hashlib.sha256(canonical(target)).hexdigest()
    memory = Memory()
    monkeypatch.setattr(cloud, "load_target", lambda store: dict(target))
    monkeypatch.setattr(cloud, "_connection", lambda *args, **kwargs: memory)
    monkeypatch.setattr(
        cloud.portable_semantics,
        "inspect_database",
        lambda *args, **kwargs: {"level": "synthetic_transfer_test_only"},
    )

    def upload(client, server, path, remote, guard, update, **kwargs):
        guard()
        kwargs["endpoint_guard"]("https://storage.example/transient")
        if remote.endswith("COMMITTED.json"):
            assert any(name.endswith("manifest.json") for name in memory.readbacks)
            assert any("/objects/" in name for name in memory.readbacks)
        memory.files[remote] = path.read_bytes()
        memory.uploads.append(remote)

    def read(client, frozen, remote, *, size=None, maximum=None, digest=None, **kwargs):
        raw = memory.files[remote]
        assert size is None or len(raw) == size
        assert maximum is None or len(raw) <= maximum
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("checksum")
        memory.readbacks.append(remote)
        return {"size": len(raw), "sha256": digest}

    monkeypatch.setattr(cloud.native, "upload", upload)
    monkeypatch.setattr(cloud, "_download", read)
    return SimpleNamespace(root=tmp_path / "library"), target, memory


def test_commit_is_last_after_download_back_verification(sealed, storage, monkeypatch):
    store, target, memory = storage
    store.root.mkdir()
    result = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    assert result["state"] == "complete"
    assert memory.uploads[-1].endswith("/COMMITTED.json")
    assert memory.readbacks[-1] == memory.uploads[-1]
    before = list(memory.uploads)
    monkeypatch.setattr(
        memory, "create_folder", lambda path: pytest.fail("recovery created a folder")
    )
    resumed = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    assert resumed["completed_at"] == result["completed_at"]
    assert memory.uploads == before


def test_check_remote_backup_streams_against_independent_receipt_without_writing(
    sealed, storage, monkeypatch
):
    store, target, memory = storage
    store.root.mkdir()
    uploaded = cloud.upload_package(store, None, sealed[0], expected_commit_sha256=sealed[1])
    before = dict(memory.files)

    def read(client, frozen, remote, *, size=None, maximum=None, digest=None, collect=False, **_):
        if remote not in memory.files:
            raise ValueError("missing")
        raw = memory.files[remote]
        if (size is not None and len(raw) != size) or (maximum is not None and len(raw) > maximum):
            raise ValueError("size")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("checksum")
        return raw if collect else {"sha256": digest, "size": len(raw)}

    monkeypatch.setattr(cloud, "_download", read)
    result = cloud.inspect_remote_package(
        memory, target, uploaded["remote_folder"], expected_commit_sha256=sealed[1]
    )
    assert result["verification"] == "transport_integrity_only"
    assert result["commit_sha256"] == sealed[1]
    assert memory.files == before
    with pytest.raises(ValueError, match="checksum"):
        cloud.inspect_remote_package(
            memory, target, uploaded["remote_folder"], expected_commit_sha256="b" * 64
        )
    object_path = next(name for name in memory.files if "/objects/" in name)
    memory.files[object_path] = b"corrupt"
    with pytest.raises(ValueError, match="size|checksum"):
        cloud.inspect_remote_package(
            memory, target, uploaded["remote_folder"], expected_commit_sha256=sealed[1]
        )
    del memory.files[uploaded["remote_folder"] + "/COMMITTED.json"]
    with pytest.raises(ValueError, match="missing"):
        cloud.inspect_remote_package(
            memory, target, uploaded["remote_folder"], expected_commit_sha256=sealed[1]
        )


def test_failed_checksum_never_publishes_commit(sealed, storage, monkeypatch):
    store, _, memory = storage
    store.root.mkdir()
    monkeypatch.setattr(
        cloud, "_download", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("checksum"))
    )
    with pytest.raises(ValueError, match="checksum"):
        cloud.upload_package(store, None, sealed[0], expected_commit_sha256=sealed[1])
    assert not any(name.endswith("COMMITTED.json") for name in memory.uploads)


def test_lost_folder_reply_retains_uncertainty_and_does_not_claim_it(sealed, storage, monkeypatch):
    store, _, memory = storage
    store.root.mkdir()

    def lost(path):
        memory.folders.add(path)
        raise ValueError("lost reply")

    monkeypatch.setattr(memory, "create_folder", lost)
    with pytest.raises(ValueError, match="lost reply"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    saved = json.loads((store.root / "portable-transfers" / ("1" * 32 + ".json")).read_text())
    assert saved["state"] == "folder_uncertain"
    with pytest.raises(ValueError, match="uncertain"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    assert not memory.uploads


@pytest.mark.parametrize(
    "endpoint",
    ["http://storage.example/x", "https://evil.example/x", "https://user:secret@storage.example/x"],
)
def test_unapproved_transfer_origin_refused(storage, endpoint):
    with pytest.raises(ValueError):
        cloud._endpoint(storage[1], endpoint)


def test_revision_change_between_objects_blocks_even_identical_reapproval(
    sealed, storage, monkeypatch
):
    store, target, memory = storage
    store.root.mkdir()
    revision = ["a" * 64]
    monkeypatch.setattr(cloud, "_load_target", lambda _: (dict(target), None, None, revision[0]))
    uploaded = cloud.native.upload

    def revoke_and_reapprove(*args, **kwargs):
        uploaded(*args, **kwargs)
        revision[0] = "b" * 64

    monkeypatch.setattr(cloud.native, "upload", revoke_and_reapprove)
    with pytest.raises(cloud.StaleBackupStorage, match="No further requests"):
        cloud.upload_package(
            store,
            None,
            sealed[0],
            expected_commit_sha256=sealed[1],
            expected_target=dict(target),
            expected_revision="a" * 64,
        )
    assert len(memory.uploads) == 1
    assert not any(path.endswith("COMMITTED.json") for path in memory.uploads)


def test_revoking_approval_between_objects_blocks_commit(sealed, storage, monkeypatch):
    store, target, memory = storage
    store.root.mkdir()
    uploaded = cloud.native.upload

    def revoke(*args, **kwargs):
        uploaded(*args, **kwargs)
        target["whole_library_approved"] = False

    monkeypatch.setattr(cloud.native, "upload", revoke)
    with pytest.raises(ValueError, match="revoked"):
        cloud.upload_package(store, None, sealed[0], expected_commit_sha256=sealed[1])
    assert len(memory.uploads) == 1
    assert not any(path.endswith("COMMITTED.json") for path in memory.uploads)


def test_changed_destination_cannot_retarget_frozen_transfer(sealed, storage):
    store, target, memory = storage
    store.root.mkdir()
    cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    before = list(memory.uploads)
    target["parent"] = "/another/drive"
    target["fingerprint"] = "e" * 64
    with pytest.raises(ValueError, match="another package or destination"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    assert memory.uploads == before


def test_lost_commit_reply_is_settled_by_reading_same_marker(sealed, storage, monkeypatch):
    store, _, memory = storage
    store.root.mkdir()
    uploaded = cloud.native.upload
    lost = False

    def interrupt(client, server, path, remote, *args, **kwargs):
        nonlocal lost
        uploaded(client, server, path, remote, *args, **kwargs)
        if remote.endswith("COMMITTED.json") and not lost:
            lost = True
            raise ValueError("lost marker reply")

    monkeypatch.setattr(cloud.native, "upload", interrupt)
    with pytest.raises(ValueError, match="lost marker"):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    result = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    assert result["state"] == "complete"
    assert sum(path.endswith("COMMITTED.json") for path in memory.uploads) == 1


def test_uncertain_empty_folder_needs_fresh_confirmation(sealed, storage, monkeypatch):
    store, _, memory = storage
    store.root.mkdir()

    def lost(path):
        memory.folders.add(path)
        raise ValueError("lost reply")

    monkeypatch.setattr(memory, "create_folder", lost)
    with pytest.raises(ValueError):
        cloud.upload_package(
            store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
        )
    shown = cloud.folder_preview(store, None, "1" * 32)
    assert shown["observed"] == "empty_folder"
    with pytest.raises(ValueError, match="Confirm"):
        cloud.reconcile_folder(
            store, None, "1" * 32, fingerprint=shown["fingerprint"], confirmed=False
        )
    cloud.reconcile_folder(store, None, "1" * 32, fingerprint=shown["fingerprint"], confirmed=True)
    monkeypatch.setattr(memory, "create_folder", Memory.create_folder.__get__(memory))
    result = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    assert result["state"] == "complete"


@pytest.mark.parametrize(
    "status,body", [(200, b"wrong"), (302, b"redirect"), (200, b"too many bytes")]
)
def test_streaming_download_rejects_bad_hash_redirect_and_extra_bytes(
    monkeypatch, storage, status, body
):
    _, target, _ = storage
    original_client = httpx.Client
    seen = []

    def handle(request):
        seen.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(
            status, stream=httpx.ByteStream(body), headers={"Location": "https://evil.example/"}
        )

    def separate(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(cloud.httpx, "Client", separate)
    control = SimpleNamespace(
        request=lambda *args, **kwargs: SimpleNamespace(
            json=lambda: {"responses": [{"endpoint": "https://storage.example/token"}]}
        )
    )
    with pytest.raises(ValueError):
        REAL_DOWNLOAD(
            control,
            target,
            "/drive/owned/object",
            size=5,
            digest=hashlib.sha256(b"right").hexdigest(),
        )
    assert len(seen) == 1


def retention_pair(sealed, storage, monkeypatch):
    import shutil

    from cbs_transcriber import portable_retention

    store, target, memory = storage
    store.root.mkdir()
    first = cloud.upload_package(
        store, None, sealed[0], expected_commit_sha256=sealed[1], operation_id="1" * 32
    )
    second_root = sealed[0].with_name("second-sealed")
    shutil.copytree(sealed[0], second_root)
    manifest = dict(sealed[2], backup_id="c" * 32)
    raw = canonical(manifest)
    (second_root / "manifest.json").write_bytes(raw)
    marker = canonical(
        {
            "format": "cbs-library-backup-commit-v2",
            "backup_id": "c" * 32,
            "manifest_size": len(raw),
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    (second_root / "COMMITTED.json").write_bytes(marker)
    second = cloud.upload_package(
        store,
        None,
        second_root,
        expected_commit_sha256=hashlib.sha256(marker).hexdigest(),
        operation_id="2" * 32,
    )
    for record, date in (
        (first, "2020-01-01T00:00:00+00:00"),
        (second, "2021-01-01T00:00:00+00:00"),
    ):
        record["completed_at"] = date
        cloud._journal(
            store.root / "portable-transfers" / (record["operation_id"] + ".json"), record
        )
    portable_retention.save_policy(store, {"keep_last": 1, "minimum_age_days": 30, "holds": []})
    moved = []

    def trash(client, folder):
        moved.append(folder)
        client.folders = {
            item for item in client.folders if item != folder and not item.startswith(folder + "/")
        }
        client.files = {
            name: body for name, body in client.files.items() if not name.startswith(folder + "/")
        }

    monkeypatch.setattr(cloud.native, "trash_owned_folder", trash)
    return portable_retention, first, second, moved


def test_retention_requires_exact_preview_keeps_recent_and_never_deletes_local(
    sealed, storage, monkeypatch
):
    retention, first, second, moved = retention_pair(sealed, storage, monkeypatch)
    store = storage[0]
    shown = retention.preview(store, None)
    assert shown["eligible_operations"] == [first["operation_id"]]
    with pytest.raises(ValueError, match="confirmation"):
        retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=False)
    assert not moved
    result = retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=True)
    assert moved == [first["remote_folder"]]
    assert result["permanent_erasure"] is False
    assert (sealed[0] / "COMMITTED.json").is_file()
    assert second["remote_folder"] in storage[2].folders
    assert (store.root / "portable-retention-events" / result["event_id"] / "intent.json").is_file()


def test_retention_hold_invalidates_preview_before_any_remote_action(sealed, storage, monkeypatch):
    retention, first, _, moved = retention_pair(sealed, storage, monkeypatch)
    store = storage[0]
    shown = retention.preview(store, None)
    retention.save_policy(
        store, {"keep_last": 1, "minimum_age_days": 30, "holds": [first["backup_id"]]}
    )
    with pytest.raises(ValueError, match="preview changed"):
        retention.apply(store, None, fingerprint=shown["fingerprint"], confirmed=True)
    assert not moved


def test_retention_requires_available_independent_package(sealed, storage, monkeypatch):
    retention, first, _, moved = retention_pair(sealed, storage, monkeypatch)
    (sealed[0] / "COMMITTED.json").unlink()
    shown = retention.preview(storage[0], None)
    assert not shown["eligible_operations"]
    assert any(row["reason"] == "independent_local_copy_unavailable" for row in shown["rows"])
    assert not moved


@pytest.mark.parametrize("correct_reference", [True, False])
def test_streamed_remote_package_download_uses_external_reference(
    sealed, storage, monkeypatch, tmp_path, correct_reference
):
    from urllib.parse import parse_qs, quote

    store, target, memory = storage
    store.root.mkdir()
    uploaded = cloud.upload_package(store, None, sealed[0], expected_commit_sha256=sealed[1])
    original_client = httpx.Client

    def handler(request):
        assert "authorization" not in request.headers
        remote = parse_qs(request.url.query.decode())["name"][0]
        return httpx.Response(200, stream=httpx.ByteStream(memory.files[remote]))

    def control(*args, **kwargs):
        remote = kwargs["json"]["items"][0]["id"]
        return SimpleNamespace(
            json=lambda: {
                "responses": [{"endpoint": "https://storage.example/file?name=" + quote(remote)}]
            }
        )

    monkeypatch.setattr(memory, "request", control, raising=False)
    monkeypatch.setattr(cloud, "_download", REAL_DOWNLOAD)
    monkeypatch.setattr(
        cloud.httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    destination = tmp_path / "downloaded"
    if correct_reference:
        result = cloud.download_package(
            memory,
            target,
            uploaded["remote_folder"],
            expected_commit_sha256=sealed[1],
            destination=destination,
        )
        assert result["commit_sha256"] == sealed[1]
        assert (destination / "manifest.json").read_bytes() == (
            sealed[0] / "manifest.json"
        ).read_bytes()
    else:
        with pytest.raises(ValueError, match="checksum"):
            cloud.download_package(
                memory,
                target,
                uploaded["remote_folder"],
                expected_commit_sha256="b" * 64,
                destination=destination,
            )
        assert not destination.exists()
