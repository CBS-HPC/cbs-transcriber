"""Deferred runtime preparation needs new, explicit frozen-request consent."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def needs_confirmation():
    return {
        "id": "a" * 32,
        "recording_id": "b" * 32,
        "kind": "enhance",
        "state": "failed",
        "recording_name": "Synthetic recording",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
        "input_preparation": {
            "kind": "intent",
            "phase": "terminal",
            "epoch": 4,
            "request_id": "c" * 32,
            "source_state": "ready",
            "can_cancel": False,
            "can_retry": False,
            "can_prepare": True,
            "prepare_preview": {
                "scope": "native_selection",
                "count": 2,
                "fingerprint": "d" * 64,
                "recordings": [
                    {
                        "recording_id": "b" * 32,
                        "name": "First frozen recording",
                        "provider_name": "UCloud model",
                    },
                    {
                        "recording_id": "e" * 32,
                        "name": "Second frozen recording",
                        "provider_name": "UCloud model",
                    },
                ],
            },
            "error_code": "needs_confirmation",
        },
    }


@pytest.mark.parametrize("consent", [False, True])
def test_deferred_runtime_preparation_requires_explicit_frozen_consent(page, local_app, consent):
    job = needs_confirmation()
    requests = []
    prompts = []
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))

    def prepare_request(route):
        requests.append(route.request.post_data_json)
        job["input_preparation"].update(
            can_prepare=False,
            prepare_preview=None,
            continuation={"request_id": "f" * 32, "job_ids": ["1" * 32, "2" * 32]},
        )
        route.fulfill(status=202, json={"request_id": "f" * 32, "jobs": []})

    page.route("**/api/input-intents/*/prepare", prepare_request)
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    expect(page.locator('#jobs [data-input-action="retry"]')).to_have_count(0)
    prepare = page.locator('#jobs [data-input-action="prepare"]')
    expect(prepare).to_have_text("Prepare and continue")

    def answer(dialog):
        prompts.append(dialog.message)
        (dialog.accept if consent else dialog.dismiss)()

    page.once("dialog", answer)
    # The decision belongs to the rendered failed request, not a current form or
    # a mutable poll snapshot that happens to show a different epoch or scope.
    prepare.evaluate(
        "button => { state.jobs[0].input_preparation.epoch=99; "
        "state.jobs[0].input_preparation.prepare_preview.count=50; "
        "state.jobs[0].input_preparation.prepare_preview.fingerprint='f'.repeat(64); "
        "button.click(); }"
    )
    if consent:
        expect(prepare).to_have_count(0)
        expect(page.locator("#jobs")).to_contain_text("new request(s) accepted")
        expect(page.locator("#jobs")).not_to_contain_text("transcription complete")
    else:
        expect(prepare).to_be_enabled()
    assert len(prompts) == 1
    assert "2" in prompts[0] and "50" not in prompts[0]
    assert "runtime" in prompts[0] and "public models" in prompts[0]
    assert "download" in prompts[0] and "frozen" in prompts[0]
    assert "First frozen recording" in prompts[0] and "Second frozen recording" in prompts[0]
    assert requests == (
        [{"epoch": 4, "phase": "terminal", "fingerprint": "d" * 64, "confirmed": True}]
        if consent
        else []
    )
    if consent:
        page.locator("#activity-clear").click()
        expect(page.locator("#jobs .job")).to_have_count(0)


def test_stale_preparation_consent_cannot_retarget_or_retry_a_new_owner(page, local_app):
    job = needs_confirmation()
    requests = []
    other_posts = []
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))

    def stale(route):
        requests.append(route.request.post_data_json)
        job["input_preparation"]["epoch"] = 5
        job["input_preparation"]["can_prepare"] = False
        route.fulfill(status=409, json={"detail": "Preparation scope changed. Refresh Activity."})

    page.route("**/api/input-intents/*/prepare", stale)
    page.route(
        "**/api/jobs/*/retry",
        lambda route: (other_posts.append(route.request.url), route.fulfill(json={})),
    )
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#jobs [data-input-action="prepare"]').click()
    expect(page.locator("#toast.error")).to_contain_text("Preparation scope changed")
    assert requests == [
        {"epoch": 4, "phase": "terminal", "fingerprint": "d" * 64, "confirmed": True}
    ]
    assert other_posts == []
    expect(page.locator('#jobs [data-input-action="prepare"]')).to_have_count(0)


def test_unverified_preparation_preview_has_no_consent_action(page, local_app):
    job = needs_confirmation()
    job["input_preparation"]["prepare_preview"]["fingerprint"] = "invalid"
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    expect(page.locator("#jobs")).to_contain_text("Preparation preview could not be verified")
    expect(page.locator("#jobs [data-input-action]")).to_have_count(0)


def test_pending_consent_stays_disabled_when_display_names_change(page, local_app):
    job = needs_confirmation()
    held = []
    page.route("**/api/jobs", lambda route: route.fulfill(json=[job]))
    page.route("**/api/input-intents/*/prepare", lambda route: held.append(route))
    page.goto(local_app.url)
    page.evaluate(
        "async () => { await pollJobs(); document.querySelector('#activity').hidden=false; }"
    )
    page.once("dialog", lambda dialog: dialog.accept())
    prepare = page.locator('#jobs [data-input-action="prepare"]')
    prepare.click()
    expect(prepare).to_be_disabled()
    job["input_preparation"]["prepare_preview"]["recordings"][0]["name"] = "Renamed display label"
    page.evaluate("async () => await pollJobs()")
    try:
        expect(prepare).to_be_disabled()
        assert len(held) == 1
    finally:
        held[0].fulfill(json={})
