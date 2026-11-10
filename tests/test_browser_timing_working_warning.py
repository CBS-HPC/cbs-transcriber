"""Timing can save even when its original Working designation cannot be followed."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser

WARNING = "Working was not updated; review the saved version."
SNAPSHOT = "Saved in the library, but project outputs could not be updated."


def ready_response(page, local_app, warning):
    store = local_app.server.config.app.state.store
    project = store.create_project("Timing warning")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Hello.", "start": 0, "end": 1}]),
        {},
        "Synthetic",
        {"capability": "words"},
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Hello.")
    updated = store.recording(rid)
    updated["revision"] += 1
    updated["snapshot_error"] = SNAPSHOT
    updated["segments"] = normalize_segments(
        [
            {
                "text": "Hello.",
                "start": 0,
                "end": 1,
                "words": [{"word": "Hello.", "start": 0, "end": 1}],
            }
        ]
    )
    if warning:
        updated["working_follow_warning"] = WARNING
    job = {
        "id": "synthetic-timing",
        "recording_id": rid,
        "source_result_id": result["id"],
        "realign": True,
        "realign_ready": True,
        "state": "completed",
    }
    return updated, job


@pytest.mark.parametrize("warning", [True, False])
def test_timing_success_shows_warning_without_hiding_snapshot_problem(page, local_app, warning):
    updated, job = ready_response(page, local_app, warning)
    page.route("**/realign/synthetic-timing", lambda route: route.fulfill(status=200, json=updated))
    page.evaluate("job => { state.jobs = [job]; return applyRealignments(); }", job)
    expect(page.locator("#timing-badge")).to_have_text("Word timing")
    expect(page.locator("#save-status")).to_contain_text(SNAPSHOT)
    expect(page.locator("#toast")).to_contain_text("Word timing added")
    if warning:
        expect(page.locator("#save-status")).to_contain_text(WARNING)
        expect(page.locator("#toast")).to_contain_text(WARNING)
    else:
        expect(page.locator("#toast")).not_to_contain_text(WARNING)


def test_late_timing_warning_does_not_replace_open_draft(page, local_app):
    updated, job = ready_response(page, local_app, True)
    page.evaluate(
        """updated => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options) => String(url).endsWith('/realign/synthetic-timing')
        ? new Promise(resolve => { window.releaseTiming = () => resolve(new Response(
            JSON.stringify(updated), {headers: {'Content-Type': 'application/json'}})); })
        : original(url, options);
    }""",
        updated,
    )
    page.evaluate("job => { state.jobs = [job]; void applyRealignments(); }", job)
    page.wait_for_function("typeof window.releaseTiming === 'function'")
    page.evaluate("""() => {
      state.editing = '0';
      renderTranscript();
      const draft = document.querySelector('#passage-0 textarea');
      draft.value = 'Unfinished correction';
      draft.dispatchEvent(new Event('input', {bubbles: true}));
    }""")
    page.evaluate("window.releaseTiming()")
    expect(page.locator("#toast")).to_contain_text(WARNING)
    expect(page.locator("#toast")).to_contain_text(SNAPSHOT)
    expect(page.locator("#passage-0 textarea")).to_have_value("Unfinished correction")
    expect(page.locator("#save-status")).to_have_text("Unsaved changes")
