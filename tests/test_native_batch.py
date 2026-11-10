"""Selected native recordings share one mocked UCloud allocation; no network or credentials."""

import json
import threading
from copy import deepcopy

import httpx
import pytest
from admission_support import inspect_and_materialize
from test_ucloud_native import PAYLOAD, SECRET, fresh_native_setup, trash_fake  # noqa: F401
from test_ucloud_native import remote as remote

from cbs_transcriber import native_batch, providers
from cbs_transcriber.media import Cancelled


@pytest.fixture
def batch_remote(remote):
    """Add the Batch app's input directory and per-input outputs to the shared fake."""
    remote.template["status"]["resolvedApplication"]["invocation"]["parameters"].append(
        {"name": "input_dir", "type": "input_directory"}
    )
    remote.uploads = []
    uploaded_files, upload_lock = {}, threading.Lock()
    original = remote.handle

    def handle(request):
        path = request.url.path
        if request.url.host == "files.invalid" and path == "/batch-upload":
            remote.calls.append(request)
            assert "authorization" not in request.headers
            assert SECRET not in str(request.headers)
            assert request.headers["Chunked-Upload-Token"] == "transfer-token"
            with upload_lock:
                uploaded = uploaded_files[request.url.params["target"]]
                assert int(request.headers["Chunked-Upload-Offset"]) == len(uploaded)
                uploaded.extend(request.content)
            return httpx.Response(200)
        if request.url.host == "cloud.invalid" and path == "/api/files/upload":
            remote.calls.append(request)
            assert request.headers["Authorization"] == "Bearer " + SECRET
            assert request.headers["Project"] == "project"
            target = json.loads(request.content)["items"][0]["id"]
            with upload_lock:
                uploaded_files[target] = bytearray()
                remote.uploads.append(uploaded_files[target])
            return httpx.Response(
                200,
                json={
                    "responses": [
                        {
                            "endpoint": str(
                                httpx.URL(
                                    "https://files.invalid/batch-upload", params={"target": target}
                                )
                            ),
                            "protocol": "CHUNKED",
                            "token": "transfer-token",
                        }
                    ]
                },
            )
        if request.url.host == "cloud.invalid" and path == "/api/files/retrieve":
            target = request.url.params["id"]
            with upload_lock:
                if target in uploaded_files:
                    remote.calls.append(request)
                    return httpx.Response(
                        200, json={"status": {"sizeInBytes": len(uploaded_files[target])}}
                    )
        if request.url.host == "cloud.invalid" and path == "/api/files/browse":
            folder = request.url.params["path"]
            inputs = [
                m["filename"]
                for b in remote.store.all("SELECT members FROM native_batch_runs")
                for m in json.loads(b["members"])
            ]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": folder + "/" + name.rsplit(".", 1)[0] + ".json"} for name in inputs
                    ]
                },
            )
        return original(request)

    remote.handle = handle
    return remote


@pytest.fixture
def selection(client, wav_bytes, remote, monkeypatch):
    """Native protocol tests retain this same-Store lease through admission and processing."""
    response = client.post(
        "/api/providers",
        json={
            "name": "UCloud native",
            "provider_type": "ucloud",
            "base_url": "https://cloud.invalid",
            "model": "large-v3",
            "ucloud_transcriber": fresh_native_setup(remote, monkeypatch),
            "api_key": SECRET,
            "remember": False,
        },
    )
    assert response.status_code == 201, response.text
    store = client.app.state.store
    with store.library_lock():
        project = store.create_project("Synthetic batch")
        recordings = []
        for name in ("first.wav", "second.wav"):
            recording = store.create_recording(project["id"], name)
            directory = store.directory(recording["id"])
            directory.mkdir(parents=True)
            (directory / "original.wav").write_bytes(wav_bytes)
            store.execute(
                "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
            )
            recordings.append(recording)
        yield response.json(), recordings


def enqueue(client, provider, recordings):
    """Accept, then explicitly inspect/admit this native domain fixture before execution.

    HTTP-only pending-202 assertions belong to test_input_admission_api. This is
    not an implicit client-wide drain and never executes a transcription job.
    """
    store = client.app.state.store
    assert store._lease_depth > 0, "The native fixture retains its lease through processing"
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [provider["id"]], "recording_ids": [r["id"] for r in recordings]},
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert all(job["params"]["input_intent"]["phase"] == "waiting" for job in accepted)
    assert store.claim_job("transcribe") is None
    jobs = inspect_and_materialize(
        store, client.app.state.credentials, [job["id"] for job in accepted]
    )
    assert all("input_acceptance" in job["params"] for job in jobs)
    assert all("input_intent" not in job["params"] for job in jobs)
    return jobs


