"""Fresh Text saves retain only unambiguous existing word intervals; no inference."""

import json
import sqlite3
from copy import deepcopy
from itertools import product

import pytest

from cbs_transcriber.history import edit_metadata, encode_payload
from cbs_transcriber.transcripts import (
    TEXT_TIMING_POLICY,
    _text_edit_pairs,
    edit_segment,
    edit_text_retaining_timing,
    normalize_segments,
)


def passage(text="Hej København igen."):
    tokens = text.split()
    return normalize_segments(
        [
            {
                "text": text,
                "start": 0,
                "end": len(tokens) + 1,
                "speaker": "A",
                "words": [
                    {"word": token, "start": index, "end": index + 0.75}
                    for index, token in enumerate(tokens)
                ],
            }
        ]
    )[0]


def test_replacement_inherits_own_interval_and_keeps_unchanged_word_metadata():
    original = passage()
    original["words"][0]["source_metadata"] = {"literal": "æøå"}
    original["words"][1]["manual_timing"] = True
    before = deepcopy(original)
    changed, warning = edit_text_retaining_timing(original, "Hej Århus igen.")
    assert original == before
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
    assert changed["original_text"] == original["original_text"]
    assert changed["original_words"] == original["original_words"]
    assert changed["start"] == original["start"] and changed["end"] == original["end"]
    assert changed["word_timing"] and changed["words_current"] and changed["edited"]


def test_insertion_is_untimed_and_later_words_keep_exact_intervals():
    original = passage()
    changed, _ = edit_text_retaining_timing(original, "Hej smukke København igen.")
    assert [(word["start"], word["end"]) for word in changed["words"]] == [
        (0.0, 0.75),
        (None, None),
        (1.0, 1.75),
        (2.0, 2.75),
    ]
    assert not changed["word_timing"] and changed["words_current"]
    removed, _ = edit_text_retaining_timing(changed, original["text"])
    assert removed["word_timing"]
    assert removed["words"] == original["words"]


def test_whitespace_unicode_punctuation_and_partial_source_keep_real_word_bounds():
    original = passage("Hej, København! igen.")
    original["words"][1].update(start=None, end=None)
    original["word_timing"] = False
    changed, _ = edit_text_retaining_timing(original, "  Hej,\nKøbenhavn!  igen. ")
    assert [word["word"] for word in changed["words"]] == ["Hej,", "København!", "igen."]
    assert [(word["start"], word["end"]) for word in changed["words"]] == [
        (word["start"], word["end"]) for word in original["words"]
    ]
    assert changed["words"][0]["char_start"] == 2
    assert not changed["word_timing"]
    punctuated, _ = edit_text_retaining_timing(passage("Hej verden"), "Hej! verden")
    assert punctuated["words"][0]["start"] == 0
    assert punctuated["words"][1] == {
        **passage("Hej verden")["words"][1],
        "char_start": 5,
        "char_end": 11,
    }


@pytest.mark.parametrize(
    "before,after",
    [
        ("ja ja tak", "ja tak"),
        ("ja ja! tak", "ja! tak"),
        ("ja tak", "ja ja tak"),
        ("one two", "two one"),
        ("one two", "two! one!"),
        ("a x b y", "b x a y"),
        ("ice cream", "icecream"),
        ("cannot", "can not"),
        ("hello", "hello,world"),
    ],
)
def test_ambiguous_repeat_move_merge_or_split_refuses_instead_of_guessing(before, after):
    original = passage(before)
    with pytest.raises(ValueError, match="unambiguously"):
        edit_text_retaining_timing(original, after)
    assert original == passage(before)


def test_unique_novel_replacement_can_correct_one_of_repeated_source_words():
    original = passage("ja ja tak")
    changed, _ = edit_text_retaining_timing(original, "nej ja tak")
    assert changed["words"][0]["word"] == "nej"
    assert changed["words"][0]["start"] == original["words"][0]["start"]
    assert changed["words"][0]["end"] == original["words"][0]["end"]
    assert changed["words"][1]["start"] == original["words"][1]["start"]


def test_long_simple_edit_uses_bounded_outer_anchors_but_complex_edits_refuse():
    text = " ".join(f"word{index}" for index in range(1200))
    original = passage(text)
    changed, _ = edit_text_retaining_timing(original, text + " newlyinserted")
    assert changed["words"][:-1] == original["words"]
    assert changed["words"][-1]["start"] is None
    changed, _ = edit_text_retaining_timing(original, text.replace("word600 ", "replacement "))
    assert changed["words"][600]["start"] == original["words"][600]["start"]
    with pytest.raises(ValueError, match="unambiguously"):
        edit_text_retaining_timing(original, " ".join(reversed(text.split())))


