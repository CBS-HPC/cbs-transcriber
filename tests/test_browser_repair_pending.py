"""Historical cleanup ordering must not hide a newer selected repair's pending tag."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_cleanup_updated_history_does_not_hide_queued_or_ready_timing(page, local_app):
    store = Store(local_app.root)
    project = store.create_project("Synthetic repair order")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "God morgen.", "start": 0, "end": 4},
            ]
        ),
        {},
        "Synthetic words",
        {"capability": "words"},
    )
    jobs = [
        {
            "id": "historical",
            "recording_id": rid,
            "source_result_id": result["id"],
            "segment_id": "0",
            "realign": True,
            "state": "completed",
            "realign_ready": False,
        },
        {
            "id": "active",
            "recording_id": rid,
            "source_result_id": result["id"],
            "segment_ids": ["0"],
            "realign": True,
            "state": "queued",
            "realign_ready": False,
        },
    ]
    page.route("**/api/jobs", lambda route: route.fulfill(status=200, json=jobs))
    page.goto(local_app.url + f"/#/recordings/{rid}")
    button = page.locator("#passage-0 [data-run-repair]")
    expect(button).to_have_text("Queued")
    expect(button).to_be_disabled()
    page.evaluate("""() => {
        state.jobs[1].state = 'cancelled';
        state.jobs[1].realign_ready = true;
        refreshPassageRepairs();
    }""")
    expect(button).to_have_text("Adding")
    expect(button).to_be_disabled()
