"""Standing UCloud preparation consent and one up-front confirmation per selection (ADR 0098)."""

import json
import time

import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_processing import runtime

from cbs_transcriber import (
    enhancement,
    enhancement_api,
    input_admission,
    providers,
    runtime_compute,
)

pytestmark = pytest.mark.browser


def test_processing_offers_standing_ucloud_preparation_consent(page, local_app, monkeypatch):
    store = local_app.server.config.app.state.store
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: runtime())
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    box = page.locator("#enhance-prepare-ucloud")
    expect(box).to_be_visible()
    expect(box).to_be_checked()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    expect(page.locator("#enhance-downloads")).to_be_checked()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    note = page.locator("#enhance-cloud-check-help")
    expect(note).to_contain_text("several GB")
    expect(note).to_contain_text("UCloud charges")
    expect(note).to_contain_text("No recording is uploaded until ready")
    expect(note).to_contain_text("Hugging Face token stays on this computer")
    box.uncheck()
    expect(page.locator("#enhance-cloud-save-status")).to_have_text("Saved for new jobs.")
    saved = json.loads(store.one("SELECT value FROM settings WHERE id=3")["value"])
    assert saved["prepare_ucloud_models"] is False
    # Another setting saved later keeps the explicitly disabled standing choice.
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    page.locator("#enhance-downloads").uncheck()
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#enhance-prepare-ucloud")).not_to_be_checked()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    expect(page.locator("#enhance-downloads")).not_to_be_checked()
    saved = json.loads(store.one("SELECT value FROM settings WHERE id=3")["value"])
    assert saved["prepare_ucloud_models"] is False and saved["download_models"] is False
    page.locator("#settings-dialog").screenshot(path="test-results/processing-ucloud-consent.png")


def test_ucloud_consent_save_preserves_newer_other_settings(page, local_app):
    store = local_app.server.config.app.state.store
    enhancement.save_local_settings(
        store,
        enhancement.ProcessingSettings(
            language="en", device="cpu", download_models=False, prepare_ucloud_models=True
        ),
    )
    pending = []
    page.evaluate("() => { window.consentWriteHeld = false; }")

    def settings(route):
        if route.request.method == "PUT":
            pending.append(route)
            page.evaluate("() => { window.consentWriteHeld = true; }")
        else:
            route.continue_()

    page.route("**/api/enhancement-settings", settings)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    box = page.locator("#enhance-prepare-ucloud")
    expect(box).to_be_checked()
    box.uncheck()
    page.wait_for_function("() => window.consentWriteHeld === true")
    [write] = pending
    assert write.request.post_data_json == {"prepare_ucloud_models": False}

    # Simulate an independent window saving newer processing settings first.
    enhancement.save_local_settings(
        store,
        enhancement.ProcessingSettings(
            runtime_python="synthetic-newer-python",
            language="da",
            device="cuda",
            download_models=True,
            prepare_ucloud_models=True,
        ),
    )
    write.fulfill(response=write.fetch())
    expect(page.locator("#enhance-cloud-save-status")).to_have_text("Saved for new jobs.")
    saved = enhancement.local_settings(store)
    assert saved.language == "da" and saved.device == "cuda"
    assert saved.runtime_python == "synthetic-newer-python" and saved.download_models is True
    assert saved.prepare_ucloud_models is False
    assert not store.all("SELECT id FROM jobs")


def test_transcribe_selected_asks_once_for_the_whole_selection(page, local_app, monkeypatch):
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
    # Røst is parked by default; this explicit consent scenario opts in to that model.
    for row in store.all("SELECT id,name,value FROM providers"):
        settings = providers.Settings.model_validate_json(row["value"])
        if settings.model == "whisperx/roest-v3":
            settings.enabled = True
            providers.save_profile(store, credentials, row["name"], settings, identifier=row["id"])
            break
    probes, accepted = [], []

    def states(credentials, targets, **_):
        probes.append(len(targets))
        return dict.fromkeys(targets, {"ready": False, "speakers": None})

    original_accept = input_admission.accept

    def accept(store, items, **kwargs):
        accepted.append([item["choice"]["prepare_consent"] for item in items])
        return original_accept(store, items, **kwargs)

    monkeypatch.setattr(enhancement_api, "preparation_states", states)
    monkeypatch.setattr(input_admission, "accept", accept)
    monkeypatch.setattr(
        enhancement_api, "whisperx_ready", lambda *a: pytest.fail("Consent was frozen")
    )
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
    project = store.create_project("Røst selection")
    for name in ("One.wav", "Two.wav", "Three.wav"):
        recording = store.create_recording(project["id"], name)
        write_original_wav(store, recording)
        store.execute(
            "UPDATE recordings SET status='ready',duration=600 WHERE id=?", (recording["id"],)
        )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-provider-kind").select_option("ucloud")
    roest = next(
        p["id"]
        for p in json.loads(page.evaluate("JSON.stringify(state.providers)"))
        if p["model"] == "whisperx/roest-v3"
    )
    page.locator("#bulk-provider").select_option(roest)
    prompts = []

    def answer(dialog):
        prompts.append(dialog.message)
        dialog.accept()

    page.on("dialog", answer)
    page.locator("#transcribe-selected").click()
    expect(page.locator("#toast")).to_contain_text("Added to the transcription queue")
    # The usual send confirmation, then exactly one preparation question for all files.
    assert len(prompts) == 2, prompts
    assert "UCloud Røst v3 (CBS Runtime)" in prompts[1] and "3 recordings" in prompts[1]
    assert "No recording is uploaded until the runtime is ready" in prompts[1]
    assert probes == [1] and accepted == [["request"] * 3]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        rows = [
            json.loads(row["params"])
            for row in store.all("SELECT params FROM jobs WHERE kind='enhance'")
        ]
        if len(rows) == 3 and all("input_intent" not in params for params in rows):
            break
        time.sleep(0.05)
    assert len(rows) == 3 and all("input_acceptance" in params for params in rows)
    assert all(params["ucloud_api"]["prepare_runtime"] is True for params in rows)
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()
    expect(page.locator('#jobs [data-input-action="prepare"]')).to_have_count(0)
    assert len(prompts) == 2
