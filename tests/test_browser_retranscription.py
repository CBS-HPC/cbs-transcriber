"""Re-transcribing passages from the editor and from uncertainty flags (T71, ADR 0059)."""

import json

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_passages_can_be_re_transcribed_into_a_separate_result(page, local_app, monkeypatch):
    store = local_app.server.config.app.state.store
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    monkeypatch.setattr(enhancement, "run_enhancement", lambda *a: None)
    project = store.create_project("Re-transcribe")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    values = [-0.1, -0.12, -0.9, -0.11, -0.13, -0.1, -0.14, -0.12, -0.1, -0.11]
    store.add_result(
        rid,
        normalize_segments(
            [
                {"text": f"Passage {i}.", "start": i * 2, "end": i * 2 + 1.5, "avg_logprob": v}
                for i, v in enumerate(values)
            ]
        ),
        {},
        "OpenAI",
        {},
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    # One click starts at once (ADR 0059): Automatic place, large-v3-turbo, saved language.
    with page.expect_response("**/retranscriptions") as reply:
        page.locator("#passage-4 [data-action='retranscribe']").click()
    assert reply.value.status == 202
    expect(page.locator("#toast")).to_contain_text("separate result to compare")
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    [job] = store.all("SELECT params FROM jobs")
    params = json.loads(job["params"])
    assert params["segment_ids"] == ["4"] and params["retranscription"] is True
    options = params["enhancement"]
    assert options["whisper_model"] == "large-v3-turbo" and options["language"] == "da"
    # Automatic freezes a concrete place; this short passage runs on this computer.
    assert options["execution"] == "local" and params["routing"]["mode"] == "auto"
    assert options["allow_downloads"]
    store.execute("UPDATE jobs SET state='completed'")
    # The uncertainty view offers its flagged passages in one go, as a tag above the passages.
    expect(page.locator(".passage.review")).to_have_count(1)
    tag = page.locator("#transcript-tags #retranscribe-flagged")
    expect(tag).to_have_text("Re-transcribe all 1 of 10 flagged passages")
    page.screenshot(path="test-results/retranscribe-flagged-tag.png")
    # Declining the one confirmation queues nothing.
    page.once("dialog", lambda dialog: dialog.dismiss())
    tag.click()
    assert len(store.all("SELECT id FROM jobs")) == 1
    asked = []

    def accept(dialog):
        asked.append(dialog.message)
        dialog.accept()

    page.once("dialog", accept)
    with page.expect_response("**/retranscriptions") as reply:
        tag.click()
    assert reply.value.status == 202
    assert len(asked) == 1 and "1 flagged passage(s)" in asked[0]
    assert "separate result" in asked[0]
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    rows = store.all("SELECT params FROM jobs ORDER BY created")
    assert len(rows) == 2 and json.loads(rows[-1]["params"])["segment_ids"] == ["2"]
    assert json.loads(rows[-1]["params"])["enhancement"]["whisper_model"] == "large-v3-turbo"
