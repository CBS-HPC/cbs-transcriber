"""Structural history reads reuse validation facts, never mutable snapshots or authority."""

import cProfile
import json
import pstats
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import pytest

from cbs_transcriber import history, passage_undo
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import merge_passages, normalize_segments, split_passage


def seed(store, count=600):
    project = store.create_project(
        "Synthetic bounded undo performance", storage_parent=store.root / "projects"
    )
    recording = store.create_recording(project["id"], "synthetic.wav")
    rows = []
    for index in range(count):
        tokens = [f"ord{(index + offset) % 23}" for offset in range(15)]
        words = [
            {
                "word": token,
                "start": index * 8 + offset * 0.4,
                "end": index * 8 + offset * 0.4 + 0.3,
                "probability": 0.91,
            }
            for offset, token in enumerate(tokens)
        ]
        rows.append(
            {
                "text": " ".join(tokens) + ".",
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "speaker": f"S{index % 3}",
                "words": words,
                "avg_logprob": -0.15,
                "no_speech_prob": 0.01,
                "compression_ratio": 1.4,
            }
        )
    segments = normalize_segments(rows)
    store.add_result(
        recording["id"],
        segments,
        {f"S{i}": f"Voice {i}" for i in range(3)},
        "Synthetic",
        {"kind": "provider", "capability": "words_speakers"},
        auto_first=True,
    )
    row = store.recording(recording["id"])

    def save(parts, action="manual_save", context=None):
        nonlocal row
        row = store.save_transcript(
            row["id"],
            parts,
            row["speakers"],
            row["revision"],
            result_id=row["active_transcript_id"],
            action=action,
            context=context,
        )

    for _ in range(3):
        before = row
        merged = merge_passages(before["segments"], "3", "down", "S0", before["speakers"])
        save(
            merged,
            "passage_merge",
            {"segment_id": "3", "adjacent_segment_id": "4", "direction": "down"},
        )
        save(
            before["segments"],
            "passage_merge_undo",
            {"segment_id": "3", "restored_from_version_id": before["active_version"]["id"]},
        )
    cursor = len(" ".join(row["segments"][0]["text"].split()[:3]) + " ")
    for _ in range(3):
        before = row
        parts = split_passage(row["segments"], "0", cursor)
        right_id = parts[1]["id"]
        save(parts, "passage_split", {"segment_id": "0", "cursor": cursor})
        save(
            before["segments"],
            "passage_split_undo",
            {
                "segment_id": "0",
                "adjacent_segment_id": right_id,
                "restored_from_version_id": before["active_version"]["id"],
            },
        )
    for _ in range(13):
        save(row["segments"], "manual_save")
    before_split = row
    save(
        split_passage(row["segments"], "0", cursor),
        "passage_split",
        {"segment_id": "0", "cursor": cursor},
    )
    return row, before_split, cursor


def test_warm_history_validation_does_not_repeat_canonical_encoding(tmp_path, monkeypatch):
    store = Store(tmp_path / "library")
    row, _, _ = seed(store, 12)
    for version in store.all(
        "SELECT payload,payload_sha256 FROM result_versions WHERE result_id=?",
        (row["active_transcript_id"],),
    ):
        history.verify_payload(version["payload"], version["payload_sha256"])
    encoded = []
    original = history.encode_payload

    def encode(*args, **kwargs):
        encoded.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(history, "encode_payload", encode)
    available = passage_undo.options(store, row["id"], row["active_transcript_id"])
    assert available["split_segment_ids"] == ["0"]
    assert encoded == []


def test_cold_validation_runs_once_but_each_read_returns_fresh_nested_objects(monkeypatch):
    segments = [{"id": uuid4().hex, "text": "Æ café", "source": {"values": [None, "original"]}}]
    payload, digest = history.encode_payload(segments, {"A": "Interviewer"})
    decoded = []
    original = history.decode_payload

    def decode(*args):
        decoded.append(args)
        return original(*args)

    monkeypatch.setattr(history, "decode_payload", decode)
    first = passage_undo._read_payload(payload, digest)
    first["segments"][0]["source"]["values"][1] = "caller mutation"
    first["speakers"]["A"] = "caller rename"
    second = passage_undo._read_payload(payload, digest)
    assert len(decoded) == 1
    assert second == {
        "format": history.FORMAT,
        "segments": segments,
        "speakers": {"A": "Interviewer"},
    }
    assert first["segments"] is not second["segments"]
    assert first["segments"][0]["source"] is not second["segments"][0]["source"]


