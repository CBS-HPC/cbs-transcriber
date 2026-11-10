"""Collections inside projects (schema 11, ADR 0060): metadata only, recordings never lost."""

import io
import json
import sqlite3
import zipfile

import pytest
from prior_schema import without_added_languages
from test_working_reports import mark_active_latest

from cbs_transcriber import store as store_module
from cbs_transcriber.store import SCHEMA_VERSION, Store
from cbs_transcriber.transcripts import normalize_segments


def project_with_recordings(store, count=3, name="Study"):
    project = store.create_project(name)
    recordings = [store.create_recording(project["id"], f"r{i}.wav") for i in range(count)]
    return project, recordings


def test_collections_are_named_ordered_unique_and_validated(client):
    store = client.app.state.store
    project, _ = project_with_recordings(store, 0)
    url = f"/api/projects/{project['id']}/collections"
    first = client.post(url, json={"name": " Wave 1 "})
    assert first.status_code == 201 and first.json()["name"] == "Wave 1"
    second = client.post(url, json={"name": "Wave 2"}).json()
    assert client.post(url, json={"name": "wave 1"}).status_code == 400
    assert client.post(url, json={"name": "a\nb"}).status_code == 400
    assert [c["name"] for c in client.get(url).json()] == ["Wave 1", "Wave 2"]
    renamed = client.patch(
        f"/api/collections/{second['id']}", json={"name": "Pilot", "notes": "Two sites."}
    )
    assert renamed.json()["name"] == "Pilot" and renamed.json()["notes"] == "Two sites."
    assert (
        client.patch(f"/api/collections/{second['id']}", json={"name": "Wave 1"}).status_code == 400
    )
    assert (
        client.post(f"/api/projects/{'0' * 32}/collections", json={"name": "X"}).status_code == 404
    )


def test_moving_and_deleting_never_removes_recordings(client):
    store = client.app.state.store
    project, recordings = project_with_recordings(store)
    other, foreign = project_with_recordings(store, 1, "Other")
    wave = store.create_collection(project["id"], "Wave 1")
    move = f"/api/projects/{project['id']}/collections/move"
    ids = [r["id"] for r in recordings[:2]]
    assert client.post(move, json={"recording_ids": ids, "collection_id": wave["id"]}).json() == {
        "moved": 2
    }
    listed = client.get(f"/api/projects/{project['id']}/recordings").json()
    assert sorted(r["id"] for r in listed if r["collection_id"] == wave["id"]) == sorted(ids)
    assert store.collection(wave["id"])["recordings"] == 2
    # Another project's recordings or collections are refused, and nothing moves.
    assert (
        client.post(
            move, json={"recording_ids": [foreign[0]["id"]], "collection_id": wave["id"]}
        ).status_code
        == 400
    )
    foreign_wave = store.create_collection(other["id"], "Elsewhere")
    assert (
        client.post(
            move, json={"recording_ids": ids, "collection_id": foreign_wave["id"]}
        ).status_code
        == 400
    )
    # Back to Unsorted, then deleting the collection keeps every recording.
    client.post(move, json={"recording_ids": [ids[0]], "collection_id": None})
    assert store.recording(ids[0])["collection_id"] is None
    assert client.delete(f"/api/collections/{wave['id']}").json() == {"ok": True}
    assert store.recording(ids[1])["collection_id"] is None
    assert len(store.all("SELECT id FROM recordings WHERE project_id=?", (project["id"],))) == 3
    assert client.get(f"/api/projects/{project['id']}/collections").json() == []


