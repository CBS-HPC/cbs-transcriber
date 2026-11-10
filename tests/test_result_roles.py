"""Working designations are explicit immutable versions; all libraries are synthetic."""

import json
import sqlite3
from zipfile import ZipFile

import pytest

from cbs_transcriber import result_roles
from cbs_transcriber.exports import project_archive
from cbs_transcriber.store import SCHEMA_VERSION, Store
from cbs_transcriber.transcripts import normalize_segments


def fixture(store):
    project = store.create_project("Synthetic Working study")
    recording = store.create_recording(project["id"], "source.wav")
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "Ældre exact."}]), {}, "A", {}
    )
    version = store.result_versions(recording["id"], result["id"])[0]
    return project, recording["id"], result, version


def mark(store, rid, version_id):
    current = store.recording(rid)
    revision, roles = current["revision"], current["result_roles"]["revision"]
    preview = result_roles.preview(store, rid, version_id, revision, roles)
    return result_roles.apply(store, rid, version_id, revision, roles, preview["fingerprint"])


def test_mark_pins_historical_version_without_changing_view_head_notes_or_ancestry(tmp_path):
    store = Store(tmp_path)
    _, rid, first, version = fixture(store)
    assert store.recording(rid)["result_roles"] == {"revision": 0, "working": None}
    current = store.recording(rid)
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Newer review."}]),
        {},
        current["revision"],
        result_id=first["id"],
    )
    second = store.add_result(rid, normalize_segments([{"text": "Alternative."}]), {}, "B", {})
    current = store.activate_result(rid, second["id"], store.recording(rid)["revision"])
    store.save_notes("recordings", rid, "Independent notes", current["notes_revision"])
    before = store.recording(rid)
    versions = store.all("SELECT * FROM result_versions ORDER BY id")
    saved = mark(store, rid, version["id"])
    assert saved["result_roles"]["working"]["version_id"] == version["id"]
    assert saved["active_transcript_id"] == second["id"]
    assert saved["segments"] == before["segments"] and saved["notes"] == "Independent notes"
    assert saved["revision"] == before["revision"]
    assert store.all("SELECT * FROM result_versions ORDER BY id") == versions
    reaffirmed = mark(store, rid, version["id"])
    events = store.all("SELECT * FROM result_role_events ORDER BY sequence")
    assert [event["action"] for event in events] == ["working_marked", "working_kept"]
    assert reaffirmed["result_roles"]["revision"] == saved["result_roles"]["revision"] + 1
    assert reaffirmed["result_roles"]["working"] == saved["result_roles"]["working"]
    kept = events[-1]
    assert kept["actor_kind"] == "local_user"
    assert kept["previous_version_id"] == kept["next_version_id"] == version["id"]
    assert (
        kept["previous_payload_sha256"] == kept["next_payload_sha256"] == version["payload_sha256"]
    )
    store.activate_result(rid, first["id"], saved["revision"])
    assert store.recording(rid)["result_roles"] == reaffirmed["result_roles"]


def test_trash_clears_older_working_version_atomically_restore_and_new_results_do_not_assign(
    tmp_path,
):
    store = Store(tmp_path)
    _, rid, first, version = fixture(store)
    mark(store, rid, version["id"])
    second = store.add_result(rid, normalize_segments([{"text": "B"}]), {}, "B", {})
    current = store.activate_result(rid, second["id"], store.recording(rid)["revision"])
    store.set_result_trashed(rid, first["id"], current["revision"], trashed=True)
    current = store.recording(rid)
    assert current["result_roles"] == {"revision": 2, "working": None}
    assert current["active_transcript_id"] == second["id"]
    events = store.all("SELECT * FROM result_role_events ORDER BY sequence")
    assert [event["action"] for event in events] == ["working_marked", "working_trashed"]
    assert events[-1]["previous_version_id"] == version["id"]
    store.set_result_trashed(rid, first["id"], current["revision"], trashed=False)
    store.add_result(rid, normalize_segments([{"text": "C"}]), {}, "C", {})
    assert store.recording(rid)["result_roles"]["working"] is None


