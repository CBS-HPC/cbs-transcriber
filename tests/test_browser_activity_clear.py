"""Clear finished hides finished jobs that need no action; nothing is deleted (V105)."""

import json
import uuid

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.store import now

pytestmark = pytest.mark.browser


def finished_job(store, recording_id, kind, state, message, params=None):
    # Inserted directly as finished, so the running local workers never claim them.
    identifier = uuid.uuid4().hex
    stamp = now()
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,progress,message,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (identifier, recording_id, kind, state, 1, message, json.dumps(params or {}), stamp, stamp),
    )
    return identifier


def test_clear_finished_keeps_jobs_that_need_attention(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Activity clearing")
    rid = store.create_recording(project["id"], "a.wav")["id"]
    ucloud = {"provider_type": "ucloud", "base_url": "https://cloud.example.org"}
    finished_job(store, rid, "media", "completed", "Converted for playback")
    finished_job(store, rid, "transcribe", "failed", "Provider refused the audio")
    cleanup = finished_job(
        store,
        rid,
        "transcribe",
        "completed",
        "Transcript saved",
        {"settings": ucloud, "ucloud_run": {"cleanup_state": "pending"}},
    )
    uncertain = finished_job(
        store,
        rid,
        "transcribe",
        "interrupted",
        "Submission not confirmed",
        {"settings": ucloud, "ucloud_run": {"submission_attempted": True}},
    )

    page.goto(local_app.url)
    page.locator("#activity-open").click()
    jobs = page.locator("#jobs .job")
    expect(jobs).to_have_count(4)

    with page.expect_response("**/api/activity/clear") as response:
        page.locator("#activity-clear").click()
    assert response.value.ok
    expect(jobs).to_have_count(2)
    expect(jobs.filter(has_text="Converted for playback")).to_have_count(0)
    expect(jobs.filter(has_text="Provider refused the audio")).to_have_count(0)
    expect(jobs.filter(has_text="Transcript saved")).to_contain_text("Retry remote cleanup")
    expect(jobs.filter(has_text="Submission not confirmed")).to_contain_text("Reconnect UCloud job")
    expect(page.locator("#activity-clear")).to_be_enabled()
    # Nothing is deleted; the hidden jobs stay in the library.
    assert store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 4

    finished_job(store, rid, "media", "completed", "Converted again")
    expect(jobs.filter(has_text="Converted again")).to_have_count(1)
    expect(jobs).to_have_count(3)

    # The cleared time belongs to the library, so a reload keeps the list short.
    page.reload()
    page.locator("#activity-open").click()
    expect(jobs).to_have_count(3)
    expect(jobs.filter(has_text="Converted for playback")).to_have_count(0)

    # Resolving the attention items makes them ordinary finished jobs that can be cleared.
    for identifier, update in (
        (cleanup, {"cleanup_state": "trashed"}),
        (uncertain, {"reconciled_not_created": True}),
    ):
        params = store.job(identifier)["params"]
        params["ucloud_run"].update(update)
        store.update_job(identifier, params=params)
    page.locator("#activity-clear").click()
    expect(jobs).to_have_count(0)
    expect(page.locator("#jobs .empty-jobs")).to_have_text(
        "Finished jobs cleared. New jobs will appear here."
    )
    assert store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 5


def test_clear_finished_collapses_old_cleanup_cards_and_preserves_actions(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Retained cleanup activity")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    old = [
        finished_job(
            store,
            rid,
            "transcribe",
            "completed",
            "Transcript saved locally",
            {
                "settings": {"provider_type": "ucloud", "base_url": "https://cloud.invalid"},
                "ucloud_run": {
                    "job_id": f"synthetic-{index}",
                    "remote_state": "SUCCESS",
                    "cleanup_state": "pending",
                },
            },
        )
        for index in range(3)
    ]
    finished_job(store, rid, "media", "running", "Synthetic active preparation")
    before = {identifier: store.job(identifier)["params"] for identifier in old}
    calls = []

    def cleanup(route):
        calls.append(route.request.url)
        route.fulfill(json={"cleanup_state": "pending"})

    page.route("**/api/jobs/*/cleanup-ucloud", cleanup)
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    visible_cards = page.locator("#jobs .job:visible")
    expect(visible_cards).to_have_count(4)
    page.locator("#activity-clear").click()
    expect(visible_cards).to_have_count(1)
    retained = page.locator("#jobs [data-retained-activity]")
    expect(retained.locator("summary")).to_have_text("3 earlier jobs need attention")
    expect(retained).not_to_have_attribute("open", "")
    finished_job(store, rid, "media", "completed", "New preparation completed")
    expect(visible_cards).to_have_count(2)
    page.reload()
    page.locator("#activity-open").click()
    expect(visible_cards).to_have_count(2)
    expect(retained).not_to_have_attribute("open", "")
    page.screenshot(path="test-results/activity-cleared-retained.png")
    retained.locator("summary").click()
    actions = retained.get_by_role("button", name="Retry remote cleanup", exact=True)
    expect(actions).to_have_count(3)
    actions.first.click()
    assert len(calls) == 1
    expect(retained).to_have_attribute("open", "")
    page.locator("#activity-clear").click()
    expect(retained).not_to_have_attribute("open", "")
    expect(visible_cards).to_have_count(1)
    assert {identifier: store.job(identifier)["params"] for identifier in old} == before
