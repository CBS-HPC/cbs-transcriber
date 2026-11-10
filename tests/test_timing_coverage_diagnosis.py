"""Synthetic reproduction of partial timing seen in the read-only live inspection.

These tests document frozen score filtering, not timing accuracy or a model fix.
No live library, inference engine or network is involved.
"""

import copy

from cbs_transcriber.enhancement import EnhancementOptions, merge_output
from cbs_transcriber.research_reports import _coverage
from cbs_transcriber.transcripts import normalize_segments


def fixture():
    text = "Hej Øresund vi tester ord og tider uden privat indhold."
    source = {
        "speakers": {},
        "segments": normalize_segments(
            [{"text": text, "start": index * 10, "end": (index + 1) * 10} for index in range(3)]
        ),
    }
    alignment = {
        "passages": [
            {
                "id": segment["id"],
                "words": [
                    {
                        "word": token,
                        "start": segment["start"] + index * 0.5,
                        "end": segment["start"] + index * 0.5 + 0.4,
                        "score": 0.299 if index == 4 else 0.9,
                    }
                    for index, token in enumerate(text.split())
                ],
            }
            for segment in source["segments"]
        ]
    }
    return source, alignment


def test_zero_complete_passages_can_retain_ninety_percent_word_timing():
    source, raw = fixture()
    original = copy.deepcopy((source, raw))
    rows, _ = merge_output(
        source, raw, None, EnhancementOptions(align=True, diarize=False, min_score=0.3), 30, "test"
    )
    assert len(rows) == 3
    assert sum(row["word_timing"] for row in rows) == 0
    words = [word for row in rows for word in row["words"]]
    assert len(words) == 30
    assert sum(word["start"] is not None for word in words) == 27
    assert all(row["words"][4]["start"] is None for row in rows)
    assert [row["text"] for row in rows] == [row["text"] for row in source["segments"]]
    assert [row["start"] for row in rows] == [0, 10, 20]
    assert (source, raw) == original
    coverage = _coverage({"segments": rows})
    assert coverage["complete_word_timing_passages"] == 0
    assert coverage["lexical_tokens"] == 30
    assert coverage["timed_lexical_tokens"] == 27


def test_frozen_cutoff_explains_nulls_without_missing_alignment_output():
    source, raw = fixture()
    assert all(
        word["start"] is not None and word["end"] > word["start"]
        for row in raw["passages"]
        for word in row["words"]
    )
    strict, _ = merge_output(source, raw, None, EnhancementOptions(min_score=0.3), 30, "strict")
    # A counterfactual local projection only: it does not prove those low-fit
    # timestamps are correct or authorize changing any saved result/default.
    unfiltered, _ = merge_output(
        source, raw, None, EnhancementOptions(min_score=0), 30, "counterfactual"
    )
    assert sum(row["word_timing"] for row in strict) == 0
    assert sum(row["word_timing"] for row in unfiltered) == 3
    assert [row["text"] for row in strict] == [row["text"] for row in unfiltered]
