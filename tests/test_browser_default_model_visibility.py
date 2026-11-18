"""The first setup offers a small selection without hiding other choices."""

import json

import pytest
from browser_provider_support import mock_native_check
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import skip_first_use
from test_native_model_profiles import native_settings, raw_profile

from cbs_transcriber.providers import save_profile, set_default_provider

pytestmark = [pytest.mark.browser, pytest.mark.first_use]


def test_first_setup_defaults_and_preserves_existing_flags(page, local_app, monkeypatch):
    mock_native_check(monkeypatch, key="synthetic-defaults-key")
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab", name="OpenAI API", exact=True)).to_have_count(0)
    expect(page.locator('[data-enabled-model="large-v3"]')).to_be_checked()
    expect(page.locator('[data-enabled-model="medium"]')).not_to_be_checked()
    runtime_offered = page.evaluate("featureOn('cbs_runtime')")
    if runtime_offered:
        expect(page.locator('[data-enabled-model="whisperx/edda-large"]')).not_to_be_checked()
    else:
        expect(page.locator('[data-enabled-model^="whisperx/"]')).to_have_count(0)
    page.locator('[data-enabled-model="medium"]').check()
    page.locator("#api-key").fill("synthetic-defaults-key")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator('[data-enabled-model="medium"]')).to_be_checked()
    expect(page.locator('[data-enabled-model="large-v3"]')).to_be_checked()
    if runtime_offered:
        expect(page.locator('[data-enabled-model="whisperx/edda-large"]')).not_to_be_checked()
        expect(page.locator('[data-enabled-model="whisperx/roest-v3"]')).not_to_be_checked()
        expect(
            page.locator('[data-enabled-model="whisperx/edda-large"]').locator("..")
        ).to_contain_text("(Danish)")
    else:
        expect(page.locator('[data-enabled-model^="whisperx/"]')).to_have_count(0)
    saved = [
        json.loads(row["value"])
        for row in local_app.server.config.app.state.store.all("SELECT value FROM providers")
    ]
    assert all(not model["enabled"] for model in saved if model["model"].startswith("whisperx/"))


def test_compact_cloud_models_preserve_saved_language_and_approvals(page, local_app):
    app = local_app.server.config.app
    settings = native_settings()
    saved = save_profile(app.state.store, app.state.credentials, "Synthetic", settings)
    before = {row["id"]: row for row in app.state.store.all("SELECT * FROM providers ORDER BY id")}
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    panel = page.locator("#provider-model-list")
    expect(panel.locator("select")).to_have_count(0)
    expect(panel.locator("[data-enabled-model]")).not_to_have_count(0)
    expect(page.locator("#language-label")).not_to_be_visible()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    after = {row["id"]: row for row in app.state.store.all("SELECT * FROM providers ORDER BY id")}
    assert after == before
    assert saved["language"] == "da"


def test_legacy_partial_connection_keeps_unsaved_models_enabled(page, local_app):
    app = local_app.server.config.app
    identifier = raw_profile(app.state.store, native_settings())
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator('[data-enabled-model="medium"]')).to_be_checked()
    expect(page.locator('[data-enabled-model="small"]')).to_be_checked()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    saved = app.state.store.all("SELECT id,value FROM providers")
    assert any(profile["id"] == identifier for profile in saved)
    assert all(json.loads(profile["value"]).get("enabled", True) for profile in saved)


@pytest.mark.parametrize("fresh_marker", [False, True])
def test_unrelated_checkbox_and_generic_save_preserve_mixed_duplicates(
    page, local_app, fresh_marker
):
    app = local_app.server.config.app
    store = app.state.store
    # This real bootstrap row is archived by the service policy. Its fresh/legacy
    # marker must not affect the saved native group or its explicitly disabled duplicate.
    bootstrap = store.one("SELECT * FROM providers")
    payload = json.loads(bootstrap["value"])
    assert payload["_initial_model_defaults"] is True
    if not fresh_marker:
        payload.pop("_initial_model_defaults")
        store.execute(
            "UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), bootstrap["id"])
        )
    bootstrap_before = store.one("SELECT * FROM providers WHERE id=?", (bootstrap["id"],))
    saved = save_profile(store, app.state.credentials, "Native defaults", native_settings())
    duplicate = raw_profile(
        store,
        native_settings(enabled=False),
        "Disabled duplicate",
    )
    set_default_provider(store, saved["id"])
    before = store.one("SELECT * FROM providers WHERE id=?", (duplicate,))
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    assert store.one("SELECT * FROM providers WHERE id=?", (duplicate,)) == before
    assert store.one("SELECT * FROM providers WHERE id=?", (bootstrap["id"],)) == bootstrap_before

    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    checkbox = page.locator('[data-enabled-model="medium"]')
    checkbox.set_checked(not checkbox.is_checked())
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    assert store.one("SELECT * FROM providers WHERE id=?", (duplicate,)) == before
    assert store.one("SELECT * FROM providers WHERE id=?", (bootstrap["id"],)) == bootstrap_before


def test_runtime_auto_language_survives_generic_save_and_stale_form(page, local_app):
    app = local_app.server.config.app
    settings = native_settings(model="whisperx/large-v3", language="auto", capability="words")
    saved = save_profile(app.state.store, app.state.credentials, "Synthetic Runtime", settings)
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#settings-provider")).to_have_value(saved["id"])
    if page.evaluate("featureOn('cbs_runtime')"):
        expect(page.locator('[data-enabled-model="whisperx/large-v3"]')).to_be_checked()
    else:
        expect(page.locator('[data-enabled-model^="whisperx/"]')).to_have_count(0)
        expect(page.locator(f'#bulk-provider option[value="{saved["id"]}"]')).to_have_count(0)
    expect(page.locator("#language-label")).not_to_be_visible()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    raw = app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    payload = json.loads(raw)
    assert payload["language"] == "auto"
    assert payload["enabled"] is True and payload["approved_for"] == ["protected"]

    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    payload["approved_for"] = []
    changed = json.dumps(payload, separators=(",", ":"))
    app.state.store.execute("UPDATE providers SET value=? WHERE id=?", (changed, saved["id"]))
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    assert (
        app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
        == changed
    )
