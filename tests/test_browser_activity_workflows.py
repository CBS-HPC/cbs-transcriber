"""Ordinary request cards retain exact stages; allocation cards retain their owner scope."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_activity_inspections import inspector
from test_browser_activity_remove import intent
from test_browser_activity_stop import activity_job, show_activity
from test_runtime_batch_browser import owner

pytestmark = pytest.mark.browser


def request_job(index, *, recording=None, request="d" * 32, state="queued", preparing=False):
    row = (
        intent(f"{index:032x}", f"File {recording or index}", request, error=None)
        if preparing
        else activity_job(f"{index:032x}", state, kind="transcribe")
    )
    row.update(
        project_id="a" * 32,
        recording_id=f"{recording or index:032x}",
        recording_name=f"File {recording or index}",
        activity_request_id=request,
        activity_parent_job_id=None,
        activity_membership_complete=True,
        provider_name="Synthetic API",
        model=f"model-{index}",
    )
    return row


def route_jobs(page, jobs):
    page.route("**/api/jobs", lambda route: route.fulfill(json=jobs))
    page.route(
        "**/api/activity", lambda route: route.fulfill(json={"cleared_at": None, "hidden": {}})
    )


def test_long_recording_name_does_not_compress_batch_status(page, local_app):
    page.set_viewport_size({"width": 390, "height": 800})
    jobs = [request_job(1, state="running"), request_job(2, state="running")]
    jobs[0]["recording_name"] = "Long recording " + "unbroken-name-" * 12
    route_jobs(page, jobs)
    show_activity(page, local_app)
    card = page.locator("#jobs [data-workflow-request]")
    card.locator("summary").click()
    row = card.locator("[data-workflow-member]").first
    label = row.locator(".badge")
    expect(label).to_have_text("running")
    assert label.evaluate("element => element.scrollWidth <= element.clientWidth")
    assert label.evaluate("element => getComputedStyle(element).whiteSpace") == "nowrap"
    assert label.bounding_box()["width"] > 35


def test_batch_owned_inspections_render_inside_workflow_without_hiding_errors(page, local_app):
    consumers = [request_job(1, preparing=True), request_job(2, preparing=True)]
    inspections = [inspector(101, None, "running"), inspector(102, None)]
    for consumer, inspection in zip(consumers, inspections, strict=True):
        inspection["recording_id"] = consumer["recording_id"]
        consumer["activity_inspection"] = {
            "job_id": inspection["id"],
            "generation": inspection["input_preparation"]["generation"],
            "request_id": consumer["activity_request_id"],
            "shared": False,
        }
    jobs = [*inspections, *consumers]
    route_jobs(page, jobs)
    show_activity(page, local_app)
    card = page.locator("#jobs [data-workflow-request]")
    expect(page.locator("#jobs .job")).to_have_count(1)
    card.locator("summary").click()
    expect(card).to_contain_text("Original inspection: running.")
    expect(card).to_contain_text("Original inspection: complete.")
    # An inspection error stays independently actionable, even when its batch exists.
    inspections[0].update(state="failed", message="Synthetic inspection failure")
    inspections[0]["input_preparation"].update(
        phase="error", error_code="decode_failed", can_cancel=False, can_retry=True
    )
    page.evaluate("async () => await pollJobs()")
    expect(page.locator("#jobs .job")).to_have_count(2)
    expect(page.locator('#jobs [data-input-preparation="inspection"]')).to_contain_text(
        "Synthetic inspection failure"
    )


def test_inspections_stay_inside_runtime_allocation_after_handoff(page, local_app):
    batch = owner()
    members = batch["runtime_batch"]["members"][:2]
    batch["runtime_batch"].update(member_count=2, members=members)
    batch["recording_name"] = "CBS Runtime batch (2 files)"
    consumers = [request_job(i + 1, state="running") for i in range(2)]
    inspections = [inspector(i + 101, None) for i in range(2)]
    for consumer, inspection, member in zip(consumers, inspections, members, strict=True):
        inspection["recording_id"] = consumer["recording_id"]
        consumer.update(
            runtime_batch_id=batch["runtime_batch_id"],
            activity_inspection={
                "job_id": inspection["id"],
                "generation": inspection["input_preparation"]["generation"],
                "request_id": consumer["activity_request_id"],
                "shared": False,
            },
        )
        member.update(
            job_id=consumer["id"],
            recording_id=consumer["recording_id"],
            recording_name=consumer["recording_name"],
        )
    jobs = [*inspections, *consumers, batch]
    route_jobs(page, jobs)
    show_activity(page, local_app)
    card = page.locator("#jobs [data-activity-batch]")
    expect(page.locator("#jobs .job")).to_have_count(1)
    card.locator("summary").click()
    expect(card.get_by_text("Original inspection: complete.")).to_have_count(2)


def test_inspection_shared_between_requests_remains_separately_actionable(page, local_app):
    consumers = [request_job(i + 1, preparing=True) for i in range(4)]
    inspections = [inspector(i + 101, None, "running") for i in range(3)]
    consumers[2]["activity_request_id"] = consumers[3]["activity_request_id"] = "e" * 32
    consumers[2]["recording_id"] = consumers[0]["recording_id"]
    for consumer, inspection in zip(
        consumers, [inspections[0], inspections[1], inspections[0], inspections[2]], strict=True
    ):
        inspection["recording_id"] = consumer["recording_id"]
        consumer["activity_inspection"] = {
            "job_id": inspection["id"],
            "generation": inspection["input_preparation"]["generation"],
            "request_id": consumer["activity_request_id"],
            "shared": False,
        }
    route_jobs(page, [*inspections, *consumers])
    show_activity(page, local_app)
    expect(page.locator("#jobs [data-workflow-request]")).to_have_count(2)
    shared = page.locator('#jobs [data-input-preparation="inspection"]')
    expect(shared).to_have_count(1)
    expect(shared).to_contain_text("Original 101")


def test_partial_handoff_and_automatic_followups_keep_one_request_and_exact_stop(page, local_app):
    jobs = [request_job(1, preparing=True), request_job(2, preparing=True)]
    route_jobs(page, jobs)
    show_activity(page, local_app)
    card = page.locator("#jobs [data-workflow-request]")
    expect(card).to_have_count(1)
    expect(card.locator('[data-workflow-count="files"]')).to_have_text("2")
    jobs[0].pop("input_preparation")
    jobs[0].update(kind="transcribe", state="running")
    page.evaluate("async () => await pollJobs()")
    expect(card).to_have_count(1)
    expect(page.locator("#jobs .job")).to_have_count(1)
    card.locator("summary").click()
    frozen = copy.deepcopy(jobs[1]["input_preparation"])
    controls = []

    def cancel(route):
        identifier = route.request.url.split("/")[-2]
        controls.append((identifier, route.request.post_data_json))
        job = next(job for job in jobs if job["id"] == identifier)
        job.update(state="cancelled", updated="2026-01-02T00:00:00Z")
        if job.get("input_preparation"):
            job["input_preparation"].update(
                phase="terminal", can_cancel=False, error_code="cancelled"
            )
        route.fulfill(json={})

    page.route("**/api/jobs/*/cancel", cancel)
    page.route("**/api/input-intents/*/cancel", cancel)
    card.get_by_role("button", name="Stop workflow (2 tasks)", exact=True).click()
    expect(card.get_by_role("button", name="Stop workflow (2 tasks)", exact=True)).to_have_count(0)
    assert controls == [
        (jobs[0]["id"], None),
        (jobs[1]["id"], {"epoch": frozen["epoch"], "phase": frozen["phase"]}),
    ]
    # A later materialized outcome and its atomic children keep the original request.
    for job in jobs:
        job.pop("input_preparation", None)
        job.update(state="completed", progress=1)
    children = [request_job(3, recording=1), request_job(4, recording=2)]
    for child, parent in zip(children, jobs, strict=True):
        child.update(kind="enhance", activity_parent_job_id=parent["id"])
    jobs.extend(children)
    page.evaluate("async () => await pollJobs()")
    expect(page.locator("#jobs .job")).to_have_count(1)
    expect(card.locator('[data-workflow-count="files"]')).to_have_text("2")
    expect(card.locator('[data-workflow-count="requests"]')).to_have_text("2")
    expect(card.locator('[data-workflow-count="stages"]')).to_have_text("4")
    expect(card.get_by_role("button", name="Stop workflow (2 tasks)", exact=True)).to_have_count(1)


def test_multimodel_counts_files_once_and_keeps_distinct_requests(page, local_app):
    jobs = [request_job(i + 1, recording=i // 2 + 1, preparing=True) for i in range(4)]
    jobs.extend(
        [
            request_job(10, recording=1, request="e" * 32),
            request_job(11, recording=2, request="e" * 32),
        ]
    )
    route_jobs(page, jobs)
    show_activity(page, local_app)
    cards = page.locator("#jobs [data-workflow-request]")
    expect(cards).to_have_count(2)
    first = cards.filter(has_text="4 model requests")
    expect(first.locator('[data-workflow-count="files"]')).to_have_text("2")
    expect(first.locator('[data-workflow-count="requests"]')).to_have_text("4")
    # Allocation identity takes precedence even when a request ID is present.
    projected = page.evaluate(
        """jobs => activityJobs(jobs.map(({input_preparation, ...job}, i) => ({...job,
        runtime_batch_id:i < 2 ? 'b'.repeat(32) : null,
        ucloud_batch_id:i < 2 ? null : 'c'.repeat(32),
        ucloud_batch_count:2,ucloud_batch_members:[],ucloud_batch_state:'running'
    }))).map(job=>({group:job.activity_group?.key,owner:job.ucloud_batch_id}))""",
        jobs[:4],
    )
    assert len(projected) == 1 and projected[0]["owner"] == "c" * 32
    assert projected[0]["group"] is None
    # A newer authoritative owner must suppress an older same-ID intent row.
    handoff = page.evaluate(
        """jobs => activityJobs([...jobs.slice(0,2), {
            id:'runtime-batch-'+'b'.repeat(32),runtime_batch:{id:'b'.repeat(32),
            member_count:1,members:[{job_id:jobs[0].id}],unresolved:true}
        }]).map(job=>job.id)""",
        jobs,
    )
    assert jobs[0]["id"] not in handoff
    assert handoff == [jobs[1]["id"], "runtime-batch-" + "b" * 32]


def test_incomplete_membership_retains_individual_controls_and_survives_clear(page, local_app):
    jobs = [request_job(i + 1) for i in range(2)]
    for job in jobs:
        job["activity_membership_complete"] = False
    route_jobs(page, jobs)
    page.route(
        "**/api/activity/clear",
        lambda route: route.fulfill(json={"cleared_at": "2026-06-01T00:00:00Z"}),
    )
    show_activity(page, local_app)
    card = page.locator("#jobs [data-workflow-request]")
    expect(card.locator('[role="alert"]')).to_contain_text("membership is incomplete")
    expect(card.locator("[data-activity-stop], [data-activity-remove]")).to_have_count(0)
    card.locator("summary").click()
    expect(card.locator("[data-cancel]")).to_have_count(2)
    for job in jobs:
        job.update(state="failed", updated="2026-01-02T00:00:00Z")
    page.evaluate("async () => await pollJobs()")
    page.locator("#activity-clear").click()
    expect(page.locator("#jobs [data-retained-activity]")).to_have_count(1)
    expect(card).to_have_count(1)
    expect(card.locator("[data-activity-remove]")).to_have_count(0)


@pytest.mark.parametrize("phase", ["running", "cleanup", "uncertain"])
def test_grouped_remote_followup_keeps_visible_details_and_exact_controls(page, local_app, phase):
    parent = request_job(1, recording=1, state="completed")
    child = request_job(
        2,
        recording=1,
        state={
            "running": "running",
            "cleanup": "completed",
            "uncertain": "interrupted",
        }[phase],
    )
    child.update(
        kind="enhance",
        activity_parent_job_id=parent["id"],
        execution="ucloud",
        provider_name="UCloud WhisperX enhancement",
        ucloud_server="https://cloud.invalid",
        ucloud_job_id=None if phase == "uncertain" else "synthetic-remote",
        ucloud_remote_state="RUNNING" if phase == "running" else "SUCCESS",
        ucloud_submission_uncertain=phase == "uncertain",
        ucloud_cleanup_state="pending" if phase == "cleanup" else None,
        ucloud_cleanup_error="Synthetic folder still listed" if phase == "cleanup" else None,
        routing_reason="Recording length selects UCloud",
        source_changed=True,
    )
    jobs, captured = [parent, child], []
    route_jobs(page, jobs)
    show_activity(page, local_app)
    card = page.locator("#jobs [data-workflow-request]")
    expect(card).to_have_count(1)
    expect(card.locator("[data-batch-members]")).not_to_have_attribute("open", "")
    remote = card.locator(f'[data-workflow-remote="{child["id"]}"]')
    expect(remote).to_be_visible()
    expect(remote).to_contain_text("Automatic: Recording length selects UCloud")
    expect(remote).to_contain_text("The source transcript was edited after this was queued")
    if phase != "uncertain":
        expect(remote.get_by_role("link", name="Open UCloud job ↗")).to_have_attribute(
            "href", "https://cloud.invalid/app/jobs/properties/synthetic-remote"
        )
    if phase == "running":

        def stop(route):
            captured.append(route.request.url)
            child.update(state="cancelled", ucloud_stop_requested=True)
            route.fulfill(json={})

        page.route("**/api/jobs/*/cancel", stop)
        remote.get_by_role("button", name="Stop UCloud job", exact=True).click()
        expect(remote.get_by_role("button", name="Stop UCloud job", exact=True)).to_have_count(0)
        assert captured == [local_app.url + f"/api/jobs/{child['id']}/cancel"]
    elif phase == "cleanup":

        def cleanup(route):
            captured.append(route.request.url)
            child.update(ucloud_cleanup_state="trashed")
            route.fulfill(json={"cleanup_state": "trashed"})

        page.route("**/api/jobs/*/cleanup-ucloud", cleanup)
        expect(remote).to_contain_text("Synthetic folder still listed")
        expect(card.get_by_role("button", name="Remove from Activity")).to_have_count(0)
        remote.get_by_role("button", name="Retry remote cleanup", exact=True).click()
        expect(remote).to_contain_text("UCloud run files moved to UCloud Trash")
        assert captured == [local_app.url + f"/api/jobs/{child['id']}/cleanup-ucloud"]
    else:

        def reconnect(route):
            captured.append((route.request.url, route.request.post_data_json))
            child.update(ucloud_submission_uncertain=False, ucloud_job_id="reconnected")
            route.fulfill(json={})

        page.route("**/api/jobs/*/reconcile", reconnect)
        expect(remote).to_contain_text("CBS enhancement " + child["id"])
        expect(remote.get_by_role("button", name="No job created", exact=True)).to_be_visible()
        page.once("dialog", lambda dialog: dialog.accept("reconnected"))
        remote.get_by_role("button", name="Reconnect UCloud job", exact=True).click()
        expect(remote.get_by_role("link", name="Open UCloud job ↗")).to_have_attribute(
            "href", "https://cloud.invalid/app/jobs/properties/reconnected"
        )
        assert captured == [
            (local_app.url + f"/api/jobs/{child['id']}/reconcile", {"remote_id": "reconnected"})
        ]
    # Expanding outcomes must not duplicate remote controls or links.
    card.locator("summary").click()
    expect(card.get_by_role("link", name="Open UCloud job ↗")).to_have_count(1)
