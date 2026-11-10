"""The hidden clip worker cannot bypass public/remote gates or disclose runtime bodies."""

import json
import threading
from types import SimpleNamespace

import pytest
from test_auto_language_admission import protected_boundary as protected_boundary

from cbs_transcriber import enhancement, runtime_batch_remote


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("clip_language_model", "The cached clip-language detector is incompatible."),
        ("clip_language_uncertain", "Language could not be resolved for this audio clip."),
        (
            "clip_language_unsupported",
            "The clip detector reported a language without a supported Danish/English aligner.",
        ),
    ],
)
def test_v126_clip_errors_are_curated_preserve_decision_and_add_no_timing(
    tmp_path, capfd, code, message
):
    checkpoint = tmp_path / "alignment_language.json"
    raw = b'{"synthetic_checkpoint":"unchanged"}'
    checkpoint.write_bytes(raw)
    (tmp_path / "error.json").write_text(
        json.dumps({"code": code, "message": "PRIVATE_TEXT", "token": "PRIVATE_TOKEN"}),
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
    assert checkpoint.read_bytes() == raw
    assert not (tmp_path / "alignment.json").exists()


@pytest.mark.parametrize("transcribe", [False, True])
@pytest.mark.parametrize("endpoint", ["enhance", "transcribe"])
def test_v126_worker_marker_does_not_admit_public_auto_source_or_followup(
    client, protected_boundary, transcribe, endpoint
):
    rid, result_id = protected_boundary
    store = client.app.state.store
    before = store.recording(rid)
    options = {
        "operation": "bounded_alignment",
        "language": "auto",
        "execution": "local",
        "device": "cpu",
        "align": True,
        "diarize": False,
        "transcribe": transcribe,
        "retranscribe": False,
        "allow_downloads": False,
    }
    if endpoint == "enhance":
        url = f"/api/recordings/{rid}/enhancements"
        body = options | {"result_id": result_id, "revision": before["revision"]}
    else:
        url = "/api/transcriptions"
        body = {
            "operation": "bounded_alignment",
            "recording_ids": [rid],
            "replace": True,
            "enhancement": options,
        }
    response = client.post(url, json=body)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "Invalid request fields. Check the supplied values."}
    assert store.recording(rid) == before
    assert store.all("SELECT id FROM jobs") == []
    assert len(store.results(rid)) == 1


@pytest.mark.parametrize("operation", [None, "bounded_alignment"])
def test_v126_remote_never_accepts_bounded_auto_alignment(operation):
    request = {
        "options": {
            "execution": "ucloud",
            "device": "cuda",
            "language": "auto",
            "align": True,
            "diarize": False,
            "allow_downloads": False,
            "duration": 3.0,
            "transcribe": False,
            "retranscribe": False,
            "whisper_model": "small",
            "whole_recording": False,
        },
        "passages": [{"id": "unit", "text": "Synthetic.", "start": 0, "end": 3}],
    }
    if operation is not None:
        request["operation"] = operation
    assert not runtime_batch_remote.BatchController._valid_request(request, ["alignment.json"])
    if operation is not None:
        # The marker remains forbidden even when ordinary full-ASR options would be valid.
        full = request | {"passages": [], "options": request["options"] | {"transcribe": True}}
        expected = ["transcription.json", "alignment.json"]
        baseline = {key: value for key, value in full.items() if key != "operation"}
        assert runtime_batch_remote.BatchController._valid_request(baseline, expected)
        assert not runtime_batch_remote.BatchController._valid_request(full, expected)
