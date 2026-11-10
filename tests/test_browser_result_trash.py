"""Recoverable result management uses synthetic local recordings and real local APIs."""

import json
import time
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect
from test_browser import create_project, import_recording
from test_browser import local_app as local_app
from test_browser import page as page
from test_working_reports import mark_active_latest

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed_results(store, recording_id):
    first = store.add_result(
        recording_id,
        normalize_segments([{"text": "Original wording.", "start": 0, "end": 1}]),
        {},
        "Word model",
        {"kind": "provider", "model": "whisper-1", "capability": "words"},
    )
    second = store.add_result(
        recording_id,
        normalize_segments([{"text": "Aligned wording.", "start": 0, "end": 1}]),
        {},
        "Local alignment",
        {"kind": "enhancement", "source_result_id": first["id"], "options": {"align": True}},
    )
    return first, second


def open_results(page):
    """Versions… opens on History; its Results tab is the manager (directly without a result)."""
    page.locator("#history-open").click()
    if page.locator("#history-dialog").is_visible():
        page.locator("#results-manage").click()
    expect(page.locator("#results-dialog")).to_be_visible()
    expect(page.locator("#history-dialog")).to_be_hidden()


def manage(page):
    open_results(page)
    expect(page.locator("#results-refresh")).to_be_enabled()


def trash(page, result):
    with page.expect_response(
        lambda response: (
            response.request.method == "DELETE"
            and urlsplit(response.url).path.endswith("/transcripts/" + result["id"])
        )
    ) as reply:
        page.locator(f'[data-trash-result="{result["id"]}"]').click()
    assert reply.value.status == 200
    expect(page.locator("#results-status")).to_have_text("Result moved to Trash.")
    expect(page.locator("#results-refresh")).to_be_enabled()
    expect(page.locator(f'[data-restore-result="{result["id"]}"]')).to_be_enabled()


def restore(page, result):
    with page.expect_response(
        lambda response: (
            response.request.method == "POST"
            and urlsplit(response.url).path.endswith("/transcripts/" + result["id"] + "/restore")
        )
    ) as reply:
        page.locator(f'[data-restore-result="{result["id"]}"]').click()
    assert reply.value.status == 200
    expect(page.locator("#results-status")).to_have_text("Result restored.")
    expect(page.locator("#results-refresh")).to_be_enabled()
    expect(page.locator(f'[data-trash-result="{result["id"]}"]')).to_be_enabled()


