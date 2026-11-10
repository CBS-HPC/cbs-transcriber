import threading


def test_v22_small_media_prepares_while_transcription_waits(client, monkeypatch):
    queue = client.app.state.queue
    store = queue.store
    project = store.create_project("Separate preparation")
    interview = store.create_recording(project["id"], "interview.wav")
    sample = store.create_recording(project["id"], "public-sample.wav")
    store.add_job(interview["id"], "transcribe")
    store.add_job(sample["id"], "media")
    transcription_started = threading.Event()
    transcription_release = threading.Event()
    media_prepared = threading.Event()

    def process(identifier):
        job = store.job(identifier)
        if job["kind"] == "transcribe":
            transcription_started.set()
            assert transcription_release.wait(3)
        else:
            media_prepared.set()

    for worker in queue.workers:
        monkeypatch.setattr(worker, "process", process)
    with store.library_lock():
        queue.start()
        try:
            assert transcription_started.wait(1)
            assert media_prepared.wait(0.3), "Media preparation is blocked behind an API request."
        finally:
            transcription_release.set()
            queue.stop()


def test_v22_multiple_recordings_transcribe_while_media_prepares(client, monkeypatch):
    queue = client.app.state.queue
    store = queue.store
    project = store.create_project("Concurrent files")
    first = store.create_recording(project["id"], "one.wav")
    second = store.create_recording(project["id"], "two.wav")
    sample = store.create_recording(project["id"], "sample.wav")
    other_sample = store.create_recording(project["id"], "other-sample.wav")
    for recording in (first, second):
        store.add_job(recording["id"], "transcribe")
    for recording in (sample, other_sample):
        store.add_job(recording["id"], "media")
    barrier = threading.Barrier(5)
    release = threading.Event()
    seen = set()

    def process(identifier):
        with queue.lock:
            seen.add(identifier)
        barrier.wait(5)
        assert release.wait(5)

    for worker in queue.workers:
        monkeypatch.setattr(worker, "process", process)
    with store.library_lock():
        queue.start()
        try:
            barrier.wait(5)
            assert len(seen) == 4
            assert (
                len(store.all("SELECT id FROM jobs WHERE kind='transcribe' AND state='running'"))
                == 2
            )
            assert len(store.all("SELECT id FROM jobs WHERE kind='media' AND state='running'")) == 2
        finally:
            release.set()
            queue.stop()


def test_v22_cancelling_media_leaves_transcriptions_running(client, monkeypatch):
    queue = client.app.state.queue
    store = queue.store
    project = store.create_project("Independent cancellation")
    first = store.create_recording(project["id"], "one.wav")
    second = store.create_recording(project["id"], "sample.wav")
    third = store.create_recording(project["id"], "other-sample.wav")
    store.add_job(first["id"], "transcribe")
    media_job = store.add_job(second["id"], "media")
    store.add_job(third["id"], "media")
    started = threading.Barrier(4)
    release = threading.Event()

    def process(identifier):
        started.wait(5)
        if identifier == media_job["id"]:
            with queue.lock:
                owner = next(worker for worker in queue.workers if worker.active_id == identifier)
            assert owner.cancel_event.wait(5)
        else:
            assert release.wait(5)

    for worker in queue.workers:
        monkeypatch.setattr(worker, "process", process)
    with store.library_lock():
        queue.start()
        try:
            started.wait(5)
            owner = next(worker for worker in queue.workers if worker.active_id == media_job["id"])
            queue.cancel(media_job["id"])
            assert owner.cancel_event.is_set()
            assert all(
                not worker.cancel_event.is_set() for worker in queue.workers if worker is not owner
            )
        finally:
            release.set()
            queue.stop()
