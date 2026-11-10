"""Bounded native transfers overlap without sharing clients or losing accepted work."""

import json
import threading
from copy import deepcopy

import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue, frozen_native_jobs
from test_native_batch import selection as selection
from test_ucloud_native import remote as remote

from cbs_transcriber import native_batch
from cbs_transcriber.media import Cancelled


def four_recordings(store, recordings, wav_bytes, count=4):
    for index in range(len(recordings), count):
        recording = store.create_recording(recordings[0]["project_id"], f"extra-{index}.wav")
        store.directory(recording["id"]).mkdir(parents=True)
        (store.directory(recording["id"]) / "original.wav").write_bytes(wav_bytes)
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
        recordings.append(recording)


def process(client, jobs):
    store, queue = client.app.state.store, client.app.state.queue
    claimed = store.claim_job("transcribe")
    assert claimed is not None
    queue.active_batch_id = jobs[0]["params"]["native_batch_id"]
    queue.process(claimed["id"])


def retry(client, jobs):
    client.app.state.queue.cancel_event.clear()
    response = client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={})
    assert response.status_code == 200, response.text
    process(client, jobs)


def overlap(original, *, count=4, fail=None):
    barrier, lock = threading.Barrier(count), threading.Lock()
    decided = threading.Event()
    state = {"active": 0, "peak": 0, "clients": [], "finished": [], "calls": 0}

    def transfer(*args, **kwargs):
        with lock:
            index = state["calls"]
            state["calls"] += 1
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["clients"].append(args[0])
        try:
            value = original(*args, **kwargs)
            if index < count:
                barrier.wait(timeout=10)
                if index == 0:
                    try:
                        if fail is not None:
                            fail()
                    finally:
                        decided.set()
                else:
                    assert decided.wait(10)
            return value
        finally:
            with lock:
                state["active"] -= 1
                state["finished"].append(index)

    return transfer, state


def test_batch_uploads_and_downloads_overlap_with_independent_clients_and_serial_receipts(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    four_recordings(store, recordings, wav_bytes, count=6)
    jobs = frozen_native_jobs(client, provider, recordings)
    frozen = [deepcopy(job["params"]["settings"]) for job in jobs]
    upload, uploads = overlap(native_batch.native.upload)
    download, downloads = overlap(native_batch._download)
    monkeypatch.setattr(native_batch.native, "upload", upload)
    monkeypatch.setattr(native_batch, "_download", download)
    coordinator = threading.get_ident()
    original_checkpoint, original_add = store.update_native_batch, store.add_result
    original_update = store.update_job
    messages, message_lock = {}, threading.Lock()

    def update(identifier, **fields):
        if "message" in fields:
            with message_lock:
                messages.setdefault(identifier, []).append(fields["message"])
        return original_update(identifier, **fields)

    def checkpoint(*args, **kwargs):
        assert threading.get_ident() == coordinator
        return original_checkpoint(*args, **kwargs)

    def add(*args, **kwargs):
        assert threading.get_ident() == coordinator
        return original_add(*args, **kwargs)

    monkeypatch.setattr(store, "update_native_batch", checkpoint)
    monkeypatch.setattr(store, "add_result", add)
    monkeypatch.setattr(store, "update_job", update)
    process(client, jobs)
    assert uploads["peak"] == downloads["peak"] == 4
    assert uploads["active"] == downloads["active"] == 0
    assert len({id(value) for value in uploads["clients"] + downloads["clients"]}) == 12
    assert len(batch_remote.submissions) == 1
    assert len(batch_remote.uploads) == 6
    assert [store.job(job["id"])["params"]["settings"] for job in jobs] == frozen
    assert all(store.job(job["id"])["message"] == "Complete" for job in jobs)
    for job in jobs:
        for stage in (
            "Waiting to upload",
            "Uploading original to UCloud…",
            "Waiting for transcript",
            "Downloading transcript…",
            "Saving transcript…",
            "Complete",
        ):
            assert stage in messages[job["id"]]
    client.app.state.queue.process(jobs[0]["id"])
    assert uploads["calls"] == downloads["calls"] == 6
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)


@pytest.mark.parametrize("cancel", [False, True], ids=["failure", "stop"])
def test_upload_failure_or_stop_drains_and_checkpoints_successes_without_post(
    client, batch_remote, selection, wav_bytes, monkeypatch, cancel
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    four_recordings(store, recordings, wav_bytes)
    jobs = frozen_native_jobs(client, provider, recordings)
    original = native_batch.native.upload

    def fail():
        if cancel:
            queue.cancel_event.set()
            raise Cancelled()
        raise ValueError("Synthetic transfer failure")

    upload, state = overlap(original, fail=fail)
    monkeypatch.setattr(native_batch.native, "upload", upload)
    with pytest.raises(Cancelled if cancel else ValueError):
        process(client, jobs)
    assert state["active"] == 0 and len(state["finished"]) == 4
    batch_id = jobs[0]["params"]["native_batch_id"]
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert len(run["uploaded"]) == 3
    assert not run.get("submission_attempted") and not batch_remote.submissions
    monkeypatch.setattr(native_batch.native, "upload", original)
    retry(client, jobs)
    assert len(batch_remote.uploads) == 5  # Only the uncheckpointed input is sent again.
    assert len(batch_remote.submissions) == 1
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)


