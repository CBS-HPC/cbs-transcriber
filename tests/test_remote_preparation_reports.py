"""Cross-branch gate: these use the real remote receipt validator when its module is present."""

import copy
import importlib.util

import pytest

from cbs_transcriber import research_reports

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("cbs_transcriber.remote_audio_input") is None,
    reason="The real remote preparation implementation is supplied by the remote stage.",
)


def actual_case(tmp_path, monkeypatch):
    import test_remote_audio_input

    from cbs_transcriber import remote_audio_input

    chosen, _, folder, _, _ = test_remote_audio_input.case.__wrapped__(tmp_path, monkeypatch)
    receipt = remote_audio_input.prepare(folder)
    source = chosen["audio_input"]
    return {
        "recording_id": source["recording_id"],
        "context": {
            "audio_input": source,
            "audio_preparations": [
                {
                    "schema": 1,
                    "policy": "original-v1",
                    "source": source,
                    "window": receipt["window"],
                    "prepared": receipt["prepared"],
                }
            ],
            "remote_audio_preparation": receipt,
        },
    }


def test_actual_remote_preparation_projection_is_not_labelled_local(tmp_path, monkeypatch):
    row = actual_case(tmp_path, monkeypatch)
    _, prepared = research_reports._audio_evidence(row)
    assert prepared[0]["observation"] == "remote_decoded_pcm"
    assert row["remote_preparation_projection_match"] is True
    assert prepared[0]["remote_decoder"] == row["remote_audio_preparation_evidence"]["decoder"]


def test_present_malformed_actual_remote_receipt_is_refused(tmp_path, monkeypatch):
    row = actual_case(tmp_path, monkeypatch)
    row["context"]["remote_audio_preparation"]["unexpected_claim"] = True
    with pytest.raises(ValueError):
        research_reports._audio_evidence(row)


def test_unmatched_standard_preparation_keeps_its_separate_observation(tmp_path, monkeypatch):
    row = actual_case(tmp_path, monkeypatch)
    row["context"]["audio_preparations"] = copy.deepcopy(row["context"]["audio_preparations"])
    row["context"]["audio_preparations"][0]["prepared"]["sha256"] = "0" * 64
    _, prepared = research_reports._audio_evidence(row)
    assert prepared[0]["observation"] == "local_decoded_pcm"
    assert row["remote_preparation_projection_match"] is False
    assert row["remote_audio_preparation_evidence"] is not None
