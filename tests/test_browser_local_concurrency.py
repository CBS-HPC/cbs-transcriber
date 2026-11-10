"""Settings → Local Processing → Parallel local tasks (ADR 0146), on a synthetic library."""

import httpx
import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_processing_replies import open_processing

from cbs_transcriber import local_concurrency

pytestmark = pytest.mark.browser
LANES = ("media", "inspection", "api", "admission")


def saved(local_app):
    return httpx.get(local_app.url + "/api/local-concurrency").json()


def test_parallel_tasks_validate_save_partially_and_restore_automatic(page, local_app):
    defaults = local_concurrency.defaults()
    manual_media = 5 if defaults["media"] == 6 else 6
    open_processing(page, local_app)
    page.locator("#local-concurrency > summary").click()
    for lane in LANES:
        expect(page.locator(f"#concurrency-{lane}")).to_be_enabled()
        expect(page.locator(f"#concurrency-{lane}")).to_have_value(str(defaults[lane]))
    expect(page.locator("#concurrency-note")).to_contain_text("Automatic on this computer")
    puts = []
    page.on(
        "request",
        lambda request: (
            puts.append(request.post_data_json)
            if request.method == "PUT" and request.url.endswith("/api/local-concurrency")
            else None
        ),
    )
    # An out-of-range value is refused in the browser and never sent.
    page.locator("#concurrency-media").fill("9")
    page.locator("#concurrency-save").click()
    expect(page.locator("#concurrency-status")).to_contain_text("from 1 to 8")
    assert puts == []
    # A changed lane is saved; lanes left at their automatic value stay automatic.
    page.locator("#concurrency-media").fill(str(manual_media))
    page.locator("#concurrency-media").press("Enter")
    expect(page.locator("#concurrency-status")).to_contain_text("running files continue")
    assert puts == [{"media": manual_media, "inspection": None, "api": None, "admission": None}]
    status = saved(local_app)
    assert status["saved"] == {
        "media": manual_media,
        "inspection": None,
        "admission": None,
        "api": None,
    }
    assert status["active"]["media"] == manual_media
    expect(page.locator("#concurrency-note")).to_contain_text(
        f"Your own values: playback {manual_media}"
    )
    # Settings in other tabs are unaffected by the separate parallel-task form.
    assert page.locator("#concurrency-media").evaluate("input => input.form.id") == (
        "local-concurrency-form"
    )
    page.locator("#concurrency-defaults").click()
    expect(page.locator("#concurrency-media")).to_have_value(str(defaults["media"]))
    assert saved(local_app)["saved"] == dict.fromkeys(local_concurrency.LANES)
    assert saved(local_app)["active"] == defaults
