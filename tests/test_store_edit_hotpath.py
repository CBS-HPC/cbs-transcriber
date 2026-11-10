"""Bounded save serialization retains mirror, history, Working and snapshot guarantees."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_legacy_replay_context import legacy_result

from cbs_transcriber import history
from cbs_transcriber import store as store_module
from cbs_transcriber.history import decode_payload, encode_payload
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import (
    edit_text_retaining_timing,
    merge_passages,
    normalize_segments,
)


def seeded(tmp_path, count=6):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic save hotpath")
    rows = []
    for index in range(count):
        words = ["Møller", "🧪", "e\u0301"] * 5
        rows.append(
            {
                "text": " ".join(words),
                "start": index * 20.0,
                "end": index * 20.0 + 15,
                "speaker": f"S{index % 3}",
                "words": [
                    {"word": word, "start": index * 20.0 + n, "end": index * 20.0 + n + 0.5}
                    for n, word in enumerate(words)
                ],
            }
        )
    segments = normalize_segments(rows)
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"], segments, {"S0": "Øyvind", "S1": "Åse"}, "Synthetic", {}, auto_first=True
    )
    return store, project, store.recording(recording["id"])


def state(store, project):
    folder = Path(project["storage_dir"])
    return {
        "recordings": store.all("SELECT * FROM recordings ORDER BY id"),
        "results": store.all("SELECT * FROM transcript_results ORDER BY id"),
        "versions": store.all("SELECT * FROM result_versions ORDER BY result_id,sequence"),
        "roles": store.all("SELECT * FROM recording_result_roles ORDER BY recording_id"),
        "events": store.all("SELECT * FROM result_role_events ORDER BY recording_id,sequence"),
        "files": {
            str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob("*")
            if path.is_file()
        },
    }


def save(store, current, *, action="manual_save", context=None):
    return store.save_transcript(
        current["id"],
        current["segments"],
        current["speakers"],
        current["revision"],
        action=action,
        context=context,
    )


@pytest.mark.parametrize("action", ["text", "rename", "assign", "merge"])
def test_verified_matching_mirrors_need_only_head_and_successor_encodes(
    tmp_path, monkeypatch, action
):
    store, project, current = seeded(tmp_path, count=600 if action == "text" else 6)
    before = state(store, project)
    if action == "text":
        current["segments"][0] = edit_text_retaining_timing(
            current["segments"][0], current["segments"][0]["text"] + " checked"
        )[0]
    elif action == "rename":
        current["speakers"]["S0"] = "Bjørn e\u0301 🧪"
    elif action == "assign":
        current["segments"][0]["speaker"] = "S2"
    else:
        current["segments"] = merge_passages(
            current["segments"], current["segments"][0]["id"], "down", "S0", current["speakers"]
        )
    expected = deepcopy(current)
    calls, original = [], store_module.encode_payload

    def encode(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(store_module, "encode_payload", encode)
    monkeypatch.setattr(history, "encode_payload", encode)
    after = save(store, current)
    assert len(calls) == 1  # Prior head's actual byte binding, then stored successor encoding.
    assert after["segments"] == expected["segments"] and after["speakers"] == expected["speakers"]
    versions = store.all("SELECT * FROM result_versions ORDER BY sequence")
    assert versions[:-1] == before["versions"]
    head = versions[-1]
    payload, digest = encode_payload(expected["segments"], expected["speakers"])
    assert (head["payload"], head["payload_sha256"]) == (payload, digest)
    assert decode_payload(payload, digest)["segments"] == expected["segments"]
    assert head["predecessor_id"] == before["versions"][-1]["id"]
    assert after["result_roles"]["working"]["version_id"] == head["id"]
    assert after["result_roles"]["revision"] == current["result_roles"]["revision"] + 1
    stored = store.one("SELECT segments,speakers FROM recordings WHERE id=?", (current["id"],))
    result = store.one(
        "SELECT segments,speakers FROM transcript_results WHERE id=?", (head["result_id"],)
    )
    assert stored == result
    # Consolidation sorts new mirror keys; decoded values/canonical history are preserved.
    assert stored["segments"] == json.dumps(
        expected["segments"], ensure_ascii=False, sort_keys=True
    )
    assert stored["speakers"] == json.dumps(
        expected["speakers"], ensure_ascii=False, sort_keys=True
    )
    snapshot = json.loads((Path(project["storage_dir"]) / "project.json").read_text("utf-8"))
    assert snapshot["recordings"][0]["segments"] == expected["segments"]
    assert after["snapshot_error"] is None


@pytest.mark.parametrize("field", ["segments", "speakers", "both"])
def test_legacy_differently_formatted_mirrors_use_canonical_fallback(tmp_path, monkeypatch, field):
    store, project, current = seeded(tmp_path)
    changed = ["segments", "speakers"] if field == "both" else [field]
    for key in changed:
        store.execute(
            f"UPDATE recordings SET {key}=? WHERE id=?",
            (json.dumps(current[key], ensure_ascii=True, indent=3), current["id"]),
        )
    original, calls = store_module.encode_payload, []

    def encode(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(store_module, "encode_payload", encode)
    monkeypatch.setattr(history, "encode_payload", encode)
    current["speakers"]["S0"] = "Renamed"
    after = save(store, current)
    assert len(calls) == 2
    assert after["segments"] == current["segments"]
    assert after["speakers"] == current["speakers"]
    assert after["snapshot_error"] is None


@pytest.mark.parametrize("damage", ["segments", "speakers", "head_hash"])
def test_fast_path_still_refuses_corrupt_mirrors_and_prior_head(tmp_path, damage):
    store, project, current = seeded(tmp_path)
    if damage == "head_hash":
        store.execute(
            "UPDATE result_versions SET payload_sha256=? WHERE id=?",
            ("0" * 64, current["active_version"]["id"]),
        )
    else:
        modified = deepcopy(current[damage])
        if damage == "segments":
            modified[0]["text"] = "Unverified mirror change"
        else:
            modified["S0"] = "Unverified mirror name"
        store.execute(
            f"UPDATE recordings SET {damage}=? WHERE id=?", (json.dumps(modified), current["id"])
        )
    before = state(store, project)
    with pytest.raises(RuntimeError, match="disagree"):
        save(store, current)
    assert state(store, project) == before


@pytest.mark.parametrize(
    "invalid",
    [
        "tuple",
        "speakers_list",
        "nan",
        "infinity",
        "mixed_keys",
        "object",
        "surrogate_text",
        "surrogate_speaker_key",
        "surrogate_speaker_value",
    ],
)
def test_input_refusals_still_happen_before_opening_writer(tmp_path, monkeypatch, invalid):
    store, project, current = seeded(tmp_path)
    before = state(store, project)
    expected_error = ValueError
    if invalid == "tuple":
        current["segments"] = tuple(current["segments"])
    elif invalid == "speakers_list":
        current["speakers"] = []
    elif invalid in {"nan", "infinity"}:
        current["segments"][0]["extra"] = float("nan" if invalid == "nan" else "inf")
    elif invalid == "mixed_keys":
        current["segments"][0]["extra"] = {1: "one", "two": "two"}
        expected_error = TypeError
    elif invalid.startswith("surrogate"):
        expected_error = UnicodeEncodeError
        if invalid == "surrogate_text":
            current["segments"][0]["text"] = "Unpaired \ud800"
        elif invalid == "surrogate_speaker_key":
            current["speakers"]["Unpaired \udfff"] = "Name"
        else:
            current["speakers"]["S0"] = "Unpaired \udfff"
    else:
        current["segments"][0]["extra"] = object()
        expected_error = TypeError

    def refuse_writer():
        raise AssertionError("Invalid input reached the transaction")

    with monkeypatch.context() as patch:
        patch.setattr(store, "connection", refuse_writer)
        with pytest.raises(expected_error) as caught:
            save(store, current)
        if invalid in {"tuple", "speakers_list"}:
            assert str(caught.value) == "A result snapshot requires passages and speaker labels."
    assert state(store, project) == before


def test_supported_json_key_and_tuple_normalization_still_has_canonical_history(tmp_path):
    store, project, current = seeded(tmp_path)
    current["segments"][0]["metadata"] = {2: ("æ", "e\u0301"), 10: "🧪"}
    after = save(store, current)
    assert after["segments"][0]["metadata"] == {"2": ["æ", "e\u0301"], "10": "🧪"}
    head = store.one("SELECT * FROM result_versions ORDER BY sequence DESC LIMIT 1")
    assert decode_payload(head["payload"], head["payload_sha256"])["segments"] == after["segments"]
    assert hashlib.sha256(head["payload"].encode()).hexdigest() == head["payload_sha256"]


def test_stale_revision_keeps_roles_history_projection_and_snapshot_unchanged(tmp_path):
    store, project, current = seeded(tmp_path)
    after = save(store, deepcopy(current))
    before = state(store, project)
    current["speakers"]["S0"] = "Stale name"
    with pytest.raises(ValueError, match="another window"):
        save(store, current)
    assert state(store, project) == before
    assert store.recording(current["id"])["revision"] == after["revision"]


def test_snapshot_failure_still_commits_library_and_reports_error_until_repaired(
    tmp_path, monkeypatch
):
    store, project, current = seeded(tmp_path)
    folder = Path(project["storage_dir"])
    before_snapshot = (folder / "project.json").read_bytes()
    original = Path.write_text

    def fail(path, *args, **kwargs):
        if path.name == "project.pending":
            raise OSError("Synthetic snapshot storage failure")
        return original(path, *args, **kwargs)

    current["speakers"]["S0"] = "Durably saved"
    monkeypatch.setattr(Path, "write_text", fail)
    after = save(store, current)
    assert "database" in after["snapshot_error"]
    assert after["speakers"]["S0"] == "Durably saved"
    assert after["revision"] == current["revision"] + 1
    assert after["result_roles"]["working"]["version_id"] == after["active_version"]["id"]
    assert (folder / "project.json").read_bytes() == before_snapshot
    monkeypatch.setattr(Path, "write_text", original)
    repaired = save(store, after)
    assert repaired["snapshot_error"] is None
    assert (
        json.loads((folder / "project.json").read_text("utf-8"))["recordings"][0]["speakers"]["S0"]
        == "Durably saved"
    )


@pytest.mark.parametrize("legacy_format", [False, True])
def test_recording_decode_reuse_is_local_and_legacy_replay_derivation_is_read_only(
    tmp_path, monkeypatch, legacy_format
):
    store, recording, source, _, result = legacy_result(tmp_path / "library")
    current = store.recording(recording["id"])
    store.activate_result(recording["id"], result["id"], current["revision"])
    raw = store.one("SELECT segments FROM recordings WHERE id=?", (recording["id"],))["segments"]
    current_raw = raw
    if legacy_format:
        current_raw = json.dumps(json.loads(raw), ensure_ascii=True, indent=3)
        assert current_raw != raw
        store.execute("UPDATE recordings SET segments=? WHERE id=?", (current_raw, recording["id"]))
    before = store.all("SELECT * FROM transcript_results ORDER BY id")
    reads, views = [], []
    original_loads, original_replay = json.loads, store._legacy_replay_windows

    def loads(value, *args, **kwargs):
        if value in {raw, current_raw}:
            reads.append(value)
        return original_loads(value, *args, **kwargs)

    def replay(active):
        unchanged = deepcopy(active)
        views.append(active["segments"])
        windows = original_replay(active)
        assert active == unchanged
        return windows

    monkeypatch.setattr(store_module.json, "loads", loads)
    monkeypatch.setattr(store, "_legacy_replay_windows", replay)
    first = store.recording(recording["id"])
    assert len(reads) == (2 if legacy_format else 1)
    assert (views[-1] is first["segments"]) == (not legacy_format)
    assert first["replay_windows"] == {
        "3": {"start": 2, "end": 5, "source_result_id": source["id"], "source_segment_id": "1"}
    }
    expected = deepcopy(first["segments"])
    first["segments"][0]["text"] = "Unsaved caller-side mutation"
    second = store.recording(recording["id"])
    assert second["segments"] == expected and second["segments"] is not first["segments"]
    assert second["replay_windows"] == first["replay_windows"]
    assert store.all("SELECT * FROM transcript_results ORDER BY id") == before


@pytest.mark.parametrize("damaged", ["recording", "active", "both"])
def test_recording_read_still_parses_and_refuses_malformed_mirrors(tmp_path, damaged):
    store, _, current = seeded(tmp_path)
    if damaged in {"recording", "both"}:
        store.execute("UPDATE recordings SET segments=? WHERE id=?", ("[broken", current["id"]))
    if damaged in {"active", "both"}:
        store.execute(
            "UPDATE transcript_results SET segments=? WHERE id=?",
            ("[broken", current["active_transcript_id"]),
        )
    with pytest.raises(json.JSONDecodeError):
        store.recording(current["id"])


def test_recording_decode_reuse_never_bypasses_current_active_result_ownership(tmp_path):
    store, project, current = seeded(tmp_path)
    other = store.create_recording(project["id"], "other.wav")
    result = store.add_result(other["id"], current["segments"], current["speakers"], "Other", {})
    # Its mirror bytes deliberately match, but its result belongs to another recording.
    store.execute(
        "UPDATE recordings SET active_transcript_id=? WHERE id=?", (result["id"], current["id"])
    )
    with pytest.raises(RuntimeError, match="active result disagree"):
        store.recording(current["id"])
