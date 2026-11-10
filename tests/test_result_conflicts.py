"""Synthetic exact-lineage reviews, explicit rejection and immutable reconstruction."""

import io
import json
from copy import deepcopy
from zipfile import ZipFile

import pytest

from cbs_transcriber import research_reports, result_conflicts
from cbs_transcriber.history import decode_payload
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_segment, normalize_segments, split_passage


@pytest.fixture
def branch(tmp_path):
    store = Store(tmp_path / "synthetic")
    project = store.create_project("Synthetic conflict review")
    recording = store.create_recording(project["id"], "synthetic.wav")
    source_segments = normalize_segments(
        [
            {"text": "Hej København!", "start": 0, "end": 2},
            {"text": "Review this.", "start": 2, "end": 4},
            {"text": "Known voice.", "start": 4, "end": 6, "speaker": "MANUAL"},
            {"text": "One voice.", "start": 6, "end": 8},
        ]
    )
    source_segments[3]["opaque"] = {"exact": ["\u00e6\u0301", None, False]}
    ours = store.add_result(
        recording["id"],
        source_segments,
        {"MANUAL": "Named researcher"},
        "Reviewed",
        {"kind": "provider"},
    )
    current = store.recording(recording["id"])
    frozen = store.freeze_result(recording["id"], ours["id"], current["revision"])
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    enhanced = normalize_segments(
        [
            {
                "text": "Hej København!",
                "start": 0,
                "end": 2,
                "words": [
                    {"word": "Hej", "start": 0.1, "end": 0.5},
                    {"word": "København!", "start": 0.8, "end": 1.9},
                ],
            },
            {
                "text": "Review this.",
                "start": 2,
                "end": 4,
                "words": [
                    {"word": "Review", "start": 2.1, "end": 2.5},
                    {"word": "this.", "start": 2.8, "end": 3.8},
                ],
            },
            {"text": "Known voice.", "start": 4, "end": 6},
            {"text": "One voice.", "start": 6, "end": 8, "speaker": "SPEAKER_00"},
        ]
    )
    incoming = store.add_result(
        recording["id"],
        enhanced,
        {"SPEAKER_00": "Synthetic voice"},
        "Enhanced",
        {"kind": "enhancement", "source_result_id": ours["id"]},
        job_id=job["id"],
    )
    reviewed = deepcopy(source_segments)
    reviewed[0] = edit_segment(reviewed[0], "free", "Reviewed 🧪 æ\u0301 wording.\n")
    for field in ("words", "word_timing"):
        reviewed[1][field] = deepcopy(enhanced[1][field])
    reviewed[1]["words"][0]["start"] = 2.2
    reviewed[1]["words_current"] = True
    current = store.save_transcript(
        recording["id"],
        reviewed,
        ours["speakers"],
        current["revision"],
        action="free_rewrite",
        context={"segment_id": reviewed[0]["id"]},
    )
    incoming_segments = deepcopy(incoming["segments"])
    incoming_segments[0] = edit_segment(
        incoming_segments[0], "free", "Incoming 🧪 æ\u0301 wording.\n"
    )
    value = {
        "store": store,
        "recording": current,
        "ours": ours,
        "incoming": incoming,
        "base": frozen,
    }
    append_incoming(value, incoming_segments)
    store.save_notes("recordings", recording["id"], "Independent notes æøå", 0)
    return value


def append_incoming(branch, segments=None, speakers=None):
    store, incoming = branch["store"], branch["incoming"]
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if segments is not None:
            db.execute(
                "UPDATE transcript_results SET segments=? WHERE id=?",
                (json.dumps(segments, ensure_ascii=False), incoming["id"]),
            )
        if speakers is not None:
            db.execute(
                "UPDATE transcript_results SET speakers=? WHERE id=?",
                (json.dumps(speakers), incoming["id"]),
            )
        store._append_version(
            db,
            db.execute("SELECT * FROM transcript_results WHERE id=?", (incoming["id"],)).fetchone(),
            "manual_save",
            {},
            "local_user",
        )


def decisions(branch, group, choice, index=0):
    return [
        {"passage_id": branch["ours"]["segments"][index]["id"], "group": group, "choice": choice}
    ]


def preview(branch, choices=None):
    return result_conflicts.preview(
        branch["store"],
        branch["recording"]["id"],
        branch["ours"]["id"],
        branch["incoming"]["id"],
        branch["recording"]["revision"],
        choices,
    )


def apply(branch, choices, planned):
    return result_conflicts.apply(
        branch["store"],
        branch["recording"]["id"],
        branch["ours"]["id"],
        branch["incoming"]["id"],
        branch["recording"]["revision"],
        choices,
        planned["fingerprint"],
    )


