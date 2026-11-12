"""Minimal settings paths and stale mutation responses use synthetic credentials only."""

import pytest
from admission_support import checked_connection
from browser_provider_support import mock_native_check
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import provider_advanced, skip_first_use
from test_native_model_profiles import raw_profile

from cbs_transcriber.catalog import provider_catalog, ucloud_models
from cbs_transcriber.providers import (
    Settings,
    credential_scope,
    public_profiles,
    save_profile,
    set_default_provider,
)

pytestmark = pytest.mark.browser


@pytest.mark.parametrize(
    "tab,kind,models,excluded",
    [
        (
            "OpenAI API",
            "openai",
            ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"],
            "gpt-4o-mini-transcribe",
        ),
        ("UCloud", "ucloud", ["large-v3", "medium"], "medium"),
    ],
)
def test_builtin_connections_preserve_profiles_and_control_available_models(
    page, local_app, monkeypatch, tab, kind, models, excluded
):
    app = local_app.server.config.app
    if kind == "openai":
        # Direct-service settings are archived; no editable model controls or passive writes.
        for index, model in enumerate(models):
            raw_profile(
                app.state.store,
                Settings(
                    model=model,
                    language=["da", "en"][index],
                    concurrent_chunks=index + 1,
                    approved_for=[] if index else ["protected"],
                    enabled=not index,
                ),
                "Research " + model,
            )
        before = app.state.store.all("SELECT * FROM providers ORDER BY id")
        page.goto(local_app.url)
        skip_first_use(page)
        page.locator("#settings-open").click()
        expect(page.locator("#settings-dialog")).to_be_visible()
        expect(page.get_by_role("tab", name=tab, exact=True)).to_have_count(0)
        expect(page.locator('[data-enabled-model="gpt-4o-transcribe"]')).to_have_count(0)
        expect(page.locator("#bulk-provider-kind option[value=openai]")).to_have_count(0)
        assert app.state.store.all("SELECT * FROM providers ORDER BY id") == before
        assert not app.state.store.all("SELECT id FROM jobs")
        return
    mock_native_check(monkeypatch, key="shared-connection-draft-key")
    saved = []
    writes = []
    catalog_models = provider_catalog()["openai"]["models"] if kind == "openai" else ucloud_models()
    page.on(
        "request",
        lambda request: (
            writes.append(request.post_data_json)
            if request.method == "PUT" and "/api/providers/" in request.url
            else None
        ),
    )
    for index, model in enumerate(models):
        existing = next(
            (
                p
                for p in public_profiles(app.state.store, app.state.credentials)["providers"]
                if p["provider_type"] == kind and p["model"] == model
            ),
            None,
        )
        saved.append(
            save_profile(
                app.state.store,
                app.state.credentials,
                "Research " + model,
                Settings(
                    provider_type=kind,
                    base_url="https://api.openai.com/v1"
                    if kind == "openai"
                    else "https://cloud.sdu.dk",
                    model=model,
                    language=["da", "en"][index],
                    concurrent_chunks=index + 1,
                    approved_for=[] if index else ["protected"],
                    ucloud_transcriber={
                        "project_id": "project",
                        "storage_path": "/drive/cbs",
                        "application": {"name": "transcriber", "version": "1.12"},
                        "product": {
                            "id": "gpu-nvidia-b200-1-gpu",
                            "category": "gpu",
                            "provider": "ucloud",
                        },
                    }
                    if kind == "ucloud"
                    else None,
                ),
                identifier=existing["id"] if existing else None,
                # This test starts from a user's explicit all-model selection;
                # fresh setup defaults are checked separately.
                enabled_models=[entry["model"] for entry in catalog_models] if index == 0 else None,
            )
        )
    before = {row["id"]: row for row in app.state.store.all("SELECT * FROM providers")}
    project = app.state.store.create_project("Available models")
    recording = app.state.store.create_recording(project["id"], "Models.wav")
    expected_models = {p["model"] for p in catalog_models}
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    runtime_offered = page.evaluate("featureOn('cbs_runtime')")
    offered_models = {
        model["model"]
        for model in catalog_models
        if kind != "ucloud" or runtime_offered or model.get("engine") != "whisperx"
    }
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name=tab, exact=True).click()
    expect(page.locator(".provider-manager")).to_be_hidden()
    expect(page.locator("#provider-preset")).not_to_be_visible()
    expect(page.locator("#native-model-choice")).not_to_be_visible()
    expect(page.locator("#provider-model-options")).to_be_visible()
    expect(page.locator("#provider-name")).to_be_disabled()
    expect(page.locator("#provider-name")).not_to_be_visible()
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    page.locator("#api-key").fill("shared-connection-draft-key")
    options = page.locator("#provider-model-options [data-enabled-model]")
    expect(options).to_have_count(len(offered_models))
    for option in options.all():
        expect(option).to_be_checked()
    page.locator(f'[data-enabled-model="{excluded}"]').uncheck()
    if kind == "ucloud":
        page.locator("#provider-model-options").screenshot(
            path="test-results/native-available-models.png"
        )
    expect(page.locator("#api-key")).to_have_value("shared-connection-draft-key")
    assert {row["id"]: row for row in app.state.store.all("SELECT * FROM providers")} == before
    page.locator("#connect-ucloud").click()
    expect(page.locator("#ucloud-check-results")).to_contain_text("Synthetic connection checked")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    assert writes and "language" not in writes[-1]
    profiles = [
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["provider_type"] == kind
    ]
    assert {p["model"] for p in profiles} == expected_models
    assert len(profiles) == len(expected_models)
    assert {p["model"] for p in profiles if not p["enabled"]} == {excluded}
    for prior in saved:
        current = next(p for p in profiles if p["id"] == prior["id"])
        for field in ("name", "model", "language", "concurrent_chunks", "approved_for"):
            assert current[field] == prior[field]
    disabled = next(p for p in profiles if p["model"] == excluded)
    page.locator("#bulk-provider-kind").select_option(kind)
    expect(page.locator("#bulk-provider option")).to_have_count(len(offered_models) - 1)
    expect(page.locator(f'#bulk-provider option[value="{disabled["id"]}"]')).to_have_count(0)
    card = page.locator(f'[data-recording="{recording["id"]}"]')
    expect(
        card.locator("select:not([data-recording-language]), .transcription-features")
    ).to_have_count(0)
    expect(card.locator("select[data-recording-language]")).to_have_count(1)
    expect(card.get_by_text("Description / notes", exact=True)).to_be_visible()
    page.get_by_role("button", name="Models", exact=True).click()
    expect(page.locator("#recording-title")).to_have_text("Models")
    page.locator("#file-provider-kind").select_option(kind)
    expect(page.locator(f'#file-provider option[value="{disabled["id"]}"]')).to_have_count(0)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name=tab, exact=True).click()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#key-status")).to_have_text("Connection checked")
    expect(page.locator(f'[data-enabled-model="{excluded}"]')).not_to_be_checked()
    page.locator(f'[data-enabled-model="{excluded}"]').check()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    restored = [
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["provider_type"] == kind
    ]
    assert {p["id"] for p in restored} == {p["id"] for p in profiles}
    assert all(p["enabled"] for p in restored)
    assert all(p["has_key"] for p in restored)
    assert app.state.credentials.get(credential_scope(Settings.model_validate(restored[0]))) == (
        "shared-connection-draft-key"
    )
    assert app.state.store.recording(recording["id"])["name"] == "Models"
    assert not app.state.store.all("SELECT id FROM jobs")