def frozen_native_jobs(client, provider, recordings, *, cleanup_after_success=None):
    """Seed genuinely legacy native snapshots, without adding or altering new admission proof."""
    store = client.app.state.store
    assert store._lease_depth > 0
    saved = providers.profile(store, provider["id"])
    settings = deepcopy(saved["settings"])
    if cleanup_after_success is not None:
        settings["ucloud_transcriber"]["cleanup_after_success"] = cleanup_after_success
    jobs = store.add_transcription_jobs(
        [
            {
                "recording_id": recording["id"],
                "params": {
                    "settings": settings,
                    "provider_id": provider["id"],
                    "provider_name": saved["name"],
                    "revision": store.recording(recording["id"])["revision"],
                },
            }
            for recording in recordings
        ]
    )
    assert all(
        not {"input_acceptance", "input_intent", "audio_input"}.intersection(job["params"])
        for job in jobs
    )
    return jobs


def test_v84_selected_native_recordings_share_one_allocation(
    client, batch_remote, selection, wav_bytes
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    batch_ids = {job["params"]["native_batch_id"] for job in jobs}
    assert len(jobs) == 2 and len(batch_ids) == 1
    batch_id = batch_ids.pop()
    claimed = store.claim_job("transcribe")
    assert claimed["id"] in {job["id"] for job in jobs}
    assert {store.job(job["id"])["state"] for job in jobs} == {"running"}
    queue = client.app.state.queue
    queue.active_batch_id = batch_id
    queue.process(claimed["id"])
    assert len(batch_remote.submissions) == 1
    spec = batch_remote.submissions[0]
    assert spec["name"] == "CBS Transcriber batch " + batch_id
    assert spec["parameters"]["input_dir"]["path"].endswith("/cbs-batch-" + batch_id + "/input")
    assert "input_file" not in spec["parameters"]
    assert [bytes(upload) for upload in batch_remote.uploads] == [wav_bytes, wav_bytes]
    for job, recording in zip(jobs, recordings, strict=True):
        assert store.job(job["id"])["state"] == "completed"
        results = store.results(recording["id"])
        assert len(results) == 1
        saved = store.result(recording["id"], results[0]["id"])
        assert saved["segments"][0]["text"] == PAYLOAD["segments"][0]["text"]
    assert store.native_batch(batch_id)["state"] == "completed"
    listed = [j for j in client.get("/api/jobs").json() if j["id"] in {x["id"] for x in jobs}]
    assert {j["ucloud_batch_id"] for j in listed} == {batch_id}
    assert {j["ucloud_batch_count"] for j in listed} == {2}
    assert {j["ucloud_job_id"] for j in listed} == {"remote-job"}
    assert SECRET not in client.get("/api/jobs").text
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert run["output_model"] == "large-v3"
    assert run["output_language"] == "da"  # Frozen actual application default.
    # A repeated process call reuses the recorded allocation and never imports twice.
    queue.process(claimed["id"])
    assert len(batch_remote.submissions) == 1
    assert all(len(store.results(r["id"])) == 1 for r in recordings)


def test_v84_single_native_recording_keeps_independent_job(client, selection):
    provider, recordings = selection
    (job,) = enqueue(client, provider, recordings[:1])
    assert "native_batch_id" not in job["params"]
    assert not client.app.state.store.all("SELECT id FROM native_batch_runs")


def test_v84_uncertain_batch_submission_never_repeats(client, batch_remote, selection):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    batch_remote.unknown_submission = True
    claimed = store.claim_job("transcribe")
    queue = client.app.state.queue
    queue.active_batch_id = claimed["params"]["native_batch_id"]
    with pytest.raises(ValueError):
        queue.process(claimed["id"])
    assert len(batch_remote.submissions) == 1
    listed = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert all(listed[job["id"]]["ucloud_submission_uncertain"] for job in jobs)
    # Deleting a recording is blocked until the shared allocation is reconciled.
    response = client.delete("/api/recordings/" + recordings[0]["id"])
    assert response.status_code == 409
    response = client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={"replace": True})
    assert response.status_code == 200, response.text
    batch_remote.unknown_submission = False
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = claimed["params"]["native_batch_id"]
    with pytest.raises(ValueError, match="outcome is unknown"):
        queue.process(claimed["id"])
    assert len(batch_remote.submissions) == 1
    response = client.post("/api/jobs/" + jobs[1]["id"] + "/reconcile", json={"not_created": True})
    assert response.status_code == 200, response.text
    assert not any(j["ucloud_submission_uncertain"] for j in client.get("/api/jobs").json())


