"""HF setup is an Activity owner; synthetic task projections never run inference."""

import copy
from pathlib import Path

import pytest
import test_browser
from playwright.sync_api import expect
from test_browser import LocalApp
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_first_use import native_account as native_account

from cbs_transcriber.enhancement import HF_SCOPE

pytestmark = pytest.mark.browser
TASK_ID = "a" * 32


def task_row():
    return {
        "id": "hf-setup:" + TASK_ID,
        "kind": "hf_setup",
        "recording_id": None,
        "project_id": None,
        "recording_name": "Hugging Face setup",
        "state": "running",
        "stage": "ucloud",
        "progress": 0.75,
        "message": "Waiting for configured UCloud models.",
        "created": "2000-01-01T00:00:00+00:00",
        "updated": "2000-01-01T00:00:00+00:00",
        "cancellable": True,
        "retryable": False,
        "stage_outcomes": [
            {
                "stage": "access",
                "label": "Hugging Face access",
                "outcome": "completed",
                "message": "Access accepted.",
            },
            {
                "stage": "local_public",
                "label": "Local word-timing models",
                "outcome": "skipped",
                "message": "Downloads disabled in Settings.",
            },
            {
                "stage": "ucloud",
                "label": "UCloud models",
                "outcome": "running",
                "message": "Using the existing preparation.",
            },
        ],
        "remote_child": {
            "id": "runtime-preparation:" + "b" * 32,
            "runtime_preparation_id": "b" * 32,
            "created_by_task": False,
            "state": "running",
            "retained": True,
        },
        "continuation": "server_lifetime",
    }


def test_hf_activity_progress_stop_and_retry_use_the_setup_owner(page, local_app):
    task = task_row()
    tasks = [task]
    calls = []
    page.route("**/api/jobs", lambda route: route.fulfill(json=tasks))

    def action(route):
        calls.append(route.request.url)
        if route.request.url.endswith("/cancel"):
            task.update(
                state="running",
                cancellable=False,
                retryable=False,
                message="Stopping setup; waiting for the accepted request.",
            )
        else:
            successor = copy.deepcopy(task)
            successor.update(
                id="hf-setup:" + "c" * 32,
                state="queued",
                stage="queued",
                progress=0,
                stage_outcomes=[],
                remote_child=None,
                cancellable=True,
                retryable=False,
                message="Setup queued.",
            )
            tasks.append(successor)
        route.fulfill(status=202, json=copy.deepcopy(tasks[-1]))

    page.route("**/api/huggingface-tasks/*/*", action)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator(f'[data-hf-setup="{TASK_ID}"]')
    expect(card).to_contain_text("Model access and preparation")
    expect(card).to_contain_text("Downloads disabled in Settings")
    expect(card).to_contain_text("existing UCloud preparation with independent controls")
    expect(card.locator("progress")).to_have_attribute("value", "0.75")
    expect(card.locator("[data-activity-remove]")).to_have_count(0)
    card.get_by_role("button", name="Stop task: setup", exact=True).click()
    expect(card).to_contain_text("Stopping setup; waiting for the accepted request")
    expect(card.get_by_role("button", name="Retry setup", exact=True)).to_have_count(0)
    assert calls == [local_app.url + f"/api/huggingface-tasks/{TASK_ID}/cancel"]
    task.update(state="cancelled", retryable=True, message="Setup stopped.")
    task["stage_outcomes"][-1].update(outcome="cancelled", message="Accepted request drained.")
    page.evaluate("pollJobs()")
    expect(card.get_by_role("button", name="Retry setup", exact=True)).to_be_enabled()
    card.get_by_role("button", name="Retry setup", exact=True).click()
    successor = page.locator(f'[data-hf-setup="{"c" * 32}"]')
    expect(successor.get_by_role("button", name="Stop setup", exact=True)).to_be_enabled()
    assert calls[-1] == local_app.url + f"/api/huggingface-tasks/{TASK_ID}/retry"
    assert not local_app.server.config.app.state.store.all("SELECT id FROM jobs")


def test_hf_unavailable_stage_stays_accessible_after_clear_finished(page, local_app):
    task = task_row()
    task.update(
        state="failed", cancellable=False, retryable=True, message="Access could not be checked."
    )
    task["stage_outcomes"] = [
        {
            "stage": "access",
            "label": "Hugging Face access",
            "outcome": "unavailable",
            "message": "Synthetic service unavailable.",
        }
    ]
    page.route("**/api/jobs", lambda route: route.fulfill(json=[task]))
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    expect(page.locator(f'[data-hf-setup="{TASK_ID}"]')).to_contain_text(
        "Synthetic service unavailable"
    )
    page.locator("#activity-clear").click()
    retained = page.locator("[data-retained-activity]")
    expect(retained).to_be_visible()
    retained.locator("summary").click()
    expect(retained.get_by_role("button", name="Retry setup")).to_be_visible()