def test_stale_mapping_remains_untimed_without_reviving_original_times():
    original = edit_segment(passage(), "free", "Den forrige ændring.")
    changed, warning = edit_text_retaining_timing(original, "Den nye ændring.")
    assert warning and "stale" in warning
    assert all(word["start"] is None and word["end"] is None for word in changed["words"])
    assert not changed["word_timing"] and changed["words_current"]


def test_all_null_current_mapping_keeps_unchanged_metadata_without_claiming_timing():
    original = passage()
    for word in original["words"]:
        word.update(start=None, end=None)
    original["word_timing"] = False
    original["words"][0]["source_metadata"] = {"literal": "æøå"}
    changed, _ = edit_text_retaining_timing(original, "Hej Århus igen.")
    assert changed["words"][0] == original["words"][0]
    assert not changed["word_timing"]
    repeated = passage("ja ja tak")
    for word in repeated["words"]:
        word.update(start=None, end=None)
    repeated["word_timing"] = False
    changed, _ = edit_text_retaining_timing(repeated, "ja tak")
    assert all(word["start"] is None and word["end"] is None for word in changed["words"])


def test_noop_and_unbounded_passage_do_not_rewrite_payload_or_derive_bounds():
    original = passage()
    assert edit_text_retaining_timing(original, original["text"]) == (original, None)
    original.update(start=None, end=None)
    changed, _ = edit_text_retaining_timing(original, "Hej Århus igen.")
    assert changed["start"] is None and changed["end"] is None


@pytest.mark.parametrize("change", ["offset", "word", "marker", "interval"])
def test_corrupt_current_mapping_is_refused(change):
    original = passage()
    if change == "offset":
        original["words"][1]["char_start"] += 1
    elif change == "word":
        original["words"][1]["word"] = "wrong"
    elif change == "marker":
        original["words"][1]["manual_timing"] = False
    else:
        original["words"][1].update(start=2, end=1)
    with pytest.raises(ValueError):
        edit_text_retaining_timing(original, "Hej Århus igen.")


@pytest.mark.parametrize("value", [True, "1.0", -1, float("nan"), float("inf"), 10**1000])
def test_corrupt_timing_types_are_not_coerced_or_reused(value):
    original = passage()
    original["words"][1]["start"] = value
    with pytest.raises(ValueError):
        edit_text_retaining_timing(original, "Hej Århus igen.")


def test_cross_whitespace_source_words_are_not_guessed_into_new_word_spans():
    original = passage("New York city")
    original["words"] = [
        {"word": "New York", "char_start": 0, "char_end": 8, "start": 0, "end": 1.75},
        original["words"][2],
    ]
    with pytest.raises(ValueError, match="unambiguously"):
        edit_text_retaining_timing(original, "New York town")


@pytest.mark.parametrize("start,end", [(10, 11), (1, 1), (0.1, 0.2), (10, None), (None, 10)])
def test_existing_word_bounds_and_order_must_be_valid_before_reuse(start, end):
    original = passage()
    original["words"][1].update(start=start, end=end)
    with pytest.raises(ValueError, match="inconsistent"):
        edit_text_retaining_timing(original, "Hej Århus igen.")


def test_partial_nullable_bounds_and_real_overlaps_are_retained_exactly():
    original = passage()
    original["words"][0]["end"] = 1.2
    original["words"][2]["end"] = None
    original["word_timing"] = False
    changed, _ = edit_text_retaining_timing(original, "Hej Århus igen.")
    assert changed["words"][0] == original["words"][0]
    assert changed["words"][1]["start"] == original["words"][1]["start"]
    assert changed["words"][2]["end"] is None
    assert not changed["word_timing"]


def test_partial_lexical_word_cannot_lend_its_interval_to_an_entire_replacement():
    original = normalize_segments(
        [
            {
                "text": "underground",
                "start": 0,
                "end": 2,
                "words": [{"word": "ground", "start": 0.2, "end": 1.2}],
            }
        ]
    )[0]
    with pytest.raises(ValueError, match="unambiguously"):
        edit_text_retaining_timing(original, "groundwork")
    preserved, _ = edit_text_retaining_timing(original, "underground extra")
    assert preserved["words"][0]["word"] == "under"
    assert preserved["words"][0]["start"] is None
    assert preserved["words"][1] == original["words"][0]


def test_outer_anchors_match_all_optimal_paths_for_small_repeated_sequences():
    def paths(old, new):
        if not old:
            return len(new), {tuple([-1] * len(new))}
        if not new:
            return len(old), {()}
        options = []
        cost, mappings = paths(old[1:], new[1:])
        options.append(
            (
                cost + (old[0] != new[0]),
                {(0,) + tuple(i + 1 if i >= 0 else i for i in p) for p in mappings},
            )
        )
        cost, mappings = paths(old[1:], new)
        options.append((cost + 1, {tuple(i + 1 if i >= 0 else i for i in p) for p in mappings}))
        cost, mappings = paths(old, new[1:])
        options.append((cost + 1, {(-1,) + p for p in mappings}))
        best = min(cost for cost, _ in options)
        return best, set().union(*(mappings for cost, mappings in options if cost == best))

    sequences = [values for size in range(4) for values in product(("a", "b"), repeat=size)]
    for old, new in product(sequences, repeat=2):
        _, mappings = paths(old, new)
        expected = [
            next(iter(options)) if len(options := {path[j] for path in mappings}) == 1 else -2
            for j in range(len(new))
        ]
        assert _text_edit_pairs(list(old), list(new)) == expected, (old, new)


