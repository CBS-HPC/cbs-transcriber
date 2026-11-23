"""Completed content checks can be reused; current bytes and mutation authority cannot."""

import json

import pytest

from cbs_transcriber import history
from cbs_transcriber import passage_review as subject
from cbs_transcriber.history import encode_payload
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture(autouse=True)
def empty_memo():
    with subject._CONTEXT_LOCK:
        subject._CONTEXTS.clear()
    yield
    with subject._CONTEXT_LOCK:
        subject._CONTEXTS.clear()


@pytest.fixture
def scene(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic review speed")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Private synthetic æøå"}, {"text": "Second"}]),
        {"A": "Speaker"},
        "Synthetic",
        {},
        auto_first=True,
    )
    return store, recording["id"], result["id"]


def read(scene):
    return subject.read(*scene)


def save(scene, loaded):
    item = loaded["passages"][0]
    return subject.save(
        scene[0],
        scene[1],
        item["segment_id"],
        result_id=scene[2],
        revision=loaded["revision"],
        status="checked",
        review_revision=item["review_revision"],
        passage_sha256=item["passage_sha256"],
    )


def counted(monkeypatch, module, name):
    calls = []
    original = getattr(module, name)

    def wrapper(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, wrapper)
    return calls


def test_identical_mirrors_encode_once_and_warm_marks_skip_full_content_work(scene, monkeypatch):
    # ADR 0193: identical mirrors need one binding check, and the producer's proven binding
    # (ADR 0159) means no canonical re-encoding; fingerprints come from verified slices.
    heads = counted(monkeypatch, subject, "verified_passage_texts")
    mirrors = counted(monkeypatch, subject, "serialized_payload_matches")
    encodes = counted(monkeypatch, history, "encode_payload")
    fingerprints = counted(monkeypatch, subject, "fingerprint")
    loaded = read(scene)
    assert (heads, mirrors, encodes, fingerprints) == ([True], [True], [], [])

    def unexpected(*args, **kwargs):
        raise AssertionError("A warm unchanged mark must not redo full payload work")

    monkeypatch.setattr(subject, "verified_passage_texts", unexpected)
    monkeypatch.setattr(subject, "serialized_payload_matches", unexpected)
    monkeypatch.setattr(history, "encode_payload", unexpected)
    monkeypatch.setattr(subject, "fingerprint", unexpected)
    assert save(scene, loaded)["counts"]["checked"] == 1
    assert read(scene)["counts"]["checked"] == 1


@pytest.mark.parametrize(
    "damage",
    ["result_text", "recording_text", "result_speakers", "recording_speakers", "payload", "hash"],
)
def test_warm_checks_still_refuse_changed_bytes_or_speakers(scene, damage):
    store, rid, result_id = scene
    loaded = read(scene)
    head = store.result_versions(rid, result_id)[0]
    if damage.endswith("text"):
        table = "transcript_results" if damage.startswith("result") else "recordings"
        identifier = result_id if table == "transcript_results" else rid
        row = store.one(f"SELECT segments FROM {table} WHERE id=?", (identifier,))
        segments = json.loads(row["segments"])
        segments[0]["text"] = "Different bytes"
        store.execute(
            f"UPDATE {table} SET segments=? WHERE id=?", (json.dumps(segments), identifier)
        )
    elif damage.endswith("speakers"):
        table = "transcript_results" if damage.startswith("result") else "recordings"
        identifier = result_id if table == "transcript_results" else rid
        store.execute(f"UPDATE {table} SET speakers=? WHERE id=?", ('{"A":"Changed"}', identifier))
    elif damage == "payload":
        store.execute("UPDATE result_versions SET payload='{}' WHERE id=?", (head["id"],))
    else:
        store.execute(
            "UPDATE result_versions SET payload_sha256=? WHERE id=?", ("0" * 64, head["id"])
        )
    with pytest.raises(ValueError):
        save(scene, loaded)
    assert store.all("SELECT * FROM passage_reviews") == []


