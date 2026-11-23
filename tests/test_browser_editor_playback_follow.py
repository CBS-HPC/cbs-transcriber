"""Text stays a single editable surface; playback and word times share it (ADR 0195)."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_review_layout import seed_review

pytestmark = pytest.mark.browser


def open_editor(page, local_app):
    store = local_app.server.config.app.state.store
    _, rid, _, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    expect(passage.locator(".editor-playback-preview")).to_be_hidden()
    return passage


def test_replay_highlights_text_while_typed_word_times_keep_their_focus(page, local_app):
    # ADR 0195 replaced the timing pane and its saved-text preview with word-time boxes.
    passage = open_editor(page, local_app)
    expect(passage.locator(".editor-playback-preview")).to_have_count(0)
    draft = passage.locator("textarea")
    draft.evaluate("e => { e.focus(); e.setSelectionRange(2, 2); }")  # wo|rd0
    start = passage.locator('[data-word-time="start"]')
    expect(start).to_have_value("0")
    start.fill("0.1")
    passage.locator('[data-action="editor-replay"]').click()
    page.locator("#audio").evaluate("e => { e.playbackRate = 0.1; e.currentTime = 2.1; }")
    expect(passage.locator('.passage-text-overlay [data-editor-word="4"]')).to_have_class("current")
    expect(start).to_be_focused()
    expect(start).to_have_value("0.1")
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 2]
    expect(passage.locator("[data-word-times-status]")).to_have_text("“word0”: unsaved times.")


def test_text_has_no_duplicate_preview_and_saved_words_keep_their_timing(page, local_app):
    passage = open_editor(page, local_app)
    text = passage.locator("textarea").input_value().replace("word0", "corrected", 1)
    passage.locator("textarea").fill(text)
    passage.locator('[data-action="save-edit"]').click()
    expect(passage.locator(".passage-text")).to_have_text(text)
    expect(passage.locator(".word")).to_have_count(200)
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    expect(passage.locator(".editor-playback-preview")).to_have_count(0)
    expect(passage.locator("[data-editor-mode]")).to_have_count(0)
    # The replaced word kept word0's interval (ADR 0166); the boxes show it once saved.
    passage.locator("textarea").evaluate("e => { e.focus(); e.setSelectionRange(3, 3); }")
    expect(passage.locator('[data-word-time="start"]')).to_have_value("0")
    expect(passage.locator('[data-word-time="end"]')).to_have_value("0.5")
    expect(passage.locator("[data-word-times-status]")).to_have_text("“corrected”")
    expect(passage.locator('[data-action="editor-replay"]')).to_be_enabled()
