"""Larger transcript text keeps native editing, timing controls and wrapping usable."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_editor_playback_follow import open_editor
from test_browser_text_playback_prototype import enable_prototype

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", [1440, 760, 390], ids=["desktop", "narrow", "compact"])
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
    assert text.evaluate("e => getComputedStyle(e).fontSize") == "15px"
    assert text.evaluate("e => e.scrollWidth <= e.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("width", [1440, 760, 390], ids=["desktop", "narrow", "compact"])
def test_actions_and_details_remain_readable_and_fit_at_smaller_widths(page, local_app, width):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic typography balance")
    filename = "Synthetic recording with a deliberately long filename for wrapping " * 2 + ".wav"
    recording = store.create_recording(project["id"], filename)
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "First synthetic passage.", "start": 0, "end": 2, "speaker": "A"},
                {"text": "Second synthetic passage.", "start": 2, "end": 4, "speaker": "B"},
            ]
        ),
        {"A": "A deliberately long synthetic speaker name which should wrap in the sidebar"},
        "Synthetic typography",
        {},
        auto_first=True,
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#detail-file")).to_have_text(filename)
    expect(page.locator("#speakers .speaker-entry")).to_have_count(2)
    page.evaluate("document.fonts.ready")

    for passage in page.locator("#transcript .passage").all():
        buttons = passage.locator(".passage-actions button:visible")
        assert buttons.count() >= 4
        for button in buttons.all():
            assert button.evaluate("e => getComputedStyle(e).fontSize") == "12px"
            assert button.evaluate("""e => {
              const box = e.getBoundingClientRect();
              const passage = e.closest('.passage').getBoundingClientRect();
              return box.height >= 24 && box.left >= passage.left
                && box.right <= passage.right + 1
                && e.scrollWidth <= e.clientWidth + 1
                && getComputedStyle(e).whiteSpace === 'nowrap';
            }""")
        assert passage.evaluate("""e => {
          const buttons = [...e.querySelectorAll('.passage-actions button')]
            .map(button => button.getBoundingClientRect()).filter(box => box.width && box.height);
          return buttons.every((box, i) => buttons.slice(i + 1).every(other =>
            Math.min(box.right, other.right) - Math.max(box.left, other.left) <= 0
            || Math.min(box.bottom, other.bottom) - Math.max(box.top, other.top) <= 0));
        }""")

    details = page.locator(".details-panel")
    for heading in details.locator(".panel-heading").all():
        assert heading.evaluate("e => getComputedStyle(e).fontSize") == "14px"
    for selector in (".panel-heading small", ".detail-row", ".detail-row strong", ".speaker-entry"):
        for node in details.locator(selector).all():
            assert node.evaluate("e => getComputedStyle(e).fontSize") == "13px"
            assert node.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    long_name = details.locator('.speaker-entry[data-speaker="A"] > span').nth(1)
    expect(long_name).to_have_text(
        "A deliberately long synthetic speaker name which should wrap in the sidebar"
    )
    details.locator(".description-notes summary").click()
    notes = details.locator("#recording-notes")
    notes.fill("Synthetic notes remain editable at the sidebar text size.")
    expect(notes).to_be_focused()
    expect(notes).to_have_value("Synthetic notes remain editable at the sidebar text size.")
    assert notes.evaluate("e => getComputedStyle(e).fontSize") == "13px"
    assert notes.evaluate("""e => {
      const box = e.getBoundingClientRect();
      const panel = e.closest('.details-panel').getBoundingClientRect();
      return box.left >= panel.left && box.right <= panel.right + 1;
    }""")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
