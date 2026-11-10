"""Pure three-way timing/speaker merges preserve the researcher's exact reviewed payload."""

import copy

import pytest

from cbs_transcriber import transcript_merge as merge
from cbs_transcriber.transcripts import normalize_segments


def passage(identifier, text="Yes", *, speaker="A", words=None):
    row = normalize_segments(
        [{"text": text, "speaker": speaker, "start": 0, "end": 10, "words": words or []}]
    )[0]
    row["id"] = identifier
    return row


def timed(identifier="0", *, start=1, speaker="A"):
    return passage(
        identifier, speaker=speaker, words=[{"word": "Yes", "start": start, "end": start + 1}]
    )


def payload(*passages, speakers=None):
    return {"segments": list(passages), "speakers": speakers or {}}


def test_creation_ordinal_mapping_handles_renumbering_and_repeated_text():
    base = payload(passage("5"), passage("19"))
    creation = payload(timed("0", start=1), timed("1", start=5))
    ours, incoming = copy.deepcopy(base), copy.deepcopy(creation)
    snapshots = copy.deepcopy((base, ours, incoming, creation))
    result = merge.merge_payload(base, ours, incoming, creation)
    assert [row["id"] for row in result["segments"]] == ["5", "19"]
    assert [row["words"][0]["start"] for row in result["segments"]] == [1, 5]
    assert result["summary"] == {
        "updated_passages": 2,
        "timing_updates": 2,
        "speaker_updates": 0,
        "conflicts": 0,
        "skipped": 0,
        "unchanged": 0,
    }
    assert (base, ours, incoming, creation) == snapshots


@pytest.mark.parametrize("kind", ["extra", "changed", "reordered"])
def test_creation_requires_proven_one_to_one_literal_text(kind):
    base = payload(passage("9", "First"), passage("20", "Second"))
    creation = copy.deepcopy(base)
    if kind == "extra":
        creation["segments"].append(passage("21", "Third"))
    elif kind == "changed":
        creation["segments"][0]["text"] = " First"
    else:
        creation["segments"].reverse()
    with pytest.raises(ValueError, match="correspondence is not proven"):
        merge.merge_payload(base, base, creation, creation)


def test_changed_deleted_split_and_new_reviewed_passages_are_preserved_exactly():
    base = payload(passage("5"), passage("8"), passage("12"))
    creation = payload(timed("0"), timed("1"), timed("2"))
    ours = payload(passage("5", "Edited wording"), passage("12", "Ye"), passage("99", "s"))
    result = merge.merge_payload(base, ours, creation, creation)
    assert result["segments"] == ours["segments"]
    assert [row["id"] for row in result["passages"]] == ["5", "12", "99", "8"]
    assert result["summary"]["skipped"] == 4
    assert result["summary"]["updated_passages"] == 0


def test_incoming_new_wording_and_structure_never_become_reviewed_text():
    base = payload(passage("5"), passage("8"))
    creation = payload(timed("0"), timed("1"))
    incoming = payload(passage("0", "New model wording"), passage("99", "Incoming split"))
    result = merge.merge_payload(base, base, incoming, creation)
    assert result["segments"] == base["segments"]
    assert result["summary"]["conflicts"] == 1
    assert result["summary"]["skipped"] == 1


def test_both_sides_changed_wording_is_a_conflict():
    base = payload(passage("5"))
    creation = payload(timed())
    ours, incoming = payload(passage("5", "Ours")), payload(passage("0", "Theirs"))
    result = merge.merge_payload(base, ours, incoming, creation)
    assert result["segments"] == ours["segments"]
    assert result["passages"][0]["status"] == "conflict"


def test_timing_is_one_conflicting_group_but_independent_speaker_can_update():
    base = payload(passage("5"))
    ours = payload(timed("5", start=2))
    creation = payload(timed("0", start=5, speaker="ENH_other_1"))
    result = merge.merge_payload(base, ours, creation, creation)
    expected = copy.deepcopy(ours["segments"][0])
    expected["speaker"] = "ENH_other_1"
    assert result["segments"] == [expected]
    assert result["summary"]["updated_passages"] == result["summary"]["speaker_updates"] == 1
    assert result["summary"]["timing_updates"] == 0
    assert result["summary"]["conflicts"] == 1
    assert result["passages"][0]["changes"] == ["speaker"]


