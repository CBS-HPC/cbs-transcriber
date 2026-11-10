"""Manual structure changes preserve text, real timing and exact research versions."""

import json
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile

import pytest
from test_working_reports import mark_active_latest

from cbs_transcriber.history import edit_metadata
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import (
    edit_segment,
    merge_passages,
    normalize_segments,
    split_passage,
    word_mapping_current,
)


@pytest.fixture
def passages():
    result = normalize_segments(
        [
            {
                "text": "Hej, 👩‍🔬 København!",
                "start": 1,
                "end": 4,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 1.1, "end": 2},
                    {"word": "København", "start": 2.5, "end": 3.8},
                ],
            },
            {
                "text": "  Goddag igen.\n",
                "start": 5,
                "end": 7,
                "speaker": "A",
                "words": [
                    {"word": "Goddag", "start": 5.1, "end": 6},
                    {"word": "igen", "start": 6, "end": 6.8},
                ],
            },
            {"text": "Tak.", "start": 8, "end": 9, "speaker": "B"},
        ]
    )
    result[-1]["extra_source_context"] = {"must": ["stay", "exact"]}
    return result


@pytest.mark.parametrize("identifier,direction", [("0", "down"), ("1", "up")])
def test_merge_is_adjacent_exact_and_keeps_unselected_ids_and_word_intervals(
    passages, identifier, direction
):
    before = deepcopy(passages)
    merged = merge_passages(passages, identifier, direction)
    joined = merged[0]
    assert joined["id"] == "0"
    assert joined["text"] == passages[0]["text"] + " " + passages[1]["text"]
    assert joined["original_text"] == joined["text"]
    assert joined["start"] == 1 and joined["end"] == 7
    assert joined["word_timing"] and joined["original_word_timing"]
    assert not joined["edited"]
    for word, original in zip(
        joined["words"], passages[0]["words"] + passages[1]["words"], strict=True
    ):
        assert (word["start"], word["end"]) == (original["start"], original["end"])
        assert joined["text"][word["char_start"] : word["char_end"]] == word["word"]
    assert merged[-1] == before[-1]
    assert merged[-1] is passages[-1]
    assert passages == before


def test_merge_requires_a_deliberate_speaker_when_labels_differ(passages):
    with pytest.raises(ValueError, match="speaker explicitly"):
        merge_passages(passages, "1", "down")
    assert merge_passages(passages, "1", "down", "B")[1]["speaker"] == "B"
    assert merge_passages(passages, "1", "down", "C", {"C": "Earlier voice"})[1]["speaker"] == "C"
    with pytest.raises(ValueError, match="existing speaker"):
        merge_passages(passages, "1", "down", "FOREIGN")


@pytest.mark.parametrize("identifier,direction", [("0", "up"), ("2", "down"), ("1", "sideways")])
def test_merge_rejects_non_adjacent_or_invalid_direction(passages, identifier, direction):
    with pytest.raises(ValueError):
        merge_passages(passages, identifier, direction)


def test_merge_does_not_give_an_untimed_fragment_its_neighbors_bounds(passages):
    passages[1].update(
        start=None,
        end=None,
        words=[],
        original_words=[],
        word_timing=False,
        original_word_timing=False,
    )
    merged = merge_passages(passages, "0", "down")[0]
    assert merged["start"] is None and merged["end"] is None
    assert not merged["word_timing"]
    assert merged["words"] == passages[0]["words"]


def test_merge_free_rewrite_does_not_reenable_stale_words_and_original_restores_safely(passages):
    passages[0] = edit_segment(passages[0], "free", "Rewritten words.")
    merged = merge_passages(passages, "0", "down")[0]
    assert not merged["word_timing"]
    assert all(word["word"] not in {"Hej", "København"} for word in merged["words"])
    restored = edit_segment(merged, "restore")
    assert restored["text"] == passages[0]["original_text"] + " " + passages[1]["original_text"]
    assert restored["word_timing"]
    for word in restored["words"]:
        assert restored["text"][word["char_start"] : word["char_end"]] == word["word"]