def test_result_trash_lifecycle_preserves_media_notes_and_history(page, local_app, wav_bytes):
    store = local_app.server.config.app.state.store
    started, events = time.monotonic(), []
    page.on("pageerror", lambda error: events.append({"error": str(error)}))
    page.on(
        "console",
        lambda message: (
            events.append({"console": message.text}) if message.type == "error" else None
        ),
    )
    page.on(
        "response",
        lambda response: (
            events.append(
                {
                    "at": round(time.monotonic() - started, 3),
                    "path": urlsplit(response.url).path,
                    "method": response.request.method,
                    "status": response.status,
                }
            )
            if "/api/" in response.url
            else None
        ),
    )
    page.goto(local_app.url)
    create_project(page, "Recoverable results")
    import_recording(page, local_app, "interview.wav", wav_bytes)
    rid = page.url.rsplit("/", 1)[-1]
    first, second = seed_results(store, rid)
    store.save_notes("recordings", rid, "Saved recording notes", 0)
    notes_before = store.all("SELECT * FROM note_versions ORDER BY id")
    first_text = store.result(rid, first["id"])["segments"]
    page.reload()
    expect(page.locator("#result-select")).to_have_value(first["id"])
    page.locator("#compare-result").select_option(second["id"])
    expect(page.locator("#comparison-panel")).to_be_visible()
    page.locator("#recording-notes").locator("..").locator("summary").click()
    page.locator("#recording-notes").fill("Retain this independent notes draft")
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 1")
    page.evaluate(
        "() => { const a = document.querySelector('#audio'); "
        "a.currentTime = 0.4; a.playbackRate = 1.25; }"
    )
    original_url = page.locator("#original-link").get_attribute("href")
    manage(page)
    expect(page.locator("#result-list .managed-result")).to_have_count(2)
    expect(page.locator("#results-dialog")).to_contain_text(
        "original recording and result history are preserved"
    )
    trash(page, second)
    expect(page.locator("#comparison-panel")).to_be_hidden()
    expect(page.locator("#compare-result")).to_have_value("")
    expect(page.locator("#result-select")).to_have_value(first["id"])
    restore(page, second)
    expect(page.locator("#result-select")).to_have_value(first["id"])
    try:
        trash(page, first)
    except Exception:
        diagnostics = page.evaluate(
            """() => ({
              epoch: viewEpoch, editVersion,
              current: state.recording?.active_transcript_id,
              revision: state.recording?.revision,
              manager: resultsContext ? {
                epoch: resultsContext.epoch, result: resultsContext.result,
                editVersion: resultsContext.editVersion,
                revision: resultsContext.recording?.revision,
                loading: resultsContext.loading
              } : null,
              pending: [...pendingResultMutations],
              status: document.querySelector('#results-status').textContent,
              error: document.querySelector('#results-error').textContent
            })"""
        )
        print(json.dumps({"events": events, "view": diagnostics}, ensure_ascii=True, indent=2))
        raise
    expect(page.locator("#result-select")).to_have_value(second["id"])
    trash(page, second)
    expect(page.locator("#result-select")).to_be_disabled()
    expect(page.locator("#result-select")).to_have_text("No transcript yet")
    expect(page.locator("#transcript .transcript-empty")).to_be_visible()
    expect(page.locator("#player")).to_be_visible()
    expect(page.locator("#recording-notes")).to_have_value("Retain this independent notes draft")
    assert page.locator("#audio").evaluate("a => a.currentTime") == pytest.approx(0.4, abs=0.1)
    assert page.locator("#audio").evaluate("a => a.playbackRate") == 1.25
    expect(page.locator("#original-link")).to_have_attribute("href", original_url)
    response = page.request.get(local_app.url + original_url)
    assert response.status == 200 and response.body() == wav_bytes
    page.locator('#results-dialog [data-close="results-dialog"]').last.click()
    manage(page)
    expect(page.locator("#result-trash-list .managed-result")).to_have_count(2)
    restore(page, first)
    expect(page.locator("#result-select")).to_have_value(first["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Original wording.")
    page.locator("#results-dialog").screenshot(path="test-results/result-trash-manager.png")
    assert store.result(rid, first["id"])["segments"] == first_text
    actions = {v["action"] for v in store.result_versions(rid, first["id"])}
    assert {"result_trashed", "result_restored"} <= actions
    saved = store.recording(rid)
    assert saved["notes"] == "Saved recording notes" and saved["notes_revision"] == 1
    assert store.all("SELECT * FROM note_versions ORDER BY id") == notes_before


def test_result_trash_preserves_other_result_drafts_and_reports_errors_in_dialog(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Result drafts")
    recording = store.create_recording(project["id"], "draft.wav")
    rid = recording["id"]
    first, second = seed_results(store, rid)
    mark_active_latest(store, project["id"])
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").fill("Keep this reviewed draft.")
    manage(page)
    trash(page, second)
    page.locator('#results-dialog [data-close="results-dialog"]').last.click()
    expect(page.locator("#passage-0 textarea")).to_have_value("Keep this reviewed draft.")
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
    rejected = []

    def reject(route):
        if route.request.method == "DELETE":
            rejected.append(route.request.post_data_json)
            route.fulfill(
                status=409,
                json={"detail": "Synthetic revision conflict; refresh this result list."},
            )
        else:
            route.continue_()

    path = f"**/api/recordings/{rid}/transcripts/{first['id']}"
    page.route(path, reject)
    manage(page)
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator(f'[data-trash-result="{first["id"]}"]').click()
    assert not rejected
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f'[data-trash-result="{first["id"]}"]').click()
    expect(page.locator("#results-error")).to_have_text(
        "Synthetic revision conflict; refresh this result list."
    )
    expect(page.locator("#results-error")).to_be_focused()
    expect(page.locator("#results-refresh")).to_be_enabled()
    assert rejected == [{"revision": store.recording(rid)["revision"]}]
    assert len(store.recording(rid)["transcripts"]) == 1
    expect(page.locator("#toast")).not_to_contain_text("Synthetic revision conflict")
    page.locator('#results-dialog [data-close="results-dialog"]').last.click()
    expect(page.locator("#passage-0 textarea")).to_have_value("Keep this reviewed draft.")
    page.locator('#passage-0 [data-action="save-edit"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    page.unroute(path, reject)
    manage(page)
    page.evaluate(
        """() => {
          const original = renderEditorDetails;
          renderEditorDetails = () => {
            renderEditorDetails = original;
            throw new Error('Synthetic result view refresh failure');
          };
        }"""
    )
    with page.expect_response(
        lambda response: (
            response.request.method == "DELETE"
            and urlsplit(response.url).path.endswith("/transcripts/" + first["id"])
        )
    ) as reply:
        page.locator(f'[data-trash-result="{first["id"]}"]').click()
    assert reply.value.status == 200
    expect(page.locator("#results-error")).to_have_text("Synthetic result view refresh failure")
    expect(page.locator("#results-status")).to_have_text(
        "The result was updated. Refresh results to reload the view."
    )
    page.locator("#results-refresh").click()
    expect(page.locator("#results-error")).to_be_hidden()
    expect(page.locator("#result-list .managed-result")).to_have_count(0)
    expect(page.locator("#transcript .transcript-empty")).to_be_visible()
    assert not store.recording(rid)["transcripts"]


@pytest.mark.parametrize("operation", ["list", "trash"])
def test_result_trash_late_responses_cannot_replace_new_recording(page, local_app, operation):
    store = local_app.server.config.app.state.store
    project = store.create_project("Late result replies")
    first_recording = store.create_recording(project["id"], "First.wav")
    second_recording = store.create_recording(project["id"], "Second.wav")
    first, second = seed_results(store, first_recording["id"])
    other = store.add_result(
        second_recording["id"],
        normalize_segments([{"text": "Second recording text."}]),
        {},
        "Second model",
        {},
    )
    rid = first_recording["id"]
    held = []
    suffix = "/trashed-transcripts" if operation == "list" else f"/transcripts/{second['id']}"

    def hold(route):
        if route.request.method == ("GET" if operation == "list" else "DELETE"):
            held.append((route, route.fetch()))
            page.evaluate("window.resultReplyHeld = true")
        else:
            route.continue_()

    page.route(f"**/api/recordings/{rid}{suffix}", hold)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    open_results(page)
    if operation == "trash":
        expect(page.locator("#results-refresh")).to_be_enabled()
        page.locator(f'[data-trash-result="{second["id"]}"]').click()
        expect(page.locator("#results-refresh")).to_be_disabled()
        expect(page.locator("#result-list [data-trash-result]:enabled")).to_have_count(0)
    page.wait_for_function("() => window.resultReplyHeld === true")
    assert held
    page.locator('#results-dialog [data-close="results-dialog"]').last.click()
    if operation == "trash":
        expect(page.locator("#results-manage")).to_be_disabled()
    page.evaluate("id => { location.hash = '/recordings/' + id; }", second_recording["id"])
    expect(page.locator("#recording-title")).to_have_text("Second")
    expect(page.locator("#results-dialog")).not_to_be_visible()
    manage(page)
    expect(page.locator("#result-list .managed-result")).to_have_count(1)
    expect(page.locator("#result-list")).to_contain_text("Second model")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#recording-title")).to_have_text("Second")
    expect(page.locator("#result-select")).to_have_value(other["id"])
    expect(page.locator("#result-list .managed-result")).to_have_count(1)
    expect(page.locator("#result-list")).not_to_contain_text("Word model")
    expect(page.locator("#results-error")).to_be_hidden()
    assert store.recording(second_recording["id"])["active_transcript_id"] == other["id"]
    assert len(store.recording(rid)["transcripts"]) == (2 if operation == "list" else 1)


def test_result_trash_finishes_in_current_view_after_manager_closes(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Closed result manager")
    recording = store.create_recording(project["id"], "Current.wav")
    rid = recording["id"]
    first, second = seed_results(store, rid)
    held = []

    def hold(route):
        if route.request.method == "DELETE":
            held.append((route, route.fetch()))
            page.evaluate("window.closedTrashReplyHeld = true")
        else:
            route.continue_()

    page.route(f"**/api/recordings/{rid}/transcripts/{first['id']}", hold)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#recording-notes").locator("..").locator("summary").click()
    page.locator("#recording-notes").fill("Notes saved during Trash request")
    manage(page)
    page.locator(f'[data-trash-result="{first["id"]}"]').click()
    page.wait_for_function("() => window.closedTrashReplyHeld === true")
    page.locator('#results-dialog [data-close="results-dialog"]').last.click()
    expect(page.locator("#results-manage")).to_be_disabled()
    page.locator("#recording-notes-save").click()
    expect(page.locator("#recording-notes-status")).to_have_text("Notes saved")
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#result-select")).to_have_value(second["id"])
    expect(page.locator("#result-select option")).to_have_count(1)
    expect(page.locator("#results-manage")).to_be_enabled()
    expect(page.locator("#results-dialog")).not_to_be_visible()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Aligned wording.")
    expect(page.locator("#recording-notes")).to_have_value("Notes saved during Trash request")
    expect(page.locator("#recording-notes")).to_have_attribute("data-revision", "1")
    assert store.recording(rid)["notes"] == "Notes saved during Trash request"


def test_result_activation_blocks_manager_and_duplicates_without_touching_newer_view(
    page, local_app
):
    store = local_app.server.config.app.state.store
    project = store.create_project("Pending result activation")
    recording = store.create_recording(project["id"], "Activation.wav")
    other_recording = store.create_recording(project["id"], "Other.wav")
    rid = recording["id"]
    first, second = seed_results(store, rid)
    other = store.add_result(
        other_recording["id"],
        normalize_segments([{"text": "Other recording."}]),
        {},
        "Other model",
        {},
    )
    held = []

    def hold(route):
        held.append((route, route.fetch()))
        page.evaluate("window.activationReplyHeld = true")

    page.route(f"**/api/recordings/{rid}/transcripts/*/activate", hold)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#compare-result").select_option(second["id"])
    expect(page.locator("#comparison-panel")).to_be_visible()
    page.locator("#result-select").select_option(second["id"])
    page.wait_for_function("() => window.activationReplyHeld === true")
    expect(page.locator("#results-manage")).to_be_disabled()
    expect(page.locator("#result-select")).to_be_disabled()
    expect(page.locator("#compare-result")).to_be_disabled()
    page.evaluate("id => reviewResult(id)", first["id"])
    page.locator("#results-manage").dispatch_event("click")
    expect(page.locator("#results-dialog")).not_to_be_visible()
    assert len(held) == 1
    route, response = held.pop()
    route.fulfill(response=response)
    expect(page.locator("#result-select")).to_have_value(second["id"])
    expect(page.locator("#result-select")).to_be_enabled()
    expect(page.locator("#results-manage")).to_be_enabled()
    expect(page.locator("#comparison-panel")).to_be_hidden()
    page.evaluate("window.activationReplyHeld = false")
    page.locator("#result-select").select_option(first["id"])
    page.wait_for_function("() => window.activationReplyHeld === true")
    page.evaluate("id => { location.hash = '/recordings/' + id; }", other_recording["id"])
    expect(page.locator("#recording-title")).to_have_text("Other")
    expect(page.locator("#results-manage")).to_be_enabled()
    route, response = held.pop()
    route.fulfill(
        status=409, json={"detail": "An older activation response must stay in its view."}
    )
    expect(page.locator("#recording-title")).to_have_text("Other")
    expect(page.locator("#toast")).not_to_contain_text("An older activation response")
    expect(page.locator("#result-select")).to_have_value(other["id"])
    expect(page.locator("#results-manage")).to_be_enabled()
    manage(page)
    expect(page.locator("#result-list")).to_contain_text("Other model")
    expect(page.locator("#results-error")).to_be_hidden()
