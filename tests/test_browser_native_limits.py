"""Settings → UCloud native Transcriber limits (ADR 0183): save on change, never unsaved."""

import httpx
import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_settings_unsaved import open_settings
from test_browser_settings_unsaved import ready as ready

pytestmark = pytest.mark.browser


def saved(local_app):
    return httpx.get(local_app.url + "/api/native-batches/settings").json()


def test_v230_native_limits_save_on_change_and_never_ask_before_reload(page, local_app, ready):
    app, _ = ready
    open_settings(page, local_app)
    page.locator("#provider-advanced > summary").click()
    uploads, batches = page.locator("#native-upload-limit"), page.locator("#native-batch-limit")
    expect(uploads).to_be_enabled()
    expect(uploads).to_have_value("4")
    expect(batches).to_have_value("2")
    expect(page.locator("#native-limits-note")).to_contain_text("depends on your network")
    # Separate saves: these inputs never belong to the connection form.
    assert uploads.evaluate("input => input.form.id") == "native-limits-form"
    puts = []
    page.on(
        "request",
        lambda request: (
            puts.append(request.post_data_json)
            if request.method == "PUT" and request.url.endswith("/api/native-batches/settings")
            else None
        ),
    )
    uploads.fill("9")
    uploads.press("Enter")
    expect(page.locator("#native-limits-status")).to_contain_text("from 1 to 8")
    assert puts == []
    uploads.fill("6")
    uploads.press("Enter")
    expect(page.locator("#native-limits-status")).to_contain_text("Saved")
    batches.fill("3")
    batches.press("Enter")
    expect(batches).to_be_enabled()
    expect(page.locator("#native-limits-status")).to_contain_text("Saved")
    assert puts == [{"uploads": 6}, {"batches": 3}]
    assert (saved(local_app)["uploads"], saved(local_app)["batches"]) == (6, 3)
    assert app.state.queue.native_batch_limit() == 3
    # They save on change, so reselecting the open tab reloads without asking.
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.get_by_role("tab", name="UCloud", exact=True).click()
    page.locator("#provider-advanced > summary").click()
    expect(uploads).to_have_value("6")
    expect(batches).to_have_value("3")
    assert dialogs == []
    # Other tabs do not show the UCloud limits.
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    expect(page.locator("#native-limits-panel")).to_be_hidden()
