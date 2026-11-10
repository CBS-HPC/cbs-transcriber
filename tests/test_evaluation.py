import json
import runpy
import threading
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app
from cbs_transcriber.evaluation import (
    edit_distance,
    load_suite,
    normalize,
    read_predictions,
    score,
)
from cbs_transcriber.providers import OPENAI_URL

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "fixtures" / "fleurs_da"
RUNNER = runpy.run_path(str(ROOT / "scripts" / "evaluate_fleurs.py"))


def small_suite():
    return {
        "dataset": "synthetic",
        "revision": "test",
        "clips": [{"id": "one", "text": "a b c"}, {"id": "two", "text": "d"}],
    }


def test_v11_metrics_unicode_empty_output_and_corpus_weighting():
    assert normalize("  ÆØA\u030a, grøn—BLÅ!\n") == "æøå grøn blå"
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance([], ["hallucination"]) == 1
    result = score(small_suite(), {"one": "a b c", "two": ""})
    assert result["wer"] == 1 / 4  # Not the unweighted average of clip scores (1/2).
    assert result["cer"] == 1 / 6  # Five characters including spaces, plus one.
    assert result["totals"]["word_errors"] == 1
    assert result["coverage"] == {"expected": 2, "scored": 2, "complete": True}
    assert score(small_suite(), {"one": "a b c", "two": "d x y"})["clips"][1]["wer"] == 2
    assert score(small_suite(), {"one": "", "two": ""})["wer"] == 1


@pytest.mark.parametrize(
    "predictions", [{"one": "a"}, {"one": "a", "two": "d", "extra": ""}, {"one": None, "two": "d"}]
)
def test_v11_missing_unknown_or_invalid_predictions_fail(predictions):
    with pytest.raises(ValueError):
        score(small_suite(), predictions)