def test_unchanged_incoming_and_already_equal_groups_keep_ours_without_rewriting():
    base = payload(passage("5"))
    ours = payload(timed("5"))
    result = merge.merge_payload(base, ours, base, base)
    assert result["segments"] == ours["segments"]
    assert result["summary"]["unchanged"] == 1
    creation = payload(timed("0"))
    result = merge.merge_payload(base, ours, creation, creation)
    assert result["segments"] == ours["segments"]
    assert result["summary"]["unchanged"] == 1


def test_timing_update_preserves_originals_edit_state_and_unknown_fields():
    base = payload(passage("5"))
    ours = copy.deepcopy(base)
    ours["segments"][0].update(
        original_text="Earlier original",
        original_words=[{"earlier": "evidence"}],
        original_word_timing=False,
        edited=True,
        words_current=True,
        extra_evidence={"keep": ["literal", "data"]},
    )
    creation = payload(timed())
    creation["segments"][0].update(original_text="Never copy", edited=False, extra_evidence="wrong")
    result = merge.merge_payload(base, ours, creation, creation)
    updated = result["segments"][0]
    assert result["summary"]["timing_updates"] == 1
    assert {key: value for key, value in updated.items() if key not in merge.TIMING_FIELDS} == {
        key: value for key, value in ours["segments"][0].items() if key not in merge.TIMING_FIELDS
    }
    assert updated["words_current"] is True


def test_reliable_partial_mapping_stays_partial_and_keeps_real_bounds():
    base = payload(passage("5", "Yes now"))
    creation = payload(
        passage(
            "0",
            "Yes now",
            words=[
                {"word": "Yes", "start": 1, "end": 2},
                {"word": "now", "start": None, "end": None},
            ],
        )
    )
    result = merge.merge_payload(base, base, creation, creation)
    updated = result["segments"][0]
    assert result["summary"]["timing_updates"] == 1
    assert updated["words_current"] is True and updated["word_timing"] is False
    assert updated["start"] == 0 and updated["end"] == 10
    assert updated["words"][1]["start"] is updated["words"][1]["end"] is None
    assert updated["original_words"] == base["segments"][0]["original_words"]


def test_stale_partial_mapping_is_not_revived_by_an_unchanged_text_string():
    base = payload(passage("5", "Yes now"))
    creation = payload(
        passage(
            "0",
            "Yes now",
            words=[
                {"word": "Yes", "start": 1, "end": 2},
                {"word": "now", "start": None, "end": None},
            ],
        )
    )
    incoming = copy.deepcopy(creation)
    incoming["segments"][0].update(edited=True, words_current=False)
    result = merge.merge_payload(base, base, incoming, creation)
    assert result["segments"] == base["segments"]
    assert result["summary"]["conflicts"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"start": True},
        {"start": 10**400},
        {"start": -1},
        {"end": 0},
        {"end": 12},
        {"char_start": True},
        {"char_end": 100},
        {"word": "Different"},
        {"start": None, "end": 2},
    ],
)
def test_invalid_word_intervals_or_character_maps_are_conflicts(mutation):
    base, creation = payload(passage("5")), payload(timed())
    creation["segments"][0]["words"][0].update(mutation)
    result = merge.merge_payload(base, base, creation, creation)
    assert result["segments"] == base["segments"]
    assert result["summary"]["timing_updates"] == 0
    assert result["summary"]["conflicts"] == 1


def test_word_time_order_and_false_complete_coverage_are_rejected():
    base = payload(passage("5", "Yes now"))
    creation = payload(
        passage(
            "0",
            "Yes now",
            words=[
                {"word": "Yes", "start": 4, "end": 5},
                {"word": "now", "start": 2, "end": 3},
            ],
        )
    )
    assert merge.merge_payload(base, base, creation, creation)["summary"]["conflicts"] == 1
    creation["segments"][0]["words"] = creation["segments"][0]["words"][:1]
    creation["segments"][0]["word_timing"] = True
    assert merge.merge_payload(base, base, creation, creation)["summary"]["conflicts"] == 1


def test_unknown_word_metadata_is_not_copied_into_the_reviewed_result():
    base, creation = payload(passage("5")), payload(timed())
    creation["segments"][0]["words"][0]["unexpected_metadata"] = "Do not import"
    result = merge.merge_payload(base, base, creation, creation)
    assert result["segments"] == base["segments"]
    assert result["summary"]["conflicts"] == 1