def test_v79_word_correction_and_partial_timing_survive_merge_and_split():
    passages = normalize_segments(
        [
            {
                "text": "one two",
                "start": 0,
                "end": 2,
                "words": [
                    {"word": "one", "start": 0, "end": 0.8},
                    {"word": "two", "start": 1, "end": 1.8},
                ],
            },
            {
                "text": "three gap",
                "start": 2,
                "end": 4,
                "words": [{"word": "three", "start": 2.2, "end": 2.8}],
            },
        ]
    )
    passages[0] = edit_segment(passages[0], "word", "ONE", 0)
    merged = merge_passages(passages, "0", "down")
    assert not merged[0]["word_timing"] and merged[0]["edited"]
    assert word_mapping_current(merged[0])
    assert len(merged[0]["words"]) == 3
    parts = split_passage(merged, "0", len("ONE two "), 2)
    words = [word for part in parts for word in part["words"]]
    assert [(word["word"], word["start"], word["end"]) for word in words] == [
        ("ONE", 0, 0.8),
        ("two", 1, 1.8),
        ("three", 2.2, 2.8),
    ]
    assert [part["word_timing"] for part in parts] == [True, False]
    rejoined = merge_passages(parts, "0", "down")[0]
    assert len(rejoined["words"]) == 3 and word_mapping_current(rejoined)
    rewritten = edit_segment(rejoined, "free", "ONE two three changed")
    assert not word_mapping_current(rewritten)
    assert all(not part["words"] for part in split_passage([rewritten], "0", len("ONE two "), 2))
    legacy = deepcopy(merged[0])
    legacy.pop("words_current")
    assert legacy["edited"] and not legacy["word_timing"]
    assert not word_mapping_current(legacy)
    assert all(not part["words"] for part in split_passage([legacy], "0", len("ONE two "), 2))
    assert word_mapping_current(edit_segment(rewritten, "restore"))


def test_merge_size_limit_keeps_edit_and_restore_limits_usable(passages):
    passages[0]["text"] = "x" * 200_000
    with pytest.raises(ValueError, match="200,000"):
        merge_passages(passages, "0", "down")


def test_split_uses_code_point_cursor_preserves_every_character_and_real_word_bounds(passages):
    cursor = len("Hej, 👩‍🔬 ")
    before = deepcopy(passages)
    split = split_passage(passages, "0", cursor)
    left, right = split[:2]
    assert left["text"] == "Hej, 👩‍🔬 "
    assert right["text"] == "København!"
    assert left["text"] + right["text"] == passages[0]["text"]
    assert (left["start"], left["end"]) == (1, 2)
    assert (right["start"], right["end"]) == (2.5, 4)
    assert left["word_timing"] and right["word_timing"]
    assert left["id"] == "0" and right["id"] not in {p["id"] for p in passages}
    assert len(right["id"]) == 32
    assert right["words"][0]["char_start"] == 0
    assert split[2:] == passages[1:]
    assert all(a is b for a, b in zip(split[2:], passages[1:], strict=True))
    assert not left["edited"] and not right["edited"]
    assert passages == before


def test_split_word_correction_restores_corresponding_original_halves_not_stale_offsets(passages):
    passages[0] = edit_segment(passages[0], "word", "Goddag", 0)
    split = split_passage(passages, "0", len("Goddag, 👩‍🔬 "))
    assert split[0]["edited"]
    restored = [edit_segment(part, "restore") for part in split[:2]]
    assert "".join(part["text"] for part in restored) == passages[0]["original_text"]
    for part in restored:
        assert part["word_timing"]
        for word in part["words"]:
            assert part["text"][word["char_start"] : word["char_end"]] == word["word"]
            assert part["start"] <= word["start"] < word["end"] <= part["end"]


def test_split_timed_text_at_punctuation_keeps_whitespace_in_same_half(passages):
    split = split_passage(passages, "0", len("Hej,"))
    assert split[0]["text"] == split[0]["original_text"] == "Hej,"
    assert split[1]["text"] == split[1]["original_text"] == " 👩‍🔬 København!"
    assert not split[0]["edited"] and not split[1]["edited"]


@pytest.mark.parametrize("cursor", [0, 1, 2, -1, True, 1.5, "4", 200_001])
def test_split_rejects_invalid_cursor_or_inside_timed_word(passages, cursor):
    with pytest.raises(ValueError):
        split_passage(passages, "0", cursor)


def test_split_complete_timing_does_not_silently_ignore_an_audio_time(passages):
    with pytest.raises(ValueError, match="omit the audio split time"):
        split_passage(passages, "0", len("Hej, 👩‍🔬 "), 2.2)


