import threading

import pytest
from admission_support import COMPATIBLE_API_URL

from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.media import chunk_boundaries
from cbs_transcriber.providers import Settings


def test_v6_failed_second_chunk_resumes_without_resubmitting_first(
    client, credentials, monkeypatch
):
    store = client.app.state.store
    project = store.create_project("Research")
    recording = store.create_recording(project["id"], "interview.wav")
    store.directory(recording["id"]).mkdir(parents=True)
    store.execute(
        "UPDATE recordings SET status='ready',duration=1800 WHERE id=?", (recording["id"],)
    )
    credentials.keys[COMPATIBLE_API_URL] = "synthetic-key"
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"settings": Settings(base_url=COMPATIBLE_API_URL).model_dump(), "revision": 0},
    )
    folder = store.directory(recording["id"]) / "test-chunks"
    folder.mkdir()
    plan = [{"file": "a.mp3", "offset": 0}, {"file": "b.mp3", "offset": 900}]
    monkeypatch.setattr("cbs_transcriber.media.prepare_chunks", lambda *args: (folder, plan))
    calls = []

    def transcribe(path, settings, key):
        calls.append(path.name)
        if len(calls) == 2:
            raise ValueError("Synthetic network failure")
        return {"text": "Hello", "segments": [{"text": "Hello", "start": 0, "end": 1}]}

    monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", transcribe)
    queue = client.app.state.queue
    with pytest.raises(ValueError, match="Synthetic"):
        queue.process(job["id"])
    assert len(store.job(job["id"])["chunks"]) == 1
    JobQueue(store, credentials).process(job["id"])
    assert calls == ["a.mp3", "b.mp3", "b.mp3"]
    assert store.recording(recording["id"])["segments"][1]["start"] == 900


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (KeyError("char_start"), "Unexpected processing error: expected job or recording data"),
        (PermissionError(13, "Permission denied"), "A local file was busy or could not be opened"),
        (FileNotFoundError(2, "No such file"), "Local file operation failed. Check free space"),
        (ValueError("Curated reason."), "Curated reason."),
    ],
)
def test_failed_job_messages_name_the_kind_of_failure(
    client, credentials, monkeypatch, error, expected
):
    """B469: a missing key or a busy file is not reported as a disk-space problem."""
    store = client.app.state.store
    project = store.create_project("Research")
    recording = store.create_recording(project["id"], "interview.wav")
    store.execute("UPDATE recordings SET status='ready',duration=60 WHERE id=?", (recording["id"],))
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"settings": Settings(base_url=COMPATIBLE_API_URL).model_dump(), "revision": 0},
    )
    worker = JobQueue(store, credentials)

    def process(identifier):
        assert identifier == job["id"]
        worker.stopping.set()  # One claimed job, then this synchronous worker ends.
        raise error

    monkeypatch.setattr(worker, "process", process)
    worker.run()
    failed = store.job(job["id"])
    assert failed["state"] == "failed"
    assert failed["message"].startswith(expected)
    assert "char_start" not in failed["message"]


def test_chunk_boundaries_prefer_silence_without_gaps():
    chunks = chunk_boundaries(2000, [895, 1790])
    assert chunks == [(0, 895), (895, 1790), (1790, 2000)]
    assert chunk_boundaries(5, []) == [(0, 5)]


def test_cancel_queued_job_is_durable(client):
    store = client.app.state.store
    project = store.create_project("Research")
    recording = store.create_recording(project["id"], "interview.wav")
    job = store.add_job(recording["id"], "media")
    client.app.state.queue.cancel(job["id"])
    assert store.job(job["id"])["state"] == "cancelled"
    assert not client.app.state.queue.busy(recording["id"])


def test_media_cancel_is_immediate():
    from cbs_transcriber.media import Cancelled, ffmpeg

    event = threading.Event()
    event.set()
    with pytest.raises(Cancelled):
        ffmpeg([], event)
