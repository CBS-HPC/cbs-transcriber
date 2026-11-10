import json
import sqlite3
from pathlib import Path

import pytest
from admission_support import inspect_and_materialize
from conftest import finish_media
from prior_schema import remove_later_additions

from cbs_transcriber.providers import check_models
from cbs_transcriber.store import SCHEMA_VERSION, Store
from cbs_transcriber.transcripts import normalize_segments


def prepared(client, wav_bytes, classification="protected", **extra):
    project = client.post(
        "/api/projects",
        json={
            "name": "Research",
            "classification": classification,
            **extra,
        },
    ).json()
    uploaded = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("interview.v2.wav", wav_bytes)},
    ).json()
    return project, finish_media(client, uploaded)


@pytest.mark.parametrize("classification", ["protected"])
def test_v18_protected_projects_enforce_provider_approval(
    client, wav_bytes, credentials, classification
):
    project, recording = prepared(client, wav_bytes, classification)
    provider = client.post(
        "/api/providers",
        json={
            "name": "Local",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "local",
        },
    ).json()
    request = {"recording_ids": [recording["id"]], "provider_ids": [provider["id"]]}
    store = client.app.state.store
    with store.library_lock():
        assert client.post("/api/transcriptions", json=request).status_code == 403
        assert (
            client.patch(
                f"/api/recordings/{recording['id']}/provider", json={"provider_id": provider["id"]}
            ).status_code
            == 400
        )
        assert not store.all("SELECT id FROM jobs WHERE kind='transcribe'")
        provider["approved_for"] = [classification]
        assert client.put(f"/api/providers/{provider['id']}", json=provider).status_code == 200
        accepted = client.post("/api/transcriptions", json=request)
        assert accepted.status_code == 202, accepted.text
        job = inspect_and_materialize(store, credentials, [accepted.json()[0]["id"]])[0]
        # Revocation after executable admission must block the worker and explicit retries.
        provider["approved_for"] = []
        client.put(f"/api/providers/{provider['id']}", json=provider)
        with pytest.raises(ValueError, match="explicitly approved"):
            client.app.state.queue.process(job["id"])
        store.update_job(job["id"], state="failed")
        assert client.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 400
    assert (
        Store(client.app.state.store.root).one(
            "SELECT classification FROM projects WHERE id=?", (project["id"],)
        )["classification"]
        == classification
    )


def test_v18_worker_rechecks_between_chunks_and_changed_endpoint(
    client, wav_bytes, credentials, monkeypatch
):
    project, recording = prepared(client, wav_bytes)
    provider = client.post(
        "/api/providers",
        json={
            "name": "Approved",
            "base_url": "https://one.test/v1",
            "model": "local",
            "approved_for": ["protected"],
            "concurrent_chunks": 1,
        },
    ).json()
    store = client.app.state.store
    with store.library_lock():
        accepted = client.post(
            "/api/transcriptions",
            json={
                "recording_ids": [recording["id"]],
                "provider_ids": [provider["id"]],
            },
        )
        assert accepted.status_code == 202, accepted.text
        job = inspect_and_materialize(store, credentials, [accepted.json()[0]["id"]])[0]
        # Pick two real intervals; FFmpeg and the original-v1 receipts remain real.
        monkeypatch.setattr(
            "cbs_transcriber.media.chunk_boundaries",
            lambda duration, *_: [(0, duration / 2), (duration / 2, duration)],
        )
        calls = []

        def response(*args):
            calls.append(True)
            provider["base_url"] = "https://changed.test/v1"
            client.put(f"/api/providers/{provider['id']}", json=provider)
            return {"text": "Hej."}

        monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", response)
        with pytest.raises(ValueError, match="explicitly approved"):
            client.app.state.queue.process(job["id"])
        assert calls == [True]
        assert len(store.job(job["id"])["chunks"]) == 1


def test_v18_classification_change_blocks_pending_jobs(client, wav_bytes, credentials):
    project, recording = prepared(client, wav_bytes, "standard")
    provider = client.post(
        "/api/providers",
        json={
            "name": "Local",
            "base_url": "http://localhost:9000/v1",
            "model": "local",
        },
    ).json()
    store = client.app.state.store
    with store.library_lock():
        accepted = client.post(
            "/api/transcriptions",
            json={
                "recording_ids": [recording["id"]],
                "provider_ids": [provider["id"]],
            },
        )
        assert accepted.status_code == 202, accepted.text
        job = inspect_and_materialize(store, credentials, [accepted.json()[0]["id"]])[0]
        assert (
            client.patch(
                f"/api/projects/{project['id']}/classification",
                json={"classification": "protected"},
            ).status_code
            == 200
        )
        with pytest.raises(ValueError, match="explicitly approved"):
            client.app.state.queue.process(job["id"])
        store.update_job(job["id"], state="running")
        assert (
            client.patch(
                f"/api/projects/{project['id']}/classification", json={"classification": "standard"}
            ).status_code
            == 409
        )


