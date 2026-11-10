"""Edited passages regain word timing automatically when the researcher opts in (T69)."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement
from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_realignment_adds_timing_to_the_same_result(page, local_app, monkeypatch):
    store = local_app.server.config.app.state.store
    project = store.create_project("Realign")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=30",
            "-ac",
            "1",
            str(directory / "audio.mp3"),
        ]
    )
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": "Hej verden.",
                    "start": 0,
                    "end": 4,
                    "words": [
                        {"word": "Hej", "start": 0, "end": 1},
                        {"word": "verden.", "start": 1, "end": 4},
                    ],
                }
            ]
        ),
        {},
        "Word model",
        {"capability": "words"},
    )

    def realign(queue, job, recording):
        # Stands in for the local passage worker: bounded words for the frozen text.
        frozen = job["params"]["source_snapshot"]["segments"][0]
        fragment = normalize_segments(
            [
                {
                    "text": frozen["text"],
                    "start": 0,
                    "end": 4,
                    "words": [
                        {"word": word, "start": index, "end": index + 0.9}
                        for index, word in enumerate(frozen["text"].split())
                    ],
                }
            ]
        )[0]
        enhancement.finish_realign(queue, job, [fragment])

    monkeypatch.setattr(enhancement, "run_enhancement", realign)
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action='edit']").click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    realign = page.locator("#passage-0 [data-editor-realign]")
    expect(realign).not_to_be_checked()
    realign.check()
    assert not enhancement.realign_enabled(store)
    page.locator("#passage-0 textarea").fill("Hej hele verden.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Hej hele verden.")
    expect(page.locator("#toast")).to_contain_text("Word timing added", timeout=15000)
    # Complete word timing again: the edited passage needs no timing note any more.
    expect(page.locator("#passage-0 .segment-note")).to_have_count(0)
    expect(page.locator("#passage-0 .timing-tag")).to_have_count(0)
    current = store.result(rid, result["id"])
    assert current["segments"][0]["words_current"] is True
    assert [w["word"] for w in current["segments"][0]["words"]] == ["Hej", "hele", "verden."]
    page.locator("#history-open").click()
    expect(page.locator("[data-history-version]").first).to_contain_text("timing realign")
    page.locator("#history-dialog [data-close]").first.click()
    # Finished realignments stay out of Activity.
    page.locator("#activity-open").click()
    expect(page.locator("#jobs")).not_to_contain_text("edited passage")
    assert len(store.results(rid)) == 1