def test_provider_basic_setup_and_reopen_preserve_keys(page, local_app, monkeypatch):
    app = local_app.server.config.app
    mock_native_check(monkeypatch, key="synthetic-basic-key")
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator(".provider-manager")).to_be_hidden()
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    expect(page.locator("#provider-name")).not_to_be_visible()
    expect(page.locator("#provider-name")).to_be_disabled()
    expect(page.locator("#language")).not_to_be_visible()
    page.locator("#provider-advanced > summary").click()
    expect(page.locator("#language")).not_to_be_visible()
    page.locator("#provider-advanced > summary").click()
    expect(page.locator("#provider-preset")).not_to_be_visible()
    expect(page.locator("#check-models")).to_be_hidden()
    expect(page.locator("#provider-model-options")).to_be_visible()
    page.locator("#api-key").fill("synthetic-basic-key")
    expect(page.locator("#provider-policy-summary")).to_contain_text("Allowed")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    saved = next(
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["model"] == "large-v3"
    )
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#key-status")).to_have_text("Connection checked")
    provider_advanced(page)
    expect(page.locator("#provider-name")).not_to_be_visible()
    expect(page.locator("#provider-name")).to_be_disabled()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    settings = Settings.model_validate_json(
        app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    )
    assert settings.model == "large-v3" and settings.capability == "words_speakers"
    assert settings.approved_for == ["protected"]
    assert (
        app.state.store.one("SELECT name FROM providers WHERE id=?", (saved["id"],))["name"]
        == saved["name"]
    )
    assert app.state.credentials.get(credential_scope(settings)) == "synthetic-basic-key"
    assert not app.state.store.all("SELECT id FROM jobs")


