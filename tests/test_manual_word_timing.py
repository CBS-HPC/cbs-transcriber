"""Synthetic, revision-fenced manual word timing and provenance checks."""

import io
import json
from copy import deepcopy
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from cbs_transcriber.app import Edit
from cbs_transcriber.review_hints import current_hints
from cbs_transcriber.transcripts import (
    edit_mapped_word,
    edit_timed_word,
    merge_passages,
    normalize_segments,
    split_passage,
)


def passage():
    return normalize_segments(
        [
            {
                "text": "Hej København igen.",
                "start": 0,
                "end": 4,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1.2, "score": 0.2},
                    {"word": "København", "start": 1, "end": 2, "score": 0.4},
                    {"word": "igen.", "start": 2, "end": 3, "score": 0.6},
                ],
            }
        ]
    )[0]


def test_adjusts_one_word_and_optional_unicode_text_without_rewriting_other_times():
    original = passage()
    changed = edit_timed_word(original, 1, 1.1, 2.2, "Århus")
    assert original == passage()  # Input is never mutated.
    assert changed["text"] == "Hej Århus igen."
    assert changed["words"][0] == original["words"][0]
    assert changed["words"][1] == {
        **original["words"][1],
        "word": "Århus",
        "char_end": 9,
        "start": 1.1,
        "end": 2.2,
        "manual_timing": True,
    }
    assert changed["words"][2] == {
        **original["words"][2],
        "char_start": original["words"][2]["char_start"] - 4,
        "char_end": original["words"][2]["char_end"] - 4,
    }
    assert changed["original_words"] == original["original_words"]
    assert changed["original_text"] == original["original_text"]
    assert changed["word_timing"] and changed["words_current"] and changed["edited"]


@pytest.mark.parametrize(
    "index,start,end",
    [
        (True, 1, 2),
        (1, True, 2),
        (1, 1, float("nan")),
        (1, -1, 2),
        (1, 2, 2),
        (1, 1, 5),
        (1, 0.0, 1.0),
        (1, 2.1, 2.2),
        (4, 1, 2),
    ],
)
def test_invalid_or_unordered_intervals_are_rejected(index, start, end):
    with pytest.raises(ValueError):
        edit_timed_word(passage(), index, start, end)


@pytest.mark.parametrize(
    "changes",
    [
        {"word_index": True},
        {"revision": True},
        {"start": "1.2"},
        {"end": None},
        {"start": float("inf")},
        {"end": -1},
    ],
)
def test_request_rejects_coercion_and_invalid_numeric_fields(changes):
    value = {"mode": "timed_word", "word_index": 1, "revision": 2, "start": 1.0, "end": 2.0}
    value.update(changes)
    if changes == {"end": None}:
        body = Edit.model_validate(value)
        with pytest.raises(ValueError):
            edit_timed_word(passage(), body.word_index, body.start, body.end)
    else:
        with pytest.raises(ValidationError):
            Edit.model_validate(value)


def test_current_partial_mapping_can_be_timed_but_stale_or_unbounded_cannot():
    partial = passage()
    partial["words"][1]["start"] = partial["words"][1]["end"] = None
    partial["word_timing"] = False
    changed = edit_timed_word(partial, 1, 1.1, 2.2)
    assert changed["word_timing"] is False
    partial["text"] += " extra"
    changed = edit_timed_word(partial, 1, 1.1, 2.2)
    assert changed["word_timing"] is False and changed["words_current"] is True
    stale = deepcopy(partial)
    stale["edited"] = True
    with pytest.raises(ValueError, match="stale"):
        edit_timed_word(stale, 1, 1.1, 2.2)
    unbounded = deepcopy(partial)
    unbounded["start"] = None
    with pytest.raises(ValueError, match="bounds"):
        edit_timed_word(unbounded, 1, 1.1, 2.2)


