"""Inactive deleted passages restore individually without rolling back later edits."""

from copy import deepcopy

import httpx
import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import edit_segment, normalize_segments

pytestmark = pytest.mark.browser


def delete(app, store, rid, result, sid):
    current = store.recording(rid)
    working = current["result_roles"]["working"]
    response = httpx.post(
        app.url + f"/api/recordings/{rid}/segments/{sid}/delete",
        headers={
            "X-CBS-Request": "1",
            "Origin": app.url,
            "X-CBS-Instance": app.server.config.app.state.browser_instance,
        },
        json={
            "revision": current["revision"],
            "result_id": result,
            "working_choice": {
                "version_id": current["active_version"]["id"],
                "role_revision": current["result_roles"]["revision"],
                "make_working": working is not None
                and working["version_id"] == current["active_version"]["id"],
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def deleted(local_app):
    store = local_app.server.config.app.state.store
    assert any(
        route.path.endswith("/segments/{segment_id}/restore")
        for route in local_app.server.config.app.routes
    ), "The production restoration route must be mounted."
    project = store.create_project("Synthetic deleted passages")
    rid = store.create_recording(project["id"], "deleted.wav")["id"]
    original = normalize_segments(
        [
            {"text": text, "start": index, "end": index + 0.8, "speaker": "A"}
            for index, text in enumerate(
                ["First passage.", "Middle <img src=x onerror=alert(1)>.", "Last passage."]
            )
        ]
    )
    result = store.add_result(rid, original, {"A": "Anna"}, "Original", {}, auto_first=True)
    other = store.create_recording(project["id"], "other.wav")["id"]
    store.add_result(
        other, normalize_segments([{"text": "Other recording."}]), {}, "Other", {}, auto_first=True
    )
    delete(local_app, store, rid, result["id"], "1")
    return local_app, store, rid, result["id"], other, original


def restore_button(page):
    return page.get_by_role("button", name="Restore deleted passage 1", exact=True)


def open_deleted(page, fixture):
    app, _, rid, _, _, _ = fixture
    page.goto(app.url + f"/#/recordings/{rid}")
    expect(restore_button(page)).to_be_enabled()


def test_inactive_deleted_passage_is_excluded_then_restores_without_rolling_back_later_edit(
    page, deleted
):
    app, store, rid, result, _, original = deleted
    current = store.recording(rid)
    later = deepcopy(current["segments"])
    later[-1] = edit_segment(later[-1], "free", "Later correction to the last passage.")
    store.save_transcript(rid, later, current["speakers"], current["revision"], result_id=result)
    lookup = []
    page.on(
        "request",
        lambda request: (
            lookup.append(request.url) if "/merge-undo-options?" in request.url else None
        ),
    )
    open_deleted(page, deleted)
    expect(page.locator("#deleted-passages .deleted-passage-preview")).to_have_text(
        original[1]["text"]
    )
    expect(page.locator("#deleted-passages img")).to_have_count(0)
    expect(page.locator("#deleted-passages [data-action=replay]")).to_have_count(0)
    expect(page.locator("#transcript > .passage")).to_have_count(2)
    expect(page.locator("[data-review-summary]")).to_contain_text("Checked 0 of 2")
    exported = httpx.get(app.url + f"/api/recordings/{rid}/export?format=json")
    assert exported.status_code == 200
    assert len(exported.json()["segments"]) == 2
    assert "Middle" not in exported.text
    page.locator("#transcript-search").fill("Middle")
    expect(page.locator("#transcript > .passage")).to_have_count(0)
    expect(page.locator("#next-uncertain")).to_be_disabled()
    page.locator("#transcript-search").fill("")
    assert len(lookup) == 1
    restore_button(page).click()
    expect(page.locator("#transcript > .passage")).to_have_count(3)
    expect(page.locator("#deleted-passages")).to_be_hidden()
    expect(page.locator("#passage-2 .passage-text")).to_have_text(later[-1]["text"])
    assert store.recording(rid)["segments"] == [later[0], original[1], later[1]]


def test_restore_failure_keeps_the_inactive_passage_and_exact_retry_target(page, deleted):
    app, store, rid, result, _, _ = deleted
    requests = []
    reject = {"value": True}

    def intercept(route):
        requests.append(route.request.post_data_json)
        if reject["value"]:
            route.fulfill(status=409, json={"detail": "Synthetic stale restoration proof."})
        else:
            route.continue_()

    page.route("**/segments/1/restore", intercept)
    open_deleted(page, deleted)
    descriptor = httpx.get(
        app.url + f"/api/recordings/{rid}/merge-undo-options?result_id={result}"
    ).json()["deleted_segments"][0]
    before = store.recording(rid)
    restore_button(page).click()
    expect(page.locator("#deleted-passages [role=alert]")).to_contain_text(
        "Synthetic stale restoration proof"
    )
    expect(restore_button(page)).to_be_enabled()
    assert store.recording(rid)["segments"] == before["segments"]
    assert requests[0]["delete_version_id"] == descriptor["delete_version_id"]
    reject["value"] = False
    restore_button(page).click()
    expect(page.locator("#transcript > .passage")).to_have_count(3)
    assert requests[0] == requests[1]


def test_restoration_refuses_a_dirty_editor_and_retains_its_node_caret_and_draft(page, deleted):
    open_deleted(page, deleted)
    page.locator("#passage-0 [data-action=edit]").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("Keep this unsaved passage correction.")
    draft.evaluate("node => { window.deletedPassageDraft = node; node.setSelectionRange(3, 8); }")
    expect(restore_button(page)).to_be_disabled()
    expect(page.locator("[data-deleted-draft]")).to_be_visible()
    writes = []
    page.route("**/segments/1/restore", lambda route: writes.append(route))
    restore_button(page).evaluate(
        "node => node.dispatchEvent(new MouseEvent('click', {bubbles:true}))"
    )
    expect(draft).to_have_value("Keep this unsaved passage correction.")
    assert draft.evaluate("""node => node === window.deletedPassageDraft &&
        node.selectionStart === 3 && node.selectionEnd === 8""")
    assert writes == []
    page.unroute("**/segments/1/restore")
    page.locator('#passage-0 [data-editor-pane="text"] [data-action=cancel-edit]').click()
    expect(restore_button(page)).to_be_enabled()
    restore_button(page).click()
    expect(page.locator("#transcript > .passage")).to_have_count(3)


def test_restore_obtains_working_choice_and_can_keep_an_alternative_working_version(page, deleted):
    _, store, rid, result, _, _ = deleted
    alternative = store.add_result(
        rid,
        normalize_segments([{"text": "Alternative working transcript."}]),
        {},
        "Alternative",
        {},
    )
    alternative_version = store.result_versions(rid, alternative["id"])[0]["id"]
    current = store.recording(rid)
    preview = result_roles.preview(
        store, rid, alternative_version, current["revision"], current["result_roles"]["revision"]
    )
    result_roles.apply(
        store,
        rid,
        alternative_version,
        current["revision"],
        current["result_roles"]["revision"],
        preview["fingerprint"],
    )
    open_deleted(page, deleted)
    restore_button(page).click()
    choice = page.locator("#working-edit-dialog")
    expect(choice).to_be_visible()
    choice.locator('[data-working-edit-choice][value="cancel"]').click()
    expect(restore_button(page)).to_be_enabled()
    assert len(store.recording(rid)["segments"]) == 2
    restore_button(page).click()
    choice.get_by_role("button", name="Keep current Working", exact=True).click()
    expect(page.locator("#transcript > .passage")).to_have_count(3)
    after = store.recording(rid)
    assert after["active_transcript_id"] == result
    assert after["result_roles"]["working"]["version_id"] == alternative_version


def test_consecutive_deletions_restore_only_the_currently_proven_anchor_order(page, deleted):
    app, store, rid, result, _, original = deleted
    delete(app, store, rid, result, "0")
    open_deleted(page, deleted)
    expect(page.locator("#deleted-passages .deleted-passage-preview")).to_have_text(
        original[0]["text"]
    )
    restore_button(page).click()
    expect(page.locator("#transcript > .passage")).to_have_count(2)
    expect(page.locator("#deleted-passages .deleted-passage-preview")).to_have_text(
        original[1]["text"]
    )
    restore_button(page).click()
    expect(page.locator("#transcript > .passage")).to_have_count(3)
    assert store.recording(rid)["segments"] == original


@pytest.mark.parametrize("stage", ["lookup", "apply"])
def test_late_deleted_passage_reply_cannot_replace_another_recording_or_its_draft(
    page, deleted, stage
):
    app, store, rid, _, other, _ = deleted
    held = []
    if stage == "apply":
        open_deleted(page, deleted)

        def hold_apply(route):
            held.append((route, route.fetch()))
            page.evaluate("window.deletedPassageReplyHeld = true")

        page.route("**/segments/1/restore", hold_apply)
        restore_button(page).click()
    else:

        def hold_lookup(route):
            held.append((route, route.fetch()))
            page.evaluate("window.deletedPassageReplyHeld = true")

        page.route(f"**/recordings/{rid}/merge-undo-options?*", hold_lookup)
        page.goto(app.url + f"/#/recordings/{rid}")
    page.wait_for_function("() => window.deletedPassageReplyHeld === true")
    page.goto(app.url + f"/#/recordings/{other}")
    expect(page.locator("#recording-title")).to_have_text("other")
    page.locator("#passage-0 [data-action=edit]").click()
    page.locator("#passage-0 textarea").fill("Other recording's unsaved correction.")
    route, response = held[0]
    route.fulfill(response=response)
    expect(page.locator("#passage-0 textarea")).to_have_value(
        "Other recording's unsaved correction."
    )
    expect(page.locator("#deleted-passages")).to_be_hidden()
    assert len(store.recording(rid)["segments"]) == (3 if stage == "apply" else 2)
