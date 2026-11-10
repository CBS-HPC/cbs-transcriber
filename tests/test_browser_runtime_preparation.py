"""Standalone runtime-preparation UI contract; every cloud response is synthetic."""

import pytest
import test_browser
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_processing_replies import rules_status, watch_json_replies

pytestmark = pytest.mark.browser
OPERATION = "a" * 32
CONNECTION = "c" * 64


@pytest.fixture(autouse=True)
def contract_app_has_no_workers(monkeypatch):
    create = test_browser.create_app

    def app(*args, **kwargs):
        return create(*args, **kwargs, start_worker=False)

    monkeypatch.setattr(test_browser, "create_app", app)


def operation(**updates):
    return {
        "id": "runtime-preparation:" + OPERATION,
        "runtime_preparation_id": OPERATION,
        "kind": "runtime-preparation",
        "recording_id": None,
        "project_id": None,
        "recording_name": "UCloud runtime preparation",
        "members": [],
        "state": "queued",
        "progress": 0,
        "message": "Synthetic base runtime preparation queued",
        "created": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-01T00:00:01+00:00",
        "can_cancel": True,
        "can_retry": False,
        "can_remove": False,
        "can_cleanup": False,
        "can_reconcile": False,
        "needs_attention": False,
        "archived": False,
        "run_name": "Synthetic runtime preparation " + OPERATION,
        "ucloud_job_id": None,
        "ucloud_job_url": None,
        "ucloud_state": None,
        "ucloud_submission_uncertain": False,
        "ucloud_cleanup_state": None,
        **updates,
    }


