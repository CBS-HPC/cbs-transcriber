"""HF Activity admission and existing library-boundary integration, without external work."""

import json

import pytest
from admission_support import checked_connection
from fastapi.testclient import TestClient

from cbs_transcriber import app as app_module
from cbs_transcriber import enhancement, huggingface_access, huggingface_tasks, providers
from cbs_transcriber.ucloud_native import MIG_PRODUCT


@pytest.fixture
def setup(client, monkeypatch):
    manager = client.app.state.huggingface_tasks
    manager.freeze_cloud_plan = lambda: {"status": "needs_setup"}

    def refuse(*args, **kwargs):
        pytest.fail("HF HTTP admission and polling must never perform external work inline")

    monkeypatch.setattr(huggingface_access, "check_token", refuse)
    monkeypatch.setattr(manager, "_child", refuse)
    monkeypatch.setattr(app_module.enhancement_api, "local_speaker_status", refuse)
    return client, manager


def task_id(response):
    return response.json()["preparation_task"]["id"].removeprefix("hf-setup:")


def test_primary_save_queues_redacted_activity_without_recording_jobs_or_new_settings(setup):
    client, manager = setup
    store = client.app.state.store
    before = store.all("SELECT * FROM settings ORDER BY id")
    saved = client.put("/api/huggingface-settings", json={"hf_token": "hf_SYNTHETIC"})
    assert saved.status_code == 202
    assert saved.json()["has_token"] and saved.json()["token_status"] == "unchecked"
    card = saved.json()["preparation_task"]
    assert card["kind"] == "hf_setup" and card["state"] == "queued"
    assert card["cancellable"] and not card["retryable"]
    assert client.get("/api/jobs").json() == [card]
    assert client.get("/api/huggingface-settings").json() == saved.json()
    assert store.all("SELECT * FROM settings ORDER BY id") == before
    assert not store.all("SELECT * FROM jobs")
    public = json.dumps([saved.json(), manager.activity(), manager.status()])
    assert "hf_SYNTHETIC" not in public
    assert huggingface_access.token_fingerprint("hf_SYNTHETIC") not in public


def test_no_change_check_and_repeat_save_deduplicate_but_forget_stops_existing(setup):
    client, manager = setup
    first = client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
    identifier = task_id(first)
    assert client.put("/api/huggingface-settings", json={}).status_code == 200
    checked = client.post("/api/huggingface-settings/check", json={})
    assert checked.status_code == 202 and task_id(checked) == identifier
    assert (
        task_id(client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"}))
        == identifier
    )
    assert len(manager.activity()) == 1
    unsaved = client.post("/api/huggingface-settings/check", json={"hf_token": "hf_OTHER"})
    assert unsaved.status_code == 409 and "Save" in unsaved.json()["detail"]
    assert client.app.state.credentials.get(enhancement.HF_SCOPE) == "hf_FIRST"
    assert manager.activity()[0]["state"] == "queued"
    forgotten = client.put("/api/huggingface-settings", json={"hf_token": ""})
    assert forgotten.status_code == 200 and not forgotten.json()["has_token"]
    assert manager.activity()[0]["state"] == "cancelled"
    missing = client.post("/api/huggingface-settings/check", json={})
    assert missing.status_code == 409 and "Save" in missing.json()["detail"]
    assert len(manager.activity()) == 1


def test_stop_and_retry_use_independent_activity_ids_and_reject_stale_plan(setup):
    client, manager = setup
    identifier = task_id(client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"}))
    stopped = client.post(f"/api/huggingface-tasks/{identifier}/cancel")
    assert stopped.status_code == 200 and stopped.json()["state"] == "cancelled"
    retried = client.post(f"/api/huggingface-tasks/{identifier}/retry")
    assert retried.status_code == 202 and retried.json()["id"] != "hf-setup:" + identifier
    successor = retried.json()["id"].removeprefix("hf-setup:")
    client.post(f"/api/huggingface-tasks/{successor}/cancel")
    manager.freeze_cloud_plan = lambda: {"status": "disabled"}
    assert client.post(f"/api/huggingface-tasks/{successor}/retry").status_code == 409
    assert client.post("/api/huggingface-tasks/not-a-uuid/retry").status_code == 422
    assert client.post(f"/api/huggingface-tasks/{'0' * 32}/cancel").status_code == 404


def test_processing_alias_invalidates_changed_plan_without_starting_setup(setup, monkeypatch):
    client, manager = setup
    saved = client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
    changed = client.put("/api/enhancement-settings", json={"download_models": False})
    assert changed.status_code == 200
    assert manager.activity()[0]["state"] == "cancelled" and len(manager.activity()) == 1
    # Even an alias that changed a session credential before failing must invalidate it.
    current = client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
    assert task_id(current) != task_id(saved)

    def fail(*args, **kwargs):
        raise ValueError("Synthetic configuration failure")

    monkeypatch.setattr(app_module, "save_local_settings", fail)
    partial = client.put(
        "/api/enhancement-settings", json={"hf_token": "hf_CHANGED", "download_models": True}
    )
    assert partial.status_code == 400
    assert client.app.state.credentials.get(enhancement.HF_SCOPE) == "hf_CHANGED"
    assert all(item["state"] == "cancelled" for item in manager.activity())
    assert manager.status()["token_status"] == "unchecked"


def test_capture_staging_and_switch_guard_share_hf_admission_fence(setup, monkeypatch, tmp_path):
    client, manager = setup
    store = client.app.state.store
    with client.app.state.deletions.staging():
        # A promoted capture correctly blocks concurrent HTTP mutations until release;
        # assert HF admission directly inside this already-exclusive staging context.
        with pytest.raises(huggingface_tasks.Conflict):
            manager.save("hf_FIRST")
        assert not client.app.state.credentials.get(enhancement.HF_SCOPE)
    client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
    with pytest.raises(huggingface_tasks.Conflict):
        with client.app.state.deletions.staging():
            pytest.fail("A source-destructive operation started during HF setup")
    project = client.post("/api/projects", json={"name": "Synthetic"}).json()
    moved = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(tmp_path / "moved")}
    )
    assert moved.status_code == 409
    switched = []
    client.app.state.switch_library = switched.append
    target = type("Choice", (), {"path": store.root.parent / "other-library"})()
    monkeypatch.setattr(app_module.libraries, "select", lambda *args, **kwargs: target)
    blocked = client.post("/api/libraries/open", json={"path": str(target.path)})
    assert blocked.status_code == 409 and not switched
    manager.cancel(manager.activity()[0]["id"])
    switched_response = client.post("/api/libraries/open", json={"path": str(target.path)})
    assert switched_response.status_code == 202 and switched == [target]
    assert client.put("/api/huggingface-settings", json={"hf_token": "hf_OTHER"}).status_code == 409