@pytest.mark.parametrize("refreshed_hash", [False, True])
def test_warm_validation_still_refuses_changed_or_noncanonical_actual_bytes(refreshed_hash):
    payload, digest = history.encode_payload([{"id": uuid4().hex, "text": "Synthetic"}], {})
    passage_undo._read_payload(payload, digest)
    changed = payload + " "
    supplied_hash = history.text_hash(changed) if refreshed_hash else digest
    with pytest.raises(ValueError, match="integrity check"):
        passage_undo._read_payload(changed, supplied_hash)


def test_identical_verified_payload_cannot_authorize_a_foreign_result(tmp_path):
    store = Store(tmp_path / "library")
    row, _, _ = seed(store, 12)
    assert passage_undo.options(store, row["id"], row["active_transcript_id"])[
        "split_segment_ids"
    ] == ["0"]
    foreign = store.create_recording(row["project_id"], "other-synthetic.wav")
    other_result = store.add_result(
        foreign["id"], row["segments"], row["speakers"], "Other", {}, auto_first=True
    )
    original_payload = store.one(
        "SELECT payload FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT 1",
        (row["active_transcript_id"],),
    )["payload"]
    foreign_payload = store.one(
        "SELECT payload FROM result_versions WHERE result_id=?", (other_result["id"],)
    )["payload"]
    assert foreign_payload == original_payload
    before = store.all("SELECT * FROM result_versions ORDER BY rowid")
    with pytest.raises(ValueError, match="saved result changed"):
        passage_undo.options(store, row["id"], other_result["id"])
    with pytest.raises(ValueError, match="saved result changed"):
        passage_undo.apply_split(store, row["id"], other_result["id"], "0", row["revision"])
    assert store.all("SELECT * FROM result_versions ORDER BY rowid") == before


def scan(store, row, target=None):
    with store.connection() as db:
        db.execute("BEGIN")
        _, head, payload = passage_undo._read_current(
            store, db, row["id"], row["active_transcript_id"], row["revision"]
        )
        return passage_undo._candidates(
            db, row["id"], row["active_transcript_id"], payload, head["sequence"], target=target
        )


@pytest.mark.parametrize("kind", ["merge", "split"])
@pytest.mark.parametrize(
    "history_case",
    ["current", "restored", "corrupt_newer", "corrupt_older", "limit_before", "limit_after"],
)
def test_targeted_apply_proof_equals_full_scan(tmp_path, monkeypatch, kind, history_case):
    store = Store(tmp_path / "library")
    row, _, _ = seed(store, 12)
    if kind == "merge":
        adjacent = row["segments"][1]["id"]
        row = store.save_transcript(
            row["id"],
            merge_passages(row["segments"], "0", "down", None, row["speakers"]),
            row["speakers"],
            row["revision"],
            result_id=row["active_transcript_id"],
            action="passage_merge",
            context={"segment_id": "0", "adjacent_segment_id": adjacent, "direction": "down"},
        )
    if history_case in {"restored", "corrupt_newer"}:
        row = store.save_transcript(
            row["id"],
            row["segments"],
            row["speakers"],
            row["revision"],
            result_id=row["active_transcript_id"],
            action="version_restore" if history_case == "restored" else f"passage_{kind}",
            context={"restored_from_version_id": row["active_version"]["id"]}
            if history_case == "restored"
            else {"segment_id": "0"},
        )
    elif history_case == "corrupt_older":
        with store.connection() as db:
            db.execute(
                "UPDATE result_versions SET payload_sha256=? WHERE result_id=? AND sequence=2",
                ("0" * 64, row["active_transcript_id"]),
            )
    elif history_case == "limit_before":
        monkeypatch.setattr(passage_undo, "MAX_HISTORY_BYTES", 1)
    elif history_case == "limit_after":
        sizes = store.one(
            "SELECT length(CAST(m.payload AS BLOB))+length(CAST(p.payload AS BLOB)) AS total "
            "FROM result_versions m JOIN result_versions p ON p.id=m.predecessor_id WHERE m.id=?",
            (row["active_version"]["id"],),
        )["total"]
        monkeypatch.setattr(passage_undo, "MAX_HISTORY_BYTES", sizes)
    complete = scan(store, row)
    targeted = scan(store, row, (kind, "0"))
    map_index = 1 if kind == "split" else 0
    assert targeted[map_index].get("0") == complete[map_index].get("0")
    assert ("0" in complete[map_index]) is (history_case != "limit_before")


def test_targeted_nested_split_keeps_outer_refused_until_inner_is_undone(tmp_path):
    store = Store(tmp_path / "library")
    row, _, _ = seed(store, 12)
    right = row["segments"][1]
    cursor = len(" ".join(right["text"].split()[:3]) + " ")
    row = store.save_transcript(
        row["id"],
        split_passage(row["segments"], right["id"], cursor),
        row["speakers"],
        row["revision"],
        result_id=row["active_transcript_id"],
        action="passage_split",
        context={"segment_id": right["id"], "cursor": cursor},
    )
    complete = scan(store, row)
    assert complete[1].get("0") is None
    assert scan(store, row, ("split", "0"))[1].get("0") is None
    assert scan(store, row, ("split", right["id"]))[1][right["id"]] == complete[1][right["id"]]