def database_rows(store):
    with store.connection() as db:
        return {
            name: [tuple(row) for row in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]
            for name in (
                "recordings",
                "transcript_results",
                "result_versions",
                "jobs",
                "note_versions",
            )
        }


def captured_history(store):
    with store.connection() as db:
        result = {}
        for row in db.execute(
            "SELECT v.*,t.recording_id FROM result_versions v "
            "JOIN transcript_results t ON t.id=v.result_id"
        ):
            row = dict(row)
            row["payload"] = decode_payload(row["payload"], row["payload_sha256"])
            row["context"] = json.loads(row["context"])
            result[row["id"]] = row
        return result


def test_preview_is_read_only_and_exposes_exact_three_way_values(branch):
    before = database_rows(branch["store"])
    planned = preview(branch)
    assert database_rows(branch["store"]) == before
    assert preview(branch) == planned
    group = planned["passages"][0]["groups"]["wording"]
    assert group["base"] == "Hej København!"
    assert group["current"] == "Reviewed 🧪 æ\u0301 wording.\n"
    assert group["incoming"] == "Incoming 🧪 æ\u0301 wording.\n"
    assert group["incoming_allowed"]
    assert not planned["passages"][0]["groups"]["timing"]["incoming_allowed"]


def test_explicit_wording_preserves_originals_bounds_speakers_and_other_dictionaries(branch):
    choices = decisions(branch, "wording", "incoming")
    store = branch["store"]
    before = store.recording(branch["recording"]["id"])
    outcome = apply(branch, choices, preview(branch, choices))
    after = outcome["recording"]
    expected = edit_segment(before["segments"][0], "free", "Incoming 🧪 æ\u0301 wording.\n")
    assert after["segments"] == [expected, *before["segments"][1:]]
    assert after["speakers"] == before["speakers"]
    assert after["notes"] == "Independent notes æøå"
    history = captured_history(store)
    reviewed = history[outcome["version_id"]]
    assert reviewed["action"] == "result_conflict_review"
    assert reviewed["source_version_id"] is None
    result_conflicts.verify_historical(reviewed, history)
    projection = result_conflicts.public_projection(
        reviewed, {key: f"version-{index}" for index, key in enumerate(history)}
    )
    assert projection["decisions"] == [
        {"passage": "passage-001", "group": "wording", "choice": "incoming"}
    ]


def test_explicit_keep_records_same_payload_review_with_source_references(branch):
    choices = decisions(branch, "wording", "current")
    outcome = apply(branch, choices, preview(branch, choices))
    history = captured_history(branch["store"])
    row = history[outcome["version_id"]]
    assert row["action"] == "result_conflict_review"
    assert row["payload_sha256"] == history[row["predecessor_id"]]["payload_sha256"]
    assert row["context"]["conflict_review"]["decisions"] == choices
    assert outcome["summary"]["current"] == 1
    assert outcome["summary"]["changed_passages"] == 0
    result_conflicts.verify_historical(row, history)


def test_explicit_timing_resolves_both_changed_timing_without_overwriting_text(branch):
    choices = decisions(branch, "timing", "incoming", 1)
    before = branch["store"].recording(branch["recording"]["id"])
    incoming = branch["store"].result(branch["recording"]["id"], branch["incoming"]["id"])
    outcome = apply(branch, choices, preview(branch, choices))
    after = outcome["recording"]["segments"]
    assert after[1]["words"] == incoming["segments"][1]["words"]
    assert after[1]["original_words"] == before["segments"][1]["original_words"]
    assert after[0] == before["segments"][0]
    assert after[2:] == before["segments"][2:]
    assert outcome["summary"]["timing_updates"] == 1


def test_timing_cannot_join_different_wording_even_when_wording_is_also_selected(branch):
    choices = decisions(branch, "wording", "incoming") + decisions(branch, "timing", "incoming")
    with pytest.raises(ValueError, match="current literal wording"):
        preview(branch, choices)


def test_unknown_incoming_cannot_replace_known_named_voice(branch):
    choices = decisions(branch, "speaker", "incoming", 2)
    with pytest.raises(ValueError, match="unknown incoming label"):
        preview(branch, choices)


def test_explicit_new_speaker_assignment_keeps_named_voices(branch):
    choices = decisions(branch, "speaker", "incoming", 3)
    outcome = apply(branch, choices, preview(branch, choices))
    assert outcome["recording"]["speakers"] == {
        "MANUAL": "Named researcher",
        "SPEAKER_00": "Synthetic voice",
    }
    assert outcome["recording"]["segments"][3]["speaker"] == "SPEAKER_00"
    assert outcome["recording"]["segments"][3]["opaque"] == {"exact": ["æ\u0301", None, False]}


