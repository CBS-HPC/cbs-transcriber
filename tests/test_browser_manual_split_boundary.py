"""Manual splits require a lexical boundary, independently of timing metadata."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def open_editor(page, local_app, timing="complete", text="Alpha, beta. gamma"):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic lexical split")
    rid = store.create_recording(project["id"], "boundary.wav")["id"]
    words = [
        {"word": word, "start": index, "end": index + 0.8}
        for index, word in enumerate(text.split())
    ]
    if timing == "partial":
        words[1]["start"] = words[1]["end"] = None
    elif timing in {"none", "untimed"}:
        words = []
    elif timing == "grouped":
        words = [
            {"word": "Alpha, beta.", "start": 0, "end": 1.8},
            {"word": "gamma", "start": 2, "end": 2.8},
        ]
    row = {"text": text, "start": 0, "end": len(text.split()), "words": words}
    if timing == "untimed":
        row["start"] = row["end"] = None
    store.add_result(rid, normalize_segments([row]), {}, "Synthetic", {}, auto_first=True)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator('#passage-0 [data-action="edit"]').click()
    draft = page.locator("#passage-0 textarea")
    expect(draft).to_have_value(text.replace("\r\n", "\n").replace("\r", "\n"))
    return store, rid, draft, page.locator('#passage-0 [data-action="split"]')


def caret(draft, position, end=None):
    draft.evaluate(
        "(input, values) => {input.setSelectionRange(...values); "
        "input.dispatchEvent(new Event('select', {bubbles:true}));}",
        [position, position if end is None else end],
    )


def test_split_is_disabled_inside_a_complete_timed_word(page, local_app):
    _, _, draft, split = open_editor(page, local_app)
    for position in [0, 2, 5, 8, 11, len(draft.input_value())]:
        caret(draft, position)
        expect(split).to_be_disabled()
    expect(split).to_have_attribute("title", __import__("re").compile("between words"))
    for position in [6, 7, 12, 13]:
        caret(draft, position)
        expect(split).to_be_enabled()
    caret(draft, 6, 8)
    expect(split).to_be_disabled()


def capture_refused_request(page, pattern):
    captured = []

    def capture(route):
        captured.append(route.request.post_data_json)
        # This frontend-only gate proves exact intent; root's backend gate owns commit semantics.
        route.fulfill(status=409, json={"detail": "Synthetic intent captured without committing."})

    page.route(pattern, capture)
    return captured


@pytest.mark.parametrize("timing", ["complete", "partial", "none", "untimed", "grouped"])
def test_new_split_route_has_no_timing_gate_and_preserves_exact_cursor(page, local_app, timing):
    store, rid, draft, split = open_editor(page, local_app, timing)
    before = store.recording(rid)
    caret(draft, 2)
    expect(split).to_be_disabled()
    captured = capture_refused_request(page, f"**/api/recordings/{rid}/segments/0/split-v2")
    caret(draft, 7)
    expect(split).to_be_enabled()
    split.click()
    expect(page.locator("#toast")).to_contain_text("Synthetic intent captured")
    assert len(captured) == 1
    body = captured[0]
    assert body["cursor"] == 7
    assert body["result_id"] == before["active_transcript_id"]
    assert body["revision"] == before["revision"]
    assert "split_time" not in body
    assert "working_choice" in body
    expect(draft).to_have_value("Alpha, beta. gamma")
    assert draft.evaluate("e => [e.selectionStart,e.selectionEnd]") == [7, 7]
    assert store.recording(rid) == before


def test_unicode_and_crlf_boundary_sends_codepoints_without_snapping(page, local_app):
    text = "😀🧪 alpha\r\nbeta"
    store, rid, draft, split = open_editor(page, local_app, text=text)
    before = store.recording(rid)
    captured = capture_refused_request(page, f"**/api/recordings/{rid}/segments/0/split-v2")
    caret(draft, 11)
    expect(split).to_be_enabled()
    split.click()
    expect(page.locator("#toast")).to_contain_text("Synthetic intent captured")
    assert captured[0]["cursor"] == text.index("beta") == 10
    assert draft.evaluate("e => [e.selectionStart,e.selectionEnd]") == [11, 11]
    assert store.recording(rid) == before


def test_fresh_text_save_v3_retains_working_authority_and_dirty_split_guard(page, local_app):
    store, rid, draft, split = open_editor(page, local_app)
    before = store.recording(rid)
    path = f"/recordings/{rid}/segments/0/edit-v3"
    captured = capture_refused_request(page, "**/api" + path)
    draft.fill("Alpha, beta. gamma extra")
    caret(draft, 7)
    expect(split).to_be_disabled()
    page.locator('#passage-0 [data-action="save-edit"]').click()
    expect(page.locator("#toast")).to_contain_text("Synthetic intent captured")
    assert captured[0]["text"] == "Alpha, beta. gamma extra"
    assert "realign" not in captured[0]
    assert "working_choice" in captured[0]
    assert captured[0]["revision"] == before["revision"]
    assert page.evaluate(
        "path => manualEditTarget(path,'PATCH',{mode:'free'}) === "
        "manualEditTarget(path.replace('/edit-v3',''),'PATCH',{mode:'free'})",
        path,
    )
    assert store.recording(rid) == before
