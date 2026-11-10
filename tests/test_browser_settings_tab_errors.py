"""Settings load ownership/errors with synthetic replies and memory credentials."""

import json

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser
TABS = [
    ("huggingface", "huggingface-settings", "hf"),
    ("processing", "enhancement-settings", "processing"),
]


def settings_response(tab):
    if tab == "huggingface":
        return {"has_token": False}
    return {
        "runtime_python": "",
        "device": "cpu",
        "language": "da",
        "download_models": False,
        "prepare_ucloud_models": False,
    }


def editable_control(page, tab):
    return page.locator("#hf-settings-token" if tab == "huggingface" else "#enhance-downloads")


def held_settings_load(page, local_app, tab, endpoint, *, local_response=None):
    held = []

    def load(route):
        if not held:
            held.append(route)
            page.evaluate("window.settingsTabHeld = true")
        else:
            route.fulfill(json=settings_response(tab))

    page.route("**/api/" + endpoint, load)
    page.route(
        "**/api/local-runtime",
        lambda route: route.fulfill(
            json=local_response or {"status": {"ready": False, "error": "Synthetic runtime status"}}
        ),
    )
    page.route(
        "**/api/runtime-batches/settings", lambda route: route.fulfill(json={"max_allocations": 2})
    )
    page.route(
        "**/api/enhancement-api/status",
        lambda route: route.fulfill(
            json={
                "connected": False,
                "message": "Synthetic disconnected connection",
                "local_minutes": {"words": 15, "speakers": 10},
                "cuda_factor": 2,
                "fallback": "stop",
                "rules": {
                    "word_minutes": 15,
                    "speaker_minutes": 10,
                    "cuda_factor": 2,
                    "fallback": "stop",
                },
                "defaults": {},
            }
        ),
    )
    page.goto(local_app.url)
    page.wait_for_function("() => state.providers.length > 0")
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.locator("#tab-" + tab).click()
    page.wait_for_function("() => window.settingsTabHeld === true")
    return held[0]


def newer_settings_context(page, outcome):
    if outcome == "switched":
        # Use the shipped tab keyboard handler, including its synthetic click.
        page.keyboard.press("Home")
    else:
        page.keyboard.press("Escape")
        page.locator("#settings-open").click()
    expect(page.locator("#tab-openai")).to_have_attribute("aria-selected", "true")
    page.locator("#api-key").fill("synthetic-newer-draft")


@pytest.mark.parametrize("tab,endpoint,panel", TABS)
@pytest.mark.parametrize("outcome", ["current", "switched", "reopened"])
def test_settings_tab_load_error_scope_and_retry(page, local_app, tab, endpoint, panel, outcome):
    held = held_settings_load(page, local_app, tab, endpoint)
    controls = page.locator("#" + panel + "-panel")
    # The panel role is not an actionable control; assert native fieldset inheritance.
    expect(controls).to_have_attribute("disabled", "")
    expect(editable_control(page, tab)).to_be_disabled()
    if outcome != "current":
        newer_settings_context(page, outcome)
    held.fulfill(status=503, json={"detail": "Synthetic settings tab load failed."})
    # Wait for the foreground loader to settle before asserting stale replies did nothing.
    page.wait_for_function("() => pendingRequests === 0")
    if outcome == "current":
        alert = page.locator("#provider-error")
        expect(alert).to_have_text("Synthetic settings tab load failed.")
        page.bring_to_front()
        expect(alert).to_be_focused()
        expect(page.locator("#tab-" + tab)).to_have_attribute("aria-selected", "true")
        expect(editable_control(page, tab)).to_be_disabled()
        hint = "#hf-settings-result" if tab == "huggingface" else "#processing-status"
        expect(page.locator(hint)).to_contain_text("again to retry")
        # The same tab remains a keyboard-operable retry; no credentials or model checks.
        page.locator("#tab-" + tab).focus()
        page.keyboard.press("Enter")
        expect(editable_control(page, tab)).to_be_enabled()
        expect(alert).to_be_hidden()
        if tab == "huggingface":
            expect(page.locator("#hf-settings-status")).to_have_text("No token saved")
        else:
            expect(page.locator("#enhance-language")).to_have_count(0)
            expect(page.locator("#enhance-downloads")).not_to_be_checked()
    else:
        expect(page.locator("#provider-error")).to_be_hidden()
        expect(page.locator("#api-key")).to_have_value("synthetic-newer-draft")
        expect(controls).to_be_hidden()
        expect(controls).to_have_attribute("disabled", "")
        expect(editable_control(page, tab)).to_be_disabled()


@pytest.mark.parametrize("tab,endpoint,panel", TABS)
@pytest.mark.parametrize("outcome", ["switched", "reopened"])
def test_late_settings_tab_success_does_not_reenable_departed_form(
    page, local_app, tab, endpoint, panel, outcome
):
    held = held_settings_load(page, local_app, tab, endpoint)
    newer_settings_context(page, outcome)
    held.fulfill(content_type="application/json", body=json.dumps(settings_response(tab)))
    page.wait_for_function("() => pendingRequests === 0")
    expect(page.locator("#provider-error")).to_be_hidden()
    expect(page.locator("#api-key")).to_have_value("synthetic-newer-draft")
    expect(page.locator("#" + panel + "-panel")).to_be_hidden()
    expect(page.locator("#" + panel + "-panel")).to_have_attribute("disabled", "")
    expect(editable_control(page, tab)).to_be_disabled()


def test_delayed_processing_status_preserves_newer_rule_draft(page, local_app):
    settings = held_settings_load(page, local_app, "processing", "enhancement-settings")
    held = []

    def hold(route):
        held.append(route)
        page.evaluate("window.processingStatusHeld = true")

    page.route("**/api/enhancement-api/status", hold)
    settings.fulfill(json=settings_response("processing"))
    page.wait_for_function("() => window.processingStatusHeld === true")
    word_minutes = page.locator("#rule-word-minutes")
    expect(word_minutes).to_be_enabled()
    word_minutes.fill("7")
    held[0].fulfill(
        json={
            "connected": False,
            "message": "Synthetic disconnected connection",
            "local_minutes": {"words": 15, "speakers": 10},
            "cuda_factor": 2,
            "fallback": "stop",
            "rules": {
                "word_minutes": 15,
                "speaker_minutes": 10,
                "cuda_factor": 2,
                "fallback": "stop",
            },
            "defaults": {},
        }
    )
    page.wait_for_function("() => pendingRequests === 0")
    expect(word_minutes).to_have_value("7")
    expect(page.locator("#tab-processing")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#provider-error")).to_be_hidden()
