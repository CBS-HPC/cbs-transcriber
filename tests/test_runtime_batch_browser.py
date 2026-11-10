"""Runtime allocation cards and capacity controls use synthetic local API responses only."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_processing import runtime

pytestmark = pytest.mark.browser


def owner():
    identifier = "a" * 32
    return {
        "id": "runtime-batch-" + identifier,
        "kind": "enhance",
        "runtime_batch_id": identifier,
        "recording_id": None,
        "recording_name": "CBS Runtime batch (4 files)",
        "provider_name": "CBS Runtime",
        "model": "large-v3",
        "state": "running",
        "message": "2 of 4 files saved",
        "progress": 0.5,
        "created": "2000-01-01T00:00:00+00:00",
        "updated": "2000-01-01T00:00:00+00:00",
        "ucloud_server": "https://cloud.example.test",
        "ucloud_job_id": "owned-remote",
        "ucloud_remote_state": "RUNNING",
        "runtime_batch": {
            "id": identifier,
            "member_count": 4,
            "unresolved": True,
            "can_cancel": True,
            "can_retry": False,
            "can_cleanup": False,
            "submission_uncertain": False,
            "admission": "open",
            "members": [
                {
                    "job_id": f"file-{index}",
                    "recording_id": f"recording-{index}",
                    "recording_name": f"Sample {index}.wav",
                    "state": "completed" if index < 2 else "running",
                    "message": "Result saved locally" if index < 2 else "Aligning words",
                    "result_id": f"result-{index}" if index < 2 else None,
                }
                for index in range(4)
            ],
        },
    }


def route_activity(page, activity):
    """Retain raw file jobs in /jobs, while the independently supplied owner drives the card."""
    cleared = {"cleared_at": None}

    def jobs(route):
        members = [
            {
                "id": member["job_id"],
                "runtime_batch_id": activity["runtime_batch_id"],
                "kind": "enhance",
                **member,
            }
            for member in activity["runtime_batch"]["members"]
        ]
        route.fulfill(json=[*members, copy.deepcopy(activity)])

    def clear(route):
        cleared["cleared_at"] = "2010-01-01T00:00:00+00:00"
        route.fulfill(json=cleared)

    page.route("**/api/jobs", jobs)
    page.route("**/api/activity", lambda route: route.fulfill(json=cleared))
    page.route("**/api/activity/clear", clear)


def test_runtime_owner_is_one_activity_with_late_members_and_recovery(page, local_app):
    activity = owner()
    activity["runtime_batch"]["retryable_members"] = 1
    activity["runtime_batch"]["members"][2]["state"] = "failed"
    route_activity(page, activity)
    requests = []

    def action(route):
        requests.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data_json))
        route.fulfill(json={"scope": "runtime_batch", "batch_id": activity["runtime_batch_id"]})

    page.route("**/api/runtime-batches/*/*", action)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    cards = page.locator("#jobs .job")
    expect(cards).to_have_count(1)
    expect(cards).to_contain_text("CBS Runtime batch (4 files)")
    expect(cards.locator("progress")).to_have_attribute("value", "0.5")
    expect(page.locator("#job-count")).to_have_text("1")
    outcomes = cards.locator(".batch-members")
    outcomes.locator("summary").click()
    expect(outcomes.locator("li")).to_have_count(4)
    assert page.evaluate("state.jobs.filter(job => !job.runtime_batch).length") == 4
    activity["runtime_batch"]["members"].append(
        {
            "job_id": "file-late",
            "recording_id": "recording-late",
            "recording_name": "<Late & literal>.wav",
            "state": "queued",
            "message": "Waiting for this allocation",
        }
    )
    activity["runtime_batch"]["member_count"] = 5
    activity["recording_name"] = "CBS Runtime batch (5 files)"
    expect(outcomes.locator("li")).to_have_count(5)
    expect(outcomes).to_have_attribute("open", "")
    expect(outcomes.locator("li").last).to_contain_text("<Late & literal>.wav")
    cards.get_by_role("button", name="Retry failed files", exact=True).click()
    assert requests == [("retry", None)]
    cards.get_by_role("button", name="Stop runtime batch (5 files)").click()
    assert requests == [("retry", None), ("cancel", None)]
    activity.update(state="interrupted", message="Reconnect the same allocation")
    activity["runtime_batch"].update(can_cancel=False, can_retry=True, retryable_members=0)
    expect(cards.get_by_role("button", name="Reconnect batch", exact=True)).to_be_visible()
    cards.get_by_role("button", name="Reconnect batch", exact=True).click()
    assert requests[-1] == ("retry", None)
    expect(cards.locator("[data-cancel]")).to_have_count(0)


def test_runtime_cleanup_stays_after_clear_and_uncertain_discovery_needs_no_job_id(page, local_app):
    activity = owner()
    activity.update(
        state="completed",
        progress=1,
        message="All results saved locally",
        ucloud_remote_state="SUCCESS",
        ucloud_cleanup_state="pending",
        ucloud_cleanup_error="Removal confirmation is unavailable",
    )
    activity["runtime_batch"].update(can_cancel=False, can_cleanup=True)
    route_activity(page, activity)
    requests = []

    def action(route):
        requests.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data_json))
        route.fulfill(json={"cleanup_state": "pending"})

    page.route("**/api/runtime-batches/*/*", action)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    cards = page.locator("#jobs .job")
    expect(page.locator('#jobs [aria-label="Needs attention"] .job')).to_have_count(1)
    page.locator("#activity-clear").click()
    expect(cards).to_have_count(1)
    page.locator("#jobs [data-retained-activity] > summary").click()
    cards.get_by_role("button", name="Retry remote cleanup", exact=True).click()
    assert requests == [("cleanup", None)]
    activity.update(state="interrupted", ucloud_submission_uncertain=True)
    activity["runtime_batch"].update(submission_uncertain=True, can_cleanup=False)
    expect(cards).to_contain_text("CBS Runtime batch " + activity["runtime_batch_id"])
    cards.get_by_role("button", name="Find UCloud job", exact=True).click()
    assert requests[-1] == ("reconcile", {})
    page.once("dialog", lambda dialog: dialog.accept())
    cards.get_by_role("button", name="No job created", exact=True).click()
    assert requests[-1] == ("reconcile", {"not_created": True})
    activity.update(
        state="completed", ucloud_submission_uncertain=False, ucloud_cleanup_state="trashed"
    )
    activity["runtime_batch"].update(submission_uncertain=False, unresolved=False)
    # Its earlier Clear now applies as soon as the outstanding obligation is resolved.
    expect(cards).to_have_count(0)


def test_running_runtime_submission_does_not_offer_inactive_reconciliation(page, local_app):
    activity = owner()
    activity.update(ucloud_job_id=None, ucloud_remote_state=None, ucloud_submission_uncertain=True)
    activity["runtime_batch"]["submission_uncertain"] = True
    route_activity(page, activity)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_contain_text("CBS Runtime batch " + activity["runtime_batch_id"])
    expect(card.get_by_role("button", name="Find UCloud job", exact=True)).to_have_count(0)
    expect(card.get_by_role("button", name="No job created", exact=True)).to_have_count(0)
    activity.update(state="interrupted", message="Allocation submission needs reconciliation")
    activity["runtime_batch"]["can_cancel"] = False
    expect(card.get_by_role("button", name="Find UCloud job", exact=True)).to_be_visible()
    expect(card.get_by_role("button", name="No job created", exact=True)).to_be_visible()


def test_native_stop_reports_shared_runtime_enhancements_continue(page, local_app, monkeypatch):
    from cbs_transcriber import runtime_batches

    app = local_app.server.config.app
    store = app.state.store
    project = store.create_project("Native stop with shared enhancement")
    recordings = [store.create_recording(project["id"], f"Sample {i}.wav") for i in range(2)]
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
            store.update_job(job["id"], state="completed", message="Native result saved")
        store.update_native_batch(
            batch_id,
            state="running",
            run_update={"job_id": "synthetic-native", "remote_state": "RUNNING"},
        )
    runtime_owner = owner()
    monkeypatch.setattr(runtime_batches, "activity", lambda *_: [copy.deepcopy(runtime_owner)])
    native_stops, runtime_stops = [], []

    def stop_native(route):
        native_stops.append(route.request.url)
        store.update_native_batch(
            batch_id, state="cancelled", run_update={"remote_state": "CANCELLED"}
        )
        route.fulfill(
            json={"ok": True, "continued_runtime_batches": [runtime_owner["runtime_batch_id"]]}
        )

    def stop_runtime(route):
        runtime_stops.append(route.request.url)
        route.fulfill(json={"ok": True})

    page.route("**/api/jobs/*/cancel", stop_native)
    page.route("**/api/runtime-batches/*/cancel", stop_runtime)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    page.get_by_role("button", name="Stop UCloud batch (2 files)", exact=True).click()
    expect(page.locator("#toast")).to_contain_text(
        "Native batch stopped. Enhancements shared with other files continue in Activity."
    )
    runtime_card = page.locator(
        f'#jobs [data-activity-batch="{runtime_owner["runtime_batch_id"]}"]'
    )
    expect(runtime_card.locator(".job-footer > .badge")).to_have_text("running")
    assert len(native_stops) == 1
    assert runtime_stops == []


def test_ucloud_capacity_is_advanced_and_saves_only_its_setting(page, local_app):
    capacity = {"max_allocations": 2}
    requests = []
    reject = {"save": False}

    def settings(route):
        if route.request.method == "PUT":
            requests.append(route.request.post_data_json)
            if reject["save"]:
                route.fulfill(status=503, json={"detail": "The library is temporarily busy."})
                return
            capacity.update(route.request.post_data_json)
        route.fulfill(json=capacity)

    page.route("**/api/jobs", lambda route: route.fulfill(json=[]))
    page.route("**/api/runtime-batches/settings", settings)
    page.route("**/api/local-runtime", lambda route: route.fulfill(json={"status": runtime()}))
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    limit = page.locator("#runtime-max-allocations")
    expect(limit).to_be_hidden()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    page.locator("#provider-advanced > summary").click()
    expect(limit).to_be_enabled()
    expect(limit).to_have_value("2")
    device_before = page.locator("#enhance-device").input_value()
    limit.fill("3")
    # Enter saves this field through blur/change and never submits the provider form.
    limit.press("Enter")
    expect(page.locator("#runtime-capacity-status")).to_have_text(
        "Saved. Active allocations keep running."
    )
    assert requests == [{"max_allocations": 3}]
    expect(page.locator("#enhance-device")).to_have_value(device_before)
    limit.fill("5")
    limit.press("Tab")
    expect(page.locator("#runtime-capacity-status")).to_have_text(
        "Choose a whole number from 1 to 4."
    )
    assert requests == [{"max_allocations": 3}]
    reject["save"] = True
    limit.fill("4")
    limit.press("Tab")
    expect(page.locator("#runtime-capacity-status")).to_have_text(
        "The library is temporarily busy."
    )
    expect(limit).to_have_value("3")
    expect(limit).to_be_enabled()
    assert requests == [{"max_allocations": 3}, {"max_allocations": 4}]
    page.locator("#settings-dialog").screenshot(path="test-results/runtime-capacity.png")