@pytest.fixture
def preparation_ui(page, local_app):
    state = {
        "settings": {
            "runtime_python": "",
            "device": "cpu",
            "language": "da",
            "download_models": False,
            "prepare_ucloud_models": True,
        },
        "prepared": False,
        "jobs": [],
        "checks": [],
        "prepares": [],
        "writes": [],
        "pending_writes": [],
        "hold_write": False,
        "hold_check": False,
        "pending_checks": [],
        "hold_prepare": False,
        "pending_prepares": [],
        "prepare_ready": False,
        "connection": CONNECTION,
        "connected": True,
        "prepare_error": None,
        "lose_prepare_reply": False,
        "activity": {"cleared_at": None, "hidden": {}},
        "actions": [],
        "pending_actions": [],
        "hold_action": None,
        "hidden": {},
        "wrong_paths": [],
        "reconcile_fingerprint": "e" * 64,
        "reconcile_operation": None,
    }

    def settings(route):
        if route.request.method == "PUT":
            state["writes"].append(route.request.post_data_json)
            if state["hold_write"]:
                state["pending_writes"].append(route)
                page.evaluate("window.preparationWriteHeld = true")
                return
            state["settings"].update(route.request.post_data_json)
        route.fulfill(json=state["settings"])

    def check(route):
        state["checks"].append(route.request.post_data_json)
        page.evaluate("count => window.preparationChecks = count", len(state["checks"]))
        if state["hold_check"] and len(state["checks"]) > 1:
            state["pending_checks"].append(route)
            page.evaluate("window.preparationCheckHeld = true")
            return
        route.fulfill(json={"prepared": state["prepared"], "runtime_id": "synthetic-base"})

    def prepare(route):
        state["prepares"].append(route.request.post_data_json)
        if state["prepare_error"]:
            route.fulfill(status=409, json={"detail": state["prepare_error"]})
            return
        if state["prepare_ready"]:
            route.fulfill(
                json={"prepared": True, "runtime_id": "synthetic-base", "operation": None}
            )
            return
        duplicate = bool(state["jobs"])
        if not duplicate:
            state["jobs"].append(operation())
        if state["lose_prepare_reply"]:
            route.fulfill(status=503, json={"detail": "Synthetic preparation reply lost"})
            return
        reply = {
            "prepared": False,
            "runtime_id": "synthetic-base",
            "operation": state["jobs"][0],
            "deduplicated": duplicate,
        }
        if state["hold_prepare"]:
            state["pending_prepares"].append((route, reply))
            page.evaluate("window.preparationAcceptHeld = true")
            return
        route.fulfill(status=202, json=reply)

    def action(route):
        name = route.request.url.rsplit("/", 1)[-1]
        identifier = route.request.url.rsplit("/", 2)[-2]
        chosen = next(
            item for item in state["jobs"] if item["runtime_preparation_id"] == identifier
        )
        state["actions"].append(
            {"action": name, "id": identifier, "body": route.request.post_data_json}
        )
        if state["hold_action"] == name:
            state["pending_actions"].append(route)
            page.evaluate("window.preparationActionHeld = true")
            return
        if name == "hide":
            state["hidden"][identifier] = chosen["updated"]
            route.fulfill(json={"hidden": True})
        elif name == "reconcile":
            if route.request.post_data_json["confirm_not_created"]:
                chosen.update(
                    ucloud_submission_uncertain=False,
                    can_reconcile=False,
                    can_retry=True,
                    can_remove=True,
                    needs_attention=False,
                )
                route.fulfill(json={"status": "not-found", "operation": chosen})
            else:
                route.fulfill(
                    json={
                        "status": "not-found",
                        "operation": state["reconcile_operation"] or chosen,
                        "requires_confirmation": True,
                        "fingerprint": state["reconcile_fingerprint"],
                    }
                )
        elif name == "retry":
            successor = operation(
                id="runtime-preparation:" + "b" * 32,
                runtime_preparation_id="b" * 32,
                run_name="Synthetic successor " + "b" * 32,
            )
            state["jobs"].append(successor)
            route.fulfill(json=successor)
        elif name == "cleanup":
            chosen.update(
                ucloud_cleanup_state="trashed",
                can_cleanup=False,
                can_remove=True,
                needs_attention=False,
            )
            route.fulfill(json=chosen)
        else:
            route.fulfill(json=chosen)

    def clear(route):
        state["activity"]["cleared_at"] = "2099-01-01T00:00:00+00:00"
        route.fulfill(json=state["activity"])

    def wrong(route):
        state["wrong_paths"].append(route.request.url)
        route.fulfill(status=400, json={"detail": "Wrong owner endpoint"})

    page.route("**/api/enhancement-settings", settings)
    page.route(
        "**/api/local-runtime",
        lambda route: route.fulfill(
            json={"status": {"error": "Synthetic local runtime", "gpu": {}}}
        ),
    )
    page.route(
        "**/api/runtime-batches/settings", lambda route: route.fulfill(json={"max_allocations": 2})
    )
    page.route(
        "**/api/enhancement-api/status",
        lambda route: route.fulfill(
            json=rules_status(15)
            | {
                "connected": state["connected"],
                "message": "Synthetic disconnected UCloud connection",
                "provider_id": "p" * 32,
                "provider_name": "Synthetic UCloud",
                "project_id": "synthetic-project",
                "storage_path": "/drive/synthetic",
                "runtime_id": "synthetic-base",
                "connection_fingerprint": state["connection"],
            }
        ),
    )
    page.route("**/api/enhancement-api/check", check)
    page.route("**/api/enhancement-api/prepare", prepare)
    page.route(
        "**/api/jobs",
        lambda route: route.fulfill(
            json=[
                item
                for item in state["jobs"]
                if state["hidden"].get(item["runtime_preparation_id"]) != item["updated"]
            ]
        ),
    )
    page.route("**/api/activity", lambda route: route.fulfill(json=state["activity"]))
    page.route("**/api/activity/clear", clear)
    page.route("**/api/activity/hide", wrong)
    page.route("**/api/jobs/*/*", wrong)
    page.route("**/api/runtime-preparations/*/*", action)
    return state


def open_ucloud(page, local_app):
    page.goto(local_app.url)
    page.wait_for_function("() => state.providers.length > 0")
    page.locator("#settings-open").click()
    page.locator("#tab-ucloud").click()
    expect(page.locator("#enhance-cloud-check")).to_be_enabled()
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("prepared")


