"""Undoing a passage merge preserves unrelated edits and an open draft."""

import time

import pytest
from playwright.sync_api import expect
from test_browser import create_project, import_recording
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_undo_merge_preserves_other_edits_and_respects_draft(page, local_app, wav_bytes):
    page.goto(local_app.url)
    create_project(page, "Undo merge")
    import_recording(page, local_app, "undo.wav", wav_bytes)
    identifier = page.url.rsplit("/", 1)[-1]
    store = local_app.server.config.app.state.store
    source = normalize_segments(
        [
            {"text": "First", "start": 0, "end": 1, "speaker": "A"},
            {"text": "Second", "start": 1, "end": 2, "speaker": "A"},
            {"text": "Third", "start": 2, "end": 3, "speaker": "A"},
        ]
    )
    store.add_result(identifier, source, {"A": "Anna"}, "Synthetic", {}, auto_first=True)
    page.reload()
    page.locator('#passage-0 [data-action="merge-down"]').click()
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    undo = page.locator('#passage-0 [data-action="undo-merge"]')
    expect(undo).to_be_visible()

    def fail_check(route):
        route.fulfill(status=503, json={"detail": "Temporary failure"})

    page.route("**/merge-undo-options?*", fail_check)
    page.reload()
    expect(page.locator("#merge-undo-check-error")).to_be_visible()
    page.unroute("**/merge-undo-options?*", fail_check)
    page.locator("#merge-undo-check-retry").click()
    expect(undo).to_be_visible()
    expect(page.locator("#merge-undo-check-error")).to_be_hidden()

    def limit_history(route):
        response = route.fetch()
        payload = response.json()
        payload["history_limited"] = True
        route.fulfill(response=response, json=payload)

    page.route("**/merge-undo-options?*", limit_history)
    page.reload()
    expect(undo).to_be_visible()
    expect(page.locator("#merge-undo-history-limit")).to_be_visible()
    page.unroute("**/merge-undo-options?*", limit_history)

    # Hold the next eligibility response while a draft is opened. Its eventual
    # arrival must add the action without replacing the textarea or its value.
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.fetch = (...args) => {
            const result = originalFetch(...args);
            if (!String(args[0]).includes('/merge-undo-options?')) return result;
            return result.then(response => new Promise(resolve => {
                window.releaseUndoOptions = () => resolve(response);
            }));
        };
    }""")
    page.locator('#passage-2 [data-action="edit"]').click()
    page.locator("#passage-2 textarea").fill("Third, edited")
    page.locator('#passage-2 [data-action="save-edit"]').click()
    expect(page.locator("#passage-2 .passage-text")).to_have_text("Third, edited")
    page.locator('#passage-2 [data-action="edit"]').click()
    draft = page.locator("#passage-2 textarea")
    draft.fill("Unsaved draft")
    page.wait_for_function("typeof window.releaseUndoOptions === 'function'")
    page.evaluate("window.releaseUndoOptions()")
    expect(undo).to_be_visible()
    expect(draft).to_have_value("Unsaved draft")
    page.once("dialog", lambda dialog: dialog.dismiss())
    undo.click()
    expect(draft).to_have_value("Unsaved draft")
    expect(page.locator("#transcript .passage")).to_have_count(2)

    page.locator('#passage-2 [data-action="cancel-edit"]').click()

    # A late hints response must not re-enable editing while undo is pending.
    hints = []
    undos = []

    def hold_hints(route):
        hints.append((route, route.fetch()))

    def hold_undo(route):
        undos.append((route, route.fetch()))

    def wait_for_routes(routes):
        deadline = time.monotonic() + 30
        while not routes and time.monotonic() < deadline:
            page.wait_for_timeout(100)
        assert routes

    page.route("**/hints", hold_hints)
    page.reload()
    expect(undo).to_be_visible()
    wait_for_routes(hints)
    page.route("**/segments/*/undo-merge", hold_undo)
    undo.click()
    wait_for_routes(undos)
    hints[0][0].fulfill(response=hints[0][1])
    expect(page.locator('#passage-0 [data-action="merge-down"]')).to_be_disabled()
    undos[0][0].fulfill(response=undos[0][1])
    expect(page.locator("#transcript .passage")).to_have_count(3)
    segments = store.recording(identifier)["segments"]
    assert segments[:2] == source[:2]
    assert segments[2]["text"] == "Third, edited"
    expect(page.locator('[data-action="undo-merge"]')).to_have_count(0)


def test_undo_of_nonworking_merge_asks_before_changing_working(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Undo Working decision")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    source = normalize_segments([{"text": "First"}, {"text": "Second"}])
    store.add_result(rid, source, {}, "Synthetic", {}, auto_first=True)
    original = store.recording(rid)["active_version"]["id"]
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator('#passage-0 [data-action="merge-down"]').click()
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    expect(page.locator('#passage-0 [data-action="undo-merge"]')).to_be_visible()

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
    undo = page.locator('#passage-0 [data-action="undo-merge"]')
    undo.click()
    dialog = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#transcript .passage")).to_have_count(1)
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == original
    undo.click()
    dialog.get_by_role("button", name="Keep current Working").click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    assert store.recording(rid)["result_roles"]["working"]["version_id"] == original