@pytest.fixture
def inert_app(tmp_path, monkeypatch):
    factory = test_browser.create_app
    monkeypatch.setattr(
        test_browser,
        "create_app",
        lambda root, **kwargs: factory(root, start_worker=False, **kwargs),
    )
    app = LocalApp(tmp_path / "library")
    app.start()
    monkeypatch.setattr(
        app.server.config.app.state.huggingface_tasks,
        "freeze_cloud_plan",
        lambda: {"status": "needs_setup"},
    )
    try:
        yield app
    finally:
        app.stop()


def test_hf_settings_save_and_saved_token_recheck_queue_activity_without_inline_work(
    page, inert_app
):
    calls = []
    page.on(
        "request",
        lambda request: (
            calls.append((request.method, request.url, request.post_data_json))
            if request.method in {"POST", "PUT"}
            else None
        ),
    )
    page.goto(inert_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="Hugging Face", exact=True).click()
    expect(page.locator("#hf-settings-check")).to_be_disabled()
    page.locator("#hf-settings-token").fill("hf_synthetic-settings-token")
    expect(page.locator("#hf-settings-check")).to_be_disabled()
    page.locator("#hf-settings-save").click()
    expect(page.locator("#hf-settings-token")).to_have_value("")
    expect(page.locator("#hf-settings-activity")).to_be_visible()
    expect(page.locator("#hf-settings-check")).to_be_enabled()
    page.set_viewport_size({"width": 390, "height": 1000})
    dialog = page.locator("#settings-dialog")
    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    screenshot = Path("test-results/morning-onboarding/hf-settings-390.png")
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshot))
    page.set_viewport_size({"width": 1440, "height": 1000})
    assert (
        inert_app.server.config.app.state.credentials.get(HF_SCOPE) == "hf_synthetic-settings-token"
    )
    page.locator("#hf-settings-check").click()
    expect(page.locator("#hf-settings-check")).to_be_enabled()
    check = next(body for method, url, body in calls if url.endswith("/huggingface-settings/check"))
    assert check["hf_token"] is None
    page.locator("#hf-settings-token").fill("hf_new-unsaved-draft")
    expect(page.locator("#hf-settings-check")).to_be_disabled()
    page.locator("#hf-settings-token").fill("")
    page.locator("#hf-settings-activity").click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("[data-hf-setup]")).to_have_count(1)
    expect(page.locator("[data-hf-setup]")).to_contain_text("queued")
    storage = page.evaluate("() => JSON.stringify({...localStorage})")
    assert "hf_synthetic-settings-token" not in storage
    assert not inert_app.server.config.app.state.store.all("SELECT id FROM jobs")


def test_first_use_native_and_hf_save_create_one_inert_activity_owner(
    page, inert_app, native_account
):
    writes = []
    page.on(
        "request",
        lambda request: writes.append(request.url) if request.method in {"POST", "PUT"} else None,
    )
    page.goto(inert_app.url)
    page.locator("#first-use-ucloud-key").fill("synthetic-ucloud-key")
    page.locator("#first-use-hf-token").fill("hf_synthetic-onboarding-token")
    page.locator("#first-use-save").click()
    expect(page.locator("#first-use-setup")).to_be_hidden()
    app = inert_app.server.config.app
    assert app.state.credentials.get(HF_SCOPE) == "hf_synthetic-onboarding-token"
    assert (
        app.state.credentials.get("ucloud-control:https://cloud.sdu.dk") == "synthetic-ucloud-key"
    )
    assert native_account == ["large-v3"]
    assert writes == [
        inert_app.url + "/api/providers/discover-ucloud",
        inert_app.url + "/api/providers",
        inert_app.url + "/api/huggingface-settings",
    ]
    page.locator("#activity-open").click()
    expect(page.locator("[data-hf-setup]")).to_have_count(1)
    expect(page.locator("[data-hf-setup]")).to_contain_text("queued")
    assert not app.state.store.all("SELECT id FROM jobs")
    assert page.evaluate("key => localStorage.getItem(key)", "cbs-first-use-completed-v1") == "1"
