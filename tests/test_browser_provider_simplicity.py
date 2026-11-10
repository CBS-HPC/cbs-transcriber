"""Minimal settings paths and stale mutation responses use synthetic credentials only."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import provider_advanced

from cbs_transcriber.catalog import provider_catalog, ucloud_models
from cbs_transcriber.providers import Settings, credential_scope, public_profiles, save_profile

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
    page, local_app, tab, kind, models, excluded
):
    app = local_app.server.config.app
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
    page.locator("#settings-open").click()
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
    expect(options).to_have_count(len(expected_models))
    for option in options.all():
        expect(option).to_be_checked()
    page.locator(f'[data-enabled-model="{excluded}"]').uncheck()
    if kind == "ucloud":
        page.locator("#provider-model-options").screenshot(
            path="test-results/native-available-models.png"
        )
    expect(page.locator("#api-key")).to_have_value("shared-connection-draft-key")
    assert {row["id"]: row for row in app.state.store.all("SELECT * FROM providers")} == before
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
    expect(page.locator("#bulk-provider option")).to_have_count(len(expected_models) - 1)
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
    page.get_by_role("tab", name=tab, exact=True).click()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#key-status")).to_have_text("Key available")
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


def test_provider_basic_setup_and_reopen_preserve_keys(page, local_app):
    app = local_app.server.config.app
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    expect(page.locator(".provider-manager")).to_be_hidden()
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    expect(page.locator("#provider-name")).not_to_be_visible()
    expect(page.locator("#provider-name")).to_be_disabled()
    expect(page.locator("#language")).not_to_be_visible()
    page.locator("#provider-advanced > summary").click()
    expect(page.locator("#language")).not_to_be_visible()
    page.locator("#provider-advanced > summary").click()
    expect(page.locator("#provider-preset")).not_to_be_visible()
    expect(page.locator("#check-models")).to_be_visible()
    expect(page.locator("#provider-model-options")).to_be_visible()
    page.locator("#api-key").fill("synthetic-basic-key")
    expect(page.locator("#provider-policy-summary")).to_contain_text("Not allowed")
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    saved = next(
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["model"] == "whisper-1"
    )
    page.locator("#settings-open").click()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#key-status")).to_have_text("Key available")
    provider_advanced(page)
    expect(page.locator("#provider-name")).not_to_be_visible()
    expect(page.locator("#provider-name")).to_be_disabled()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    settings = Settings.model_validate_json(
        app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    )
    assert settings.model == "whisper-1" and settings.capability == "words"
    assert settings.approved_for == []
    assert (
        app.state.store.one("SELECT name FROM providers WHERE id=?", (saved["id"],))["name"]
        == saved["name"]
    )
    assert app.state.credentials.get(settings.base_url) == "synthetic-basic-key"
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
    page.locator("#settings-open").click()
    expect(page.get_by_role("tab")).to_have_text(
        ["OpenAI API", "UCloud", "Hugging Face", "Local Processing"]
    )
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


def seed_native(app):
    return save_profile(
        app.state.store,
        app.state.credentials,
        "Research native connection",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
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
    )


@pytest.mark.parametrize("operation", ["save", "forget-key"])
def test_late_provider_mutation_preserves_newer_configuration_drafts(page, local_app, operation):
    app = local_app.server.config.app
    first = public_profiles(app.state.store, app.state.credentials)["providers"][0]
    second = seed_native(app)
    held = []

    def hold(route):
        if route.request.method == "PUT":
            held.append(route)
        else:
            route.continue_()

    page.route(f"**/api/providers/{first['id']}", hold)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
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
    page.get_by_role("tab", name="UCloud", exact=True).click()
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


@pytest.mark.parametrize(
    "tab,excluded",
    [
        ("OpenAI API", "gpt-4o-transcribe-diarize"),
        ("UCloud", "medium"),
    ],
)
def test_late_provider_refresh_retains_new_connection_and_available_model_drafts(
    page, local_app, tab, excluded
):
    app = local_app.server.config.app
    native = seed_native(app)
    first = (
        native
        if tab == "OpenAI API"
        else public_profiles(app.state.store, app.state.credentials)["providers"][0]
    )
    held = []
    page.route(f"**/api/providers/{first['id']}", lambda route: held.append(route))
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role(
        "tab", name="UCloud" if tab == "OpenAI API" else "OpenAI API", exact=True
    ).click()
    provider_advanced(page)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#forget-key").click()
    assert held
    page.get_by_role("tab", name=tab, exact=True).click()
    page.locator("#api-key").fill("newer-connection-token-draft")
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    page.locator(f'[data-enabled-model="{excluded}"]').uncheck()
    held[0].fulfill(response=held[0].fetch())
    expect(page.locator("#toast")).to_have_text("Saved endpoint key forgotten")
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab", name=tab, exact=True)).to_have_attribute("aria-selected", "true")
    expect(page.locator("#api-key")).to_have_value("newer-connection-token-draft")
    expect(page.locator(f'[data-enabled-model="{excluded}"]')).not_to_be_checked()


def test_unknown_openai_models_keep_settings_and_available_model_controls(page, local_app):
    app = local_app.server.config.app
    saved = [
        save_profile(
            app.state.store,
            app.state.credentials,
            "Saved " + model,
            Settings(provider_type="openai", model=model, capability="segments", language="da"),
        )
        for model in ("experimental-speech-a", "experimental-speech-b")
    ]
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    expect(page.locator("#provider-preset")).not_to_be_visible()
    expect(page.locator("#provider-model-options [data-enabled-model]")).to_have_count(7)
    for item in saved:
        expect(page.get_by_role("checkbox", name=item["model"], exact=True)).to_be_checked()
    page.get_by_role("checkbox", name=saved[0]["model"], exact=True).uncheck()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    for index, item in enumerate(saved):
        current = Settings.model_validate_json(
            app.state.store.one("SELECT value FROM providers WHERE id=?", (item["id"],))["value"]
        )
        assert current.model == item["model"] and current.capability == "segments"
        assert current.language == "da" and current.enabled == bool(index)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    expect(page.locator("#check-models")).to_be_visible()
    page.locator("#settings-dialog").screenshot(path="test-results/openai-basic-availability.png")
    expect(page.get_by_role("checkbox", name=saved[0]["model"], exact=True)).not_to_be_checked()
    expect(page.get_by_role("checkbox", name=saved[1]["model"], exact=True)).to_be_checked()
    assert not app.state.store.all("SELECT id FROM jobs")


def test_all_builtin_models_can_be_disabled_and_reenabled_without_losing_connection(
    page, local_app
):
    app = local_app.server.config.app
    project = app.state.store.create_project("Temporarily disabled models")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#settings-open").click()
    page.locator("#api-key").fill("retained-connection-key")
    options = page.locator("#provider-model-options [data-enabled-model]")
    expect(options).to_have_count(5)
    for option in options.all():
        option.uncheck()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#bulk-provider-kind")).to_have_value("")
    expect(page.locator("#bulk-provider-kind")).to_have_text("No enabled models")
    expect(page.locator("#bulk-provider")).to_have_text("No enabled models")
    expect(page.locator("#transcribe-selected")).to_be_disabled()
    profiles = public_profiles(app.state.store, app.state.credentials)["providers"]
    assert len(profiles) == 5 and all(not p["enabled"] and p["has_key"] for p in profiles)
    original_ids = {p["id"] for p in profiles}
    whisper_id = next(p["id"] for p in profiles if p["model"] == "whisper-1")
    page.locator("#settings-open").click()
    expect(page.locator("#api-key")).to_have_value("")
    expect(page.locator("#key-status")).to_have_text("Key available")
    expect(page.locator("#provider-model-options [data-enabled-model]:checked")).to_have_count(0)
    page.get_by_role("checkbox", name="OpenAI whisper-1 (Word timing)", exact=True).check()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    expect(page.locator("#bulk-provider-kind")).to_have_value("openai")
    expect(page.locator("#bulk-provider option")).to_have_count(1)
    expect(page.locator("#bulk-provider")).to_have_value(whisper_id)
    profiles = public_profiles(app.state.store, app.state.credentials)["providers"]
    assert {p["id"] for p in profiles} == original_ids
    assert [p["model"] for p in profiles if p["enabled"]] == ["whisper-1"]
    assert app.state.credentials.get("https://api.openai.com/v1") == "retained-connection-key"
    assert not app.state.store.all("SELECT id FROM jobs")
