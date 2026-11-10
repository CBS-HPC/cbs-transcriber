"""One shared Activity with canonical state, member outcomes and retained obligations."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_native_batch_is_one_activity_using_shared_state_and_cleanup(page, local_app):
    app = local_app.server.config.app
    store = app.state.store
    project = store.create_project("Synthetic batch Activity")
    recordings = [store.create_recording(project["id"], f"Sample {i}.wav") for i in range(4)]
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
        for index, job in enumerate(jobs):
            store.update_job(
                job["id"],
                state="completed" if index == 0 else "failed",
                message="Saved locally" if index == 0 else "Output needs retry",
            )
        store.update_native_batch(
            batch_id,
            state="running",
            progress=0.6,
            message="Importing batch outputs",
            run_update={"job_id": "synthetic-remote", "remote_state": "SUCCESS"},
        )
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    cards = page.locator("#jobs .job")
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Batch transcription of 4 files")
    expect(cards.locator(".job-footer > .badge")).to_have_text("running")
    expect(cards.locator("progress")).to_have_attribute("value", "0.6")
    expect(cards.get_by_role("button", name="Stop UCloud batch (4 files)")).to_be_visible()
    expect(cards.get_by_role("button", name="Retry", exact=True)).to_have_count(0)
    outcomes = cards.locator(".batch-members")
    outcomes.locator("summary").click()
    expect(outcomes.locator("li")).to_have_count(4)
    expect(outcomes.locator("li").first).to_contain_text("Saved locally")
    # Polling keeps the expanded view; the card is never driven by a completed member.
    expect(outcomes).to_have_attribute("open", "")
    store.update_native_batch(
        batch_id,
        state="completed",
        progress=1,
        message="Batch results saved",
        run_update={
            "cleanup_state": "pending",
            "cleanup_stage": "confirmation",
            "cleanup_error": "Remote folder removal could not be confirmed.",
        },
    )
    expect(page.locator('#jobs [aria-label="Needs attention"] .job')).to_have_count(1)
    expect(cards).to_contain_text("Remote cleanup is pending")
    expect(cards.get_by_role("button", name="Retry remote cleanup")).to_have_count(1)
    page.locator("#activity-clear").click()
    expect(cards).to_have_count(1)
    page.reload()
    page.locator("#activity-open").click()
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("Remote folder removal could not be confirmed")
    store.update_native_batch(
        batch_id,
        state="failed",
        message="One output needs retry",
        run_update={"cleanup_state": "trashed", "cleanup_error": None},
    )
    expect(cards).to_contain_text("moved to UCloud Trash")
    expect(cards.get_by_role("button", name="Retry", exact=True)).to_have_count(1)
    expect(cards.locator("[data-cancel]")).to_have_count(0)
    page.locator("#activity-clear").click()
    expect(cards).to_have_count(0)
    assert len(store.all("SELECT id FROM jobs")) == 4
    assert store.native_batch(batch_id)["params"]["ucloud_run"]["job_id"] == "synthetic-remote"
