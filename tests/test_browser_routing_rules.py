"""Researchers set the Automatic rules in Provider Settings → Processing (T68, ADR 0059)."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement, routing
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_automatic_rules_are_shown_saved_and_restored(page, local_app, monkeypatch):
    # Opening Processing checks this computer; never run the real runtime/GPU check.
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = local_app.server.config.app.state.store
    project = store.create_project("Rules")
    recording = store.create_recording(project["id"], "a.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej.", "start": 0, "end": 1}]),
        {},
        "R",
        {},
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    expect(page.locator("#processing-panel")).to_be_visible()
    expect(page.locator("#enhance-auto-note")).to_contain_text("up to 10 minutes (3 with speaker")
    expect(page.locator("#enhance-rules")).to_have_attribute("open", "")
    expect(page.locator("#rule-word-minutes")).to_have_value("10")
    page.locator("#rule-word-minutes").fill("4")
    page.locator("#rule-fallback").select_option("stop")
    # Enter saves the rules; it never submits the settings form or starts any job.
    page.locator("#rule-word-minutes").press("Enter")
    expect(page.locator("#rule-status")).to_contain_text("Rules saved")
    expect(page.locator("#enhance-auto-note")).to_contain_text("up to 4 minutes")
    expect(page.locator("#enhance-auto-note")).to_contain_text("they are not started")
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#processing-panel")).to_be_visible()
    assert store.all("SELECT id FROM jobs") == []
    saved = routing.rules(store)
    assert (saved.word_minutes, saved.fallback) == (4, "stop")
    page.locator("#rule-defaults").click()
    expect(page.locator("#enhance-auto-note")).to_contain_text("up to 10 minutes")
    expect(page.locator("#rule-fallback")).to_have_value("local")
    assert routing.rules(store) == routing.Rules()
    assert store.all("SELECT id FROM jobs") == []