def test_partial_mapped_word_text_preserves_absent_times_and_exact_other_data(client):
    original = passage()
    original.update(start=None, end=None, word_timing=False)
    original["words"][1].update(start=None, end=None, manual_timing=True)
    original["words"][0]["extra_provenance"] = {"retained": True}
    original["words_current"] = True
    original["edited"] = True
    saved = edit_mapped_word(original, 1, "Århus")
    assert saved["text"] == "Hej Århus igen."
    assert saved["word_timing"] is False
    assert saved["start"] is saved["end"] is None
    assert saved["words"][1]["start"] is saved["words"][1]["end"] is None
    assert saved["words"][1]["manual_timing"] is True
    assert saved["words"][0] == original["words"][0]
    assert saved["original_words"] == original["original_words"]
    assert saved["words"][2]["start"] == original["words"][2]["start"]
    assert saved["words"][2]["char_start"] == original["words"][2]["char_start"] - 4
    stale = deepcopy(original)
    stale.pop("words_current")
    with pytest.raises(ValueError, match="stale"):
        edit_mapped_word(stale, 1, "Århus")
    malformed = deepcopy(original)
    malformed["words"][2]["char_start"] += 1
    with pytest.raises(ValueError, match="mapping"):
        edit_mapped_word(malformed, 1, "Århus")
    store = client.app.state.store
    project = store.create_project("Synthetic partial word")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(recording["id"], [original], {}, "Partial", {}, auto_first=True)
    current = store.recording(recording["id"])
    response = client.patch(
        f"/api/recordings/{recording['id']}/segments/0",
        json={
            "revision": current["revision"],
            "mode": "mapped_word",
            "word_index": 1,
            "text": "Århus",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["segments"][0] == {
        key: value for key, value in saved.items() if key != "_hints"
    }
    assert store.result_versions(recording["id"], result["id"])[0]["action"] == "word_correction"
    stale_response = client.patch(
        f"/api/recordings/{recording['id']}/segments/0",
        json={
            "revision": current["revision"],
            "mode": "mapped_word",
            "word_index": 1,
            "text": "Aarhus",
        },
    )
    assert stale_response.status_code in (400, 409)


def test_manual_word_edit_is_atomic_historical_and_filters_only_changed_alignment(client):
    store = client.app.state.store
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    original = passage()
    result = store.add_result(
        recording["id"], [original], {"A": "Søren"}, "Synthetic", {}, auto_first=True
    )
    rid = recording["id"]
    before = store.recording(rid)
    result_id = result["id"]
    old_version = store.result_versions(rid, result_id)[0]
    hints = current_hints(store, rid, result_id)
    assert len(hints["passages"]["0"]["words"]) == 3
    response = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": before["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.1,
            "end": 2.2,
        },
    )
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["segments"][0]["text"] == original["text"]
    assert after["segments"][0]["words"][0] == original["words"][0]
    assert after["segments"][0]["words"][2] == original["words"][2]
    assert after["segments"][0]["words"][1]["manual_timing"] is True
    assert after["segments"][0]["original_words"] == original["original_words"]
    latest = store.result_versions(rid, result_id)[0]
    assert latest["action"] == "word_timing_edit"
    assert store.result_version(rid, result_id, old_version["id"])["payload"]["segments"] == [
        original
    ]
    assert after["result_roles"]["working"]["version_id"] == latest["id"]
    exported = client.get(f"/api/recordings/{rid}/export", params={"format": "json"})
    assert exported.status_code == 200
    exported_word = exported.json()["segments"][0]["words"][1]
    assert exported_word["manual_timing"] is True and exported_word["start"] == 1.1
    current = current_hints(store, rid, result_id)["passages"]["0"]
    assert current["words"] == [
        hints["passages"]["0"]["words"][0],
        hints["passages"]["0"]["words"][2],
    ]
    stale = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": before["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.2,
            "end": 2.3,
        },
    )
    assert stale.status_code in (400, 409)
    assert len(store.result_versions(rid, result_id)) == 2
    from report_support import selected_view_report

    report = selected_view_report(store, project["id"])
    with ZipFile(io.BytesIO(report)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["manual_change_counts"]["word_timing_edit"] == 1
    restored = client.post(
        f"/api/recordings/{rid}/transcripts/{result_id}/versions/{old_version['id']}/restore",
        json={"revision": after["revision"]},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["segments"][0]["words"] == original["words"]
    assert len(current_hints(store, rid, result_id)["passages"]["0"]["words"]) == 3


def test_manual_marker_survives_merge_split_and_split_restore_baseline():
    from cbs_transcriber.transcripts import edit_segment

    first = edit_timed_word(passage(), 1, 1.1, 2.2)
    second = normalize_segments(
        [
            {
                "text": "Slut.",
                "start": 4,
                "end": 5,
                "speaker": "A",
                "words": [{"word": "Slut.", "start": 4, "end": 5}],
            }
        ]
    )[0]
    second["id"] = "another"
    merged = merge_passages([first, second], "0", "down", "A", {})[0]
    assert merged["words"][1]["manual_timing"] is True
    halves = split_passage([merged], "0", len(first["text"]))
    assert halves[0]["words"][1]["manual_timing"] is True
    restored = edit_segment(halves[0], "restore")
    # Split re-baselines modified times; it must not claim those came from the model.
    assert restored["words"][1]["manual_timing"] is True


def test_enhancement_merge_and_speaker_only_split_retain_manual_marker():
    from cbs_transcriber.enhancement import EnhancementOptions, merge_output
    from cbs_transcriber.transcript_merge import merge_payload
    from cbs_transcriber.transcripts import edit_segment

    original = passage()
    original.pop("_hints", None)
    changed = edit_timed_word(original, 1, 1.1, 2.2)
    baseline = {"segments": [original], "speakers": {"A": "Søren"}}
    incoming = {"segments": [changed], "speakers": {"A": "Søren"}}
    merged = merge_payload(baseline, baseline, incoming, baseline)
    assert merged["summary"]["timing_updates"] == 1
    assert merged["segments"][0]["words"][1]["manual_timing"] is True
    fragments, _ = merge_output(
        incoming,
        None,
        {
            "turns": [
                {"start": 0, "end": 1.05, "speaker": "X"},
                {"start": 1.05, "end": 4, "speaker": "Y"},
            ]
        },
        EnhancementOptions(align=False, diarize=True),
        4,
        "synthetic",
    )
    assert len(fragments) == 2
    marked = next(
        fragment
        for fragment in fragments
        if any(word.get("manual_timing") for word in fragment["words"])
    )
    assert (
        next(word for word in marked["original_words"] if word["word"] == "København")[
            "manual_timing"
        ]
        is True
    )
    restored = edit_segment(marked, "restore")
    assert (
        next(word for word in restored["words"] if word["word"] == "København")["manual_timing"]
        is True
    )


def test_later_local_alignment_cannot_reanimate_old_alignment_hints():
    from cbs_transcriber.local_timing import _candidate
    from cbs_transcriber.review_hints import _current_alignment_hints

    original = passage()
    original.pop("_hints", None)
    manually_timed = edit_timed_word(original, 1, 1.1, 2.2)
    source_words = deepcopy(original["words"])
    later = _candidate(manually_timed, {"words": source_words, "word_timing": True})
    assert all(word.get("alignment_hints_stale") is True for word in later["words"])
    assert not any(word.get("manual_timing") for word in later["words"])
    hints = {"word_kind": "alignment", "words": [[0, 3, 0.2], [4, 13, 0.4], [14, 19, 0.6]]}
    assert _current_alignment_hints(hints, later)["words"] == []
    for key in ("manual_timing", "alignment_hints_stale"):
        forged = deepcopy(source_words)
        forged[0][key] = True
        with pytest.raises(ValueError, match="cannot assert"):
            _candidate(manually_timed, {"words": forged, "word_timing": True})


def test_transcription_confidence_not_removed_by_manual_timing():
    from cbs_transcriber.review_hints import _current_alignment_hints

    edited = edit_timed_word(passage(), 1, 1.1, 2.2)
    hints = {"word_kind": "transcription", "words": [[0, 3, 0.2], [4, 13, 0.4]]}
    assert _current_alignment_hints(hints, edited) == hints


def test_preserves_unicode_codepoint_offsets_and_unknown_word_metadata():
    source = passage()
    source["text"] = "Hej 🧪 København igen."
    source["original_text"] = source["text"]
    source["words"][1]["char_start"] += 2
    source["words"][1]["char_end"] += 2
    source["words"][2]["char_start"] += 2
    source["words"][2]["char_end"] += 2
    source["words"][0]["custom_source"] = {"untouched": "æøå"}
    source["words"][1]["custom_source"] = "literal"
    changed = edit_timed_word(source, 1, 1.1, 2.2, "Århus")
    assert changed["text"] == "Hej 🧪 Århus igen."
    assert changed["words"][0] == source["words"][0]
    assert changed["words"][1]["custom_source"] == "literal"
    assert (
        changed["text"][changed["words"][1]["char_start"] : changed["words"][1]["char_end"]]
        == "Århus"
    )


def test_noop_does_not_falsely_mark_timing_and_malformed_evidence_is_refused():
    source = passage()
    same = edit_timed_word(source, 1, 1, 2, "København")
    assert same == source
    for value in (False, 1, "true", None):
        corrupt = deepcopy(source)
        corrupt["words"][0]["manual_timing"] = value
        with pytest.raises(ValueError, match="inconsistent"):
            edit_timed_word(corrupt, 1, 1.1, 2.2)
    with pytest.raises(ValueError):
        edit_timed_word(source, 1, 10**1000, 10**1001)


def test_recording_api_refuses_ambiguous_passage_and_invalid_time(client):
    store = client.app.state.store
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(recording["id"], [passage()], {}, "Synthetic", {}, auto_first=True)
    current = store.recording(recording["id"])
    rid = recording["id"]
    for changes in ({"start": "1.1"}, {"word_index": True}, {"end": None}):
        body = {
            "revision": current["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.1,
            "end": 2.2,
            **changes,
        }
        assert client.patch(f"/api/recordings/{rid}/segments/0", json=body).status_code in (
            400,
            422,
        )
    duplicated = deepcopy(current["segments"])
    duplicated.append(deepcopy(duplicated[0]))
    duplicate = store.save_transcript(rid, duplicated, current["speakers"], current["revision"])
    response = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": duplicate["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.1,
            "end": 2.2,
        },
    )
    assert response.status_code in (400, 409)
    assert store.recording(rid)["segments"] == duplicate["segments"]


@pytest.mark.parametrize("make_working", [False, True])
def test_timed_word_uses_explicit_working_choice_atomically(client, make_working):
    store = client.app.state.store
    project = store.create_project("Synthetic Working timing")
    recording = store.create_recording(project["id"], "synthetic.wav")
    first = store.add_result(recording["id"], [passage()], {}, "First", {}, auto_first=True)
    first_version = store.recording(recording["id"])["active_version"]["id"]
    second = store.add_result(recording["id"], [passage()], {}, "Second", {})
    active = store.recording(recording["id"])
    active = store.activate_result(recording["id"], second["id"], active["revision"])
    choice = {
        "version_id": active["active_version"]["id"],
        "role_revision": active["result_roles"]["revision"],
        "make_working": make_working,
    }
    response = client.patch(
        f"/api/recordings/{recording['id']}/segments/0",
        json={
            "revision": active["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.1,
            "end": 2.2,
            "working_choice": choice,
        },
    )
    assert response.status_code == 200, response.text
    saved = response.json()
    expected = saved["active_version"]["id"] if make_working else first_version
    assert saved["result_roles"]["working"]["version_id"] == expected
    assert (
        store.result_version(recording["id"], first["id"], first_version)["payload"]["segments"][0][
            "words"
        ][1]["start"]
        == 1.0
    )


def test_stale_working_choice_rejects_timing_edit_without_writes(client):
    store = client.app.state.store
    project = store.create_project("Synthetic role race")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(recording["id"], [passage()], {}, "First", {}, auto_first=True)
    second = store.add_result(recording["id"], [passage()], {}, "Second", {})
    active = store.recording(recording["id"])
    active = store.activate_result(recording["id"], second["id"], active["revision"])
    rid = recording["id"]
    choice = {
        "version_id": active["active_version"]["id"],
        "role_revision": active["result_roles"]["revision"],
        "make_working": True,
    }
    body = {
        "version_id": active["active_version"]["id"],
        "revision": active["revision"],
        "role_revision": active["result_roles"]["revision"],
    }
    preview = client.post(f"/api/recordings/{rid}/working-preview", json=body)
    assert preview.status_code == 200, preview.text
    changed = client.post(
        f"/api/recordings/{rid}/working",
        json={**body, "fingerprint": preview.json()["fingerprint"]},
    )
    assert changed.status_code == 200, changed.text
    before = store.recording(rid)
    response = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": active["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.1,
            "end": 2.2,
            "working_choice": choice,
        },
    )
    assert response.status_code == 409, response.text
    after = store.recording(rid)
    assert after["revision"] == before["revision"]
    assert after["segments"] == before["segments"]
    assert after["result_roles"] == before["result_roles"]


