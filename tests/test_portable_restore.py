"""Synthetic isolated restores; no real library, credentials, transfer or model runtime."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from cbs_transcriber import language_preferences
from cbs_transcriber import portable_capture as capture
from cbs_transcriber import portable_restore as restore
from cbs_transcriber import store as store_module
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store


@pytest.fixture
def package(client, wav_bytes, tmp_path, monkeypatch):
    source = client.app.state.store
    project = source.create_project("Synthetic portable round trip", default_language="da")
    recording = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("tiny.wav", wav_bytes, "audio/wav")},
    ).json()["recording"]
    language_preferences.save(source, "recording", recording["id"], "en", 0)
    source.execute("UPDATE jobs SET state='done'")
    source.execute("UPDATE recordings SET status='ready'")
    with source.library_lock():
        saved = capture.capture_library(
            source, client.app.state.queue, tmp_path / "packages", gate=capture.CaptureGate()
        )
    # Restores target the current schema, independently of the captured source schema.
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 19)
    return saved, source, recording


def destination(tmp_path, name):
    # Full UUID restore layout is intentionally longer than the old eight-character project
    # folder. Keep synthetic destinations inside native Win32 limits as a real user must.
    parent = tmp_path.parent / ("d-" + hashlib.sha256(tmp_path.name.encode()).hexdigest()[:6])
    parent.mkdir(exist_ok=True)
    return parent / name


def apply(package, target, **kwargs):
    saved = package[0]
    return restore.restore_library(
        saved["folder"],
        expected_commit_sha256=saved["commit_sha256"],
        target=target,
        live_root=package[1].root,
        **kwargs,
    )


def test_complete_new_library_preserves_original_and_archives_jobs(package, tmp_path, wav_bytes):
    registered = []
    target = destination(tmp_path, "new-library")
    result = apply(package, target, register=registered.append)
    assert result["state"] == "complete" and registered == [target]
    new = Store(target)
    assert (new.directory(package[2]["id"]) / "original.wav").read_bytes() == wav_bytes
    assert new.recording(package[2]["id"])["status"] == "preparing"
    with new.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute("SELECT default_language FROM projects").fetchone()[0] == "da"
        assert db.execute("SELECT language_override FROM recordings").fetchone()[0] == "en"
        assert (
            db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            == db.execute("SELECT COUNT(*) FROM restored_owners WHERE kind='job'").fetchone()[0]
        )
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state='done'").fetchone()[0] == 1
    assert JobQueue(new, None).store.claim_job("media") is None
    source = target / "preservation" / result["operation_id"] / "source.sqlite3"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == result["source_database_sha256"]


@pytest.mark.parametrize("old_version", [13, 14, 15, 16])
def test_old_schema_restore_initializes_language_inheritance_without_changing_source(
    tmp_path, monkeypatch, wav_bytes, old_version
):
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", old_version)
    source = Store(tmp_path / "old-schema")
    project = source.create_project("Before language preferences")
    recording_id = store_module.new_id()
    with source.connection() as db:
        source._create_recording(db, project["id"], "old.wav", recording_id)
    directory = source.directory(recording_id)
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    source.execute("UPDATE recordings SET status='ready' WHERE id=?", (recording_id,))
    with source.library_lock():
        saved = capture.capture_library(
            source, JobQueue(source, None), tmp_path / "old-packages", gate=capture.CaptureGate()
        )
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 19)
    target = destination(tmp_path, "old-language-restore")
    restore.restore_library(
        saved["folder"], expected_commit_sha256=saved["commit_sha256"], target=target
    )
    restored = Store(target).one("SELECT * FROM projects WHERE id=?", (project["id"],))
    assert restored["default_language"] is None
    assert restored["language_revision"] == 0
    restored_recording = Store(target).recording(recording_id)
    assert restored_recording["language_override"] is None
    assert restored_recording["language_revision"] == 0
    with sqlite3.connect(source.db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == old_version


def test_existing_target_is_never_overwritten(package, tmp_path):
    target = destination(tmp_path, "existing")
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("unrelated user data")
    with pytest.raises(ValueError, match="already exists"):
        apply(package, target)
    assert sentinel.read_text() == "unrelated user data"


def test_changed_object_fails_without_target_or_registration(package, tmp_path):
    root = Path(package[0]["folder"])
    next((root / "objects").iterdir()).write_bytes(b"corrupt synthetic bytes")
    target = destination(tmp_path, "refused")
    registered = []
    with pytest.raises(ValueError):
        apply(package, target, register=registered.append)
    assert not target.exists() and not registered


def test_lost_registration_reply_retains_and_recovers_complete_target(package, tmp_path):
    target = destination(tmp_path, "registered-later")

    def fail(_):
        raise OSError("synthetic registry refusal")

    result = apply(package, target, operation_id="1" * 32, register=fail)
    assert result["registration_error"] and target.is_dir()
    assert not (target / "restore-pending").exists()
    seen = []
    resumed = apply(package, target, operation_id="1" * 32, register=seen.append)
    assert resumed["registered"] and seen == [target]


def test_interrupted_promotion_is_closed_to_store_and_resumes(package, tmp_path, monkeypatch):
    target = destination(tmp_path, "resume-promotion")
    rename = Path.rename
    failed = False

    def interrupt(path, destination):
        nonlocal failed
        if path.name == "preservation" and path.parent.name == "library" and not failed:
            failed = True
            raise OSError("synthetic process interruption")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", interrupt)
    with pytest.raises(OSError, match="synthetic"):
        apply(package, target, operation_id="2" * 32)
    assert (target / "restore-pending").exists()
    with pytest.raises(RuntimeError, match="incomplete"):
        Store(target)
    monkeypatch.setattr(Path, "rename", rename)
    resumed = apply(package, target, operation_id="2" * 32)
    assert resumed["state"] == "complete" and not (target / "restore-pending").exists()


def test_empty_restored_library_can_be_captured_again(client, tmp_path, monkeypatch):
    source = client.app.state.store
    with source.library_lock():
        saved = capture.capture_library(
            source, client.app.state.queue, tmp_path / "empty-packages", gate=capture.CaptureGate()
        )
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 19)
    target = destination(tmp_path, "empty-restored")
    restore.restore_library(
        saved["folder"], expected_commit_sha256=saved["commit_sha256"], target=target
    )
    restored = Store(target)
    with restored.library_lock():
        second = capture.capture_library(
            restored, JobQueue(restored, None), gate=capture.CaptureGate()
        )
    assert second["originals"] == 0
    assert second["files"] == 4  # immutable source DB and its three bound control files


def test_operation_binding_refuses_other_destination(package, tmp_path):
    apply(package, destination(tmp_path, "first"), operation_id="3" * 32)
    with pytest.raises(ValueError, match="another package or destination"):
        apply(package, destination(tmp_path, "second"), operation_id="3" * 32)


def test_no_parent_sqlite_open_for_incoming_package(package, tmp_path, monkeypatch):
    # All database opens during restore must occur in the captured child. The test fixture
    # created its trusted source before this fault is installed.
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("parent SQLite open")),
    )
    result = apply(package, destination(tmp_path, "contained-only"))
    assert result["state"] == "complete"
