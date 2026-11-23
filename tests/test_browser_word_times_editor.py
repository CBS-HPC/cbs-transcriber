"""Word-timing editor: start/end boxes for the word at the Text cursor (ADR 0195).

Synthetic libraries and generated silent playback only.
"""

import io
import wave

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import edit_manual_text_retaining_timing, normalize_segments

pytestmark = pytest.mark.browser

TEXT = "Hej verden i dag"
WORDS = [
    {"word": "Hej", "start": 0.1, "end": 0.8},
    {"word": "verden", "start": 1, "end": 1.8},
    {"word": "i", "start": 2, "end": 2.1},
    {"word": "dag", "start": 2.3, "end": 2.9},
]


def seed(local_app, text=TEXT, words=WORDS, *, end=3):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic word times editor")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    store.execute("UPDATE recordings SET status='ready',duration=8 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": text, "start": 0, "end": end, "words": words},
                {
                    "text": "Anden passage",
                    "start": end,
                    "end": end + 2,
                    "words": [
                        {"word": "Anden", "start": end + 0.1, "end": end + 0.6},
                        {"word": "passage", "start": end + 0.8, "end": end + 1.5},
                    ],
                },
            ]
        ),
        {},
        "Synthetic result",
        {"capability": "words"},
    )
    return store, rid, result["id"]


def open_editor(page, local_app, **kwargs):
    store, rid, result_id = seed(local_app, **kwargs)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(8 * 16000 * 2))
    silence = stream.getvalue()
    page.route(
        f"**/api/recordings/{rid}/media/audio",
        lambda route: route.fulfill(content_type="audio/wav", body=silence),
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Set edited version as Working"
    ).click()
    editor = passage.locator(".passage-editor")
    expect(editor.locator("textarea")).to_be_focused()
    return store, rid, result_id, editor


def caret(editor, start, end=None):
    """Place the native caret/selection by UTF-16 offsets, as a click or arrow key would."""
    editor.locator("textarea").evaluate(
        "(node, [start, end]) => { node.focus(); node.setSelectionRange(start, end); }",
        [start, start if end is None else end],
    )


def boxes(editor):
    return (
        editor.locator('[data-word-time="start"]'),
        editor.locator('[data-word-time="end"]'),
        editor.locator("[data-word-times-status]"),
    )


def expect_word(editor, start, end, label):
    first, last, status = boxes(editor)
    expect(first).to_be_enabled()
    expect(first).to_have_value(start)
    expect(last).to_have_value(end)
    expect(status).to_contain_text(label)


def expect_empty(editor, message="Put the cursor in a word"):
    first, last, status = boxes(editor)
    expect(first).to_be_disabled()
    expect(last).to_be_disabled()
    expect(first).to_have_value("")
    expect(last).to_have_value("")
    expect(status).to_contain_text(message)


def test_cursor_in_a_word_between_words_and_one_character_words(page, local_app):
    _, _, _, editor = open_editor(page, local_app)
    expect(editor.locator("[data-editor-mode]")).to_have_count(0)
    expect(editor.locator("[data-timing-word]")).to_have_count(0)
    expect_empty(editor)  # The caret starts after the last word.
    caret(editor, 6)  # ve|rden
    expect_word(editor, "1", "1.8", "“verden”")
    for position in (3, 4, 10, 13, 16):  # Hej| |verden verden| |dag dag|
        caret(editor, position)
        expect_empty(editor)
    caret(editor, 1)
    expect_word(editor, "0.1", "0.8", "“Hej”")
    # A one-character word has no inside: the caret on either side of it selects it.
    caret(editor, 11)  # |i
    expect_word(editor, "2", "2.1", "“i”")
    caret(editor, 12)  # i|
    expect_word(editor, "2", "2.1", "“i”")
    caret(editor, 14)
    expect_word(editor, "2.3", "2.9", "“dag”")
    help_text = editor.locator("[data-word-times]").get_attribute("title")
    assert "Between words" in help_text and "Cancel discards both" in help_text


def test_selections_within_one_word_show_it_and_several_words_show_nothing(page, local_app):
    _, _, _, editor = open_editor(page, local_app)
    caret(editor, 4, 10)  # verden
    expect_word(editor, "1", "1.8", "“verden”")
    caret(editor, 4, 11)  # "verden " (a double click can include the space)
    expect_word(editor, "1", "1.8", "“verden”")
    caret(editor, 5, 7)  # part of one word
    expect_word(editor, "1", "1.8", "“verden”")
    caret(editor, 11, 12)  # the one-character word itself
    expect_word(editor, "2", "2.1", "“i”")
    caret(editor, 0, 10)  # Hej verden
    expect_empty(editor, "Select a single word")
    caret(editor, 9, 12)  # en i
    expect_empty(editor, "Select a single word")
    caret(editor, 3, 4)  # only a space
    expect_empty(editor)


