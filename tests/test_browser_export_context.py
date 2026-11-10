"""Export Settings cannot carry another recording's result across browser navigation."""

from urllib.parse import urljoin

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_back_navigation_closes_old_export_scope_before_opening_another_recording(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Export navigation")
    recordings = []
    for name in ("First", "Second"):
        recording = store.create_recording(project["id"], name + ".wav")
        store.add_result(
            recording["id"],
            normalize_segments([{"text": name + " words.", "start": 0, "end": 1}]),
            {},
            name,
            {},
        )
        recordings.append(recording)
    page.goto(local_app.url + f"/#/recordings/{recordings[1]['id']}")
    expect(page.locator("#recording-title")).to_have_text("Second")
    page.evaluate("hash => location.hash = hash", f"#/recordings/{recordings[0]['id']}")
    expect(page.locator("#recording-title")).to_have_text("First")
    page.locator("#export-open").click()
    expect(page.locator("#export-dialog")).to_be_visible()
    assert f"/recordings/{recordings[0]['id']}/export" in page.locator(
        "#export-link"
    ).get_attribute("href")
    page.go_back()
    expect(page.locator("#recording-title")).to_have_text("Second")
    expect(page.locator("#export-dialog")).to_be_hidden()
    page.locator("#export-open").click()
    expect(page.locator("#export-scope")).to_contain_text("Second")
    link = page.locator("#export-link").get_attribute("href")
    assert f"/recordings/{recordings[1]['id']}/export" in link
    response = page.request.get(urljoin(local_app.url, link))
    assert response.status == 200 and "Second words." in response.text()
