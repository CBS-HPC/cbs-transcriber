"""The first setup offers a small selection without hiding other choices."""

import json

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_native_model_profiles import native_settings, raw_profile

from cbs_transcriber.providers import Settings, save_profile

pytestmark = pytest.mark.browser


def test_first_setup_defaults_and_preserves_existing_flags(page, local_app):
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    expect(page.locator('[data-enabled-model="gpt-transcribe"]')).to_be_checked()
    expect(page.locator('[data-enabled-model="whisper-1"]')).not_to_be_checked()
    expect(page.locator('[data-enabled-model="gpt-4o-transcribe"]')).not_to_be_checked()
    page.locator('[data-enabled-model="whisper-1"]').check()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    expect(page.locator('[data-enabled-model="whisper-1"]')).to_be_checked()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator('[data-enabled-model="large-v3"]')).to_be_checked()
    expect(page.locator('[data-enabled-model="whisperx/edda-large"]')).not_to_be_checked()
    expect(page.locator('[data-enabled-model="whisperx/roest-v3"]')).not_to_be_checked()
    expect(
        page.locator('[data-enabled-model="whisperx/edda-large"]').locator("..")
    ).to_contain_text("(Danish)")


def test_compact_cloud_models_preserve_saved_language_and_approvals(page, local_app):
    app = local_app.server.config.app
    settings = native_settings()
    saved = save_profile(app.state.store, app.state.credentials, "Synthetic", settings)
    before = {row["id"]: row for row in app.state.store.all("SELECT * FROM providers ORDER BY id")}
    page.goto(local_app.url)
    page.locator("#settings-open").click()
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
    row = app.state.store.one("SELECT * FROM providers")
    payload = json.loads(row["value"])
    payload.pop("_initial_model_defaults")
    app.state.store.execute(
        "UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), row["id"])
    )
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    expect(page.locator('[data-enabled-model="whisper-1"]')).to_be_checked()
    expect(page.locator('[data-enabled-model="gpt-4o-transcribe"]')).to_be_checked()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    saved = app.state.store.all("SELECT value FROM providers")
    assert all(json.loads(profile["value"]).get("enabled", True) for profile in saved)


@pytest.mark.parametrize("fresh_marker", [False, True])
def test_unrelated_checkbox_and_generic_save_preserve_mixed_duplicates(
    page, local_app, fresh_marker
):
    app = local_app.server.config.app
    store = app.state.store
    row = store.one("SELECT * FROM providers")
    payload = json.loads(row["value"])
    if not fresh_marker:
        payload.pop("_initial_model_defaults")
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), row["id"]))
    duplicate = raw_profile(
        store,
        Settings(provider_type="openai", model="gpt-transcribe", enabled=False),
        "Disabled duplicate",
    )
    before = store.one("SELECT * FROM providers WHERE id=?", (duplicate,))
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    assert store.one("SELECT * FROM providers WHERE id=?", (duplicate,)) == before

    page.locator("#settings-open").click()
    page.get_by_role("tab", name="OpenAI API", exact=True).click()
    page.locator('[data-enabled-model="whisper-1"]').uncheck()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    assert store.one("SELECT * FROM providers WHERE id=?", (duplicate,)) == before


def test_runtime_auto_language_survives_generic_save_and_stale_form(page, local_app):
    app = local_app.server.config.app
    settings = native_settings(model="whisperx/large-v3", language="auto", capability="words")
    saved = save_profile(app.state.store, app.state.credentials, "Synthetic Runtime", settings)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#settings-provider")).to_have_value(saved["id"])
    expect(page.locator('[data-enabled-model="whisperx/large-v3"]')).to_be_checked()
    expect(page.locator("#language-label")).not_to_be_visible()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    raw = app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    payload = json.loads(raw)
    assert payload["language"] == "auto"
    assert payload["enabled"] is True and payload["approved_for"] == ["protected"]

    page.locator("#settings-open").click()
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
