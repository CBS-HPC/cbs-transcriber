"""Editing one mapped word in the passage editor keeps unrelated evidence intact."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def timed_recording(local_app, *, partial=False):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic word timing")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=2 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": "Hej verden",
                    "start": 0,
                    "end": 2,
                    "words": [
                        {
                            "word": "Hej",
                            "start": None if partial else 0.1,
                            "end": None if partial else 0.8,
                        },
                        {"word": "verden", "start": 1, "end": 1.8},
                    ],
                }
            ]
        ),
        {},
        "Synthetic result",
        {"capability": "words"},
    )
    return store, rid, result["id"]


def open_editor(page, local_app, *, partial=False):
    store, rid, result_id = timed_recording(local_app, partial=partial)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Set edited version as Working"
    ).click()
    return store, rid, result_id, passage


def test_inline_timing_edit_preserves_drafts_and_working_choice(page, local_app):
    store, rid, result_id, passage = open_editor(page, local_app)
    before = store.result(rid, result_id)["segments"][0]
    editor = passage.locator(".passage-editor")
    text = editor.locator("textarea")
    text.fill("A separate unsaved draft")
    mode = editor.locator("[data-editor-mode]")
    mode.select_option("timing")
    save_word = editor.locator('[data-action="save-timed-word"]')
    expect(save_word).to_be_disabled()
    expect(editor.locator("[data-timing-draft-note]")).to_be_visible()
    mode.select_option("text")
    expect(text).to_have_value("A separate unsaved draft")
    text.fill("Hej verden")
    mode.select_option("timing")
    expect(save_word).to_be_enabled()
    editor.locator('[data-action="edit-timed-word"]').click()
    word = editor.locator("[data-timing-text]")
    assert word.evaluate("e => e.spellcheck") is False
    word.fill("Hejsa")
    word.evaluate("e => e.setSelectionRange(2, 2)")
    editor.locator("[data-timing-start]").fill("0.2")
    editor.locator("[data-timing-end]").fill("0.9")
    save_word.click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(editor.locator("[data-editor-mode]")).to_have_value("timing")
    expect(editor.locator("[data-timing-text]")).to_have_value("Hejsa")
    expect(editor.locator("[data-timing-text]")).to_be_focused()
    assert editor.locator("[data-timing-text]").evaluate("e => e.selectionStart") == 2
    after = store.result(rid, result_id)["segments"][0]
    assert after["words"][0]["start"] == 0.2
    assert after["words"][0]["end"] == 0.9
    assert after["words"][0]["word"] == "Hejsa"
    assert after["words"][1] == before["words"][1] | {"char_start": 6, "char_end": 12}
    assert after["original_words"] == before["original_words"]
    assert after["words"][0]["manual_timing"] is True
    recording = store.recording(rid)
    assert recording["result_roles"]["working"]["version_id"] == recording["active_version"]["id"]


def test_word_switch_refuses_to_discard_unsaved_correction(page, local_app):
    _, _, _, passage = open_editor(page, local_app)
    editor = passage.locator(".passage-editor")
    editor.locator("[data-editor-mode]").select_option("timing")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("Draft")
    page.once("dialog", lambda dialog: dialog.dismiss())
    editor.locator('[data-action="next-timed-word"]').click()
    expect(editor.locator("[data-timing-word]")).to_have_value("1")
    expect(editor.locator("[data-timing-text]")).to_have_value("Draft")
    editor.locator('[data-action="reset-timed-word"]').click()
    expect(editor.locator("[data-timing-text]")).to_have_value("Hej")


@pytest.mark.parametrize("choice", [False, True])
def test_realign_choice_survives_word_save(page, local_app, choice):
    _, _, _, passage = open_editor(page, local_app)
    editor = passage.locator(".passage-editor")
    realign = editor.locator("[data-editor-realign]")
    realign.set_checked(choice)
    editor.locator("[data-editor-mode]").select_option("timing")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("Hejsa")
    editor.locator('[data-action="save-timed-word"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(editor.locator("[data-timing-text]")).to_have_value("Hejsa")
    editor.locator("[data-editor-mode]").select_option("text")
    assert realign.is_checked() is choice


@pytest.mark.parametrize("refused", [False, True])
def test_word_save_freezes_draft_and_preserves_it_on_refusal(page, local_app, refused):
    store, rid, result_id, passage = open_editor(page, local_app)
    editor = passage.locator(".passage-editor")
    editor.locator("[data-editor-mode]").select_option("timing")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("Correction")
    held = []

    def hold(route):
        held.append((route, None if refused else route.fetch()))
        page.evaluate("window.wordReplyHeld = true")

    page.route(f"**/api/recordings/{rid}/segments/0", hold)
    editor.locator('[data-action="save-timed-word"]').click()
    page.wait_for_function("() => window.wordReplyHeld === true")
    expect(editor.locator("[data-timing-text]")).to_be_disabled()
    expect(editor.locator("[data-editor-mode]")).to_be_disabled()
    route, response = held.pop()
    if refused:
        route.fulfill(status=409, json={"detail": "A newer revision was saved."})
        expect(editor.locator("[data-timing-text]")).to_be_enabled()
        expect(editor.locator("[data-timing-text]")).to_have_value("Correction")
        assert store.result(rid, result_id)["segments"][0]["text"] == "Hej verden"
    else:
        route.fulfill(response=response)
        expect(page.locator("#save-status")).to_have_text("All changes saved")
        expect(editor.locator("[data-timing-text]")).to_have_value("Correction")
        assert store.result(rid, result_id)["segments"][0]["text"] == "Correction verden"


def test_partial_word_spelling_keeps_missing_timestamps(page, local_app):
    store, rid, result_id, passage = open_editor(page, local_app, partial=True)
    before = store.result(rid, result_id)["segments"][0]
    assert before["word_timing"] is False
    editor = passage.locator(".passage-editor")
    editor.locator("[data-editor-mode]").select_option("timing")
    expect(editor.locator("[data-timing-start]")).to_have_value("")
    expect(editor.locator("[data-timing-end]")).to_have_value("")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("Hejsa")
    editor.locator('[data-action="save-timed-word"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    after = store.result(rid, result_id)["segments"][0]
    assert after["text"] == "Hejsa verden"
    assert after["words"][0]["start"] is None and after["words"][0]["end"] is None
    assert after["word_timing"] is False
    assert after["original_words"] == before["original_words"]
