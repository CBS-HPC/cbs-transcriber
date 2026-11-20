"""V217 reconnects submitted native work; no source preparation or new allocation."""

import json
import threading
import time
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from test_native_batch import batch_remote as batch_remote
from test_native_batch import frozen_native_jobs
from test_native_batch import selection as selection
from test_ucloud_native import fresh_native_setup, native_selection
from test_ucloud_native import remote as remote

from cbs_transcriber import media, native_startup
from cbs_transcriber.app import create_app
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import now
from cbs_transcriber.transcripts import normalize_segments


def parked(client, provider, recordings, *, state="interrupted"):
    """A synthetic historical API allocation whose inputs were already submitted."""
    store = client.app.state.store
    jobs = frozen_native_jobs(client, provider, recordings)
    identifier = jobs[0]["params"].get("native_batch_id")
    folder = "/drive/cbs/" + ("cbs-batch-" + identifier if identifier else "cbs-" + jobs[0]["id"])
    run = {
        "submission_attempted": True,
        "job_id": "remote-job",
        "remote_state": "RUNNING",
        "owned_folder": folder,
        "folders_created": True,
    }
    if identifier:
        store.update_native_batch(identifier, run_update=run, state=state)
        for job in jobs:
            store.update_job(job["id"], state=state)
    else:
        store.update_job(
            jobs[0]["id"], params={**jobs[0]["params"], "ucloud_run": run}, state=state
        )
    return [store.job(job["id"]) for job in jobs]


def admitted_queue(client, monkeypatch):
    """Exercise startup admission without allowing unrelated fixture lanes to run."""
    queue = JobQueue(client.app.state.store, client.app.state.credentials, kind="transcribe")
    monkeypatch.setattr(queue, "_start", lambda: None)
    queue.start()
    return queue


def mutations(remote):
    return [
        (request.method, request.url.path)
        for request in remote.calls
        if request.url.path in {"/api/files/folder", "/api/files/upload", "/api/jobs"}
    ]


@pytest.mark.parametrize("count", [1, 2])
def test_v217_startup_retrieves_known_singleton_and_batch_without_source_or_new_post(
    client, batch_remote, selection, monkeypatch, count
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = parked(client, provider, recordings[:count], state="running")
    if count == 1:
        previous = batch_remote.handle

        def singleton_output(request):
            if request.url.path == "/api/files/browse":
                return httpx.Response(
                    200, json={"items": [{"id": request.url.params["path"] + "/recording.json"}]}
                )
            return previous(request)

        batch_remote.handle = singleton_output
    # Retrieval never needs playback or another inspection/decode of submitted input.
    store.execute(
        "UPDATE recordings SET status='pending' WHERE project_id=?", (recordings[0]["project_id"],)
    )

    def no_preparation(*args, **kwargs):
        raise AssertionError("startup recovery must not prepare audio")

    monkeypatch.setattr(media, "prepare_media", no_preparation)
    monkeypatch.setattr(media, "prepare_chunks", no_preparation)
    queue = admitted_queue(client, monkeypatch)
    assert {store.job(job["id"])["state"] for job in jobs} == {"queued"}
    claimed = store.claim_job("transcribe")
    assert claimed is not None
    queue.active_batch_id = claimed["params"].get("native_batch_id")
    queue.process(claimed["id"])
    assert all(store.job(job["id"])["state"] == "completed" for job in jobs)
    assert all(len(store.results(job["recording_id"])) == 1 for job in jobs)
    assert not mutations(batch_remote)
    assert not batch_remote.submissions and not batch_remote.uploads


def test_v217_startup_preserves_saved_sibling_and_does_not_repeat_same_lifecycle(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = parked(client, provider, recordings)
    saved = store.add_result(
        jobs[0]["recording_id"],
        normalize_segments([{"text": "Reviewed sibling"}]),
        {"Speaker_01": "A named speaker"},
        "Saved",
        {"kind": "provider", "ucloud_job_id": "remote-job"},
        job_id=jobs[0]["id"],
    )
    store.update_job(jobs[0]["id"], state="completed")
    before = deepcopy(store.result(jobs[0]["recording_id"], saved["id"]))
    queue = admitted_queue(client, monkeypatch)
    assert store.job(jobs[0]["id"])["state"] == "completed"
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = claimed["params"]["native_batch_id"]
    queue.process(claimed["id"])
    assert store.result(jobs[0]["recording_id"], saved["id"]) == before
    owner_id = jobs[0]["params"]["native_batch_id"]
    store.finish_native_batch(owner_id, "interrupted", "Synthetic later interruption")
    queue.start()
    assert store.native_batch(owner_id)["state"] == "interrupted"
    assert not mutations(batch_remote)


def test_v217_singleton_stop_is_durable_before_worker_acknowledgment(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1], state="running")[0]
    store.request_job_cancellation(job["id"])
    assert store.job(job["id"])["params"]["ucloud_run"].get("stop_requested") is True
    admitted_queue(client, monkeypatch)
    assert store.job(job["id"])["state"] != "queued"


def test_v217_legacy_pending_cancelling_survives_generic_recovery(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1], state="running")[0]
    store.update_job(job["id"], message="Cancelling; waiting for active work to stop.")
    admitted_queue(client, monkeypatch)
    assert store.job(job["id"])["state"] != "queued"
    assert store.job(job["id"])["params"]["ucloud_run"].get("stop_requested") is True


