"""Fresh manual wording stays editable when timing correspondence is uncertain."""

from copy import deepcopy

import pytest

from cbs_transcriber import transcripts


def passage(text="ja ja"):
    return transcripts.normalize_segments(
        [
            {
                "text": text,
                "start": 0,
                "end": len(text.split()) + 1,
                "speaker": "A",
                "words": [
                    {"word": word, "start": i, "end": i + 0.75}
                    for i, word in enumerate(text.split())
                ],
            }
        ]
    )[0]


def manual_edit(segment, text):
    return transcripts.edit_manual_text_retaining_timing(segment, text)


def test_literal_repeated_final_word_append_retains_every_old_word():
    original = passage()
    original["words"][1]["source_metadata"] = {"literal": "æøå"}
    changed, _ = manual_edit(original, "ja ja ja")
    assert changed["words"][:2] == original["words"]
    assert changed["words"][2] == {
        "word": "ja",
        "char_start": 6,
        "char_end": 8,
        "start": None,
        "end": None,
    }
    assert not changed["word_timing"] and changed["words_current"]


def test_cross_whitespace_word_is_untimed_without_losing_identifiable_other_word():
    original = passage("New York city")
    original["words"] = [
        {"word": "New York", "char_start": 0, "char_end": 8, "start": 0, "end": 1.75},
        original["words"][2],
    ]
    changed, warning = manual_edit(original, "New York town")
    assert [word["word"] for word in changed["words"]] == ["New", "York", "town"]
    assert [(word["start"], word["end"]) for word in changed["words"]] == [
        (None, None),
        (None, None),
        (2.0, 2.75),
    ]
    assert warning and "timing" in warning.lower()


def test_ambiguous_deletion_saves_text_and_keeps_only_proved_neighbor():
    original = passage("ja ja tak")
    before = deepcopy(original)
    changed, warning = manual_edit(original, "ja tak")
    assert changed["text"] == "ja tak"
    assert changed["words"][0]["start"] is None
    assert changed["words"][1]["start"] == original["words"][2]["start"]
    assert changed["original_words"] == original["original_words"]
    assert original == before
    assert warning


def test_correspondence_budget_never_blocks_text_or_lends_moved_timing(monkeypatch):
    original = passage("first second third fourth")
    monkeypatch.setattr(transcripts, "MAX_TEXT_EDIT_CELLS", 1)
    changed, warning = manual_edit(original, "fourth third second first")
    assert changed["text"] == "fourth third second first"
    assert all(word["start"] is None and word["end"] is None for word in changed["words"])
    assert warning


def test_policy_names_are_distinct_and_historical_policy_keeps_refusing():
    assert transcripts.MANUAL_TEXT_TIMING_POLICY == "permissive-word-map-v2"
    assert transcripts.TEXT_TIMING_POLICY == "conservative-word-map-v1"
    original = passage()
    with pytest.raises(ValueError, match="unambiguously"):
        transcripts.edit_text_retaining_timing(original, "ja ja ja")


def test_single_replacement_preserves_source_metadata_originals_and_bounds():
    original = passage("Hej København igen.")
    original["source"] = {"literal": "private source"}
    original["words"][0]["metadata"] = {"literal": "æøå"}
    original["words"][1]["manual_timing"] = True
    before = deepcopy(original)
    changed, warning = manual_edit(original, "Hej Århus igen.")
    assert warning is None
    assert changed["words"][0] == original["words"][0]
    assert changed["words"][1] == {
        **original["words"][1],
        "word": "Århus",
        "char_end": 9,
        "alignment_hints_stale": True,
    }
    assert changed["words"][2] == {
        **original["words"][2],
        "char_start": 10,
        "char_end": 15,
    }
    for field in (
        "source",
        "original_text",
        "original_words",
        "original_word_timing",
        "start",
        "end",
    ):
        assert changed[field] == original[field]
    assert original == before
    changed["words"][0]["metadata"]["literal"] = "separate copy"
    assert original == before


@pytest.mark.parametrize(
    "before,after",
    [
        ("one two", "two one"),
        ("one two", "two! one!"),
        ("ice cream", "icecream"),
        ("cannot", "can not"),
        ("hello", "hello,world"),
    ],
)
def test_ambiguous_moves_and_lexical_merges_or_splits_save_untimed(before, after):
    changed, warning = manual_edit(passage(before), after)
    assert changed["text"] == after
    assert all(word["start"] is word["end"] is None for word in changed["words"])
    assert warning


