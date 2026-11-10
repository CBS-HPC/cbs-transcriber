"""Recordings are renamed from the heading pencil, matching project headings."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_recording_heading_pencil_renames_recording(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Rename recordings")
    recording = store.create_recording(project["id"], "Interview.wav")
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#recording-title")).to_have_text("Interview")
    expect(page.locator(".editor-actions").get_by_role("button", name="Rename")).to_have_count(0)
    rename = page.locator("#recording-title + #recording-rename")
    expect(rename).to_be_visible()
    expect(rename).to_have_accessible_name("Rename recording")
    rename.click()
    expect(page.locator("#name-title")).to_have_text("Rename recording")
    page.locator("#name-input").fill("Interview æøå")
    page.locator("#name-input").press("Enter")
    expect(page.locator("#recording-title")).to_have_text("Interview æøå")
    assert store.recording(recording["id"])["name"] == "Interview æøå"
