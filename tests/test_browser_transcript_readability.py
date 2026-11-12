"""Larger transcript text keeps native editing, timing controls and wrapping usable."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_editor_playback_follow import open_editor
from test_browser_text_playback_prototype import enable_prototype

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", [1440, 760], ids=["desktop", "narrow"])
def test_larger_text_keeps_wrapping_highlights_drafts_and_controls(page, local_app, width):
    page.set_viewport_size({"width": width, "height": 900})
    enable_prototype(page)
    passage = open_editor(page, local_app)
    draft = passage.locator("textarea")
    original = draft.input_value()
    overlay = passage.locator(".passage-text-overlay")
    page.evaluate("document.fonts.ready")
    page.wait_for_function("""() => {
      const input = document.querySelector('#passage-0 textarea');
      const content = document.querySelector('#passage-0 .passage-text-overlay-content');
      return parseFloat(content.style.width) === input.clientWidth;
    }""")
    metrics = passage.evaluate("""e => {
      const input = e.querySelector('textarea');
      const mirror = e.querySelector('.passage-text-overlay-content');
      const nativeStyle = getComputedStyle(input), mirrorStyle = getComputedStyle(mirror);
      return {
        nativeFont: nativeStyle.fontSize,
        mirrorFont: mirrorStyle.fontSize,
        sameFont: nativeStyle.fontFamily === mirrorStyle.fontFamily,
        sameLineHeight: nativeStyle.lineHeight === mirrorStyle.lineHeight,
        nativeHeight: input.scrollHeight,
        mirrorHeight: mirror.scrollHeight,
        noHorizontalScroll: input.scrollWidth <= input.clientWidth,
      };
    }""")
    assert metrics["nativeFont"] == metrics["mirrorFont"] == "15px"
    assert metrics["sameFont"] and metrics["sameLineHeight"], metrics
    assert abs(metrics["nativeHeight"] - metrics["mirrorHeight"]) <= 2, metrics
    assert metrics["noHorizontalScroll"], metrics

    draft.evaluate("e => e.setSelectionRange(2, 5)")
    passage.locator('[data-action="editor-replay"]').click()
    page.locator("#audio").evaluate("e => { e.playbackRate = 0.1; e.currentTime = 80.1; }")
    expect(overlay.locator('[data-editor-word="160"]')).to_have_class("current")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    draft.press("x")
    expect(overlay).to_be_hidden()
    expect(passage.locator('[data-action="save-edit"]')).to_be_enabled()
    draft.press("Control+z")
    expect(draft).to_have_value(original)
    expect(overlay).to_be_visible()
    expect(passage.locator('[data-action="save-edit"]')).to_be_disabled()

    passage.locator("[data-editor-mode]").select_option("timing")
    expect(passage.locator(".editor-playback-preview")).to_be_visible()
    assert (
        passage.locator(".editor-playback-text").evaluate("e => getComputedStyle(e).fontSize")
        == "15px"
    )
    passage.locator('[data-action="edit-timed-word"]').click()
    word = passage.locator("[data-timing-text]")
    word.fill("corrected")
    expect(word).to_have_value("corrected")
    for selector in (
        "[data-timing-word]",
        "[data-timing-text]",
        "[data-timing-start]",
        "[data-timing-end]",
    ):
        control = passage.locator(selector)
        assert control.evaluate("e => getComputedStyle(e).fontSize") == "13px"
        assert control.evaluate("""e => {
          const box = e.getBoundingClientRect();
          const parent = e.closest('.passage').getBoundingClientRect();
          return box.left >= parent.left && box.right <= parent.right + 1;
        }""")
    passage.locator('[data-editor-pane="timing"] [data-action="cancel-edit"]').click()
    expect(passage.locator("textarea")).to_have_count(0)
    text = passage.locator(".passage-text")
    expect(text).to_have_text(original)
    assert text.evaluate("e => getComputedStyle(e).fontSize") == "16px"
    assert text.evaluate("e => e.scrollWidth <= e.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
