"""Editing a source after queueing an enhancement is reported, never silently merged."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_stale_enhancement_source_is_named_in_editor_and_activity(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Stale source")
    recording = store.create_recording(project["id"], "a.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    source = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "Provider",
        {},
        auto_first=True,
    )
    frozen = store.freeze_result(recording["id"], source["id"], 1)
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#result-source-note")).to_be_hidden()
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator("#passage-0 textarea").fill("Edited while the enhancement waited.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(
        "Edited while the enhancement waited."
    )
    page.locator("#activity-open").click()
    expect(page.locator("#jobs")).to_contain_text("edited after this was queued")
    page.locator("#activity-close").click()

    enhanced = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]),
        {},
        "Enhanced",
        {"kind": "enhancement", "source_result_id": source["id"]},
        job_id=job["id"],
    )
    page.reload()
    # The reviewed source stays active; the note explains what the new result lacks.
    expect(page.locator("#enhancement-ready")).to_contain_text("from version 1 of this result")
    expect(page.locator("#result-source-note")).to_be_hidden()
    page.locator("#enhancement-ready [data-review-enhancement]").click()
    expect(page.locator("#result-select")).to_have_value(enhanced["id"])
    expect(page.locator("#result-source-note")).to_be_visible()
    expect(page.locator("#result-source-note")).to_contain_text("Built from version 1 of Provider")
    expect(page.locator("#result-source-note")).to_contain_text("now version 2")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Original wording.")
