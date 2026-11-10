import threading
from collections import Counter

import pytest

from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.media import Cancelled
from cbs_transcriber.providers import OPENAI_URL, Settings


def parallel_job(client, credentials, monkeypatch, parts=3):
    store = client.app.state.store
    project = store.create_project("Parallel")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute(
        "UPDATE recordings SET status='ready',duration=2700 WHERE id=?", (recording["id"],)
    )
    credentials.keys[OPENAI_URL] = "synthetic-key"
    job = store.add_job(
        recording["id"],
        "transcribe",
        {
            "settings": Settings(concurrent_chunks=2).model_dump(),
            "revision": 0,
        },
    )
    folder = store.directory(recording["id"]) / "parts"
    folder.mkdir(parents=True)
    plan = [{"file": f"{i}.mp3", "offset": i * 900} for i in range(parts)]
    monkeypatch.setattr("cbs_transcriber.media.prepare_chunks", lambda *args: (folder, plan))
    return client.app.state.queue, job, recording, folder


def payload(index):
    return {
        "text": f"Part {index}",
        "segments": [
            {"text": f"Part {index}", "start": 0, "end": 1, "speaker": "A"},
        ],
    }


def test_v21_parallel_completion_is_bounded_checkpointed_and_ordered(
    client, credentials, monkeypatch
):
    queue, job, recording, folder = parallel_job(client, credentials, monkeypatch, parts=4)
    third_started = threading.Event()
    lock = threading.Lock()
    active, peak = 0, 0
    checkpoints = []
    update_job = queue.store.update_job

    def checkpoint(identifier, **fields):
        if "chunks" in fields:
            checkpoints.append([part["index"] for part in fields["chunks"]])
        update_job(identifier, **fields)

    def response(path, settings, key):
        nonlocal active, peak
        index = int(path.stem)
        with lock:
            active += 1
            peak = max(active, peak)
        try:
            if index == 0:
                assert third_started.wait(5), "Second success did not permit the next part."
            if index == 2:
                third_started.set()
            return payload(index)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(queue.store, "update_job", checkpoint)
    monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", response)
    queue.process(job["id"])
    assert peak == 2
    assert checkpoints[0] == [1]
    assert checkpoints[-1] == [0, 1, 2, 3]
    result = queue.store.recording(recording["id"])
    assert [segment["start"] for segment in result["segments"]] == [0, 900, 1800, 2700]
    assert [segment["text"] for segment in result["segments"]] == [f"Part {i}" for i in range(4)]
    assert len({segment["speaker"] for segment in result["segments"]}) == 4
    assert len(list(folder.glob("response-*.json"))) == 4


def test_v21_failure_retains_nonprefix_success_and_retry_skips_it(client, credentials, monkeypatch):
    queue, job, recording, _ = parallel_job(client, credentials, monkeypatch)
    calls = []
    first_failed = threading.Event()
    recovering = False

    def response(path, settings, key):
        index = int(path.stem)
        calls.append(index)
        if not recovering and index == 0:
            first_failed.set()
            raise ValueError("Synthetic rate limit")
        if not recovering:
            assert first_failed.wait(5)
        return payload(index)

    monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", response)
    with pytest.raises(ValueError, match="rate limit"):
        queue.process(job["id"])
    completed = queue.store.job(job["id"])["chunks"]
    assert [part["index"] for part in completed] == [1]
    assert sorted(calls) == [0, 1]  # Failed work never triggers an automatic new request.
    recovering = True
    JobQueue(queue.store, credentials).process(job["id"])
    assert Counter(calls) == Counter({0: 2, 1: 1, 2: 1})
    assert [segment["start"] for segment in queue.store.recording(recording["id"])["segments"]] == [
        0,
        900,
        1800,
    ]


def test_v21_cancellation_retains_accepted_results_and_stops_new_parts(
    client, credentials, monkeypatch
):
    queue, job, _, _ = parallel_job(client, credentials, monkeypatch)
    accepted = threading.Barrier(2)
    calls = []

    def response(path, settings, key):
        index = int(path.stem)
        calls.append(index)
        accepted.wait(5)
        queue.cancel_event.set()
        return payload(index)

    monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", response)
    with pytest.raises(Cancelled):
        queue.process(job["id"])
    assert sorted(calls) == [0, 1]
    assert [part["index"] for part in queue.store.job(job["id"])["chunks"]] == [0, 1]


def test_v21_shutdown_waits_for_checkpointing(client, credentials, monkeypatch):
    queue, job, _, _ = parallel_job(client, credentials, monkeypatch, parts=2)
    accepted = threading.Barrier(3)
    release = threading.Event()
    stopped = threading.Event()

    def response(path, settings, key):
        accepted.wait(5)
        assert release.wait(5)
        return payload(int(path.stem))

    monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", response)
    with queue.store.library_lock():
        queue.start()
        try:
            accepted.wait(5)
            stopper = threading.Thread(target=lambda: (queue.stop(), stopped.set()))
            stopper.start()
            assert not stopped.wait(0.1)
            release.set()
            stopper.join(5)
            assert stopped.is_set() and not queue.thread.is_alive()
            saved = queue.store.job(job["id"])
            assert saved["state"] == "interrupted" and len(saved["chunks"]) == 2
        finally:
            release.set()
            queue.stop()
