"""Manual passage review metadata survives isolated portable capture and restore."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest
from test_portable_restore import destination
from test_portable_semantics import RESULT, VERSION, build, inspect

from cbs_transcriber import passage_review, portable_capture, portable_restore
from cbs_transcriber import portable_database_worker as database_worker
from cbs_transcriber import portable_semantics_worker as semantics_worker
from cbs_transcriber.history import encode_payload
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

STAMP = "2026-11-09T12:00:00+00:00"


def add_mark(path, *, segment="p", status="checked"):
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO passage_reviews VALUES (?,?,?,?,?,?)",
            (RESULT, segment, "f" * 64, status, 3, STAMP),
        )


@pytest.mark.parametrize("status", ["checked", "needs_review", "not_checked"])
def test_contained_observation_retains_stale_and_removed_passage_marks(tmp_path, status):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 19)
    add_mark(path, status=status)
    add_mark(path, segment="removed-passage", status=status)
    before = path.read_bytes()
    result = inspect(path)
    assert result["schema_version"] == 19
    assert result["semantic_counts"]["passage_reviews"] == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "column,value",
    [
        ("passage_sha256", "F" * 64),
        ("segment_id", ""),
        ("segment_id", "x" * 201),
        ("status", "unknown"),
        ("revision", 1.5),
        ("updated", "2026-11-09T12:00:00"),
        ("result_id", "9" * 32),
    ],
)
def test_invalid_review_rows_are_refused_before_restore(tmp_path, column, value):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 19)
    add_mark(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(f"UPDATE passage_reviews SET {column}=?", (value,))
    with pytest.raises(ValueError):
        inspect(path)


def test_split_undo_action_is_retained_by_contained_history_observation(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 19)
    payload, digest = encode_payload([{"id": "p", "text": "æøå", "start": -0.0}], {})
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
                STAMP,
                "passage_split_undo",
                "{}",
                "local_user",
                None,
            ),
        )
    assert inspect(path)["semantic_counts"]["result_versions"] == 2


def test_review_reads_share_the_existing_semantic_byte_budget(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 19)
    add_mark(path)
    # Exercise the new row validator with a real synthetic query and an exhausted budget;
    # containment and immutable source opening are covered by inspect() above.
    with sqlite3.connect(path) as db:
        observer = object.__new__(semantics_worker.Semantics)
        observer.db, observer.base = db, database_worker
        observer.request = {"deadline_ns": time.monotonic_ns() + 60 * 10**9}
        observer.bytes, observer.counts = semantics_worker.SEMANTIC_BYTES, {}
        with pytest.raises(semantics_worker.Refused) as caught:
            observer.passage_reviews()
        assert caught.value.code == "resource"


def test_complete_capture_restore_carries_review_rows_without_granting_approval(
    client, tmp_path, wav_bytes
):
    source = client.app.state.store
    project = source.create_project("Synthetic review round trip")
    recording = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("tiny.wav", wav_bytes, "audio/wav")},
    ).json()["recording"]
    source.execute("UPDATE jobs SET state='done'")
    source.execute("UPDATE recordings SET status='ready'")
    result = source.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic exact wording", "start": 0, "end": 1}]),
        {},
        "Synthetic",
        {},
    )
    mark = (result["id"], "0", "f" * 64, "checked", 3, STAMP)
    source.execute("INSERT INTO passage_reviews VALUES (?,?,?,?,?,?)", mark)
    with source.library_lock():
        saved = portable_capture.capture_library(
            source,
            client.app.state.queue,
            tmp_path / "packages",
            gate=portable_capture.CaptureGate(),
        )
    manifest = json.loads((Path(saved["folder"]) / "manifest.json").read_bytes())
    object_path = Path(saved["folder"]) / "objects" / manifest["database"]["object"]
    # The preserved source is independently bound again by the restore receipt below.
    before = object_path.read_bytes()
    target = destination(tmp_path, "review-restored")
    receipt = portable_restore.restore_library(
        saved["folder"],
        expected_commit_sha256=saved["commit_sha256"],
        target=target,
        live_root=source.root,
    )
    restored = Store(target)
    with restored.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert tuple(db.execute("SELECT * FROM passage_reviews").fetchone()) == mark
        assert db.execute("SELECT count(*) FROM restored_owners WHERE kind='job'").fetchone()[0]
    view = passage_review.read(restored, recording["id"], result["id"])
    assert view["passages"][0]["status"] == "needs_review"
    assert view["passages"][0]["stale"] is True
    assert (restored.directory(recording["id"]) / "original.wav").read_bytes() == wav_bytes
    preserved = target / "preservation" / receipt["operation_id"] / "source.sqlite3"
    assert hashlib.sha256(preserved.read_bytes()).hexdigest() == receipt["source_database_sha256"]
    assert object_path.read_bytes() == before