def test_v84_cancel_applies_to_the_whole_queued_batch(client, selection):
    provider, recordings = selection
    store = client.app.state.store
    jobs = enqueue(client, provider, recordings)
    response = client.post("/api/jobs/" + jobs[1]["id"] + "/cancel")
    assert response.status_code == 200, response.text
    assert {store.job(job["id"])["state"] for job in jobs} == {"cancelled"}
    assert store.claim_job("transcribe") is None


def test_v84_cancelled_running_batch_terminates_once(client, batch_remote, selection):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    batch_remote.states = ["RUNNING"]
    jobs = enqueue(client, provider, recordings)
    claimed = store.claim_job("transcribe")
    queue = client.app.state.queue
    queue.active_batch_id = claimed["params"]["native_batch_id"]
    original = batch_remote.handle

    def handle(request):
        if request.url.path == "/api/jobs/retrieve" and request.url.params["id"] == "remote-job":
            queue.cancel_event.set()
        return original(request)

    batch_remote.handle = handle
    with pytest.raises(Cancelled):
        queue.process(claimed["id"])
    terminations = [c for c in batch_remote.calls if c.url.path == "/api/jobs/terminate"]
    assert len(terminations) == 1
    assert {store.job(job["id"])["state"] for job in jobs} == {"cancelled"}


@pytest.mark.parametrize("language", ["", "da"])
def test_v84_four_file_retry_imports_exact_model_language_outputs(
    client, batch_remote, selection, language
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    provider["language"] = language
    assert client.put("/api/providers/" + provider["id"], json=provider).status_code == 200
    for name in ("third.m4a", "fourth.mp4"):
        recording = store.create_recording(recordings[0]["project_id"], name)
        store.directory(recording["id"]).mkdir(parents=True)
        # Already submitted legacy runs recover outputs without their old originals.
        # Do not masquerade WAV bytes as an AAC/video container for fresh admission.
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
        recordings.append(recording)
    jobs = frozen_native_jobs(client, provider, recordings)
    batch_id = jobs[0]["params"]["native_batch_id"]
    members = store.native_batch(batch_id)["members"]
    store.update_native_batch(
        batch_id,
        run_update={
            "job_id": "remote-job",
            "remote_state": "SUCCESS",
            "submission_attempted": True,
            "owned_folder": "/drive/cbs/cbs-batch-" + batch_id,
            "folders_created": True,
        },
    )
    store.finish_native_batch(batch_id, "failed", "Earlier output import failed")
    original = batch_remote.handle

    def output_names(request):
        if request.url.host == "files.invalid":
            assert "authorization" not in request.headers
            member_id = request.url.params["member"]
            assert member_id in {member["job_id"] for member in members}
            return httpx.Response(
                200,
                json={"segments": [{"text": "Synthetic " + member_id, "start": 0, "end": 1}]},
            )
        if request.url.path == "/api/files/download":
            path = json.loads(request.content)["items"][0]["id"]
            member_id = path.rsplit("/", 1)[-1].removesuffix("_large-v3_da.json")
            return httpx.Response(
                200,
                json={
                    "responses": [
                        {"endpoint": "https://files.invalid/download?member=" + member_id}
                    ]
                },
            )
        if request.url.path == "/api/files/browse":
            folder = request.url.params["path"]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": folder + "/" + member["job_id"] + "_large-v3_da.json"}
                        for member in members
                    ]
                },
            )
        return original(request)

    batch_remote.handle = output_names
    assert client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={}).status_code == 200
    claimed = store.claim_job("transcribe")
    queue = client.app.state.queue
    queue.active_batch_id = batch_id
    queue.process(claimed["id"])
    assert store.native_batch(batch_id)["state"] == "completed"
    assert {store.job(job["id"])["state"] for job in jobs} == {"completed"}
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)
    for job in jobs:
        result = store.results(job["recording_id"])[0]
        assert store.result(job["recording_id"], result["id"])["segments"][0]["text"] == (
            "Synthetic " + job["id"]
        )
    assert not batch_remote.submissions and not batch_remote.uploads
    # Successful imports and their allocation are reused even after another coordinator call.
    queue.process(claimed["id"])
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)
    assert not batch_remote.submissions and not batch_remote.uploads


