"""Opt-in native Text highlighting prototype; synthetic media and libraries only."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_editor_playback_follow import open_editor
from test_browser_word_doubleclick import open_timed_recording

pytestmark = pytest.mark.browser


def enable_prototype(page):
    page.add_init_script("localStorage.setItem('cbs-text-playback-prototype', '1')")


def test_text_highlighting_is_explicitly_opt_in_without_duplicate_panel(page, local_app):
    passage = open_editor(page, local_app)
    expect(passage.locator(".passage-text-overlay")).to_be_hidden()
    expect(passage.locator("[data-editor-word]")).to_have_count(0)
    expect(passage.locator("[data-text-playback-note]")).to_be_hidden()
    expect(passage.locator(".editor-playback-preview")).to_be_hidden()
    expect(passage.locator("textarea")).to_have_count(1)
    expect(passage.locator("[contenteditable]")).to_have_count(0)
    assert page.evaluate("localStorage.getItem('cbs-text-playback-prototype')") is None

    page.evaluate("localStorage.setItem('cbs-text-playback-prototype', '1')")
    passage.locator('[data-action="cancel-edit"]').first.click()
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    expect(passage.locator(".passage-text-overlay")).to_be_visible()
    expect(passage.locator("[data-editor-word]")).to_have_count(200)
    expect(passage.locator(".editor-playback-preview")).to_be_hidden()
    expect(passage.locator(".passage-text-overlay")).to_have_attribute("aria-hidden", "true")
    expect(passage.locator(".passage-text-overlay button")).to_have_count(0)
    assert (
        passage.locator(".passage-text-overlay").evaluate("e => getComputedStyle(e).pointerEvents")
        == "none"
    )

    page.evaluate("localStorage.removeItem('cbs-text-playback-prototype')")
    passage.locator('[data-action="cancel-edit"]').first.click()
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    expect(passage.locator(".passage-text-overlay")).to_be_hidden()


def test_text_follow_preserves_native_selection_typing_undo_and_scroll(page, local_app):
    enable_prototype(page)
    passage = open_editor(page, local_app)
    draft = passage.locator("textarea")
    overlay = passage.locator(".passage-text-overlay")
    original = draft.input_value()
    passage.locator(".passage-text-surface").evaluate("e => e.style.width = '360px'")
    draft.evaluate("e => e.setSelectionRange(2, 5)")
    passage.locator('[data-action="editor-replay"]').click()
    page.locator("#audio").evaluate("e => { e.playbackRate = 0.1; e.currentTime = 0.1; }")
    before = page.evaluate("[scrollY, document.querySelector('#transcript').scrollTop]")
    page.locator("#audio").evaluate("e => e.currentTime = 80.1")
    expect(overlay.locator('[data-editor-word="160"]')).to_have_class("current")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    assert draft.evaluate("e => e.scrollTop > 0")
    assert passage.evaluate("""e => {
      const input = e.querySelector('textarea').getBoundingClientRect();
      const word = e.querySelector('.passage-text-overlay .current').getBoundingClientRect();
      return word.top >= input.top && word.bottom <= input.bottom;
    }""")
    assert page.evaluate("[scrollY, document.querySelector('#transcript').scrollTop]") == before
    assert draft.evaluate("""e => {
      const content = e.closest('.passage-text-surface')
        .querySelector('.passage-text-overlay-content');
      return parseFloat(content.style.width) === e.clientWidth
        && content.style.transform === `translate(${-e.scrollLeft}px, ${-e.scrollTop}px)`;
    }""")

    # Actual keyboard editing creates a browser-native undo entry. Playback may
    # decorate the mirror but must never rewrite the textarea or its undo stack.
    draft.press("x")
    expect(overlay).to_be_hidden()
    expect(passage.locator("[data-text-playback-note]")).to_contain_text("draft differs")
    changed = draft.input_value()
    assert changed == original[:2] + "x" + original[5:]
    selection = draft.evaluate("e => [e.selectionStart, e.selectionEnd]")
    page.locator("#audio").evaluate("e => e.currentTime = 90.1")
    expect(overlay.locator(".current")).to_have_count(0)
    expect(draft).to_have_value(changed)
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == selection
    draft.press("Control+z")
    expect(draft).to_have_value(original)
    expect(overlay).to_be_visible()
    expect(overlay.locator('[data-editor-word="180"]')).to_have_class("current")
    expect(passage.locator('[data-action="save-edit"]')).to_be_disabled()
    draft.press("Control+Shift+z")
    expect(draft).to_have_value(changed)
    expect(overlay).to_be_hidden()
    draft.press("Control+z")
    expect(draft).to_have_value(original)

    page.locator("#follow-playback").uncheck()
    draft.focus()
    draft.evaluate("e => { e.setSelectionRange(2, 5); e.scrollTop = 0; }")
    page.locator("#audio").evaluate("e => e.currentTime = 95.1")
    expect(overlay.locator('[data-editor-word="190"]')).to_have_class("current")
    assert draft.evaluate("e => e.scrollTop") == 0
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    # Resizing is still the native textarea; the transparent mirror follows its
    # actual client width and manual scroll offset rather than resizing the draft.
    passage.locator(".passage-text-surface").evaluate("e => e.style.width = '280px'")
    draft.evaluate("e => { e.style.height = '180px'; e.scrollTop = 40; }")
    page.wait_for_function("""() => {
      const e = document.querySelector('#passage-0 textarea');
      const content = document.querySelector('#passage-0 .passage-text-overlay-content');
      return parseFloat(content.style.width) === e.clientWidth
        && content.style.transform === `translate(0px, ${-e.scrollTop}px)`;
    }""")
    expect(draft).to_have_value(original)
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]


def test_partial_and_stale_maps_never_gain_draft_word_timing(page, local_app):
    enable_prototype(page)
    store, rid = open_timed_recording(page, local_app)
    before = store.recording(rid)
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url)
            if request.method in {"POST", "PATCH", "PUT", "DELETE"}
            else None
        ),
    )
    # A projection-only synthetic fixture makes word 1 untimed. This does not
    # write timing or alter the saved result merely by opening the prototype.
    page.evaluate("""() => {
      const segment = {...state.recording.segments[0], word_timing:false, words_current:true};
      segment.words = segment.words.map((word, i) => i ? {...word,start:null,end:null} : word);
      state.recording.segments = [segment,...state.recording.segments.slice(1)];
      renderTranscript();
    }""")
    page.locator('#passage-0 .passage-text [data-word="0"]').dblclick()
    passage = page.locator("#passage-0")
    expect(passage.locator("[data-editor-word]")).to_have_count(1)
    expect(passage.locator('[data-editor-word="0"]')).to_have_text("First")
    draft = passage.locator("textarea")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [0, 5]
    page.locator("#audio").evaluate("e => e.currentTime = 1.2")
    expect(passage.locator(".passage-text-overlay .current")).to_have_count(0)
    draft.press("End")
    draft.press("!")
    expect(passage.locator(".passage-text-overlay")).to_be_hidden()
    expect(passage.locator('[data-action="save-edit"]')).to_be_enabled()
    passage.locator('[data-action="cancel-edit"]').first.click()
    assert store.recording(rid) == before
    assert not writes
    assert store.all("SELECT id FROM jobs") == []

    page.evaluate("""() => {
      state.recording.segments = state.recording.segments.map((segment) =>
        ({...segment,edited:true,word_timing:false,words_current:false}));
      renderTranscript();
    }""")
    page.locator('#passage-0 [data-action="edit"]').click()
    expect(passage.locator("[data-editor-word]")).to_have_count(0)
    expect(passage.locator(".passage-text-overlay")).to_be_hidden()
    expect(passage.locator("[data-text-playback-note]")).to_contain_text("unavailable")
    expect(passage.locator("textarea")).to_be_enabled()


def test_unicode_newlines_and_long_native_wrapping_match_the_overlay(page, local_app):
    enable_prototype(page)
    long_word = "København" * 60
    tokens = ["😀", "repeat", "repeat", long_word, *[f"w{i}" for i in range(5000)]]
    text = "😀\t repeat\r\nrepeat\n" + long_word + "\n" + " ".join(tokens[4:]) + "\n"
    interval = 4 / (len(tokens) + 1)
    _, _ = open_timed_recording(
        page,
        local_app,
        [
            {
                "text": text,
                "start": 0,
                "end": 4,
                "words": [
                    {"word": word, "start": i * interval, "end": (i + 0.8) * interval}
                    for i, word in enumerate(tokens)
                ],
            }
        ],
    )
    page.locator('#passage-0 .passage-text [data-word="2"]').dblclick()
    passage = page.locator("#passage-0")
    draft = passage.locator("textarea")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => e.value.slice(e.selectionStart,e.selectionEnd)") == "repeat"
    expect(draft).to_have_value(text.replace("\r\n", "\n"))
    expect(passage.locator("[data-editor-word]")).to_have_count(len(tokens))
    passage.locator(".passage-text-surface").evaluate("e => e.style.width = '280px'")
    page.evaluate("document.fonts.ready")
    page.wait_for_function("""() => {
      const input = document.querySelector('#passage-0 textarea');
      const content = document.querySelector('#passage-0 .passage-text-overlay-content');
      return parseFloat(content.style.width) === input.clientWidth;
    }""")
    metrics = passage.evaluate("""e => {
      const input = e.querySelector('textarea');
      const content = e.querySelector('.passage-text-overlay-content');
      const box = input.getBoundingClientRect();
      return {
        nativeHeight: input.scrollHeight,
        mirrorHeight: content.scrollHeight,
        literalMatch: content.textContent === input.value + '\u200b',
        pointerTarget: document.elementFromPoint(box.left+30,box.top+30) === input
      };
    }""")
    assert metrics["literalMatch"]
    assert abs(metrics["nativeHeight"] - metrics["mirrorHeight"]) <= 2, metrics
    assert metrics["pointerTarget"]
    # Native double-click selection stays inside the very same textarea.
    page.evaluate("window.prototypeNativeDraft = document.querySelector('#passage-0 textarea')")
    draft.dblclick(position={"x": 70, "y": 24})
    expect(draft).to_be_focused()
    assert page.evaluate(
        "window.prototypeNativeDraft === document.querySelector('#passage-0 textarea')"
    )
    expect(draft).to_have_value(text.replace("\r\n", "\n"))


def test_opted_in_text_save_cancel_and_between_word_split_keep_native_behavior(page, local_app):
    enable_prototype(page)
    store, rid = open_timed_recording(page, local_app)
    page.locator('#passage-0 .passage-text [data-word="1"]').dblclick()
    passage = page.locator("#passage-0")
    draft = passage.locator("textarea")
    draft.press_sequentially("phrases")
    expect(draft).to_have_value("First phrases")
    expect(passage.locator(".passage-text-overlay")).to_be_hidden()
    passage.locator('[data-action="save-edit"]').click()
    expect(passage.locator(".passage-text")).to_have_text("First phrases")
    saved = store.recording(rid)
    assert saved["segments"][0]["words"][1]["start"] == 1
    assert saved["segments"][0]["words"][1]["end"] == 1.8

    passage.locator('[data-action="edit"]').click()
    expect(passage.locator('[data-editor-word="1"]')).to_have_text("phrases")
    draft.press("Home")
    draft.press("Control+ArrowRight")
    expect(passage.locator('[data-action="split"]')).to_be_enabled()
    passage.locator('[data-action="split"]').click()
    expect(page.locator("#transcript > .passage")).to_have_count(3)
    split = store.recording(rid)
    assert [segment["text"] for segment in split["segments"]] == [
        "First ",
        "phrases",
        "Second passage",
    ]
    assert store.all("SELECT id FROM jobs") == []

    first = page.locator("#transcript > .passage").first
    first.locator('[data-action="edit"]').click()
    first.locator("textarea").press("End")
    first.locator("textarea").press("!")
    first.locator('[data-action="cancel-edit"]').first.click()
    assert first.locator(".passage-text").text_content() == "First "
    assert store.recording(rid) == split