@pytest.mark.parametrize("kind", ["custom", "ucloud-vllm"])
def test_deferred_families_preserve_advanced_policy_and_credentials(
    page, local_app, monkeypatch, kind
):
    def no_check(*args, **kwargs):
        raise AssertionError("Opening settings must not check or deploy a model.")

    monkeypatch.setattr("cbs_transcriber.app.check_models", no_check)
    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", no_check)
    app = local_app.server.config.app
    seed_native(app)
    saved = save_profile(
        app.state.store,
        app.state.credentials,
        "Research model " + kind,
        Settings(
            provider_type=kind,
            base_url="http://localhost:9201/v1",
            model="research/custom-model",
            capability="words",
            language="da",
            max_upload_mb=11,
            concurrent_chunks=3,
            approved_for=[],
            ucloud={"hf_model": "research/custom-model", "hf_revision": "fixed-research-revision"}
            if kind == "ucloud-vllm"
            else None,
        ),
        api_key="synthetic-serving-key",
        remember=False,
    )
    before = app.state.store.one("SELECT * FROM providers WHERE id=?", (saved["id"],))
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab")).to_have_text(["UCloud", "Hugging Face", "Local Processing"])
    expect(page.locator("#ucloud-vllm-setup")).not_to_be_visible()
    provider_advanced(page)
    expect(page.locator("#forget-hosting-keys")).not_to_be_visible()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator(f'#bulk-provider option[value="{saved["id"]}"]')).to_have_count(0)
    expect(page.locator(f'#bulk-provider-kind option[value="{kind}"]')).to_have_count(0)
    assert app.state.store.one("SELECT * FROM providers WHERE id=?", (saved["id"],)) == before
    assert app.state.credentials.get(saved["base_url"]) == "synthetic-serving-key"
    assert not app.state.store.all("SELECT id FROM jobs")


