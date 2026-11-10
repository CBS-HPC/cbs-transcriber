import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_older_settings_open_cannot_reset_a_newer_dialog(page, local_app):
    page.goto(local_app.url)
    page.wait_for_function("() => state.providers.length > 0")
    page.evaluate("""() => {
        const original = openSettings;
        let request = 0;
        window.settingsOpensCompleted = [];
        openSettings = async (...args) => {
            const id = ++request;
            try { return await original(...args); }
            finally { window.settingsOpensCompleted.push(id); }
        };
    }""")
    held = []

    def hold_first(route):
        if not held:
            held.append((route, route.fetch()))
            page.evaluate("window.providerOpenHeld = true")
        else:
            route.continue_()

    page.route("**/api/providers", hold_first)
    page.locator("#settings-open").click()
    page.wait_for_function("() => window.providerOpenHeld === true")
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.locator("#api-key").fill("Newer unsaved key")
    page.locator('[data-enabled-model="gpt-4o-transcribe-diarize"]').uncheck()
    route, response = held[0]
    route.fulfill(response=response)
    page.wait_for_function("() => window.settingsOpensCompleted.length === 2")
    expect(page.locator("#api-key")).to_have_value("Newer unsaved key", timeout=1500)
    expect(page.locator('[data-enabled-model="gpt-4o-transcribe-diarize"]')).not_to_be_checked()
