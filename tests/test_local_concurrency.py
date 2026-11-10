"""Adjustable parallel local tasks (ADR 0146): limits, exactly-once claims, drain and polling."""

import os
import subprocess
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient
from test_input_admission import inspect, item
from test_input_admission import library as library

from cbs_transcriber import ffmpeg_runtime, input_admission, local_concurrency, media
from cbs_transcriber.app import create_app
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store


def recordings(store, count, suffix=".wav"):
    project = store.create_project("Synthetic parallel lanes")
    return [store.create_recording(project["id"], f"file-{i:03d}{suffix}") for i in range(count)]


class Observer:
    """Records which job each worker processed and the peak concurrency per job kind."""

    def __init__(self, store, hold=0.0):
        self.store, self.hold = store, hold
        self.lock = threading.Lock()
        self.processed, self.active, self.peak = [], {}, {}

    def __call__(self, identifier):
        kind = self.store.job(identifier)["kind"]
        params = self.store.job(identifier)["params"]
        if params.get("settings", {}).get("provider_type") == "ucloud":
            kind = "native"
        with self.lock:
            self.processed.append(identifier)
            self.active[kind] = self.active.get(kind, 0) + 1
            self.peak[kind] = max(self.peak.get(kind, 0), self.active[kind])
        time.sleep(self.hold)
        with self.lock:
            self.active[kind] -= 1


def patch_process(queue, monkeypatch, process):
    for worker in queue.workers:
        monkeypatch.setattr(worker, "process", process)


