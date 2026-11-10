"""Selected repair admission/application uses synthetic libraries and no live providers."""

import copy
import threading

import pytest
import test_repair_bundles as bundle_fixtures
import test_repair_cloud as cloud_fixtures
from fastapi.testclient import TestClient

from cbs_transcriber import enhancement, original_inspection, repair_cloud, repair_remote, routing
from cbs_transcriber.app import create_app


@pytest.fixture
def case(tmp_path, monkeypatch, credentials):
    study = bundle_fixtures.study.__wrapped__(tmp_path)
    execution = cloud_fixtures.execution.__wrapped__(study, tmp_path, monkeypatch)
    app = create_app(study[0].root, start_worker=False, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        execution.client = client
        yield execution


def request(case, ids=("1", "4")):
    current = case.store.recording(case.recording["id"])
    return case.client.post(
        f"/api/recordings/{current['id']}/passage-timing",
        json={
            "result_id": current["active_transcript_id"],
            "revision": current["revision"],
            "segment_ids": list(ids),
        },
    )


def ready(case, monkeypatch):
    routing.save_rules(case.store, routing.Rules(word_minutes=0))
    snapshot = case.job["params"]["audio_input"]
    cache = {
        "state": "ready",
        "receipt": snapshot,
        "generation": "f" * 32,
        "receipt_sha256": repair_remote.digest(repair_remote.encoded(snapshot)),
    }
    monkeypatch.setattr(original_inspection, "read", lambda *_: cache)
    extras = {
        key: copy.deepcopy(case.job["params"][key])
        for key in ("ucloud_api", "repair_cloud", "repair_runtime")
    }

    def freeze(_store, _credentials, _projects, options):
        assert not case.client.app.state.queue.lock._is_owned()
        return options, extras

    monkeypatch.setattr(repair_cloud, "freeze", freeze)
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *_: None)
    # The already constructed fixture run must not block this new request.
    case.store.update_job(case.job["id"], state="failed")
    return cache


def test_large_public_request_freezes_selected_source_and_prepared_mig(case, monkeypatch):
    ready(case, monkeypatch)
    response = request(case)
    assert response.status_code == 202, response.text
    job = case.store.job(response.json()["id"])
    assert job["params"]["repair_cloud"]["protocol"] == repair_cloud.PROTOCOL
    assert job["params"]["repair_routing"]["selected_seconds"] == 6
    assert job["params"]["enhancement"]["execution"] == "ucloud"
    assert [s["id"] for s in job["params"]["source_snapshot"]["segments"]] == ["1", "4"]
    assert (
        job["params"]["source_snapshot"]["source_payload_sha256"]
        == case.job["params"]["source_snapshot"]["source_payload_sha256"]
    )
    assert "input_intent" not in job["params"] and "batch_plan" not in job["params"]["ucloud_api"]


def test_older_public_request_defers_source_proof_without_media_work(case, monkeypatch):
    from cbs_transcriber import audio_inputs

    ready(case, monkeypatch)
    monkeypatch.setattr(original_inspection, "read", lambda *_: None)
    monkeypatch.setattr(
        audio_inputs,
        "freeze",
        lambda *_a, **_kw: pytest.fail("HTTP must not hash or decode originals"),
    )
    response = request(case)
    assert response.status_code == 202, response.text
    job = case.store.job(response.json()["id"])
    assert job["params"]["enhancement"]["execution"] == "ucloud"
    assert "audio_input" not in job["params"]
    assert job["params"]["repair_source"]["policy"] == "selected-original-v1"
    assert job["params"]["repair_source"]["windows"] == [
        {"ids": ["1"], "start": 2, "end": 5},
        {"ids": ["4"], "start": 9, "end": 12},
    ]


