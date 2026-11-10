import json
import sqlite3

import pytest
from prior_schema import remove_later_additions

from cbs_transcriber.providers import Settings, protected_approvals
from cbs_transcriber.store import SCHEMA_VERSION, Store, new_id, now
from cbs_transcriber.transcripts import normalize_segments


def test_v25_schema3_migration_preserves_transcripts_queue_and_narrow_approvals(tmp_path):
    store = Store(tmp_path)
    projects = [store.create_project(name) for name in ("sensitive", "restricted", "confidential")]
    recording = store.create_recording(projects[0]["id"], "interview.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Bevar æøå.", "start": 0.2, "end": 2.3}]),
        {},
        "Original",
        {},
    )
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"settings": {"approved_for": ["confidential"], "model": "whisper-1"}},
    )
    partial, complete = new_id(), new_id()
    before_recording, before_job = store.recording(recording["id"]), store.job(job["id"])
    with store.connection() as db:
        db.execute("DROP TABLE IF EXISTS result_hints")
        db.execute("DROP TABLE IF EXISTS native_batch_runs")
        db.execute("DROP TABLE IF EXISTS result_trash")
        for table in (
            "annotation_review_events",
            "annotation_items",
            "annotation_runs",
            "codebook_versions",
        ):
            db.execute(f"DROP TABLE {table}")
        remove_later_additions(db, 3)
        db.execute("DROP TABLE note_versions")
        db.execute("DROP TABLE result_versions")
        for project in projects:
            db.execute(
                "UPDATE projects SET classification=? WHERE id=?", (project["name"], project["id"])
            )
        for identifier, approvals in (
            (partial, ["sensitive"]),
            (complete, ["sensitive", "restricted", "confidential"]),
        ):
            db.execute(
                "INSERT INTO providers VALUES (?,?,?,?)",
                (identifier, "Legacy", json.dumps({"approved_for": approvals}), now()),
            )
        db.execute("PRAGMA user_version=3")
    migrated = Store(tmp_path)
    assert migrated.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert {p["classification"] for p in migrated.all("SELECT classification FROM projects")} == {
        "protected"
    }
    for identifier, expected in ((partial, []), (complete, ["protected"])):
        value = json.loads(
            migrated.one("SELECT value FROM providers WHERE id=?", (identifier,))["value"]
        )
        assert value["approved_for"] == expected
    # Schema 3 had no version table: migration records a new legacy baseline of the same payload.
    after_recording = migrated.recording(recording["id"])
    observed, earlier = (
        after_recording.pop("active_version"),
        before_recording.pop("active_version"),
    )
    assert observed["payload_sha256"] == earlier["payload_sha256"]
    [baseline] = migrated.result_versions(recording["id"], after_recording["active_transcript_id"])
    assert (baseline["id"], baseline["action"]) == (observed["id"], "legacy_observed")
    assert after_recording == before_recording
    assert migrated.job(job["id"]) == before_job
    assert Settings.model_validate(before_job["params"]["settings"]).approved_for == []
    backups = list((tmp_path / "backups").glob("schema-3-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert (
            db.execute(
                "SELECT classification FROM projects WHERE id=?", (projects[0]["id"],)
            ).fetchone()[0]
            == "sensitive"
        )
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-3-*.sqlite3"))) == 1


@pytest.mark.parametrize(
    "value", [["protected"], [], ["sensitive"], ["restricted", "confidential"]]
)
def test_v25_only_explicit_protected_or_all_legacy_categories_remain_approved(value):
    expected = ["protected"] if value == ["protected"] else []
    assert protected_approvals(value) == expected


def test_v25_invalid_approvals_are_rejected_and_old_category_inputs_are_removed(client):
    assert (
        client.post("/api/providers", json={"name": "Bad", "approved_for": [{}]}).status_code == 422
    )
    assert (
        client.post(
            "/api/projects", json={"name": "Bad", "classification": "sensitive"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/projects", json={"name": "Good", "classification": "protected"}
        ).status_code
        == 201
    )
