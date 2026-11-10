import copy
import json
import sys
import threading
from types import SimpleNamespace

import pytest
from admission_support import inspect_and_materialize, write_original_wav

from cbs_transcriber.enhancement import (
    EnhancementOptions,
    child_environment,
    merge_output,
    run_worker,
    validate_input,
)
from cbs_transcriber.enhancement_worker import align_passages
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import default_speaker_names, normalize_segments


def source_result():
    return {
        "id": "source-result",
        "speakers": {"OLD": "Reviewed person"},
        "segments": normalize_segments(
            [
                {"text": "🙂 Hej  København. Tak!", "start": 0, "end": 3, "speaker": "OLD"},
            ]
        ),
    }


def alignment():
    return {
        "passages": [
            {
                "id": "0",
                "words": [
                    {"word": "🙂", "start": None, "end": None, "score": None},
                    {"word": "Hej", "start": 0, "end": 0.6, "score": 0.9},
                    {"word": "København.", "start": 0.6, "end": 1.5, "score": 0.9},
                    {"word": "Tak!", "start": 1.5, "end": 3, "score": 0.9},
                ],
            }
        ]
    }


@pytest.fixture
def library_lease(client):
    with client.app.state.store.library_lock():
        yield


def test_v31_preserves_source_and_missing_timing_without_interpolation():
    source = source_result()
    before = copy.deepcopy(source)
    missing = alignment()
    missing["passages"][0]["words"][-1].update(start=None, end=None, score=None)
    rows, names = merge_output(source, missing, None, EnhancementOptions(), 3, "run")
    assert source == before
    assert rows[-1]["words"][-1]["start"] is None
    assert "".join(s["text"] for s in rows) == source["segments"][0]["text"]
    assert all(word["word"] != "🙂" for word in rows[0]["words"])
    assert len(rows) == 1 and not rows[0]["word_timing"]
    assert (rows[0]["start"], rows[0]["end"]) == (0, 3)
    raw = alignment()
    raw["passages"][0]["words"][2]["score"] = 0.01
    rows, _ = merge_output(source, raw, None, EnhancementOptions(), 3, "run")
    assert next(w for s in rows for w in s["words"] if w["word"] == "København.")["start"] is None
    assert names == source["speakers"]


def test_v31_diarization_splits_at_actual_words_and_keeps_characters():
    source = source_result()
    source["segments"][0]["text"] = "Hej  København. Tak!"
    raw = alignment()
    raw["passages"][0]["words"] = raw["passages"][0]["words"][1:]
    turns = {
        "turns": [
            {"start": 0, "end": 1.5, "speaker": "A"},
            {"start": 1.5, "end": 3, "speaker": "B"},
        ]
    }
    rows, names = merge_output(source, raw, turns, EnhancementOptions(diarize=True), 3, "run")
    assert "".join(s["text"] for s in rows) == source["segments"][0]["text"]
    assert len(rows) == 2 and rows[0]["speaker"] != rows[1]["speaker"]
    assert [s["id"] for s in rows] == ["0", "1"]
    assert all(s["word_timing"] for s in rows)
    # New speaker labels stay unnamed; the display default is Speaker_01, Speaker_02, ...
    assert rows[0]["speaker"] not in names
    assert default_speaker_names(rows, names)[rows[0]["speaker"]] == "Speaker_01"
    # Names of voices this result does not use are not inherited from the source.
    assert "OLD" not in names
    assert source["speakers"] == {"OLD": "Reviewed person"}


def test_v31_speaker_only_keeps_unlocated_words_and_marks_mixed_passage():
    source = source_result()
    source["segments"][0]["original_text"] = "Earlier imported wording."
    source["segments"][0]["edited"] = True
    turns = {
        "turns": [
            {"start": 0, "end": 1.5, "speaker": "A"},
            {"start": 1.5, "end": 3, "speaker": "B"},
        ]
    }
    rows, names = merge_output(
        source, None, turns, EnhancementOptions(align=False, diarize=True), 3, "run"
    )
    assert len(rows) == 1 and rows[0]["text"] == source["segments"][0]["text"]
    assert names[rows[0]["speaker"]] == "Multiple speakers (passage)"
    assert not rows[0]["word_timing"]
    assert rows[0]["original_text"] == source["segments"][0]["text"]
    assert source["segments"][0]["original_text"] == "Earlier imported wording."


@pytest.mark.parametrize("change", ["text", "outside", "ids"])
def test_v31_runtime_cannot_change_text_or_escape_intervals(change):
    raw = alignment()
    if change == "text":
        raw["passages"][0]["words"][1]["word"] = "Replaced"
    elif change == "outside":
        raw["passages"][0]["words"][1]["start"] = -5
    else:
        raw["passages"][0]["id"] = "another-result"
    with pytest.raises(ValueError):
        merge_output(source_result(), raw, None, EnhancementOptions(), 3, "run")


