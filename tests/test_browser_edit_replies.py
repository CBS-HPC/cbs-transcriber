"""Submitted editor dialogs cannot discard a correction opened while their reply waits."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import edit_segment, normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture
def dialog_recording(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Dialog saves")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {
                "text": text,
                "start": start,
                "end": start + 2,
                "speaker": speaker,
                "words": [
                    {"word": text.split()[0], "start": start, "end": start + 1},
                    {"word": "word", "start": start + 1, "end": start + 2},
                ],
            }
            for text, start, speaker in (("First word.", 0, "A"), ("Second word.", 2, "B"))
        ]
    )
    store.add_result(
        recording["id"],
        segments,
        {"A": "Interviewer", "B": "Respondent"},
        "Synthetic",
        {},
        auto_first=True,
    )
    return store, recording["id"]


def open_correction(page, kind, passage, value):
    if kind == "word":
        page.locator(f'#passage-{passage} [data-word="0"]').dblclick()
        dialog, field = "#word-dialog", "#word-input"
        controls = "#word-input, #word-select, #word-form button[type=submit]"
    else:
        page.locator(f'#passage-{passage} [data-action="speaker"]').click()
        page.locator("#passage-speaker-select").select_option("")
        dialog, field = "#speaker-dialog", "#passage-speaker-name"
        controls = "#passage-speaker-name, #passage-speaker-select, #passage-speaker-save"
    page.locator(field).fill(value)
    return dialog, field, controls


def test_pending_inline_word_save_blocks_opening_another_passage(page, local_app, dialog_recording):
    store, rid = dialog_recording
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator('#passage-0 [data-word="0"]').dblclick()
    editor = page.locator("#passage-0 .passage-editor")
    expect(editor.locator("[data-editor-mode]")).to_have_value("text")
    editor.locator("[data-editor-mode]").select_option("timing")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("Corrected")
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.wordReplyHeld = true")

    page.route(f"**/api/recordings/{rid}/segments/0", hold)
    editor.locator('[data-action="save-timed-word"]').click()
    page.wait_for_function("() => window.wordReplyHeld === true")
    page.locator('#passage-1 [data-action="edit"]').click()
    expect(page.locator("#passage-1 .passage-editor")).to_have_count(0)
    expect(editor.locator("[data-timing-text]")).to_have_value("Corrected")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(editor.locator("[data-timing-text]")).to_have_value("Corrected")
    assert store.recording(rid)["segments"][1]["text"] == "Second word."


@pytest.mark.parametrize("kind", ["speaker"])
@pytest.mark.parametrize("refused", [False, True])
def test_submitted_correction_freezes_fields_and_retains_a_refused_value(
    page, local_app, dialog_recording, kind, refused
):
    store, rid = dialog_recording
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#recording-title")).to_have_text("synthetic")
    if kind == "word":
        page.locator("#edit-mode").select_option("word")
    dialog, field, controls = open_correction(page, kind, 0, "Correction")
    suffix = "/segments/0" + ("/speaker" if kind == "speaker" else "")
    held = []

    def hold(route):
        held.append((route, None if refused else route.fetch()))
        page.evaluate("window.correctionReplyHeld = true")

    page.route(f"**/api/recordings/{rid}{suffix}", hold)
    page.locator(dialog + " button[type=submit]").click()
    page.wait_for_function("() => window.correctionReplyHeld === true")
    for control in page.locator(controls).all():
        expect(control).to_be_disabled()
    route, response = held.pop()
    if refused:
        route.fulfill(status=409, json={"detail": "A newer revision was saved."})
        expect(page.locator(dialog)).to_be_visible()
        expect(page.locator(field)).to_have_value("Correction")
        for control in page.locator(controls).all():
            expect(control).to_be_enabled()
        assert store.recording(rid)["segments"][0]["text"] == "First word."
    else:
        route.fulfill(response=response)
        expect(page.locator(dialog)).not_to_be_visible()
        current = store.recording(rid)
        assert current["revision"] == 2
        if kind == "word":
            assert current["segments"][0]["text"] == "Correction word."
            assert [(w["start"], w["end"]) for w in current["segments"][0]["words"]] == [
                (0, 1),
                (1, 2),
            ]
        else:
            assert current["speakers"][current["segments"][0]["speaker"]] == "Correction"


@pytest.mark.parametrize("kind", ["speaker"])
@pytest.mark.parametrize("new_save_pending", [False, True])
def test_late_correction_reply_preserves_a_new_dialog_and_its_pending_controls(
    page, local_app, dialog_recording, kind, new_save_pending
):
    store, rid = dialog_recording
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#recording-title")).to_have_text("synthetic")
    if kind == "word":
        page.locator("#edit-mode").select_option("word")
    dialog, field, controls = open_correction(page, kind, 0, "Saved")
    held = []
    page.evaluate("""() => {
        const original = acceptRecording;
        window.correctionRepliesHandled = 0;
        acceptRecording = (...args) => {
            window.correctionRepliesHandled++;
            return original(...args);
        };
    }""")

    def hold(route):
        response = route.fetch()
        held.append((route, response))
        page.evaluate("count => window.correctionRepliesHeld = count", len(held))

    suffix = "/speaker" if kind == "speaker" else ""
    page.route(f"**/api/recordings/{rid}/segments/*{suffix}", hold)
    page.locator(dialog + " button[type=submit]").click()
    page.wait_for_function("() => window.correctionRepliesHeld === 1")
    page.locator(dialog + " [data-close]").first.click()
    open_correction(page, kind, 1, "Newdraft")
    if new_save_pending:
        page.locator(dialog + " button[type=submit]").click()
        page.wait_for_function("() => window.correctionRepliesHeld === 2")
        assert held[1][1].status == 409  # This new form still has its original saved revision.
    route, response = held.pop(0)
    assert response.status == 200
    route.fulfill(response=response)
    # Wait for the old async handler, not only for its HTTP response.
    page.wait_for_function("() => window.correctionRepliesHandled === 1")
    expect(page.locator(dialog)).to_be_visible()
    expect(page.locator(field)).to_have_value("Newdraft")
    for control in page.locator(controls).all():
        if new_save_pending:
            expect(control).to_be_disabled()
        else:
            expect(control).to_be_enabled()
    if new_save_pending:
        route, response = held.pop()
        route.fulfill(response=response)
        for control in page.locator(controls).all():
            expect(control).to_be_enabled()
        expect(page.locator(field)).to_have_value("Newdraft")
    current = store.recording(rid)
    assert current["segments"][1]["text"] == "Second word."
    assert current["segments"][1]["speaker"] == "B"


@pytest.mark.parametrize("operation", ["free", "delete"])
def test_edit_reply_preserves_newer_independently_saved_notes(
    page, local_app, dialog_recording, operation
):
    store, rid = dialog_recording
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#recording-title")).to_have_text("synthetic")
    held = []

    def hold(route):
        response = route.fetch()
        assert response.status == 200
        held.append((route, response))
        page.evaluate("window.editReplyHeld = true")

    suffix = "/delete" if operation == "delete" else "/edit-v3"
    page.route(f"**/api/recordings/{rid}/segments/0{suffix}", hold)
    if operation == "free":
        page.locator('#passage-0 [data-action="edit"]').click()
        page.locator("#passage-0 textarea").fill("Reviewed wording.")
        page.locator('#passage-0 [data-action="save-edit"]').click()
    else:
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator('#passage-0 [data-action="delete"]').click()
    page.wait_for_function("() => window.editReplyHeld === true")
    page.locator("#recording-notes").locator("..").locator("summary").click()
    page.locator("#recording-notes").fill("Newer independently saved notes.")
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Notes saved")
    route, response = held.pop()
    route.fulfill(response=response)
    if operation == "free":
        expect(page.locator("#passage-0 .passage-text")).to_have_text("Reviewed wording.")
    else:
        expect(page.locator("#passage-0")).to_have_count(0)
    expect(page.locator("#recording-notes")).to_have_value("Newer independently saved notes.")
    expect(page.locator("#recording-notes")).to_have_attribute("data-revision", "1")
    # The retained revision must permit the researcher's next independent save too.
    page.locator("#recording-notes").fill("A second notes save.")
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Notes saved")
    assert store.recording(rid)["notes"] == "A second notes save."
    assert store.recording(rid)["notes_revision"] == 2


@pytest.mark.parametrize("discard", [False, True])
def test_restore_original_requires_permission_to_discard_another_passage_draft(
    page, local_app, dialog_recording, discard
):
    store, rid = dialog_recording
    before = store.recording(rid)
    before["segments"][1] = edit_segment(before["segments"][1], "free", "Second saved edit.")
    before = store.save_transcript(rid, before["segments"], before["speakers"], before["revision"])
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#recording-title")).to_have_text("synthetic")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").fill("An unsaved first draft.")
    messages = []

    def answer(dialog):
        messages.append(dialog.message)
        if "Discard" in dialog.message and not discard:
            dialog.dismiss()
        else:
            dialog.accept()

    page.on("dialog", answer)
    page.locator('#passage-1 [data-action="restore"]').click()
    assert messages and "Discard" in messages[0]
    if discard:
        expect(page.locator("#passage-1 .passage-text")).to_have_text("Second word.")
        expect(page.locator("#passage-0 textarea")).to_have_count(0)
        assert store.recording(rid)["revision"] == before["revision"] + 1
        assert len(messages) == 2
    else:
        expect(page.locator("#passage-0 textarea")).to_have_value("An unsaved first draft.")
        assert store.recording(rid) == before
        assert len(messages) == 1
