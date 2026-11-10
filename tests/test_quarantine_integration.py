"""Actual schema14→15 and newly integrated writer/deletion quarantine seams."""

import sqlite3
from types import SimpleNamespace

import pytest

from cbs_transcriber import (
    local_asr_capture,
    local_timing,
    repair_admission,
    restored_owners,
    runtime_batches,
    timing_capture,
)
from cbs_transcriber.store import Store, new_id


def archive(store, kind, identifier):
    store.execute(
        "INSERT INTO restored_owners VALUES (?,?,?,?)",
        (kind, identifier, new_id(), new_id()),
    )


def test_schema14_migration_keeps_existing_values_and_backs_up_before_schema15(
    tmp_path, monkeypatch
):
    from cbs_transcriber import store as module

    monkeypatch.setattr(module, "SCHEMA_VERSION", 14)
    previous = Store(tmp_path)
    project = previous.create_project("Migration preservation")
    previous.create_recording(project["id"], "literal.æ.wav")
    tables = [
        row["name"]
        for row in previous.all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    rows = {name: previous.all(f'SELECT * FROM "{name}" ORDER BY rowid') for name in tables}
    monkeypatch.setattr(module, "SCHEMA_VERSION", 15)
    current = Store(tmp_path)
    assert current.one("PRAGMA user_version")["user_version"] == 15
    assert {name: current.all(f'SELECT * FROM "{name}" ORDER BY rowid') for name in tables} == rows
    assert current.all("SELECT * FROM restored_owners") == []
    backups = list((tmp_path / "backups").glob("schema-14-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute("PRAGMA user_version").fetchone()[0] == 14
        assert saved.execute("SELECT name FROM projects").fetchone()[0] == project["name"]
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-14-*.sqlite3"))) == 1


def test_new_dedicated_writers_refuse_archival_jobs_before_parsing_or_file_reads(client):
    store = client.app.state.store
    project = store.create_project("Archival writer exclusion")
    recording = store.create_recording(project["id"], "source.wav")
    job = store.add_job(recording["id"], "enhance")
    archive(store, "job", job["id"])
    before = store.all("SELECT * FROM jobs")
    coordinator = object.__new__(runtime_batches.Coordinator)
    coordinator.store, coordinator.id, coordinator.epoch = store, new_id(), 1
    actions = [
        lambda: store.request_job_cancellation(job["id"]),
        lambda: store.mark_job_termination_requested(job["id"], "unknown"),
        lambda: local_asr_capture._publish(store, {"id": job["id"]}, {}),
        lambda: timing_capture._publish(store, {"id": job["id"]}, {}),
        lambda: local_timing.apply(store, recording["id"], new_id(), job["id"], 0),
        lambda: repair_admission.prepare(SimpleNamespace(store=store), {"id": job["id"]}, {}),
        lambda: coordinator.save_preparation({}, {"job_id": job["id"]}, {}),
    ]
    for action in actions:
        with pytest.raises(restored_owners.HistoricalOwner):
            action()
        assert store.all("SELECT * FROM jobs") == before
    missing = new_id()
    archive(store, "job", missing)
    with pytest.raises(restored_owners.HistoricalOwner):
        store.request_job_cancellation(missing)


def test_new_preparation_writer_refuses_archival_allocation_with_missing_members(client):
    store = client.app.state.store
    owner = new_id()
    archive(store, "runtime_batch", owner)
    coordinator = object.__new__(runtime_batches.Coordinator)
    coordinator.store, coordinator.id, coordinator.epoch = store, owner, 1
    with pytest.raises(restored_owners.HistoricalOwner):
        coordinator.save_preparation({}, {"job_id": new_id()}, {})


def test_delete_ignores_archival_running_or_malformed_jobs_without_remote_controls(client):
    store = client.app.state.store
    project = store.create_project("Delete archival rows")
    recording = store.create_recording(project["id"], "source.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "original.wav").write_bytes(b"Synthetic managed bytes")
    job = store.add_job(recording["id"], "enhance")
    store.update_job(job["id"], state="running")
    store.execute("UPDATE jobs SET params='not JSON' WHERE id=?", (job["id"],))
    archive(store, "job", job["id"])
    response = client.delete(f"/api/recordings/{recording['id']}")
    assert response.status_code == 200, response.text
    assert not directory.exists()
    assert store.all("SELECT * FROM jobs WHERE id=?", (job["id"],)) == []
    assert store.all("SELECT * FROM restored_owners WHERE owner_id=?", (job["id"],))