def test_uploads_can_go_straight_into_a_collection(client, wav_bytes):
    store = client.app.state.store
    project, _ = project_with_recordings(store, 0)
    wave = store.create_collection(project["id"], "Wave 1")
    response = client.post(
        "/api/recordings",
        data={"project_id": project["id"], "collection_id": wave["id"]},
        files={"file": ("a.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["recording"]["collection_id"] == wave["id"]
    other, _ = project_with_recordings(store, 0, "Other")
    elsewhere = store.create_collection(other["id"], "Elsewhere")
    refused = client.post(
        "/api/recordings",
        data={"project_id": project["id"], "collection_id": elsewhere["id"]},
        files={"file": ("b.wav", wav_bytes, "audio/wav")},
    )
    assert refused.status_code == 404
    assert len(store.all("SELECT id FROM recordings WHERE project_id=?", (project["id"],))) == 1


def test_project_zip_has_a_folder_per_collection_and_can_be_limited(client):
    store = client.app.state.store
    project, recordings = project_with_recordings(store)
    for recording in recordings:
        store.add_result(
            recording["id"],
            normalize_segments([{"text": "Hej.", "start": 0, "end": 1}]),
            {},
            "Model",
            {},
        )
    wave = store.create_collection(project["id"], "Wave/1: sites")
    store.move_recordings(project["id"], [recordings[0]["id"]], wave["id"])

    def names(**params):
        # Folder layout is independent of Working designation (default export, ADR 0093).
        params = {"results": "active", **params}
        response = client.get(f"/api/projects/{project['id']}/export", params=params)
        assert response.status_code == 200, response.text
        bundle = zipfile.ZipFile(io.BytesIO(response.content))
        manifest = json.loads(bundle.read("manifest.json"))
        return [n for n in bundle.namelist() if n.startswith("transcripts/")], manifest

    everything, manifest = names()
    assert len(everything) == 3
    assert sum(n.startswith("transcripts/01 Wave_1_ sites/") for n in everything) == 1
    assert sum(n.startswith("transcripts/Unsorted/") for n in everything) == 2
    assert {e["collection"] for e in manifest["entries"]} == {"Wave/1: sites", None}
    one, manifest = names(collection=wave["id"])
    assert len(one) == 1 and manifest["collection"] == wave["id"]
    unsorted, _ = names(collection="unsorted")
    assert len(unsorted) == 2
    # The research report's default selection follows the visible collection too.
    mark_active_latest(store, project["id"])
    report_url = f"/api/projects/{project['id']}/research-report"
    for scope, selected in ((None, 3), (wave["id"], 1), ("unsorted", 2)):
        report = client.post(report_url, json={"collection": scope})
        assert report.status_code == 200, report.text
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(report.content)).read("manifest.json"))
        assert manifest["selection"]["selected_recordings"] == selected
    assert client.post(report_url, json={"collection": "0" * 32}).status_code == 404
    assert (
        client.get(
            f"/api/projects/{project['id']}/export", params={"collection": "0" * 32}
        ).status_code
        == 404
    )


def test_schema10_upgrade_backs_up_and_keeps_every_row(tmp_path, monkeypatch):
    # Build a genuine schema-10 library: the collections migration does not run.
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 10)
    monkeypatch.setattr(Store, "_migrate_collections", lambda self: None)
    monkeypatch.setattr(Store, "snapshot_project", lambda self, identifier: None)
    old = Store(tmp_path)
    project, recordings = project_with_recordings(old)
    tables = [
        r["name"]
        for r in old.all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    before = {t: old.all(f'SELECT * FROM "{t}" ORDER BY rowid') for t in tables}
    assert "collections" not in tables
    monkeypatch.undo()
    upgraded = Store(tmp_path)
    with upgraded.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    after = {t: upgraded.all(f'SELECT * FROM "{t}" ORDER BY rowid') for t in tables}
    # Earlier rows remain exact; recordings gain only an empty collection and no source observation.
    for row in after["recordings"]:
        assert row.pop("collection_id") is None
        assert row.pop("original_input") is None
    for table in ("projects", "recordings"):
        after[table] = without_added_languages(table, after[table])
    assert after == before
    backups = list((tmp_path / "backups").glob("schema-10-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-10-*.sqlite3"))) == 1
    wave = upgraded.create_collection(project["id"], "Wave 1")
    upgraded.move_recordings(project["id"], [recordings[0]["id"]], wave["id"])
    with pytest.raises(ValueError):
        upgraded.create_collection(project["id"], "WAVE 1")