@pytest.mark.parametrize("count", [1, 2])
def test_v217_actual_application_reopen_retrieves_without_explicit_retry(
    tmp_path, credentials, batch_remote, wav_bytes, monkeypatch, count
):
    library = tmp_path / "reopen"
    first_app = create_app(library, start_worker=False, credentials=credentials)
    with TestClient(first_app, headers={"X-CBS-Request": "1"}) as first:
        store = first_app.state.store
        batch_remote.store = store
        with store.library_lock():
            provider, recording = native_selection(
                first, wav_bytes, setup=fresh_native_setup(batch_remote, monkeypatch)
            )
            recordings = [recording]
            if count == 2:
                other = store.create_recording(recording["project_id"], "second.wav")
                store.directory(other["id"]).mkdir(parents=True)
                (store.directory(other["id"]) / "original.wav").write_bytes(wav_bytes)
                store.execute("UPDATE recordings SET status='ready' WHERE id=?", (other["id"],))
                recordings.append(other)
            jobs = parked(first, provider, recordings, state="running")
    if count == 1:
        previous = batch_remote.handle

        def listing(request):
            if request.url.path == "/api/files/browse":
                return httpx.Response(
                    200, json={"items": [{"id": request.url.params["path"] + "/recording.json"}]}
                )
            return previous(request)

        batch_remote.handle = listing
    second_app = create_app(library, start_worker=True, credentials=credentials)
    batch_remote.store = second_app.state.store
    with TestClient(second_app, headers={"X-CBS-Request": "1"}):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if all(second_app.state.store.job(job["id"])["state"] == "completed" for job in jobs):
                break
            threading.Event().wait(0.02)
        assert all(second_app.state.store.job(job["id"])["state"] == "completed" for job in jobs)
        assert all(len(second_app.state.store.results(job["recording_id"])) == 1 for job in jobs)
    assert not mutations(batch_remote)
    assert not batch_remote.submissions and not batch_remote.uploads


def test_v217_explicit_singleton_retry_atomically_resets_only_startup_intent(
    client, batch_remote, selection
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1], state="running")[0]
    store.request_job_cancellation(job["id"])
    store.update_job(job["id"], state="interrupted")
    response = client.post("/api/jobs/" + job["id"] + "/retry", json={})
    assert response.status_code == 200, response.text
    queued = store.job(job["id"])
    assert queued["state"] == "queued"
    assert queued["params"]["ucloud_run"]["stop_requested"] is False
    assert queued["params"]["ucloud_run"]["job_id"] == "remote-job"
    # A lost HTTP reply cannot accept another recovery or clear a newer intent.
    repeated = client.post("/api/jobs/" + job["id"] + "/retry", json={})
    assert repeated.status_code == 409
    assert store.job(job["id"])["params"] == queued["params"]