def test_v33_bounded_windows_and_untimed_short_text():
    source = source_result()
    assert not validate_input(source, EnhancementOptions(), 3)
    source["segments"][0].update(start=None, end=None)
    assert validate_input(source, EnhancementOptions(), 3)
    assert validate_input(source, EnhancementOptions(), 600)
    with pytest.raises(ValueError, match="Untimed text needs word alignment"):
        validate_input(source, EnhancementOptions(align=False, diarize=True), 600)
    with pytest.raises(ValueError):
        EnhancementOptions(align=False, diarize=False)


def test_v33_worker_disables_interpolation_and_maps_words_back():
    class Engine:
        @staticmethod
        def align(rows, *args, **kwargs):
            assert kwargs["interpolate_method"] == "ignore"
            assert rows[0]["text"] == "Hej København. Tak!"
            return {
                "word_segments": [
                    {"word": "Hej", "start": 0, "end": 0.6, "score": 0.9},
                    {"word": "København.", "start": 0.6, "end": 1.5, "score": 0.9},
                    {"word": "Tak!"},
                ]
            }

    source = [
        {"id": "a", "text": "Hej  København.", "start": None, "end": None},
        {"id": "b", "text": "Tak!", "start": None, "end": None},
    ]
    options = EnhancementOptions().model_dump() | {"duration": 3, "whole_recording": True}
    result = align_passages(Engine, None, None, [], source, options, lambda *args: None)
    assert [p["id"] for p in result["passages"]] == ["a", "b"]
    assert result["passages"][1]["words"][0]["start"] is None


def test_v32_child_environment_redacts_other_provider_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai")
    monkeypatch.setenv("UCLOUD_TOKEN", "private-control")
    environment = child_environment(False, "hf-synthetic")
    assert "OPENAI_API_KEY" not in environment and "UCLOUD_TOKEN" not in environment
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["PYANNOTE_METRICS_ENABLED"] == "0"
    assert environment["HF_TOKEN"] == "hf-synthetic"


def test_v31_local_job_snapshots_result_and_retry_keeps_media_ready(
    client, monkeypatch, wav_bytes, library_lease
):
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.check_runtime", lambda *args, **kw: {"ready": True}
    )
    store = client.app.state.store
    project = store.create_project("Enhance", "protected")
    recording = store.create_recording(project["id"], "audio.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    source = store.add_result(recording["id"], source_result()["segments"], {}, "Source", {})
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements",
        json={
            "result_id": source["id"],
            "revision": 1,
            "runtime_python": sys.executable,
        },
    )
    assert response.status_code == 202, response.text
    job = inspect_and_materialize(store, client.app.state.credentials, [response.json()["id"]])[0]
    frozen = copy.deepcopy(store.job(job["id"])["params"])
    assert frozen["enhancement"]["timing_policy"] == "retain-estimates"
    current = store.recording(recording["id"])
    current["segments"][0]["text"] = "Later user edit."
    store.save_transcript(recording["id"], current["segments"], {}, 1)
    assert store.job(job["id"])["params"] == frozen
    store.update_job(job["id"], state="failed")
    assert client.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 200
    assert store.recording(recording["id"])["status"] == "ready"
    folder = store.directory(recording["id"]) / "enhancements" / job["id"]
    folder.mkdir(parents=True)
    (folder / "alignment.json").write_text(json.dumps(alignment()), encoding="utf-8")
    # A complete checkpoint needs no subprocess or weights on retry.
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.run_worker", lambda *args: pytest.fail("reran stage")
    )
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    monkeypatch.setattr(
        "cbs_transcriber.enhancement.check_runtime",
        lambda *args, **kw: pytest.fail("checked runtime for completed stages"),
    )
    queue.process(job["id"])
    queue.process(job["id"])
    current = Store(store.root).recording(recording["id"])
    assert len(current["transcripts"]) == 2
    assert current["segments"][0]["text"] == "Later user edit."
    assert current["active_transcript_id"] == source["id"]
    enhanced = next(r for r in current["transcripts"] if r["id"] != source["id"])
    assert (
        store.result(recording["id"], enhanced["id"])["segments"][0]["text"]
        == frozen["source_snapshot"]["segments"][0]["text"]
    )


