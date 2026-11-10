import json
import sqlite3

import pytest
from prior_schema import remove_later_additions, without_added_languages

from cbs_transcriber.store import SCHEMA_VERSION, Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def notes_items(client):
    store = client.app.state.store
    projects = [store.create_project(name) for name in ("Research A", "Research B")]
    recordings = [store.create_recording(p["id"], "interview.wav") for p in projects]
    for recording in recordings:
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
        store.save_transcript(
            recording["id"],
            normalize_segments(
                [
                    {
                        "text": "Hej København.",
                        "start": 0,
                        "end": 3,
                        "speaker": "A",
                        "words": [
                            {"word": "Hej", "start": 0, "end": 1},
                            {"word": "København.", "start": 1, "end": 3},
                        ],
                    }
                ]
            ),
            {"A": "Søren"},
            0,
        )
    return store, projects, recordings


def test_project_and_recording_notes_persist_independently_and_snapshot(client, notes_items):
    store, projects, recordings = notes_items
    note = '  Research context: æøå 👩‍💻\n<script>alert("literal")</script>  '
    for scope, resource in (("projects", projects[0]), ("recordings", recordings[0])):
        response = client.patch(
            f"/api/{scope}/{resource['id']}/notes", json={"text": note, "revision": 0}
        )
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["notes"] == note
        assert saved["notes_revision"] == 1
        assert saved["id"] == resource["id"]
        assert saved["name"] == resource["name"]
    listed_project = client.get("/api/projects").json()[0]
    listed_recording = client.get(f"/api/projects/{projects[0]['id']}/recordings").json()[0]
    assert listed_project["notes"] == listed_recording["notes"] == note
    assert listed_project["notes_revision"] == listed_recording["notes_revision"] == 1
    reopened = Store(store.root)
    assert reopened.one("SELECT * FROM projects WHERE id=?", (projects[0]["id"],))["notes"] == note
    assert reopened.recording(recordings[0]["id"])["notes"] == note
    snapshot = json.loads(
        (store.project_folder(projects[0]["id"]) / "project.json").read_text(encoding="utf-8")
    )
    assert snapshot["project"]["notes"] == snapshot["recordings"][0]["notes"] == note
    assert snapshot["project"]["notes_revision"] == 1
    assert snapshot["recordings"][0]["notes_revision"] == 1
    other_project = reopened.one("SELECT * FROM projects WHERE id=?", (projects[1]["id"],))
    other_recording = reopened.recording(recordings[1]["id"])
    assert other_project["notes"] == other_recording["notes"] == ""
    assert other_project["notes_revision"] == other_recording["notes_revision"] == 0


@pytest.mark.parametrize("scope,index", [("projects", 1), ("recordings", 2)])
def test_notes_reject_stale_revisions_and_missing_items(client, notes_items, scope, index):
    resource = notes_items[index][0]
    url = f"/api/{scope}/{resource['id']}/notes"
    assert client.patch(url, json={"text": "Saved first", "revision": 0}).status_code == 200
    stale = client.patch(url, json={"text": "Do not overwrite", "revision": 0})
    assert stale.status_code == 409
    assert "another window" in stale.json()["detail"]
    cleared = client.patch(url, json={"text": "", "revision": 1})
    assert cleared.status_code == 200
    assert cleared.json()["notes"] == ""
    assert cleared.json()["notes_revision"] == 2
    assert (
        client.patch(f"/api/{scope}/missing/notes", json={"text": "", "revision": 0}).status_code
        == 404
    )


