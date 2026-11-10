import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.store import now
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_completed_enhancement_is_discovered_after_draft_closes(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Deferred result discovery")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    source = store.add_result(
        rid,
        normalize_segments([{"text": "Preserved source passage."}]),
        {},
        "Source",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#result-select option")).to_have_count(1)
    page.locator("#passage-0 [data-action=edit]").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("My pending local draft.")

    # Insert an already completed synthetic job so no inference worker is invoked.
    job_id = "d" * 32
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,message,created,updated) "
        "VALUES (?,?,?,'completed',?,?,?)",
        (job_id, rid, "enhance", "Synthetic enhancement completed", now(), now()),
    )
    enhanced = store.add_result(
        rid,
        normalize_segments([{"text": "Preserved source passage.", "start": 0, "end": 2}]),
        {},
        "Local WhisperX enhancement",
        {"kind": "enhancement", "source_result_id": source["id"]},
        job_id=job_id,
    )
    expect(page.locator("#jobs")).to_contain_text("Synthetic enhancement completed", timeout=8000)
    expect(draft).to_have_value("My pending local draft.")
    expect(page.locator("#result-select option")).to_have_count(1)
    page.locator("#passage-0 [data-action=cancel-edit]").click()
    expect(page.locator("#result-select option")).to_have_count(2, timeout=8000)
    expect(page.locator("#result-select")).to_have_value(source["id"])
    expect(page.locator("#enhancement-ready")).to_be_visible()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Preserved source passage.")
    assert store.result(rid, enhanced["id"])["segments"][0]["start"] == 0
    assert store.result(rid, source["id"])["segments"] == source["segments"]
