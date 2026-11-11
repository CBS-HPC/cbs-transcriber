"""Connection selectors follow explicit mocked checks without sending audio."""

import pytest
from browser_provider_support import mock_native_check
from playwright.sync_api import expect
from test_browser import create_project
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_fresh_setup_and_failed_recheck_update_choices_without_overwriting_draft(
    page, local_app, monkeypatch
):
    app = local_app.server.config.app
    requests = []
    page.on("request", lambda request: requests.append((request.method, request.url)))

    mock_native_check(monkeypatch, key="synthetic-settings-key")
    page.goto(local_app.url)
    create_project(page, "Synthetic connected providers")
    expect(page.locator("#bulk-provider option")).to_have_count(1)
    expect(page.locator("#bulk-provider")).to_have_value("")
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.locator("#api-key").fill("synthetic-settings-key")
    page.locator("#connect-ucloud").click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("Synthetic connection checked")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    expect(page.locator("#bulk-provider-kind")).to_have_value("ucloud")
    expect(page.locator("#bulk-provider")).not_to_have_value("")

    def fail(*args, **kwargs):
        raise ValueError("Synthetic provider outage.")

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", fail)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    # A failed refresh preserves this unrelated unsaved draft.
    page.locator("#provider-advanced > summary").click()
    page.locator("#native-hours").fill("19")
    page.locator("#connect-ucloud").click()
    expect(page.locator("#provider-error")).to_have_text("Synthetic provider outage.")
    expect(page.locator("#native-hours")).to_have_value("19")
    expect(page.locator("#bulk-provider")).to_have_value("")
    page.locator('#settings-dialog [aria-label="Close settings"]').click()
    expect(page.locator("#bulk-provider")).to_have_value("")
    assert not any(
        method == "POST" and url.endswith("/api/transcriptions") for method, url in requests
    )
    assert not app.state.store.all("SELECT id FROM jobs")
