"""B376: fresh preview authority stays separate from the editor and its drafts."""

from copy import deepcopy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_result_conflicts import choose, open_conflicts
from test_browser_result_merge import branch as branch
from test_browser_result_merge import open_preview

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import edit_segment

pytestmark = pytest.mark.browser


def open_review(page, branch, kind):
    if kind == "merge":
        open_preview(page, branch)
        return "result-merge"
    open_conflicts(page, branch)
    choose(page, "3", "wording", "current")
    return "conflict-review"


def editor_snapshot(page):
    return page.evaluate("""() => ({
        id: state.recording.id,
        result: state.recording.active_transcript_id,
        revision: state.recording.revision,
        head: state.recording.active_version.id,
        roles: state.recording.result_roles
    })""")


@pytest.mark.parametrize("kind", ["merge", "conflicts"])
def test_refresh_then_close_keeps_the_editor_snapshot_and_draft(page, branch, kind):
    store, rid = branch["store"], branch["rid"]
    page.goto(branch["url"])
    page.locator("#passage-1 [data-action=edit]").click()
    draft = page.locator("#passage-1 textarea")
    draft.fill("Keep this unsaved correction exactly.")
    before = editor_snapshot(page)
    prefix = open_review(page, branch, kind)

    current = store.recording(rid)
    changed = deepcopy(current["segments"])
    changed[0] = edit_segment(changed[0], "free", "New wording from another window.")
    store.save_transcript(
        rid, changed, current["speakers"], current["revision"], result_id=branch["ours"]["id"]
    )
    page.locator(f"#{prefix}-refresh").click()
    expect(page.locator(f"#{prefix}-apply")).to_be_enabled()
    expect(page.locator(f"#{prefix}-error")).to_be_hidden()
    page.locator(f"#{prefix}-dialog").evaluate("dialog => dialog.close()")
    page.get_by_role("button", name="Close result manager", exact=True).click()

    expect(draft).to_have_value("Keep this unsaved correction exactly.")
    assert editor_snapshot(page) == before
    assert store.recording(rid)["segments"][0] == changed[0]


@pytest.mark.parametrize("kind", ["merge", "conflicts"])
def test_closed_preview_cannot_submit_a_late_working_choice(page, branch, kind):
    store, rid = branch["store"], branch["rid"]
    current = store.recording(rid)
    alternate = store.result_versions(rid, branch["incoming"]["id"])[0]["id"]
    roles = current["result_roles"]["revision"]
    preview = result_roles.preview(store, rid, alternate, current["revision"], roles)
    result_roles.apply(store, rid, alternate, current["revision"], roles, preview["fingerprint"])
    head = store.result_versions(rid, branch["ours"]["id"])[0]["id"]

    page.goto(branch["url"])
    prefix = open_review(page, branch, kind)
    writes = []

    def refuse_unexpected_write(route):
        writes.append(route.request.post_data_json)
        route.fulfill(status=503, json={"detail": "Unexpected write from a closed preview"})

    page.route(f"**/transcripts/{branch['ours']['id']}/{kind}", refuse_unexpected_write)
    page.locator(f"#{prefix}-apply").click()
    choice = page.get_by_role("dialog", name="Which transcript should be Working?", exact=True)
    expect(choice).to_be_visible()
    page.locator(f"#{prefix}-dialog").evaluate("dialog => dialog.close()")
    choice.get_by_role("button", name="Keep current Working", exact=True).click()
    page.wait_for_function("id => !pendingResultMutations.has(id)", arg=rid)

    assert writes == []
    assert store.result_versions(rid, branch["ours"]["id"])[0]["id"] == head