def test_timing_never_discards_known_bounds_or_replaces_words_with_failed_alignment():
    base, creation = payload(passage("5")), payload(timed())
    creation["segments"][0].update(start=None, end=None)
    assert merge.merge_payload(base, base, creation, creation)["summary"]["conflicts"] == 1
    base = payload(timed("5"))
    creation = payload(passage("0"))
    result = merge.merge_payload(base, base, creation, creation)
    assert result["segments"] == base["segments"]
    assert result["summary"]["conflicts"] == 1


def test_named_reviewed_voice_is_kept_while_timing_can_update():
    base = payload(passage("5"))
    ours = payload(copy.deepcopy(base["segments"][0]), speakers={"A": "Alice"})
    creation = payload(timed(speaker="ENH_x_1"), speakers={"ENH_x_1": "Detected"})
    result = merge.merge_payload(base, ours, creation, creation)
    assert result["segments"][0]["speaker"] == "A"
    assert result["speakers"] == {"A": "Alice"}
    assert result["summary"]["timing_updates"] == 1
    assert result["summary"]["speaker_updates"] == 0


def test_new_speaker_names_are_added_only_for_adopted_labels():
    base = payload(passage("5"))
    creation = payload(
        passage("0", speaker="ENH_x_1"), speakers={"ENH_x_1": "New voice", "unused": "Do not copy"}
    )
    result = merge.merge_payload(base, base, creation, creation)
    assert result["speakers"] == {"ENH_x_1": "New voice"}
    assert result["summary"]["speaker_updates"] == 1


def test_both_speaker_assignments_changed_or_label_collisions_are_conflicts():
    base = payload(passage("5"))
    ours = payload(passage("5", speaker="MANUAL_voice"))
    creation = payload(passage("0", speaker="ENH_x_1"))
    assert merge.merge_payload(base, ours, creation, creation)["summary"]["conflicts"] == 1
    ours = payload(
        passage("5"), passage("new", speaker="ENH_x_1"), speakers={"ENH_x_1": "Existing person"}
    )
    result = merge.merge_payload(base, ours, creation, creation)
    assert result["segments"] == ours["segments"]
    assert result["speakers"] == ours["speakers"]
    assert result["summary"]["conflicts"] == 1


def test_exact_unicode_offsets_and_whitespace_survive_merge():
    text = "Héj  👋 e\u0301"
    base = payload(passage("5", text))
    creation = payload(
        passage(
            "0",
            text,
            words=[
                {"word": "Héj", "start": 1, "end": 2},
                {"word": "👋", "start": 3, "end": 4},
                {"word": "e\u0301", "start": 5, "end": 6},
            ],
        )
    )
    result = merge.merge_payload(base, base, creation, creation)
    assert result["segments"][0]["text"] == text
    assert result["summary"]["timing_updates"] == 1
    for word in result["segments"][0]["words"]:
        assert text[word["char_start"] : word["char_end"]] == word["word"]


@pytest.mark.parametrize("position", range(4))
def test_duplicate_and_malformed_ids_are_rejected_in_every_snapshot(position):
    snapshots = [payload(passage("5")) for _ in range(4)]
    snapshots[position]["segments"].append(copy.deepcopy(snapshots[position]["segments"][0]))
    with pytest.raises(ValueError, match="unique bounded IDs"):
        merge.merge_payload(*snapshots)
    snapshots[position]["segments"].pop()
    snapshots[position]["segments"][0]["id"] = True
    with pytest.raises(ValueError, match="unique bounded IDs"):
        merge.merge_payload(*snapshots)


def test_nonfinite_or_oversized_snapshots_are_rejected(monkeypatch):
    base, creation = payload(passage("5")), payload(timed())
    creation["segments"][0]["words"][0]["start"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        merge.merge_payload(base, base, creation, creation)
    monkeypatch.setattr(merge, "MAX_MERGE_BYTES", 1)
    with pytest.raises(ValueError, match="payload limit"):
        merge.merge_payload(base, base, base, base)


def test_details_are_bounded_but_all_passages_are_counted():
    base = payload(*(passage(str(index)) for index in range(205)))
    result = merge.merge_payload(base, base, base, base)
    assert len(result["passages"]) == 200
    assert result["summary"]["unchanged"] == 205
    assert result["segments"] == base["segments"]


def test_empty_payloads_are_a_valid_zero_change_preview():
    result = merge.merge_payload(payload(), payload(), payload(), payload())
    assert result["segments"] == [] and result["speakers"] == {} and result["passages"] == []
    assert not any(result["summary"].values())