def test_speaker_name_collision_is_unresolved(branch):
    store = branch["store"]
    current = store.recording(branch["recording"]["id"])
    branch["recording"] = store.save_transcript(
        current["id"],
        current["segments"],
        {**current["speakers"], "SPEAKER_00": "A reviewed person"},
        current["revision"],
    )
    with pytest.raises(ValueError, match="different saved name"):
        preview(branch, decisions(branch, "speaker", "incoming", 3))


def test_structural_split_does_not_reuse_a_matching_original_id(branch):
    store = branch["store"]
    current = store.recording(branch["recording"]["id"])
    split = split_passage(current["segments"], current["segments"][0]["id"], 8, 1)
    branch["recording"] = store.save_transcript(
        current["id"], split, current["speakers"], current["revision"]
    )
    planned = preview(branch)
    assert not planned["passages"][0]["groups"]["wording"]["incoming_allowed"]
    with pytest.raises(ValueError, match="structure changed"):
        preview(branch, decisions(branch, "wording", "incoming"))


def test_changed_incoming_head_and_changed_decisions_invalidate_fingerprint(branch):
    choices = decisions(branch, "wording", "current")
    planned = preview(branch, choices)
    with pytest.raises(ValueError, match="changed in another window"):
        apply(branch, decisions(branch, "wording", "incoming"), planned)
    append_incoming(branch)
    with pytest.raises(ValueError, match="changed in another window"):
        apply(branch, choices, planned)


def test_writer_failure_rolls_back_content_version_and_independent_notes(branch, monkeypatch):
    choices = decisions(branch, "wording", "incoming")
    planned = preview(branch, choices)
    before = database_rows(branch["store"])
    append = result_conflicts.append_edit

    def fail(*args, **kwargs):
        append(*args, **kwargs)
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(result_conflicts, "append_edit", fail)
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        apply(branch, choices, planned)
    assert database_rows(branch["store"]) == before


def test_historical_review_refuses_payload_or_parent_drift(branch):
    choices = decisions(branch, "wording", "incoming")
    outcome = apply(branch, choices, preview(branch, choices))
    captured = captured_history(branch["store"])
    row = captured[outcome["version_id"]]
    row["context"]["conflict_review"]["incoming"]["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="ancestry check"):
        result_conflicts.verify_historical(row, captured)


def test_report_projects_verified_decisions_as_aliases_without_text_or_internal_ids(branch):
    choices = decisions(branch, "wording", "current")
    outcome = apply(branch, choices, preview(branch, choices))
    store = branch["store"]
    recording = store.recording(branch["recording"]["id"])
    content = research_reports.project_report(
        store,
        recording["project_id"],
        [
            {
                "recording_id": recording["id"],
                "result_id": branch["ours"]["id"],
                "version_id": outcome["version_id"],
            }
        ],
    )
    with ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    row = next(item for item in manifest["versions"] if item["action"] == "result_conflict_review")
    assert row["conflict_review"]["decisions"] == [
        {"passage": "passage-001", "group": "wording", "choice": "current"}
    ]
    serialized = json.dumps(manifest)
    for private in (
        "Reviewed",
        "Incoming 🧪",
        "Independent notes",
        branch["ours"]["id"],
        branch["incoming"]["id"],
        outcome["version_id"],
    ):
        assert private not in serialized


def test_hidden_passages_and_duplicate_or_malformed_choices_are_rejected():
    base = {
        "segments": normalize_segments(
            [{"text": f"Word {index}", "start": index, "end": index + 1} for index in range(201)]
        ),
        "speakers": {},
    }
    incoming = deepcopy(base)
    for passage in incoming["segments"]:
        passage["speaker"] = "VOICE"
    planned = result_conflicts.review_payload(base, base, incoming, incoming, [])
    assert len(planned["passages"]) == 200 and planned["truncated"]
    hidden = [{"passage_id": base["segments"][-1]["id"], "group": "speaker", "choice": "current"}]
    with pytest.raises(ValueError, match="displayed bounded preview"):
        result_conflicts.review_payload(base, base, incoming, incoming, hidden)
    choice = {"passage_id": "0", "group": "wording", "choice": "current"}
    with pytest.raises(ValueError, match="only once"):
        result_conflicts.validate_decisions([choice, choice])
    with pytest.raises(ValueError):
        result_conflicts.validate_decisions([{**choice, "choice": {}}])
