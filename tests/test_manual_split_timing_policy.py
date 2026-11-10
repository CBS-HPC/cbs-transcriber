"""Lexical splitting is independent of whether audio timing can be retained."""

from copy import deepcopy

import pytest
from test_manual_text_timing_policy import passage

from cbs_transcriber import transcripts


def manual_split(segments, cursor, split_time=None):
    return transcripts.split_passage_manual(segments, segments[0]["id"], cursor, split_time)


def test_partial_word_map_without_audio_cursor_keeps_proved_half_only():
    original = passage("one two three")
    original["words"][2].update(start=None, end=None)
    original["word_timing"] = False
    before = deepcopy(original)
    left, right = manual_split([original], 4)
    assert left["text"] == "one " and right["text"] == "two three"
    assert (left["start"], left["end"]) == (0.0, 0.75)
    assert right["start"] is right["end"] is None
    assert left["word_timing"] and not right["word_timing"]
    assert right["words"][0]["start"] == 1
    assert original == before


def test_untimed_text_with_parent_bounds_splits_without_borrowing_parent_window():
    original = passage("one two")
    original.update(words=[], word_timing=False)
    left, right = manual_split([original], 4)
    assert left["start"] is left["end"] is right["start"] is right["end"] is None
    assert left["text"] + right["text"] == original["text"]


def test_malformed_timing_does_not_block_lexical_split():
    original = passage("one two")
    original["words"][0]["start"] = float("nan")
    left, right = manual_split([original], 4)
    assert left["words"][0]["start"] is None
    assert left["start"] is left["end"] is None
    assert right["word_timing"]


def test_word_group_crossing_cursor_is_untimed_in_both_halves():
    original = passage("New York city")
    original["words"] = [
        {"word": "New York", "char_start": 0, "char_end": 8, "start": 0, "end": 1.75},
        original["words"][2],
    ]
    left, right = manual_split([original], 4)
    assert left["words"][0]["start"] is None
    assert right["words"][0]["start"] is None
    assert right["words"][1]["start"] == 2
    assert left["start"] is left["end"] is right["start"] is right["end"] is None


def test_cursor_inside_lexical_word_is_still_a_text_error():
    original = passage("one two")
    original.update(words=[], word_timing=False)
    with pytest.raises(ValueError, match="between.*words"):
        manual_split([original], 2, 1)


def test_new_split_policy_is_distinct_and_old_partial_refusal_is_unchanged():
    assert transcripts.MANUAL_SPLIT_POLICY == "manual-word-boundary-v2"
    original = passage("one two")
    original["words"][0]["end"] = None
    with pytest.raises(ValueError, match="incomplete"):
        transcripts.split_passage([original], original["id"], 4)


def test_complete_timing_and_aligned_originals_keep_legacy_payload_exactly(monkeypatch):
    original = passage("Hej København igen.")
    original = transcripts.edit_mapped_word(original, 1, "Århus")
    from types import SimpleNamespace

    monkeypatch.setattr(transcripts.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed-right"))
    old = transcripts.split_passage([original], original["id"], 4)
    new = manual_split([original], 4)
    assert new == old
    assert new[1]["original_text"] == "København igen."


def test_unselected_passages_speaker_source_and_exact_whitespace_are_preserved():
    original = passage("æøå \n two  three")
    original.update(words=[], word_timing=False, source={"literal": "source metadata"})
    other = passage("unselected")
    other["id"] = "other"
    before = deepcopy([original, other])
    left, right, remaining = manual_split([original, other], 5)
    assert left["text"] + right["text"] == original["text"]
    assert left["id"] == original["id"] and right["id"] not in {original["id"], other["id"]}
    assert left["speaker"] == right["speaker"] == original["speaker"]
    assert left["source"] == right["source"] == original["source"]
    assert remaining == other
    assert [original, other] == before
    for part in (left, right):
        assert part["original_text"] == part["text"]
        assert part["original_words"] == part["words"]
        assert part["words_current"] and not part["edited"]


def test_valid_explicit_audio_split_keeps_real_bounds_and_nulls_conflicting_words():
    original = passage("one two three")
    original["words"][2].update(start=None, end=None)
    original["word_timing"] = False
    left, right = manual_split([original], 4, 0.5)
    assert (left["start"], left["end"]) == (0, 0.5)
    assert (right["start"], right["end"]) == (0.5, original["end"])
    assert left["words"][0]["start"] is left["words"][0]["end"] is None
    assert right["words"][0]["start"] == 1


@pytest.mark.parametrize("audio_time", [-1, 0, 99, "bad", True, float("nan"), float("inf")])
def test_invalid_optional_audio_time_cannot_block_or_locate_a_text_split(audio_time):
    original = passage("one two")
    original.update(words=[], word_timing=False)
    left, right = manual_split([original], 4, audio_time)
    assert left["start"] is left["end"] is right["start"] is right["end"] is None


@pytest.mark.parametrize(
    "text,cursor",
    [("l'été reste", 2), ("well-known word", 4), ("e\u0301tude ici", 1), ("e\u0301tude ici", 2)],
)
def test_unicode_and_compound_words_cannot_be_split_inside_the_word(text, cursor):
    original = passage(text)
    original.update(words=[], word_timing=False)
    with pytest.raises(ValueError, match="between words"):
        manual_split([original], cursor)


@pytest.mark.parametrize("cursor", [True, None, "4", 0, 7, -1])
def test_invalid_cursor_still_refuses_without_mutation(cursor):
    original = passage("one two")
    before = deepcopy(original)
    with pytest.raises(ValueError):
        manual_split([original], cursor)
    assert original == before


def test_inside_punctuation_token_is_not_a_whitespace_word_boundary():
    original = passage("one,two three")
    with pytest.raises(ValueError, match="between words"):
        manual_split([original], 4)
