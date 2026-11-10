"""Shared card/editor note saves retain frozen revisions through delayed replies."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


@pytest.fixture
def note_recordings(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic note concurrency")
    recordings = []
    for name in ("First", "Second"):
        recording = store.create_recording(project["id"], name + ".wav")
        store.save_notes("recordings", recording["id"], name + " baseline.", 0)
        recordings.append(store.recording(recording["id"]))
    return store, project, recordings


def hold_note_reply(page, identifier, *, commit=False):
    held = []

    def capture(route):
        held.append((route, route.fetch() if commit else None))
        page.evaluate("window.noteReplyHeld = true")

    pattern = f"**/api/recordings/{identifier}/notes"
    page.route(pattern, capture)
    return pattern, capture, held


def open_card_notes(page, identifier):
    host = page.locator(f'[data-card-notes="{identifier}"]')
    host.locator("summary").click()
    return host


def test_failed_pending_notes_keep_original_revision_after_external_update_and_navigation(
    page, local_app, note_recordings
):
    store, project, (first, _) = note_recordings
    pattern, capture, held = hold_note_reply(page, first["id"])
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    card = open_card_notes(page, first["id"])
    draft = "Old draft with Danish metadata: æøå."
    card.locator("textarea").fill(draft)
    card.get_by_role("button", name="Save notes").click()
    page.wait_for_function("() => window.noteReplyHeld === true")
    try:
        external = store.save_notes(
            "recordings", first["id"], "Newer notes from another window.", first["notes_revision"]
        )
        page.locator(f'[data-open="{first["id"]}"]').click()
        expect(page.locator("#recording-title")).to_have_text("First")
        expect(page.locator("#recording-notes")).to_have_value(draft)
        expect(page.locator("#recording-notes")).to_be_disabled()
        expect(page.locator("#recording-notes")).to_have_attribute(
            "data-revision", str(first["notes_revision"])
        )
        assert (
            page.locator("#recording-notes").evaluate("input => input.defaultValue")
            == first["notes"]
        )
        route, _ = held.pop()
        reply = route.fetch()
        assert reply.status == 409
        route.fulfill(response=reply)
        page.unroute(pattern, capture)
        page.wait_for_function("() => pendingRecordingNotes.size === 0")
        expect(page.locator("#recording-notes-status")).to_have_text("Save failed — notes retained")
        expect(page.locator("#recording-notes")).to_have_value(draft)
        expect(page.locator("#recording-notes")).to_have_attribute(
            "data-revision", str(first["notes_revision"])
        )
        page.locator("#editor-layout .description-notes summary").click()
        url = local_app.url + f"/api/recordings/{first['id']}/notes"
        with page.expect_request(url) as retry_request, page.expect_response(url) as retry_reply:
            page.locator("#recording-notes-save").click()
        assert retry_request.value.post_data_json == {
            "text": draft,
            "revision": first["notes_revision"],
        }
        assert retry_reply.value.status == 409
        expect(page.locator("#recording-notes-status")).to_have_text("Save failed — notes retained")
        assert store.recording(first["id"])["notes"] == external["notes"]
        assert store.recording(first["id"])["notes_revision"] == external["notes_revision"]
    finally:
        for route, _ in held:
            route.fulfill(status=503, json={"detail": "Synthetic interrupted note save."})


def test_late_successful_note_reply_does_not_regress_a_newer_observed_revision(
    page, local_app, note_recordings
):
    store, project, (first, _) = note_recordings
    _, _, held = hold_note_reply(page, first["id"], commit=True)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    card = open_card_notes(page, first["id"])
    card.locator("textarea").fill("First accepted save, reply delayed.")
    card.get_by_role("button", name="Save notes").click()
    page.wait_for_function("() => window.noteReplyHeld === true")
    try:
        accepted = store.recording(first["id"])
        assert accepted["notes_revision"] == first["notes_revision"] + 1
        external = store.save_notes(
            "recordings",
            first["id"],
            "The newer accepted notes: Øresund.",
            accepted["notes_revision"],
        )
        page.locator(f'[data-open="{first["id"]}"]').click()
        expect(page.locator("#recording-title")).to_have_text("First")
        expect(page.locator("#recording-notes")).to_be_disabled()
        route, reply = held.pop()
        assert reply.status == 200
        route.fulfill(response=reply)
        page.wait_for_function("() => pendingRecordingNotes.size === 0")
        expect(page.locator("#recording-notes")).to_have_value(external["notes"])
        expect(page.locator("#recording-notes")).to_have_attribute(
            "data-revision", str(external["notes_revision"])
        )
        assert (
            page.locator("#recording-notes").evaluate("input => input.defaultValue")
            == external["notes"]
        )
        expect(page.locator("#recording-notes")).to_be_enabled()
        page.locator("#back-library").click()
        expect(card.locator("textarea")).to_have_value(external["notes"])
        expect(card.locator("textarea")).to_have_attribute(
            "data-revision", str(external["notes_revision"])
        )
        expect(card.locator('[role="status"]')).to_have_text("Notes saved")
        assert store.recording(first["id"])["notes"] == external["notes"]
    finally:
        for route, reply in held:
            route.fulfill(response=reply)


def test_failed_note_reply_after_card_rerender_retains_drafts_error_and_enabled_controls(
    page, local_app, note_recordings
):
    store, project, (first, second) = note_recordings
    _, _, held = hold_note_reply(page, first["id"])
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    first_card = open_card_notes(page, first["id"])
    second_card = open_card_notes(page, second["id"])
    first_draft = "A failed save must retain this draft."
    second_draft = "Another draft remains untouched: æøå."
    first_card.locator("textarea").fill(first_draft)
    second_card.locator("textarea").fill(second_draft)
    first_card.get_by_role("button", name="Save notes").click()
    page.wait_for_function("() => window.noteReplyHeld === true")
    try:
        page.locator("#recording-search").fill("First")
        expect(second_card).to_have_count(0)
        page.locator("#recording-search").fill("")
        expect(first_card.locator("textarea")).to_be_disabled()
        expect(second_card.locator("textarea")).to_have_value(second_draft)
        route, _ = held.pop()
        route.fulfill(status=503, json={"detail": "Synthetic temporary note-save failure."})
        page.wait_for_function("() => pendingRecordingNotes.size === 0")
        expect(first_card.locator("textarea")).to_have_value(first_draft)
        expect(first_card.locator('[role="status"]')).to_have_text("Save failed — notes retained")
        expect(first_card.locator("textarea")).to_be_enabled()
        expect(first_card.get_by_role("button", name="Save notes")).to_be_enabled()
        expect(second_card.locator("textarea")).to_have_value(second_draft)
        expect(second_card.locator('[role="status"]')).to_have_text("Unsaved notes")
        assert store.recording(first["id"])["notes"] == first["notes"]
        assert store.recording(second["id"])["notes"] == second["notes"]
    finally:
        for route, _ in held:
            route.fulfill(status=503, json={"detail": "Synthetic interrupted note save."})


def test_failed_editor_note_draft_is_recoverable_until_explicitly_discarded(
    page, local_app, note_recordings
):
    store, project, (first, second) = note_recordings
    _, _, held = hold_note_reply(page, first["id"])
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    page.locator("#editor-layout .description-notes summary").click()
    draft = "An editor-origin failed draft: Øresund research notes."
    page.locator("#recording-notes").fill(draft)
    page.locator("#recording-notes-save").click()
    page.wait_for_function("() => window.noteReplyHeld === true")
    try:
        page.evaluate("id => location.hash = '/recordings/' + id", second["id"])
        expect(page.locator("#recording-title")).to_have_text("Second")
        expect(page.locator("#recording-notes")).to_have_value(second["notes"])
        external = store.save_notes(
            "recordings",
            first["id"],
            "Database notes accepted in another window.",
            first["notes_revision"],
        )
        route, _ = held.pop()
        reply = route.fetch()
        assert reply.status == 409
        route.fulfill(response=reply)
        page.wait_for_function("() => pendingRecordingNotes.size === 0")
        expect(page.locator("#recording-notes")).to_have_value(second["notes"])
        page.evaluate("id => location.hash = '/recordings/' + id", first["id"])
        expect(page.locator("#recording-title")).to_have_text("First")
        expect(page.locator("#recording-notes")).to_have_value(draft)
        expect(page.locator("#recording-notes")).to_have_attribute(
            "data-revision", str(first["notes_revision"])
        )
        assert (
            page.locator("#recording-notes").evaluate("input => input.defaultValue")
            == first["notes"]
        )
        expect(page.locator("#recording-notes-status")).to_have_text("Save failed — notes retained")
        expect(page.locator("#recording-notes-save")).to_be_enabled()
        confirmations = []

        def discard_notes(dialog):
            confirmations.append(dialog.message)
            dialog.accept()

        page.once("dialog", discard_notes)
        page.locator("#back-library").click()
        expect(page.locator("#project-title")).to_have_text(project["name"])
        assert confirmations == ["Discard these unsaved recording notes?"]
        page.locator(f'[data-open="{first["id"]}"]').click()
        expect(page.locator("#recording-title")).to_have_text("First")
        expect(page.locator("#recording-notes")).to_have_value(external["notes"])
        expect(page.locator("#recording-notes")).to_have_attribute(
            "data-revision", str(external["notes_revision"])
        )
        assert store.recording(first["id"])["notes"] == external["notes"]
    finally:
        for route, _ in held:
            route.fulfill(status=503, json={"detail": "Synthetic interrupted note save."})
