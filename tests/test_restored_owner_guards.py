"""Restored queues retain exact evidence while every operational seam refuses ownership."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from cbs_transcriber import (
    enhancement_api,
    input_admission,
    native_batch,
    original_inspection,
    remote_jobs,
    repair_cloud,
    restored_owners,
    runtime_batches,
    runtime_capacity,
    runtime_discard,
    runtime_store,
    ucloud_native,
)
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store, new_id, now


class NoCredentials:
    def get(self, *args, **kwargs):
        pytest.fail("A restored owner reached credentials or network setup.")


def mark(store, kind, identifier):
    store.execute(
        "INSERT OR IGNORE INTO restored_owners VALUES (?,?,?,?)",
        (kind, identifier, "synthetic-restore", "synthetic-backup"),
    )


@pytest.fixture
def restored(tmp_path):
    store = Store(tmp_path / "synthetic")
    if not store.all("SELECT 1 FROM sqlite_master WHERE name='restored_owners'"):
        store.execute(restored_owners.DDL)
    project = store.create_project("Synthetic restored history")
    recording = store.create_recording(project["id"], "source.wav")
    jobs = []
    for state in ("queued", "running", "completed", "failed", "interrupted", "cancelled"):
        job = store.add_job(recording["id"], "transcribe", {"private_frozen": state})
        store.update_job(job["id"], state=state)
        jobs.append(store.job(job["id"]))
        mark(store, "job", job["id"])
    native, runtime, missing = new_id(), new_id(), new_id()
    store.execute(
        "INSERT INTO native_batch_runs(id,project_id,params,members,created,updated) "
        "VALUES (?,?,?,?,?,?)",
        (
            native,
            project["id"],
            json.dumps({"ucloud_run": {"submission_intent": True}}),
            json.dumps([{"job_id": missing, "recording_id": recording["id"]}]),
            now(),
            now(),
        ),
    )
    identity = runtime_store.canonical({"synthetic": True})
    store.execute(
        "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,journal,"
        "created,updated) VALUES (?,?,?,?,?,?,?)",
        (
            runtime,
            project["id"],
            hashlib.sha256(identity.encode()).hexdigest(),
            identity,
            json.dumps({"capacity_reserved": True, "job_id": "old-remote"}),
            now(),
            now(),
        ),
    )
    for kind, identifier in (
        ("native_batch", native),
        ("runtime_batch", runtime),
        ("job", missing),
    ):
        mark(store, kind, identifier)
    queue = JobQueue(store, NoCredentials(), kind="transcribe")
    return SimpleNamespace(
        store=store,
        project=project,
        recording=recording,
        jobs=jobs,
        native=native,
        runtime=runtime,
        missing=missing,
        queue=queue,
    )


def observed(store):
    with store.connection() as db:
        return {
            table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in (
                "jobs",
                "native_batch_runs",
                "runtime_batch_runs",
                "runtime_batch_members",
                "runtime_batch_attempts",
                "transcript_results",
                "result_versions",
            )
        }


def test_claim_recovery_capacity_and_obligations_ignore_archival_evidence(restored):
    store = restored.store
    # Archival raw evidence remains untouched, even when old parameters are malformed.
    store.execute("UPDATE jobs SET kind='enhance',params='{' WHERE id=?", (restored.jobs[0]["id"],))
    before = observed(store)
    assert store.claim_job("transcribe") is None
    assert runtime_store.claim(store) is None
    assert runtime_store.obligations(store) == []
    assert runtime_capacity.refresh_blocker(store, NoCredentials()) is None
    assert input_admission.obligations(store) == []
    assert not restored.queue.busy(restored.recording["id"])
    with store.connection() as db:
        assert runtime_store._capacity(db) == 0
    store.recover_jobs()
    assert observed(store) == before
    fresh = store.add_job(restored.recording["id"], "transcribe", {"fresh": True})
    assert store.claim_job("transcribe")["id"] == fresh["id"]


@pytest.mark.parametrize("index", range(6))
def test_store_and_queue_cannot_mutate_or_dispatch_any_imported_terminal_state(restored, index):
    store, job = restored.store, restored.jobs[index]
    before = observed(store)
    operations = [
        lambda: store.update_job(job["id"], state="queued"),
        lambda: store.record_audio_preparation(job["id"], {}),
        lambda: store.add_result(restored.recording["id"], [], {}, "Forged", {}, job_id=job["id"]),
        lambda: store.apply_repair_timing(restored.recording["id"], "result", job["id"], 0, "hash"),
        lambda: restored.queue.process(job["id"]),
        lambda: restored.queue.cancel(job["id"]),
    ]
    for operation in operations:
        with pytest.raises(restored_owners.HistoricalOwner):
            operation()
    assert observed(store) == before


def test_archival_markers_survive_missing_rows_and_block_reused_job_ids(restored):
    store = restored.store
    with store.connection() as db, pytest.raises(restored_owners.HistoricalOwner):
        store._insert_job(db, restored.recording["id"], "transcribe", {}, restored.missing)
    with pytest.raises(restored_owners.HistoricalOwner):
        store.update_job(restored.missing, state="queued")
    with pytest.raises(restored_owners.HistoricalOwner):
        store.claim_native_batch(restored.native)
    with pytest.raises(restored_owners.HistoricalOwner):
        store.update_native_batch(restored.native, state="queued")
    with pytest.raises(restored_owners.HistoricalOwner):
        store.finish_native_batch(restored.native, "completed", "Forged")
    with store.connection() as db, pytest.raises(restored_owners.HistoricalOwner):
        store._insert_job(
            db,
            restored.recording["id"],
            "transcribe",
            {"native_batch_id": restored.native},
            new_id(),
        )


def test_all_remote_owner_entrypoints_refuse_before_credentials_or_clients(restored, monkeypatch):
    store, queue, credentials = restored.store, restored.queue, NoCredentials()
    job = restored.jobs[2]  # Even a completed job must not gain cleanup authority.
    for module, attribute in ((ucloud_native, "client_for"), (enhancement_api, "remote_state")):
        monkeypatch.setattr(module, attribute, lambda *a, **k: pytest.fail("network called"))
    operations = [
        lambda: native_batch.retry_batch(queue, job),
        lambda: native_batch.cancel_batch(queue, job),
        lambda: native_batch.cleanup_batch(store, credentials, restored.native),
        lambda: native_batch.run_batch(queue, job),
        lambda: ucloud_native.cleanup_owned(store, credentials, job),
        lambda: ucloud_native.run_transcription(queue, job, {}, None, "no-key"),
        lambda: remote_jobs.reconcile(store, credentials, job, "remote", False),
        lambda: enhancement_api.retry_cleanup(store, credentials, job),
        lambda: enhancement_api.settle(store, credentials, job),
        lambda: repair_cloud.stop_owned(store, credentials, job),
        lambda: repair_cloud.retry(store, credentials, job),
        lambda: repair_cloud.reconcile(store, credentials, job, "remote", False),
        lambda: repair_cloud.retry_cleanup(store, credentials, job),
        lambda: runtime_capacity.refresh(store, credentials, job),
        lambda: runtime_batches.cancel(store, credentials, restored.runtime),
        lambda: runtime_batches.retry(store, credentials, restored.runtime),
        lambda: runtime_batches.reconcile(store, credentials, restored.runtime),
        lambda: runtime_batches.retry_cleanup(store, credentials, restored.runtime),
        lambda: runtime_discard.preview(store, credentials, restored.runtime),
        lambda: runtime_discard.confirm(
            store, credentials, restored.runtime, fingerprint="hash", confirmed=True
        ),
    ]
    before = observed(store)
    for operation in operations:
        with pytest.raises(restored_owners.HistoricalOwner):
            operation()
    assert observed(store) == before
    assert not remote_jobs.unresolved_for(store, job)


def test_runtime_direct_writers_refuse_even_with_no_member_rows(restored):
    store, bid, job = restored.store, restored.runtime, restored.missing
    operations = [
        lambda: runtime_store.stop(store, bid),
        lambda: runtime_store.claim_member(store, bid, job, 0),
        lambda: runtime_store.checkpoint(store, bid, 0, state="running"),
        lambda: runtime_store.seal(store, bid, 0, 0),
        lambda: runtime_store.reserve_capacity(store, bid, epoch=0),
        lambda: runtime_store.release_capacity(store, bid, epoch=0),
        lambda: runtime_store.queue_result_recovery(store, bid, job, 0, task_id="task"),
        lambda: runtime_store.retry_member(store, bid, job, 0, task_id="task"),
        lambda: runtime_store.reconnect(store, bid, 0, remote_status={}),
        lambda: runtime_store.queue_recovery(store, bid, 0, remote_status={}),
        lambda: runtime_store.interrupt(store, bid, 0, "Forged"),
        lambda: runtime_store.confirm_discard(store, bid, "hash", {}, "fingerprint"),
    ]
    before = observed(store)
    for operation in operations:
        with pytest.raises(restored_owners.HistoricalOwner):
            operation()
    assert observed(store) == before


def test_inspector_successor_is_fresh_but_old_job_remains_exact(restored):
    store, rid = restored.store, restored.recording["id"]
    with store.library_lock():
        cache = original_inspection.ensure(store, rid)
        old = store.job(cache["inspection_job_id"])
        mark(store, "job", old["id"])
        # The isolated restore's allowlisted operational overlay clears this cache only.
        store.execute("UPDATE recordings SET original_input=NULL WHERE id=?", (rid,))
        assert input_admission.source_readiness(store, rid)["state"] == "uninspected"
        assert original_inspection.claim(store, old["id"]) is None
        with pytest.raises(restored_owners.HistoricalOwner):
            original_inspection.cancel(store, old["id"], generation=cache["generation"], epoch=1)
        fresh = original_inspection.ensure(store, rid)
        assert fresh["generation"] != cache["generation"]
        assert fresh["inspection_job_id"] != old["id"]
        assert fresh["state"] == "pending" and fresh["receipt"] is None
        assert store.job(old["id"]) == old


@pytest.mark.parametrize(
    "kind", ["transcribe", "runtime-batch", "source-inspect", "input-admission"]
)
def test_capture_pause_blocks_every_background_claim_and_releases_on_failure(
    restored, kind, monkeypatch
):
    queue = JobQueue(restored.store, NoCredentials(), kind=kind)

    class Wakeup:
        def wait(self, seconds):
            queue.stopping.set()

        def clear(self):
            pass

        def set(self):
            pass

    queue.wakeup = Wakeup()
    monkeypatch.setattr(restored.store, "claim_job", lambda *a: pytest.fail("claim during capture"))
    monkeypatch.setattr(
        runtime_store, "claim", lambda *a: pytest.fail("runtime claim during capture")
    )
    monkeypatch.setattr(
        original_inspection, "claim", lambda *a: pytest.fail("source claim during capture")
    )
    monkeypatch.setattr(
        input_admission, "claim", lambda *a: pytest.fail("admission during capture")
    )
    monkeypatch.setattr(
        runtime_capacity, "refresh_blocker", lambda *a: pytest.fail("remote read during capture")
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        with queue.pause_admission():
            assert queue.capture_paused
            queue.run()
            raise RuntimeError("synthetic capture failure")
    assert not queue.capture_paused
    queue.active_ids = ["active-owner"]
    with pytest.raises(ValueError, match="active work"):
        with queue.pause_admission():
            pytest.fail("entered capture while an owner was active")


def test_schema15_missing_quarantine_fails_closed_but_pre15_is_compatible(tmp_path, monkeypatch):
    with monkeypatch.context() as previous:
        previous.setattr("cbs_transcriber.store.SCHEMA_VERSION", 14)
        store = Store(tmp_path)
    with store.connection() as db:
        assert not restored_owners.is_restored(db, "job", "old")
        assert restored_owners.sql_exclusion(db, "job", "jobs.id") == "1"
        db.execute("PRAGMA user_version=15")
        with pytest.raises(RuntimeError, match="quarantine"):
            restored_owners.is_restored(db, "job", "old")
        with pytest.raises(RuntimeError, match="quarantine"):
            restored_owners.sql_exclusion(db, "job", "jobs.id")
        db.execute("PRAGMA user_version=14")


def test_activity_is_read_only_and_public_controls_refuse_before_network(client, monkeypatch):
    store = client.app.state.store
    if not store.all("SELECT 1 FROM sqlite_master WHERE name='restored_owners'"):
        store.execute(restored_owners.DDL)
    project = store.create_project("Imported project")
    recording = store.create_recording(project["id"], "source.wav")
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"ucloud_run": {"job_id": "source-allocation", "cleanup_state": "pending"}},
    )
    store.update_job(job["id"], state="running")
    mark(store, "job", job["id"])
    monkeypatch.setattr(ucloud_native, "client_for", lambda *a, **k: pytest.fail("network called"))
    before = observed(store)
    activity = client.get("/api/jobs")
    assert activity.status_code == 200
    row = next(row for row in activity.json() if row["id"] == job["id"])
    assert row["state"] == "historical" and row["archived_state"] == "running"
    assert row["restored_history"] and row["ucloud_cleanup_state"] == "historical"
    for path, body in (
        ("cancel", None),
        ("retry", {}),
        ("cleanup-ucloud", None),
        ("reconcile", {"remote_id": "source-allocation", "not_created": False}),
    ):
        response = client.post(f"/api/jobs/{job['id']}/{path}", json=body)
        assert response.status_code == 400, response.text
        assert "historical" in response.text
    assert observed(store) == before
