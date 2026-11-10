"""ADR 0059 layout: Processing settings, one-click actions, sidebar actions, Versions."""

import json
import time
import wave

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def runtime(gpu=False):
    return {
        "ready": True,
        "version": "3.8.6",
        "expected_version": "3.8.6",
        "gpu": {"checked": True, "available": gpu, "devices": []},
        "note": "Synthetic runtime check.",
    }


def reviewed(store, name="Interview"):
    project = store.create_project(name)
    recording = store.create_recording(project["id"], "interview.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    words = [{"word": "Hej", "start": 0, "end": 1}, {"word": "der.", "start": 1, "end": 2}]
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "Hej der.", "start": 0, "end": 2, "words": words},
                {"text": "Godt at se dig.", "start": 2, "end": 5},
            ]
        ),
        {},
        "Word model",
        {"capability": "words"},
    )
    return project, rid, result


def test_actions_live_in_the_sidebar_and_versions_is_one_dialog(page, local_app):
    store = local_app.server.config.app.state.store
    project, rid, _ = reviewed(store, "Sidebar actions")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    sidebar = page.locator(".sidebar")
    for name in ("Research Report", "Export Settings", "Topics & Sentiments"):
        expect(sidebar.get_by_role("button", name=name)).to_be_visible()
    expect(page.locator("#sidebar-project-actions button")).to_have_text(
        ["▦ Research Report", "⇩ Export Settings", "◔ Topics & Sentiments"]
    )
    expect(page.locator(".library-toolbar #project-export-open")).to_have_count(0)
    expect(page.locator("#project-settings-open")).to_be_visible()
    expect(page.locator("#file-settings-open")).to_be_hidden()
    page.screenshot(path="test-results/sidebar-project-actions.png")
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#sidebar-recording-actions #export-open")).to_be_visible()
    expect(page.locator("#sidebar-recording-actions #topics-open")).to_be_visible()
    expect(page.locator("#sidebar-project-actions")).to_be_hidden()
    expect(page.locator("#project-settings-open")).to_be_hidden()
    expect(page.locator("#file-settings-open")).to_be_visible()
    expect(page.locator("#enhance-open")).to_have_count(0)
    # Transcribing again sits below the result controls, above the transcript.
    row = page.locator("#transcribe-one").bounding_box()
    assert row["y"] > page.locator("#compare-result").bounding_box()["y"]
    page.screenshot(path="test-results/recording-layout.png")
    # One Versions… button; History and Results are its tabs.
    page.locator("#history-open").click()
    expect(page.locator("#history-dialog")).to_be_visible()
    page.locator("#results-manage").click()
    expect(page.locator("#results-dialog")).to_be_visible()
    expect(page.locator("#history-dialog")).to_be_hidden()
    page.locator("#versions-history-tab").click()
    expect(page.locator("#history-dialog")).to_be_visible()
    page.locator("#history-dialog [data-close]").first.click()
    # File Settings: the original, import and deletion of this recording.
    page.locator("#file-settings-open").click()
    expect(page.locator("#original-link")).to_have_attribute(
        "href",
        f"/api/recordings/{rid}/media/original"
        f"?cbs_instance={local_app.server.config.app.state.browser_instance}",
    )
    expect(page.locator("#import-transcript")).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#file-delete").click()
    expect(page.locator("#project-content")).to_be_visible()
    assert not store.all("SELECT id FROM recordings WHERE id=?", (rid,))