@pytest.mark.parametrize("width", [390, 640, 1440])
def test_runtime_preparation_controls_stack_in_ucloud_settings(
    page, local_app, preparation_ui, width
):
    page.set_viewport_size({"width": width, "height": 900})
    open_ucloud(page, local_app)
    panel = page.locator("#runtime-preparation-panel")
    expect(panel).to_be_visible()
    expect(page.get_by_role("tab", name="Local Processing", exact=True)).to_be_visible()
    label = panel.locator("label:has(#enhance-prepare-ucloud)")
    help_text = panel.locator("#enhance-cloud-check-help")
    check = panel.locator("#enhance-cloud-check")
    help_text.scroll_into_view_if_needed()
    a, b, c = check.bounding_box(), label.bounding_box(), help_text.bounding_box()
    assert b["y"] >= a["y"] + a["height"] - 1
    assert c["y"] >= b["y"] + b["height"] - 1
    assert help_text.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    expect(label).to_contain_text("Prepare UCloud models automatically when needed")
    assert preparation_ui["checks"] == [{}] and not preparation_ui["prepares"]
    if width == 640:
        page.screenshot(path="test-results/ucloud-runtime-settings-layout.png")


def test_cloud_status_survives_provider_draft_edits_without_starting_preparation(
    page, local_app, preparation_ui
):
    held = []

    def hold(route):
        held.append(route)
        page.evaluate("window.cloudStatusHeld = true")

    page.route("**/api/enhancement-api/status", hold)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.locator("#tab-ucloud").click()
    page.wait_for_function("() => window.cloudStatusHeld")
    page.locator("#api-key").fill("synthetic-provider-draft")
    held[0].fulfill(
        json=rules_status(15)
        | {
            "connected": True,
            "provider_name": "Saved UCloud",
            "project_id": "saved-project",
            "storage_path": "/drive/saved",
            "runtime_id": "synthetic-base",
            "connection_fingerprint": CONNECTION,
        }
    )
    expect(page.locator("#enhance-cloud-connection")).to_contain_text("saved-project")
    expect(page.locator("#enhance-cloud-check")).to_be_enabled()
    expect(page.locator("#api-key")).to_have_value("synthetic-provider-draft")
    assert preparation_ui["checks"] == [{}] and not preparation_ui["prepares"]


@pytest.mark.parametrize("consent,prepared", [(True, False), (False, False), (True, True)])
def test_only_manual_unprepared_check_with_saved_consent_creates_activity(
    page, local_app, preparation_ui, consent, prepared
):
    ui = preparation_ui
    ui["settings"]["prepare_ucloud_models"] = consent
    ui["prepared"] = prepared
    open_ucloud(page, local_app)
    expect(page.locator("#enhance-cloud-check-help")).to_contain_text("baseline public caches")
    expect(page.locator("#enhance-cloud-check-help")).to_contain_text(
        "Additional selected ASR profiles and gated speaker models"
    )
    assert ui["checks"] == [{}] and not ui["prepares"]
    page.locator("#enhance-cloud-check").click()
    page.wait_for_function("() => !document.querySelector('#enhance-cloud-check').disabled")
    assert ui["checks"] == [{}, {}]
    if consent and not prepared:
        assert ui["prepares"] == [{"connection_fingerprint": CONNECTION}]
        expect(page.locator("#settings-dialog")).to_be_visible()
        page.get_by_role("button", name="View preparation in Activity", exact=True).click()
        expect(page.locator("#activity")).to_be_visible()
        card = page.locator(f'[data-runtime-preparation="{OPERATION}"]')
        expect(card).to_have_count(1)
        expect(card.locator(".job-kind")).to_have_text("UCloud runtime preparation")
        expect(card.locator('a[href*="/recordings/"]')).to_have_count(0)
    else:
        assert not ui["prepares"]
        expect(page.locator("#settings-dialog")).to_be_visible()


def test_standalone_preparation_card_uses_its_own_owner_controls(page, local_app, preparation_ui):
    ui = preparation_ui
    ui["jobs"] = [operation()]
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_have_count(1)
    expect(card.locator(".job-kind")).to_have_text("UCloud runtime preparation")
    expect(card).to_contain_text("baseline public caches")
    expect(card).to_contain_text(
        "Additional selected ASR profiles and gated speaker models have separate readiness checks"
    )
    expect(card.locator('a[href*="/recordings/"]')).to_have_count(0)
    expect(card.get_by_role("button", name="Stop runtime preparation", exact=True)).to_be_visible()


