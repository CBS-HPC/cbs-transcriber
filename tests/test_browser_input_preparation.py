"""Original preparation controls use the rendered phase/generation authority."""

import copy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.store import Store

pytestmark = pytest.mark.browser


def prepared_job(kind="intent", **changes):
    preparation = {
        "kind": kind,
        "phase": "waiting" if kind == "intent" else "running",
        "epoch": 3,
        "source_state": "running",
        "can_cancel": True,
        "can_retry": False,
        "error_code": None,
    }
    if kind == "intent":
        preparation.update(request_id="b" * 32, native_group_size=4, native_group_waiting=2)
    else:
        preparation.update(generation="c" * 32)
    preparation.update(changes)
    return {
        "id": "a" * 32,
        "recording_id": "d" * 32,
        "kind": "source-inspect" if kind == "inspection" else "transcribe",
        "state": "queued",
        "recording_name": "Synthetic recording",
        "progress": 0,
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
        "input_preparation": preparation,
    }


@pytest.mark.parametrize("kind", ["intent", "inspection"])
def test_preparation_actions_freeze_tokens_and_never_fall_back(page, local_app, kind):
    jobs = [prepared_job(kind)]
    page.route("**/api/jobs", lambda route: route.fulfill(json=jobs))
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    card = page.locator("#jobs .job")
    expect(card).to_contain_text(
        "Original inspection" if kind == "inspection" else "Source preparation"
    )
    expect(card.locator("[data-cancel], [data-retry], [data-runtime-action]")).to_have_count(0)
    if kind == "intent":
        expect(card).to_contain_text("2 of 4 originals")
        expect(card).not_to_contain_text("Open UCloud job")
    button = card.locator("[data-activity-stop]")
    expect(button).to_be_enabled()
    captured = []
    generic = []
    page.route(
        "**/api/jobs/*/cancel",
        lambda route: (generic.append(route.request.url), route.fulfill(json={})),
    )

    def refuse_stale(route):
        captured.append(route.request.post_data_json)
        materialized = copy.deepcopy(jobs[0])
        materialized.pop("input_preparation")
        materialized.update(
            state="running", ucloud_job_id="new-allocation", ucloud_server="https://example.invalid"
        )
        if kind == "intent":
            materialized["runtime_batch"] = {
                "id": "e" * 32,
                "member_count": 4,
                "members": [],
                "can_cancel": True,
            }
        jobs[:] = [materialized]
        route.fulfill(
            status=409, json={"detail": "Preparation changed; refresh its current action."}
        )

    endpoint = "input-intents" if kind == "intent" else "input-inspections"
    page.route(f"**/api/{endpoint}/*/cancel", refuse_stale)
    # The mutable snapshot changes after rendering; the clicked action still owns
    # only its old token. The server refuses it instead of stopping a new owner.
    button.evaluate("button => { state.jobs[0].input_preparation.epoch=99; button.click(); }")
    expect(page.locator("#toast.error")).to_contain_text("Preparation changed")
    expected = (
        {"epoch": 3, "phase": "waiting"}
        if kind == "intent"
        else {"epoch": 3, "generation": "c" * 32}
    )
    assert captured == [expected]
    assert generic == []
    # Only a later, freshly rendered canonical owner offers its ordinary Stop.
    expect(page.locator("#jobs [data-input-action]")).to_have_count(0)
    if kind == "intent":
        expect(page.locator('#jobs .job-footer [data-runtime-action="cancel"]')).to_have_text(
            "Stop runtime batch (4 files)"
        )
    else:
        expect(page.locator("#jobs .job-footer [data-cancel]")).to_have_text("Stop UCloud job")


def test_pending_retry_stays_disabled_through_activity_refresh(page, local_app):
    jobs = [prepared_job(phase="terminal", can_cancel=False, can_retry=True)]
    jobs[0]["state"] = "failed"
    held = []
    page.route("**/api/jobs", lambda route: route.fulfill(json=jobs))
    page.route("**/api/input-intents/*/retry", lambda route: held.append(route))
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    retry = page.locator('#jobs [data-input-action="retry"]')
    retry.click()
    expect(retry).to_be_disabled()
    page.evaluate("async () => await pollJobs()")
    expect(retry).to_be_disabled()
    assert held[0].request.post_data_json == {"epoch": 3, "phase": "terminal"}
    held[0].fulfill(json={})
    expect(retry).to_be_enabled()


def test_invalid_preparation_proof_survives_clear_and_has_no_actions(page, local_app):
    job = prepared_job()
    job.update(state="failed", runtime_batch={"id": "wrong-owner", "can_cancel": True})
    job["input_preparation"] = {
        "kind": "intent",
        "phase": "error",
        "can_cancel": False,
        "can_retry": False,
        "error_code": "invalid_evidence",
    }
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    expect(page.locator("#jobs")).to_contain_text("Preparation evidence")
    expect(
        page.locator(
            "#jobs [data-input-action], #jobs [data-runtime-action], "
            "#jobs [data-cancel], #jobs [data-retry]"
        )
    ).to_have_count(0)
    page.locator("#activity-clear").click()
    expect(page.locator("#jobs [data-retained-activity]")).to_be_visible()
    page.locator("#jobs [data-retained-activity] summary").click()
    expect(page.locator("#jobs")).to_contain_text("Preparation evidence")
    expect(
        page.locator(
            "#jobs [data-input-action], #jobs [data-runtime-action], "
            "#jobs [data-cancel], #jobs [data-retry]"
        )
    ).to_have_count(0)


@pytest.mark.parametrize("status", ["preparing", "error"])
def test_transcription_admission_does_not_require_playback(page, local_app, status):
    store = Store(local_app.root)
    project = store.create_project("Independent source")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status=? WHERE id=?", (status, recording["id"]))
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#transcribe-one")).to_be_enabled()
    expect(page.locator("#play-pause")).to_be_disabled()
    expect(page.locator("#play-transcript")).to_be_disabled()
    expect(page.locator("#transcribe-one")).to_have_attribute(
        "title",
        "Transcription uses the original file; browser playback preparation runs separately.",
    )
