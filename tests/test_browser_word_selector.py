"""Word-time boxes replace the word dropdown: literal labels, keyboard use, long passages."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def open_words(page, local_app, words=None):
    store = local_app.server.config.app.state.store
    words = words or ["før", "<b>", "café,"]
    project = store.create_project("Word selector")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    source = normalize_segments(
        [
            {
                "text": " ".join(words),
                "start": 0,
                "end": len(words),
                "words": [
                    {"word": word, "start": i, "end": i + 0.8} for i, word in enumerate(words)
                ],
            }
        ]
    )
    store.execute("UPDATE recordings SET status='ready',duration=? WHERE id=?", (len(words), rid))
    result = store.add_result(rid, source, {}, "Synthetic", {}, auto_first=True)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator('#passage-0 [data-action="edit"]').click()
    return store, rid, result["id"], source[0], page.locator("#passage-0 .passage-editor")


def test_word_labels_are_literal_and_times_save_with_a_spelling_fix(page, local_app):
    store, rid, result_id, source, editor = open_words(page, local_app)
    expect(editor.locator("select")).to_have_count(0)
    draft = editor.locator("textarea")
    start = editor.locator('[data-word-time="start"]')
    end = editor.locator('[data-word-time="end"]')
    status = editor.locator("[data-word-times-status]")
    draft.evaluate("e => { e.focus(); e.setSelectionRange(5, 5); }")  # <|b>
    expect(status).to_have_text("“<b>”")
    expect(status.locator("b")).to_have_count(0)
    expect(start).to_have_value("1")
    expect(end).to_have_value("1.8")
    start_box, end_box = start.bounding_box(), end.bounding_box()
    assert end_box["x"] > start_box["x"] + start_box["width"]
    assert abs(start_box["y"] - end_box["y"]) < 2
    start.fill("1.1")
    end.fill("1.7")
    # Correct the word's spelling in the same draft: its typed times stay with it.
    draft.evaluate("e => { e.focus(); e.setSelectionRange(4, 7); }")
    draft.press_sequentially("β-rettet")
    expect(draft).to_have_value("før β-rettet café,")
    draft.evaluate("e => { e.focus(); e.setSelectionRange(9, 9); }")
    expect(status).to_have_text("“β-rettet”: unsaved times.")
    expect(start).to_have_value("1.1")
    editor.locator('[data-action="save-edit"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    saved = store.result(rid, result_id)["segments"][0]
    assert saved["text"] == "før β-rettet café,"
    assert (saved["words"][1]["word"], saved["words"][1]["start"], saved["words"][1]["end"]) == (
        "β-rettet",
        1.1,
        1.7,
    )
    assert saved["words"][2]["start"] == 2 and saved["words"][2]["word"] == "café,"
    assert saved["original_words"] == source["original_words"]
    assert len(store.result_versions(rid, result_id)) == 2
    recording = store.recording(rid)
    assert recording["result_roles"]["working"]["version_id"] == recording["active_version"]["id"]


def test_keyboard_reaches_the_boxes_and_returns_to_the_same_caret(page, local_app):
    store, rid, result_id, source, editor = open_words(page, local_app)
    draft = editor.locator("textarea")
    start = editor.locator('[data-word-time="start"]')
    end = editor.locator('[data-word-time="end"]')
    draft.press("Control+Home")
    draft.press("ArrowRight")  # f|ør
    expect(start).to_have_value("0")
    draft.press("Tab")
    expect(start).to_be_focused()
    page.keyboard.press("Control+a")
    page.keyboard.type("0.05")
    page.keyboard.press("Tab")
    expect(end).to_be_focused()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Shift+Tab")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [1, 1]
    expect(start).to_have_value("0.05")
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    # Text typing and its native Undo never touch the typed time of another word.
    draft.press("Control+End")
    draft.press_sequentially(" nu")
    expect(start).to_be_disabled()
    for _ in range(5):  # Chrome may undo typed characters in one or several steps.
        if draft.input_value() == source["text"]:
            break
        draft.press("Control+z")
    expect(draft).to_have_value(source["text"])
    draft.press("Control+Home")
    draft.press("ArrowRight")
    expect(start).to_have_value("0.05")
    editor.locator('[data-action="cancel-edit"]').click()
    expect(page.locator("#passage-0 .passage-editor")).to_have_count(0)
    assert store.result(rid, result_id)["segments"][0] == source


def test_long_passage_needs_no_word_list(page, local_app):
    _, _, _, _, editor = open_words(page, local_app, [f"ord{i}" for i in range(200)])
    draft = editor.locator("textarea")
    expect(editor.locator("option")).to_have_count(0)
    draft.press("Control+End")
    expect(editor.locator('[data-word-time="start"]')).to_be_disabled()  # after the last word
    draft.press("ArrowLeft")
    expect(editor.locator('[data-word-time="start"]')).to_have_value("199")
    expect(editor.locator("[data-word-times-status]")).to_have_text("“ord199”")
