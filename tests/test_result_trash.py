"""Recoverable result lifecycle; all libraries and processing receipts are synthetic."""

import io
import json
import sqlite3
from contextlib import contextmanager
from zipfile import ZipFile

import pytest
from prior_schema import remove_later_additions, without_added_languages

from cbs_transcriber.exports import project_archive
from cbs_transcriber.research_reports import project_report
from cbs_transcriber.store import SCHEMA_VERSION, Store
from cbs_transcriber.transcripts import normalize_segments


def example(store):
    project = store.create_project("Synthetic study")
    recording = store.create_recording(project["id"], "many.dots.æøå.wav")
    first = store.add_result(
        recording["id"],
        normalize_segments([{"text": "First reviewed passage.", "start": 0, "end": 2}]),
        {"A": "Anna"},
        "OpenAI whisper-1 (Word timing)",
        {"kind": "provider", "model": "whisper-1"},
    )
    second = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Second reviewed passage.", "start": 0, "end": 3}]),
        {"B": "Bo"},
        "Another model",
        {"kind": "import"},
    )
    return project, store.recording(recording["id"]), first, second


def change(store, recording_id, result_id, *, trashed=True):
    return store.set_result_trashed(
        recording_id, result_id, store.recording(recording_id)["revision"], trashed=trashed
    )


def rows(store):
    with store.connection() as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: [dict(row) for row in db.execute(f'SELECT * FROM "{table}"')] for table in tables
        }


def schema7(store):
    example(store)
    store.execute("DROP TABLE IF EXISTS result_hints")
    store.execute("DROP TABLE IF EXISTS native_batch_runs")
    store.execute("DROP TABLE result_trash")
    with store.connection() as db:
        remove_later_additions(db, 7)
    store.execute("PRAGMA user_version=7")