def test_preview_cas_ownership_hash_and_writer_rollback(tmp_path):
    store = Store(tmp_path)
    _, rid, first, version = fixture(store)
    _, other, _, foreign = fixture(store)
    current = store.recording(rid)
    with pytest.raises(ValueError, match="belong"):
        result_roles.preview(store, rid, foreign["id"], current["revision"], 0)
    preview = result_roles.preview(store, rid, version["id"], current["revision"], 0)
    mark(store, rid, version["id"])
    with pytest.raises(ValueError, match="changed"):
        result_roles.apply(
            store, rid, version["id"], current["revision"], 0, preview["fingerprint"]
        )
    mark(store, rid, None)
    with store.connection() as db:
        db.execute(
            "CREATE TRIGGER refuse_role BEFORE INSERT ON result_role_events "
            "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        mark(store, rid, version["id"])
    assert store.recording(rid)["result_roles"] == {"revision": 2, "working": None}
    assert len(store.all("SELECT * FROM result_role_events")) == 2
    store.execute("UPDATE result_versions SET payload='{}' WHERE id=?", (version["id"],))
    with pytest.raises(ValueError):
        mark(store, rid, version["id"])
    assert store.recording(other)["result_roles"]["working"] is None


def test_project_working_export_exact_payload_and_manifest_only_unassigned(tmp_path):
    store = Store(tmp_path)
    project, rid, first, version = fixture(store)
    store.create_recording(project["id"], "unassigned.wav")
    with (
        project_archive(store, project["id"], "json", "speaker", "working") as archive,
        ZipFile(archive) as bundle,
    ):
        report = json.loads(bundle.read("manifest.json"))
        assert report["exported"] == 0 and report["skipped"] == 2
        assert all("Working" in entry["reason"] for entry in report["entries"])
    mark(store, rid, version["id"])
    edited = store.save_transcript(
        rid,
        normalize_segments([{"text": "Later wording."}]),
        {},
        store.recording(rid)["revision"],
        result_id=first["id"],
    )
    assert edited["result_roles"]["working"]["version_id"] == edited["active_version"]["id"]
    mark(store, rid, version["id"])
    with (
        project_archive(store, project["id"], "json", "speaker", "working") as archive,
        ZipFile(archive) as bundle,
    ):
        report = json.loads(bundle.read("manifest.json"))
        entry = next(item for item in report["entries"] if item["status"] == "exported")
        assert (
            entry["version_id"] == version["id"]
            and entry["payload_sha256"] == version["payload_sha256"]
        )
        assert json.loads(bundle.read(entry["path"]))["segments"][0]["text"] == "Ældre exact."
        assert report["skipped"] == 1


def test_schema13_migration_backups_and_preserves_every_old_row(tmp_path, monkeypatch):
    from cbs_transcriber import store as store_module

    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 13)
    store = Store(tmp_path)
    fixture(store)
    with store.connection() as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {
            name: (
                [column[1] for column in db.execute(f'PRAGMA table_info("{name}")')],
                [tuple(row) for row in db.execute(f'SELECT * FROM "{name}"')],
            )
            for name in tables
        }
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", SCHEMA_VERSION)
    migrated = Store(tmp_path)
    with migrated.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for name, (columns, rows) in before.items():
            projection = ",".join(f'"{column}"' for column in columns)
            assert [tuple(row) for row in db.execute(f'SELECT {projection} FROM "{name}"')] == rows
        assert db.execute("SELECT count(*) FROM recording_result_roles").fetchone()[0] == 0
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((tmp_path / "backups").glob("schema-13-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 13


def test_working_export_refuses_pointer_drift_even_to_valid_owned_version(tmp_path):
    store = Store(tmp_path)
    project, rid, result, first = fixture(store)
    mark(store, rid, first["id"])
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Newer wording."}]),
        {},
        store.recording(rid)["revision"],
        result_id=result["id"],
    )
    newer = store.result_versions(rid, result["id"])[0]
    assert newer["id"] != first["id"]
    store.execute(
        "UPDATE recording_result_roles SET working_version_id=? WHERE recording_id=?",
        (first["id"], rid),
    )
    with project_archive(store, project["id"], "json", "speaker", "working") as archive:
        with ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["exported"] == 0
            assert "disagree" in manifest["entries"][0]["reason"]
            assert not any(name.startswith("transcripts/") for name in bundle.namelist())


def test_v149_working_export_uses_only_its_immutable_payload_not_the_current_head(tmp_path):
    store = Store(tmp_path)
    project, rid, result, first = fixture(store)
    mark(store, rid, first["id"])
    # A broken later projection must not change the independently verified saved version.
    store.execute(
        "UPDATE transcript_results SET segments='broken head' WHERE id=?", (result["id"],)
    )
    with project_archive(store, project["id"], "json", "speaker", "working") as archive:
        with ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["exported"] == 1
            entry = manifest["entries"][0]
            assert entry["version_id"] == first["id"]
            assert json.loads(bundle.read(entry["path"]))["segments"][0]["text"] == "Ældre exact."


def test_working_api_defaults_to_pinned_export_and_rejects_unknown_fields(client):
    project, rid, _, version = fixture(client.app.state.store)
    current = client.app.state.store.recording(rid)
    body = {"version_id": version["id"], "revision": current["revision"], "role_revision": 0}
    preview = client.post(f"/api/recordings/{rid}/working-preview", json=body)
    assert preview.status_code == 200
    saved = client.post(
        f"/api/recordings/{rid}/working",
        json={**body, "fingerprint": preview.json()["fingerprint"]},
    )
    assert (
        saved.status_code == 200
        and saved.json()["result_roles"]["working"]["version_id"] == version["id"]
    )
    response = client.get(f"/api/projects/{project['id']}/export?format=json")
    assert response.status_code == 200
    assert (
        client.post(
            f"/api/recordings/{rid}/working-preview", json={**body, "guessed": True}
        ).status_code
        == 422
    )


def test_role_history_verifies_owned_hash_chain_and_projects_only_scoped_metadata(tmp_path):
    store = Store(tmp_path)
    _, rid, result, version = fixture(store)
    mark(store, rid, version["id"])
    mark(store, rid, None)
    with store.connection() as db:
        evidence = result_roles.public_history(db, store, rid, "recording1")
    encoded = json.dumps(evidence)
    assert rid not in encoded and result["id"] not in encoded and version["id"] not in encoded
    assert "Ældre" not in encoded
    assert evidence["events"][1]["previous"]["payload_sha256"] == version["payload_sha256"]
    assert evidence["working"] is None
    store.execute(
        "UPDATE result_role_events SET previous_payload_sha256=? WHERE sequence=2", ("0" * 64,)
    )
    with store.connection() as db, pytest.raises(ValueError, match="hash"):
        result_roles.public_history(db, store, rid, "recording1")
