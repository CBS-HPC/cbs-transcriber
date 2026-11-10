import json

from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def test_v19_existing_project_changes_folder_without_losing_media_or_edits(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Move project")
    recording = store.create_recording(project["id"], "interview.v2.wav")
    original_dir = store.directory(recording["id"])
    original_dir.mkdir(parents=True)
    (original_dir / "original.wav").write_bytes(b"immutable synthetic original")
    (original_dir / "raw.json").write_text('{"text":"original"}', encoding="utf-8")
    result = store.add_result(
        recording["id"], normalize_segments([{"text": "Hej København."}]), {}, "Reviewed", {}
    )
    before = store.recording(recording["id"])
    parent = tmp_path / "New location"
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(parent)}
    )
    assert response.status_code == 200, response.text
    new_dir = store.directory(recording["id"])
    assert new_dir != original_dir and new_dir.is_relative_to(parent)
    assert (new_dir / "original.wav").read_bytes() == b"immutable synthetic original"
    assert (new_dir / "raw.json").read_text(encoding="utf-8") == '{"text":"original"}'
    after = store.recording(recording["id"])
    for key in ("segments", "speakers", "revision", "active_transcript_id", "transcripts"):
        assert after[key] == before[key]
    assert not original_dir.exists()
    assert Store(store.root).directory(recording["id"]) == new_dir
    snapshot = json.loads((store.project_folder(project["id"]) / "project.json").read_text("utf-8"))
    assert snapshot["recordings"][0]["transcripts"][0]["id"] == result["id"]


def test_v19_storage_change_blocks_queued_work_and_rolls_back_failed_copy(
    client, tmp_path, monkeypatch
):
    store = client.app.state.store
    project = store.create_project("Busy project")
    recording = store.create_recording(project["id"], "source.wav")
    source = store.directory(recording["id"])
    source.mkdir(parents=True)
    (source / "original.wav").write_bytes(b"original")
    job = store.add_job(recording["id"], "media")
    path = f"/api/projects/{project['id']}/storage"
    body = {"storage_parent": str(tmp_path / "Destination")}
    assert client.post(path, json=body).status_code == 409
    store.update_job(job["id"], state="completed")

    def fail_copy(*args, **kwargs):
        raise OSError("synthetic full disk")

    monkeypatch.setattr("cbs_transcriber.storage.shutil.copytree", fail_copy)
    assert client.post(path, json=body).status_code == 400
    assert store.directory(recording["id"]) == source
    assert (source / "original.wav").read_bytes() == b"original"
    assert not (tmp_path / "Destination" / project["id"][:8]).exists()


def test_v19_legacy_project_move_preserves_other_projects_and_database(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Legacy")
    store.execute("UPDATE projects SET storage_dir=NULL WHERE id=?", (project["id"],))
    other = store.create_project("Keep")
    recordings = [store.create_recording(p["id"], "source.wav") for p in (project, other)]
    before = []
    for recording in recordings:
        folder = store.directory(recording["id"])
        folder.mkdir(parents=True)
        (folder / "original.wav").write_bytes(b"keep recording")
        before.append(folder)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(tmp_path / "Moved")}
    )
    assert response.status_code == 200, response.text
    assert store.db_path.exists()
    assert store.directory(recordings[1]["id"]) == before[1]
    assert (before[1] / "original.wav").read_bytes() == b"keep recording"
    assert not before[0].exists()


def test_v19_committed_move_survives_finalization_error(client, tmp_path, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Committed")
    recording = store.create_recording(project["id"], "source.wav")
    source = store.directory(recording["id"])
    source.mkdir(parents=True)
    (source / "original.wav").write_bytes(b"keep both copies")
    execute = store.execute

    def commit_then_fail(sql, args=()):
        result = execute(sql, args)
        if sql.startswith("UPDATE projects SET storage_dir"):
            raise OSError("synthetic lost connection after commit")
        return result

    monkeypatch.setattr(store, "execute", commit_then_fail)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(tmp_path / "Moved")}
    )
    assert response.status_code == 200
    assert response.json()["cleanup_warning"]
    assert source.exists() and store.directory(recording["id"]) != source
    assert (store.directory(recording["id"]) / "original.wav").read_bytes() == b"keep both copies"


def test_v19_relocation_rejects_nested_and_existing_destinations(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Do not overwrite")
    original = store.project_folder(project["id"])
    endpoint = f"/api/projects/{project['id']}/storage"
    assert (
        client.post(endpoint, json={"storage_parent": str(original / "nested")}).status_code == 400
    )
    destination = tmp_path / "Existing"
    target = destination / project["id"][:8]
    target.mkdir(parents=True)
    (target / "unrelated.txt").write_text("Keep me")
    assert client.post(endpoint, json={"storage_parent": str(destination)}).status_code == 400
    assert (target / "unrelated.txt").read_text() == "Keep me"
    assert store.project_folder(project["id"]) == original


def test_v19_cleanup_failure_keeps_active_copy_and_reports_leftovers(client, tmp_path, monkeypatch):
    import shutil

    store = client.app.state.store
    project = store.create_project("Cleanup")
    recording = store.create_recording(project["id"], "source.wav")
    source = store.directory(recording["id"])
    source.mkdir(parents=True)
    (source / "original.wav").write_bytes(b"safe")
    remove = shutil.rmtree

    def deny_old(path, *args, **kwargs):
        if path == source:
            raise PermissionError("synthetic locked source")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr("cbs_transcriber.storage.shutil.rmtree", deny_old)
    response = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(tmp_path / "Moved")}
    )
    assert response.status_code == 200
    assert "old copies remain" in response.json()["cleanup_warning"]
    assert source.exists()
    assert (store.directory(recording["id"]) / "original.wav").read_bytes() == b"safe"