def test_split_passage_timing_requires_an_explicit_real_audio_position(passages):
    before = deepcopy(passages)
    split = split_passage(passages, "2", 2, 8.4)
    left, right = split[-2:]
    assert (left["start"], left["end"]) == (8, 8.4)
    assert (right["start"], right["end"]) == (8.4, 9)
    assert left["text"] + right["text"] == "Tak."
    assert not left["word_timing"] and not right["word_timing"]
    assert passages == before


@pytest.mark.parametrize("audio_time", [None, 8, 9, -1, True, float("nan"), float("inf")])
def test_split_passage_only_timing_rejects_missing_or_outside_audio_position(passages, audio_time):
    with pytest.raises(ValueError):
        split_passage(passages, "2", 2, audio_time)


def test_split_free_rewrite_cannot_restore_stale_full_passage_timing(passages):
    passages[0] = edit_segment(passages[0], "free", "Nye ord i stedet.")
    split = split_passage(passages, "0", len("Nye ord "), 2.2)
    assert split[0]["words"] == split[1]["words"] == []
    for part in split[:2]:
        edited = edit_segment(part, "free", "Different text")
        restored = edit_segment(edited, "restore")
        assert restored["text"] == part["text"]
        assert not restored["word_timing"] and not restored["words"]


def test_split_incomplete_timing_retains_only_exact_words_inside_each_actual_half(passages):
    passages[0]["word_timing"] = False
    split = split_passage(passages, "0", len("Hej, 👩‍🔬 "), 2.2)
    assert split[0]["words"] == passages[0]["words"][:1]
    assert split[1]["words"][0]["word"] == "København"
    assert split[1]["words"][0]["start"] == 2.5
    assert split[1]["words"][0]["char_start"] == 0
    assert split[0]["word_timing"] and split[1]["word_timing"]
    # A chosen audio boundary cannot reassign a word crossing that boundary.
    cross = split_passage(passages, "0", len("Hej, 👩‍🔬 "), 2.8)
    assert cross[1]["words"] == []
    assert passages[0]["words"][1]["start"] == 2.5


def test_split_untimed_text_retains_no_audio_or_neighbor_timing():
    passages = normalize_segments([{"text": "Æble 🙂 banan"}])
    split = split_passage(passages, "0", len("Æble 🙂 "))
    assert split[0]["text"] + split[1]["text"] == passages[0]["text"]
    assert all(p["start"] is p["end"] is None for p in split)
    with pytest.raises(ValueError, match="untimed"):
        split_passage(passages, "0", len("Æble 🙂 "), 1)


def test_v79_split_unbounded_partial_map_recovers_only_fully_located_half_bounds():
    passages = normalize_segments(
        [
            {
                "text": "one two gap three",
                "words": [
                    {"word": "one", "start": 0, "end": 0.8},
                    {"word": "two", "start": 1, "end": 1.8},
                    {"word": "gap", "start": None, "end": None},
                    {"word": "three", "start": 2.2, "end": 2.8},
                ],
            }
        ]
    )
    before = deepcopy(passages)
    left, right = split_passage(passages, "0", len("one two "))
    assert left["word_timing"] and (left["start"], left["end"]) == (0, 1.8)
    assert not right["word_timing"] and right["start"] is right["end"] is None
    assert left["words"] == before[0]["words"][:2]
    assert right["words"][-1]["start"] == 2.2
    assert passages == before


def test_split_does_not_trust_a_stale_complete_flag(passages):
    passages[0]["words"][0]["char_end"] = 100
    with pytest.raises(ValueError, match="mapping is incomplete"):
        split_passage(passages, "0", len("Hej, 👩‍🔬 "))


@pytest.fixture
def structure_result(client, passages):
    store = client.app.state.store
    project = store.create_project("Structural review")
    recording = store.create_recording(project["id"], "interview.wav")
    result = store.add_result(
        recording["id"],
        passages,
        {"A": "Interviewer", "B": "Benny"},
        "Reviewed words",
        {"kind": "import"},
        activate=True,
        revision=0,
    )
    other = store.add_result(
        recording["id"], deepcopy(passages), {}, "Independent", {"kind": "import"}
    )
    foreign_project = store.create_project("Another project")
    foreign_recording = store.create_recording(foreign_project["id"], "foreign.wav")
    foreign = store.add_result(
        foreign_recording["id"], deepcopy(passages), {}, "Foreign", {"kind": "import"}
    )
    return store.recording(recording["id"]), result, other, foreign


