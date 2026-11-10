"""Rendered-word double clicks open Text without replacing a live passage draft."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def open_timed_recording(page, local_app, source=None):
    store = local_app.server.config.app.state.store
    project = store.create_project("Double click Text")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    segments = normalize_segments(
        source
        if source is not None
        else [
            {
                "text": "First words",
                "start": 0,
                "end": 2,
                "words": [
                    {"word": "First", "start": 0, "end": 0.8},
                    {"word": "words", "start": 1, "end": 1.8},
                ],
            },
            {
                "text": "Second passage",
                "start": 2,
                "end": 4,
                "words": [
                    {"word": "Second", "start": 2, "end": 2.8},
                    {"word": "passage", "start": 3, "end": 3.8},
                ],
            },
        ]
    )
    store.add_result(rid, segments, {}, "Synthetic", {}, auto_first=True)
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=330:duration=4",
            "-ac",
            "1",
            str(directory / "audio.mp3"),
        ]
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    return store, rid


@pytest.mark.parametrize("legacy_mode", ["free", "word"])
def test_word_click_seeks_and_double_click_opens_text_with_explicit_timing_available(
    page, local_app, legacy_mode
):
    store, rid = open_timed_recording(page, local_app)
    before = store.recording(rid)
    page.evaluate("mode => {state.mode = mode; renderTranscript();}", legacy_mode)
    passage = page.locator("#passage-0")
    word = passage.locator('[data-word="1"]')
    word.click()
    assert page.locator("#audio").evaluate("e => e.currentTime") == pytest.approx(1, abs=0.1)
    expect(passage.locator(".passage-editor")).to_have_count(0)
    word.dblclick()
    editor = passage.locator(".passage-editor")
    expect(editor.locator("[data-editor-mode]")).to_have_value("text")
    expect(editor.locator('[data-editor-pane="text"]')).to_be_visible()
    expect(editor.locator('[data-editor-pane="timing"]')).to_be_hidden()
    expect(editor.locator("textarea")).to_be_focused()
    expect(editor.locator("textarea")).to_have_value("First words")
    assert editor.locator("textarea").evaluate(
        "e => [e.selectionStart, e.selectionEnd, e.value.slice(e.selectionStart, e.selectionEnd)]"
    ) == [6, 11, "words"]
    expect(page.locator("#word-dialog")).to_be_hidden()
    expect(editor.locator("[data-timing-word] option")).to_have_count(0)
    editor.locator("[data-editor-mode]").select_option("timing")
    editor.locator("[data-timing-word]").select_option("2")
    expect(editor.locator("[data-timing-start]")).to_have_value("1")
    editor.locator('[data-action="edit-timed-word"]').click()
    expect(editor.locator("[data-timing-text]")).to_be_focused()
    expect(editor.locator("[data-timing-text]")).to_have_value("words")
    editor.locator('[data-editor-pane="timing"] [data-action="cancel-edit"]').click()
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["revision"] == before["revision"]
    assert after["result_roles"] == before["result_roles"]
    assert store.all("SELECT id FROM jobs") == []


def test_double_click_selects_exact_repeated_word_after_astral_prefix_and_crlf(page, local_app):
    text = "😀🧪 repeat\r\nrepeat repeat"
    store, rid = open_timed_recording(
        page,
        local_app,
        [
            {
                "text": text,
                "start": 0,
                "end": 4,
                "words": [
                    {"word": word, "start": index * 0.7, "end": index * 0.7 + 0.6}
                    for index, word in enumerate(["😀", "🧪", "repeat", "repeat", "repeat"])
                ],
            }
        ],
    )
    before = store.recording(rid)
    result_id = before["active_transcript_id"]
    versions = len(store.result_versions(rid, result_id))
    assert before["segments"][0]["text"] == text
    clicked = before["segments"][0]["words"][4]
    assert (clicked["char_start"], clicked["char_end"]) == (18, 24)
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}
            else None
        ),
    )
    page.locator('#passage-0 .passage-text [data-word="4"]').dblclick()
    draft = page.locator("#passage-0 textarea")
    expect(draft).to_be_focused()
    expect(draft).to_have_value("😀🧪 repeat\nrepeat repeat")
    assert draft.evaluate(
        "e => [e.selectionStart, e.selectionEnd, e.value.slice(e.selectionStart, e.selectionEnd)]"
    ) == [19, 25, "repeat"]
    assert not writes
    assert store.recording(rid) == before
    assert len(store.result_versions(rid, result_id)) == versions
    assert store.all("SELECT id FROM jobs") == []


def test_word_double_click_ignores_previous_clean_timing_mode(page, local_app):
    _, _ = open_timed_recording(page, local_app)
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 [data-editor-mode]").select_option("timing")
    page.locator('#passage-1 .passage-text [data-word="0"]').dblclick()
    expect(page.locator("#passage-0 .passage-editor")).to_have_count(0)
    expect(page.locator("#passage-1 [data-editor-mode]")).to_have_value("text")
    expect(page.locator("#passage-1 textarea")).to_be_focused()
    expect(page.locator("#passage-1 textarea")).to_have_value("Second passage")


def test_unbounded_playback_word_opens_text_without_enabling_timing_edits(page, local_app):
    _, _ = open_timed_recording(page, local_app)
    # Valid legacy word intervals can remain usable for playback without passage bounds.
    page.evaluate("""() => {
        const segment = state.recording.segments[0];
        segment.start = segment.end = null;
        segment.word_timing = false;
        segment.words_current = true;
        renderTranscript();
    }""")
    page.locator('#passage-0 .passage-text [data-word="0"]').dblclick()
    editor = page.locator("#passage-0 .passage-editor")
    expect(editor.locator("[data-editor-mode]")).to_have_value("text", timeout=2000)
    expect(editor.locator("textarea")).to_have_value("First words")
    expect(editor.locator("[data-editor-mode] option[value='timing']")).to_have_attribute(
        "disabled", ""
    )


@pytest.mark.parametrize("pane", ["text", "timing"])
def test_preview_or_other_word_double_click_never_replaces_a_dirty_draft(page, local_app, pane):
    store, rid = open_timed_recording(page, local_app)
    before = store.recording(rid)
    page.locator('#passage-0 [data-action="edit"]').click()
    editor = page.locator("#passage-0 .passage-editor")
    if pane == "timing":
        editor.locator("[data-editor-mode]").select_option("timing")
        editor.locator('[data-action="edit-timed-word"]').click()
        draft = editor.locator("[data-timing-text]")
    else:
        draft = editor.locator("textarea")
    draft.fill("A retained draft")
    draft.evaluate("e => e.setSelectionRange(2, 6)")
    editor.evaluate("e => window.originalDraftEditor = e")
    if pane == "timing":
        editor.locator('.editor-playback-preview [data-word="1"]').dblclick()
    else:
        expect(editor.locator(".editor-playback-preview")).to_be_hidden()
        editor.locator('[data-action="editor-replay"]').click()
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 6]
    dialogs = []

    def unexpected_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", unexpected_dialog)
    page.locator('#passage-1 .passage-text [data-word="0"]').dblclick()
    assert not dialogs
    assert editor.evaluate("e => e === window.originalDraftEditor")
    expect(editor.locator("[data-editor-mode]")).to_have_value(pane)
    expect(draft).to_have_value("A retained draft")
    expect(page.locator("#passage-1 .passage-editor")).to_have_count(0)
    assert store.recording(rid)["segments"] == before["segments"]
    assert store.recording(rid)["revision"] == before["revision"]