def wait_until(condition, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_defaults_follow_cpu_count_within_bounds():
    # Small machines keep two local workers per lane; provider requests default to six.
    for count in (1, 2, 4):
        assert local_concurrency.defaults(count) == {
            "media": 2,
            "inspection": 2,
            "admission": 2,
            "api": 6,
        }
    assert local_concurrency.defaults(6) == {"media": 4, "inspection": 3, "admission": 2, "api": 6}
    # The 8-thread benchmark laptop: playback preparation leaves two threads for the UI.
    assert local_concurrency.defaults(8) == {"media": 6, "inspection": 4, "admission": 2, "api": 6}
    assert local_concurrency.defaults(64)["media"] == 8
    assert local_concurrency.defaults(64)["inspection"] == 4
    for count in (1, 2, 3, 4, 6, 8, 12, 16, 32, 64, 256):
        values = local_concurrency.defaults(count)
        for lane, (_, maximum) in local_concurrency.LANES.items():
            assert 1 <= values[lane] <= maximum


@pytest.mark.parametrize(
    "body",
    [
        {"media": 0},
        {"media": 9},
        {"inspection": -1},
        {"admission": 5},
        {"api": 9},
        {"media": 2.5},
        {"media": "4"},
        {"media": True},
        {"threads": 4},
    ],
)
def test_invalid_limits_are_refused_and_change_nothing(client, body):
    before = client.get("/api/local-concurrency").json()
    response = client.put("/api/local-concurrency", json=body)
    assert response.status_code == 422
    assert client.get("/api/local-concurrency").json() == before
    assert client.app.state.queue.lane_limit_settings() == before["effective"]


def test_partial_save_preserves_other_lanes_applies_live_and_persists(tmp_path, credentials):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    defaults = local_concurrency.defaults()
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        status = client.get("/api/local-concurrency").json()
        assert status["saved"] == dict.fromkeys(local_concurrency.LANES)
        assert status["effective"] == status["active"] == defaults
        assert status["maximum"] == {"media": 8, "inspection": 8, "admission": 4, "api": 8}
        saved = client.put("/api/local-concurrency", json={"media": 7, "api": 3}).json()
        assert saved["saved"] == {"media": 7, "inspection": None, "admission": None, "api": 3}
        assert saved["active"] == {**defaults, "media": 7, "api": 3}
        assert client.app.state.queue.lane_limits["media"] == 7
        assert client.app.state.queue.lane_limits["transcribe-api"] == 3
        # Omitted lanes are preserved; null restores automatic for that lane only.
        saved = client.put("/api/local-concurrency", json={"inspection": 1, "api": None}).json()
        assert saved["saved"] == {"media": 7, "inspection": 1, "admission": None, "api": None}
        assert saved["active"] == {**defaults, "media": 7, "inspection": 1}
    # A restarted app (and a bare queue on the same library) applies the saved limits.
    restarted = create_app(tmp_path, start_worker=False, credentials=credentials)
    assert restarted.state.queue.lane_limit_settings() == {**defaults, "media": 7, "inspection": 1}
    assert JobQueue(Store(tmp_path), credentials).lane_limits["source-inspect"] == 1


def test_unreadable_saved_value_falls_back_to_automatic(tmp_path):
    store = Store(tmp_path)
    store.execute(
        "INSERT INTO settings(id,value) VALUES (?,?)", (local_concurrency.SETTING, '{"media": 99}')
    )
    assert local_concurrency.saved(store) == local_concurrency.LocalConcurrency()
    assert JobQueue(store, None).lane_limit_settings() == local_concurrency.defaults()


def test_queue_layout_keeps_fixed_lanes_and_starts_each_lane_maximum():
    kinds = [worker.kind for worker in JobQueue(None, None).workers]
    assert kinds[:2] == ["transcribe-api", "transcribe-api"]
    for lane, (kind, maximum) in local_concurrency.LANES.items():
        assert kinds.count(kind) == maximum, lane
    assert kinds.count("transcribe-native") == local_concurrency.NATIVE_WORKERS == 2
    assert kinds.count("enhance") == kinds.count("enhance-remote") == 1
    assert kinds.count("runtime-prepare") == 1 and kinds.count("runtime-batch") == 4
    assert kinds[-1] == "runtime-batch"


def test_api_and_native_lanes_claim_disjoint_jobs_and_legacy_kind_claims_both(tmp_path):
    store = Store(tmp_path)
    api_row, native_row, other_row = recordings(store, 3)
    api = store.add_job(api_row["id"], "transcribe", {"settings": {"provider_type": "openai"}})
    native = store.add_job(
        native_row["id"], "transcribe", {"settings": {"provider_type": "ucloud"}}
    )
    untyped = store.add_job(other_row["id"], "transcribe", {})
    assert store.claim_job("transcribe-native")["id"] == native["id"]
    assert store.claim_job("transcribe-native") is None
    assert store.claim_job("transcribe-api")["id"] == api["id"]
    assert store.claim_job("transcribe-api")["id"] == untyped["id"]
    assert store.claim_job("transcribe-api") is None
    store.execute("UPDATE jobs SET state='queued'")
    assert {store.claim_job("transcribe")["id"] for _ in range(3)} == {
        api["id"],
        native["id"],
        untyped["id"],
    }


def test_many_workers_process_every_job_exactly_once_within_lane_limits(
    tmp_path, credentials, monkeypatch
):
    store = Store(tmp_path)
    rows = recordings(store, 150)
    expected = []
    for index, row in enumerate(rows):
        if index % 3 == 0:
            expected.append(store.add_job(row["id"], "media")["id"])
        elif index % 3 == 1:
            expected.append(store.add_job(row["id"], "transcribe", {})["id"])
        else:
            params = {"settings": {"provider_type": "ucloud"}} if index % 6 == 2 else {}
            expected.append(store.add_job(row["id"], "transcribe", params)["id"])
    queue = JobQueue(store, credentials, concurrency={"media": 8, "api": 6})
    observer = Observer(store, hold=0.01)
    patch_process(queue, monkeypatch, observer)
    with store.library_lock():
        queue.start()
        try:
            assert wait_until(
                lambda: (
                    store.one("SELECT count(*) AS n FROM jobs WHERE state='completed'")["n"]
                    == len(expected)
                ),
                timeout=60,
            )
        finally:
            queue.stop()
    assert sorted(observer.processed) == sorted(expected)
    assert len(set(observer.processed)) == len(expected)
    assert 2 <= observer.peak["media"] <= 8
    assert 2 <= observer.peak["transcribe"] <= 6
    assert observer.peak["native"] <= 2
    assert not store.all("SELECT id FROM jobs WHERE state!='completed'")


def test_limits_change_live_without_stopping_running_work(tmp_path, credentials, monkeypatch):
    store = Store(tmp_path)
    jobs = [store.add_job(row["id"], "media")["id"] for row in recordings(store, 10)]
    queue = JobQueue(store, credentials, concurrency={"media": 3})
    release, lock = threading.Event(), threading.Lock()
    running, started_after_lowering, lowered = [0], [], threading.Event()

    def process(identifier):
        with lock:
            running[0] += 1
            if lowered.is_set():
                started_after_lowering.append(running[0])
        try:
            assert release.wait(20)
        finally:
            with lock:
                running[0] -= 1

    patch_process(queue, monkeypatch, process)
    with store.library_lock():
        queue.start()
        try:
            assert wait_until(lambda: running[0] == 3)
            time.sleep(0.6)  # Idle siblings keep polling but never exceed the limit.
            assert running[0] == 3
            queue.set_lane_limits({"media": 5})
            assert wait_until(lambda: running[0] == 5)
            lowered.set()
            queue.set_lane_limits({"media": 1})
            time.sleep(0.6)
            # Lowering a limit never cancels or interrupts work that already started.
            assert running[0] == 5
            assert not any(worker.cancel_event.is_set() for worker in queue.workers)
            release.set()
            assert wait_until(
                lambda: (
                    store.one("SELECT count(*) AS n FROM jobs WHERE state='completed'")["n"] == 10
                )
            )
        finally:
            release.set()
            queue.stop()
    assert started_after_lowering == [1] * 5
    assert {store.job(identifier)["state"] for identifier in jobs} == {"completed"}


def test_cancel_reaches_only_owner_and_shutdown_drains_many_workers_under_lease(
    tmp_path, credentials, monkeypatch
):
    store = Store(tmp_path)
    jobs = [store.add_job(row["id"], "media")["id"] for row in recordings(store, 8)]
    queue = JobQueue(store, credentials, concurrency={"media": 8})
    started, drained = threading.Barrier(9), []

    def process(identifier):
        started.wait(10)
        with queue.lock:
            owner = next(worker for worker in queue.workers if worker.active_id == identifier)
        assert owner.cancel_event.wait(20)
        drained.append(store._lease_depth)

    patch_process(queue, monkeypatch, process)
    with store.library_lock():
        queue.start()
        try:
            started.wait(10)
            owner = next(worker for worker in queue.workers if worker.active_id == jobs[3])
            queue.cancel(jobs[3])
            assert owner.cancel_event.is_set()
            assert sum(worker.cancel_event.is_set() for worker in queue.workers) == 1
            assert wait_until(lambda: store.job(jobs[3])["state"] == "cancelled")
            assert {store.job(job)["state"] for job in jobs if job != jobs[3]} == {"running"}
        finally:
            queue.stop()
        assert not any(worker.thread.is_alive() for worker in queue.workers)
    # Every accepted task drained while the library lease was still held.
    assert drained == [1] * 8
    assert {store.job(job)["state"] for job in jobs if job != jobs[3]} == {"interrupted"}


def test_shutdown_kills_and_reaps_every_running_ffmpeg(tmp_path, credentials, monkeypatch):
    store = Store(tmp_path)
    rows = recordings(store, 4)
    source = tmp_path / "long.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(os.urandom(2 * 16000 * 240))
    for row in rows:
        directory = store.directory(row["id"])
        directory.mkdir(parents=True)
        os.link(source, directory / "original.wav")
        store.add_job(row["id"], "media")
    ffmpeg_runtime.resolve()  # Its cached version probe is not one of the conversions.
    processes, real = [], subprocess.Popen

    def tracked(*args, **kwargs):
        process = real(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(media.subprocess, "Popen", tracked)
    queue = JobQueue(store, credentials, concurrency={"media": 4})
    with store.library_lock():
        queue.start()
        try:
            assert wait_until(
                lambda: sum(process.poll() is None for process in processes) == 4, timeout=30
            )
        finally:
            queue.stop()
    assert len(processes) == 4
    assert all(process.poll() is not None for process in processes)
    states = {row["state"] for row in store.all("SELECT state FROM jobs")}
    assert states == {"interrupted"}
    for row in rows:
        assert not (store.directory(row["id"]) / "audio.mp3").exists()


def test_idle_siblings_coalesce_polling_but_notice_claims_promptly(
    tmp_path, credentials, monkeypatch
):
    store = Store(tmp_path)
    row = recordings(store, 1)[0]
    queue = JobQueue(store, credentials, concurrency={"media": 8})
    queue.workers = [worker for worker in queue.workers if worker.kind == "media"]
    polls, claim = [], store.claim_job

    def counted(kind):
        polls.append(kind)
        return claim(kind)

    monkeypatch.setattr(store, "claim_job", counted)
    processed = threading.Event()
    patch_process(queue, monkeypatch, lambda identifier: processed.set())
    with store.library_lock():
        queue.start()
        try:
            time.sleep(2.2)
            # Eight idle workers once polled up to ~4 times each per two seconds. Now one
            # empty poll per lane covers its siblings until the interval passes.
            assert len(polls) <= 12
            store.add_job(row["id"], "media")
            started = time.monotonic()
            queue.notify()
            assert processed.wait(2)
            assert time.monotonic() - started < 0.4
        finally:
            queue.stop()


def test_admission_claim_skips_intents_waiting_on_pending_inspection(
    library, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    loads, load = [], input_admission._load

    def counted(db, job_id):
        loads.append(job_id)
        return load(db, job_id)

    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        monkeypatch.setattr(input_admission, "_load", counted)
        assert input_admission.claim(store) is None
        assert loads == []  # No full validation for intents that cannot be admitted yet.
        monkeypatch.setattr(input_admission, "_load", load)
        inspect(store, rows[1], wav_bytes)
        token = input_admission.claim(store)
        assert [member["id"] for member in token["jobs"]] == [accepted[1]["id"]]
        assert input_admission.claim(store) is None
