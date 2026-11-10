"""A lazy word dropdown shares its column with deliberate spelling correction."""

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


def test_word_dropdown_labels_are_literal_and_save_times_and_spelling(page, local_app):
    store, rid, result_id, source, editor = open_words(page, local_app)
    selector = editor.locator("[data-timing-word]")
    expect(selector).to_have_count(1)
    expect(selector.locator("option")).to_have_count(0)
    editor.locator("[data-editor-mode]").select_option("timing")
    assert selector.evaluate("e => e.tagName") == "SELECT"
    assert selector.locator("option").all_text_contents() == ["1 – før", "2 – <b>", "3 – café,"]
    expect(selector.locator("b")).to_have_count(0)
    selector.select_option("2")
    expect(editor.locator("[data-timing-start]")).to_have_value("1")
    expect(editor.locator("[data-timing-end]")).to_have_value("1.8")
    expect(editor.locator("[data-timing-text]")).to_be_hidden()
    word_box = selector.bounding_box()
    start_box = editor.locator("[data-timing-start]").bounding_box()
    end_box = editor.locator("[data-timing-end]").bounding_box()
    assert start_box["x"] > word_box["x"] + word_box["width"]
    assert end_box["x"] > start_box["x"] + start_box["width"]
    assert abs(start_box["y"] - word_box["y"]) < 2

    # Timing can be saved without opening a separate spelling field.
    editor.locator("[data-timing-start]").fill("1.1")
    editor.locator("[data-timing-end]").fill("1.7")
    editor.locator('[data-action="save-timed-word"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(editor.locator("[data-timing-text]")).to_be_hidden()
    expect(selector).to_have_value("2")
    assert store.result(rid, result_id)["segments"][0]["words"][1]["start"] == 1.1

    editor.locator('[data-action="edit-timed-word"]').click()
    correction = editor.locator("[data-timing-text]")
    expect(correction).to_be_focused()
    correction.fill("β-rettet")
    correction.evaluate("e => e.setSelectionRange(2, 5)")
    editor.locator('[data-action="save-timed-word"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(correction).to_be_focused()
    expect(correction).to_have_value("β-rettet")
    assert correction.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    expect(selector.locator('option[value="2"]')).to_have_text("2 – β-rettet")
    saved = store.result(rid, result_id)["segments"][0]
    assert saved["text"] == "før β-rettet café,"
    assert saved["words"][1]["start"] == 1.1 and saved["words"][1]["end"] == 1.7
    assert saved["original_words"] == source["original_words"]
    recording = store.recording(rid)
    assert recording["result_roles"]["working"]["version_id"] == recording["active_version"]["id"]
    editor.locator(".passage-word-fields").screenshot(path="test-results/word-selector.png")


def test_keyboard_choice_keeps_hidden_unsaved_word_until_confirmed_discard(page, local_app):
    store, rid, result_id, source, editor = open_words(page, local_app)
    editor.locator("[data-editor-mode]").select_option("timing")
    selector = editor.locator("[data-timing-word]")
    selector.focus()
    selector.press("ArrowDown")
    expect(selector).to_have_value("2")
    expect(editor.locator("[data-timing-start]")).to_have_value("1")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("Unsaved Ω")
    editor.locator('[data-action="edit-timed-word"]').click()
    expect(editor.locator("[data-timing-text]")).to_be_hidden()
    page.once("dialog", lambda dialog: dialog.dismiss())
    selector.press("ArrowUp")
    expect(selector).to_have_value("2")
    expect(editor.locator("[data-timing-text]")).to_have_value("Unsaved Ω")
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    page.once("dialog", lambda dialog: dialog.accept())
    editor.locator('[data-action="next-timed-word"]').click()
    expect(selector).to_have_value("3")
    expect(editor.locator("[data-timing-text]")).to_have_value("café,")
    editor.locator('[data-editor-pane="timing"] [data-action="cancel-edit"]').click()
    expect(page.locator("#passage-0 .passage-editor")).to_have_count(0)
    assert store.result(rid, result_id)["segments"][0] == source


def test_long_word_list_is_populated_only_when_timing_is_opened(page, local_app):
    _, _, _, _, editor = open_words(page, local_app, [f"ord{i}" for i in range(200)])
    selector = editor.locator("[data-timing-word]")
    expect(selector.locator("option")).to_have_count(0)
    editor.locator("[data-editor-mode]").select_option("timing")
    expect(selector.locator("option")).to_have_count(200)
    selector.select_option("200")
    expect(selector.locator("option:checked")).to_have_text("200 – ord199")
    expect(editor.locator("[data-timing-start]")).to_have_value("199")
    editor.locator('[data-action="previous-timed-word"]').click()
    expect(selector).to_have_value("199")
    expect(editor.locator("[data-timing-text]")).to_be_hidden()
