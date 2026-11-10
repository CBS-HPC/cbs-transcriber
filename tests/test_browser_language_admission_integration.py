"""Exercise real language persistence and transcription admission without provider calls."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import providers

pytestmark = pytest.mark.browser


def test_project_default_and_recording_override_freeze_on_batch(page, local_app, monkeypatch):
    app = local_app.server.config.app
    store = app.state.store

    # A worker that ever gets as far as provider execution must not make a network call.
    def no_provider_call(*_args, **_kwargs):
        raise RuntimeError("Synthetic integration test: no provider calls")

    monkeypatch.setattr(providers, "transcribe_file", no_provider_call)
    profile = providers.save_profile(
        store,
        app.state.credentials,
        "Synthetic OpenAI",
        providers.Settings(provider_type="openai", model="gpt-transcribe"),
        api_key="synthetic-key",
        remember=False,
    )
    before = providers.profile(store, profile["id"])["settings"]

    page.goto(local_app.url)
    page.locator("#sidebar-create").click()
    page.locator("#name-input").fill("Language integration")
    page.locator("#project-classification").select_option("standard")
    page.locator("#project-default-language").select_option("da")
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/projects") and response.request.method == "POST"
        )
    ) as creation:
        page.locator("#name-form button[type=submit]").click()
    assert creation.value.status == 201, creation.value.text()
    expect(page.locator("#name-dialog")).to_be_hidden()
    project = store.one("SELECT id FROM projects WHERE name=?", ("Language integration",))
    first = store.create_recording(project["id"], "first.wav")
    second = store.create_recording(project["id"], "second.wav")

    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.reload()
    override = page.locator(f'[data-recording-language="{second["id"]}"]')
    override.select_option("en")
    expect(override).to_be_enabled()
    expect(override).to_have_value("en")
    page.locator(f'[data-select="{first["id"]}"]').check()
    page.locator(f'[data-select="{second["id"]}"]').check()
    page.locator("#bulk-provider-kind").select_option("openai")
    page.locator("#bulk-provider").select_option(profile["id"])
    page.on("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/transcriptions") and response.request.method == "POST"
        )
    ) as accepted:
        page.locator("#transcribe-selected").click()
    response = accepted.value
    assert response.status == 202, response.text()
    jobs = response.json()
    assert len(jobs) == 2
    frozen = {job["recording_id"]: job["params"]["input_intent"]["accepted"] for job in jobs}
    assert frozen[first["id"]]["language_preference"] == {"scope": "project", "language": "da"}
    assert frozen[second["id"]]["language_preference"] == {"scope": "recording", "language": "en"}
    assert frozen[first["id"]]["provider"]["settings"]["language"] == "da"
    assert frozen[second["id"]]["provider"]["settings"]["language"] == "en"
    assert providers.profile(store, profile["id"])["settings"] == before