def finish_check(page):
    page.wait_for_function("() => !document.querySelector('#enhance-cloud-check').disabled")


@pytest.mark.parametrize("save_failed", [False, True])
def test_manual_preparation_waits_for_the_accepted_checkbox_save(
    page, local_app, preparation_ui, save_failed
):
    ui = preparation_ui
    ui["settings"]["prepare_ucloud_models"] = False
    open_ucloud(page, local_app)
    ui["hold_write"] = True
    page.locator("#enhance-prepare-ucloud").check()
    page.wait_for_function("() => window.preparationWriteHeld === true")
    page.locator("#enhance-cloud-check").click()
    assert ui["checks"] == [{}] and not ui["prepares"]
    [write] = ui["pending_writes"]
    if save_failed:
        write.fulfill(status=503, json={"detail": "Synthetic saved consent failed"})
    else:
        ui["settings"].update(write.request.post_data_json)
        write.fulfill(json=ui["settings"])
    finish_check(page)
    if save_failed:
        assert not ui["prepares"] and not ui["jobs"]
        expect(page.locator("#enhance-cloud-check-result")).to_contain_text("saved consent failed")
    else:
        assert ui["prepares"] == [{"connection_fingerprint": CONNECTION}]
        expect(page.locator("#enhance-cloud-activity")).to_be_visible()


def test_opt_out_while_check_is_pending_prevents_preparation(page, local_app, preparation_ui):
    ui = preparation_ui
    open_ucloud(page, local_app)
    ui["hold_check"] = True
    page.locator("#enhance-cloud-check").click()
    page.wait_for_function("() => window.preparationCheckHeld === true")
    page.locator("#enhance-prepare-ucloud").uncheck()
    expect(page.locator("#enhance-cloud-save-status")).to_have_text("Saved for new jobs.")
    ui["pending_checks"][0].fulfill(json={"prepared": False, "runtime_id": "synthetic-base"})
    finish_check(page)
    assert not ui["prepares"] and not ui["jobs"]
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("Enable and save")