def test_v19_project_folder_has_inputs_originals_edits_and_nvivo(client, wav_bytes, tmp_path):
    parent = tmp_path / "chosen"
    parent.mkdir()
    unrelated = parent / "keep.txt"
    unrelated.write_text("Unrelated")
    project, recording = prepared(client, wav_bytes, "standard", storage_parent=str(parent))
    folder = Path(project["storage_dir"])
    store = client.app.state.store
    assert folder.parent == parent
    assert store.directory(recording["id"]).is_relative_to(folder)
    assert (store.directory(recording["id"]) / "original.wav").read_bytes() == wav_bytes
    segment = normalize_segments([{"text": "Første æøå.", "start": 0, "end": 2}])
    result = store.add_result(recording["id"], segment, {}, "Imported", {"kind": "import"})
    response = client.patch(
        f"/api/recordings/{recording['id']}/segments/0",
        json={
            "revision": 1,
            "mode": "free",
            "text": "Rettet København.",
        },
    )
    assert response.status_code == 200
    snapshot = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    saved = snapshot["recordings"][0]["transcripts"][0]["segments"][0]
    assert saved["text"] == "Rettet København."
    assert saved["original_text"] == "Første æøå."
    exported = folder / "exports" / recording["id"] / f"{result['id']}.txt"
    assert "INTERVIEW" in exported.read_text(encoding="utf-8")
    assert "Rettet København." in exported.read_text(encoding="utf-8")
    assert Store(store.root).directory(recording["id"]) == store.directory(recording["id"])
    assert client.delete(f"/api/projects/{project['id']}").status_code == 200
    assert unrelated.read_text() == "Unrelated"
    assert not exported.exists() and not (folder / "project.json").exists()


def test_v19_rejects_relative_storage_and_reports_snapshot_failure(client, tmp_path, monkeypatch):
    assert (
        client.post(
            "/api/projects", json={"name": "Wrong", "storage_parent": "relative"}
        ).status_code
        == 400
    )
    project = client.post("/api/projects", json={"name": "Research"}).json()
    write_text = Path.write_text

    def fail_snapshot(path, *args, **kwargs):
        if path.name == "project.pending":
            raise OSError("synthetic disk full")
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_snapshot)
    assert client.patch(f"/api/projects/{project['id']}", json={"name": "Saved"}).status_code == 200
    returned = client.get("/api/projects").json()[0]
    assert returned["name"] == "Saved" and "database" in returned["snapshot_error"]


def test_v17_check_api_uses_draft_key_without_saving_or_queueing(client, monkeypatch):
    import httpx

    def checked(settings, key):
        assert key == "draft-synthetic-key"
        return check_models(
            settings,
            key,
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"data": [], "object": "list"})
                )
            ),
        )

    monkeypatch.setattr("cbs_transcriber.app.check_models", checked)
    response = client.post("/api/providers/check-models", json={"api_key": "draft-synthetic-key"})
    assert response.status_code == 200 and len(response.json()["models"]) == 5
    assert "draft-synthetic-key" not in response.text
    assert not client.app.state.credentials.keys
    assert client.get("/api/jobs").json() == []
    assert "draft-synthetic-key" not in client.app.state.store.db_path.read_bytes().decode("latin1")


def test_v19_schema2_upgrade_keeps_legacy_files_results_and_revisions(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Existing")
    recording = store.create_recording(project["id"], "legacy.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Bevar æøå."}]),
        {},
        "Reviewed",
        {"kind": "import"},
    )
    original = tmp_path / "recordings" / recording["id"] / "original.wav"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"synthetic-original")
    before = store.recording(recording["id"])
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
        remove_later_additions(db, 2)
        db.execute("DROP TABLE note_versions")
        db.execute("DROP TABLE result_versions")
        db.execute("ALTER TABLE projects DROP COLUMN storage_dir")
        db.execute("ALTER TABLE projects DROP COLUMN classification")
        db.execute("PRAGMA user_version=2")
    migrated = Store(tmp_path)
    assert migrated.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert migrated.directory(recording["id"]) == original.parent
    assert original.read_bytes() == b"synthetic-original"
    after = migrated.recording(recording["id"])
    for field in ("segments", "speakers", "revision", "active_transcript_id", "transcripts"):
        assert after[field] == before[field]
    assert migrated.one("SELECT classification FROM projects")["classification"] == "standard"
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