def test_nonidentical_but_equivalent_mirrors_are_fully_revalidated(scene, monkeypatch):
    store, rid, result_id = scene
    original = read(scene)
    segments = store.one("SELECT segments FROM recordings WHERE id=?", (rid,))["segments"]
    store.execute(
        "UPDATE recordings SET segments=? WHERE id=?",
        (json.dumps(json.loads(segments), ensure_ascii=True, indent=2), rid),
    )
    mirrors = counted(monkeypatch, subject, "serialized_payload_matches")
    encodes = counted(monkeypatch, history, "encode_payload")
    assert read(scene) == original
    # Both mirrors are checked; the differently formatted one has no proven binding and is
    # canonically re-encoded in full (ADR 0193 keeps ADR 0159's fallback).
    assert len(mirrors) == 2
    assert len(encodes) == 1
    assert store.all("SELECT * FROM passage_reviews WHERE result_id=?", (result_id,)) == []


def test_warm_context_does_not_cache_transcript_or_mark_revisions(scene):
    store, rid, result_id = scene
    first = read(scene)
    save(scene, first)
    with pytest.raises(subject.Conflict, match="another window"):
        save(scene, first)
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Edited after loading"}, {"text": "Second"}]),
        {"A": "Speaker"},
        first["revision"],
        result_id=result_id,
    )
    with pytest.raises(subject.Conflict, match="transcript changed"):
        save(scene, first)
    current = read(scene)
    assert current["passages"][0]["stale"]
    assert current["passages"][0]["status"] == "needs_review"


def test_warm_context_obeys_current_size_and_passage_limits(scene, monkeypatch):
    read(scene)
    monkeypatch.setattr(subject, "MAX_PASSAGES", 1)
    with pytest.raises(ValueError, match="too many passages"):
        read(scene)
    monkeypatch.setattr(subject, "MAX_PASSAGES", 10000)
    monkeypatch.setattr(subject, "MAX_TRANSCRIPT_BYTES", 1)
    with pytest.raises(ValueError, match="too large"):
        read(scene)


def test_response_mutation_cannot_change_cached_fingerprints(scene):
    loaded = read(scene)
    before = read(scene)
    loaded["passages"][0]["passage_sha256"] = "0" * 64
    loaded["passages"][0]["segment_id"] = "unrelated"
    assert read(scene) == before


def test_cache_retains_only_bounded_ids_and_digests_and_eviction_rechecks(scene, monkeypatch):
    store, rid, result_id = scene
    monkeypatch.setattr(subject, "_CONTEXT_LIMIT", 1)
    monkeypatch.setattr(subject, "_CONTEXT_PASSAGES", 2)
    loaded = read(scene)
    assert "Private synthetic" not in repr(subject._CONTEXTS)
    alternative = store.add_result(
        rid, normalize_segments([{"text": "Alternative"}]), {}, "Alternative", {}
    )
    store.activate_result(rid, alternative["id"], store.recording(rid)["revision"])
    subject.read(store, rid, alternative["id"])
    assert len(subject._CONTEXTS) == 1
    assert sum(map(len, subject._CONTEXTS.values())) <= 2
    store.activate_result(rid, result_id, store.recording(rid)["revision"])
    calls = counted(monkeypatch, subject, "verified_passage_texts")
    assert read(scene)["passages"] == loaded["passages"]
    assert calls == [True]


def test_new_valid_hash_does_not_hide_corrupt_projection_or_duplicate_ids(scene):
    store, rid, result_id = scene
    read(scene)
    row = store.recording(rid)
    segments = row["segments"]
    segments[1]["id"] = segments[0]["id"]
    payload, digest = encode_payload(segments, row["speakers"])
    head = store.result_versions(rid, result_id)[0]
    with store.connection() as db:
        db.execute("UPDATE recordings SET segments=? WHERE id=?", (json.dumps(segments), rid))
        db.execute(
            "UPDATE transcript_results SET segments=? WHERE id=?", (json.dumps(segments), result_id)
        )
        db.execute(
            "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
            (payload, digest, head["id"]),
        )
    with pytest.raises(ValueError, match="ambiguous"):
        read(scene)