def test_policy_metadata_is_versioned_and_cannot_be_used_for_another_action():
    context = {"segment_id": "0", "mode": "free", "text_policy": TEXT_TIMING_POLICY}
    assert edit_metadata("free_rewrite", context, None) == context
    for action, policy in (("word_correction", TEXT_TIMING_POLICY), ("free_rewrite", "future")):
        with pytest.raises(ValueError):
            edit_metadata(action, {**context, "text_policy": policy}, None)


def test_v2_is_revision_fenced_historical_and_never_queues_alignment(client, monkeypatch):
    from cbs_transcriber import enhancement

    store = client.app.state.store
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    original, other = passage(), passage("Another passage.")
    other["id"] = "other"
    result = store.add_result(
        recording["id"], [original, other], {"A": "Søren"}, "Test", {}, auto_first=True
    )
    current = store.recording(recording["id"])
    enhancement.save_realign(store, True)
    rid, result_id = recording["id"], result["id"]
    url = f"/api/recordings/{rid}/segments/0/edit-v2"
    body = {"revision": current["revision"], "mode": "free", "text": "Hej nye København igen."}
    response = client.patch(url, json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["segments"][1] == other
    assert saved["segments"][0]["words"][1]["start"] is None
    assert saved["speakers"] == {"A": "Søren"}
    latest = store.result_versions(rid, result_id)[0]
    assert latest["action"] == "free_rewrite"
    assert latest["context"]["text_policy"] == TEXT_TIMING_POLICY
    assert saved["result_roles"]["working"]["version_id"] == latest["id"]
    assert not store.all("SELECT id FROM jobs WHERE recording_id=?", (rid,))
    stale = client.patch(url, json=body)
    assert stale.status_code in (400, 409)
    assert len(store.result_versions(rid, result_id)) == 2
    for extra in ({"realign": True}, {"word_index": 0}, {"start": 0}):
        invalid = client.patch(url, json={**body, "revision": saved["revision"], **extra})
        assert invalid.status_code == 422


def test_v2_ambiguity_refuses_atomic_save_and_v1_keeps_legacy_contract(client):
    store = client.app.state.store
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"], [passage("ja ja tak")], {}, "Test", {}, auto_first=True
    )
    rid = recording["id"]
    current = store.recording(rid)
    body = {"revision": current["revision"], "mode": "free", "text": "ja tak"}
    refused = client.patch(f"/api/recordings/{rid}/segments/0/edit-v2", json=body)
    assert refused.status_code == 400 and "unambiguously" in refused.json()["detail"]
    assert store.recording(rid) == current
    assert len(store.result_versions(rid, result["id"])) == 1
    legacy = client.patch(
        f"/api/recordings/{rid}/segments/0/edit-v1", json={**body, "realign": False}
    )
    assert legacy.status_code == 200, legacy.text
    saved = legacy.json()["segments"][0]
    assert not saved["word_timing"] and "words_current" not in saved
    assert saved["words"] == current["segments"][0]["words"]


def test_versioned_text_policy_and_nullable_words_survive_contained_history(tmp_path):
    from test_portable_semantics import RECORDING, RESULT, VERSION, build, inspect

    path = tmp_path / "synthetic.sqlite3"
    build(path, 19)
    original = {"id": "p", "text": "æøå", "start": -0.0}
    changed, _ = edit_text_retaining_timing(original, "æøå ekstra")
    payload, digest = encode_payload([changed], {})
    context = {"segment_id": "p", "mode": "free", "text_policy": TEXT_TIMING_POLICY}
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "f" * 32,
                RESULT,
                2,
                VERSION,
                None,
                payload,
                digest,
                "2026-11-09T12:00:00+00:00",
                "free_rewrite",
                json.dumps(context),
                "local_user",
                None,
            ),
        )
        encoded = json.dumps([changed], ensure_ascii=False)
        db.execute("UPDATE transcript_results SET segments=? WHERE id=?", (encoded, RESULT))
        db.execute("UPDATE recordings SET segments=? WHERE id=?", (encoded, RECORDING))
    before = path.read_bytes()
    observed = inspect(path)
    assert observed["semantic_counts"]["result_versions"] == 2
    assert "domain_specific_edit_and_inference_transformations" in observed["not_checked"]
    assert path.read_bytes() == before
