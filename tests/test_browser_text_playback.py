"""Text-editor word highlighting during playback; synthetic media and libraries only."""

import time

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_editor_playback_follow import open_editor
from test_browser_word_doubleclick import open_timed_recording

pytestmark = pytest.mark.browser

MIRRORS_DRAFT = """e => {
  const content = e.closest('.passage-editor').querySelector('.passage-text-overlay-content');
  return content.textContent === e.value + '\\u200b';
}"""


def poll(locator, script, timeout=5):
    """Wait for a page condition without page.wait_for_function (the app's CSP)."""
    deadline = time.monotonic() + timeout
    while not locator.evaluate(script):
        assert time.monotonic() < deadline, script
        time.sleep(0.02)


def select_text(draft, text, length=None):
    """Select the first `length` characters of a unique UTF-16 substring."""
    draft.evaluate(
        """(e, [text, length]) => {
          const start = e.value.indexOf(text);
          e.focus();
          e.setSelectionRange(start, start + (length ?? text.length));
        }""",
        [text, length],
    )


def undo_to(draft, value):
    for _ in range(20):
        if draft.input_value() == value:
            return
        draft.press("Control+z")
    expect(draft).to_have_value(value)


def seek(page, seconds):
    page.locator("#audio").evaluate("(e, seconds) => { e.currentTime = seconds; }", seconds)


def test_text_highlighting_is_on_by_default_without_duplicate_panel(page, local_app):
    # A leftover value of the removed prototype key has no effect.
    page.add_init_script("localStorage.setItem('cbs-text-playback-prototype', '0')")
    passage = open_editor(page, local_app)
    overlay = passage.locator(".passage-text-overlay")
    expect(overlay).to_be_visible()
    expect(passage.locator("[data-editor-word]")).to_have_count(200)
    expect(passage.locator("[data-text-playback-note]")).to_be_hidden()
    expect(passage.locator(".editor-playback-preview")).to_be_hidden()
    expect(passage.locator("textarea")).to_have_count(1)
    expect(passage.locator("[contenteditable]")).to_have_count(0)
    expect(overlay).to_have_attribute("aria-hidden", "true")
    expect(overlay.locator("button")).to_have_count(0)
    assert overlay.evaluate("e => getComputedStyle(e).pointerEvents") == "none"
    assert passage.locator("textarea").evaluate(MIRRORS_DRAFT)
    assert "rototype" not in passage.inner_text()
    assert page.evaluate("typeof textPlaybackPrototypeEnabled") == "undefined"


def test_text_follow_preserves_native_selection_typing_undo_and_scroll(page, local_app):
    passage = open_editor(page, local_app)
    draft = passage.locator("textarea")
    overlay = passage.locator(".passage-text-overlay")
    note = passage.locator("[data-text-playback-note]")
    original = draft.input_value()
    passage.locator(".passage-text-surface").evaluate("e => e.style.width = '360px'")
    draft.evaluate("e => e.setSelectionRange(2, 5)")
    passage.locator('[data-action="editor-replay"]').click()
    # Replay in Text plays only this passage.
    assert page.evaluate("state.replayEnd") == 100
    page.locator("#audio").evaluate("e => { e.playbackRate = 0.1; e.currentTime = 0.1; }")
    before = page.evaluate("[scrollY, document.querySelector('#transcript').scrollTop]")
    seek(page, 80.1)
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

    # Actual keyboard editing creates a browser-native undo entry. Playback decorates
    # only the mirror: the edited first word loses its highlight, unchanged words keep
    # theirs at shifted positions, and the textarea, caret and scroll are never rewritten.
    draft.press("x")
    recent = draft.evaluate("""e => {
      const recent = performance.now() - lastTextDraftInput;
      window.typedScroll = e.scrollTop;
      document.querySelector('#audio').currentTime = 90.1;
      return recent;
    }""")
    assert recent < 1500
    changed = draft.input_value()
    assert changed == original[:2] + "x" + original[5:]
    expect(overlay.locator('[data-editor-word="180"]')).to_have_class("current")
    expect(overlay.locator('[data-editor-word="180"]')).to_have_text("word180")
    assert draft.evaluate("e => e.scrollTop === window.typedScroll")
    expect(overlay).to_be_visible()
    expect(overlay.locator('[data-editor-word="0"]')).to_have_count(0)
    expect(overlay.locator("[data-editor-word]")).to_have_count(199)
    expect(note).to_have_text(
        "Unsaved changes: only unchanged words are highlighted during playback."
    )
    assert draft.evaluate(MIRRORS_DRAFT)
    selection = draft.evaluate("e => [e.selectionStart, e.selectionEnd]")
    assert selection == [3, 3]
    expect(draft).to_have_value(changed)
    expect(draft).to_be_focused()
    draft.press("Control+z")
    expect(draft).to_have_value(original)
    expect(overlay.locator("[data-editor-word]")).to_have_count(200)
    expect(overlay.locator('[data-editor-word="180"]')).to_have_class("current")
    expect(note).to_be_hidden()
    expect(passage.locator('[data-action="save-edit"]')).to_be_disabled()
    draft.press("Control+Shift+z")
    expect(draft).to_have_value(changed)
    expect(overlay.locator('[data-editor-word="0"]')).to_have_count(0)
    draft.press("Control+z")
    expect(draft).to_have_value(original)

    # Once typing pauses, Follow playback scrolls the draft to the spoken word again.
    draft.evaluate("e => { lastTextDraftInput = -Infinity; e.scrollTop = 0; }")
    seek(page, 95.1)
    expect(overlay.locator('[data-editor-word="190"]')).to_have_class("current")
    poll(draft, "e => e.scrollTop > 0")

    page.locator("#follow-playback").uncheck()
    draft.focus()
    draft.evaluate("e => { e.setSelectionRange(2, 5); e.scrollTop = 0; }")
    seek(page, 97.1)
    expect(overlay.locator('[data-editor-word="194"]')).to_have_class("current")
    assert draft.evaluate("e => e.scrollTop") == 0
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]
    # Resizing is still the native textarea; the transparent mirror follows its
    # actual client width and manual scroll offset rather than resizing the draft.
    passage.locator(".passage-text-surface").evaluate("e => e.style.width = '280px'")
    draft.evaluate("e => { e.style.height = '180px'; e.scrollTop = 40; }")
    poll(
        draft,
        """e => {
          const content = e.closest('.passage-editor')
            .querySelector('.passage-text-overlay-content');
          return parseFloat(content.style.width) === e.clientWidth
            && content.style.transform === `translate(0px, ${-e.scrollTop}px)`;
        }""",
    )
    expect(draft).to_have_value(original)
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == [2, 5]


