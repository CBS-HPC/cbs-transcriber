"""Passage deletion changes only the chosen saved result and keeps exact research versions."""

import pytest
from playwright.sync_api import expect
from test_browser import create_project, import_recording
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def passages():
    return normalize_segments(
        [
            {
                "text": word,
                "start": index,
                "end": index + 0.8,
                "speaker": "S1",
                "words": [{"word": word, "start": index, "end": index + 0.8}],
            }
            for index, word in enumerate(("First.", "Middle.", "Last."))
        ]
    )


def test_delete_passage_keeps_history_comparison_notes_and_timed_playback(
    page, local_app, wav_bytes
):
    store = local_app.server.config.app.state.store
    page.goto(local_app.url)
    create_project(page, "Passage deletion")
    import_recording(page, local_app, "Passages.wav", wav_bytes)
    rid = page.url.rsplit("/", 1)[-1]
    source = store.add_result(
        rid,
        passages(),
        {"S1": "Research speaker"},
        "Timed result",
        {"capability": "words"},
        auto_first=True,
    )
    other = store.add_result(
        rid, passages(), {"S1": "Research speaker"}, "Other result", {"capability": "words"}
    )
    unchanged = store.result(rid, other["id"])["segments"]
    page.reload()
    expect(page.locator("#result-select")).to_have_value(source["id"])
    page.locator("#compare-result").select_option(other["id"])
    expect(page.locator("#comparison-transcript .passage")).to_have_count(3)
    page.locator("#recording-notes").locator("..").locator("summary").click()
    page.locator("#recording-notes").fill("Independent recording notes draft")
    page.locator('#passage-1 [data-action="edit"]').click()
    page.locator("#passage-1 textarea").fill("Unsaved middle correction")
    confirmations = []

    def dismiss(dialog):
        confirmations.append(dialog.message)
        dialog.dismiss()

    page.once("dialog", dismiss)
    page.locator('#passage-1 [data-action="delete"]').click()
    assert "History" in confirmations[0] and "Unsaved passage" in confirmations[0]
    expect(page.locator("#passage-1 textarea")).to_have_value("Unsaved middle correction")
    assert len(store.recording(rid)["segments"]) == 3
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-1 [data-action="delete"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    expect(page.locator("#passage-1")).to_have_count(0)
    expect(page.locator("#comparison-transcript .passage")).to_have_count(3)
    expect(page.locator("#recording-notes")).to_have_value("Independent recording notes draft")
    assert [s["id"] for s in store.recording(rid)["segments"]] == ["0", "2"]
    assert store.result(rid, other["id"])["segments"] == unchanged
    page.locator('#passage-2 [data-action="replay"]').click()
    expect(page.locator("#passage-2 .word.current")).to_have_count(1)
    page.locator("#play-pause").click()
    page.screenshot(path="test-results/passage-delete-comparison.png", full_page=True)
    # Notes remain independent of transcript structure and do not need to be discarded.
    page.locator("#compare-result").select_option("")
    for segment in ("0", "2"):
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator(f'#passage-{segment} [data-action="delete"]').click()
        expect(page.locator(f"#passage-{segment}")).to_have_count(0)
    expect(page.locator("#transcript .transcript-empty")).to_contain_text(
        "No passages in this result"
    )
    expect(page.locator("#history-open")).to_be_enabled()
    expect(page.locator("#result-select")).to_have_value(source["id"])
    assert store.result(rid, source["id"])["segments"] == []
    versions = store.result_versions(rid, source["id"])
    assert [v["action"] for v in versions].count("passage_delete") == 3
    original = store.result_version(rid, source["id"], versions[-1]["id"])
    assert len(original["payload"]["segments"]) == 3
    assert store.result(rid, other["id"])["segments"] == unchanged


def test_delete_passage_retains_failed_draft_and_ignores_late_navigation_reply(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Passage delete race")
    first = store.create_recording(project["id"], "First.wav")
    second = store.create_recording(project["id"], "Second.wav")
    result = store.add_result(first["id"], passages(), {}, "First result", {}, auto_first=True)
    other = store.add_result(
        second["id"],
        normalize_segments([{"text": "Other recording text."}]),
        {},
        "Second result",
        {},
    )
    held, bodies = [], []
    reject = [True]

    def intercept(route):
        bodies.append(route.request.post_data_json)
        if reject[0]:
            route.fulfill(status=409, json={"detail": "Synthetic stale passage revision."})
        else:
            held.append((route, route.fetch()))
            page.evaluate("window.deletePassageHeld = true")

    page.route(f"**/api/recordings/{first['id']}/segments/0/delete", intercept)
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").fill("Retain this correction")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-0 [data-action="delete"]').click()
    expect(page.locator("#toast")).to_have_text("Synthetic stale passage revision.")
    expect(page.locator("#passage-0 textarea")).to_have_value("Retain this correction")
    expect(page.locator("#passage-0 textarea")).to_be_enabled()
    assert len(store.recording(first["id"])["segments"]) == 3
    current = store.recording(first["id"])
    assert bodies[0] == {
        "revision": current["revision"],
        "result_id": result["id"],
        "working_choice": {
            "version_id": current["active_version"]["id"],
            "role_revision": current["result_roles"]["revision"],
            "make_working": True,
        },
    }
    reject[0] = False
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-0 [data-action="delete"]').click()
    page.wait_for_function("() => window.deletePassageHeld === true")
    expect(page.locator('#transcript [data-action="delete"]:enabled')).to_have_count(0)
    expect(page.locator("#results-manage")).to_be_disabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.evaluate("id => { location.hash = '/recordings/' + id; }", second["id"])
    expect(page.locator("#recording-title")).to_have_text("Second")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#recording-title")).to_have_text("Second")
    expect(page.locator("#result-select")).to_have_value(other["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Other recording text.")
    assert len(store.recording(first["id"])["segments"]) == 2
