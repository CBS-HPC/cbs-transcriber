"""Typed word times in the passage editor keep unrelated evidence intact (ADR 0195)."""

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


def at(editor, position):
    editor.locator("textarea").evaluate(
        "(e, at) => { e.focus(); e.setSelectionRange(at, at); }", position
    )


def test_typed_times_and_a_text_draft_save_together_with_the_working_choice(page, local_app):
    store, rid, result_id, passage = open_editor(page, local_app)
    before = store.result(rid, result_id)["segments"][0]
    editor = passage.locator(".passage-editor")
    text = editor.locator("textarea")
    assert text.evaluate("e => e.spellcheck") is False
    start = editor.locator('[data-word-time="start"]')
    end = editor.locator('[data-word-time="end"]')
    assert start.evaluate("e => e.spellcheck") is False
    at(editor, 1)
    start.fill("0.2")
    end.fill("0.9")
    at(editor, 3)
    text.press_sequentially("sa")  # Hej -> Hejsa: the typed times follow the corrected word
    at(editor, 2)
    expect(start).to_have_value("0.2")
    with page.expect_response(lambda response: response.url.endswith("/edit-v4")):
        editor.locator('[data-action="save-edit"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    after = store.result(rid, result_id)["segments"][0]
    assert after["text"] == "Hejsa verden"
    assert (after["words"][0]["word"], after["words"][0]["start"], after["words"][0]["end"]) == (
        "Hejsa",
        0.2,
        0.9,
    )
    assert after["words"][0]["manual_timing"] is True
    assert after["words"][1] == before["words"][1] | {"char_start": 6, "char_end": 12}
    assert after["original_words"] == before["original_words"]
    recording = store.recording(rid)
    assert recording["result_roles"]["working"]["version_id"] == recording["active_version"]["id"]


@pytest.mark.parametrize("choice", [False, True])
def test_realign_choice_is_kept_and_typed_times_are_never_realigned(page, local_app, choice):
    store, rid, _, passage = open_editor(page, local_app)
    editor = passage.locator(".passage-editor")
    realign = editor.locator("[data-editor-realign]")
    realign.set_checked(choice)
    at(editor, 1)
    editor.locator('[data-word-time="start"]').fill("0.2")
    with page.expect_request(lambda request: request.url.endswith("/edit-v4")) as sent:
        editor.locator('[data-action="save-edit"]').click()
    assert "realign" not in sent.value.post_data_json
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    if choice:
        expect(page.locator("#toast")).to_have_text(
            "Passage saved. It was not realigned, because you typed word times."
        )
    assert not store.all("SELECT id FROM jobs WHERE recording_id=? AND kind='enhance'", (rid,))


@pytest.mark.parametrize("refused", [False, True])
def test_save_freezes_the_draft_and_preserves_it_on_refusal(page, local_app, refused):
    store, rid, result_id, passage = open_editor(page, local_app)
    editor = passage.locator(".passage-editor")
    start = editor.locator('[data-word-time="start"]')
    at(editor, 6)
    start.fill("1.1")
    held = []

    def hold(route):
        held.append((route, None if refused else route.fetch()))
        page.evaluate("window.wordReplyHeld = true")

    page.route(f"**/api/recordings/{rid}/segments/0/edit-v4", hold)
    editor.locator('[data-action="save-edit"]').click()
    page.wait_for_function("() => window.wordReplyHeld === true")
    expect(start).to_be_disabled()
    expect(editor.locator('[data-word-time="end"]')).to_be_disabled()
    expect(editor.locator("textarea")).to_be_disabled()
    route, response = held.pop()
    if refused:
        route.fulfill(status=409, json={"detail": "A newer revision was saved."})
        expect(start).to_be_enabled()
        expect(start).to_have_value("1.1")
        assert store.result(rid, result_id)["segments"][0]["words"][1]["start"] == 1
    else:
        route.fulfill(response=response)
        expect(page.locator("#save-status")).to_have_text("All changes saved")
        expect(passage.locator(".passage-editor")).to_have_count(0)
        assert store.result(rid, result_id)["segments"][0]["words"][1]["start"] == 1.1


def test_untimed_word_stays_untimed_unless_times_are_typed(page, local_app):
    store, rid, result_id, passage = open_editor(page, local_app, partial=True)
    before = store.result(rid, result_id)["segments"][0]
    assert before["word_timing"] is False
    editor = passage.locator(".passage-editor")
    start = editor.locator('[data-word-time="start"]')
    end = editor.locator('[data-word-time="end"]')
    at(editor, 1)
    expect(start).to_be_enabled()
    expect(start).to_have_value("")
    expect(end).to_have_value("")
    # A text-only fix keeps the untimed word untimed; nothing is guessed for it.
    at(editor, 3)
    editor.locator("textarea").press_sequentially("sa")
    with page.expect_request(lambda request: request.url.endswith("/edit-v3")):
        editor.locator('[data-action="save-edit"]').click()
    expect(page.locator("#save-status")).to_contain_text("Passage saved")
    after = store.result(rid, result_id)["segments"][0]
    assert after["text"] == "Hejsa verden"
    assert after["words"][0]["start"] is None and after["words"][0]["end"] is None
    assert after["word_timing"] is False
    assert after["original_words"] == before["original_words"]