def test_unchanged_prefix_and_suffix_words_highlight_but_changed_and_new_words_do_not(
    page, local_app
):
    passage = open_editor(page, local_app)
    draft = passage.locator("textarea")
    overlay = passage.locator(".passage-text-overlay")
    original = draft.input_value()
    page.locator("#audio").evaluate("e => e.pause()")

    # Letters attached to a saved word change it, so that word stops highlighting.
    draft.press("Control+End")
    draft.press("s")
    expect(overlay.locator('[data-editor-word="199"]')).to_have_count(0)
    expect(overlay.locator('[data-editor-word="198"]')).to_have_text("word198")
    expect(passage.locator('[data-action="save-edit"]')).to_be_enabled()
    undo_to(draft, original)
    expect(overlay.locator("[data-editor-word]")).to_have_count(200)

    # Replace one middle word: the prefix keeps its offsets, the suffix shifts.
    select_text(draft, "word100 ", 7)
    draft.press_sequentially("changed")
    expect(draft).to_have_value(original.replace("word100 ", "changed "))
    caret = draft.evaluate("e => [e.selectionStart, e.selectionEnd]")
    expect(overlay.locator("[data-editor-word]")).to_have_count(199)
    expect(overlay.locator('[data-editor-word="100"]')).to_have_count(0)
    expect(overlay.locator('[data-editor-word="99"]')).to_have_text("word99")
    expect(overlay.locator('[data-editor-word="101"]')).to_have_text("word101")
    assert draft.evaluate(MIRRORS_DRAFT)
    for seconds, index in [(49.6, "99"), (50.6, "101"), (90.1, "180")]:
        seek(page, seconds)
        expect(overlay.locator(f'[data-editor-word="{index}"]')).to_have_class("current")
    seek(page, 50.1)
    expect(overlay.locator(".current")).to_have_count(0)
    expect(draft).to_be_focused()
    assert draft.evaluate("e => [e.selectionStart, e.selectionEnd]") == caret

    # A new word typed between unchanged words gets no time; its neighbours keep theirs.
    undo_to(draft, original)
    select_text(draft, " word101", 0)
    draft.press_sequentially(" inserted")
    expect(draft).to_have_value(original.replace("word100 ", "word100 inserted "))
    expect(overlay.locator("[data-editor-word]")).to_have_count(200)
    expect(overlay.locator('[data-editor-word="100"]')).to_have_text("word100")
    expect(overlay.locator('[data-editor-word="101"]')).to_have_text("word101")
    assert "inserted" not in "".join(
        overlay.locator("[data-editor-word]").evaluate_all("nodes => nodes.map(n => n.textContent)")
    )
    assert draft.evaluate(MIRRORS_DRAFT)
    seek(page, 50.6)
    expect(overlay.locator('[data-editor-word="101"]')).to_have_class("current")
    expect(passage.locator('[data-action="save-edit"]')).to_be_enabled()