def test_processing_tab_checks_by_itself_and_saves_on_change(page, local_app, monkeypatch):
    calls = []
    writes = []
    store = local_app.server.config.app.state.store
    enhancement.save_local_settings(
        store, enhancement.ProcessingSettings(language="en", download_models=False)
    )

    def observe(route):
        if route.request.method == "PUT":
            writes.append(route.request.post_data_json)
        route.continue_()

    page.route("**/api/enhancement-settings", observe)

    def check(settings, **kw):
        calls.append(settings.runtime_python)
        result = runtime(gpu=False)
        if settings.runtime_python == "synthetic-newer-python":
            result["version"] = "synthetic-newer"
        return result

    monkeypatch.setattr(enhancement, "check_runtime", check)
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    status = page.locator("#processing-local-status")
    # No button press: opening the tab checks this computer once and reports the device.
    expect(status).to_contain_text("WhisperX 3.8.6 is ready on this computer")
    expect(status).to_contain_text("No CUDA GPU found")
    assert len(calls) == 1
    expect(page.locator("#enhance-device")).to_have_value("cpu")
    expect(page.locator("#enhance-device")).to_be_disabled()
    expect(page.locator("#rule-cuda-factor")).to_be_disabled()
    expect(page.locator("#enhance-rules")).to_be_visible()
    expect(page.locator("#enhance-python")).to_be_visible()
    page.locator("#enhance-downloads").check()
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    assert enhancement.local_settings(store).language == "en"
    assert writes[-1] == {"download_models": True}
    # A setting changed elsewhere while this page is open must not be overwritten.
    enhancement.save_local_settings(
        store,
        enhancement.ProcessingSettings(
            runtime_python="synthetic-newer-python",
            device="cuda",
            language="da",
            download_models=True,
        ),
    )
    page.locator("#enhance-downloads").uncheck()
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    saved = enhancement.local_settings(store)
    assert saved.language == "da" and saved.device == "cuda"
    assert saved.runtime_python == "synthetic-newer-python"
    assert writes[-1] == {"download_models": False}
    page.locator("#settings-dialog").screenshot(path="test-results/processing-settings.png")
    # Reopening checks the newly saved interpreter, rather than the stale one.
    page.get_by_role("tab", name="Hugging Face", exact=True).click()
    page.get_by_role("tab", name="Local Processing", exact=True).click()
    expect(page.locator("#enhance-downloads")).not_to_be_checked()
    expect(status).to_contain_text("synthetic-newer")
    assert calls[-1] == "synthetic-newer-python"


def test_re_transcribe_and_speaker_labels_start_with_one_click(page, local_app, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: runtime())
    monkeypatch.setattr(enhancement, "run_enhancement", lambda *a: None)
    store = local_app.server.config.app.state.store
    # Exercise cached speaker processing without supplying a token for model downloads.
    enhancement.save_local_settings(store, enhancement.ProcessingSettings(download_models=False))
    _, rid, _ = reviewed(store, "One click")
    directory = store.directory(rid)
    directory.mkdir(parents=True, exist_ok=True)
    with wave.open(str(directory / "original.wav"), "wb") as original:
        original.setnchannels(1)
        original.setsampwidth(2)
        original.setframerate(16000)
        original.writeframes(b"\x00\x00" * 16000 * 30)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-1 [data-action='retranscribe']").click()
    expect(page.locator("#toast")).to_contain_text("Re-transcribing 1 passage(s)")
    expect(page.locator("#enhancement-dialog")).to_be_hidden()
    [job] = store.all("SELECT params FROM jobs WHERE kind='enhance'")
    params = json.loads(job["params"])
    assert params["retranscription"] is True and params["segment_ids"] == ["1"]
    assert params["enhancement"]["whisper_model"] == "large-v3-turbo"
    assert params["routing"]["mode"] == "auto"
    store.execute("UPDATE jobs SET state='completed'")
    # No speaker labels yet: one tag asks once, then queues the whole recording.
    tag = page.locator("#transcript-tags #speaker-tag")
    expect(tag).to_have_text("Add speaker labels")
    page.once("dialog", lambda dialog: dialog.accept())
    tag.click()
    expect(page.locator("#toast")).to_contain_text("Queued")
    deadline = time.monotonic() + 15
    speakers = {}
    while time.monotonic() < deadline:
        rows = store.all("SELECT params FROM jobs WHERE kind='enhance' ORDER BY created")
        speakers = json.loads(rows[-1]["params"])
        if "input_intent" not in speakers:
            break
        time.sleep(0.05)
    assert "input_intent" not in speakers
    assert speakers["enhancement"]["diarize"] is True
    assert speakers["enhancement"]["execution"] == "local"  # Automatic, short recording.
    assert speakers["routing"]["mode"] == "auto"
    assert "input_acceptance" in speakers