@pytest.mark.parametrize("value", [True, "1.0", -1, float("nan"), float("inf"), 10**1000])
def test_invalid_timing_nulls_only_that_region(value):
    original = passage("one two three")
    original["words"][1]["start"] = value
    changed, warning = manual_edit(original, original["text"] + " four")
    assert changed["words"][0] == original["words"][0]
    assert changed["words"][1]["start"] is changed["words"][1]["end"] is None
    assert changed["words"][2] == original["words"][2]
    assert changed["words"][3]["start"] is None
    assert warning


@pytest.mark.parametrize("defect", ["offset", "word", "marker", "inverted", "outside", "object"])
def test_malformed_word_map_still_saves_and_keeps_proved_neighbors(defect):
    original = passage("one two three")
    word = original["words"][1]
    if defect == "offset":
        word["char_start"] += 1
    elif defect == "word":
        word["word"] = "different"
    elif defect == "marker":
        word["manual_timing"] = False
    elif defect == "inverted":
        word.update(start=2, end=1)
    elif defect == "outside":
        word["end"] = original["end"] + 1
    else:
        original["words"][1] = "not a word object"
    changed, warning = manual_edit(original, original["text"] + " four")
    assert changed["words"][0] == original["words"][0]
    assert changed["words"][1]["start"] is None
    assert changed["words"][2] == original["words"][2]
    assert warning


def test_overlapping_or_out_of_order_char_maps_do_not_lend_ambiguous_intervals():
    original = passage("one two three")
    original["words"].insert(1, deepcopy(original["words"][0]))
    changed, warning = manual_edit(original, original["text"] + " four")
    assert changed["words"][0]["start"] is None
    assert changed["words"][1]["start"] == 1
    assert changed["words"][2]["start"] == 2
    assert warning
    original["words"] = list(reversed(original["words"]))
    changed, warning = manual_edit(original, original["text"] + " four")
    assert all(word["start"] is None for word in changed["words"])
    assert warning


def test_stale_or_invalid_container_cannot_reactivate_old_or_original_timing():
    original = transcripts.edit_segment(passage("one two"), "free", "new text")
    for words in (original["words"], {"bad": "container"}):
        original["words"] = words
        changed, warning = manual_edit(original, "new text extra")
        assert changed["text"] == "new text extra"
        assert all(word["start"] is word["end"] is None for word in changed["words"])
        assert changed["original_words"] == original["original_words"]
        assert warning


def test_noop_returns_an_independent_exact_copy_even_with_malformed_timing():
    original = passage()
    original["words"][0]["start"] = "not numeric"
    changed, warning = manual_edit(original, original["text"])
    assert changed == original and changed is not original
    assert changed["words"] is not original["words"]
    assert warning is None


def test_clearing_text_needs_no_word_timing_warning_even_with_bad_old_map():
    original = passage()
    original["words"] = "invalid map"
    changed, warning = manual_edit(original, "")
    assert changed["words"] == [] and not changed["word_timing"]
    assert warning is None


def test_unicode_whitespace_partial_pieces_and_empty_rewrite():
    original = passage("Hej, København! igen.")
    changed, _ = manual_edit(original, "  Hej,\nKøbenhavn!  igen. ")
    assert [(word["start"], word["end"]) for word in changed["words"]] == [
        (word["start"], word["end"]) for word in original["words"]
    ]
    assert [
        changed["text"][word["char_start"] : word["char_end"]] for word in changed["words"]
    ] == [word["word"] for word in changed["words"]]
    partial = passage("underground")
    partial["words"][0].update(word="ground", char_start=5)
    partial["word_timing"] = False
    changed, warning = manual_edit(partial, "underground extra")
    assert changed["words"][0]["word"] == "under" and changed["words"][0]["start"] is None
    assert changed["words"][1] == partial["words"][0]
    assert warning
    empty, _ = manual_edit(original, "")
    assert empty["text"] == "" and empty["words"] == []
    assert empty["original_words"] == original["original_words"]


@pytest.mark.parametrize(
    "text",
    [None, 123, True, ["words"], "a" * 200_001],
    ids=["null", "number", "boolean", "list", "oversized"],
)
def test_invalid_request_still_refuses_without_mutation(text):
    original = passage()
    before = deepcopy(original)
    with pytest.raises(ValueError, match="200,000 characters"):
        manual_edit(original, text)
    assert original == before
