"""Undo a verified split without losing other passages or a live editor draft."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_split(store):
    project = store.create_project("Undo split")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    source = normalize_segments(
        [
            {
                "text": "Alpha beta.",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Alpha", "start": 0, "end": 0.9},
                    {"word": "beta.", "start": 1, "end": 2},
                ],
            },
            {"text": "Other original", "start": 2, "end": 3, "speaker": "B"},
        ]
    )
    store.add_result(rid, source, {"A": "Anna", "B": "Benny"}, "Synthetic", {}, auto_first=True)
    return rid, source


def split_first(page):
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").evaluate("e => e.setSelectionRange(6, 6)")
    page.locator('#passage-0 [data-action="split"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    expect(page.locator('#passage-0 [data-action="undo-split"]')).to_be_visible()
    expect(page.locator('[data-action="undo-split"]')).to_have_count(1)


def test_undo_split_preserves_other_edits_and_open_draft(page, local_app):
    store = local_app.server.config.app.state.store
    rid, source = seed_split(store)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    split_first(page)
    split_version = store.recording(rid)["active_version"]["id"]
    undo = page.locator('#passage-0 [data-action="undo-split"]')

    def fail_check(route):
        route.fulfill(status=503, json={"detail": "Synthetic eligibility failure"})

    page.route("**/merge-undo-options?*", fail_check)
    page.reload()
    expect(page.locator("#merge-undo-check-error")).to_be_visible()
    expect(page.locator("#merge-undo-check-error")).to_contain_text("splits")
    page.unroute("**/merge-undo-options?*", fail_check)
    page.locator("#merge-undo-check-retry").click()
    expect(undo).to_be_visible()

    page.locator('#passage-1 [data-action="edit"]').click()
    page.locator("#passage-1 textarea").fill("Other saved edit")
    page.locator('#passage-1 [data-action="save-edit"]').click()
    expect(page.locator("#passage-1 .passage-text")).to_have_text("Other saved edit")
    page.locator('#passage-1 [data-action="edit"]').click()
    draft = page.locator("#passage-1 textarea")
    draft.fill("Other unsaved draft")
    page.once("dialog", lambda dialog: dialog.dismiss())
    undo.click()
    expect(draft).to_have_value("Other unsaved draft")
    expect(page.locator("#transcript .passage")).to_have_count(3)
    page.locator('#passage-1 [data-action="cancel-edit"]:visible').click()
    undo.click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    expect(page.locator('[data-action="undo-split"]')).to_have_count(0)
    recording = store.recording(rid)
    assert recording["segments"][0] == source[0]
    assert recording["segments"][1]["text"] == "Other saved edit"
    assert recording["active_version"]["id"] != split_version
    assert recording["result_roles"]["working"]["version_id"] == recording["active_version"]["id"]

    # Editing either split half invalidates the offered inverse.
    split_first(page)
    right = store.recording(rid)["segments"][1]["id"]
    page.locator(f'#passage-{right} [data-action="edit"]').click()
    page.locator(f"#passage-{right} textarea").fill("A changed half")
    page.locator(f'#passage-{right} [data-action="save-edit"]').click()
    expect(page.locator(f"#passage-{right} .passage-text")).to_have_text("A changed half")
    expect(page.locator('[data-action="undo-split"]')).to_have_count(0)


def test_undo_split_preserves_explicit_working_decision(page, local_app):
    store = local_app.server.config.app.state.store
    rid, source = seed_split(store)
    original = store.recording(rid)["active_version"]["id"]
    page.goto(local_app.url + f"/#/recordings/{rid}")
    split_first(page)
    current = store.recording(rid)
    preview = result_roles.preview(
        store, rid, original, current["revision"], current["result_roles"]["revision"]
    )
    result_roles.apply(
        store,
        rid,
        original,
        current["revision"],
        current["result_roles"]["revision"],
        preview["fingerprint"],
    )
    page.reload()
    undo = page.locator('#passage-0 [data-action="undo-split"]')
    undo.click()
    choice = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(choice).to_be_visible()
    choice.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    undo.click()
    choice.get_by_role("button", name="Keep current Working").click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    recording = store.recording(rid)
    assert recording["segments"] == source
    assert recording["result_roles"]["working"]["version_id"] == original