def test_older_source_retry_advances_epoch_without_changing_selection(case, monkeypatch):
    ready(case, monkeypatch)
    monkeypatch.setattr(original_inspection, "read", lambda *_: None)
    response = request(case)
    assert response.status_code == 202, response.text
    job = case.store.job(response.json()["id"])
    case.store.update_job(job["id"], state="failed")
    monkeypatch.setattr(repair_cloud.native, "require_setup", lambda *_: None)
    retried = case.client.post(f"/api/jobs/{job['id']}/retry", json={"replace": False})
    assert retried.status_code == 200, retried.text
    saved = case.store.job(job["id"])
    assert saved["params"]["repair_source"] == job["params"]["repair_source"] | {"epoch": 2}
    assert saved["params"]["source_snapshot"] == job["params"]["source_snapshot"]


def test_revision_change_during_outside_lock_preflight_never_enqueues(case, monkeypatch):
    ready(case, monkeypatch)
    real = repair_cloud.freeze

    def changed(*args):
        current = case.store.recording(case.recording["id"])
        case.store.save_transcript(
            current["id"], current["segments"], current["speakers"], current["revision"]
        )
        return real(*args)

    monkeypatch.setattr(repair_cloud, "freeze", changed)
    count = len(case.store.all("SELECT id FROM jobs"))
    response = request(case)
    assert response.status_code == 409, response.text
    assert len(case.store.all("SELECT id FROM jobs")) == count


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_partial_ready_terminal_job_still_blocks_duplicate_selected_requests(
    case, monkeypatch, state
):
    ready(case, monkeypatch)
    repair_remote.process(case.remote, case.job["id"], runner=cloud_fixtures.fake_alignment)
    cloud_fixtures.terminal(case)
    case.controller.harvest()
    case.controller.publish()
    case.store.update_job(case.job["id"], state=state)
    response = request(case)
    assert response.status_code == 409 and "already on its way" in response.text


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_public_apply_requires_exact_result_and_accepts_valid_partial_terminal(case, state):
    repair_remote.process(case.remote, case.job["id"], runner=cloud_fixtures.fake_alignment)
    cloud_fixtures.terminal(case)
    case.controller.harvest()
    case.controller.publish()
    case.store.update_job(case.job["id"], state=state)
    current = case.store.recording(case.recording["id"])
    path = f"/api/recordings/{current['id']}/realign/{case.job['id']}"
    assert case.client.post(path, json={"revision": current["revision"]}).status_code == 409
    assert (
        case.client.post(
            path, json={"revision": current["revision"], "result_id": "wrong"}
        ).status_code
        == 409
    )
    payload = {"revision": current["revision"], "result_id": current["active_transcript_id"]}
    response = case.client.post(path, json=payload)
    assert response.status_code == 200, response.text
    assert case.client.post(path, json=payload).status_code == 200


def test_worker_dispatches_cloud_repair_to_selected_clip_preparation(case, monkeypatch):
    seen = []
    monkeypatch.setattr(repair_cloud, "prepare_and_run", lambda _w, job, _r: seen.append(job["id"]))
    monkeypatch.setattr(
        enhancement,
        "run_enhancement",
        lambda *_: pytest.fail("whole enhancer is not a repair transport"),
    )
    worker = case.client.app.state.queue
    worker.cancel_event = threading.Event()
    worker.process(case.job["id"])
    assert seen == [case.job["id"]]


def test_cleanup_dispatches_to_receipt_guarded_repair_path_outside_queue_lock(case, monkeypatch):
    case.store.update_job(case.job["id"], state="failed")

    def cleanup(_store, _credentials, job):
        assert not case.client.app.state.queue.lock._is_owned()
        assert job["id"] == case.job["id"]
        return "retained"

    monkeypatch.setattr(repair_cloud, "retry_cleanup", cleanup)
    response = case.client.post(f"/api/jobs/{case.job['id']}/cleanup-ucloud")
    assert response.status_code == 200 and response.json() == {"cleanup_state": "retained"}