def test_partial_and_stale_maps_never_gain_draft_word_timing(page, local_app):
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
    # write timing or alter the saved result merely by opening the editor.
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
    seek(page, 1.2)
    expect(passage.locator(".passage-text-overlay .current")).to_have_count(0)
    draft.press("End")
    draft.press("!")
    # "First" is provably unchanged; the untimed word and the new "!" gain nothing.
    expect(passage.locator("[data-editor-word]")).to_have_count(1)
    expect(passage.locator('[data-editor-word="0"]')).to_have_text("First")
    seek(page, 0.2)
    expect(passage.locator('[data-editor-word="0"]')).to_have_class("current")
    seek(page, 1.2)
    expect(passage.locator(".passage-text-overlay .current")).to_have_count(0)
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
    expect(passage.locator("[data-text-playback-note]")).to_have_text(
        "Word highlighting is unavailable: this passage has no reliable saved word timing."
    )
    expect(passage.locator("textarea")).to_be_enabled()


def test_unicode_newlines_and_long_native_wrapping_match_the_overlay(page, local_app):
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
    poll(
        draft,
        """e => parseFloat(e.closest('.passage-editor')
          .querySelector('.passage-text-overlay-content').style.width) === e.clientWidth""",
    )
    metrics = passage.evaluate("""e => {
      const input = e.querySelector('textarea');
      const content = e.querySelector('.passage-text-overlay-content');
      const box = input.getBoundingClientRect();
      return {
        nativeHeight: input.scrollHeight,
        mirrorHeight: content.scrollHeight,
        literalMatch: content.textContent === input.value + '​',
        pointerTarget: document.elementFromPoint(box.left+30,box.top+30) === input
      };
    }""")
    assert metrics["literalMatch"]
    assert abs(metrics["nativeHeight"] - metrics["mirrorHeight"]) <= 2, metrics
    assert metrics["pointerTarget"]
    # Native double-click selection stays inside the very same textarea.
    page.evaluate("window.nativeDraft = document.querySelector('#passage-0 textarea')")
    draft.dblclick(position={"x": 70, "y": 24})
    expect(draft).to_be_focused()
    assert page.evaluate("window.nativeDraft === document.querySelector('#passage-0 textarea')")
    expect(draft).to_have_value(text.replace("\r\n", "\n"))

    # Offsets are code points of the LF-normalized draft: a letter typed before the
    # emoji changes that token only, and later tokens (after the CRLF) shift by one.
    draft.press("Control+Home")
    draft.type("X")
    expect(passage.locator('[data-editor-word="0"]')).to_have_count(0)
    expect(passage.locator("[data-editor-word]")).to_have_count(len(tokens) - 1)
    expect(passage.locator('[data-editor-word="2"]')).to_have_text("repeat")
    expect(passage.locator('[data-editor-word="3"]')).to_have_text(long_word)
    assert draft.evaluate(MIRRORS_DRAFT)
    draft.press("Control+z")
    expect(draft).to_have_value(text.replace("\r\n", "\n"))
    draft.press("Control+End")
    draft.type(" tail")
    expect(passage.locator("[data-editor-word]")).to_have_count(len(tokens))
    assert draft.evaluate(MIRRORS_DRAFT)
    # Each keystroke recomputes one prefix/suffix mapping; playback ticks reuse it.
    elapsed = draft.evaluate("""e => {
      const start = performance.now();
      for (let i = 0; i < 20; i++) {
        e.setRangeText('z', e.value.length, e.value.length, 'end');
        e.dispatchEvent(new Event('input', {bubbles: true}));
      }
      return (performance.now() - start) / 20;
    }""")
    assert elapsed < 250, elapsed
    expect(passage.locator("[data-editor-word]")).to_have_count(len(tokens))


def test_text_save_cancel_and_between_word_split_keep_native_behavior(page, local_app):
    store, rid = open_timed_recording(page, local_app)
    page.locator('#passage-0 .passage-text [data-word="1"]').dblclick()
    passage = page.locator("#passage-0")
    draft = passage.locator("textarea")
    draft.press_sequentially("phrases")
    expect(draft).to_have_value("First phrases")
    # The replaced word has no draft timing; the unchanged first word keeps its own.
    expect(passage.locator('[data-editor-word="1"]')).to_have_count(0)
    expect(passage.locator('[data-editor-word="0"]')).to_have_text("First")
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
