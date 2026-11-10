"""WhisperX is a UCloud model in the normal Transcribe controls (T70)."""

import json
import time

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement, enhancement_api, providers, runtime_compute

pytestmark = pytest.mark.browser


def test_ucloud_whisperx_model_transcribes_after_confirmed_preparation(
    page, local_app, monkeypatch
):
    app = local_app.server.config.app
    store, credentials = app.state.store, app.state.credentials
    enhancement.save_local_settings(
        store, enhancement.ProcessingSettings(prepare_ucloud_models=False)
    )
    providers.save_profile(
        store,
        credentials,
        "UCloud",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            language="da",
            ucloud_transcriber={
                "project_id": "synthetic-project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {
                    "id": "gpu-nvidia-b200-1-gpu",
                    "category": "gpu",
                    "provider": "ucloud",
                },
            },
        ),
        api_key="synthetic-cloud-token",
        remember=False,
    )
    # Runtime profiles are parked by default; this preparation test explicitly opts in.
    for row in store.all("SELECT id,name,value FROM providers"):
        settings = providers.Settings.model_validate_json(row["value"])
        if settings.model == "whisperx/large-v3":
            settings.enabled = True
            providers.save_profile(store, credentials, row["name"], settings, identifier=row["id"])
            break
    # No network in tests: the drive is "not prepared" and queued work is not run.
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *a: False)
    # This queue-only fixture retains singleton execution; batch metadata is covered separately.
    monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *_: None)
    monkeypatch.setattr(
        runtime_compute,
        "freeze",
        lambda *_: {
            "runtime_product": runtime_compute.MIG_PRODUCT,
            "runtime_compute_policy": runtime_compute.POLICY,
        },
    )
    monkeypatch.setattr(enhancement, "run_enhancement", lambda *a: None)
    project = store.create_project("WhisperX")
    recording = store.create_recording(project["id"], "Interview.wav")
    write_original_wav(store, recording)
    store.execute(
        "UPDATE recordings SET status='ready',duration=600 WHERE id=?", (recording["id"],)
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-provider-kind").select_option("ucloud")
    whisperx = next(
        p["id"]
        for p in json.loads(page.evaluate("JSON.stringify(state.providers)"))
        if p["model"] == "whisperx/large-v3"
    )
    expect(page.locator(f'#bulk-provider option[value="{whisperx}"]')).to_have_text(
        "WhisperX large-v3 (CBS Runtime)"
    )
    page.locator("#bulk-provider").select_option(whisperx)
    speakers = page.locator('#bulk-features [data-transcription-feature="speaker_labels"]')
    expect(speakers.locator("xpath=..")).to_have_attribute(
        "title", "Detected in the same UCloud WhisperX run"
    )
    prompts = []

    def answer(dialog):
        prompts.append(dialog.message)
        dialog.accept()

    page.on("dialog", answer)
    page.locator("#transcribe-selected").click()
    expect(page.locator("#toast")).to_contain_text("Added to the transcription queue")
    # No enhancement dialog: the WhisperX run itself provides word timing.
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    # Source inspection/preflight run after202; preparation always needs fresh explicit consent.
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()
    prepare = page.locator('#jobs [data-input-action="prepare"]')
    expect(prepare).to_be_visible(timeout=15000)
    assert len(prompts) == 1
    [pending] = store.all("SELECT params FROM jobs WHERE kind='enhance'")
    assert json.loads(pending["params"])["input_intent"]["error"]["code"] == "needs_confirmation"
    prepare.click()
    assert len(prompts) == 2 and "pinned UCloud runtime" in prompts[1]
    assert "1 frozen recording request" in prompts[1]
    deadline = time.monotonic() + 15
    params, job = {}, {}
    while time.monotonic() < deadline:
        rows = store.all("SELECT kind,params FROM jobs WHERE kind='enhance' ORDER BY created")
        if len(rows) == 2:
            job = rows[-1]
            params = json.loads(job["params"])
            if "input_intent" not in params:
                break
        time.sleep(0.05)
    assert len(rows) == 2 and "input_intent" not in params
    assert "input_acceptance" in params
    assert job["kind"] == "enhance" and params["transcription"] is True
    assert params["provider_id"] == whisperx
    assert params["ucloud_api"]["prepare_runtime"] is True