def test_v217_explicit_retry_cannot_clear_a_stop_arriving_after_endpoint_checks(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    job = parked(client, provider, recordings[:1])[0]
    original = native_startup.explicit_retry

    def stopped_before_commit(queue, observed):
        store.request_job_cancellation(observed["id"])
        return original(queue, observed)

    monkeypatch.setattr(native_startup, "explicit_retry", stopped_before_commit)
    response = client.post("/api/jobs/" + job["id"] + "/retry", json={})
    assert response.status_code != 200
    current = store.job(job["id"])
    assert current["state"] == "cancelled"
    assert current["params"]["ucloud_run"]["stop_requested"] is True
    assert not mutations(batch_remote)


def test_v217_new_queued_native_selection_is_not_recovery(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = frozen_native_jobs(client, provider, recordings)
    owner_id = jobs[0]["params"]["native_batch_id"]
    before = deepcopy(store.native_batch(owner_id)["params"])
    queue = admitted_queue(client, monkeypatch)
    assert all(store.job(job["id"])["state"] == "queued" for job in jobs)
    assert store.native_batch(owner_id)["params"] == before
    assert not queue.native_reconnect


@pytest.mark.parametrize("count", [1, 2])
def test_v217_fresh_typed_transient_receipt_allows_one_startup_attempt(
    client, batch_remote, selection, monkeypatch, count
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = parked(client, provider, recordings[:count], state="failed")
    owner_id = jobs[0]["params"].get("native_batch_id", jobs[0]["id"])
    native_startup.failure(
        store,
        "native_batch" if count == 2 else "job",
        owner_id,
        httpx.ReadTimeout("synthetic connection failure"),
        phase="monitor",
    )
    queue = admitted_queue(client, monkeypatch)
    assert all(store.job(job["id"])["state"] == "queued" for job in jobs)
    assert all(job["id"] in queue.native_reconnect for job in jobs)


def test_v217_native_metadata_budget_does_not_block_large_nonnative_cancellation(client, selection):
    _, recordings = selection
    store = client.app.state.store
    job = store.add_job(
        recordings[0]["id"],
        "media",
        {"synthetic_metadata": "x" * (native_startup.MAX_METADATA_BYTES + 1)},
    )
    cancelled = store.request_job_cancellation(job["id"])
    assert cancelled["state"] == "cancelled"
    assert cancelled["params"] == job["params"]
    assert "ucloud_run" not in cancelled["params"]


@pytest.mark.parametrize("count", [1, 2])
def test_v217_reconciliation_requires_valid_timezone_aware_timestamp(
    client, batch_remote, selection, monkeypatch, count
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = parked(client, provider, recordings[:count])
    owner_id = jobs[0]["params"].get("native_batch_id")

    def reconciliation(value):
        fields = {"submission_attempted": False, "reconciled_at": value}
        if owner_id:
            store.update_native_batch(owner_id, run_update=fields)
        else:
            params = deepcopy(store.job(jobs[0]["id"])["params"])
            params["ucloud_run"].update(fields)
            store.update_job(jobs[0]["id"], params=params)

    for malformed in ("", " ", "not-a-time", "2026-11-12T12:00:00", None):
        reconciliation(malformed)
        queue = admitted_queue(client, monkeypatch)
        assert not queue.native_reconnect
        assert all(store.job(job["id"])["state"] == "interrupted" for job in jobs)
    reconciliation(now())
    queue = admitted_queue(client, monkeypatch)
    assert all(job["id"] in queue.native_reconnect for job in jobs)
    assert all(store.job(job["id"])["state"] == "queued" for job in jobs)
    assert not mutations(batch_remote)


def completed_sibling(store, jobs, *, remote_id="remote-job", trash=False):
    saved = store.add_result(
        jobs[0]["recording_id"],
        normalize_segments([{"text": "Preserve reviewed wording"}]),
        {"A": "Named voice"},
        "Previously saved native result",
        {"kind": "provider", "ucloud_job_id": remote_id},
        job_id=jobs[0]["id"],
    )
    if trash:
        store.set_result_trashed(
            jobs[0]["recording_id"],
            saved["id"],
            store.recording(jobs[0]["recording_id"])["revision"],
            trashed=True,
        )
    store.update_job(jobs[0]["id"], state="completed")
    return saved


@pytest.mark.parametrize("trash", [False, True])
def test_v217_wrong_remote_id_on_completed_sibling_refuses_recovery_without_mutation(
    client, batch_remote, selection, monkeypatch, trash
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = parked(client, provider, recordings)
    saved = completed_sibling(store, jobs, remote_id="another-allocation", trash=trash)
    before = deepcopy(store.result(jobs[0]["recording_id"], saved["id"], include_trashed=True))
    history = store.all("SELECT * FROM result_versions WHERE result_id=?", (saved["id"],))
    queue = admitted_queue(client, monkeypatch)
    assert not queue.native_reconnect
    assert store.job(jobs[1]["id"])["state"] == "interrupted"
    assert store.job(jobs[0]["id"])["state"] == "completed"
    assert store.result(jobs[0]["recording_id"], saved["id"], include_trashed=True) == before
    assert store.all("SELECT * FROM result_versions WHERE result_id=?", (saved["id"],)) == history
    assert bool(store.all("SELECT 1 FROM result_trash WHERE result_id=?", (saved["id"],))) is trash
    assert not mutations(batch_remote)


@pytest.mark.parametrize("trash", [False, True])
def test_v217_completed_legacy_history_gap_keeps_payload_and_allows_other_output_recovery(
    client, batch_remote, selection, monkeypatch, trash
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = parked(client, provider, recordings)
    saved = completed_sibling(store, jobs, trash=trash)
    store.execute(
        "UPDATE result_versions SET action='legacy_observation' WHERE result_id=? AND sequence=1",
        (saved["id"],),
    )
    before = deepcopy(store.result(jobs[0]["recording_id"], saved["id"], include_trashed=True))
    history = store.all("SELECT * FROM result_versions WHERE result_id=?", (saved["id"],))
    queue = admitted_queue(client, monkeypatch)
    assert jobs[1]["id"] in queue.native_reconnect
    assert store.job(jobs[1]["id"])["state"] == "queued"
    assert store.result(jobs[0]["recording_id"], saved["id"], include_trashed=True) == before
    assert store.all("SELECT * FROM result_versions WHERE result_id=?", (saved["id"],)) == history
    assert bool(store.all("SELECT 1 FROM result_trash WHERE result_id=?", (saved["id"],))) is trash


@pytest.mark.parametrize("trash", [False, True])
def test_v217_worker_guard_refuses_late_completed_sibling_remote_id_disagreement(
    client, batch_remote, selection, monkeypatch, trash
):
    provider, recordings = selection
    store = client.app.state.store
    jobs = parked(client, provider, recordings)
    saved = completed_sibling(store, jobs, trash=trash)
    queue = admitted_queue(client, monkeypatch)
    token = queue.native_reconnect[jobs[1]["id"]]
    source = deepcopy(saved["source"])
    source["ucloud_job_id"] = "another-allocation"
    store.execute(
        "UPDATE transcript_results SET source=? WHERE id=?", (json.dumps(source), saved["id"])
    )
    with pytest.raises(ValueError, match="could not verify this allocation"):
        native_startup.guard(store, token)
    assert store.job(jobs[1]["id"])["state"] == "queued"
    assert bool(store.all("SELECT 1 FROM result_trash WHERE result_id=?", (saved["id"],))) is trash
    assert not mutations(batch_remote)
