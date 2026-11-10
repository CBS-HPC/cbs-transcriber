"""Hardware-dependent controls retain preferences; UCloud groups retain exact IDs."""

import json

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_processing import runtime

from cbs_transcriber import enhancement
from cbs_transcriber.providers import Settings, save_profile

pytestmark = pytest.mark.browser


def test_processing_absent_unknown_and_available_gpu_preserve_preferences(
    page, local_app, monkeypatch
):
    store = local_app.server.config.app.state.store
    settings = enhancement.LocalSettings(device="cuda", language="en")
    store.execute(
        "INSERT OR REPLACE INTO settings(id,value) VALUES(3,?)", (settings.model_dump_json(),)
    )
    statuses = [runtime(False), {**runtime(), "gpu": {"checked": False}}, runtime(True)]
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: statuses.pop(0))
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    status = page.locator("#processing-local-status")
    expect(status).to_contain_text("No CUDA GPU found")
    expect(page.locator("#enhance-language")).to_have_count(0)
    expect(page.locator("#enhance-device")).to_be_disabled()
    expect(page.locator("#enhance-device")).to_have_value("cpu")
    expect(page.locator("#rule-cuda-factor")).to_be_disabled()
    page.locator("#enhance-downloads").check()
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    assert (
        json.loads(store.one("SELECT value FROM settings WHERE id=3")["value"])["device"] == "cuda"
    )
    assert enhancement.local_settings(store).language == "en"
    page.locator("#settings-dialog").screenshot(path="test-results/processing-no-gpu.png")
    page.locator("#enhance-check").click()
    expect(status).to_contain_text("GPU detection is unavailable")
    expect(status).not_to_contain_text("Automatic uses the CPU")
    expect(page.locator("#enhance-device")).to_be_enabled()
    expect(page.locator("#enhance-device")).to_have_value("cuda")
    expect(page.locator("#rule-cuda-factor")).to_be_enabled()
    page.locator("#enhance-check").click()
    expect(status).to_contain_text("GPU found")
    expect(page.locator("#enhance-device")).to_be_enabled()
    expect(page.locator("#enhance-device")).to_have_value("cuda")
    assert (
        json.loads(store.one("SELECT value FROM settings WHERE id=3")["value"])["device"] == "cuda"
    )


def test_ucloud_native_and_cbs_models_have_distinct_groups_with_stable_values(
    page, local_app, monkeypatch
):
    monkeypatch.setattr(
        "cbs_transcriber.enhancement_api.runtime_prepared", lambda *_: {"prepared": None}
    )
    app = local_app.server.config.app
    saved = save_profile(
        app.state.store,
        app.state.credentials,
        "Research connection",
        Settings(
            provider_type="ucloud",
            base_url="https://cloud.sdu.dk",
            model="large-v3",
            ucloud_transcriber={
                "project_id": "research",
                "storage_path": "/drive/research",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        api_key="synthetic",
        remember=False,
    )
    ids = {
        json.loads(row["value"])["model"]: row["id"]
        for row in app.state.store.all("SELECT id,value FROM providers")
        if json.loads(row["value"])["provider_type"] == "ucloud"
    }
    # Group enumeration opts in to every model; fresh presets intentionally hide most.
    for row in app.state.store.all("SELECT id,name,value FROM providers"):
        settings = Settings.model_validate_json(row["value"])
        if settings.provider_type == "ucloud":
            settings.enabled = True
            save_profile(
                app.state.store,
                app.state.credentials,
                row["name"],
                settings,
                identifier=row["id"],
            )
    project = app.state.store.create_project("Grouped choices")
    recording = app.state.store.create_recording(project["id"], "Grouped.wav")
    runtime_models = {
        "whisperx/large-v3",
        "whisperx/large-v3-turbo",
        "whisperx/edda-turbo",
        "whisperx/edda-large",
        "whisperx/roest-v3",
    }
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#bulk-provider-kind").select_option("ucloud")
    for selector in ("#bulk-provider",):
        expect(page.locator(selector + " optgroup")).to_have_count(2)
        expect(
            page.locator(selector + ' optgroup[label="UCloud Transcriber"] option')
        ).to_have_count(6)
        expect(
            page.locator(selector + ' optgroup[label="CBS Runtime / WhisperX"] option')
        ).to_have_count(len(runtime_models))
        assert set(
            page.locator(
                selector + ' optgroup[label="CBS Runtime / WhisperX"] option'
            ).evaluate_all("options => options.map(option => option.value)")
        ) == {ids[model] for model in runtime_models}
    page.locator("#bulk-provider").select_option(ids["whisperx/large-v3"])
    expect(page.locator("#bulk-provider")).to_have_value(ids["whisperx/large-v3"])
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#file-provider-kind")).to_have_value("ucloud")
    expect(
        page.locator('#file-provider optgroup[label="CBS Runtime / WhisperX"] option')
    ).to_have_count(len(runtime_models))
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator('#provider-model-list [role="group"]')).to_have_count(2)
    expect(
        page.locator('#provider-model-list [aria-label="UCloud Transcriber"] input')
    ).to_have_count(6)
    expect(
        page.locator('#provider-model-list [aria-label="CBS Runtime / WhisperX"] input')
    ).to_have_count(len(runtime_models))
    assert (
        set(
            page.locator(
                '#provider-model-list [aria-label="CBS Runtime / WhisperX"] input'
            ).evaluate_all("inputs => inputs.map(input => input.dataset.enabledModel)")
        )
        == runtime_models
    )
    expect(page.locator("#native-model-choice option")).to_have_count(6)
    expect(page.locator("#native-model-choice")).not_to_contain_text("WhisperX")
    page.locator("#settings-dialog").screenshot(path="test-results/ucloud-model-groups.png")
    assert (
        app.state.store.one("SELECT name FROM providers WHERE id=?", (saved["id"],))["name"]
        == "Research connection"
    )
