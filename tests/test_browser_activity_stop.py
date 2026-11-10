"""Active Activity × controls stop the displayed scope and keep its progress visible."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_activity_remove import intent, route_activity

pytestmark = pytest.mark.browser


def activity_job(identifier, state="running", **fields):
    return {
        "id": identifier,
        "recording_id": "f" * 32,
        "recording_name": "Synthetic task",
        "kind": "media",
        "state": state,
        "message": "Synthetic task progress",
        "progress": 0.4,
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
        **fields,
    }


def show_activity(page, local_app):
    page.goto(local_app.url)
    page.locator("#activity-open").click()


@pytest.mark.parametrize("state", ["queued", "running"])
def test_active_stop_coalesces_and_finished_remove_only_hides(page, local_app, state):
    job = activity_job("a" * 32, state)
    jobs = [job]
    removals = route_activity(page, jobs)
    held = []
    page.route("**/api/jobs/*/cancel", lambda route: held.append(route))
    show_activity(page, local_app)
    card = page.locator("#jobs .job")
    stop = card.get_by_role("button", name="Stop preparation", exact=True)
    expect(stop).to_have_text("×")
    expect(stop).to_have_attribute("title", "Stop preparation. Progress stays in Activity.")
    expect(card.get_by_role("button", name="Remove from Activity")).to_have_count(0)
    stop.click()
    expect(stop).to_be_disabled()
    page.evaluate("async () => await pollJobs()")
    expect(stop).to_be_disabled()
    # A refreshed footer cannot issue another cancellation while the header waits.
    card.locator(".job-footer [data-cancel]").evaluate("button => button.click()")
    assert len(held) == 1
    assert held[0].request.url.endswith(f"/jobs/{job['id']}/cancel")
    assert removals == []
    job.update(state="cancelled", message="Preparation cancelled", updated="2026-01-02T00:00:00Z")
    held[0].fulfill(json={})
    expect(card).to_contain_text("Preparation cancelled")
    expect(card.locator("[data-activity-stop]")).to_have_count(0)
    card.get_by_role("button", name="Remove from Activity", exact=True).click()
    expect(card).to_have_count(0)
    assert removals == [job["id"]]
    assert len(held) == 1


def test_media_group_stop_only_cancels_unfinished_members(page, local_app):
    batch = "d" * 32
    jobs = [
        activity_job(
            f"{i:032x}",
            "completed" if i < 2 else "running" if i == 2 else "queued",
            upload_batch=batch,
            recording_name=f"File {i}",
        )
        for i in range(12)
    ]
    completed = copy.deepcopy(jobs[:2])
    removals = route_activity(page, jobs)
    requests, held = [], []

    def cancel(route):
        identifier = route.request.url.split("/")[-2]
        requests.append(identifier)
        if len(requests) == 1:
            held.append(route)
            return
        next(job for job in jobs if job["id"] == identifier)["state"] = "cancelled"
        route.fulfill(json={})

    page.route("**/api/jobs/*/cancel", cancel)
    show_activity(page, local_app)
    card = page.locator("#jobs .job[data-upload-group]")
    card.locator("summary").click()
    stop = card.get_by_role("button", name="Stop preparation (10 files)", exact=True)
    expect(stop).to_have_text("×")
    stop.click()
    page.evaluate("async () => await pollJobs()")
    expect(stop).to_be_disabled()
    card.locator(".job-footer [data-upload-cancel]").evaluate("button => button.click()")
    assert requests == [jobs[2]["id"]]
    expect(card.locator('[data-upload-count="done"]')).to_have_text("2")
    expect(card.locator("[data-batch-members]")).to_have_attribute("open", "")
    jobs[2]["state"] = "cancelled"
    held[0].fulfill(json={})
    expect(card.locator('[data-upload-count="cancelled"]')).to_have_text("10")
    expect(card.locator("li")).to_have_count(12)
    expect(card.locator("[data-activity-stop]")).to_have_count(0)
    assert requests == [job["id"] for job in jobs[2:]]
    assert jobs[:2] == completed
    assert removals == []
    page.screenshot(path="test-results/activity-stopped-media-group.png")


@pytest.mark.parametrize("scope", ["native", "runtime"])
def test_shared_allocation_stop_keeps_existing_scope(page, local_app, scope):
    batch = "b" * 32
    job = activity_job(
        "a" * 32,
        kind="transcribe",
        ucloud_batch_id=batch,
        ucloud_batch_count=4,
        ucloud_batch_state="running",
    )
    if scope == "runtime":
        job["runtime_batch"] = {"id": batch, "member_count": 4, "members": [], "can_cancel": True}
    route_activity(page, [job])
    requests = []
    page.route("**/api/jobs/*/cancel", lambda route: requests.append(route))
    page.route("**/api/runtime-batches/*/cancel", lambda route: requests.append(route))
    show_activity(page, local_app)
    card = page.locator("#jobs .job")
    stop = card.locator("[data-activity-stop]")
    expect(stop).to_have_attribute(
        "aria-label", f"Stop task: {'UCloud' if scope == 'native' else 'runtime'} batch (4 files)"
    )
    stop.click()
    page.evaluate("async () => await pollJobs()")
    expect(stop).to_be_disabled()
    card.locator(".job-footer [data-cancel], .job-footer [data-runtime-action=cancel]").evaluate(
        "button => button.click()"
    )
    assert len(requests) == 1
    endpoint = f"jobs/{job['id']}" if scope == "native" else f"runtime-batches/{batch}"
    assert requests[0].request.url.endswith(f"/{endpoint}/cancel")
    requests[0].fulfill(json={})
    expect(card).to_be_visible()


def test_request_group_stop_uses_each_rendered_frozen_token(page, local_app):
    request = "e" * 32
    jobs = [intent(f"{i:032x}", f"File {i}", request, error=None) for i in range(3)]
    jobs[2] = intent("2" * 32, "Finished file", request, error="cancelled")
    finished = copy.deepcopy(jobs[2])
    route_activity(page, jobs)
    captured, generic = [], []

    def cancel(route):
        identifier = route.request.url.split("/")[-2]
        captured.append((identifier, route.request.post_data_json))
        job = next(job for job in jobs if job["id"] == identifier)
        job.update(state="cancelled", updated="2026-01-02T00:00:00Z")
        job["input_preparation"].update(phase="terminal", can_cancel=False, error_code="cancelled")
        route.fulfill(json={})

    page.route("**/api/input-intents/*/cancel", cancel)
    page.route(
        "**/api/jobs/*/cancel",
        lambda route: (generic.append(route.request.url), route.fulfill(json={})),
    )
    show_activity(page, local_app)
    card = page.locator("#jobs .job")
    # A mutable snapshot after rendering cannot retarget the captured controls.
    page.evaluate("() => state.jobs.forEach(job => job.input_preparation.epoch = 99)")
    card.get_by_role("button", name="Stop preparation (2 files)", exact=True).click()
    expect(card.locator('[data-input-count="cancelled"]')).to_have_text("3")
    assert captured == [(job["id"], {"epoch": 2, "phase": "waiting"}) for job in jobs[:2]]
    assert jobs[2] == finished
    assert generic == []
    expect(card.get_by_role("button", name="Remove from Activity")).to_have_count(1)


def test_pending_local_cleanup_has_no_header_stop(page, local_app):
    route_activity(page, [activity_job("a" * 32, local_deletion={"state": "pending"})])
    show_activity(page, local_app)
    expect(page.locator("#jobs .job")).to_be_visible()
    expect(page.locator("#jobs [data-activity-stop], #jobs [data-activity-remove]")).to_have_count(
        0
    )