@pytest.mark.parametrize(
    ("language", "names"),
    [
        ("da", ["member_large-v3_en.json"]),
        ("da", ["member_large-v2_da.json"]),
        ("da", ["other-member_large-v3_da.json"]),
        ("da", ["member_large-v3_da.json", "member.json"]),
        ("", ["member_large-v3_unknown.json"]),
        ("", ["member_large-v3_da.json", "member_large-v3_en.json"]),
    ],
)
def test_v84_batch_output_identity_must_match_exactly(language, names):
    paths = {"/owned/out/" + name for name in names}
    with pytest.raises(ValueError, match="unambiguous"):
        native_batch._download(
            None, "https://cloud.invalid", paths, "/owned/out", "member.wav", "large-v3", language
        )


def completed_cleanup_batch(client, batch_remote, selection):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    # This legacy cleanup protocol fixture starts with its frozen success policy;
    # it never rewrites a newly admitted job's immutable settings after execution.
    jobs = frozen_native_jobs(client, provider, recordings, cleanup_after_success=True)
    batch_id = jobs[0]["params"]["native_batch_id"]
    trash_fake(batch_remote, "/drive/cbs/cbs-batch-" + batch_id, accept=False)
    claimed = store.claim_job("transcribe")
    queue = client.app.state.queue
    queue.active_batch_id = batch_id
    queue.process(claimed["id"])
    assert store.native_batch(batch_id)["params"]["ucloud_run"]["cleanup_state"] == "pending"
    return jobs, batch_id


@pytest.mark.parametrize(
    ("damage", "error_code"),
    [("missing", "local_response_missing"), ("corrupt", "local_response_hash_mismatch")],
)
def test_v86_shared_cleanup_records_missing_or_corrupt_response_without_trash(
    client, batch_remote, selection, damage, error_code
):
    jobs, batch_id = completed_cleanup_batch(client, batch_remote, selection)
    store = client.app.state.store
    member = store.native_batch(batch_id)["members"][-1]
    raw = native_batch._raw_path(store, member)
    if damage == "missing":
        raw.unlink()
    else:
        raw.write_bytes(b"synthetic corrupt response")
    state = trash_fake(batch_remote, "/drive/cbs/cbs-batch-" + batch_id)

    with pytest.raises(ValueError):
        native_batch.cleanup_batch(store, client.app.state.credentials, batch_id)

    receipt = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert receipt["cleanup_state"] == "pending"
    assert receipt["cleanup_stage"] == "local_validation"
    assert receipt["cleanup_error_code"] == error_code
    assert receipt["cleanup_attempted_at"]
    assert not state["requests"]
    assert all(store.results(job["recording_id"]) for job in jobs)
    assert len(batch_remote.submissions) == 1


def test_v86_shared_cleanup_retry_is_idempotent_and_never_resubmits(
    client, batch_remote, selection
):
    jobs, batch_id = completed_cleanup_batch(client, batch_remote, selection)
    store = client.app.state.store
    state = trash_fake(batch_remote, "/drive/cbs/cbs-batch-" + batch_id, accept=False)
    endpoint = "/api/jobs/" + jobs[-1]["id"] + "/cleanup-ucloud"
    assert client.post(endpoint).json() == {"cleanup_state": "pending"}
    receipt = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert receipt["cleanup_stage"] == "trash"
    assert receipt["cleanup_error_code"] == "http_error"
    assert receipt["cleanup_http_status"] == 403
    assert SECRET not in json.dumps(receipt)
    state["accept"] = True
    assert client.post(endpoint).json() == {"cleanup_state": "trashed"}
    assert client.post(endpoint).json() == {"cleanup_state": "trashed"}
    assert len(state["requests"]) == 2
    assert len(batch_remote.submissions) == 1
    assert all(len(store.results(job["recording_id"])) == 1 for job in jobs)
    receipt = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert receipt["cleanup_stage"] == "complete"
    assert not receipt.get("cleanup_error")
    assert not receipt.get("cleanup_error_code")