def test_stop_keeps_publication_and_capacity_written_during_control_request(case, monkeypatch):
    repair_remote.process(case.remote, case.job["id"], runner=cloud_fixtures.fake_alignment)
    cloud_fixtures.terminal(case)
    case.controller.harvest()
    case.controller.publish()
    case.store.update_job(case.job["id"], state="failed")

    def terminate(job, _credentials):
        assert job["params"]["ucloud_run"]["termination_requested"] is True
        fresh = case.store.job(job["id"])
        fresh["params"]["ucloud_run"]["capacity_refresh_error"] = "independent observation"
        fresh["params"]["realign_receipt"] = {"synthetic": "concurrent apply receipt"}
        case.store.update_job(job["id"], params=fresh["params"])

    monkeypatch.setattr(repair_cloud.api, "terminate_owned", terminate)
    case.client.app.state.queue.cancel(case.job["id"])
    fresh = case.store.job(case.job["id"])
    assert fresh["state"] == "cancelled"
    assert fresh["params"]["ucloud_run"]["repair"]["published"] is True
    assert fresh["params"]["ucloud_run"]["capacity_refresh_error"] == "independent observation"
    assert fresh["params"]["realign_receipt"] == {"synthetic": "concurrent apply receipt"}


def test_retry_posts_reconnect_exact_plan_without_local_checker_or_params_replacement(
    case, monkeypatch
):
    case.store.update_job(case.job["id"], state="interrupted")
    monkeypatch.setattr(repair_cloud.native, "require_setup", lambda *_: None)

    def approval(*_args):
        fresh = case.store.job(case.job["id"])
        fresh["params"]["ucloud_run"] = {"capacity_refresh_error": "retain concurrent receipt"}
        case.store.update_job(case.job["id"], params=fresh["params"])

    monkeypatch.setattr(repair_cloud.providers, "require_approval", approval)
    response = case.client.post(f"/api/jobs/{case.job['id']}/retry", json={"replace": False})
    assert response.status_code == 200, response.text
    fresh = case.store.job(case.job["id"])
    assert fresh["state"] == "queued"
    assert fresh["params"]["ucloud_run"]["capacity_refresh_error"] == "retain concurrent receipt"
    assert fresh["params"]["repair_cloud"] == case.job["params"]["repair_cloud"]


def test_reconcile_refuses_changed_owner_and_preserves_independent_journal(case, monkeypatch):
    case.store.update_job(case.job["id"], state="failed")
    fresh = case.store.job(case.job["id"])
    fresh["params"]["ucloud_run"] = {"submission_intent": True}
    case.store.update_job(case.job["id"], params=fresh["params"])
    response = case.client.post(f"/api/jobs/{case.job['id']}/reconcile", json={"not_created": True})
    assert response.status_code == 200, response.text
    assert case.store.job(case.job["id"])["params"]["ucloud_run"]["reconciled_not_created"] is True


def test_public_retry_recovers_same_owner_without_private_transfer_or_inference(
    case, credentials, current_transcriber_application, tmp_path, monkeypatch
):
    fake = cloud_fixtures.api_execution(
        case, credentials, current_transcriber_application, tmp_path, monkeypatch
    )
    repair_cloud.run(
        case.worker, case.store.job(case.job["id"]), case.store.recording(case.recording["id"])
    )
    case.store.update_job(case.job["id"], state="interrupted")
    owner = case.store.job(case.job["id"])["params"]["ucloud_run"]["job_id"]
    uploaded = list(fake.uploaded)
    calls = list(fake.inference_calls)
    response = case.client.post(f"/api/jobs/{case.job['id']}/retry", json={"replace": False})
    assert response.status_code == 200, response.text
    case.client.app.state.queue.process(case.job["id"])
    assert case.store.job(case.job["id"])["params"]["ucloud_run"]["job_id"] == owner
    assert len(fake.submissions) == 1
    assert fake.uploaded == uploaded and fake.inference_calls == calls