def test_targeted_apply_stops_only_after_verified_target(tmp_path, monkeypatch):
    store = Store(tmp_path / "library")
    row, _, _ = seed(store, 12)
    reads = []
    original = passage_undo._read_payload

    def read(*args):
        reads.append(args[1])
        return original(*args)

    monkeypatch.setattr(passage_undo, "_read_payload", read)
    complete = scan(store, row)
    full_count = len(reads)
    reads.clear()
    targeted = scan(store, row, ("split", "0"))
    assert targeted[1]["0"] == complete[1]["0"]
    assert len(reads) == 3  # Fresh current snapshot plus both exact proof snapshots.
    assert full_count > len(reads)


@pytest.fixture
def mirror_recording(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project(
        "Synthetic mirror authority", storage_parent=tmp_path / "projects"
    )
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Exact æ café", "speaker": "A"}]),
        {"A": "Named voice"},
        "Synthetic",
        {},
        auto_first=True,
    )
    return store, store.recording(recording["id"])


def read_current(store, row, revision=None):
    with store.connection() as db:
        db.execute("BEGIN")
        return passage_undo._read_current(
            store,
            db,
            row["id"],
            row["active_transcript_id"],
            row["revision"] if revision is None else revision,
        )


@pytest.mark.parametrize("different_format", [False, True])
def test_current_mirror_reuses_verified_result_only_when_both_raw_fields_match(
    mirror_recording, monkeypatch, different_format
):
    store, row = mirror_recording
    if different_format:
        store.execute(
            "UPDATE recordings SET segments=?,speakers=? WHERE id=?",
            (
                json.dumps(row["segments"], indent=2),
                json.dumps(row["speakers"], indent=2),
                row["id"],
            ),
        )
    order = []
    head, parse, encode = (
        store._head_version,
        passage_undo._read_payload,
        passage_undo.encode_payload,
    )

    def verified_head(*args):
        order.append("head")
        return head(*args)

    def fresh_payload(*args):
        order.append("fresh payload")
        return parse(*args)

    def encoded_mirror(*args):
        order.append("mirror")
        return encode(*args)

    monkeypatch.setattr(store, "_head_version", verified_head)
    monkeypatch.setattr(passage_undo, "_read_payload", fresh_payload)
    monkeypatch.setattr(passage_undo, "encode_payload", encoded_mirror)
    _, _, payload = read_current(store, row)
    assert payload["segments"] == row["segments"]
    assert payload["speakers"] == row["speakers"]
    assert order == ["head", "fresh payload"] + (["mirror"] if different_format else [])


@pytest.mark.parametrize("field", ["segments", "speakers"])
@pytest.mark.parametrize("damage", ["malformed_mirror", "equal_malformed", "changed_value"])
def test_current_mirror_shortcut_refuses_malformed_or_different_content(
    mirror_recording, field, damage
):
    store, row = mirror_recording
    value = (
        "{"
        if damage != "changed_value"
        else json.dumps(
            [{"text": "different"}] if field == "segments" else {"A": "Different voice"}
        )
    )
    store.execute(f"UPDATE recordings SET {field}=? WHERE id=?", (value, row["id"]))
    if damage == "equal_malformed":
        store.execute(
            f"UPDATE transcript_results SET {field}=? WHERE id=?",
            (value, row["active_transcript_id"]),
        )
    before = store.one("SELECT * FROM recordings WHERE id=?", (row["id"],))
    with pytest.raises(ValueError):
        read_current(store, row)
    assert store.one("SELECT * FROM recordings WHERE id=?", (row["id"],)) == before


def test_current_mirror_shortcut_cannot_bypass_stale_revision(mirror_recording):
    store, row = mirror_recording
    with pytest.raises(ValueError, match="saved result changed"):
        read_current(store, row, row["revision"] + 1)


def test_identical_foreign_result_mirror_cannot_bypass_owned_result_lookup(mirror_recording):
    store, row = mirror_recording
    foreign = store.create_recording(row["project_id"], "foreign-synthetic.wav")
    result = store.add_result(
        foreign["id"], row["segments"], row["speakers"], "Foreign", {}, auto_first=True
    )
    # Synthetic corruption makes the active pointer and identical raw mirror agree,
    # but the result still belongs to another recording and must never authorize undo.
    store.execute(
        "UPDATE recordings SET active_transcript_id=? WHERE id=?", (result["id"], row["id"])
    )
    row["active_transcript_id"] = result["id"]
    with pytest.raises(ValueError, match="saved result is unavailable"):
        read_current(store, row)