def test_failed_download_drains_imports_and_retry_fetches_only_the_missing_output(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    four_recordings(store, recordings, wav_bytes)
    jobs = frozen_native_jobs(client, provider, recordings)
    original = native_batch._download
    invalid_member = store.native_batch(jobs[0]["params"]["native_batch_id"])["members"][0]

    def invalid_output(*args, **kwargs):
        data, name = original(*args, **kwargs)
        return (b"not JSON", name) if args[4] == invalid_member["filename"] else (data, name)

    download, state = overlap(invalid_output)
    monkeypatch.setattr(native_batch, "_download", download)
    with pytest.raises(ValueError):
        process(client, jobs)
    assert state["active"] == 0 and len(state["finished"]) == 4
    run = store.native_batch(jobs[0]["params"]["native_batch_id"])["params"]["ucloud_run"]
    assert len(run["raw_hashes"]) == 3
    assert sum(store.job(job["id"])["state"] == "completed" for job in jobs) == 3
    assert not native_batch._raw_path(store, invalid_member).exists()
    before = len(
        [request for request in batch_remote.calls if request.url.path == "/api/files/download"]
    )
    monkeypatch.setattr(native_batch, "_download", original)
    retry(client, jobs)
    after = len(
        [request for request in batch_remote.calls if request.url.path == "/api/files/download"]
    )
    assert after == before + 1
    assert len(batch_remote.submissions) == 1 and len(batch_remote.uploads) == 4
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)


def test_stop_download_preserves_raw_capture_and_retry_never_downloads_again(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    download, state = overlap(native_batch._download, count=2, fail=queue.cancel_event.set)
    monkeypatch.setattr(native_batch, "_download", download)
    with pytest.raises(Cancelled):
        process(client, jobs)
    assert state["active"] == 0 and len(state["finished"]) == 2
    batch_id = jobs[0]["params"]["native_batch_id"]
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert len(run["raw_hashes"]) == 2
    before = []
    for job in jobs:
        capture = store.job(job["id"])["params"]["native_artifact_capture"]
        assert isinstance(capture["output"], dict)
        before.append(deepcopy(capture))
        assert not store.results(job["recording_id"])
        assert json.loads(
            native_batch._raw_path(
                store, {"job_id": job["id"], "recording_id": job["recording_id"]}
            ).read_bytes()
        )
    monkeypatch.setattr(native_batch, "_download", lambda *a, **kw: pytest.fail("reuse raw bytes"))
    retry(client, jobs)
    assert len(batch_remote.submissions) == 1
    assert [store.job(job["id"])["params"]["native_artifact_capture"] for job in jobs] == before
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)


def test_failed_capture_keeps_other_import_and_retry_does_not_backfill_observation(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    observe = native_batch.native_capture.observe_output
    attempted = []

    def crash(store, job, name):
        attempted.append(job["id"])
        if len(attempted) == 1:
            raise OSError("Synthetic crash after preserving downloaded bytes")
        return observe(store, job, name)

    monkeypatch.setattr(native_batch.native_capture, "observe_output", crash)
    with pytest.raises(ValueError, match="1 of 2 batch outputs could not be imported"):
        process(client, jobs)
    assert len(attempted) == 2
    assert sum(store.job(job["id"])["state"] == "completed" for job in jobs) == 1
    monkeypatch.setattr(
        native_batch.native_capture,
        "observe_output",
        lambda *a: pytest.fail("never invent a missed observation"),
    )
    monkeypatch.setattr(native_batch, "_download", lambda *a, **kw: pytest.fail("reuse raw bytes"))
    retry(client, jobs)
    assert store.job(attempted[0])["params"]["native_artifact_capture"]["output"] == "not_captured"
    assert isinstance(store.job(attempted[1])["params"]["native_artifact_capture"]["output"], dict)
    assert len(batch_remote.submissions) == 1
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("RUNNING", "Processing on UCloud · shared batch"),
        ("IN_QUEUE", "Waiting for UCloud allocation"),
    ],
)
def test_reconnect_reconciles_stale_upload_labels_even_when_remote_state_is_unchanged(
    client, batch_remote, selection, monkeypatch, state, message
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    jobs = frozen_native_jobs(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    store.update_native_batch(
        identifier,
        run_update={"job_id": "remote-job", "remote_state": state, "submission_attempted": True},
    )
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = identifier
    for job in jobs:
        store.update_job(job["id"], message="Uploading original to UCloud…")
    batch_remote.states = [state, state, "SUCCESS"]
    updates, polls = [], []
    original = store.update_job

    def update(identifier, **fields):
        if fields.get("message") == message:
            updates.append(identifier)
        return original(identifier, **fields)

    def wait(_):
        assert all(store.job(job["id"])["message"] == message for job in jobs)
        polls.append(state)

    monkeypatch.setattr(store, "update_job", update)
    monkeypatch.setattr(queue.cancel_event, "wait", wait)
    queue.process(claimed["id"])
    assert len(polls) == 2
    assert sorted(updates) == sorted(job["id"] for job in jobs)  # No writes for unchanged polls.
    assert not batch_remote.submissions and not batch_remote.uploads
    assert all(store.job(job["id"])["message"] == "Complete" for job in jobs)


def test_download_window_never_accumulates_more_than_four_unreceived_buffers():
    blocked, release, lock = threading.Event(), threading.Event(), threading.Lock()
    first_window = threading.Barrier(4)
    started, received, errors = [], [], []

    def transfer(member):
        with lock:
            started.append(member)
        if member < 4:
            first_window.wait(timeout=10)
        return b"buffer"

    def receive(member, future):
        assert future.result() == b"buffer"
        if not received:
            blocked.set()
            assert release.wait(10)
        received.append(member)

    def run():
        try:
            native_batch._transfers(range(20), transfer, receive, lambda: None)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert blocked.wait(10)
        assert len(started) == 4
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and not errors
    assert sorted(received) == list(range(20))
