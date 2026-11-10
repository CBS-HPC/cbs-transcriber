"""Full WhisperX transcription stage (T70): worker rows, checkpoint reuse and host pipeline."""

import json
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

from cbs_transcriber import enhancement, media
from cbs_transcriber import enhancement_worker as worker


def segment(text, start, end, logprob=-0.2):
    return SimpleNamespace(
        text=text,
        start=start,
        end=end,
        avg_logprob=logprob,
        no_speech_prob=0.01,
        compression_ratio=1.1,
    )


def test_zero_length_segments_keep_their_text_without_invented_timing():
    rows = worker.transcription_rows(
        [
            segment(" Først ", 1.0, 1.0),  # No interval: carried into the next passage.
            segment(" Hej verden.", 1.0, 3.0, -0.4),
            segment(" igen", 3.0, 3.01),  # Joins the previous passage's text.
            segment("   ", 4.0, 5.0),
            segment(" Slut.", 5.0, 99.0),  # Clamped to the recording's duration.
        ],
        duration=6.0,
        progress=lambda *a: None,
    )
    assert [row["text"] for row in rows] == ["Først Hej verden. igen", "Slut."]
    assert (rows[0]["start"], rows[0]["end"]) == (1.0, 3.0)
    assert rows[1]["end"] == 6.0
    # Merged text no longer matches one model segment, so its metrics are dropped.
    assert "avg_logprob" not in rows[0] and rows[1]["avg_logprob"] == -0.2


def test_transcription_checkpoint_is_reused_and_needs_a_cached_tokenizer(
    tmp_path, monkeypatch, wav_bytes
):
    loaded = []

    class FakeModel:
        def __init__(self, location, **kw):
            assert kw["local_files_only"] is True and kw["compute_type"] == "float16"
            loaded.append(location)

        def transcribe(self, audio, **kw):
            assert kw["vad_filter"] is True and kw["language"] == "da"
            return iter([segment("Hej.", 0.0, 2.0)]), None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel)
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    options = {"whisper_model": "large-v3", "device": "cuda", "language": "da", "duration": 3}
    (tmp_path / "audio.wav").write_bytes(wav_bytes)
    worker.write_json(tmp_path / "request.json", {"passages": [], "options": options})
    keys = []

    def fake_snapshot(key):
        keys.append(key)
        return str(snapshot)

    def transcribe():
        return worker.transcribe_audio(None, options, tmp_path, fake_snapshot, lambda *a: None)

    assert transcribe() == "cache"  # No tokenizer: never let faster-whisper download one.
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    passages = transcribe()
    assert passages == [{"id": "0", "text": "Hej.", "start": 0.0, "end": 2.0}]
    assert keys == ["whisper-large-v3", "whisper-large-v3"] and len(loaded) == 1
    assert transcribe() == passages and len(loaded) == 1  # Checkpoint reused.
    assert json.loads((tmp_path / "transcription.json").read_text(encoding="utf-8"))


def test_local_transcription_job_appends_a_provider_result(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Local WhisperX")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    options = enhancement.EnhancementOptions(
        execution="local", transcribe=True, whisper_model="small", language="da"
    )
    job = store.add_job(
        rid,
        "enhance",
        {"enhancement": options.model_dump(), "transcription": True, "provider_name": "Local"},
    )

    def run(folder, options, token, cancel, progress, **kw):
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        assert request["passages"] == [] and request["options"]["transcribe"] is True
        enhancement.write_json(
            folder / "transcription.json",
            {"segments": [{"text": "Hej verden.", "start": 0.5, "end": 3.0, "avg_logprob": -0.9}]},
        )
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "0",
                        "words": [
                            {"word": "Hej", "start": 0.5, "end": 1.0, "score": 0.9},
                            {"word": "verden.", "start": 1.2, "end": 2.8, "score": 0.2},
                        ],
                    }
                ]
            },
        )

    monkeypatch.setattr(media, "ffmpeg", lambda args, cancel: Path(args[-1]).write_bytes(b"x"))
    monkeypatch.setattr(enhancement, "run_worker", run)
    queue = client.app.state.queue
    queue.cancel_event = threading.Event()
    queue.process(job["id"])
    [result] = store.results(rid)
    saved = store.result(rid, result["id"])
    assert saved["label"] == "Local WhisperX small"
    assert (
        saved["source"]["provider_type"] == "whisperx" and saved["source"]["execution"] == "local"
    )
    assert saved["segments"][0]["text"] == "Hej verden."
    # The low-scoring word stays untimed; passage bounds come from the transcription.
    assert [w["start"] for w in saved["segments"][0]["words"]] == [0.5, None]
    hints = store.result_hints(rid, result["id"])["passages"]["0"]
    assert hints["avg_logprob"] == -0.9 and hints["words"]
