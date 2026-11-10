"""Review cached Automatic partial results without admitting jobs or running inference."""

import re
import shutil
import threading
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def cached_result(local_app, monkeypatch, status):
    app = local_app.server.config.app
    store = app.state.store

    def refuse(*args, **kwargs):
        pytest.fail("A complete cached Auto result must not check or launch inference")

    monkeypatch.setattr(enhancement, "require_local_runtime", refuse)
    monkeypatch.setattr(enhancement, "run_worker", refuse)
    with app.state.queue.lock:
        project = store.create_project("Synthetic Automatic review")
        recording = store.create_recording(project["id"], "Synthetic.wav")
        rid = recording["id"]
        store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
        baseline = store.add_result(
            rid,
            normalize_segments([{"text": "Reviewed source æøå.", "start": 0, "end": 3}]),
            {},
            "Reviewed source",
            {},
        )
        options = enhancement.EnhancementOptions(
            language="auto", transcribe=True, device="cpu", whisper_model="small"
        )
        job = store.add_job(
            rid, "enhance", {"enhancement": options.model_dump(), "transcription": True}
        )
        # Synthetic producer evidence must never be claimed by the running test workers.
        store.update_job(job["id"], state="completed", progress=1, message="Complete")
        directory = store.directory(rid)
        directory.mkdir(parents=True, exist_ok=True)
        ffmpeg(
            ["-f", "lavfi", "-i", "sine=frequency=330:duration=3", str(directory / "audio.mp3")],
            threading.Event(),
        )
        folder = directory / "enhancements" / job["id"]
        folder.mkdir(parents=True)
        ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=330:duration=3",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(folder / "audio.wav"),
            ],
            threading.Event(),
        )
        shutil.copyfile(folder / "audio.wav", directory / "original.wav")
        frozen = options.model_dump(exclude={"runtime_python", "prepare_runtime"}) | {
            "duration": 3.0,
            "whole_recording": False,
        }
        worker.write_json(folder / "request.json", {"passages": [], "options": frozen})
        text = '<img src=x onerror="window.autoXss=true"> Bonjour æøå.'
        rows = [{"text": text, "start": 0.2, "end": 2.8}] if status == "unsupported" else []
        decision = worker._asr_language_decision(
            worker.language_binding(folder),
            frozen,
            SimpleNamespace(
                language="fr" if rows else "en",
                language_probability=0.99,
                duration_after_vad=0.0 if status == "no_speech" else 2.0,
            ),
            len(rows),
        )
        assert decision["status"] == status
        outcome = {
            "schema": 1,
            "state": "unavailable_language",
            "decision_sha256": worker.decision_digest(decision),
        }
        worker.write_json(
            folder / "transcription.json", {"segments": rows, "language_decision": decision}
        )
        worker.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": str(index),
                        "words": [
                            {"word": token, "start": None, "end": None, "score": None}
                            for token in re.findall(r"\S+", row["text"])
                        ],
                    }
                    for index, row in enumerate(rows)
                ],
                "language_outcome": outcome,
            },
        )
        queue = SimpleNamespace(store=store, cancel_event=threading.Event())
        enhancement.run_transcription(queue, job, store.recording(rid), options)
        [produced] = store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
        result = store.result(rid, produced["id"])
        assert store.recording(rid)["active_transcript_id"] == baseline["id"]
    return store, rid, baseline, result, text


def test_v124_partial_auto_result_keeps_replay_without_word_highlighting(
    page, local_app, monkeypatch
):
    store, rid, baseline, result, text = cached_result(local_app, monkeypatch, "unsupported")
    assert result["source"]["capability"] == "segments"
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#result-select")).to_have_value(baseline["id"])
    page.locator("#result-select").select_option(result["id"])
    expect(page.locator("#result-select option:checked")).to_contain_text("word timing unavailable")
    expect(page.locator("#passage-0 .passage-text")).to_have_text(text)
    expect(page.locator("#passage-0 img")).to_have_count(0)
    expect(page.locator("#passage-0")).to_have_class(re.compile(r"\btiming-gap\b"))
    replay = page.locator("#passage-0 [data-action='replay']")
    expect(replay).to_be_enabled()
    replay.click()
    page.wait_for_function("() => document.querySelector('#audio').currentTime > 0.25")
    expect(page.locator("#passage-0")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#passage-0 .word.current")).to_have_count(0)
    page.evaluate("() => document.querySelector('#audio').pause()")
    assert page.evaluate("() => window.autoXss === true") is False
    assert store.result(rid, baseline["id"])["segments"][0]["text"] == "Reviewed source æøå."
    page.screenshot(path="test-results/automatic-partial-result.png")


@pytest.mark.parametrize("status", ["no_speech", "uncertain"])
def test_v124_empty_auto_result_has_no_invented_passage(page, local_app, monkeypatch, status):
    store, rid, baseline, result, _ = cached_result(local_app, monkeypatch, status)
    assert result["segments"] == [] and result["source"]["capability"] == "segments"
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#result-select").select_option(result["id"])
    expect(page.locator("#transcript .passage")).to_have_count(0)
    expect(page.locator("#transcript .transcript-empty")).to_contain_text(
        "No passages in this result."
    )
    label = "no speech detected" if status == "no_speech" else "no ASR text"
    expect(page.locator("#result-select option:checked")).to_contain_text(label)
    page.locator("#result-select").select_option(baseline["id"])
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Reviewed source æøå.")
    assert len(store.results(rid)) == 2
