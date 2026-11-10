"""Re-transcribing selected passages with WhisperX into one combined result (T71)."""

import json
import sys
import threading
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement, media
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber.history import processing_metadata
from cbs_transcriber.transcripts import normalize_segments


def word(text, start, end):
    return SimpleNamespace(word=text, start=start, end=end)


def test_worker_keeps_only_words_centred_inside_each_passage(tmp_path, monkeypatch):
    calls = []

    responses = [
        # Clip-relative times: 0.5 s of padding precede the passage starting at 10 s.
        [word(" tak", 0.0, 0.3), word(" Hej", 0.6, 1.0), word(" verden.", 1.1, 1.9)],
        # Only padding words for the second passage: nothing of its own was recognised.
        [word(" ja", 0.0, 0.4), word(" nej", 1.6, 1.9)],
    ]

    class FakeModel:
        def __init__(self, location, **kw):
            assert kw["local_files_only"] is True

        def transcribe(self, clip, **kw):
            calls.append(len(clip))
            assert kw["word_timestamps"] is True and kw["condition_on_previous_text"] is False
            words = responses[len(calls) - 1]
            return iter(
                [
                    SimpleNamespace(
                        avg_logprob=-0.6, no_speech_prob=0.02, compression_ratio=1.3, words=words
                    )
                ]
            ), None

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel)
    )
    (tmp_path / "snap").mkdir()
    (tmp_path / "snap" / "tokenizer.json").write_text("{}", encoding="utf-8")
    request = {
        "passages": [
            {"id": "4", "text": "", "start": 10.0, "end": 12.0},
            {"id": "7", "text": "", "start": 20.0, "end": 21.0},
        ]
    }
    options = {"whisper_model": "small", "device": "cpu", "language": "da", "duration": 30}
    with wave.open(str(tmp_path / "audio.wav"), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(16000)
        audio_file.writeframes(b"\x00\x00" * 16000 * 30)
    worker.write_json(tmp_path / "request.json", {**request, "options": options})

    def run():
        return worker.retranscribe_passages(
            [0.0] * 16000 * 30,
            request,
            options,
            tmp_path,
            lambda key: str(tmp_path / "snap"),
            lambda *a: None,
        )

    passages = run()
    # Passage 7 got only words outside its interval, so it is left out (kept unchanged later).
    assert passages == [{"id": "4", "text": "Hej verden.", "start": 10.0, "end": 12.0}]
    saved = json.loads((tmp_path / "retranscription.json").read_text(encoding="utf-8"))
    assert saved["passages"][0]["avg_logprob"] == -0.6
    assert calls == [int(12.5 * 16000) - int(9.5 * 16000), int(21.5 * 16000) - int(19.5 * 16000)]
    assert run() == passages and len(calls) == 2  # Checkpoint reused.


@pytest.fixture
def reviewed(client, monkeypatch):
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store = client.app.state.store
    project = store.create_project("Re-transcribe")
    recording = store.create_recording(project["id"], "a.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=120 WHERE id=?", (rid,))
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "Keep this.", "start": 0, "end": 5, "speaker": "A"},
                {"text": "Hej verdn.", "start": 10, "end": 12, "speaker": "B"},
                {"text": "Also flagged.", "start": 20, "end": 21, "speaker": "A"},
                {"text": "Untimed.", "start": None, "end": None, "speaker": "A"},
                {"text": "Too long.", "start": 30, "end": 95.0 - 4, "speaker": "A"},
            ]
        ),
        {"B": "Bodil"},
        "OpenAI",
        {},
    )
    return client, store, rid, result


def request(client, rid, result, ids, **extra):
    return client.post(
        f"/api/recordings/{rid}/retranscriptions",
        json={
            "result_id": result["id"],
            "revision": 1,
            "segment_ids": ids,
            "execution": "local",
            "whisper_model": "small",
            **extra,
        },
    )


def test_selection_must_have_real_bounded_audio(reviewed):
    client, store, rid, result = reviewed
    for ids, message in (
        (["4"], "known audio interval"),  # Untimed passages are ordered last.
        (["3"], "longer than 60 seconds"),
        (["missing"], "does not belong"),
        (["1", "1"], "only once"),
    ):
        response = request(client, rid, result, ids)
        assert response.status_code in {400, 422} and message in response.text, ids
    assert store.all("SELECT id FROM jobs") == []