def test_v32_runtime_check_and_hf_settings_are_redacted(client):
    response = client.post(
        "/api/enhancement-runtime/check", json={"runtime_python": sys.executable}
    )
    assert response.status_code == 200 and response.json()["expected_version"] == "3.8.6"
    assert response.json()["ready"] is True
    assert set(response.json()["gpu"]) == {"checked", "available", "devices"}
    secret = "synthetic-hf-enhancement-key"
    response = client.put(
        "/api/enhancement-settings",
        json={
            "runtime_python": sys.executable,
            "hf_token": secret,
            "remember": False,
        },
    )
    assert response.status_code == 200 and response.json()["has_hf_token"]
    assert secret not in response.text
    assert secret.encode() not in client.app.state.store.db_path.read_bytes()
    assert client.get("/api/enhancement-settings").json()["runtime_python"] == sys.executable
    assert client.get("/api/jobs").json() == []


def test_v31_enqueue_rejects_stale_and_cross_recording_results(client, library_lease):
    store = client.app.state.store
    project = store.create_project("Scoped enhancement")
    first = store.create_recording(project["id"], "a.wav")
    other = store.create_recording(project["id"], "b.wav")
    for recording in (first, other):
        write_original_wav(store, recording)
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
    result = store.add_result(first["id"], source_result()["segments"], {}, "First", {})
    endpoint = f"/api/recordings/{other['id']}/enhancements"
    assert client.post(endpoint, json={"result_id": result["id"], "revision": 0}).status_code == 404
    endpoint = f"/api/recordings/{first['id']}/enhancements"
    assert client.post(endpoint, json={"result_id": result["id"], "revision": 0}).status_code == 409
    assert client.get("/api/jobs").json() == []


def test_v32_cancel_terminates_the_optional_process(tmp_path, monkeypatch):
    import subprocess

    from cbs_transcriber.media import Cancelled

    worker = tmp_path / "synthetic_worker.py"
    worker.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr("cbs_transcriber.enhancement.WORKER", worker)
    original = subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        assert kwargs.get("shell", False) is False
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("cbs_transcriber.enhancement.subprocess.Popen", launch)
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            run_worker(tmp_path, EnhancementOptions(), "", cancel, lambda *args: None)
    finally:
        timer.cancel()
    assert processes and all(p.poll() is not None for p in processes)


def test_v32_worker_errors_and_stdout_never_expose_token(tmp_path, monkeypatch, capfd):
    worker = tmp_path / "synthetic_error.py"
    worker.write_text(
        "import json,sys,os,pathlib\n"
        "print(os.environ['HF_TOKEN'])\n"
        "p=pathlib.Path(sys.argv[1])/'error.json'\n"
        "p.write_text(json.dumps({'code': os.environ['HF_TOKEN']}))\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cbs_transcriber.enhancement.WORKER", worker)
    with pytest.raises(ValueError, match="Local enhancement failed") as error:
        run_worker(
            tmp_path,
            EnhancementOptions(),
            "synthetic-hf-secret",
            threading.Event(),
            lambda *a: None,
        )
    assert "synthetic-hf-secret" not in str(error.value)
    captured = capfd.readouterr()
    assert "synthetic-hf-secret" not in captured.out + captured.err


def test_v32_worker_retry_reuses_completed_alignment_stage(tmp_path, monkeypatch):
    from cbs_transcriber import enhancement_worker as worker

    calls = []
    fail = [True]

    def align(*args, **kwargs):
        calls.append("align")
        assert kwargs["interpolate_method"] == "ignore"
        return {"word_segments": [{"word": "Hej", "start": 0, "end": 1, "score": 0.9}]}

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            calls.append("speakers")
            if fail[0]:
                raise RuntimeError("synthetic failed speaker stage")
            return SimpleNamespace(
                itertuples=lambda: iter([SimpleNamespace(start=0, end=1, speaker="A")])
            )

    def snapshot(repo, **kwargs):
        assert kwargs["local_files_only"]
        assert len(kwargs["revision"]) == 40
        return "cached-model"

    modules = {
        "torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
        "whisperx": SimpleNamespace(align=align, load_align_model=lambda *a, **kw: (None, {})),
        "whisperx.diarize": SimpleNamespace(DiarizationPipeline=Pipeline),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        "nltk": SimpleNamespace(data=SimpleNamespace(load=lambda *a: None)),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(worker, "audio_data", lambda folder: [])
    request = {
        "options": EnhancementOptions(diarize=True).model_dump() | {"duration": 1},
        "passages": [{"id": "0", "start": 0, "end": 1, "text": "Hej"}],
    }
    with pytest.raises(RuntimeError):
        worker.execute(request, tmp_path)
    assert (tmp_path / "alignment.json").exists()
    fail[0] = False
    assert worker.execute(request, tmp_path) is None
    assert calls == ["align", "speakers", "speakers"]
    assert (tmp_path / "diarization.json").exists()