@pytest.mark.parametrize("damage", ["head_hash", "equal_changed_mirrors"])
def test_matching_mirrors_still_require_fresh_valid_head_after_warming(mirror_recording, damage):
    store, row = mirror_recording
    read_current(store, row)
    if damage == "head_hash":
        store.execute(
            "UPDATE result_versions SET payload_sha256=? WHERE result_id=?",
            ("0" * 64, row["active_transcript_id"]),
        )
    else:
        changed = json.dumps(normalize_segments([{"text": "Changed but not recorded in history"}]))
        store.execute("UPDATE recordings SET segments=? WHERE id=?", (changed, row["id"]))
        store.execute(
            "UPDATE transcript_results SET segments=? WHERE id=?",
            (changed, row["active_transcript_id"]),
        )
    before = store.one("SELECT * FROM recordings WHERE id=?", (row["id"],))
    with pytest.raises(ValueError, match="saved research version disagree"):
        read_current(store, row)
    assert store.one("SELECT * FROM recordings WHERE id=?", (row["id"],)) == before


def test_warmed_current_read_rechecks_legitimate_new_head_and_revision(mirror_recording):
    store, row = mirror_recording
    original = read_current(store, row)[2]
    segments = normalize_segments([{"text": "A later legitimate edit", "speaker": "A"}])
    newer = store.save_transcript(
        row["id"], segments, row["speakers"], row["revision"], result_id=row["active_transcript_id"]
    )
    assert read_current(store, newer)[2]["segments"] == segments
    assert original["segments"] == row["segments"]
    with pytest.raises(ValueError, match="saved result changed"):
        read_current(store, row)


def benchmark(output):
    def profile(function):
        profiler = cProfile.Profile()
        started = time.perf_counter()
        result = profiler.runcall(function)
        elapsed = time.perf_counter() - started
        stats = pstats.Stats(profiler)
        rows = sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:25]
        return (
            result,
            elapsed,
            [
                {
                    "file": key[0],
                    "line": key[1],
                    "function": key[2],
                    "calls": value[1],
                    "self_seconds": value[2],
                    "cumulative_seconds": value[3],
                }
                for key, value in rows
            ],
        )

    with tempfile.TemporaryDirectory(prefix="cbs-undo-perf-") as temporary:
        store = Store(Path(temporary) / "library")
        row, prior, cursor = seed(store)
        version_count = store.one("SELECT count(*) AS n FROM result_versions")["n"]
        payload_bytes = len(
            store.one("SELECT payload FROM result_versions ORDER BY sequence DESC LIMIT 1")[
                "payload"
            ].encode()
        )
        options_times, apply_times, profiles = [], [], {}
        for index in range(3):
            started = time.perf_counter()
            available = passage_undo.options(store, row["id"], row["active_transcript_id"])
            options_times.append(time.perf_counter() - started)
            assert available["split_segment_ids"] == ["0"]
            started = time.perf_counter()
            row = passage_undo.apply_split(
                store, row["id"], row["active_transcript_id"], "0", row["revision"]
            )
            apply_times.append(time.perf_counter() - started)
            assert row["segments"] == prior["segments"]
            if index != 2:
                row = store.save_transcript(
                    row["id"],
                    split_passage(row["segments"], "0", cursor),
                    row["speakers"],
                    row["revision"],
                    result_id=row["active_transcript_id"],
                    action="passage_split",
                    context={"segment_id": "0", "cursor": cursor},
                )
        row = store.save_transcript(
            row["id"],
            split_passage(row["segments"], "0", cursor),
            row["speakers"],
            row["revision"],
            result_id=row["active_transcript_id"],
            action="passage_split",
            context={"segment_id": "0", "cursor": cursor},
        )
        _, profiles["options_seconds"], profiles["options"] = profile(
            lambda: passage_undo.options(store, row["id"], row["active_transcript_id"])
        )
        restored, profiles["apply_seconds"], profiles["apply"] = profile(
            lambda: passage_undo.apply_split(
                store, row["id"], row["active_transcript_id"], "0", row["revision"]
            )
        )
        assert restored["segments"] == prior["segments"]
        Path(output).write_text(
            json.dumps(
                {
                    "passages": 600,
                    "words_per_passage": 15,
                    "initial_versions": version_count,
                    "snapshot_bytes": payload_bytes,
                    "options_seconds": options_times,
                    "apply_seconds": apply_times,
                    "profiles": profiles,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    benchmark(sys.argv[1])