def structure_request(client, before, operation, passage_id="0", **body):
    return client.post(
        f"/api/recordings/{before['id']}/segments/{passage_id}/{operation}",
        json={"revision": before["revision"], "result_id": before["active_transcript_id"], **body},
    )


def undo_options(client, recording):
    return client.get(
        f"/api/recordings/{recording['id']}/merge-undo-options",
        params={"result_id": recording["active_transcript_id"]},
    )


@pytest.mark.parametrize("passage_id,direction", [("0", "down"), ("1", "up")])
def test_undo_merge_restores_exact_historical_passages_and_keeps_later_edits(
    client, structure_result, passage_id, direction
):
    before, active, _, _ = structure_result
    store = client.app.state.store
    merged = structure_request(client, before, "merge", passage_id, direction=direction).json()
    later = deepcopy(merged["segments"])
    later[1]["text"] = "A later independent edit 🌸"
    saved = store.save_transcript(
        before["id"], later, merged["speakers"], merged["revision"], result_id=active["id"]
    )
    choices = undo_options(client, saved)
    assert choices.status_code == 200
    assert choices.json()["segment_ids"] == ["0"]
    response = structure_request(client, saved, "undo-merge")
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["segments"][:2] == before["segments"][:2]
    assert after["segments"][2] == later[1]
    assert after["speakers"] == saved["speakers"]
    assert after["revision"] == saved["revision"] + 1
    assert store.result(before["id"], active["id"])["segments"] == after["segments"]
    assert store.result_versions(before["id"], active["id"])[0]["action"] == "passage_merge_undo"
    assert undo_options(client, after).json()["segment_ids"] == []


def test_undo_merge_rejects_changed_passage_and_stale_revision(client, structure_result):
    before, active, _, _ = structure_result
    store = client.app.state.store
    merged = structure_request(client, before, "merge", direction="down").json()
    changed = deepcopy(merged["segments"])
    changed[0]["text"] += " modified"
    saved = store.save_transcript(
        before["id"], changed, merged["speakers"], merged["revision"], result_id=active["id"]
    )
    assert undo_options(client, saved).json()["segment_ids"] == []
    assert structure_request(client, merged, "undo-merge").status_code == 409
    assert structure_request(client, saved, "undo-merge").status_code == 409
    assert store.recording(before["id"])["segments"] == changed


def test_undo_merge_does_not_trust_forged_merge_context(client, structure_result):
    before, active, _, _ = structure_result
    store = client.app.state.store
    forged = deepcopy(before["segments"])
    forged[0]["text"] = "Not a merge"
    forged.pop(1)
    current = store.save_transcript(
        before["id"],
        forged,
        before["speakers"],
        before["revision"],
        action="passage_merge",
        context={"segment_id": "0", "adjacent_segment_id": "1", "direction": "down"},
        result_id=active["id"],
    )
    assert undo_options(client, current).json()["segment_ids"] == []
    assert structure_request(client, current, "undo-merge").status_code == 409


def test_undo_merge_restores_distinct_speakers_and_retains_renamed_labels(client, structure_result):
    before, active, _, _ = structure_result
    store = client.app.state.store
    response = structure_request(client, before, "merge", "1", direction="down", speaker_id="B")
    assert response.status_code == 200, response.text
    merged = response.json()
    speakers = {**merged["speakers"], "A": "Renamed later"}
    current = store.save_transcript(
        before["id"], merged["segments"], speakers, merged["revision"], result_id=active["id"]
    )
    response = structure_request(client, current, "undo-merge", "1")
    assert response.status_code == 200, response.text
    assert response.json()["segments"] == before["segments"]
    assert response.json()["speakers"] == speakers


def test_undo_merge_rejects_reused_original_passage_id(client, structure_result):
    before, active, _, _ = structure_result
    store = client.app.state.store
    merged = structure_request(client, before, "merge", direction="down").json()
    segments = deepcopy(merged["segments"])
    segments[-1]["id"] = "1"
    current = store.save_transcript(
        before["id"], segments, merged["speakers"], merged["revision"], result_id=active["id"]
    )
    assert undo_options(client, current).json()["segment_ids"] == []
    assert structure_request(client, current, "undo-merge").status_code == 409