def test_departed_check_cannot_start_preparation_or_replace_newer_provider_draft(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    open_ucloud(page, local_app)
    watch_json_replies(page, "enhancement-api/check", "lateCheckRead")
    ui["hold_check"] = True
    page.locator("#enhance-cloud-check").click()
    page.wait_for_function("() => window.preparationCheckHeld === true")
    page.keyboard.press("Escape")
    page.locator("#settings-open").click()
    page.locator("#api-key").fill("synthetic-newer-draft")
    ui["pending_checks"][0].fulfill(json={"prepared": False, "runtime_id": "synthetic-base"})
    page.wait_for_function("() => window.lateCheckRead === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    assert not ui["prepares"] and not ui["jobs"]
    expect(page.locator("#api-key")).to_have_value("synthetic-newer-draft")


def test_old_check_cannot_enable_reopened_ucloud_before_settings_load(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    open_ucloud(page, local_app)
    watch_json_replies(page, "enhancement-api/check", "oldReopenCheckRead")
    ui["hold_check"] = True
    page.locator("#enhance-cloud-check").click()
    page.wait_for_function("() => window.preparationCheckHeld === true")
    held_settings = []

    def hold_settings(route):
        held_settings.append(route)
        page.evaluate("window.reopenedSettingsHeld = true")

    page.route("**/api/enhancement-settings", hold_settings)
    page.locator("#tab-openai").click()
    page.locator("#tab-ucloud").click()
    page.wait_for_function("() => window.reopenedSettingsHeld")
    ui["pending_checks"][0].fulfill(json={"prepared": False, "runtime_id": "synthetic-base"})
    page.wait_for_function("() => window.oldReopenCheckRead === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#enhance-cloud-check")).to_be_disabled()
    assert not ui["prepares"]
    ui["hold_check"] = False
    held_settings[0].fulfill(json=ui["settings"])
    expect(page.locator("#enhance-cloud-check")).to_be_enabled()


def test_departed_manual_check_cannot_prepare_a_newly_disconnected_connection(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    open_ucloud(page, local_app)
    watch_json_replies(page, "enhancement-api/check", "disconnectedCheckRead")
    ui["hold_check"] = True
    page.locator("#enhance-cloud-check").click()
    page.wait_for_function("() => window.preparationCheckHeld === true")
    ui["connected"] = False
    page.locator("#tab-openai").click()
    page.locator("#tab-ucloud").click()
    expect(page.locator("#enhance-cloud-connection")).to_have_text(
        "Synthetic disconnected UCloud connection"
    )
    ui["pending_checks"][0].fulfill(json={"prepared": False, "runtime_id": "synthetic-base"})
    page.wait_for_function("() => window.disconnectedCheckRead === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#enhance-cloud-check")).to_be_disabled()
    expect(page.locator("#enhance-cloud-activity")).to_be_hidden()
    assert not ui["prepares"] and not ui["jobs"]


def test_accepted_late_preparation_stays_in_activity_without_reopening_old_settings(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    ui["hold_prepare"] = True
    open_ucloud(page, local_app)
    watch_json_replies(page, "enhancement-api/prepare", "latePrepareRead")
    page.locator("#enhance-cloud-check").click()
    page.wait_for_function("() => window.preparationAcceptHeld === true")
    page.keyboard.press("Escape")
    page.locator("#settings-open").click()
    page.locator("#api-key").fill("synthetic-newer-draft")
    [(request, reply)] = ui["pending_prepares"]
    request.fulfill(status=202, json=reply)
    page.wait_for_function("() => window.latePrepareRead === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#api-key")).to_have_value("synthetic-newer-draft")
    expect(page.locator("#enhance-cloud-activity")).to_be_hidden()
    page.keyboard.press("Escape")
    page.locator("#activity-open").click()
    expect(page.locator(f'[data-runtime-preparation="{OPERATION}"]')).to_have_count(1)


def test_now_prepared_response_creates_no_fake_activity_owner(page, local_app, preparation_ui):
    ui = preparation_ui
    ui["prepare_ready"] = True
    open_ucloud(page, local_app)
    page.locator("#enhance-cloud-check").click()
    finish_check(page)
    assert len(ui["prepares"]) == 1 and not ui["jobs"]
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("is prepared")
    expect(page.locator("#enhance-cloud-activity")).to_be_hidden()


def test_lost_preparation_reply_recovers_activity_and_explicit_recheck_deduplicates(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    ui["lose_prepare_reply"] = True
    open_ucloud(page, local_app)
    page.locator("#enhance-cloud-check").click()
    finish_check(page)
    assert len(ui["prepares"]) == 1 and len(ui["jobs"]) == 1
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("Check Activity")
    expect(page.locator("#enhance-cloud-activity")).to_be_hidden()
    expect(page.locator(f'[data-runtime-preparation="{OPERATION}"]')).to_have_count(1)
    ui["lose_prepare_reply"] = False
    page.locator("#enhance-cloud-check").click()
    finish_check(page)
    assert len(ui["prepares"]) == 2 and len(ui["jobs"]) == 1
    expect(page.locator("#enhance-cloud-check-result")).to_contain_text("already in Activity")
    page.locator("#enhance-cloud-activity").click()
    expect(page.locator(f'[data-runtime-preparation="{OPERATION}"]')).to_have_count(1)


def test_manual_preparation_fails_closed_for_unknown_or_changed_authority(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    for condition in ("unknown", "missing-fingerprint", "saved-opt-out", "changed-connection"):
        ui["settings"]["prepare_ucloud_models"] = True
        ui.update(prepared=False, connection=CONNECTION, prepare_error=None, checks=[], prepares=[])
        if condition == "missing-fingerprint":
            ui["connection"] = None
        open_ucloud(page, local_app)
        if condition == "unknown":
            ui["prepared"] = None
        elif condition == "saved-opt-out":
            ui["settings"]["prepare_ucloud_models"] = False
        elif condition == "changed-connection":
            ui["prepare_error"] = "Synthetic connection changed. Reopen UCloud settings."
        page.locator("#enhance-cloud-check").click()
        finish_check(page)
        assert not ui["jobs"], condition
        assert len(ui["prepares"]) == (1 if condition == "changed-connection" else 0), condition
        expect(page.locator("#enhance-cloud-activity")).to_be_hidden()
        expect(page.locator("#settings-dialog")).to_be_visible()
    assert not ui["wrong_paths"]


def test_preparation_stop_is_single_owner_and_retry_shows_a_separate_successor(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    ui["jobs"] = [operation()]
    ui["hold_action"] = "cancel"
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator(f'[data-runtime-preparation="{OPERATION}"]')
    card.get_by_role("button", name="Stop task: runtime preparation", exact=True).click()
    page.wait_for_function("() => window.preparationActionHeld === true")
    expect(card.get_by_role("button", name="Stop runtime preparation", exact=True)).to_be_disabled()
    expect(
        card.get_by_role("button", name="Stop task: runtime preparation", exact=True)
    ).to_be_disabled()
    page.evaluate("pollJobs()")
    expect(card.get_by_role("button", name="Stop runtime preparation", exact=True)).to_be_disabled()
    assert ui["actions"] == [{"action": "cancel", "id": OPERATION, "body": {}}]
    ui["jobs"][0].update(
        state="cancelled",
        can_cancel=False,
        can_retry=True,
        can_remove=True,
        message="Synthetic preparation stopped",
    )
    ui["pending_actions"][0].fulfill(json=ui["jobs"][0])
    card.get_by_role("button", name="Retry preparation", exact=True).click()
    successor = page.locator('[data-runtime-preparation="' + "b" * 32 + '"]')
    expect(successor).to_have_count(1)
    expect(card).to_have_count(1)
    expect(
        successor.get_by_role("button", name="Stop runtime preparation", exact=True)
    ).to_be_visible()
    assert ui["actions"] == [
        {"action": "cancel", "id": OPERATION, "body": {}},
        {"action": "retry", "id": OPERATION, "body": {}},
    ]
    assert not ui["wrong_paths"]


def test_preparation_hide_and_clear_keep_cleanup_obligations(page, local_app, preparation_ui):
    ui = preparation_ui
    done = operation(
        id="runtime-preparation:" + "d" * 32,
        runtime_preparation_id="d" * 32,
        state="completed",
        progress=1,
        can_cancel=False,
        can_remove=True,
        can_retry=True,
    )
    ui["jobs"] = [
        operation(
            state="completed",
            progress=1,
            can_cancel=False,
            needs_attention=True,
            can_cleanup=True,
            ucloud_cleanup_state="pending",
            ucloud_job_url="https://cloud.example.invalid/jobs/synthetic",
        ),
        done,
    ]
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    done_card = page.locator('[data-runtime-preparation="' + "d" * 32 + '"]')
    pending = page.locator(f'[data-runtime-preparation="{OPERATION}"]')
    expect(pending.get_by_role("link", name="Open UCloud job")).to_have_attribute(
        "href", "https://cloud.example.invalid/jobs/synthetic"
    )
    expect(pending.get_by_role("button", name="Remove from Activity", exact=True)).to_have_count(0)
    ui["hold_action"] = "hide"
    done_card.get_by_role("button", name="Remove from Activity", exact=True).click()
    page.wait_for_function("() => window.preparationActionHeld === true")
    # The earlier view may still contain Retry until the next poll. It cannot
    # issue a second owner action while Hide is accepted and unresolved.
    done_card.get_by_role("button", name="Retry preparation", exact=True).evaluate(
        "button => button.click()"
    )
    page.evaluate("pollJobs()")
    expect(done_card.get_by_role("button", name="Retry preparation", exact=True)).to_be_disabled()
    assert ui["actions"] == [{"action": "hide", "id": "d" * 32, "body": {}}]
    ui["hidden"]["d" * 32] = done["updated"]
    ui["pending_actions"][0].fulfill(json={"hidden": True})
    expect(done_card).to_have_count(0)
    assert ui["actions"] == [{"action": "hide", "id": "d" * 32, "body": {}}]
    assert len(ui["jobs"]) == 2
    done["updated"] = "2026-01-01T00:00:02+00:00"
    page.evaluate("pollJobs()")
    expect(done_card).to_have_count(1)
    page.locator("#activity-clear").click()
    expect(done_card).to_have_count(0)
    retained = page.locator("[data-retained-activity]")
    expect(retained).to_have_count(1)
    retained.locator("summary").click()
    expect(pending).to_be_visible()
    expect(pending).to_contain_text("Shared runtime and models are kept")
    pending.get_by_role("button", name="Clean up preparation run files", exact=True).click()
    expect(pending).to_have_count(0)
    assert ui["actions"][-1] == {"action": "cleanup", "id": OPERATION, "body": {}}
    assert len(ui["jobs"]) == 2 and not ui["wrong_paths"]


def test_uncertain_preparation_requires_explicit_fresh_absence_confirmation(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    ui["jobs"] = [
        operation(
            state="interrupted",
            can_cancel=False,
            can_retry=True,
            can_reconcile=True,
            needs_attention=True,
            ucloud_submission_uncertain=True,
            run_name="Exact synthetic run < & 🐚",
        )
    ]
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator(f'[data-runtime-preparation="{OPERATION}"]')
    expect(card.get_by_role("button", name="Retry preparation", exact=True)).to_have_count(0)
    confirmations = []
    accept = False

    def confirm(dialog):
        confirmations.append(dialog.message)
        dialog.accept() if accept else dialog.dismiss()

    page.on("dialog", confirm)
    ui["reconcile_operation"] = operation(
        id="runtime-preparation:" + "b" * 32,
        runtime_preparation_id="b" * 32,
    )
    card.get_by_role("button", name="Find UCloud job", exact=True).click()
    expect(page.locator("#toast")).to_contain_text("absence check changed")
    assert not confirmations
    ui["reconcile_operation"] = None
    card.get_by_role("button", name="Find UCloud job", exact=True).click()
    expect(card.get_by_role("button", name="Find UCloud job", exact=True)).to_be_enabled()
    assert len(ui["actions"]) == 2 and ui["jobs"][0]["ucloud_submission_uncertain"]
    accept = True
    card.get_by_role("button", name="Find UCloud job", exact=True).click()
    expect(card.get_by_role("button", name="Retry preparation", exact=True)).to_be_visible()
    assert all("Exact synthetic run < & 🐚" in text for text in confirmations)
    assert ui["actions"] == [
        {"action": "reconcile", "id": OPERATION, "body": {"confirm_not_created": False}},
        {"action": "reconcile", "id": OPERATION, "body": {"confirm_not_created": False}},
        {"action": "reconcile", "id": OPERATION, "body": {"confirm_not_created": False}},
        {
            "action": "reconcile",
            "id": OPERATION,
            "body": {
                "confirm_not_created": True,
                "fingerprint": ui["reconcile_fingerprint"],
            },
        },
    ]
    assert not ui["wrong_paths"]


def test_archived_or_unidentified_preparation_has_no_remote_controls(
    page, local_app, preparation_ui
):
    ui = preparation_ui
    ui["jobs"] = [
        operation(
            state="historical",
            archived=True,
            can_cancel=True,
            can_retry=True,
            can_cleanup=True,
            can_reconcile=True,
            can_remove=True,
        ),
        operation(
            id="runtime-preparation:invalid",
            runtime_preparation_id="invalid",
            state="failed",
            can_cancel=True,
            can_retry=True,
            can_cleanup=True,
            can_reconcile=True,
            can_remove=True,
            needs_attention=True,
        ),
    ]
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    expect(page.locator("[data-runtime-preparation]")).to_have_count(2)
    expect(page.locator("[data-runtime-preparation-action]")).to_have_count(0)
    invalid = page.locator('[data-runtime-preparation="invalid"]')
    expect(invalid.get_by_role("button", name="Remove from Activity", exact=True)).to_have_count(0)
    assert not ui["actions"] and not ui["wrong_paths"]
