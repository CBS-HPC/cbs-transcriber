"""Current provider controls defer legacy families without rewriting stored research work."""

import json

import pytest
from admission_support import checked_connection
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser import provider_advanced
from test_browser_huggingface_activity import inert_app as inert_app
from test_native_model_profiles import native_settings, raw_profile

from cbs_transcriber.catalog import ucloud_models
from cbs_transcriber.providers import (
    Settings,
    credential_scope,
    public_profiles,
    save_profile,
    set_default_provider,
)
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_deferred_families_stay_saved_but_leave_new_provider_choices(page, local_app):
    app = local_app.server.config.app
    store, credentials = app.state.store, app.state.credentials
    native = save_profile(
        store,
        credentials,
        "Checked native",
        native_settings(),
        api_key="synthetic-native",
        remember=False,
    )
    checked_connection(store, credentials, native["id"])
    project = store.create_project("Existing provider research")
    recording = store.create_recording(project["id"], "Existing.wav")
    legacy = []
    for kind in ("custom", "ucloud-vllm"):
        legacy.append(
            save_profile(
                store,
                credentials,
                "Saved " + kind,
                Settings(
                    provider_type=kind,
                    base_url="http://localhost:9999/v1",
                    model="research/model",
                    ucloud={"hf_model": "research/model"} if kind == "ucloud-vllm" else None,
                ),
            )
        )
    set_default_provider(store, legacy[0]["id"])
    store.execute(
        "UPDATE recordings SET provider_id=? WHERE id=?", (legacy[0]["id"], recording["id"])
    )
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Existing research text."}]),
        {},
        "Legacy saved result",
        {"kind": "provider", "provider_type": "custom", "model": "research/model"},
    )
    frozen = {"provider_id": legacy[1]["id"], "settings": legacy[1]}
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?)",
        (new_id(), recording["id"], "transcribe", "completed", json.dumps(frozen), now(), now()),
    )
    before = {
        table: store.all("SELECT * FROM " + table + " ORDER BY id")
        for table in ("providers", "jobs", "transcript_results", "result_versions")
    }
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator("#bulk-provider-kind option")).to_have_count(1)
    expect(page.locator("#bulk-provider-kind")).to_have_value("ucloud")
    expect(page.locator("#bulk-provider")).not_to_contain_text("research/model")
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab")).to_have_text(["UCloud", "Hugging Face", "Local Processing"])
    expect(page.get_by_role("tab", name="OpenAI API", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="UCloud", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    expect(page.get_by_role("tab", name="Custom", exact=True)).to_have_count(0)
    expect(page.get_by_role("tab", name="UCloud vLLM", exact=True)).to_have_count(0)
    page.get_by_role("tab", name="UCloud", exact=True).press("ArrowRight")
    expect(page.get_by_role("tab", name="Hugging Face", exact=True)).to_be_focused()
    page.locator('#settings-dialog [aria-label="Close settings"]').click()
    page.get_by_role("button", name="Existing", exact=True).click()
    expect(page.locator("#file-provider-kind")).to_have_value("ucloud")
    expect(page.locator(f'#file-provider option[value="{legacy[0]["id"]}"]')).to_have_count(0)
    expect(page.locator("#result-select")).to_have_value(result["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Existing research text.")
    assert {
        table: store.all("SELECT * FROM " + table + " ORDER BY id") for table in before
    } == before
    assert store.recording(recording["id"])["provider_id"] == legacy[0]["id"]


@pytest.mark.parametrize("kind", ["openai", "ucloud"])
def test_basic_models_keep_saved_names_keys_and_visibility(page, local_app, kind):
    app = local_app.server.config.app
    settings = Settings(
        provider_type=kind,
        base_url="https://api.openai.com/v1" if kind == "openai" else "https://cloud.sdu.dk",
        model="gpt-transcribe" if kind == "openai" else "large-v3",
        language="da",
        approved_for=[],
        ucloud_transcriber={
            "project_id": "research",
            "storage_path": "/drive/research",
            "application": {"name": "transcriber", "version": "1.12"},
            "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
        }
        if kind == "ucloud"
        else None,
    )
    if kind == "openai":
        # Historical direct-service profiles remain readable but cannot be edited/selected.
        identifier = raw_profile(app.state.store, settings, "Exact saved research name")
        app.state.credentials.set(credential_scope(settings), "synthetic-connection-key", False)
        before = app.state.store.all("SELECT * FROM providers ORDER BY id")
        page.goto(local_app.url)
        page.locator("#settings-open").click()
        expect(page.locator("#settings-dialog")).to_be_visible()
        expect(page.get_by_role("tab", name="OpenAI API", exact=True)).to_have_count(0)
        expect(page.locator(f'#settings-provider option[value="{identifier}"]')).to_have_count(0)
        expect(page.locator('[data-enabled-model="gpt-transcribe"]')).to_have_count(0)
        page.locator('#settings-dialog [aria-label="Close settings"]').click()
        assert app.state.store.all("SELECT * FROM providers ORDER BY id") == before
        assert app.state.credentials.get(credential_scope(settings)) == "synthetic-connection-key"
        return
    existing = next(
        (
            p
            for p in public_profiles(app.state.store, app.state.credentials)["providers"]
            if p["provider_type"] == kind and p["model"] == settings.model
        ),
        None,
    )
    saved = save_profile(
        app.state.store,
        app.state.credentials,
        "Exact saved research name",
        settings,
        identifier=existing["id"] if existing else None,
        api_key="synthetic-connection-key",
        remember=False,
    )
    checked_connection(app.state.store, app.state.credentials, saved["id"])
    # Open Settings on this saved connection rather than the startup fixture's choice.
    set_default_provider(app.state.store, saved["id"])
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="OpenAI API" if kind == "openai" else "UCloud", exact=True).click()
    expect(page.locator("#provider-advanced")).not_to_have_attribute("open", "")
    expect(page.locator("#provider-model-options")).to_be_visible()
    assert page.locator("#provider-model-options").evaluate(
        "e => e.closest('#provider-basic') !== null && e.closest('#provider-advanced') === null"
    )
    expect(page.locator("#provider-model-options [data-enabled-model]")).to_have_count(
        5 if kind == "openai" else len(ucloud_models())
    )
    expect(page.locator("#provider-name-label")).to_be_hidden()
    expect(page.locator("#provider-name")).to_be_disabled()
    expect(page.locator("#provider-name")).to_have_value("Exact saved research name")
    expect(page.locator("#key-status")).to_have_text("Connection checked")
    if kind == "ucloud":
        expect(
            page.get_by_role("checkbox", name="UCloud whisper/large-v3 (Transcriber)", exact=True)
        ).to_be_checked()
    else:
        expect(page.locator("#check-models")).to_be_visible()
    excluded = "gpt-4o-mini-transcribe" if kind == "openai" else "medium"
    page.locator(f'[data-enabled-model="{excluded}"]').uncheck()
    provider_advanced(page)
    expect(page.locator("#provider-name-label")).to_be_hidden()
    expect(page.locator("#provider-model-options")).to_be_visible()
    page.get_by_role("button", name="Save settings", exact=True).click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    current = next(
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["id"] == saved["id"]
    )
    assert current["name"] == "Exact saved research name" and current["has_key"]
    assert current["language"] == "da" and current["approved_for"] == []
    assert not next(
        p
        for p in public_profiles(app.state.store, app.state.credentials)["providers"]
        if p["provider_type"] == kind and p["model"] == excluded
    )["enabled"]
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="OpenAI API" if kind == "openai" else "UCloud", exact=True).click()
    expect(page.locator(f'[data-enabled-model="{excluded}"]')).not_to_be_checked()
    expect(page.locator("#provider-name")).to_have_value("Exact saved research name")
    page.locator("#settings-dialog").screenshot(path=f"test-results/provider-basic-{kind}.png")


def test_hugging_face_tab_saves_and_checks_token_without_echoing_it(page, inert_app):
    from cbs_transcriber.enhancement import HF_SCOPE

    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append((request.url, request.post_data_json))
            if request.method in {"PUT", "POST"}
            else None
        ),
    )
    page.goto(inert_app.url)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.get_by_role("tab", name="Hugging Face", exact=True).click()
    expect(page.locator("#hf-panel")).to_be_visible()
    expect(page.locator("#provider-fields")).to_be_hidden()
    expect(page.locator("#hf-settings-result")).to_contain_text("Word timing works without one")
    page.locator("#hf-settings-token").fill("hf_SYNTHETIC_BROWSER")
    expect(page.locator("#hf-settings-check")).to_be_disabled()
    page.locator("#hf-settings-save").click()
    expect(page.locator("#hf-settings-status")).to_have_text("Token saved")
    expect(page.locator("#hf-settings-token")).to_have_value("")
    expect(page.locator("#hf-settings-result")).to_contain_text("Activity")
    assert inert_app.server.config.app.state.credentials.get(HF_SCOPE) == "hf_SYNTHETIC_BROWSER"
    page.locator("#hf-settings-check").click()
    expect(page.locator("#hf-settings-check")).to_be_enabled()
    assert next(body for url, body in writes if url.endswith("/huggingface-settings/check")) == {
        "hf_token": None,
        "remember": True,
    }
    assert "hf_SYNTHETIC_BROWSER" not in page.content()
    page.locator("#hf-settings-activity").click()
    expect(page.locator("[data-hf-setup]")).to_have_count(1)
    expect(page.locator("[data-hf-setup]")).to_contain_text("queued")
    assert not inert_app.server.config.app.state.store.all("SELECT id FROM jobs")