def test_undo_merge_rejects_corrupt_history(client, structure_result):
    before, active, _, _ = structure_result
    store = client.app.state.store
    merged = structure_request(client, before, "merge", direction="down").json()
    merge_version = store.result_versions(before["id"], active["id"])[0]
    # Tamper only the earlier historical snapshot in a synthetic test library.
    with store.connection() as db:
        db.execute(
            "UPDATE result_versions SET payload_sha256=? WHERE id=?",
            ("0" * 64, merge_version["predecessor_id"]),
        )
    assert undo_options(client, merged).json()["segment_ids"] == []
    assert structure_request(client, merged, "undo-merge").status_code == 409


def test_undo_merge_reports_bounded_history_search(client, structure_result, monkeypatch):
    from cbs_transcriber import passage_undo

    before, _, _, _ = structure_result
    merged = structure_request(client, before, "merge", direction="down").json()
    monkeypatch.setattr(passage_undo, "MAX_MERGES", 0)
    options = undo_options(client, merged).json()
    assert options["segment_ids"] == []
    assert options["history_limited"] is True
    assert structure_request(client, merged, "undo-merge").status_code == 409
    monkeypatch.setattr(passage_undo, "MAX_MERGES", 100)
    monkeypatch.setattr(passage_undo, "MAX_HISTORY_VERSIONS", 0)
    options = undo_options(client, merged).json()
    assert options["segment_ids"] == []
    assert options["history_limited"] is True


@pytest.mark.parametrize(
    "operation,body,action",
    [
        ("merge", {"direction": "down"}, "passage_merge"),
        ("split", {"cursor": len("Hej, 👩‍🔬 ")}, "passage_split"),
    ],
)
def test_structure_api_atomically_updates_active_projection_and_one_exact_history_version(
    client, structure_result, tmp_path, operation, body, action
):
    before, active, other, foreign = structure_result
    store = client.app.state.store
    previous = store.result_versions(before["id"], active["id"])[0]
    response = structure_request(client, before, operation, reason="Human review", **body)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["revision"] == before["revision"] + 1
    assert current["notes_revision"] == before["notes_revision"]
    assert current["active_transcript_id"] == active["id"]
    assert len(current["transcripts"]) == len(before["transcripts"])
    persisted = Store(tmp_path)
    assert persisted.recording(current["id"]) == current
    assert persisted.result(current["id"], active["id"])["segments"] == current["segments"]
    assert persisted.result(current["id"], other["id"]) == other
    assert persisted.result(foreign["recording_id"], foreign["id"]) == foreign
    versions = persisted.result_versions(before["id"], active["id"])
    assert len(versions) == 2
    assert versions[0]["action"] == action
    latest = persisted.result_version(before["id"], active["id"], versions[0]["id"])
    assert latest["payload"]["segments"] == current["segments"]
    assert latest["context"]["segment_id"] == "0"
    exact_before = persisted.result_version(before["id"], active["id"], previous["id"])
    assert exact_before["payload"]["segments"] == before["segments"]
    assert exact_before["payload"]["speakers"] == before["speakers"]


def test_structure_api_rejects_stale_revision_and_a_different_saved_result(
    client, structure_result
):
    before, active, other, foreign = structure_result
    updated = structure_request(client, before, "merge", direction="down").json()
    response = structure_request(client, before, "merge", direction="down")
    assert response.status_code == 409
    assert (
        structure_request(client, updated, "split", cursor=4, result_id=other["id"]).status_code
        == 409
    )
    assert (
        structure_request(client, updated, "split", cursor=4, result_id=foreign["id"]).status_code
        == 409
    )
    store = client.app.state.store
    assert store.recording(before["id"]) == updated
    assert len(store.result_versions(before["id"], active["id"])) == 2


@pytest.mark.parametrize(
    "operation,body,status",
    [
        ("merge", {"direction": "sideways"}, 422),
        ("merge", {"direction": "up"}, 400),
        ("merge", {"direction": "down", "speaker_id": "FOREIGN"}, 400),
        ("merge", {"direction": "down", "revision": True}, 422),
        ("merge", {"direction": "down", "result_id": ""}, 422),
        ("merge", {"direction": "down", "secret": "ignored?"}, 422),
        ("split", {"cursor": True}, 422),
        ("split", {"cursor": 0}, 422),
        ("split", {"cursor": 1}, 400),
        ("split", {"cursor": 4, "split_time": "2.5"}, 422),
        ("split", {"cursor": 4, "split_time": True}, 422),
        ("split", {"cursor": 4, "result_id": 1}, 422),
    ],
)
def test_structure_api_rejects_bad_requests_without_writing_history(
    client, structure_result, operation, body, status
):
    before, active, _, _ = structure_result
    response = structure_request(client, before, operation, **body)
    assert response.status_code == status, response.text
    store = client.app.state.store
    assert store.recording(before["id"]) == before
    assert len(store.result_versions(before["id"], active["id"])) == 1


