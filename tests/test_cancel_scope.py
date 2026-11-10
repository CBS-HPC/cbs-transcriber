"""Scoped cancellation releases queue locks before remote I/O and keeps concurrent evidence."""

import threading


def test_remote_stop_does_not_hold_queue_lock_or_erase_concurrent_journal(client, monkeypatch):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Cancel scope")
    recording = store.create_recording(project["id"], "clip.wav")
    job = store.add_job(
        recording["id"],
        "enhance",
        {"ucloud_api": {}, "ucloud_run": {"job_id": "owned", "remote_state": "RUNNING"}},
    )
    store.update_job(job["id"], state="failed")
    acquired = threading.Event()

    def terminate(*args):
        def observe():
            with queue.lock:
                acquired.set()

        observer = threading.Thread(target=observe)
        observer.start()
        try:
            assert acquired.wait(1), "Remote cancellation holds the shared queue lock."
        finally:
            observer.join(0.05)
        current = store.job(job["id"])
        current["params"]["ucloud_run"]["capacity_released"] = True
        store.update_job(job["id"], params=current["params"])

    monkeypatch.setattr("cbs_transcriber.enhancement_api.terminate_owned", terminate)
    queue.cancel(job["id"])
    saved = store.job(job["id"])
    assert saved["state"] == "cancelled"
    assert saved["params"]["ucloud_run"]["termination_requested"] is True
    assert saved["params"]["ucloud_run"]["capacity_released"] is True


def test_cancel_respects_frozen_allowed_job_scope(client):
    import pytest

    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Exact cancellation")
    recording = store.create_recording(project["id"], "clip.wav")
    job = store.add_job(recording["id"], "media")
    with pytest.raises(ValueError, match="scope"):
        queue.cancel(job["id"], allowed_job_ids=set())
    assert store.job(job["id"])["state"] == "queued"


def test_blocked_remote_stop_rejects_retry_and_duplicate_stop_until_drain(client, monkeypatch):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Stop before Retry")
    recording = store.create_recording(project["id"], "clip.wav")
    job = store.add_job(
        recording["id"],
        "enhance",
        {
            "ucloud_api": {},
            "ucloud_run": {"job_id": "owned", "remote_state": "RUNNING"},
            "frozen_marker": "Keep the accepted request",
        },
    )
    started, release = threading.Event(), threading.Event()
    errors = []

    def terminate(*args):
        started.set()
        assert release.wait(4), "The test must release its mock remote request"

    def cancel():
        try:
            queue.cancel(job["id"])
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr("cbs_transcriber.enhancement_api.terminate_owned", terminate)
    control = threading.Thread(target=cancel)
    control.start()
    try:
        assert started.wait(2)
        retry = client.post(f"/api/jobs/{job['id']}/retry", json={})
        assert retry.status_code == 409, retry.text
        duplicate = client.post(f"/api/jobs/{job['id']}/cancel")
        assert duplicate.status_code == 409, duplicate.text
        assert store.job(job["id"])["state"] == "cancelled"
        assert store.job(job["id"])["params"] == job["params"]
        other = store.create_recording(project["id"], "other.wav")
        unrelated = store.add_job(other["id"], "media")
        queue.cancel(unrelated["id"])
        assert store.job(unrelated["id"])["state"] == "cancelled"
    finally:
        release.set()
        control.join(3)
    assert not control.is_alive()
    assert not errors
    assert not queue.control_job_ids
    saved = store.job(job["id"])
    assert saved["params"]["frozen_marker"] == "Keep the accepted request"
    assert saved["params"]["ucloud_run"]["termination_requested"] is True
    with queue.control_jobs([job["id"]]):
        pass  # A completed control never strands a future explicit Retry.


def test_retry_control_slot_rejects_stop_without_changing_job(client):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Retry before Stop")
    recording = store.create_recording(project["id"], "clip.wav")
    job = store.add_job(recording["id"], "media")
    store.update_job(job["id"], state="failed")
    with queue.control_jobs([job["id"]]):
        response = client.post(f"/api/jobs/{job['id']}/cancel")
        assert response.status_code == 409, response.text
        assert store.job(job["id"])["state"] == "failed"
    assert not queue.control_job_ids


def test_shared_native_stop_fences_retry_of_a_different_member(client, monkeypatch):
    from test_delete_cancellation import native_jobs

    store, queue = client.app.state.store, client.app.state.queue
    _, jobs = native_jobs(client)
    batch_id = jobs[0]["params"]["native_batch_id"]
    store.update_native_batch(
        batch_id, state="failed", run_update={"job_id": "remote", "remote_state": "RUNNING"}
    )
    for job in jobs:
        store.update_job(job["id"], state="failed")
    started, release = threading.Event(), threading.Event()
    errors = []

    def terminate(*args):
        started.set()
        assert release.wait(4)

    def cancel():
        try:
            queue.cancel(jobs[0]["id"])
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr("cbs_transcriber.ucloud_native.terminate_owned", terminate)
    control = threading.Thread(target=cancel)
    control.start()
    try:
        assert started.wait(2)
        retry = client.post(f"/api/jobs/{jobs[1]['id']}/retry", json={})
        assert retry.status_code == 409, retry.text
        assert store.job(jobs[1]["id"])["state"] == "failed"
    finally:
        release.set()
        control.join(3)
    assert not control.is_alive()
    assert not errors
    assert not queue.control_job_ids