def test_v11_duplicate_predictions_fail(tmp_path):
    path = tmp_path / "predictions.json"
    path.write_text('{"one": "a", "one": "b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        read_predictions(path)


def test_v10_real_fixtures_are_pinned_decodable_and_complete(tmp_path):
    manifest = load_suite(SUITE)
    assert len(manifest["revision"]) == 40
    assert manifest["license"] == "CC-BY-4.0"
    assert [c["id"] for c in manifest["clips"]] == [f"da_dk_{i:04d}" for i in range(5)]
    for clip in manifest["clips"]:
        with wave.open(str(SUITE / clip["file"])) as audio:
            assert audio.getnchannels() == 1
            assert audio.getframerate() == 16000
            assert audio.getnframes() / audio.getframerate() == clip["duration_seconds"]
    damaged = tmp_path / "fixtures"
    damaged.mkdir()
    (damaged / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (damaged / manifest["clips"][0]["file"]).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_suite(damaged)
    # Duplicate IDs are rejected before any duplicate can silently overwrite a reference.
    (damaged / manifest["clips"][0]["file"]).write_bytes(
        (SUITE / manifest["clips"][0]["file"]).read_bytes()
    )
    manifest["clips"] = [manifest["clips"][0], manifest["clips"][0]]
    (damaged / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_suite(damaged)


def test_v10_live_command_requires_explicit_opt_in_before_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Network must not be used without explicit opt-in")

    monkeypatch.setattr("httpx.Client", forbidden)
    with pytest.raises(SystemExit) as error:
        RUNNER["main"](["run", "--output", str(tmp_path / "run")])
    assert error.value.code == 2
    assert not (tmp_path / "run").exists()


def test_v11_offline_cli_scores_saved_predictions_without_network(monkeypatch, tmp_path):
    manifest = load_suite(SUITE)
    predictions = {clip["id"]: "" for clip in manifest["clips"]}
    path, output = tmp_path / "predictions.json", tmp_path / "report.json"
    path.write_text(json.dumps(predictions), encoding="utf-8")
    monkeypatch.setattr("httpx.Client", lambda *a, **k: pytest.fail("Offline scoring used HTTP"))
    assert RUNNER["main"](["score", str(path), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["wer"] == report["cer"] == 1
    assert report["coverage"]["scored"] == 5
    assert RUNNER["main"](["score", str(path), "--output", str(output)]) == 1


def test_v10_fleurs_through_app_queue_playback_persistence_export(
    tmp_path, credentials, monkeypatch
):
    manifest = load_suite(SUITE)
    answers = {Path(clip["file"]).name: clip for clip in manifest["clips"]}
    calls = []
    later_called = threading.Event()

    def fake_provider(path, settings, key):
        assert path.exists() and path.stat().st_size > 0
        assert key == "synthetic-test-key"
        expected_ids = {
            row["id"]
            for row in app.state.store.all("SELECT id,filename FROM recordings")
            if row["filename"] in answers
        }
        matches = expected_ids & {parent.name for parent in path.parents}
        assert len(matches) == 1
        recording = app.state.store.recording(matches.pop())
        clip = answers[recording["filename"]]
        if clip["id"] == manifest["clips"][0]["id"]:
            assert later_called.wait(20), "Expected another recording to run concurrently."
        calls.append(clip["id"])
        if clip["id"] != manifest["clips"][0]["id"]:
            later_called.set()
        text = clip["text"]
        if clip["id"] == manifest["clips"][0]["id"]:
            text = "fejl " + text.split(" ", 1)[1]
        return {"text": text}  # Deliberate test error, not measured speech recognition.

    monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", fake_provider)
    credentials.keys[OPENAI_URL] = "synthetic-test-key"
    data = tmp_path / "library"
    app = create_app(data, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        report = RUNNER["run_app_suite"](
            client, SUITE, manifest, tmp_path / "run", timeout=30, poll_interval=0.02
        )
        assert sorted(calls) == sorted(clip["id"] for clip in manifest["clips"])
        assert report["totals"]["word_errors"] == 1
        assert "synthetic-test-key" not in json.dumps(report)
        assert report["run"]["status"] == "completed"
        for clip in manifest["clips"]:
            identifier = report["run"]["recordings"][clip["id"]]
            recording = client.get(f"/api/recordings/{identifier}").json()
            assert not recording["segments"][0]["word_timing"]
            assert recording["segments"][0]["start"] is None
            media = client.get(
                f"/api/recordings/{identifier}/media/audio", headers={"Range": "bytes=0-99"}
            )
            assert media.status_code == 206
            exported = (tmp_path / "run" / f"{clip['id']}.nvivo.txt").read_text(encoding="utf-8")
            assert exported.startswith("INTERVIEW\n")
            prediction = next(c["prediction"] for c in report["clips"] if c["id"] == clip["id"])
            assert prediction in exported
    restored = create_app(data, start_worker=False, credentials=credentials)
    for identifier in report["run"]["recordings"].values():
        assert restored.state.store.recording(identifier)["segments"]


def test_v11_failed_job_leaves_incomplete_run_without_score(client, tmp_path, monkeypatch):
    manifest = load_suite(SUITE)
    wait = RUNNER["wait_for_jobs"]

    def fail_media(client, identifiers, timeout, poll_interval):
        client.app.state.store.update_job(identifiers[0], state="failed")
        wait(client, identifiers, timeout, poll_interval)

    # The fixture has no worker: exercise a real failed job without any provider call.
    monkeypatch.setitem(
        RUNNER["run_app_suite"].__globals__,
        "wait_for_jobs",
        fail_media,
    )
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="stopped"):
        RUNNER["run_app_suite"](client, SUITE, manifest, output)
    assert json.loads((output / "run.json").read_text())["status"] == "incomplete"
    assert not (output / "report.json").exists()


def test_v11_timeout_does_not_turn_pending_jobs_into_a_score():
    def pending(request):
        return httpx.Response(200, json=[{"id": "pending", "state": "running"}])

    with httpx.Client(
        transport=httpx.MockTransport(pending), base_url="http://localhost"
    ) as client:
        with pytest.raises(ValueError, match="timed out"):
            RUNNER["wait_for_jobs"](client, ["pending"], 0, 0)
