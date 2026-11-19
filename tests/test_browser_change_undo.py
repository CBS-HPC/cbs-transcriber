"""Undo last change / Redo toolbar (ADR 0180) against a real local server. Synthetic data only."""

import time

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser
TEXTS = ("First passage", "Second passage", "Third passage")


def open_recording(page, local_app, name="Synthetic undo toolbar"):
    store = local_app.server.config.app.state.store
    project = store.create_project(name)
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    source = normalize_segments(
        [
            {"text": text, "start": 2 * i, "end": 2 * i + 1.5, "speaker": "A"}
            for i, text in enumerate(TEXTS)
        ]
    )
    store.add_result(rid, source, {"A": "Anna"}, "Synthetic", {}, auto_first=True)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#transcript .passage")).to_have_count(3)
    return store, rid, source


def edit_text(page, index, text):
    page.locator(f'#passage-{index} [data-action="edit"]').click()
    page.locator(f"#passage-{index} textarea").fill(text)
    page.locator(f'#passage-{index} [data-action="save-edit"]').click()
    expect(page.locator(f"#passage-{index} .passage-text")).to_have_text(text)


def test_labels_follow_each_newest_change_and_redo_walks_forward(page, local_app):
    store, rid, source = open_recording(page, local_app)
    undo, redo = page.locator("#undo-last"), page.locator("#redo-last")
    expect(undo).to_be_disabled()
    expect(undo).to_have_attribute("title", "Nothing earlier to undo in this result.")
    edit_text(page, 2, "Third passage, edited")
    expect(undo).to_have_text("↶ Undo last text edit")
    expect(undo).to_be_enabled()
    expect(redo).to_be_disabled()
    page.locator('#passage-0 [data-action="merge-down"]').click()
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    expect(undo).to_have_text("↶ Undo last merge")
    undo.click()  # A merge is one step on neighbouring passages: no confirmation.
    expect(page.locator("#transcript .passage")).to_have_count(3)
    expect(undo).to_have_text("↶ Undo last text edit")
    expect(redo).to_have_text("↷ Redo merge")
    undo.click()
    expect(page.locator("#passage-2 .passage-text")).to_have_text("Third passage")
    expect(undo).to_be_disabled()
    expect(redo).to_have_text("↷ Redo text edit")
    assert store.recording(rid)["segments"] == source
    redo.click()
    expect(page.locator("#passage-2 .passage-text")).to_have_text("Third passage, edited")
    expect(redo).to_have_text("↷ Redo merge")
    redo.click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    expect(redo).to_be_disabled()
    # A new change clears Redo and becomes the next step.
    undo.click()
    expect(redo).to_be_enabled()
    edit_text(page, 0, "First passage, new")
    expect(redo).to_be_disabled()
    expect(undo).to_have_text("↶ Undo last text edit")
    page.locator("#history-open").click()
    expect(page.locator("#history-versions")).to_contain_text("undo last change")
    expect(page.locator("#history-versions")).to_contain_text("redo")


def test_an_open_draft_disables_undo_until_saved_or_cancelled(page, local_app):
    store, rid, _ = open_recording(page, local_app, "Synthetic undo draft")
    edit_text(page, 1, "Second passage, edited")
    undo = page.locator("#undo-last")
    expect(undo).to_be_enabled()
    page.locator('#passage-0 [data-action="edit"]').click()
    expect(undo).to_be_enabled()  # A clean editor can be cancelled freely.
    page.locator("#passage-0 textarea").fill("Unsaved draft")
    expect(undo).to_be_disabled()
    expect(undo).to_have_attribute("title", "Save or cancel your passage correction first.")
    page.locator('#passage-0 [data-editor-pane="text"] [data-action="cancel-edit"]').click()
    expect(undo).to_be_enabled()
    undo.click()
    expect(page.locator("#passage-1 .passage-text")).to_have_text("Second passage")
    assert store.recording(rid)["segments"][0]["text"] == "First passage"


def test_a_newer_save_elsewhere_refuses_and_relabels(page, local_app):
    store, rid, _ = open_recording(page, local_app, "Synthetic undo two windows")
    edit_text(page, 0, "First passage, edited")
    undo = page.locator("#undo-last")
    expect(undo).to_have_text("↶ Undo last text edit")
    current = store.recording(rid)
    store.save_transcript(
        rid,
        current["segments"],
        {"A": "Anna in another window"},
        current["revision"],
        action="speaker_rename",
        result_id=current["active_transcript_id"],
    )
    versions = store.result_versions(rid, current["active_transcript_id"])
    undo.click()
    expect(page.locator("#toast")).to_contain_text("The saved result changed")
    expect(page.locator("#toast")).to_contain_text("newest saved change is now shown")
    expect(undo).to_have_text("↶ Undo last speaker rename")
    assert store.result_versions(rid, current["active_transcript_id"]) == versions


def test_version_restore_asks_with_counts_before_undoing(page, local_app):
    store, rid, source = open_recording(page, local_app, "Synthetic undo confirm")
    current = store.recording(rid)
    first = current["active_version"]["id"]
    segments = [{**passage, "text": passage["text"] + " changed"} for passage in source]
    current = store.save_transcript(
        rid, segments, current["speakers"], current["revision"], action="manual_save"
    )
    store.save_transcript(
        rid,
        source,
        current["speakers"],
        current["revision"],
        action="version_restore",
        context={"restored_from_version_id": first},
    )
    page.reload()
    undo = page.locator("#undo-last")
    expect(undo).to_have_text("↶ Undo last version restore")
    messages = []

    def dismiss(dialog):
        messages.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", dismiss)
    undo.click()
    deadline = time.monotonic() + 10
    while not messages and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert messages == [
        "Undo last version restore? This changes 3 passages. The current text stays in History."
    ]
    expect(page.locator("#passage-0 .passage-text")).to_have_text("First passage")
    page.once("dialog", lambda dialog: dialog.accept())
    undo.click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("First passage changed")


def test_late_undo_reply_never_replaces_another_recording(page, local_app):
    store, first, _ = open_recording(page, local_app, "Synthetic undo late reply")
    edit_text(page, 0, "First passage, edited")
    held = []

    def hold(route):
        held.append((route, route.fetch()))

    page.route("**/undo-last", hold)
    page.locator("#undo-last").click()
    deadline = time.monotonic() + 30
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert held
    expect(page.locator("#undo-last")).to_be_disabled()
    expect(page.locator('#passage-0 [data-action="merge-down"]')).to_be_disabled()
    project = store.create_project("Other synthetic recording")
    other = store.create_recording(project["id"], "other.wav")["id"]
    store.add_result(
        other, normalize_segments([{"text": "Other only", "speaker": "B"}]), {}, "Other", {}
    )
    page.goto(local_app.url + f"/#/recordings/{other}")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Other only")
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_timeout(500)
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Other only")
    expect(page.locator("#transcript .passage")).to_have_count(1)
    # The server committed the exact undo for the first recording only.
    assert store.recording(first)["segments"][0]["text"] == "First passage"
    assert store.recording(other)["segments"][0]["text"] == "Other only"