def test_manual_word_timing_is_not_overwritten_by_earlier_alignment_job(client):
    store = client.app.state.store
    project = store.create_project("Synthetic stale alignment")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(recording["id"], [passage()], {}, "Synthetic", {}, auto_first=True)
    rid = recording["id"]
    current = store.recording(rid)
    job = store.enqueue_enhancement(
        rid,
        result["id"],
        current["revision"],
        {"realign": True, "segment_id": "0", "enhancement": {"execution": "local"}},
    )
    params = job["params"]
    params["realign_output"] = {
        "passages": {
            "0": {
                "words": deepcopy(current["segments"][0]["words"]),
                "word_timing": True,
            }
        }
    }
    store.update_job(job["id"], params=params, state="completed")
    manual = client.patch(
        f"/api/recordings/{rid}/segments/0",
        json={
            "revision": current["revision"],
            "mode": "timed_word",
            "word_index": 1,
            "start": 1.1,
            "end": 2.2,
        },
    )
    assert manual.status_code == 200, manual.text
    after = manual.json()
    apply = client.post(
        f"/api/recordings/{rid}/realign/{job['id']}", json={"revision": after["revision"]}
    )
    assert apply.status_code == 409
    assert store.recording(rid)["segments"] == after["segments"]
    assert store.job(job["id"])["params"]["realign_applied"] == "stale"