def test_lifespan_drains_hf_before_queue_stop_under_serving_lease(
    tmp_path, credentials, monkeypatch
):
    order = []
    monkeypatch.setattr(app_module.JobQueue, "start", lambda self: order.append("queue-start"))
    monkeypatch.setattr(app_module.JobQueue, "stop", lambda self: order.append("queue-stop"))
    actual_close = huggingface_tasks.HuggingFaceTasks.close

    def closed(self):
        assert self.store._lease_depth
        order.append("hf-close")
        actual_close(self)

    monkeypatch.setattr(huggingface_tasks.HuggingFaceTasks, "close", closed)
    app = app_module.create_app(tmp_path, credentials=credentials, start_worker=True)
    with TestClient(app):
        assert order == ["queue-start"]
    assert order == ["queue-start", "hf-close", "queue-stop"]
    assert not app.state.store._lease_depth


def test_mvp_hf_plan_cannot_bypass_held_cloud_feature(tmp_path, credentials, monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("MVP HF setup must not inspect/admit a cloud preparation plan")

    monkeypatch.setattr(app_module.runtime_preparation, "huggingface_plan", refuse, raising=False)
    app = app_module.create_app(
        tmp_path, credentials=credentials, start_worker=False, release="mvp"
    )
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        response = client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
        assert response.status_code == 202
        task = app.state.huggingface_tasks._tasks[task_id(response)]
        assert task.plan["cloud"] == {"status": "disabled"}


def test_real_cloud_configuration_plan_is_offline_during_save_and_polling(client, monkeypatch):
    """Exercise the real app callback/plan adapter, without a mocked freeze callback."""
    store, credentials = client.app.state.store, client.app.state.credentials
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="whisperx/large-v3",
        enabled=True,
        ucloud_transcriber={
            "project_id": "synthetic-project",
            "storage_path": "/drive/cbs",
            "application": {"name": "transcriber-batch", "version": "1.12"},
            "product": dict(MIG_PRODUCT),
        },
    )
    saved = providers.save_profile(
        store, credentials, "Synthetic Runtime", settings, "synthetic-control", remember=False
    )
    checked_connection(store, credentials, saved["id"])
    providers.set_default_provider(store, saved["id"], credentials)

    def refuse(*args, **kwargs):
        pytest.fail("Save/GET must not contact UCloud/HF or scan model caches")

    monkeypatch.setattr(app_module.runtime_preparation.native, "client_for", refuse)
    monkeypatch.setattr(huggingface_access, "check_token", refuse)
    monkeypatch.setattr(app_module.enhancement_api, "local_speaker_files", refuse)
    response = client.put("/api/huggingface-settings", json={"hf_token": "hf_SYNTHETIC"})
    assert response.status_code == 202, response.text
    task = client.app.state.huggingface_tasks._tasks[task_id(response)]
    assert task.plan["cloud"]["status"] == "ready"
    plan = task.plan["cloud"]["model_plan"]
    assert plan["policy"] == "cbs-runtime-model-plan-v1"
    assert {"coarse", "da", "en", "speakers", "whisper-large-v3"} <= set(plan["models"])
    assert any(profile["provider_id"] == saved["id"] for profile in plan["profiles"])
    assert client.get("/api/huggingface-settings").json() == response.json()
    assert client.get("/api/jobs").json() == [response.json()["preparation_task"]]
    assert not store.all("SELECT * FROM runtime_preparations")
    assert not store.all("SELECT * FROM jobs")
