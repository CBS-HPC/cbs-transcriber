"""ADR 0069's standalone worker does not expose general Automatic processing."""

import json
import threading
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement, runtime_batch_remote
from cbs_transcriber import enhancement_worker as worker


@pytest.mark.parametrize("language", ["da", "en"])
def test_v123_processing_settings_reject_auto_without_mutation(client, language):
    saved = client.put("/api/enhancement-settings", json={"language": language, "device": "cpu"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["language"] == language
    store = client.app.state.store
    before = store.all("SELECT * FROM settings ORDER BY id")
    response = client.put("/api/enhancement-settings", json={"language": "auto"})
    assert response.status_code == 422
    assert client.get("/api/enhancement-settings").json() == saved.json()
    assert store.all("SELECT * FROM settings ORDER BY id") == before
    assert client.get("/api/jobs").json() == []


@pytest.mark.parametrize("language", ["da", "en"])
def test_v125_remote_admission_accepts_automatic_only_for_complete_full_asr(language):
    request = {
        "options": {
            "execution": "ucloud",
            "device": "cuda",
            "language": language,
            "align": True,
            "diarize": False,
            "allow_downloads": False,
            "duration": 3.0,
            "transcribe": True,
            "retranscribe": False,
            "whisper_model": "small",
            "whole_recording": False,
        },
        "passages": [],
    }
    expected = ["transcription.json", "alignment.json"]
    assert runtime_batch_remote.BatchController._valid_request(request, expected)
    automatic = request | {"options": request["options"] | {"language": "auto"}}
    assert runtime_batch_remote.BatchController._valid_request(automatic, expected)
    non_asr = automatic | {"options": automatic["options"] | {"transcribe": False}}
    assert not runtime_batch_remote.BatchController._valid_request(non_asr, ["alignment.json"])
    existing_source = automatic | {
        "passages": [{"id": "0", "text": "Existing words.", "start": 0.0, "end": 2.0}]
    }
    assert not runtime_batch_remote.BatchController._valid_request(existing_source, expected)
    assert request["options"]["language"] == language


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "language_scope",
            "Automatic language is not yet available for this processing operation.",
        ),
        ("language_model", "Automatic language requires a multilingual ASR model."),
        ("language_uncertain", "The ASR engine could not resolve Automatic language reliably."),
        (
            "language_unsupported",
            "The ASR engine reported a language without a supported Danish/English aligner.",
        ),
        ("language_no_speech", "The ASR engine reported no speech after filtering,"),
    ],
)
def test_v123_worker_language_failures_are_curated_and_keep_checkpoint(
    tmp_path, capfd, code, message
):
    stage = tmp_path / "transcription.json"
    raw = b'{"segments":[{"text":"PRIVATE_TRANSCRIPT","start":0,"end":1}]}'
    stage.write_bytes(raw)
    (tmp_path / "error.json").write_text(
        json.dumps(
            {
                "code": code,
                "message": "PRIVATE_UPSTREAM_BODY",
                "api_key": "PRIVATE_TOKEN",
                "path": "C:/PRIVATE_AUDIO.wav",
            }
        ),
        encoding="utf-8",
    )
    exited = SimpleNamespace(returncode=1, poll=lambda: 1, wait=lambda: 1)
    with pytest.raises(ValueError) as error:
        enhancement.monitor_worker(
            exited, tmp_path, enhancement.EnhancementOptions(), threading.Event(), lambda _: None
        )
    assert str(error.value).startswith(message)
    captured = capfd.readouterr()
    assert "PRIVATE_" not in str(error.value) + captured.out + captured.err
    assert stage.read_bytes() == raw
    assert not (tmp_path / "alignment.json").exists()
    assert not (tmp_path / "diarization.json").exists()


@pytest.mark.parametrize("retranscribe", [False, True])
def test_v123_parent_refuses_decisions_before_legacy_evidence_fallback(
    tmp_path, monkeypatch, retranscribe
):
    stage = tmp_path / ("retranscription.json" if retranscribe else "transcription.json")
    raw = b'{"language_decision":null,"passages":[],"segments":[]}'
    stage.write_bytes(raw)

    def refuse(*args, **kwargs):
        pytest.fail("Gated decisions must be refused before input/runtime inspection")

    monkeypatch.setattr(worker, "language_binding", refuse)
    monkeypatch.setattr(enhancement, "check_runtime", refuse)
    monkeypatch.setattr(worker.importlib.metadata, "version", refuse)
    options = enhancement.EnhancementOptions(retranscribe=retranscribe)
    with pytest.raises(
        ValueError, match="Automatic ASR language decisions.*new processing protocol"
    ):
        enhancement.asr_language_evidence(tmp_path, options)
    assert stage.read_bytes() == raw
    assert not (tmp_path / "request.json").exists()
    assert not (tmp_path / "audio.wav").exists()
