"""Startup recovery must retain negative intent, saved results and exact-ID authority."""

import threading
from copy import deepcopy

import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import selection as selection
from test_native_startup_recovery import admitted_queue, mutations, parked
from test_ucloud_native import remote as remote
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import native_startup
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.transcripts import normalize_segments


def update_owner(store, jobs, **fields):
    owner = jobs[0]["params"].get("native_batch_id")
    if owner:
        store.update_native_batch(owner, run_update=fields)
    else:
        current = store.job(jobs[0]["id"])
        params = deepcopy(current["params"])
        params["ucloud_run"].update(fields)
        store.update_job(current["id"], params=params)


@pytest.mark.parametrize("count", [1, 2], ids=["singleton", "batch"])
@pytest.mark.parametrize(
    "negative",
    ["stop_requested", "cancel_requested", "cancellation_requested", "termination_requested"],
)
def test_every_durable_negative_flag_excludes_startup_without_network(
    client, batch_remote, selection, monkeypatch, count, negative
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = parked(client, provider, recordings[:count], state="running")
    update_owner(store, jobs, **{negative: True})
    admitted_queue(client, monkeypatch)
    assert all(store.job(job["id"])["state"] != "queued" for job in jobs)
    assert not mutations(batch_remote)
    assert not batch_remote.submissions


def test_stop_before_ephemeral_acknowledgment_survives_a_fresh_queue_lifecycle(
    client, batch_remote, selection, monkeypatch
):
    class SimulatedProcessLoss(BaseException):
        pass

    class LostAcknowledgment:
        def set(self):
            raise SimulatedProcessLoss()

    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1], state="running")[0]
    old_queue = JobQueue(store, client.app.state.credentials, kind="transcribe")
    old_queue.active_id = job["id"]
    old_queue.cancel_event = LostAcknowledgment()
    with pytest.raises(SimulatedProcessLoss):
        old_queue.cancel(job["id"])
    assert store.job(job["id"])["params"]["ucloud_run"]["stop_requested"] is True
    fresh_queue = admitted_queue(client, monkeypatch)
    assert fresh_queue.native_lifecycle != old_queue.native_lifecycle
    assert store.job(job["id"])["state"] != "queued"
    assert not mutations(batch_remote)