def seed_native(app, *, base_url="https://cloud.sdu.dk"):
    saved = save_profile(
        app.state.store,
        app.state.credentials,
        "Research native connection",
        Settings(
            provider_type="ucloud",
            base_url=base_url,
            model="large-v3",
            capability="words_speakers",
            approved_for=[],
            ucloud_transcriber={
                "project_id": "research-project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        api_key="synthetic-control-key",
        remember=False,
        enabled_models=[m["model"] for m in ucloud_models()],
    )
    checked_connection(app.state.store, app.state.credentials, saved["id"])
    return saved


@pytest.mark.parametrize("operation", ["save", "forget-key"])
def test_late_provider_mutation_preserves_newer_configuration_drafts(page, local_app, operation):
    app = local_app.server.config.app
    first = seed_native(app)
    second = seed_native(app, base_url="https://cloud-second.example.test")
    set_default_provider(app.state.store, first["id"])
    held = []

    def hold(route):
        if route.request.method == "PUT":
            held.append(route)
        else:
            route.continue_()

    page.route(f"**/api/providers/{first['id']}", hold)
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    provider_advanced(page)
    if operation == "save":
        page.get_by_role("button", name="Save settings", exact=True).click()
        message = "Settings saved"
    else:
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#forget-key").click()
        message = "Saved endpoint key forgotten"
    expect(page.locator("#settings-provider")).to_have_value(first["id"])
    assert held
    # The retained named-profile select exercises a different exact Settings context.
    page.locator("#settings-provider").select_option(second["id"], force=True)
    provider_advanced(page)
    page.locator("#native-storage").fill("/newer-unsaved-storage")
    page.locator("#api-key").fill("newer-unsaved-control-key")
    page.locator('[data-enabled-model="medium"]').uncheck()
    held[0].fulfill(response=held[0].fetch())
    expect(page.locator("#toast")).to_have_text(message)
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#settings-provider")).to_have_value(second["id"])
    expect(page.locator("#native-storage")).to_have_value("/newer-unsaved-storage")
    expect(page.locator("#api-key")).to_have_value("newer-unsaved-control-key")
    expect(page.locator('[data-enabled-model="medium"]')).not_to_be_checked()
    expect(page.locator("#provider-name")).to_have_value(second["name"])
    expect(page.locator("#provider-name")).to_be_disabled()


@pytest.mark.parametrize("tab,excluded", [("UCloud", "medium"), ("Hugging Face", None)])
def test_late_provider_refresh_retains_new_connection_and_available_model_drafts(
    page, local_app, tab, excluded
):
    app = local_app.server.config.app
    first = seed_native(app)
    held = []
    page.route(f"**/api/providers/{first['id']}", lambda route: held.append(route))
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    provider_advanced(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#forget-key").click()
    assert held
    page.get_by_role("tab", name=tab, exact=True).click()
    token = page.locator("#api-key" if excluded else "#hf-settings-token")
    expect(token).to_be_enabled()
    token.fill("newer-connection-token-draft")
    if excluded:
        expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
        page.locator(f'[data-enabled-model="{excluded}"]').uncheck()
    held[0].fulfill(response=held[0].fetch())
    expect(page.locator("#toast")).to_have_text("Saved endpoint key forgotten")
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")
    expect(token).to_have_value("newer-connection-token-draft")
    if excluded:
        expect(page.locator(f'[data-enabled-model="{excluded}"]')).not_to_be_checked()


def test_unknown_openai_models_keep_settings_and_available_model_controls(page, local_app):
    app = local_app.server.config.app
    seed_native(app)
    identifiers = [
        raw_profile(
            app.state.store,
            Settings(
                provider_type="openai",
                model=model,
                capability="segments",
                language="da",
                enabled=bool(index),
            ),
            "Saved " + model,
        )
        for index, model in enumerate(("experimental-speech-a", "experimental-speech-b"))
    ]
    before = {
        identifier: app.state.store.one("SELECT * FROM providers WHERE id=?", (identifier,))
        for identifier in identifiers
    }
    page.goto(local_app.url)
    skip_first_use(page)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab", name="OpenAI API", exact=True)).to_have_count(0)
    expect(page.locator('[data-enabled-model^="experimental-speech-"]')).to_have_count(0)
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).to_be_hidden()
    for identifier, row in before.items():
        assert app.state.store.one("SELECT * FROM providers WHERE id=?", (identifier,)) == row
    expect(page.locator("#bulk-provider-kind option[value=openai]")).to_have_count(0)
    assert not app.state.store.all("SELECT id FROM jobs")


def test_all_builtin_models_can_be_disabled_and_reenabled_without_losing_connection(
    page, local_app
):
    app = local_app.server.config.app
    native = seed_native(app)
    project = app.state.store.create_project("Temporarily disabled models")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    options = page.locator("#provider-model-options [data-enabled-model]")
    runtime_offered = page.evaluate("featureOn('cbs_runtime')")
    offered_models = [
        model for model in ucloud_models() if runtime_offered or model.get("engine") != "whisperx"
    ]
    expect(options).to_have_count(len(offered_models))
    offered_names = {model["model"] for model in offered_models}
    hidden_rows = {
        profile["id"]: app.state.store.one("SELECT * FROM providers WHERE id=?", (profile["id"],))
        for profile in public_profiles(app.state.store, app.state.credentials)["providers"]
        if profile["provider_type"] == "ucloud" and profile["model"] not in offered_names
    }
    for option in options.all():
        option.uncheck()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#bulk-provider-kind")).to_have_value("")
    expect(page.locator("#bulk-provider-kind")).to_have_text("No enabled models")
    expect(page.locator("#bulk-provider")).to_have_text("No enabled models")
    expect(page.locator("#transcribe-selected")).to_be_disabled()
    profiles = [
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["provider_type"] == "ucloud"
    ]
    assert len(profiles) == len(ucloud_models())
    assert all(p["has_key"] for p in profiles)
    assert all(not p["enabled"] for p in profiles if p["model"] in offered_names)
    assert {
        identifier: app.state.store.one("SELECT * FROM providers WHERE id=?", (identifier,))
        for identifier in hidden_rows
    } == hidden_rows
    original_ids = {p["id"] for p in profiles}
    whisper_id = next(p["id"] for p in profiles if p["model"] == "large-v3")
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#key-status")).to_have_text("Connection checked")
    expect(page.locator("#provider-model-options [data-enabled-model]:checked")).to_have_count(0)
    page.get_by_role("checkbox", name="UCloud whisper/large-v3 (Transcriber)", exact=True).check()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#bulk-provider-kind")).to_have_value("ucloud")
    expect(page.locator("#bulk-provider option")).to_have_count(1)
    expect(page.locator("#bulk-provider")).to_have_value(whisper_id)
    profiles = [
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["provider_type"] == "ucloud"
    ]
    assert {p["id"] for p in profiles} == original_ids
    assert [p["model"] for p in profiles if p["enabled"] and p["model"] in offered_names] == [
        "large-v3"
    ]
    assert {
        identifier: app.state.store.one("SELECT * FROM providers WHERE id=?", (identifier,))
        for identifier in hidden_rows
    } == hidden_rows
    assert native["id"] == whisper_id
    assert (
        app.state.credentials.get("ucloud-control:https://cloud.sdu.dk") == "synthetic-control-key"
    )
    assert not app.state.store.all("SELECT id FROM jobs")