def test_text_and_times_save_as_one_version_and_undo_restores_both(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    before = store.result(rid, result_id)["segments"]
    versions = len(store.result_versions(rid, result_id))
    first, last, status = boxes(editor)
    save = editor.locator('[data-action="save-edit"]')
    expect(save).to_be_disabled()
    caret(editor, 6)
    first.fill("1.05")
    last.fill("1,7")  # A decimal comma is accepted.
    expect(status).to_contain_text("unsaved times")
    expect(save).to_be_enabled()
    expect(page.locator("#undo-last")).to_be_disabled()
    # The text draft changes too: a new word, which stays blank until times are typed.
    textarea = editor.locator("textarea")
    caret(editor, 3)
    textarea.press_sequentially(" smukke")
    expect(textarea).to_have_value("Hej smukke verden i dag")
    caret(editor, 6)
    expect_word(editor, "", "", "“smukke” is new or changed")
    first.fill("0.85")
    last.fill("0.95")
    caret(editor, 14)  # the typed times stayed with "verden"
    expect_word(editor, "1.05", "1,7", "“verden”: unsaved times")
    with page.expect_response(lambda response: response.url.endswith("/segments/0/edit-v4")):
        save.click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(page.locator("#passage-0 .passage-editor")).to_have_count(0)
    after = store.result(rid, result_id)["segments"]
    assert after[0]["text"] == "Hej smukke verden i dag"
    words = after[0]["words"]
    assert [(w["word"], w["start"], w["end"]) for w in words] == [
        ("Hej", 0.1, 0.8),
        ("smukke", 0.85, 0.95),
        ("verden", 1.05, 1.7),
        ("i", 2, 2.1),
        ("dag", 2.3, 2.9),
    ]
    assert words[1]["manual_timing"] is True and words[2]["manual_timing"] is True
    assert after[1] == before[1]
    history = store.result_versions(rid, result_id)
    assert len(history) == versions + 1
    assert history[0]["action"] == "free_rewrite"
    recording = store.recording(rid)
    assert recording["result_roles"]["working"]["version_id"] == history[0]["id"]
    undo = page.locator("#undo-last")
    expect(undo).to_have_text("↶ Undo last text edit")
    undo.click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(TEXT)
    assert store.result(rid, result_id)["segments"] == before


def test_times_only_save_and_cancel_discards_typed_times(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    before = store.result(rid, result_id)["segments"]
    versions = len(store.result_versions(rid, result_id))
    first, last, _ = boxes(editor)
    caret(editor, 1)
    first.fill("0.2")
    expect(editor.locator('[data-action="save-edit"]')).to_be_enabled()
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    editor.locator('[data-action="cancel-edit"]').click()
    expect(page.locator("#passage-0 .passage-editor")).to_have_count(0)
    assert store.result(rid, result_id)["segments"] == before
    assert len(store.result_versions(rid, result_id)) == versions
    page.locator('#passage-0 [data-action="edit"]').click()
    page.get_by_role("button", name="Set edited version as Working").click()
    editor = page.locator("#passage-0 .passage-editor")
    first, last, _ = boxes(editor)
    caret(editor, 1)
    expect_word(editor, "0.1", "0.8", "“Hej”")  # Cancel discarded the typed time.
    first.fill("0.2")
    last.fill("0.7")
    with page.expect_response(lambda response: response.url.endswith("/segments/0/edit-v4")):
        editor.locator('[data-action="save-edit"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    history = store.result_versions(rid, result_id)
    assert len(history) == versions + 1
    assert history[0]["action"] == "word_timing_edit"
    assert history[0]["context"]["word_index"] == 0
    word = store.result(rid, result_id)["segments"][0]["words"][0]
    assert (word["start"], word["end"], word["manual_timing"]) == (0.2, 0.7, True)
    expect(page.locator("#undo-last")).to_have_text("↶ Undo last word timing edit")


@pytest.mark.parametrize(
    "start, end, message",
    [
        ("1.5", "1.2", "“verden”: the start must be before the end."),
        ("1", "3.5", "“verden”: the times must be within the passage (0–3 s)."),
        ("2.05", "2.08", "“verden”: the times must stay in order with “i”."),
        ("1", "abc", "“verden”: type the times in seconds"),
        ("", "1.5", "“verden”: type both a start and an end time."),
    ],
)
def test_invalid_times_are_refused_with_a_clear_message(page, local_app, start, end, message):
    store, rid, result_id, editor = open_editor(page, local_app)
    versions = len(store.result_versions(rid, result_id))
    first, last, status = boxes(editor)
    caret(editor, 6)
    first.fill(start)
    last.fill(end)
    expect(status).to_contain_text(message)
    expect(first).to_have_attribute("aria-invalid", "true")
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    editor.locator('[data-action="save-edit"]').click()
    expect(page.locator("#toast")).to_contain_text(f"Not saved: {message}")
    assert not [url for url in requests if "/segments/" in url]
    expect(first).to_have_value(start)
    expect(editor.locator("textarea")).to_have_value(TEXT)
    assert len(store.result_versions(rid, result_id)) == versions


def test_server_refusal_and_late_reply_keep_the_typed_draft(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    first, last, _ = boxes(editor)
    caret(editor, 6)
    first.fill("1.1")
    held = []

    def hold(route):
        held.append(route)
        page.evaluate("window.timesHeld = true")

    page.route(f"**/api/recordings/{rid}/segments/0/edit-v4", hold)
    editor.locator('[data-action="save-edit"]').click()
    page.wait_for_function("() => window.timesHeld === true")
    expect(first).to_be_disabled()
    expect(editor.locator("textarea")).to_be_disabled()
    held.pop().fulfill(status=400, json={"detail": "Word times must remain in passage order."})
    expect(page.locator("#toast")).to_contain_text("Word times must remain in passage order.")
    expect(page.locator("#save-status")).to_have_text("Save failed — correction retained")
    expect(first).to_be_enabled()
    expect(first).to_have_value("1.1")
    expect(last).to_have_value("1.8")
    assert store.result(rid, result_id)["segments"][0]["words"][1]["start"] == 1


def test_unicode_and_emoji_caret_positions(page, local_app):
    text = "Hej 👍🏽 smør😀brød igen"
    words = [
        {"word": "Hej", "start": 0.1, "end": 0.5},
        {"word": "👍🏽", "start": 0.6, "end": 0.9},
        {"word": "smør😀brød", "start": 1, "end": 1.9},
        {"word": "igen", "start": 2, "end": 2.6},
    ]
    store, rid, result_id, editor = open_editor(page, local_app, text=text, words=words)
    # UTF-16 offsets: "👍🏽" is four code units and one grapheme; "😀" is two code units.
    caret(editor, 4)  # |👍🏽
    expect_word(editor, "0.6", "0.9", "“👍🏽”")
    caret(editor, 8)  # 👍🏽|
    expect_word(editor, "0.6", "0.9", "“👍🏽”")
    caret(editor, 13)  # smør|😀
    expect_word(editor, "1", "1.9", "“smør😀brød”")
    caret(editor, 15)  # 😀|brød
    expect_word(editor, "1", "1.9", "“smør😀brød”")
    caret(editor, 14)  # inside the emoji's surrogate pair: never a word
    expect_empty(editor)
    caret(editor, 19)  # smør😀brød| igen
    expect_empty(editor)
    caret(editor, 23)
    expect_word(editor, "2", "2.6", "“igen”")
    first, last, _ = boxes(editor)
    first.fill("2.05")
    with page.expect_request(lambda request: request.url.endswith("/edit-v4")) as sent:
        editor.locator('[data-action="save-edit"]').click()
    body = sent.value.post_data_json
    assert body["text"] == text
    assert body["word_times"] == [
        {"char_start": 17, "char_end": 21, "word": "igen", "start": 2.05, "end": 2.6}
    ]
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    assert store.result(rid, result_id)["segments"][0]["words"][3]["start"] == 2.05


def test_spelling_fix_keeps_typed_times_and_removing_the_word_drops_them(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    first, last, _ = boxes(editor)
    textarea = editor.locator("textarea")
    caret(editor, 6)
    first.fill("1.1")
    caret(editor, 10)
    textarea.press_sequentially("en")  # verden -> verdenen
    caret(editor, 7)
    expect_word(editor, "1.1", "1.8", "“verdenen”: unsaved times")
    caret(editor, 0, 4)
    textarea.press("Delete")  # removes "Hej " only
    caret(editor, 2)
    expect_word(editor, "1.1", "1.8", "“verdenen”")
    caret(editor, 0, 9)
    textarea.press("Delete")  # removes the word itself
    expect(page.locator("#toast")).to_contain_text("“verdenen” were removed")
    expect(textarea).to_have_value("i dag")
    # Restoring the text by hand does not bring back the typed time; the saved one shows.
    textarea.press_sequentially("verden ")
    caret(editor, 2)
    expect_word(editor, "1", "1.8", "“verden”")


@pytest.mark.parametrize("delete_first", [True, False])
def test_deleting_a_repeated_word_keeps_only_the_surviving_draft_time(
    page, local_app, delete_first
):
    store, rid, result_id, editor = open_editor(
        page,
        local_app,
        text="ord ord",
        words=[
            {"word": "ord", "start": 0, "end": 0.8},
            {"word": "ord", "start": 1, "end": 1.8},
        ],
    )
    caret(editor, 1)
    first, _, _ = boxes(editor)
    first.fill("0.15")
    caret(editor, *(0, 4) if delete_first else (3, 7))
    editor.locator("textarea").press("Delete")
    caret(editor, 1)
    if delete_first:
        expect(page.locator("#toast")).to_contain_text("“ord” were removed")
        expect_word(editor, "", "", "new or changed")
    else:
        expect_word(editor, "0.15", "0.8", "unsaved times")
    editor.locator('[data-action="save-edit"]').click()
    if delete_first:
        expect(page.locator("#save-status")).to_contain_text("Some words have no verified timing")
    else:
        expect(page.locator("#save-status")).to_have_text("All changes saved")
    word = store.result(rid, result_id)["segments"][0]["words"][0]
    if delete_first:
        assert word["start"] is None and word["end"] is None
        assert not word.get("manual_timing")
    else:
        assert word["start"] == 0.15 and word["manual_timing"] is True


def test_inserting_a_matching_word_before_preserves_the_actual_typed_occurrence(page, local_app):
    store, rid, result_id, editor = open_editor(
        page,
        local_app,
        text="ord mere",
        words=[
            {"word": "ord", "start": 0.1, "end": 0.8},
            {"word": "mere", "start": 1, "end": 1.8},
        ],
    )
    first, _, _ = boxes(editor)
    caret(editor, 1)
    first.fill("0.2")
    caret(editor, 0)
    page.keyboard.insert_text("ord ")
    expect(editor.locator("textarea")).to_have_value("ord ord mere")
    caret(editor, 1)
    expect_word(editor, "", "", "new or changed")
    caret(editor, 5)
    expect_word(editor, "0.2", "0.8", "unsaved times")
    editor.locator('[data-action="save-edit"]').click()
    expect(page.locator("#save-status")).to_contain_text("Some words have no verified timing")
    words = store.result(rid, result_id)["segments"][0]["words"]
    assert words[0]["start"] is None and words[0]["end"] is None
    assert not words[0].get("manual_timing")
    assert words[1].get("manual_timing") and words[1]["start"] == 0.2


def test_ambiguous_replacement_without_an_edit_range_drops_times(page, local_app):
    _, _, _, editor = open_editor(
        page,
        local_app,
        text="ord ord",
        words=[
            {"word": "ord", "start": 0, "end": 0.8},
            {"word": "ord", "start": 1, "end": 1.8},
        ],
    )
    caret(editor, 1)
    first, _, _ = boxes(editor)
    first.fill("0.15")
    editor.locator("textarea").evaluate(
        """node => {
            node.value = 'ord';
            node.dispatchEvent(new Event('input', { bubbles: true }));
        }"""
    )
    expect(page.locator("#toast")).to_contain_text("could not be matched safely")
    caret(editor, 1)
    expect_word(editor, "", "", "new or changed")


def test_draft_switch_guards_cover_typed_times(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    first, _, _ = boxes(editor)
    caret(editor, 6)
    first.fill("1.2")
    expect(page.locator("#undo-last")).to_be_disabled()
    expect(editor.locator('[data-action="split"]')).to_be_disabled()
    messages = []

    def dismiss(dialog):
        messages.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", dismiss)
    page.locator('#passage-1 [data-action="edit"]').click()
    assert messages == ["Discard this unsaved passage correction?"]
    expect(first).to_have_value("1.2")
    expect(page.locator("#passage-1 .passage-editor")).to_have_count(0)
    # A double click on another passage's words never replaces a dirty draft.
    page.locator('#passage-1 [data-word="0"]').dblclick()
    expect(page.locator("#passage-1 .passage-editor")).to_have_count(0)
    expect(first).to_have_value("1.2")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-1 [data-action="edit"]').click()
    page.get_by_role("button", name="Set edited version as Working").click()
    expect(page.locator("#passage-1 .passage-editor")).to_have_count(1)
    expect(page.locator("#passage-0 .passage-editor")).to_have_count(0)
    assert store.result(rid, result_id)["segments"][0]["words"][1]["start"] == 1


def test_passage_without_word_map_or_bounds_keeps_boxes_disabled(page, local_app):
    store, rid, result_id = seed(local_app, words=[])
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.get_by_role("button", name="Set edited version as Working").click()
    editor = page.locator("#passage-0 .passage-editor")
    caret(editor, 6)
    expect_empty(editor, "no usable word timing")


def test_rename_recording_preserves_open_text_and_word_time_draft(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    revision = store.recording(rid)["revision"]
    textarea = editor.locator("textarea")
    caret(editor, len(TEXT))
    textarea.press("!")
    caret(editor, 6)
    first, _, _ = boxes(editor)
    first.fill("1.2")

    page.locator("#recording-rename").click()
    page.locator("#name-input").fill("Renamed synthetic recording")
    page.locator("#name-input").press("Enter")
    expect(page.locator("#recording-title")).to_have_text("Renamed synthetic recording")
    assert store.recording(rid)["revision"] == revision

    # Save without returning to the textarea first. The metadata response has
    # replaced the parsed segment, while the same native draft remains open.
    editor.get_by_role("button", name="Save passage").click()
    expect(editor).to_have_count(0)
    saved = store.result(rid, result_id)["segments"][0]
    assert saved["text"] == "Hej verden i dag!"
    assert saved["words"][1]["start"] == 1.2


def test_newer_transcript_revision_cannot_silently_replace_a_timing_draft(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    caret(editor, 6)
    first, _, _ = boxes(editor)
    first.fill("1.2")
    before = store.recording(rid)
    changed = before["segments"][:]
    changed[0], _ = edit_manual_text_retaining_timing(changed[0], "Hej verden i dag igen")
    newer = store.save_transcript(rid, changed, before["speakers"], before["revision"])
    assert newer["revision"] > before["revision"]
    history_count = len(store.result_versions(rid, result_id))

    page.locator("#recording-rename").click()
    page.locator("#name-input").fill("Renamed synthetic recording")
    page.locator("#name-input").press("Enter")
    expect(page.locator("#recording-title")).to_have_text("Renamed synthetic recording")

    caret(editor, 6)
    expect_empty(editor, "This passage changed while you were editing.")
    editor.get_by_role("button", name="Save passage").click()
    expect(page.locator("#toast")).to_contain_text("This passage changed while you were editing.")
    expect(editor.locator("textarea")).to_have_value(TEXT)
    assert store.recording(rid)["revision"] == newer["revision"]
    assert len(store.result_versions(rid, result_id)) == history_count


def test_composition_keeps_typed_times_when_the_interim_word_split_disappears(page, local_app):
    store, rid, result_id, editor = open_editor(page, local_app)
    first, _, _ = boxes(editor)
    caret(editor, 6)
    first.fill("1.2")
    caret(editor, 6)
    textarea = editor.locator("textarea")

    textarea.evaluate(
        """(input) => {
            input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
            input.value = "Hej ver den i dag";
            input.dispatchEvent(new InputEvent("input", {
                bubbles: true, inputType: "insertCompositionText", isComposing: true,
            }));
        }"""
    )
    expect(textarea).to_have_value("Hej ver den i dag")
    expect(textarea).to_be_focused()
    expect(first).to_be_disabled()
    editor.get_by_role("button", name="Save passage").evaluate("(button) => button.click()")
    expect(page.locator("#toast")).to_contain_text("Finish composing the text before saving")
    assert store.result(rid, result_id)["segments"][0]["text"] == TEXT

    textarea.evaluate(
        """(input, text) => {
            input.value = text;
            input.setSelectionRange(6, 6);
            input.dispatchEvent(new InputEvent("input", {
                bubbles: true, inputType: "insertCompositionText", isComposing: true,
            }));
            input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
        }""",
        TEXT,
    )
    expect(textarea).to_have_value(TEXT)
    expect(textarea).to_be_focused()
    assert textarea.evaluate("(input) => [input.selectionStart, input.selectionEnd]") == [6, 6]
    expect_word(editor, "1.2", "1.8", "unsaved times")
    editor.get_by_role("button", name="Save passage").click()
    expect(editor).to_have_count(0)
    assert store.result(rid, result_id)["segments"][0]["words"][1]["start"] == 1.2
