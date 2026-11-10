import copy

from cbs_transcriber.enhancement import EnhancementOptions, merge_output
from cbs_transcriber.transcripts import normalize_segments


def test_v59_partial_alignment_retains_real_passage_replay_window():
    source = {
        "segments": normalize_segments(
            [{"text": "Hej med dig.", "start": 5, "end": 8, "speaker": "A"}]
        ),
        "speakers": {"A": "Reviewed speaker"},
    }
    before = copy.deepcopy(source)
    alignment = {
        "passages": [
            {
                "id": "0",
                "words": [
                    {"word": "Hej", "start": None, "end": None, "score": None},
                    {"word": "med", "start": 6, "end": 6.5, "score": 0.9},
                    {"word": "dig.", "start": 6.5, "end": 7.5, "score": 0.9},
                ],
            }
        ]
    }
    rows, speakers = merge_output(source, alignment, None, EnhancementOptions(), 10, "run")
    # Incomplete alignment must not remove the actual sentence replay interval.
    assert len(rows) == 1
    assert (rows[0]["start"], rows[0]["end"]) == (5, 8)
    assert rows[0]["text"] == source["segments"][0]["text"]
    assert not rows[0]["word_timing"]
    assert rows[0]["words"][0]["start"] is None
    assert rows[0]["words"][1]["start"] == 6
    assert source == before and speakers == source["speakers"]
