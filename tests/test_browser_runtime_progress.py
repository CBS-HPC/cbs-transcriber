"""One long inference task and accepted waiters have separate batch/file progress."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_runtime_batch_browser import route_activity
from test_runtime_store import enqueue, outputs
from test_runtime_store import library as library

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_batches, runtime_store
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_runtime_card_shows_saved_counts_and_actual_long_file_stage(page, local_app, library):
    store, _, _ = library
    jobs = [enqueue(library) for _ in range(5)]
    batch = runtime_store.claim(store)
    for index, member in enumerate(batch["members"]):
        store.execute(
            "UPDATE recordings SET name=? WHERE id=?",
            (
                "Synthetic 75-minute interview" if index == 0 else f"Sample {index}",
                member["recording_id"],
            ),
        )
        runtime_store.checkpoint_attempt(
            store,
            batch["id"],
            member["job_id"],
            batch["epoch"],
            member["task_id"],
            state="running" if index == 0 else "accepted",
            member_state="running" if index == 0 else "accepted",
            remote_update={"accepted": True},
        )
        store.update_job(member["job_id"], message=api.STAGES["transcribing"], progress=0.18)
    activity = runtime_batches.activity(store)[0]
    activity["progress"] = 0.18  # An old pipeline weight must never become recognition progress.
    route_activity(page, activity)
    requests = []
    page.route(
        "**/api/runtime-batches/*/*",
        lambda route: (
            requests.append(route.request.url),
            route.fulfill(json={"scope": "runtime_batch"}),
        ),
    )
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_have_count(1)
    expect(card.locator('[data-runtime-count="total"]')).to_have_text("5")
    expect(card.locator('[data-runtime-count="completed"]')).to_have_text("0")
    expect(card.locator('[data-runtime-count="waiting"]')).to_have_text("4")
    expect(card.locator(".runtime-active-file")).to_have_count(1)
    expect(card.locator(".runtime-active-file")).to_contain_text("Synthetic 75-minute interview")
    expect(card.locator(".runtime-active-file")).to_contain_text("Transcribing with WhisperX")
    expect(card).to_contain_text("Speech-recognition percentage is not reported.")
    progress = card.get_by_role("progressbar", name="Results saved locally")
    expect(progress).to_have_attribute("value", "0")
    expect(progress).to_have_attribute("max", "5")
    expect(card).not_to_contain_text("18%")
    outcomes = card.locator(".batch-members")
    outcomes.locator("summary").click()
    expect(outcomes.get_by_text("Waiting for inference", exact=True)).to_have_count(4)
    page.screenshot(path="test-results/runtime-progress-desktop.png")
    # The atomic local result receipt advances the batch, even with an old owner weight.
    receipt = outputs(store, jobs[0], runtime_store.get(store, batch["id"]))
    store.add_result(
        jobs[0]["recording_id"],
        normalize_segments([{"text": "Synthetic completed wording", "start": 0, "end": 3}]),
        {},
        "CBS Runtime",
        {},
        job_id=jobs[0]["id"],
        runtime_receipt=receipt,
    )
    activity.update(copy.deepcopy(runtime_batches.activity(store)[0]))
    expect(card.locator('[data-runtime-count="completed"]')).to_have_text("1")
    expect(progress).to_have_attribute("value", "1")
    expect(card.locator(".runtime-active-file")).to_have_count(0)
    expect(outcomes).to_have_attribute("open", "")
    card.get_by_role("button", name="Stop runtime batch (5 files)").click()
    assert requests == [local_app.url + f"/api/runtime-batches/{batch['id']}/cancel"]
    page.set_viewport_size({"width": 760, "height": 900})
    assert card.evaluate("e => e.scrollWidth <= e.clientWidth")
    page.screenshot(path="test-results/runtime-progress-small.png")


def test_cancelled_runtime_files_show_receipt_reason_without_asr_failure(page, local_app, library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    member = batch["members"][0]
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        batch["epoch"],
        member["task_id"],
        state="cancelled",
        member_state="cancelled",
        error_code="processing",
        remote_update={"error_code": "stopped", "outcome": "interrupted", "worker_gone": True},
    )
    runtime_store.checkpoint(store, batch["id"], batch["epoch"], state="cancelled")
    activity = runtime_batches.activity(store)[0]
    route_activity(page, activity)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card.locator('[data-runtime-count="cancelled"]')).to_have_text("1")
    expect(card.locator('[data-runtime-count="failed"]')).to_have_text("0")
    card.locator(".batch-members summary").click()
    expect(card.locator(".batch-members")).to_contain_text("Cancelled")
    expect(card.locator(".batch-members")).to_contain_text("Stopped before completion.")
    expect(card.locator(".batch-members")).not_to_contain_text("Processing did not finish")
    expect(card.locator(".runtime-active-file")).to_have_count(0)
