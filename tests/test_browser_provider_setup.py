"""Token-first setup acceptance against a synthetic account; no UCloud access."""

import copy
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.catalog import ucloud_models
from cbs_transcriber.providers import Settings, save_profile
from cbs_transcriber.ucloud_native import MIG_PRODUCT

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("ambiguous", [False, True])
def test_browser_token_first_native_presets_and_cleanup(page, local_app, monkeypatch, ambiguous):
    calls = []

    def discover(settings, key, *, compute_id=None):
        assert key == "synthetic-account-token"
        assert compute_id == MIG_PRODUCT["id"]
        setup = settings.ucloud_transcriber.model_dump()
        calls.append(copy.deepcopy(setup))
        if ambiguous and not setup["project_id"]:
            return {
                "ready": False,
                "setup": setup,
                "choices": {
                    "project_id": [
                        {"id": "p1", "name": "Research one"},
                        {"id": "p2", "name": "Research two"},
                    ]
                },
                "note": "Choose the UCloud project to use.",
            }
        setup.update(
            project_id=setup["project_id"] or "p1",
            application={"name": "transcriber-batch", "version": "1.12"},
            product=dict(MIG_PRODUCT),
            storage_path="/drive/CBS-Transcriber",
        )
        return {
            "ready": True,
            "setup": setup,
            "choices": {},
            "note": "Research one · gpu. Ready to save. No files uploaded or job started.",
        }

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator(".provider-manager")).to_be_hidden()
    expect(page.locator("#native-model-choice")).not_to_be_visible()
    expect(page.locator("#provider-model-options [data-enabled-model]")).to_have_count(
        len(ucloud_models())
    )
    expect(page.locator("#provider-model-options")).to_be_visible()
    expect(page.locator("#model-value-label")).not_to_be_visible()
    expect(page.locator("#model")).to_have_attribute("readonly", "")
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    expect(page.locator("#native-project")).not_to_be_visible()
    expect(page.locator("#base-url")).not_to_be_visible()
    expect(page.locator("#native-cleanup")).to_be_checked()
    for model in [
        "base",
        "small",
        "medium",
        "large-v3-turbo",
        "nvidia/parakeet-tdt-0.6b-v3",
        "large-v3",
    ]:
        checkbox = page.locator(f'[data-enabled-model="{model}"]')
        if model == "large-v3":
            expect(checkbox).to_be_checked()
        else:
            expect(checkbox).not_to_be_checked()
    page.locator("#api-key").fill("synthetic-account-token")
    # Save automatically connects when only a token was supplied.
    page.get_by_role("button", name="Save settings").click()
    if ambiguous:
        expect(page.locator("#native-discovery-choices")).to_contain_text("Research two")
        page.locator('[data-native-choice="project_id"]').select_option("0")
        expect(page.locator("#ucloud-check-results")).to_contain_text("Ready to save")
        page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    app = local_app.server.config.app
    assert (
        app.state.credentials.get("ucloud-control:https://cloud.sdu.dk")
        == "synthetic-account-token"
    )
    assert not app.state.store.all("SELECT id FROM jobs")
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#native-cleanup")).to_be_checked()
    expect(page.locator("#native-model-choice")).to_have_value("large-v3")
    screenshot = Path("test-results/ucloud-token-first.png")
    screenshot.parent.mkdir(exist_ok=True)
    page.screenshot(path=str(screenshot))
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    expect(page.locator("#provider-preset option")).to_have_count(5)
    expect(page.get_by_role("tab", name="Custom", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="UCloud vLLM", exact=True)).to_have_count(0)
    assert len(calls) == (2 if ambiguous else 1)


def test_native_retired_model_is_preserved_without_an_editable_model_control(
    page, local_app, monkeypatch
):
    app = local_app.server.config.app
    saved = save_profile(
        app.state.store,
        app.state.credentials,
        "Older native configuration",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
            model="retired-model",
            ucloud_transcriber={},
        ),
    )
    calls = []

    def discover(settings, key, *, compute_id=None):
        # Discovery is connection metadata only; it must not choose or rewrite the model.
        calls.append(settings.model)
        return {
            "setup": settings.ucloud_transcriber.model_dump(),
            "choices": {},
            "note": "Synthetic: no project found.",
            "ready": False,
        }

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#native-model-choice")).to_have_value("")
    expect(page.locator("#model-value-label")).not_to_be_visible()
    page.get_by_role("button", name="Connect UCloud", exact=True).click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("no project found")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    assert calls and set(calls) == {"retired-model"}
    current = Settings.model_validate_json(
        app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    )
    assert current.model == "retired-model"
    expect(page.locator("#native-model-choice")).not_to_be_visible()
    expect(page.locator("#model")).not_to_be_visible()
    expect(page.locator('[data-enabled-model="retired-model"]')).to_be_checked()
    assert (
        app.state.store.one("SELECT name FROM providers WHERE id=?", (saved["id"],))["name"]
        == saved["name"]
    )


def test_missing_native_application_shows_reason_without_empty_selector(
    page, local_app, monkeypatch
):
    def discover(settings, key, *, compute_id=None):
        return {
            "ready": False,
            "setup": settings.ucloud_transcriber.model_dump(),
            "choices": {"application": []},
            "note": "No compatible Transcriber Batch application is available.",
        }

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    page.get_by_role("button", name="Connect UCloud", exact=True).click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("No compatible")
    expect(page.locator("#native-discovery-choices select")).to_have_count(0)


@pytest.mark.parametrize("action", ["Connect UCloud", "Save settings"])
def test_v74_native_setup_failure_is_readable_inside_dialog(page, local_app, monkeypatch, action):
    message = "UCloud setup could not be verified during project listing. No job was started."

    def discover(settings, key, *, compute_id=None):
        raise ValueError(message)

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    page.locator("#api-key").fill("synthetic-draft-token")
    page.get_by_role("button", name=action, exact=True).click()
    error = page.locator("#settings-dialog [role='alert']")
    expect(error).to_have_text(message)
    expect(error).to_be_in_viewport()
    expect(error).to_be_focused()
    expect(page.locator("#toast.error")).to_be_hidden()
    expect(page.locator("#ucloud-check-results")).not_to_contain_text("Finding")
    expect(page.locator("#api-key")).to_have_value("synthetic-draft-token")
    expect(page.get_by_role("button", name=action, exact=True)).to_be_enabled()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    expect(error).to_be_hidden()


def test_v74_late_native_failure_does_not_replace_another_provider_form(page, local_app):
    held = []
    page.route("**/api/providers/discover-ucloud", lambda route: held.append(route))
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    page.get_by_role("button", name="Connect UCloud", exact=True).click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("Finding")
    assert held
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    page.locator("#api-key").fill("newer-provider-draft")
    held[0].fulfill(status=400, json={"detail": "Previous provider failed"})
    expect(page.locator("#connect-ucloud")).not_to_have_attribute("disabled", "")
    expect(page.locator("#settings-dialog [role='alert']")).to_be_hidden()
    expect(page.locator("#toast.error")).to_be_hidden()
    expect(page.locator("#api-key")).to_have_value("newer-provider-draft")
