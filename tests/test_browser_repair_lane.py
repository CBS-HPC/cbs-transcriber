"""A running UCloud enhancement does not hold up local passage repairs (ADR 0049)."""

import re
import threading
import time

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement, media
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_passage_repair_queues_during_a_ucloud_run(page, local_app, monkeypatch):
    store = local_app.server.config.app.state.store
    project = store.create_project("Repair lane")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": "Hej.",
                    "start": 0,
                    "end": 1,
                    "words": [{"word": "Hej", "start": 0, "end": 1}],
                },
                {"text": "God morgen.", "start": 1, "end": 3},
            ]
        ),
        {},
        "Word model",
        {"capability": "words"},
    )
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    release, running = threading.Event(), []

    def hold(queue, job, recording):
        running.append(job["id"])
        while not (release.is_set() or queue.cancel_event.is_set()):
            time.sleep(0.02)
        raise media.Cancelled()

    monkeypatch.setattr(enhancement, "run_enhancement", hold)
    try:
        remote = store.add_job(rid, "enhance", {"enhancement": {"execution": "ucloud"}})
        local_app.server.config.app.state.queue.notify()
        deadline = time.monotonic() + 10
        while remote["id"] not in running and time.monotonic() < deadline:
            time.sleep(0.05)
        assert remote["id"] in running
        page.goto(local_app.url + f"/#/recordings/{rid}")
        button = page.locator("#passage-1 [data-run-repair]")
        expect(button).to_have_text("Add word timing")
        expect(button).to_be_enabled()
        with page.expect_response("**/passage-timing") as response:
            button.click()
        assert response.value.status == 202
        expect(page.locator("#toast")).to_contain_text("Adding word timing to this passage")
        repair = store.all(
            "SELECT params FROM jobs WHERE kind='enhance' AND id!=?", (remote["id"],)
        )
        assert len(repair) == 1 and '"execution": "local"' in repair[0]["params"]
        # This passage shows its own job; the UCloud run keeps going.
        expect(button).to_have_text(re.compile("Queued|Aligning"))
        expect(button).to_be_disabled()
        assert store.job(remote["id"])["state"] == "running"
    finally:
        release.set()
