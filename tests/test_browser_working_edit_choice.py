"""Editing a non-Working version requires an explicit, revision-fenced choice."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def older_working(local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Working edit choice")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    result = store.add_result(
        rid, normalize_segments([{"text": "Original wording.", "start": 0, "end": 2}]), {}, "A", {}
    )
    old = store.result_versions(rid, result["id"])[0]["id"]
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Current wording.", "start": 0, "end": 2}]),
        {},
        store.recording(rid)["revision"],
        result_id=result["id"],
    )
    current = store.recording(rid)
    preview = result_roles.preview(
        store, rid, old, current["revision"], current["result_roles"]["revision"]
    )
    result_roles.apply(
        store,
        rid,
        old,
        current["revision"],
        current["result_roles"]["revision"],
        preview["fingerprint"],
    )
    return store, rid, result["id"], old


def test_cancel_does_not_start_edit_or_change_working(page, local_app):
    store, rid, _, old = older_working(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action='edit']").click()
    dialog = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(dialog).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 800})
    for button in dialog.get_by_role("button").all():
        rect = button.bounding_box()
        assert 0 <= rect["x"] and rect["x"] + rect["width"] <= 390
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#passage-0 textarea")).to_have_count(0)
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == old
    page.locator("#passage-0 [data-action='edit']").click()
    dialog.get_by_role("button", name="Keep current Working").click()
    page.locator("#passage-0 [data-action='cancel-edit']").click()
    page.locator("#passage-0 [data-action='edit']").click()
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel", exact=True).click()


def test_old_result_edit_asks_and_preserves_or_moves_working(page, local_app):
    store, rid, result_id, old = older_working(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    edit = page.locator("#passage-0 [data-action='edit']")
    edit.click()
    dialog = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#passage-0 textarea")).to_have_count(0)
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == old

    edit.click()
    dialog.get_by_role("button", name="Keep current Working").click()
    page.locator("#passage-0 textarea").fill("Keep the old Working version.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Keep the old Working version.")
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == old

    edit.click()
    dialog.get_by_role("button", name="Set edited version as Working").click()
    page.locator("#passage-0 textarea").fill("New Working wording.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("New Working wording.")
    current = store.recording(rid)
    assert (
        current["result_roles"]["working"]["version_id"]
        == store.result_versions(rid, result_id)[0]["id"]
    )

    # Editing the current Working head follows it without asking again.
    edit.click()
    expect(dialog).to_be_hidden()
    page.locator("#passage-0 textarea").fill("Further Working correction.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Further Working correction.")
    assert (
        store.recording(rid)["result_roles"]["working"]["version_id"]
        == store.result_versions(rid, result_id)[0]["id"]
    )


def test_role_changed_during_edit_keeps_draft_and_rejects_stale_choice(page, local_app):
    store, rid, result_id, old = older_working(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action='edit']").click()
    page.get_by_role("button", name="Set edited version as Working").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("Keep this unsaved correction.")

    current = store.recording(rid)
    head = store.result_versions(rid, result_id)[0]["id"]
    preview = result_roles.preview(
        store, rid, head, current["revision"], current["result_roles"]["revision"]
    )
    result_roles.apply(
        store,
        rid,
        head,
        current["revision"],
        current["result_roles"]["revision"],
        preview["fingerprint"],
    )
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#save-status")).to_contain_text("Save failed")
    expect(draft).to_have_value("Keep this unsaved correction.")
    assert store.recording(rid)["segments"][0]["text"] == "Current wording."
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == head
    assert head != old
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Keep this unsaved correction.")
    assert (
        store.recording(rid)["result_roles"]["working"]["version_id"]
        == store.result_versions(rid, result_id)[0]["id"]
    )


def test_speaker_rename_of_non_working_version_asks_before_name_input(page, local_app):
    store, rid, result_id, _ = older_working(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#speakers [data-speaker]").first.click()
    dialog = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(dialog).to_be_visible()
    expect(page.locator("#name-dialog")).to_be_hidden()
    dialog.get_by_role("button", name="Set edited version as Working").click()
    expect(page.locator("#name-dialog")).to_be_visible()
    page.locator("#name-input").fill("Researcher")
    page.locator("#name-form button[type='submit']").click()
    expect(page.locator("#speakers")).to_contain_text("Researcher")
    assert (
        store.recording(rid)["result_roles"]["working"]["version_id"]
        == store.result_versions(rid, result_id)[0]["id"]
    )


def test_abandoned_edit_does_not_reuse_promotion_consent(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Distinct edit attempts")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    passages = normalize_segments([{"text": "First passage."}, {"text": "Second passage."}])
    result = store.add_result(rid, passages, {}, "A", {})
    old = store.result_versions(rid, result["id"])[0]["id"]
    newer = normalize_segments([{"text": "First reviewed."}, {"text": "Second reviewed."}])
    store.save_transcript(rid, newer, {}, store.recording(rid)["revision"], result_id=result["id"])
    recording = store.recording(rid)
    role_revision = recording["result_roles"]["revision"]
    preview = result_roles.preview(store, rid, old, recording["revision"], role_revision)
    result_roles.apply(
        store, rid, old, recording["revision"], role_revision, preview["fingerprint"]
    )

    page.goto(local_app.url + f"/#/recordings/{rid}")
    prompt = page.get_by_role("dialog", name="Which transcript should be Working?")
    page.locator("#passage-0 [data-action='edit']").click()
    prompt.get_by_role("button", name="Set edited version as Working").click()
    page.locator("#passage-0 textarea").fill("This draft will be discarded.")

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#passage-1 [data-action='edit']").click()
    expect(prompt).to_be_visible()
    prompt.get_by_role("button", name="Keep current Working").click()
    page.locator("#passage-1 textarea").fill("Second draft will also be discarded.")

    # Starting a new attempt on the same passage must ask again, too.
    prior_editor = page.locator("#passage-1 textarea").element_handle()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#passage-1 [data-action='edit']").click()
    expect(prompt).to_be_visible()
    prompt.get_by_role("button", name="Keep current Working").click()
    page.wait_for_function("node => !node.isConnected", arg=prior_editor)
    page.locator("#passage-1 textarea").fill("Final text; keep the earlier Working version.")
    page.locator("#passage-1 [data-action='save-edit']").click()
    expect(page.locator("#passage-1 .passage-text")).to_have_text(
        "Final text; keep the earlier Working version."
    )
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == old
