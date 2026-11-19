"""Settings → UCloud native Transcriber limits (ADR 0183, V230): synthetic, no network."""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_local_concurrency import patch_process, recordings, wait_until
from test_native_batch import batch_remote as batch_remote
from test_native_batch import frozen_native_jobs
from test_native_batch import selection as selection
from test_native_batch_transfers import four_recordings, overlap, process
from test_ucloud_native import remote as remote

from cbs_transcriber import local_concurrency, native_batch
from cbs_transcriber.app import create_app
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store


def test_v230_defaults_partial_saves_and_restart(tmp_path, credentials):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        status = client.get("/api/native-batches/settings").json()
        assert status == {
            "uploads": 4,
            "batches": 2,
            "defaults": {"uploads": 4, "batches": 2},
            "maximum": {"uploads": 8, "batches": 4},
            "active_batches": 2,
        }
        assert client.app.state.queue.lane_limits["transcribe-native"] == 2
        saved = client.put("/api/native-batches/settings", json={"uploads": 7}).json()
        assert (saved["uploads"], saved["batches"]) == (7, 2)
        saved = client.put("/api/native-batches/settings", json={"batches": 3}).json()
        assert (saved["uploads"], saved["batches"], saved["active_batches"]) == (7, 3, 3)
        assert client.app.state.queue.lane_limits["transcribe-native"] == 3
        # The local-processing lanes are a separate row and stay untouched.
        local = client.get("/api/local-concurrency").json()
        assert local["saved"] == dict.fromkeys(local_concurrency.LANES)
    restarted = create_app(tmp_path, start_worker=False, credentials=credentials)
    assert restarted.state.queue.native_batch_limit() == 3
    assert local_concurrency.native_saved(Store(tmp_path)).uploads == 7


@pytest.mark.parametrize(
    "body",
    [
        {"uploads": 0},
        {"uploads": 9},
        {"batches": 0},
        {"batches": 5},
        {"uploads": 2.5},
        {"uploads": "4"},
        {"batches": True},
        {"uploads": None},
        {"threads": 2},
    ],
)
def test_v230_invalid_limits_are_refused_and_change_nothing(client, body):
    before = client.get("/api/native-batches/settings").json()
    response = client.put("/api/native-batches/settings", json=body)
    assert response.status_code == 422
    assert client.get("/api/native-batches/settings").json() == before
    assert client.app.state.queue.native_batch_limit() == before["batches"]


def test_v230_unreadable_saved_limits_fall_back_to_defaults(tmp_path):
    store = Store(tmp_path)
    store.execute(
        "INSERT INTO settings(id,value) VALUES (?,?)",
        (local_concurrency.NATIVE_SETTING, '{"uploads": 99}'),
    )
    assert local_concurrency.native_saved(store) == local_concurrency.NativeLimits()
    assert JobQueue(store, None).native_batch_limit() == 2


def test_v230_batch_limit_gates_new_native_claims_live_without_stopping_work(
    tmp_path, credentials, monkeypatch
):
    store = Store(tmp_path)
    jobs = [
        store.add_job(row["id"], "transcribe", {"settings": {"provider_type": "ucloud"}})["id"]
        for row in recordings(store, 6)
    ]
    local_concurrency.native_save(store, local_concurrency.NativeLimits(batches=1))
    queue = JobQueue(store, credentials)
    release, lock = threading.Event(), threading.Lock()
    running, started_after_lowering, lowered = [0], [], threading.Event()

    def process_native(identifier):
        with lock:
            running[0] += 1
            if lowered.is_set():
                started_after_lowering.append(running[0])
        try:
            assert release.wait(20)
        finally:
            with lock:
                running[0] -= 1

    patch_process(queue, monkeypatch, process_native)
    with store.library_lock():
        queue.start()
        try:
            assert wait_until(lambda: running[0] == 1)
            time.sleep(0.6)  # Idle native workers keep polling but never exceed the limit.
            assert running[0] == 1
            queue.set_native_batch_limit(4)
            assert wait_until(lambda: running[0] == 4)
            lowered.set()
            queue.set_native_batch_limit(1)
            time.sleep(0.6)
            # Lowering never cancels or interrupts an allocation that already started.
            assert running[0] == 4
            assert not any(worker.cancel_event.is_set() for worker in queue.workers)
            release.set()
            assert wait_until(
                lambda: (
                    store.one("SELECT count(*) AS n FROM jobs WHERE state='completed'")["n"] == 6
                )
            )
        finally:
            release.set()
            queue.stop()
    assert started_after_lowering == [1, 1]
    assert {store.job(identifier)["state"] for identifier in jobs} == {"completed"}


def test_v230_a_running_batch_keeps_the_upload_limit_it_started_with(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    provider, rows = selection
    store = client.app.state.store
    batch_remote.store = store
    four_recordings(store, rows, wav_bytes, count=6)
    assert client.put("/api/native-batches/settings", json={"uploads": 2}).status_code == 200
    jobs = frozen_native_jobs(client, provider, rows)
    changed = []

    def raise_limit():
        # A Settings change during the uploads applies to the next batch only.
        response = client.put("/api/native-batches/settings", json={"uploads": 8})
        changed.append(response.status_code)

    upload, uploads = overlap(native_batch.native.upload, count=2, fail=raise_limit)
    monkeypatch.setattr(native_batch.native, "upload", upload)
    process(client, jobs)
    assert changed == [200]
    assert uploads["peak"] == 2 and uploads["calls"] == 6
    assert len(batch_remote.submissions) == 1
    assert all(len(store.results(row["id"])) == 1 for row in rows)
    assert local_concurrency.native_saved(store).uploads == 8


def test_v230_upload_limit_bounds_one_batch_up_to_eight(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    provider, rows = selection
    store = client.app.state.store
    batch_remote.store = store
    four_recordings(store, rows, wav_bytes, count=8)
    assert client.put("/api/native-batches/settings", json={"uploads": 8}).status_code == 200
    jobs = frozen_native_jobs(client, provider, rows)
    upload, uploads = overlap(native_batch.native.upload, count=8)
    download, downloads = overlap(native_batch._download, count=4)
    monkeypatch.setattr(native_batch.native, "upload", upload)
    monkeypatch.setattr(native_batch, "_download", download)
    process(client, jobs)
    # Downloads keep their fixed four-transfer window (bounded transcript buffers).
    assert uploads["peak"] == 8 and downloads["peak"] == 4
    assert all(len(store.results(row["id"])) == 1 for row in rows)