def test_new_wording_replaces_only_selected_passages(reviewed, monkeypatch):
    client, store, rid, result = reviewed
    accepted_role = store.recording(rid)["result_roles"]
    response = request(client, rid, result, ["2", "1"])
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["params"]["working_role"] == accepted_role
    assert job["params"]["working_role_policy"] == "cbs-working-role-v1"
    # T87a leaves this multi-window transport legacy until T90 bounded bundles.
    assert "audio_input" not in job["params"]
    assert job["params"]["segment_ids"] == ["1", "2"]  # Frozen in transcript order.
    assert job["params"]["enhancement"]["retranscribe"] is True

    def run(folder, options, token, cancel, progress, **kw):
        sent = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        # Only audio bounds are sent; the reviewed wording never leaves the app.
        assert [p["text"] for p in sent["passages"]] == ["", ""]
        enhancement.write_json(
            folder / "retranscription.json",
            {
                "passages": [
                    {"id": "1", "text": "Hej verden.", "avg_logprob": -0.4},
                    {"id": "2", "text": ""},  # Nothing recognised: keep the original.
                ]
            },
        )
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": "1",
                        "words": [
                            {"word": "Hej", "start": 10.1, "end": 10.6, "score": 0.9},
                            {"word": "verden.", "start": 10.7, "end": 11.8, "score": 0.9},
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
    results = store.results(rid)
    assert len(results) == 2
    new = store.result(rid, results[-1]["id"])
    old = store.result(rid, result["id"])
    assert [s["text"] for s in new["segments"]] == [
        "Keep this.",
        "Hej verden.",
        "Also flagged.",
        "Too long.",
        "Untimed.",
    ]
    assert new["segments"][1]["speaker"] == "B" and new["segments"][1]["word_timing"] is True
    assert new["segments"][0] == old["segments"][0] and new["segments"][2] == old["segments"][2]
    assert new["speakers"] == {"B": "Bodil"}
    assert new["source"]["operation"] == "retranscription"
    assert new["source"]["unchanged_segment_ids"] == ["2"]
    assert "1 of 2 passages" in new["label"]
    metadata = processing_metadata(new["source"])
    assert metadata["scope"] == "passage_retranscription" and metadata["passage_count"] == 2
    hints = store.result_hints(rid, new["id"])["passages"]["1"]
    assert hints["avg_logprob"] == -0.4
    assert store.recording(rid)["active_transcript_id"] == result["id"]  # Reviewed kept.


def test_ucloud_needs_a_prepared_runtime_or_explicit_preparation(reviewed, monkeypatch):
    client, store, rid, result = reviewed
    from cbs_transcriber import enhancement_api

    client.put("/api/enhancement-settings", json={"prepare_ucloud_models": False})
    frozen = {"ucloud_api": {"provider_id": "u", "settings": {}, "runtime_id": "r"}}
    monkeypatch.setattr(enhancement_api, "connection", lambda *args: frozen["ucloud_api"])
    monkeypatch.setattr(
        enhancement_api,
        "freeze_profile",
        lambda store, credentials, provider, projects, options: (
            options.model_copy(update={"device": "cuda"}),
            frozen,
        ),
    )
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *a: False)
    # Asked once (ADR 0059): the app confirms, then repeats the request with preparation.
    refused = request(client, rid, result, ["1"], execution="ucloud")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "prepare_runtime"
    assert "lacks the" in refused.json()["detail"]["message"]
    accepted = request(client, rid, result, ["1"], execution="ucloud", prepare_runtime=True)
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["params"]["ucloud_api"] == frozen["ucloud_api"]


def test_standing_preparation_consent_answers_the_retranscription_question(reviewed, monkeypatch):
    # ADR 0098: "Prepare UCloud models automatically" applies to passage re-transcription too.
    client, store, rid, result = reviewed
    from cbs_transcriber import enhancement_api

    frozen = {"ucloud_api": {"provider_id": "u", "settings": {}, "runtime_id": "r"}}
    monkeypatch.setattr(enhancement_api, "connection", lambda *args: frozen["ucloud_api"])
    monkeypatch.setattr(
        enhancement_api,
        "freeze_profile",
        lambda store, credentials, provider, projects, options: (
            options.model_copy(update={"device": "cuda"}),
            frozen,
        ),
    )
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *a: False)
    saved = client.get("/api/enhancement-settings").json()
    response = client.put(
        "/api/enhancement-settings", json={**saved, "prepare_ucloud_models": True}
    )
    assert response.status_code == 200, response.text
    accepted = request(client, rid, result, ["1"], execution="ucloud")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["params"]["enhancement"]["prepare_runtime"] is True


def test_automatic_placement_cannot_gain_standing_preparation_after_routing(reviewed, monkeypatch):
    from cbs_transcriber import enhancement_api, routing

    client, store, rid, result = reviewed
    assert client.get("/api/enhancement-settings").json()["prepare_ucloud_models"] is True
    calls = []

    def local(options):
        calls.append("local")
        raise ValueError("Synthetic local runtime is unavailable.")

    def freeze(store, credentials, projects, options):
        calls.append("freeze")
        assert options.execution == "ucloud" and options.prepare_runtime is False
        return options, {"ucloud_api": {"provider_id": "u", "settings": {}, "runtime_id": "r"}}

    def prepared(*args):
        calls.append("runtime-ready")
        return True

    def models_ready(*args):
        calls.append("models-missing")
        return False

    monkeypatch.setattr(enhancement, "require_local_runtime", local)
    monkeypatch.setattr(enhancement_api, "freeze", freeze)
    monkeypatch.setattr(routing, "prepared", prepared)
    monkeypatch.setattr(enhancement_api, "whisperx_ready", models_ready)
    monkeypatch.setattr(enhancement_api.native, "client_for", pytest.fail)
    monkeypatch.setattr("huggingface_hub.snapshot_download", pytest.fail)
    response = request(client, rid, result, ["1"], execution="auto")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "prepare_runtime"
    assert calls == ["local", "freeze", "runtime-ready", "models-missing"]
    assert store.all("SELECT id FROM jobs") == []