def test_notes_leave_transcripts_originals_jobs_and_exports_unchanged(client, notes_items):
    store, projects, recordings = notes_items
    recording = store.recording(recordings[0]["id"])
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    original = directory / "original.wav"
    original.write_bytes(b"synthetic immutable original")
    result = store.result(recording["id"], recording["active_transcript_id"])
    job = store.add_job(recording["id"], "transcribe", {"settings": {"model": "whisper-1"}})
    note = "DO-NOT-INCLUDE-METADATA-IN-TRANSCRIPTS"
    for scope, resource in (("projects", projects[0]), ("recordings", recording)):
        response = client.patch(
            f"/api/{scope}/{resource['id']}/notes", json={"text": note, "revision": 0}
        )
        assert response.status_code == 200
    current = store.recording(recording["id"])
    for field in ("segments", "speakers", "revision", "active_transcript_id", "transcripts"):
        assert current[field] == recording[field]
    assert store.result(recording["id"], result["id"]) == result
    assert store.job(job["id"]) == job
    assert original.read_bytes() == b"synthetic immutable original"
    assert note not in json.dumps(
        store.freeze_result(recording["id"], result["id"], recording["revision"])
    )
    for format_name in ("nvivo-windows", "nvivo-mac", "text", "srt", "vtt", "csv", "json"):
        exported = client.get(
            f"/api/recordings/{recording['id']}/export", params={"format": format_name}
        )
        assert exported.status_code == 200, exported.text
        assert note not in exported.text
    # A transcript edit does not conflict with its independent metadata revision.
    store.save_transcript(recording["id"], recording["segments"], {}, recording["revision"])
    response = client.patch(
        f"/api/recordings/{recording['id']}/notes", json={"text": "New context", "revision": 1}
    )
    assert response.status_code == 200
    assert response.json()["revision"] == recording["revision"] + 1
    assert response.json()["notes_revision"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": None, "revision": 0},
        {"text": 123, "revision": 0},
        {"text": "x" * 20_001, "revision": 0},
        {"text": "", "revision": -1},
        {"text": "", "revision": 2**63},
        {"text": "", "revision": 0.5},
        {"text": "", "revision": "0"},
        {"text": "", "revision": False},
    ],
)
def test_notes_input_validation_is_curated_and_does_not_write(client, notes_items, body):
    _, projects, recordings = notes_items
    for scope, resource in (("projects", projects[0]), ("recordings", recordings[0])):
        response = client.patch(f"/api/{scope}/{resource['id']}/notes", json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "Invalid request fields. Check the supplied values."}
    assert client.get(f"/api/recordings/{recordings[0]['id']}").json()["notes_revision"] == 0


def test_notes_accept_twenty_thousand_unicode_characters(client, notes_items):
    for scope, resource in (("projects", notes_items[1][0]), ("recordings", notes_items[2][0])):
        note = "ø" * 20_000
        response = client.patch(
            f"/api/{scope}/{resource['id']}/notes", json={"text": note, "revision": 0}
        )
        assert response.status_code == 200
        assert response.json()["notes"] == note


def test_schema4_notes_migration_backs_up_and_preserves_all_existing_data(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Legacy project", "protected")
    recording = store.create_recording(project["id"], "legacy.wav")
    store.save_transcript(
        recording["id"], normalize_segments([{"text": "Original æøå", "start": 0, "end": 2}]), {}, 0
    )
    store.add_job(recording["id"], "transcribe", {"settings": {"model": "whisper-1"}})
    tables = ("projects", "recordings", "transcript_results", "jobs", "providers", "settings")
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
        # The first manual save is Working since ADR 0133; schema 4 has no role tables.
        remove_later_additions(db, 4)
        db.execute("DROP TABLE note_versions")
        db.execute("DROP TABLE result_versions")
        for table in ("projects", "recordings"):
            db.execute(f"ALTER TABLE {table} DROP COLUMN notes")
            db.execute(f"ALTER TABLE {table} DROP COLUMN notes_revision")
        db.execute("PRAGMA user_version=4")
        before = {
            table: [dict(row) for row in db.execute(f"SELECT * FROM {table}")] for table in tables
        }
    migrated = Store(tmp_path)
    with migrated.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for table in tables:
            rows = without_added_languages(
                table, [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
            )
            if table in {"projects", "recordings"}:
                assert all(row.pop("notes") == "" for row in rows)
                assert all(row.pop("notes_revision") == 0 for row in rows)
            assert rows == before[table]
    backups = list((tmp_path / "backups").glob("schema-4-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        db.row_factory = sqlite3.Row
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        for table in tables:
            assert [dict(row) for row in db.execute(f"SELECT * FROM {table}")] == before[table]
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-4-*.sqlite3"))) == 1
