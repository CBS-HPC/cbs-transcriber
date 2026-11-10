"""History can restore an earlier version as the newest one without losing anything."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_history_restores_an_earlier_version(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("History restore")
    recording = store.create_recording(project["id"], "a.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "R",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator("#passage-0 textarea").fill("Edited wording.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Edited wording.")
    page.locator("#history-open").click()
    page.locator("[data-history-version]").first.click()
    expect(page.locator("#history-restore")).to_be_hidden()  # The newest is the current text.
    page.locator("[data-history-version]").last.click()
    expect(page.locator("#history-text")).to_contain_text("Original wording.")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#history-restore").click()
    expect(page.locator("#history-dialog")).to_be_hidden()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Original wording.")
    expect(page.locator("#toast")).to_contain_text("restored as the newest version")
    page.locator("#history-open").click()
    expect(page.locator("[data-history-version]")).to_have_count(3)
    expect(page.locator("[data-history-version]").first).to_contain_text("restored earlier version")


def test_restore_keeps_notes_drafts_and_reports_refusals_in_history(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("History refusal")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "R",
        {},
        auto_first=True,
    )
    current = store.recording(rid)
    edited = [dict(current["segments"][0], text="Second wording.")]
    store.save_transcript(rid, edited, {}, current["revision"], result_id=result["id"])
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Second wording.")
    page.locator("#recording-notes").locator("..").locator("summary").click()
    page.locator("#recording-notes").fill("A notes draft that restore must not touch.")
    prompts = []

    def answer(dialog):
        prompts.append(dialog.message)
        dialog.accept()

    page.on("dialog", answer)
    page.locator("#history-open").click()
    page.locator("[data-history-version]").last.click()
    expect(page.locator("#history-restore")).to_be_visible()
    # Another window advances the Working head, so this view's content and role are stale.
    current = store.recording(rid)
    edited = [dict(current["segments"][0], text="Third wording.")]
    store.save_transcript(rid, edited, {}, current["revision"], result_id=result["id"])
    page.locator("#history-restore").click()
    expect(page.locator("#history-status")).to_have_text(
        "The saved version or Working designation changed. Review again."
    )
    expect(page.locator("#history-dialog")).to_be_visible()
    assert len(prompts) == 1 and prompts[0].startswith("Restore version 1")
    expect(page.locator("#recording-notes")).to_have_value(
        "A notes draft that restore must not touch."
    )
    assert store.result(rid, result["id"])["segments"][0]["text"] == "Third wording."
    assert len(store.result_versions(rid, result["id"])) == 3
