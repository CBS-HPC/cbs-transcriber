import json
import threading

import httpx
import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement
from cbs_transcriber.media import ffmpeg
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("target", ["passages", "all"])
def test_browser_passage_word_timing_is_added_in_place(page, local_app, monkeypatch, target):
    """ADR 0056: a tag times only its passage, others can start meanwhile, nothing forks."""
    store = Store(local_app.root)
    project = store.create_project("Passage timing")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.directory(rid).mkdir(parents=True, exist_ok=True)
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=4",
            "-ac",
            "1",
            str(store.directory(rid) / "audio.mp3"),
        ],
        threading.Event(),
    )
    segments = normalize_segments(
        [
            {
                "text": "Hej.",
                "start": 0,
                "end": 1,
                "words": [{"word": "Hej", "start": 0, "end": 1}],
            },
            {"text": "God  morgen.", "start": 1, "end": 3, "speaker": "B"},
            {"text": "Tak.", "start": 3, "end": 4},
        ]
    )
    source = store.add_result(
        rid, segments, {"B": "Benny"}, "Word model", {"capability": "words"}, auto_first=True
    )
    compared = store.add_result(
        rid, segments, {"B": "Benny"}, "Other model", {"capability": "words"}
    )
    # This cached-worker scenario deliberately opts out of the newer download default.
    enhancement.save_local_settings(
        store, enhancement.local_settings(store).model_copy(update={"download_models": False})
    )
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    release = threading.Event()

    def align(folder, options, token, cancel, progress, **kw):
        assert options.align and not options.diarize and not options.allow_downloads
        assert options.execution == "local"
        [passage] = json.loads((folder / "request.json").read_text(encoding="utf-8"))["passages"]
        assert release.wait(15)
        words = passage["text"].split()
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": passage["id"],
                        "words": [
                            {"word": w, "start": i * 0.4, "end": i * 0.4 + 0.3, "score": 0.9}
                            for i, w in enumerate(words)
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(enhancement, "run_worker", align)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#passage-1 [data-run-repair]")).to_have_text("Add word timing")
    expect(page.locator("#passage-0 .passage-repair")).to_have_count(0)
    expect(page.locator("#timing-warning [data-run-repair]")).to_have_text(
        "Repair all 2 incomplete passages"
    )
    # The comparison is read only: it explains missing timing but offers no action.
    page.locator("#compare-result").select_option(compared["id"])
    expect(page.locator("#comparison-info")).to_contain_text("2 of 3 passages without word timing")
    expect(page.locator("#comparison-panel .timing-tag")).to_have_count(0)
    expect(page.locator("#comparison-panel [data-run-repair]")).to_have_count(0)
    try:
        # An open draft stays open; the timing waits for it instead of discarding it.
        page.locator("#passage-0 [data-action='edit']").click()
        page.locator("#passage-0 textarea").fill("Keep this draft.")
        if target == "passages":
            page.locator("#passage-1 [data-run-repair]").click()
            expect(page.locator("#passage-1 [data-run-repair]")).to_have_text("Aligning")
            # Only this passage is busy: another one can start while it aligns.
            expect(page.locator("#passage-2 [data-run-repair]")).to_have_text("Add word timing")
            page.locator("#passage-2 [data-run-repair]").click()
            expect(page.locator("#passage-2 [data-run-repair]")).to_have_text("Queued")
        else:
            page.locator("#timing-warning [data-run-repair]").click()
            expect(page.locator("#passage-1 [data-run-repair]")).to_have_text("Aligning")
            expect(page.locator("#passage-2 [data-run-repair]")).to_have_text("Queued")
        expect(page.locator("#passage-0 textarea")).to_have_value("Keep this draft.")
    finally:
        release.set()
    expect(page.locator("#passage-2 [data-run-repair]")).to_have_text("Adding", timeout=15000)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#passage-0").get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#passage-1 .word")).to_have_count(2, timeout=15000)
    expect(page.locator("#passage-2 .word")).to_have_count(1, timeout=15000)
    expect(page.locator("#timing-coverage")).to_contain_text("4/4 words (100%)")
    expect(page.locator("#timing-warning [data-run-repair]")).to_have_count(0)
    # The same result gained the timing; no separate result to review.
    expect(page.locator("#result-select")).to_have_value(source["id"])
    expect(page.locator("#result-select option")).to_have_count(2)
    expect(page.locator("#passage-1 .speaker-badge")).to_have_text("Benny")
    expect(page.locator("#passage-1 .passage-text")).to_have_text("God  morgen.")
    page.locator("#passage-1 [data-action='replay']").click()
    expect(page.locator("#passage-1 .word.current")).to_have_count(1)
    page.locator("#play-pause").click()
    page.screenshot(path="test-results/passage-word-timing.png")
    with httpx.Client(base_url=local_app.url) as api:
        saved = api.get(f"/api/recordings/{rid}/transcripts/{source['id']}").json()
    assert saved["segments"][1]["original_text"] == "God  morgen."
    assert saved["segments"][0] == segments[0]
    versions = store.result_versions(rid, source["id"])
    assert [v["action"] for v in versions[: 2 if target == "passages" else 1]] == [
        "timing_realign"
    ] * (2 if target == "passages" else 1)


def test_browser_recording_notes_are_literal_and_independent(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Notes")
    recording = store.create_recording(project["id"], "notes.wav")
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (recording["id"],))
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.locator("#editor-layout .description-notes summary").click()
    text = "Danish interview: æøå. <script>not executed</script>"
    page.locator("#recording-notes").fill(text)
    expect(page.locator("#recording-notes-status")).to_have_text("Unsaved notes")
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Notes saved")
    page.reload()
    expect(page.locator("#recording-notes")).to_have_value(text)
    with httpx.Client(base_url=local_app.url) as api:
        saved = api.get(f"/api/recordings/{recording['id']}").json()
    assert saved["notes"] == text and saved["revision"] == 0


def test_browser_late_recording_note_save_cannot_target_another_recording(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Two recordings")
    first = store.create_recording(project["id"], "First.wav")
    second = store.create_recording(project["id"], "Second.wav")
    pending = []
    page.route(f"**/api/recordings/{first['id']}/notes", lambda route: pending.append(route))
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    page.locator("#editor-layout .description-notes summary").click()
    page.locator("#recording-notes").fill("Notes for the first recording")
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Saving notes…")
    page.locator(f'#projects a[href="#/projects/{project["id"]}"]').click()
    page.get_by_role("button", name="Second", exact=True).click()
    expect(page.locator("#recording-notes")).to_have_attribute("data-recording", second["id"])
    expect(page.locator("#recording-notes")).to_have_value("")
    assert len(pending) == 1
    pending[0].continue_()
    page.locator("#recording-notes").fill("Notes for the second recording")
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Notes saved")
    assert store.recording(first["id"])["notes"] == "Notes for the first recording"
    assert store.recording(second["id"])["notes"] == "Notes for the second recording"


def test_browser_card_notes_share_recording_notes_and_preserve_filtered_drafts(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Card notes")
    first = store.create_recording(project["id"], "Interview.wav")
    second = store.create_recording(project["id"], "Another.wav")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator("[data-transcribe]")).to_have_count(0)
    expect(page.locator("#batch-open")).to_have_count(0)
    card = page.locator(f'[data-recording="{first["id"]}"]')
    card.locator("summary").click()
    expect(card.locator("textarea")).to_have_attribute("maxlength", "20000")
    text = "Interview context: æøå. <script>window.untrustedNote = true</script>"
    card.locator("textarea").fill(text)
    page.locator("#select-all").click()
    expect(card.locator("textarea")).to_have_value(text)
    page.locator("#recording-search").fill("Another")
    expect(card).to_have_count(0)
    page.locator("#recording-search").fill("")
    expect(card.locator("textarea")).to_have_value(text)
    expect(card.locator('[role="status"]')).to_have_text("Unsaved notes")
    page.locator("#bulk-provider").select_option(index=0)
    expect(card.locator("textarea")).to_have_value(text)
    card.get_by_role("button", name="Save notes").click()
    expect(card.locator('[role="status"]')).to_have_text("Notes saved")
    assert store.recording(first["id"])["notes"] == text
    assert store.recording(first["id"])["revision"] == 0
    assert store.recording(second["id"])["notes"] == ""
    assert page.evaluate("window.untrustedNote") is None
    card.locator("[data-open]").click()
    expect(page.locator("#recording-notes")).to_have_value(text)
    page.locator("#editor-layout .description-notes summary").click()
    revised = text + "\nReviewed in the recording."
    page.locator("#recording-notes").fill(revised)
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Notes saved")
    page.locator("#back-library").click()
    expect(card.locator("textarea")).to_have_value(revised)
    page.reload()
    card.locator("summary").click()
    expect(card.locator("textarea")).to_have_value(revised)
    assert store.recording(first["id"])["notes_revision"] == 2
    page.screenshot(path="test-results/recording-card-notes.png", full_page=True)


def test_browser_late_card_note_save_keeps_newer_recording_draft(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Delayed card notes")
    first = store.create_recording(project["id"], "First.wav")
    second = store.create_recording(project["id"], "Second.wav")
    held = []

    def hold_save(route):
        held.append((route, route.fetch()))
        page.evaluate("window.cardNoteSaved = true")

    page.route(f"**/api/recordings/{first['id']}/notes", hold_save)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    card = page.locator(f'[data-recording="{first["id"]}"]')
    card.locator("summary").click()
    card.locator("textarea").fill("Saved from the first card")
    card.get_by_role("button", name="Save notes").click()
    expect(card.locator("textarea")).to_be_disabled()
    page.wait_for_function("() => window.cardNoteSaved === true")
    page.locator(f'[data-open="{second["id"]}"]').click()
    page.locator("#editor-layout .description-notes summary").click()
    page.locator("#recording-notes").fill("Unsaved notes for the second recording")
    route, response = held.pop()
    route.fulfill(response=response)
    page.wait_for_function("() => pendingRecordingNotes.size === 0")
    expect(page.locator("#recording-notes")).to_have_value("Unsaved notes for the second recording")
    expect(page.locator("#recording-notes")).to_be_enabled()
    expect(page.locator("#recording-notes-status")).to_have_text("Unsaved notes")
    assert store.recording(second["id"])["notes"] == ""
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#back-library").click()
    expect(card.locator("textarea")).to_have_value("Saved from the first card")
