"""Passage draft controls stay local and preserve unrelated long-transcript state."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_editor(page, local_app, count=3, *, playback=False):
    store = local_app.server.config.app.state.store
    project = store.create_project(
        "Synthetic editor controls", storage_parent=local_app.root / "projects"
    )
    row = store.create_recording(project["id"], "synthetic.wav")
    rows = []
    for index in range(count):
        start = index / 10
        rows.append(
            {
                "text": f"Passage {index} needle.",
                "speaker": "A",
                "start": start,
                "end": start + 0.09,
                "words": [
                    {"word": "Passage", "start": start, "end": start + 0.02},
                    {"word": str(index), "start": start + 0.025, "end": start + 0.045},
                    {"word": "needle.", "start": start + 0.05, "end": start + 0.09},
                ],
            }
        )
    result = store.add_result(
        row["id"],
        normalize_segments(rows),
        {"A": "Synthetic voice"},
        "Synthetic",
        {"capability": "words"},
        auto_first=True,
    )
    if playback:
        duration = count / 10 + 1
        store.execute(
            "UPDATE recordings SET status='ready',duration=? WHERE id=?", (duration, row["id"])
        )
        folder = store.directory(row["id"])
        folder.mkdir(parents=True, exist_ok=True)
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                "-t",
                str(duration),
                str(folder / "audio.mp3"),
            ]
        )
    page.goto(local_app.url + f"/#/recordings/{row['id']}")
    expect(page.locator("#transcript > .passage")).to_have_count(count)
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    expect(page.locator("[data-review-summary]")).to_contain_text(f"of {count}")
    return store, row["id"], result["id"]


def content_state(store, rid):
    row = store.recording(rid)
    return {
        key: row[key]
        for key in ("revision", "segments", "speakers", "active_version", "result_roles")
    }


def mutations(page):
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append((request.method, request.url))
            if "/api/" in request.url and request.method not in {"GET", "HEAD"}
            else None
        ),
    )
    return requests


@pytest.mark.parametrize("count", [3, 600])
def test_save_passage_is_disabled_until_text_changes_and_again_after_revert(page, local_app, count):
    store, rid, _ = seed_editor(page, local_app, count)
    before, requests = content_state(store, rid), mutations(page)
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    draft, save = passage.locator("textarea"), passage.locator('[data-action="save-edit"]')
    original = draft.input_value()
    expect(save).to_be_disabled()
    draft.fill(original + " Changed")
    expect(save).to_be_enabled()
    draft.fill(original)
    expect(save).to_be_disabled()
    assert content_state(store, rid) == before
    assert requests == []


def test_split_follows_collapsed_caret_and_unchanged_text_without_api_mutation(page, local_app):
    store, rid, _ = seed_editor(page, local_app)
    before, requests = content_state(store, rid), mutations(page)
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    draft, split = passage.locator("textarea"), passage.locator('[data-action="split"]')
    original = draft.input_value()
    draft.press("Home")
    expect(split).to_be_disabled()
    draft.press("End")
    expect(split).to_be_disabled()
    draft.press("Home")
    draft.press("Control+ArrowRight")
    assert draft.evaluate("e => [e.selectionStart,e.selectionEnd]") == [8, 8]
    expect(split).to_be_enabled()
    draft.press("Shift+ArrowRight")
    expect(split).to_be_disabled()
    draft.press("ArrowLeft")
    expect(split).to_be_enabled()
    draft.click(position={"x": 3, "y": 12})
    assert draft.evaluate("e => [e.selectionStart,e.selectionEnd]") == [0, 0]
    expect(split).to_be_disabled()
    draft.fill(original + " Changed")
    draft.press("Home")
    draft.press("Control+ArrowRight")
    expect(split).to_be_disabled()
    draft.fill(original)
    draft.press("Home")
    draft.press("Control+ArrowRight")
    expect(split).to_be_enabled()
    assert content_state(store, rid) == before
    assert requests == []


@pytest.mark.parametrize("count,target", [(3, 0), (600, 300)])
def test_cancel_discards_only_open_draft_and_preserves_search_scroll_audio_and_review(
    page, local_app, count, target
):
    store, rid, _ = seed_editor(page, local_app, count, playback=True)
    page.locator("#transcript-search").fill("needle")
    expect(page.locator("#transcript > .passage")).to_have_count(count)
    page.locator("#follow-playback").uncheck()
    passage = page.locator(f"#passage-{target}")
    review = passage.locator("[data-review-status]")
    review.select_option("needs_review")
    expect(review).to_have_value("needs_review")
    page.wait_for_function("() => manualReviews.pending.size === 0")
    before, requests = content_state(store, rid), mutations(page)
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    page.locator("#audio").evaluate(
        "async audio => { audio.currentTime = 0.03; audio.playbackRate = 0.1; await audio.play(); }"
    )
    passage.locator('[data-action="edit"]').click()
    draft = passage.locator("textarea")
    original = draft.input_value()
    draft.fill("An intentionally discarded local draft")
    expect(review.locator('option[value="checked"]')).to_be_disabled()
    cancel = passage.locator('[data-action="cancel-edit"]').first
    cancel.scroll_into_view_if_needed()
    other = 2 if target == 0 else target - 1
    page.evaluate(
        """({target,other}) => {
      window.cancelSentinels = {
        passage: document.querySelector(`#passage-${target}`),
        heading: document.querySelector(`#passage-${target} .passage-heading`),
        other: document.querySelector(`#passage-${other}`),
        text: document.querySelector(`#passage-${other} .passage-text`),
        mark: document.querySelector(`#passage-${other} mark.search-match`),
        audio: document.querySelector('#audio'),
        scroll: [scrollX,scrollY,document.querySelector('#transcript').scrollTop]
      };
    }""",
        {"target": target, "other": other},
    )
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    cancel.click()
    expect(passage.locator(".passage-editor")).to_have_count(0)
    expect(passage.locator(".passage-text")).to_have_text(original)
    assert page.evaluate(
        """({target,other}) => {
      const before=window.cancelSentinels;
      return before.passage===document.querySelector(`#passage-${target}`)
        && before.heading===document.querySelector(`#passage-${target} .passage-heading`)
        && before.other===document.querySelector(`#passage-${other}`)
        && before.text===document.querySelector(`#passage-${other} .passage-text`)
        && before.mark===document.querySelector(`#passage-${other} mark.search-match`)
        && before.audio===document.querySelector('#audio');
    }""",
        {"target": target, "other": other},
    )
    assert page.evaluate(
        "[scrollX,scrollY,document.querySelector('#transcript').scrollTop]"
    ) == page.evaluate("window.cancelSentinels.scroll")
    expect(page.locator("#transcript-search")).to_have_value("needle")
    expect(passage.locator("mark.search-match")).to_have_count(1)
    expect(review).to_have_value("needs_review")
    expect(review.locator('option[value="checked"]')).to_be_enabled()
    assert page.locator("#audio").evaluate("audio => !audio.paused")
    page.locator("#audio").evaluate("audio => audio.currentTime = 0.03")
    expect(page.locator('#passage-0 .passage-text [data-word="1"]')).to_have_class("word current")
    assert dialogs == []  # Cancel itself explicitly discards; it does not ask to switch drafts.
    assert content_state(store, rid) == before
    assert requests == []
    page.locator("#audio").evaluate("audio => audio.pause()")


def test_rejected_dirty_passage_switch_keeps_original_draft_caret_and_controls(page, local_app):
    store, rid, _ = seed_editor(page, local_app)
    before, requests = content_state(store, rid), mutations(page)
    page.locator('#passage-0 [data-action="edit"]').click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("An unsaved local correction")
    draft.evaluate("e => e.setSelectionRange(3, 7)")
    page.evaluate("window.originalEditorDraft = document.querySelector('#passage-0 textarea')")
    dialogs = []
    page.once("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
    page.locator('#passage-1 [data-action="edit"]').click()
    expect(draft).to_have_value("An unsaved local correction")
    expect(page.locator("#passage-1 textarea")).to_have_count(0)
    assert page.evaluate(
        "window.originalEditorDraft === document.querySelector('#passage-0 textarea')"
    )
    assert draft.evaluate("e => [e.selectionStart,e.selectionEnd]") == [3, 7]
    expect(page.locator('#passage-0 [data-action="save-edit"]')).to_be_enabled()
    assert dialogs == ["Discard this unsaved passage correction?"]
    assert content_state(store, rid) == before
    assert requests == []
