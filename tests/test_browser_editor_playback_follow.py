"""Timing-mode previews follow playback; Text stays a single editable surface."""

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


def test_saved_words_highlight_while_replay_keeps_draft_and_caret(page, local_app):
    passage = open_editor(page, local_app)
    passage.locator("[data-editor-mode]").select_option("timing")
    passage.locator('[data-action="edit-timed-word"]').click()
    draft = passage.locator("[data-timing-text]")
    draft.fill("corrected")
    draft.evaluate("e => e.setSelectionRange(2, 5)")
    passage.locator('[data-action="editor-replay"]').click()
    page.locator("#audio").evaluate("e => { e.playbackRate = 0.1; e.currentTime = 2.1; }")
    preview = passage.locator(".editor-playback-preview")
    expect(preview.locator('[data-word="4"]')).to_have_class("word current")
    expect(preview.locator("[data-editor-playback-draft]")).to_be_visible()
    expect(preview.locator("[data-editor-playback-label]")).to_have_text("Saved text for playback")
    expect(draft).to_have_value("corrected")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    preview.locator('[data-word="8"]').click()
    expect(preview.locator('[data-word="8"]')).to_have_class("word current")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    assert page.locator("#audio").evaluate("e => !e.paused && e.currentTime >= 4")
    # The saved-word preview is read-only; double-click does not replace the draft.
    preview.locator('[data-word="8"]').dblclick()
    expect(passage.locator("[data-editor-mode]")).to_have_value("timing")
    expect(draft).to_have_value("corrected")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]


def test_follow_scrolls_preview_only_and_respects_off_choice(page, local_app):
    passage = open_editor(page, local_app)
    passage.locator("[data-editor-mode]").select_option("timing")
    passage.locator('[data-action="edit-timed-word"]').click()
    draft = passage.locator("[data-timing-text]")
    draft.fill("corrected")
    draft.evaluate("e => e.setSelectionRange(2, 5)")
    scroll = passage.locator(".editor-playback-scroll")
    scroll.evaluate("e => e.style.width = '360px'")
    passage.locator('[data-action="editor-replay"]').click()
    page.locator("#audio").evaluate("e => { e.playbackRate = 0.1; e.currentTime = 0.1; }")
    before = page.evaluate("() => [scrollY, document.querySelector('#transcript').scrollTop]")
    expect(page.locator("#follow-playback")).to_be_checked()
    page.locator("#audio").evaluate("e => e.currentTime = 80.1")
    expect(scroll.locator('[data-word="160"]')).to_have_class("word current")
    assert scroll.evaluate("e => e.scrollTop > 0")
    assert scroll.evaluate("""e => {
        const box = e.getBoundingClientRect();
        const word = e.querySelector('.current').getBoundingClientRect();
        return word.top >= box.top && word.bottom <= box.bottom;
    }""")
    assert (
        page.evaluate("() => [scrollY, document.querySelector('#transcript').scrollTop]") == before
    )
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]

    page.locator("#follow-playback").uncheck()
    scroll.evaluate("e => e.scrollTop = 0")
    draft.focus()
    page.locator("#audio").evaluate("e => e.currentTime = 90.1")
    expect(scroll.locator('[data-word="180"]')).to_have_class("word current")
    assert scroll.evaluate("e => e.scrollTop") == 0
    expect(draft).to_be_focused()
    expect(draft).to_have_value("corrected")


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
    preview = passage.locator(".editor-playback-preview")
    expect(preview).to_be_hidden()
    passage.locator("[data-editor-mode]").select_option("timing")
    expect(preview).to_be_visible()
    expect(preview.locator('[data-word="0"]')).to_have_text("corrected")
    expect(preview.locator("[data-word]")).to_have_count(200)
    expect(passage.locator('[data-action="editor-replay"]')).to_be_enabled()