def test_schema7_additive_backup_and_reopen_preserve_every_row(tmp_path):
    store = Store(tmp_path)
    schema7(store)
    before = rows(store)
    migrated = Store(tmp_path)
    after = rows(migrated)
    assert after.pop("result_trash") == []
    assert after.pop("native_batch_runs") == []
    assert after.pop("result_hints") == []
    assert after.pop("passage_reviews") == []
    for table in ("working_job_authorities", "result_role_events", "recording_result_roles"):
        assert after.pop(table) == []
    assert {
        table: without_added_languages(table, value) for table, value in after.items()
    } == before
    backups = list((tmp_path / "backups").glob("schema-7-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("SELECT count(*) FROM transcript_results").fetchone()[0] == 2
    with migrated.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO result_trash VALUES (NULL,'synthetic')")
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-7-*.sqlite3"))) == 1


def test_schema7_backup_failure_leaves_library_unchanged(tmp_path):
    store = Store(tmp_path)
    schema7(store)
    before = rows(store)
    (tmp_path / "backups").write_text("Synthetic backup failure", encoding="utf-8")
    with pytest.raises(OSError):
        Store(tmp_path)
    assert rows(store) == before
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7


def test_active_last_and_restoration_preserve_exact_payload_history_notes_and_files(tmp_path):
    store = Store(tmp_path)
    project, recording, first, second = example(store)
    store.save_notes("recordings", recording["id"], "Research notes æøå", 0)
    original = store.directory(recording["id"], project["id"]) / "original.wav"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"synthetic immutable media")
    before = rows(store)
    updated = change(store, recording["id"], first["id"])
    assert updated["active_transcript_id"] == second["id"]
    assert updated["segments"] == second["segments"]
    assert updated["speakers"] == second["speakers"]
    assert updated["notes"] == "Research notes æøå"
    assert [r["id"] for r in updated["transcripts"]] == [second["id"]]
    trash = store.results(recording["id"], trashed=True)
    assert trash[0]["id"] == first["id"] and trash[0]["trashed_at"]
    version = store.result_versions(recording["id"], first["id"])[0]
    assert version["action"] == "result_trashed"
    assert version["actor_kind"] == "local_user"
    assert version["payload_sha256"] == before["result_versions"][0]["payload_sha256"]
    updated = change(store, recording["id"], second["id"])
    assert updated["active_transcript_id"] is None
    assert updated["segments"] == [] and updated["speakers"] == {}
    assert updated["transcripts"] == []
    store = Store(tmp_path)  # Membership and history survive a restart.
    restored = change(store, recording["id"], first["id"], trashed=False)
    assert restored["active_transcript_id"] == first["id"]
    assert restored["segments"] == first["segments"]
    restored = change(store, recording["id"], second["id"], trashed=False)
    assert restored["active_transcript_id"] == first["id"]
    assert store.result(recording["id"], second["id"])["segments"] == second["segments"]
    assert store.results(recording["id"], trashed=True) == []
    assert store.result_versions(recording["id"], first["id"])[0]["action"] == "result_restored"
    after = rows(store)
    assert after["transcript_results"] == before["transcript_results"]
    assert after["note_versions"] == before["note_versions"]
    assert all(row in after["result_versions"] for row in before["result_versions"])
    assert original.read_bytes() == b"synthetic immutable media"
    # Additional project copies are recovery artifacts, not a privacy-erasure promise.
    snapshot = json.loads((store.project_folder(project["id"]) / "project.json").read_text("utf-8"))
    assert len(snapshot["recordings"][0]["transcripts"]) == 2


def test_nonactive_noop_and_stale_mutations_are_atomic(tmp_path):
    store = Store(tmp_path)
    _, recording, first, second = example(store)
    updated = change(store, recording["id"], second["id"])
    assert updated["revision"] == recording["revision"] + 1
    for field in ("segments", "speakers", "active_transcript_id", "notes", "notes_revision"):
        assert updated[field] == recording[field]
    before = rows(store)
    assert change(store, recording["id"], second["id"]) == updated
    assert rows(store) == before
    with pytest.raises(ValueError, match="changed"):
        store.set_result_trashed(recording["id"], first["id"], recording["revision"], trashed=True)
    assert rows(store) == before
    store.execute("UPDATE recordings SET speakers='{}' WHERE id=?", (recording["id"],))
    damaged = rows(store)
    with pytest.raises(RuntimeError, match="disagree"):
        change(store, recording["id"], first["id"])
    assert rows(store) == damaged


def test_v81_recording_read_keeps_one_snapshot_during_concurrent_active_trash(
    tmp_path, monkeypatch
):
    reader = Store(tmp_path)
    _, recording, first, second = example(reader)
    writer = Store(tmp_path)
    original = reader._results
    interleaved = False

    def between_projection_and_active(db, identifier, **options):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            writer.set_result_trashed(identifier, first["id"], recording["revision"], trashed=True)
        return original(db, identifier, **options)

    monkeypatch.setattr(reader, "_results", between_projection_and_active)
    captured = reader.recording(recording["id"])
    assert interleaved
    assert captured["active_transcript_id"] == first["id"]
    assert captured["segments"] == first["segments"]
    assert len(captured["transcripts"]) == 2
    assert reader.recording(recording["id"])["active_transcript_id"] == second["id"]


def test_v81_project_copy_keeps_one_snapshot_during_concurrent_active_trash(tmp_path, monkeypatch):
    reader = Store(tmp_path)
    project, recording, first, second = example(reader)
    writer = Store(tmp_path)
    # Isolate coherent DB capture; competing filesystem writes have separate warnings.
    monkeypatch.setattr(writer, "snapshot_project", lambda identifier: None)
    original = reader.connection
    interleaved, errors = False, []

    def trace(sql):
        nonlocal interleaved
        if not interleaved and sql.startswith(
            (
                "SELECT * FROM transcript_results WHERE id=",
                "SELECT t.* FROM transcript_results t JOIN",
            )
        ):
            interleaved = True
            try:
                writer.set_result_trashed(
                    recording["id"], first["id"], recording["revision"], trashed=True
                )
            except Exception as exc:
                errors.append(exc)  # sqlite trace callbacks otherwise swallow exceptions.

    @contextmanager
    def traced_connection():
        with original() as db:
            db.set_trace_callback(trace)
            yield db

    monkeypatch.setattr(reader, "connection", traced_connection)
    reader.snapshot_project(project["id"])
    assert interleaved and not errors
    snapshot = json.loads(
        (reader.project_folder(project["id"]) / "project.json").read_text("utf-8")
    )
    captured = snapshot["recordings"][0]
    assert captured["active_transcript_id"] == first["id"]
    assert captured["segments"] == first["segments"]
    assert len(captured["transcripts"]) == 2
    assert reader.recording(recording["id"])["active_transcript_id"] == second["id"]


def test_api_scopes_visibility_comparison_exports_activity_and_revision_validation(client):
    store = client.app.state.store
    project, recording, first, second = example(store)
    other = store.create_recording(store.create_project("Isolated")["id"], "other.wav")
    url = f"/api/recordings/{recording['id']}/transcripts/{first['id']}"
    for revision in (-1, True, "1"):
        assert client.request("DELETE", url, json={"revision": revision}).status_code == 422
    assert (
        client.request(
            "DELETE",
            f"/api/recordings/{other['id']}/transcripts/{first['id']}",
            json={"revision": 0},
        ).status_code
        == 404
    )
    assert (
        client.request(
            "DELETE",
            url,
            json={"revision": recording["revision"]},
            headers={"Origin": "https://evil.invalid"},
        ).status_code
        == 403
    )
    reply = client.request("DELETE", url, json={"revision": recording["revision"]})
    assert reply.status_code == 200, reply.text
    assert reply.json()["active_transcript_id"] == second["id"]
    assert client.get(url).status_code == 404
    assert (
        client.get(f"/api/recordings/{recording['id']}/trashed-transcripts").json()[0]["id"]
        == first["id"]
    )
    assert (
        client.get(f"/api/projects/{project['id']}/recordings").json()[0]["transcript_count"] == 1
    )
    assert (
        client.get(f"/api/recordings/{recording['id']}/export?result_id={first['id']}").status_code
        == 404
    )
    assert (
        client.get(
            f"/api/recordings/{recording['id']}/alignment",
            params={"left_id": first["id"], "right_id": second["id"]},
        ).status_code
        == 404
    )
    assert (
        client.post(url + "/activate", json={"revision": reply.json()["revision"]}).status_code
        == 404
    )
    with pytest.raises(KeyError):
        store.freeze_result(recording["id"], first["id"], reply.json()["revision"])
    historical = store.result_versions(recording["id"], first["id"])[-1]
    assert client.get(url + f"/versions/{historical['id']}").status_code == 200
    assert (
        client.get(
            f"/api/recordings/{other['id']}/transcripts/{first['id']}/versions/{historical['id']}"
        ).status_code
        == 404
    )
    assert (
        client.post(url + "/restore", json={"revision": recording["revision"]}).status_code == 409
    )
    restored = client.post(url + "/restore", json={"revision": reply.json()["revision"]})
    assert restored.status_code == 200, restored.text
    assert restored.json()["active_transcript_id"] == second["id"]
    assert client.get(url).status_code == 200


def test_completed_producer_receipt_and_frozen_enhancement_survive_source_trash(client):
    store = client.app.state.store
    project, recording, first, _ = example(store)
    frozen = store.freeze_result(recording["id"], first["id"], recording["revision"])
    job = store.add_job(recording["id"], "transcribe", {"synthetic": True})
    produced = store.add_result(
        recording["id"], first["segments"], {}, "Produced", {}, job_id=job["id"]
    )
    store.update_job(job["id"], state="completed")
    change(store, recording["id"], produced["id"])
    before = rows(store)
    duplicate = store.add_result(
        recording["id"], first["segments"], {}, "Duplicate", {}, job_id=job["id"]
    )
    assert duplicate["id"] == produced["id"]
    assert rows(store) == before
    activity = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert activity["result_id"] is None
    change(store, recording["id"], first["id"])
    enhancement = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    result = store.add_result(
        recording["id"],
        first["segments"],
        first["speakers"],
        "Local enhancement",
        {"kind": "enhancement", "source_result_id": first["id"]},
        job_id=enhancement["id"],
    )
    head = store.result_versions(recording["id"], result["id"])[0]
    assert head["source_version_id"] == frozen["source_version_id"]
    selection = [
        {"recording_id": recording["id"], "result_id": result["id"], "version_id": head["id"]}
    ]
    with ZipFile(io.BytesIO(project_report(store, project["id"], selection))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert any(v["result_in_trash"] for v in manifest["versions"])
    assert manifest["manual_change_counts"] == {}
    selection[0].update(result_id=first["id"], version_id=frozen["source_version_id"])
    with pytest.raises(ValueError, match="Trash"):
        project_report(store, project["id"], selection)


def test_project_zip_excludes_trash_and_reports_recording_with_only_trash(tmp_path):
    store = Store(tmp_path)
    project, recording, first, second = example(store)
    other = store.create_recording(project["id"], "live.wav")
    live = store.add_result(other["id"], first["segments"], {}, "Live", {})
    change(store, recording["id"], first["id"])
    change(store, recording["id"], second["id"])
    for mode in ("active", "all"):
        with (
            project_archive(store, project["id"], "text", "speaker", mode) as data,
            ZipFile(data) as archive,
        ):
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["exported"] == 1 and manifest["skipped"] == 1
            assert {e["result_id"] for e in manifest["entries"]} == {None, live["id"]}
            assert (
                next(e for e in manifest["entries"] if e["recording_id"] == recording["id"])[
                    "status"
                ]
                == "skipped"
            )


def test_existing_topic_run_receipt_remains_readable_but_new_run_requires_restore(tmp_path):
    store = Store(tmp_path)
    project, recording, first, _ = example(store)
    frozen = store.freeze_result(recording["id"], first["id"], recording["revision"])
    book = store.save_codebook(
        project["id"],
        {"approved": True, "topics": [{"id": "a", "name": "A", "definition": "Synthetic topic"}]},
        0,
    )
    args = (recording["id"], first["id"], frozen["source_version_id"], book["id"])
    run = store.create_annotation_run(*args, request_id="before-trash")
    change(store, recording["id"], first["id"])
    assert store.create_annotation_run(*args, request_id="before-trash")["id"] == run["id"]
    with pytest.raises(ValueError, match="Trash"):
        store.create_annotation_run(*args, request_id="new-after-trash")
    assert (
        store.result_version(recording["id"], first["id"], frozen["source_version_id"])["payload"][
            "segments"
        ]
        == first["segments"]
    )
