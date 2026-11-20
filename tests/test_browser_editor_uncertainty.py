"""Text editor uncertainty observes saved hints without replacing the native draft."""

from copy import deepcopy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_spelling import local_browser_only as local_browser_only
from test_browser_spelling import spelled as spelled

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture
def uncertain(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Editor uncertainty")
    recording = store.create_recording(project["id"], "synthetic.wav")
    text = "😀 alpha\r\nweak beta gamma delta epsilon zeta eta theta"
    words = text.split()
    segments = normalize_segments(
        [
            {
                "text": text,
                "start": 0,
                "end": 10,
                "words": [
                    {
                        "word": word,
                        "start": None if word == "weak" else index,
                        "end": None if word == "weak" else index + 0.9,
                        "score": 0.05 if word == "weak" else 0.95,
                    }
                    for index, word in enumerate(words)
                ],
            }
        ]
    )
    result = store.add_result(recording["id"], segments, {}, "Synthetic", {}, auto_first=True)
    return local_app, store, recording["id"], result["id"], segments


def open_editor(page, fixture, *, wait_for_hints=True):
    app, _, rid, _, _ = fixture
    page.goto(app.url + f"/#/recordings/{rid}")
    if wait_for_hints:
        expect(page.locator("#passage-0 .uncertain-word")).to_have_text("weak")
    page.locator('#passage-0 [data-action="edit"]').click()
    return page.locator("#passage-0 textarea")


def marks(page):
    return page.locator(".passage-uncertainty-overlay .uncertain-word")


def draft_state(input):
    return input.evaluate(
        "node => [node.value, node.selectionStart, node.selectionEnd,"
        "node.scrollTop, node.scrollLeft]"
    )


def test_editor_marks_untimed_unicode_word_without_touching_saved_timing(page, uncertain):
    _, store, rid, result, _ = uncertain
    before = deepcopy(store.recording(rid))
    versions = store.all("SELECT * FROM result_versions WHERE result_id=?", (result,))
    input = open_editor(page, uncertain)
    expect(marks(page)).to_have_text("weak")
    assert input.input_value() == before["segments"][0]["text"].replace("\r\n", "\n")
    styles = marks(page).evaluate("""node => {
      const box = node.getBoundingClientRect(), style = getComputedStyle(node);
      return [style.textDecorationStyle, style.textUnderlineOffset,
        getComputedStyle(node.closest('.passage-uncertainty-overlay')).pointerEvents,
        document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2).tagName];
    }""")
    assert styles == ["dotted", "3px", "none", "TEXTAREA"]
    assert store.recording(rid) == before
    assert store.all("SELECT * FROM result_versions WHERE result_id=?", (result,)) == versions


def test_editor_uncertainty_toggle_preserves_draft_node_selection_and_native_undo(page, uncertain):
    input = open_editor(page, uncertain)
    original = input.input_value()
    input.press("Control+End")
    input.press("!")
    input.evaluate("node => { node.dataset.sameDraft = 'yes'; node.setSelectionRange(3, 8); }")
    before = draft_state(input)
    page.locator("#show-uncertainty").uncheck()
    expect(marks(page)).to_have_count(0)
    page.locator("#show-uncertainty").check()
    expect(marks(page)).to_have_text("weak")
    assert input.get_attribute("data-same-draft") == "yes"
    assert draft_state(input) == before
    input.focus()
    input.press("Control+z")
    expect(input).to_have_value(original)
    expect(marks(page)).to_have_text("weak")


def test_editor_uncertainty_follows_only_unchanged_draft_words(page, uncertain):
    input = open_editor(page, uncertain)
    original = input.input_value()
    input.fill(original.replace("alpha", "longer correction"))
    expect(marks(page)).to_have_text("weak")
    input.fill(original.replace("weak", "corrected"))
    expect(marks(page)).to_have_count(0)
    input.fill(original.replace("weak", "weakness"))
    expect(marks(page)).to_have_count(0)
    input.fill(original)
    expect(marks(page)).to_have_text("weak")


def test_late_hints_refresh_only_the_overlay_after_typing(page, uncertain):
    delayed = []

    def hold(route):
        delayed.append((route, route.fetch()))

    page.route("**/hints", hold)
    input = open_editor(page, uncertain, wait_for_hints=False)
    expect(marks(page)).to_have_count(0)
    input.press("Control+End")
    input.press_sequentially(" tail")
    input.evaluate("node => { node.dataset.sameDraft = 'yes'; node.setSelectionRange(3, 8); }")
    before = draft_state(input)
    assert delayed
    for route, response in delayed:
        route.fulfill(response=response)
    expect(marks(page)).to_have_text("weak")
    assert input.get_attribute("data-same-draft") == "yes"
    assert draft_state(input) == before
    expect(input).to_be_focused()


def test_editor_uncertainty_scroll_resize_and_composition_leave_native_draft_alone(page, uncertain):
    input = open_editor(page, uncertain)
    original = input.input_value()
    input.fill(original + "\n" + "plain line\n" * 40)
    expect(marks(page)).to_have_text("weak")
    input.evaluate(
        "node => { node.style.height='100px'; node.scrollTop=80; node.setSelectionRange(3, 8); }"
    )
    input.dispatch_event("scroll")
    page.set_viewport_size({"width": 1150, "height": 900})
    page.wait_for_function("""() => {
      const input=document.querySelector('#passage-0 textarea');
      const content=document.querySelector('.passage-uncertainty-overlay-content');
      return content.style.width === input.clientWidth + 'px' &&
        content.style.transform === `translate(${-input.scrollLeft}px, ${-input.scrollTop}px)`;
    }""")
    before = draft_state(input)
    input.dispatch_event("compositionstart", {"data": "æ"})
    input.dispatch_event("compositionupdate", {"data": "æ"})
    input.dispatch_event("compositionend", {"data": "æ"})
    assert draft_state(input) == before
    expect(marks(page)).to_have_text("weak")


def test_editor_uncertainty_and_spelling_mark_the_same_word_in_distinct_layers(page, spelled):
    from test_browser_spelling import quietly, ready

    words = "😀 hello helllo world hello world hello world hello world".split()
    segments = normalize_segments(
        [
            {
                "text": " ".join(words),
                "start": 0,
                "end": 10,
                "words": [
                    {
                        "word": word,
                        "start": index,
                        "end": index + 0.9,
                        "score": 0.05 if word == "helllo" else 0.95,
                    }
                    for index, word in enumerate(words)
                ],
            }
        ]
    )
    result = spelled.store.add_result(spelled.rid, segments, {}, "Dual marks", {})
    spelled.store.activate_result(
        spelled.rid, result["id"], spelled.store.recording(spelled.rid)["revision"]
    )
    quietly(spelled, install=("en",))
    page.goto(spelled.app.url + f"/#/recordings/{spelled.rid}")
    expect(page.locator("#passage-0 .passage-text .uncertain-word")).to_have_text("helllo")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#working-edit-dialog").get_by_role("button", name="Keep current Working").click()
    input = page.locator("#passage-0 textarea")
    ready(page)
    expect(marks(page)).to_have_text("helllo")
    styles = page.evaluate("""() => {
      const spelling = document.querySelector('.passage-spelling-overlay .spelling-flag');
      const uncertain = document.querySelector('.passage-uncertainty-overlay .uncertain-word');
      const style = node => {
        const value = getComputedStyle(node), box = node.getBoundingClientRect();
        return [value.textDecorationStyle, value.textDecorationColor, value.textUnderlineOffset,
          box.x, box.y, box.width, box.height];
      };
      return [style(spelling), style(uncertain)];
    }""")
    assert styles[0][:3] == ["wavy", "rgb(230, 106, 87)", "6px"]
    assert styles[1][:3] == ["dotted", "rgb(194, 122, 0)", "3px"]
    assert styles[0][3:] == styles[1][3:]
    input.focus()
    input.press("Control+End")
    before = draft_state(input)
    page.locator("#passage-0 .passage-text-surface").screenshot(
        path=str(spelled.cache.parent / "editor-dual-underlines.png")
    )
    assert draft_state(input) == before


def test_old_hint_reply_cannot_mark_a_new_recording_editor(page, uncertain):
    app, store, _, _, _ = uncertain
    delayed = []

    def hold(route):
        if delayed:
            route.continue_()
        else:
            delayed.append((route, route.fetch()))

    page.route("**/hints", hold)
    input = open_editor(page, uncertain, wait_for_hints=False)
    input.press("Control+End")
    input.press_sequentially(" tail")
    assert delayed
    project = store.create_project("Other editor")
    other = store.create_recording(project["id"], "other.wav")
    store.add_result(
        other["id"],
        normalize_segments([{"text": "New recording draft"}]),
        {},
        "Other",
        {},
        auto_first=True,
    )
    page.once("dialog", lambda dialog: dialog.accept())
    page.evaluate("rid => { location.hash = '#/recordings/' + rid; }", other["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("New recording draft")
    page.locator('#passage-0 [data-action="edit"]').click()
    other_input = page.locator("#passage-0 textarea")
    other_input.press("Control+End")
    other_input.press_sequentially(" preserved")
    before = draft_state(other_input)
    for route, response in delayed:
        route.fulfill(response=response)
    expect(marks(page)).to_have_count(0)
    assert draft_state(other_input) == before
    expect(other_input).to_be_focused()
