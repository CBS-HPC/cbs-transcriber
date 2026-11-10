"""Independent review regressions for conservative enhancement field adoption."""

from copy import deepcopy

from cbs_transcriber.transcript_merge import merge_payload
from cbs_transcriber.transcripts import normalize_segments


def test_unknown_incoming_speaker_keeps_known_unnamed_voice_and_adopts_valid_timing():
    base = {
        "segments": normalize_segments([{"text": "Yes", "start": 0, "end": 2, "speaker": "A"}]),
        "speakers": {},
    }
    incoming = {
        "segments": normalize_segments(
            [
                {
                    "text": "Yes",
                    "start": 0,
                    "end": 2,
                    "speaker": "UNKNOWN",
                    "words": [{"word": "Yes", "start": 0.2, "end": 1.2}],
                }
            ]
        ),
        "speakers": {},
    }
    merged = merge_payload(base, deepcopy(base), incoming, deepcopy(incoming))
    assert merged["segments"][0]["speaker"] == "A"
    assert merged["segments"][0]["words"] == incoming["segments"][0]["words"]
    assert merged["summary"]["timing_updates"] == 1
    assert merged["summary"]["speaker_updates"] == 0
