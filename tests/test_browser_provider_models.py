"""Provider/model selection uses configured IDs and synthetic account metadata."""

import json

import pytest
from playwright.sync_api import expect
from test_browser import create_project, import_recording
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.catalog import ucloud_models
from cbs_transcriber.providers import Settings, save_profile

# Native Transcriber models plus the CBS-runtime WhisperX models (ADR 0053).
UCLOUD_MODELS = len(ucloud_models())

pytestmark = pytest.mark.browser


def test_v78_provider_and_model_controls_cover_bulk_and_editor_with_notes_only_cards(
    page, local_app, wav_bytes
):
    app = local_app.server.config.app
    configured = save_profile(
        app.state.store,
        app.state.credentials,
        "UCloud Transcriber whisper/large-v3",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
            model="large-v3",
            capability="words_speakers",
            approved_for=["protected"],
            ucloud_transcriber={
                "project_id": "project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        api_key="synthetic-native-key",
        remember=False,
        enabled_models=[model["model"] for model in ucloud_models()],
    )
    native = [
        p
        for p in app.state.store.all("SELECT * FROM providers")
        if json.loads(p["value"])["provider_type"] == "ucloud"
    ]
    ids = {json.loads(p["value"])["model"]: p["id"] for p in native}
    assert len(ids) == UCLOUD_MODELS
    bodies = []
    page.route(
        "**/api/transcriptions",
        lambda route: (bodies.append(route.request.post_data_json), route.fulfill(json=[])),
    )
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(local_app.url)
    create_project(page, "Provider models")
    expect(page.locator("#bulk-provider-kind")).to_contain_text("OpenAI")
    expect(page.locator("#bulk-provider-kind")).to_contain_text("UCloud")
    expect(page.locator("#bulk-provider")).to_contain_text("gpt-transcribe")
    page.locator("#bulk-provider-kind").select_option("ucloud")
    expect(page.locator("#bulk-provider option")).to_have_count(UCLOUD_MODELS)
    page.locator("#bulk-provider").select_option(ids["small"])
    expect(page.locator("#bulk-features input:checked:disabled")).to_have_count(2)
    import_recording(page, local_app, "models.wav", wav_bytes)
    rid = page.url.rsplit("/", 1)[-1]
    expect(page.locator("#file-provider-kind")).to_have_value("ucloud")
    expect(page.locator("#file-provider option")).to_have_count(UCLOUD_MODELS)
    page.locator("#file-provider").select_option(ids["medium"])
    page.locator("#transcribe-one").click()
    expect(page.locator("#toast")).to_contain_text("Added to the transcription queue")
    assert bodies[-1]["provider_ids"] == [ids["medium"]]
    page.locator("#activity-close").click()
    page.locator("#back-library").click()
    card = page.locator(f'[data-recording="{rid}"]')
    expect(
        card.locator("select:not([data-recording-language]), .transcription-features")
    ).to_have_count(0)
    expect(card.locator("select[data-recording-language]")).to_have_count(1)
    expect(card.get_by_role("button", name="Transcribe", exact=True)).to_have_count(0)
    expect(card.get_by_text("Description / notes", exact=True)).to_be_visible()
    expect(card.locator(".card-results")).to_have_text("0 results")
    expect(page.locator("#batch-open")).to_have_count(0)
    page.screenshot(path="test-results/recording-cards-notes-only.png")
    card.locator("[data-open]").click()
    expect(page.locator("#file-provider")).to_have_value(ids["medium"])
    page.locator("#file-provider-kind").select_option("openai")
    expect(page.locator("#file-provider option")).to_have_count(1)
    page.locator("#file-provider-kind").select_option("ucloud")
    page.locator("#file-provider").select_option(ids["base"])
    page.locator("#back-library").click()
    page.locator("#select-all").click()
    page.locator("#bulk-provider-kind").select_option("ucloud")
    page.locator("#bulk-provider").select_option(ids["large-v3-turbo"])
    with page.expect_request("**/api/transcriptions"):
        page.locator("#transcribe-selected").click()
    expect(page.locator("#toast")).to_contain_text("Added to the transcription queue")
    assert bodies[-1]["provider_ids"] == [ids["large-v3-turbo"]]
    assert configured["id"] in ids.values()
    assert not app.state.store.all("SELECT id FROM jobs WHERE kind='transcribe'")


def test_v78_protected_model_choices_keep_explicit_denials(page, local_app):
    app = local_app.server.config.app
    native = save_profile(
        app.state.store,
        app.state.credentials,
        "Native",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
            model="large-v3",
            approved_for=["protected"],
            ucloud_transcriber={
                "project_id": "p",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        enabled_models=[model["model"] for model in ucloud_models()],
    )
    denied = next(
        row
        for row in app.state.store.all("SELECT * FROM providers")
        if json.loads(row["value"])["model"] == "small"
    )
    settings = Settings.model_validate_json(denied["value"])
    settings.approved_for = []
    save_profile(
        app.state.store, app.state.credentials, denied["name"], settings, identifier=denied["id"]
    )
    project = app.state.store.create_project("Protected", "protected")
    page.goto(local_app.url + "/#/projects/" + project["id"])
    expect(page.locator("#bulk-provider-kind option")).to_have_count(1)
    expect(page.locator("#bulk-provider-kind")).to_have_value("ucloud")
    expect(page.locator("#bulk-provider option")).to_have_count(UCLOUD_MODELS - 1)
    expect(page.locator(f'#bulk-provider option[value="{denied["id"]}"]')).to_have_count(0)
    expect(page.locator(f'#bulk-provider option[value="{native["id"]}"]')).to_have_count(1)