def test_legacy_batch_stop_message_is_preserved_before_generic_recovery(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = parked(client, provider, recordings, state="running")
    owner_id = jobs[0]["params"]["native_batch_id"]
    store.update_native_batch(owner_id, message="Stopping UCloud batch (2 files)…")
    admitted_queue(client, monkeypatch)
    batch = store.native_batch(owner_id)
    assert batch["params"]["ucloud_run"]["stop_requested"] is True
    assert batch["state"] != "queued"
    assert all(store.job(job["id"])["state"] != "queued" for job in jobs)
    assert not mutations(batch_remote)


@pytest.mark.parametrize("count", [1, 2], ids=["singleton", "batch"])
@pytest.mark.parametrize("changed_id", [None, "another-allocation"], ids=["removed", "replaced"])
@pytest.mark.parametrize("same_lifecycle_restart", [False, True], ids=["first-start", "restart"])
def test_actual_worker_refuses_changed_id_after_admission_without_new_submission(
    client, batch_remote, selection, monkeypatch, count, changed_id, same_lifecycle_restart
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = parked(client, provider, recordings[:count])
    queue = JobQueue(store, client.app.state.credentials, kind="transcribe")
    original_start, original_update, original_finish = (
        queue._start,
        store.update_job,
        store.finish_native_batch,
    )
    finished = threading.Event()

    def observe_update(identifier, **fields):
        value = original_update(identifier, **fields)
        if identifier in {job["id"] for job in jobs} and fields.get("state") == "failed":
            finished.set()
        return value

    def observe_finish(identifier, state, message):
        value = original_finish(identifier, state, message)
        if state == "failed":
            finished.set()
        return value

    def mutate_then_start():
        assert jobs[0]["id"] in queue.native_reconnect
        update_owner(store, jobs, job_id=changed_id)
        original_start()

    monkeypatch.setattr(queue, "_start", mutate_then_start)
    monkeypatch.setattr(store, "update_job", observe_update)
    monkeypatch.setattr(store, "finish_native_batch", observe_finish)
    if same_lifecycle_restart:
        monkeypatch.setattr(queue, "_start", lambda: None)
        queue.start()
        monkeypatch.setattr(queue, "_start", mutate_then_start)
    queue.start()
    try:
        assert finished.wait(8), [store.job(job["id"]) for job in jobs]
    finally:
        queue.stop()
    assert not mutations(batch_remote)
    assert not batch_remote.submissions and not batch_remote.uploads
    assert store.all("SELECT id FROM transcript_results") == []


@pytest.mark.parametrize("reason", ["generic-message", "auth", "stale-generation"])
def test_failure_text_or_stale_receipts_cannot_grant_automatic_retry(
    client, batch_remote, selection, monkeypatch, reason
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1], state="failed")[0]
    store.update_job(job["id"], message="Connection error HTTP502: please reconnect")
    if reason != "generic-message":
        native_startup.failure(
            store,
            "job",
            job["id"],
            UCloudAPIError("synthetic", status_code=401 if reason == "auth" else 503),
            phase="monitor",
        )
    if reason == "stale-generation":
        params = deepcopy(store.job(job["id"])["params"])
        params["settings"]["language"] = "en"
        store.update_job(job["id"], params=params)
    admitted_queue(client, monkeypatch)
    assert store.job(job["id"])["state"] == "failed"
    assert not mutations(batch_remote)


@pytest.mark.parametrize("trash", [False, True], ids=["live", "trashed"])
def test_exact_saved_singleton_finalizes_locally_without_import_or_restoring_trash(
    client, batch_remote, selection, monkeypatch, trash
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1])[0]
    saved = store.add_result(
        job["recording_id"],
        normalize_segments([{"text": "Synthetic saved wording", "speaker": "A"}]),
        {"A": "Named synthetic voice"},
        "Synthetic already-saved result",
        {"ucloud_job_id": "remote-job"},
        job_id=job["id"],
    )
    if trash:
        store.set_result_trashed(
            job["recording_id"],
            saved["id"],
            store.recording(job["recording_id"])["revision"],
            trashed=True,
        )
    store.update_job(job["id"], state="interrupted")
    before = deepcopy(store.result(job["recording_id"], saved["id"], include_trashed=True))
    versions = deepcopy(
        store.all("SELECT * FROM result_versions WHERE result_id=?", (saved["id"],))
    )
    trash_rows = deepcopy(store.all("SELECT * FROM result_trash WHERE result_id=?", (saved["id"],)))
    assert bool(trash_rows) is trash
    admitted_queue(client, monkeypatch)
    assert store.job(job["id"])["state"] == "completed"
    assert store.result(job["recording_id"], saved["id"], include_trashed=True) == before
    assert store.all("SELECT * FROM result_versions WHERE result_id=?", (saved["id"],)) == versions
    assert store.all("SELECT * FROM result_trash WHERE result_id=?", (saved["id"],)) == trash_rows
    assert not mutations(batch_remote)


def test_saved_result_for_another_remote_id_does_not_finalize_or_reimport(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1])[0]
    saved = store.add_result(
        job["recording_id"],
        normalize_segments([{"text": "Keep this synthetic result"}]),
        {},
        "Synthetic mismatched provenance",
        {"ucloud_job_id": "another-allocation"},
        job_id=job["id"],
    )
    store.update_job(job["id"], state="interrupted")
    before = deepcopy(store.result(job["recording_id"], saved["id"]))
    admitted_queue(client, monkeypatch)
    assert store.job(job["id"])["state"] == "interrupted"
    assert store.result(job["recording_id"], saved["id"]) == before
    assert not mutations(batch_remote)