def test_structure_api_missing_passage_cannot_reach_another_result(client, structure_result):
    before, _, _, _ = structure_result
    assert (
        structure_request(client, before, "merge", "missing-only", direction="down").status_code
        == 404
    )
    assert structure_request(client, before, "split", "missing-only", cursor=4).status_code == 404
    assert client.app.state.store.recording(before["id"]) == before


def test_store_transaction_rechecks_result_identity_even_with_a_current_revision(
    client, structure_result
):
    before, _, other, _ = structure_result
    store = client.app.state.store
    with pytest.raises(ValueError, match="another window"):
        store.save_transcript(
            before["id"],
            before["segments"],
            before["speakers"],
            before["revision"],
            action="passage_merge",
            result_id=other["id"],
        )
    assert store.recording(before["id"]) == before


def test_legacy_replay_context_remains_frozen_without_becoming_split_bounds(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Legacy structure")
    recording = store.create_recording(project["id"], "legacy.wav")
    source = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Known unknown.", "start": 2, "end": 5}]),
        {},
        "Original",
        {"kind": "import"},
    )
    frozen = store.freeze_result(recording["id"], source["id"], 1)
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Known unknown."}]),
        {},
        "Legacy enhancement",
        {"kind": "enhancement", "source_result_id": source["id"]},
        job_id=job["id"],
    )
    current = store.recording(recording["id"])
    current = store.activate_result(recording["id"], result["id"], current["revision"])
    split = split_passage(current["segments"], "0", len("Known "))
    current = store.save_transcript(
        recording["id"],
        split,
        {},
        current["revision"],
        action="passage_split",
        result_id=result["id"],
    )
    assert all(part["start"] is part["end"] is None for part in current["segments"])
    assert len(current["replay_windows"]) == 2
    for window in current["replay_windows"].values():
        assert window == {
            "start": 2,
            "end": 5,
            "source_result_id": source["id"],
            "source_segment_id": "0",
        }
    merged = merge_passages(current["segments"], "0", "down")
    current = store.save_transcript(
        recording["id"],
        merged,
        {},
        current["revision"],
        action="passage_merge",
        result_id=result["id"],
    )
    assert current["segments"][0]["text"] == "Known  unknown."
    assert current["segments"][0]["start"] is current["segments"][0]["end"] is None
    assert current["replay_windows"] == {}


def test_structure_operations_are_counted_in_research_report_without_private_context(
    client, structure_result
):
    before, _, _, _ = structure_result
    merged = structure_request(
        client, before, "merge", direction="down", reason="Private reason"
    ).json()
    response = structure_request(client, merged, "split", cursor=len("Hej, 👩‍🔬 "))
    assert response.status_code == 200, response.text
    mark_active_latest(client.app.state.store, before["project_id"])
    report = client.post(f"/api/projects/{before['project_id']}/research-report", json={})
    assert report.status_code == 200, report.text
    with ZipFile(BytesIO(report.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["manual_change_counts"]["passage_merge"] == 1
        assert manifest["manual_change_counts"]["passage_split"] == 1
        assert "Private reason" not in archive.read("methods.md").decode()
        assert "København" not in archive.read("manifest.json").decode()


def test_structure_api_rolls_back_projection_if_history_append_fails(
    client, structure_result, monkeypatch
):
    before, active, _, _ = structure_result
    store = client.app.state.store

    def fail_append(*args, **kwargs):
        raise RuntimeError("Synthetic history write failure")

    monkeypatch.setattr(store, "_append_version", fail_append)
    with pytest.raises(RuntimeError, match="Synthetic history"):
        structure_request(client, before, "merge", direction="down")
    assert store.recording(before["id"]) == before
    assert store.result(before["id"], active["id"])["segments"] == before["segments"]
    assert len(store.result_versions(before["id"], active["id"])) == 1


@pytest.mark.parametrize(
    "context",
    [
        {"cursor": True},
        {"split_time": float("inf")},
        {"split_time": True},
        {"direction": "x" * 201},
    ],
)
def test_structure_history_context_is_bounded_and_does_not_allow_invalid_numbers(context):
    with pytest.raises(ValueError):
        edit_metadata("passage_split", context, None)
