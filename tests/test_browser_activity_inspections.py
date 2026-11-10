"""Original inspectors from one verified upload share a card, preserving member tokens."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_activity_stop import activity_job, show_activity

pytestmark = pytest.mark.browser


def inspector(index, batch, phase="ready", project="a" * 32):
    return activity_job(
        f"{index:032x}",
        "completed" if phase == "ready" else "running" if phase == "running" else "queued",
        recording_name=f"Original {index}",
        project_id=project,
        kind="source-inspect",
        inspection_upload_batch=batch,
        input_preparation={
            "kind": "inspection",
            "phase": phase,
            "generation": f"{index + 100:032x}",
            "epoch": 3,
            "can_cancel": phase in {"pending", "running"},
            "can_retry": False,
            "error_code": None,
        },
    )


def route_inspectors(page, jobs):
    activity = {"cleared_at": None, "hidden": {}}
    removed = []

    def hide(route):
        identifier = route.request.post_data_json["job_id"]
        selected = next(job for job in jobs if job["id"] == identifier)
        members = [
            job
            for job in jobs
            if job["project_id"] == selected["project_id"]
            and job["inspection_upload_batch"] == selected["inspection_upload_batch"]
        ]
        removed.extend(job["id"] for job in members)
        activity["hidden"].update({job["id"]: job["updated"] for job in members})
        route.fulfill(json=activity)

    page.route("**/api/jobs", lambda route: route.fulfill(json=jobs))
    page.route("**/api/activity", lambda route: route.fulfill(json=activity))
    page.route("**/api/activity/hide", hide)
    return removed


def test_completed_upload_inspectors_group_and_remove_every_member(page, local_app):
    batch = "d" * 32
    grouped = [inspector(i, batch) for i in range(12)]
    jobs = [
        *grouped,
        inspector(20, "e" * 32),
        inspector(21, batch, project="b" * 32),
        inspector(22, None),
    ]
    removed = route_inspectors(page, jobs)
    show_activity(page, local_app)
    cards = page.locator("#jobs .job")
    expect(cards).to_have_count(4)
    group = cards.filter(has_text="Original inspection — 12 files")
    expect(group).to_contain_text("All originals inspected.")
    expect(group.locator('[data-input-count="ready"]')).to_have_text("12")
    expect(group.locator("[data-activity-stop]")).to_have_count(0)
    group.locator("summary").click()
    expect(group.locator("li")).to_have_count(12)
    page.screenshot(path="test-results/activity-grouped-original-inspections.png")
    group.get_by_role("button", name="Remove from Activity", exact=True).click()
    expect(group).to_have_count(0)
    expect(cards).to_have_count(3)
    assert removed == [job["id"] for job in grouped]
    # A retry changes one member and brings its whole upload group back.
    grouped[0].update(state="queued", updated="2026-01-02T00:00:00Z")
    grouped[0]["input_preparation"].update(phase="pending", epoch=4, can_cancel=True)
    page.evaluate("async () => await pollJobs()")
    expect(group).to_have_count(1)
    expect(group.get_by_role("button", name="Remove from Activity")).to_have_count(0)
    expect(group.get_by_role("button", name="Stop inspection (1 file)", exact=True)).to_have_text(
        "×"
    )
    page.screenshot(path="test-results/activity-grouped-inspection-retry.png")


def test_grouped_inspection_stop_and_retry_keep_each_generation(page, local_app):
    jobs = [
        inspector(0, "d" * 32),
        inspector(1, "d" * 32, "running"),
        inspector(2, "d" * 32, "pending"),
    ]
    ready = copy.deepcopy(jobs[0])
    removed = route_inspectors(page, jobs)
    captured, held, generic, retries = [], [], [], []

    def settle(identifier):
        job = next(job for job in jobs if job["id"] == identifier)
        job.update(state="cancelled", updated="2026-01-02T00:00:00Z")
        job["input_preparation"].update(
            phase="error", can_cancel=False, can_retry=True, error_code="cancelled"
        )

    def cancel(route):
        identifier = route.request.url.split("/")[-2]
        captured.append((identifier, route.request.post_data_json))
        if len(captured) == 1:
            held.append(route)
        else:
            settle(identifier)
            route.fulfill(json={})

    page.route("**/api/input-inspections/*/cancel", cancel)
    page.route(
        "**/api/jobs/*/cancel",
        lambda route: (generic.append(route.request.url), route.fulfill(json={})),
    )
    page.route(
        "**/api/input-inspections/*/retry",
        lambda route: (retries.append(route.request.post_data_json), route.fulfill(json={})),
    )
    show_activity(page, local_app)
    card = page.locator("#jobs .job")
    expect(card).to_have_count(1)
    card.locator("summary").click()
    stop = card.get_by_role("button", name="Stop inspection (2 files)", exact=True)
    page.evaluate("() => state.jobs.forEach(job => job.input_preparation.epoch = 99)")
    stop.click()
    page.evaluate("async () => await pollJobs()")
    expect(stop).to_be_disabled()
    stop.evaluate("button => button.click()")
    assert len(captured) == 1
    settle(jobs[1]["id"])
    held[0].fulfill(json={})
    expect(card.locator('[data-input-count="cancelled"]')).to_have_text("2")
    expect(card.locator('[data-input-count="ready"]')).to_have_text("1")
    assert captured == [
        (job["id"], {"generation": job["input_preparation"]["generation"], "epoch": 3})
        for job in jobs[1:]
    ]
    assert jobs[0] == ready
    assert not generic and not removed
    expect(card.locator("[data-batch-members]")).to_have_attribute("open", "")
    card.locator(f'[data-input-member="{jobs[1]["id"]}"] [data-input-action="retry"]').click()
    assert retries == [{"generation": jobs[1]["input_preparation"]["generation"], "epoch": 3}]
