"""Visible single/bulk feature choices reuse durable ASR → enhancement jobs."""

import copy
import json
import time

import httpx
import pytest
from admission_support import write_original_wav
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement, enhancement_api, providers, runtime_compute
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


@pytest.mark.parametrize(
    ("view", "capability", "extra", "execution"),
    [
        ("editor", "words", "speaker_labels", "local"),
        ("editor", "speakers", "word_timing", "local"),
        ("selected", "json", "speaker_labels", "local"),
        ("selected", "json", "speaker_labels", "ucloud"),
        ("selected", "json", "speaker_labels", "auto"),
        ("editor", "words", None, "local"),
        ("editor", "words_speakers", None, "local"),
    ],
)
def test_transcribe_controls_chain_only_missing_features(
    page, local_app, monkeypatch, view, capability, extra, execution
):
    calls = []
    requested = {"word_timing": True, "speaker_labels": bool(extra)}
    if capability in {"speakers", "words_speakers"}:
        requested["speaker_labels"] = True

    def transcribe(*args):
        calls.append("asr")
        result = {"text": "Hej CBS."}
        if capability != "json":
            result["segments"] = [{"text": "Hej CBS.", "start": 0, "end": 3}]
        if capability in {"words", "words_speakers"}:
            result["words"] = [
                {"word": "Hej", "start": 0, "end": 1},
                {"word": "CBS", "start": 1, "end": 3},
            ]
        if capability in {"speakers", "words_speakers"}:
            result["segments"][0]["speaker"] = "A"
        return result

    def enhance(worker, job, recording):
        options = job["params"]["enhancement"]
        assert options["align"] == (capability != "words")
        assert options["diarize"] == (capability != "speakers")
        # Automatic routing freezes a concrete place; these short recordings stay local.
        assert options["execution"] == ("local" if execution == "auto" else execution)
        if execution == "auto":
            assert "recording (up to" in job["params"]["routing"]["reason"]
        assert not options["allow_downloads"]
        if execution == "ucloud":
            frozen = job["params"]["ucloud_api"]["settings"]["ucloud_transcriber"]
            assert frozen["project_id"] == "synthetic-project"
            assert "cloud_settings" not in job["params"]
        source = job["params"]["source_snapshot"]
        assert source["segments"][0]["text"] == "Hej CBS."
        assert source["id"] in {r["id"] for r in worker.store.results(recording["id"])}
        calls.append("enhance")
        segments = copy.deepcopy(source["segments"])
        segments[0].update(
            start=0,
            end=3,
            speaker="A",
            words=[
                {"word": "Hej", "start": 0, "end": 1},
                {"word": "CBS.", "start": 1, "end": 3},
            ],
        )
        worker.store.add_result(
            recording["id"],
            normalize_segments(segments),
            {"A": "Speaker A"},
            "Synthetic enhancement",
            {"kind": "enhancement", "source_result_id": source["id"]},
            job_id=job["id"],
        )

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    monkeypatch.setattr(enhancement, "run_enhancement", enhance)
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = local_app.server.config.app.state.store
    # This feature-routing fixture uses cached models and no Hugging Face token.
    # Fresh Processing settings permit downloads; keep this test's opt-out explicit.
    enhancement.save_local_settings(store, enhancement.ProcessingSettings(download_models=False))
    if execution == "ucloud":
        # The synthetic enhancer writes its own result through the legacy singleton path.
        monkeypatch.setattr("cbs_transcriber.runtime_batches.freeze_plan", lambda *_: None)
        monkeypatch.setattr(
            runtime_compute,
            "freeze",
            lambda *_: {
                "runtime_product": runtime_compute.MIG_PRODUCT,
                "runtime_compute_policy": runtime_compute.POLICY,
            },
        )
        # This test uses a prepared cloud runtime; consent has separate browser coverage.
        monkeypatch.setattr(enhancement_api, "runtime_stamped", lambda *a: True)
        monkeypatch.setattr(enhancement_api, "speaker_model_available", lambda *a: True)
        # UCloud enhancement reuses the Provider Settings connection (API only, no SSH).
        providers.save_profile(
            store,
            local_app.server.config.app.state.credentials,
            "UCloud",
            providers.Settings(
                provider_type="ucloud",
                base_url="https://cloud.invalid",
                model="large-v3",
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
    project = store.create_project("Feature controls")
    recordings = [
        store.create_recording(project["id"], name)
        for name in (["One.wav", "Two.wav"] if view == "selected" else ["One.wav"])
    ]
    for recording in recordings:
        write_original_wav(store, recording)
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
    with httpx.Client(base_url=local_app.url, headers={"X-CBS-Request": "1"}) as client:
        response = client.post(
            "/api/providers",
            json={
                "name": "Synthetic " + capability,
                "provider_type": "openai",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "synthetic-model",
                "capability": capability,
            },
        )
        assert response.status_code == 201
        provider_id = response.json()["id"]

    rid = recordings[0]["id"]
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    try:
        expect(page.locator("#project-title")).to_have_text("Feature controls")
    except AssertionError:
        pytest.fail(f"App errors: {errors}; page: {page.locator('body').inner_text()[:3000]}")
    if view == "editor":
        page.locator(f'[data-open="{rid}"]').click()
        page.locator("#file-provider-kind").select_option("openai")
        provider = page.locator("#file-provider")
        controls = page.locator("#file-features")
        start = page.locator("#transcribe-one")
    else:
        page.locator("#select-all").click()
        page.locator("#bulk-provider-kind").select_option("openai")
        provider = page.locator("#bulk-provider")
        controls = page.locator("#bulk-features")
        start = page.locator("#transcribe-selected")
    provider.select_option(provider_id)
    word = controls.locator('[data-transcription-feature="word_timing"]')
    speaker = controls.locator('[data-transcription-feature="speaker_labels"]')
    for checkbox, native in [
        (word, capability in {"words", "words_speakers"}),
        (speaker, capability in {"speakers", "words_speakers"}),
    ]:
        expect(checkbox).to_be_checked(checked=native)
        if native:
            expect(checkbox).to_be_disabled()
        else:
            expect(checkbox).to_be_enabled()
    if extra:
        controls.locator(f'[data-transcription-feature="{extra}"]').check()
        expect(word).to_be_checked()
        expect(speaker).to_be_checked()
    if extra:
        start.click()
        expect(page.locator("#enhancement-dialog")).to_be_visible()
        expect(page.locator("#enhance-align")).to_be_checked()
        expect(page.locator("#enhance-diarize")).to_be_checked()
        if capability == "words":
            expect(page.locator("#enhance-align")).to_be_disabled()
        else:
            expect(page.locator("#enhance-align")).to_be_enabled()
        if capability == "speakers":
            expect(page.locator("#enhance-diarize")).to_be_disabled()
        else:
            expect(page.locator("#enhance-diarize")).to_be_enabled()
        expect(page.locator("#enhance-execution")).to_have_value("auto")
        # Machine settings live in Provider Settings → Processing; the dialog summarises them.
        expect(page.locator("#enhance-summary")).to_contain_text("Danish")
        expect(page.locator("#enhance-summary")).not_to_contain_text("download")
        expect(page.locator("#enhance-processing-settings")).to_be_visible()
        if execution != "auto":
            page.locator("#enhance-execution").select_option(execution)
        # No credential field is offered here, for any place.
        expect(page.locator("#enhancement-dialog input[type=password]")).to_have_count(0)
        assert calls == []
        start = page.locator("#enhance-start")
    page.once("dialog", lambda dialog: dialog.accept())
    with (
        page.expect_request("**/api/transcriptions") as submitted,
        page.expect_response("**/api/transcriptions") as accepted,
    ):
        start.click()
    assert accepted.value.status == 202, accepted.value.text()
    payload = json.loads(submitted.value.post_data)
    assert payload["provider_ids"] == [provider_id]
    assert set(payload["recording_ids"]) == {r["id"] for r in recordings}
    assert payload["requested_features"] == requested
    if extra:
        assert payload["enhancement"]["execution"] == execution
        expect(page.locator("#enhancement-dialog")).to_be_hidden()
    else:
        assert payload["enhancement"] is None
    expected_jobs = len(recordings) * (2 if extra else 1)
    # Original inspection, real FFmpeg preparation and the follow-up worker all run here.
    # Allow bounded loaded-machine latency, while surfacing terminal failures immediately.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        jobs = store.all("SELECT state FROM jobs WHERE kind IN ('transcribe','enhance')")
        if len(jobs) == expected_jobs and all(j["state"] == "completed" for j in jobs):
            break
        if any(j["state"] not in {"queued", "running", "completed"} for j in jobs):
            break
        time.sleep(0.05)
    assert len(jobs) == expected_jobs and all(j["state"] == "completed" for j in jobs), store.all(
        "SELECT kind,state,message FROM jobs"
    )
    inspections = store.all("SELECT state FROM jobs WHERE kind='source-inspect'")
    assert len(inspections) == len(recordings)
    assert all(job["state"] == "completed" for job in inspections)
    assert len(store.all("SELECT id FROM jobs")) == expected_jobs + len(inspections)
    assert sorted(calls) == sorted(
        ["asr"] * len(recordings) + ["enhance"] * len(recordings) * bool(extra)
    )
    for recording in recordings:
        assert len(store.results(recording["id"])) == (2 if extra else 1)
        current = store.recording(recording["id"])
        assert (
            store.result(recording["id"], current["active_transcript_id"])["source"]["kind"]
            == "provider"
        )
    page.screenshot(path=f"test-results/transcription-features-{view}-{execution}.png")
