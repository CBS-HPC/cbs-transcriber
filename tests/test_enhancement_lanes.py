"""UCloud enhancement waits in its own lane; local enhancement and repairs keep moving."""

import threading
import time

import pytest

from cbs_transcriber import enhancement, media
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture(autouse=True)
def library_lease(client):
    with client.app.state.store.library_lock():
        yield


def ready(store):
    project = store.create_project("Lanes")
    recording = store.create_recording(project["id"], "a.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    return recording


def enhance_job(store, recording, execution):
    return store.add_job(recording["id"], "enhance", {"enhancement": {"execution": execution}})


def wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_claims_split_by_frozen_execution(client):
    store = client.app.state.store
    recording = ready(store)
    remote = enhance_job(store, recording, "ucloud")
    local = enhance_job(store, recording, "local")
    older = store.add_job(recording["id"], "enhance", {})  # Legacy params stay local.
    assert store.claim_job("enhance")["id"] == local["id"]
    assert store.claim_job("enhance")["id"] == older["id"]
    assert store.claim_job("enhance") is None
    assert store.claim_job("enhance-remote")["id"] == remote["id"]
    assert store.claim_job("enhance-remote") is None
    assert store.claim_job("transcribe") is None


def test_queue_runs_one_worker_per_enhancement_lane(client):
    kinds = [worker.kind for worker in client.app.state.queue.workers]
    assert kinds.count("enhance") == 1
    assert kinds.count("enhance-remote") == 1


def test_long_ucloud_run_does_not_block_local_enhancement(client, monkeypatch):
    store, credentials = client.app.state.store, client.app.state.credentials
    recording = ready(store)
    started, release = threading.Event(), threading.Event()

    def run(queue, job, recording):
        if job["params"]["enhancement"]["execution"] == "ucloud":
            started.set()
            while not release.is_set():  # A remote allocation being polled.
                if queue.cancel_event.is_set():
                    raise media.Cancelled()
                time.sleep(0.01)
        queue.store.add_result(
            recording["id"],
            normalize_segments([{"text": "Hej.", "start": 0, "end": 1}]),
            {},
            "Enhanced",
            {"kind": "enhancement"},
            job_id=job["id"],
        )

    monkeypatch.setattr(enhancement, "run_enhancement", run)
    remote = enhance_job(store, recording, "ucloud")
    local = enhance_job(store, recording, "local")
    queue = JobQueue(store, credentials)
    queue.start()
    try:
        assert started.wait(10)
        assert wait_for(lambda: store.job(local["id"])["state"] == "completed")
        assert store.job(remote["id"])["state"] == "running"
        release.set()
        assert wait_for(lambda: store.job(remote["id"])["state"] == "completed")
    finally:
        release.set()
        queue.stop()
    assert len(store.results(recording["id"])) == 2


def test_cancel_reaches_the_remote_lane_owner(client, monkeypatch):
    store, credentials = client.app.state.store, client.app.state.credentials
    recording = ready(store)
    started = threading.Event()

    def run(queue, job, recording):
        started.set()
        while not queue.cancel_event.is_set():
            time.sleep(0.01)
        raise media.Cancelled()

    monkeypatch.setattr(enhancement, "run_enhancement", run)
    remote = enhance_job(store, recording, "ucloud")
    queue = JobQueue(store, credentials)
    queue.start()
    try:
        assert started.wait(10)
        queue.cancel(remote["id"])
        assert wait_for(lambda: store.job(remote["id"])["state"] == "cancelled")
        assert "enhancement stages" in store.job(remote["id"])["message"]
    finally:
        queue.stop()


def test_repairs_queue_beside_a_ucloud_run_but_not_beside_local_work(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    recording = ready(store)
    words = [{"word": "Hej", "start": 0, "end": 1}]
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Hej.", "start": 0, "end": 1, "words": words},
                {"text": "God morgen.", "start": 1, "end": 3},
            ]
        ),
        {},
        "Word model",
        {"capability": "words"},
    )
    remote = enhance_job(store, recording, "ucloud")
    store.update_job(remote["id"], state="running")
    url = f"/api/recordings/{recording['id']}/enhancements"
    repair = {
        "result_id": result["id"],
        "revision": 1,
        "segment_id": "1",
        "execution": "local",
        "align": True,
        "diarize": False,
    }
    queued = client.post(url, json=repair)
    assert queued.status_code == 202, queued.text
    assert store.job(queued.json()["id"])["params"]["enhancement"]["execution"] == "local"
    # Each lane still takes one enhancement per recording at a time.
    assert client.post(url, json=repair).status_code == 409
    whole = {"result_id": result["id"], "revision": 1, "execution": "ucloud"}
    assert client.post(url, json=whole).status_code == 409
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 2
