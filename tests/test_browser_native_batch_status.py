"""Plain native batch status in Activity (ADR 0183, V229); synthetic rows, no UCloud."""

from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_v229_activity_shows_plain_queue_and_running_status_without_raw_states(page, local_app):
    app = local_app.server.config.app
    store = app.state.store
    project = store.create_project("Synthetic plain status")
    recordings = [store.create_recording(project["id"], f"Sample {i}.wav") for i in range(3)]
    with app.state.queue.lock:
        jobs = store.add_transcription_jobs(
            [
                {
                    "recording_id": recording["id"],
                    "params": {
                        "provider_id": "synthetic",
                        "provider_name": "UCloud whisper/large-v3 (Transcriber)",
                        "settings": {
                            "provider_type": "ucloud",
                            "base_url": "https://cloud.example.test",
                            "model": "large-v3",
                        },
                    },
                }
                for recording in recordings
            ]
        )
        batch_id = jobs[0]["params"]["native_batch_id"]
        for job in jobs:
            store.update_job(job["id"], state="running", message="Waiting in UCloud's queue")
        since = (datetime.now(UTC) - timedelta(minutes=12, seconds=5)).isoformat()
        store.update_native_batch(
            batch_id,
            state="running",
            progress=0.3,
            message="Waiting in UCloud's queue for a GPU",
            run_update={
                "job_id": "synthetic-remote",
                "remote_state": "IN_QUEUE",
                "remote_state_since": {"state": "IN_QUEUE", "observed_at": since},
            },
        )
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator(f'#jobs [data-activity-batch="{batch_id}"]')
    expect(card).to_contain_text("Waiting in UCloud's queue for a GPU · 12 min")
    expect(card).not_to_contain_text("IN_QUEUE")
    observed = datetime.now(UTC).isoformat()
    store.update_native_batch(
        batch_id,
        message="Transcribing on UCloud",
        run_update={
            "remote_state": "RUNNING",
            "remote_state_since": {"state": "RUNNING", "observed_at": observed},
            "log_progress": {
                "files": {
                    jobs[0]["id"]: {
                        "stage": "processed_reported",
                        "reported_at": "2026-11-19 13:05:00,000",
                        "observed_at": observed,
                        "event_sha256": "a" * 64,
                    }
                },
                "batch": None,
            },
        },
    )
    clock = datetime.fromisoformat(observed).astimezone().strftime("%H:%M")
    expect(card).to_contain_text(
        f"Transcribing on UCloud · 1 of 3 files reported done · last log {clock}"
    )
    expect(card).not_to_contain_text("RUNNING")
    expect(card).not_to_contain_text("timezone")
    card.locator(".batch-members summary").click()
    first = card.locator(".batch-members li").first
    expect(first).to_contain_text(f"UCloud log: finished {clock} · transcript not saved yet")
