"""Managed deletion handles Windows read-only attributes and reports locked files."""

import os
import stat

import pytest

from cbs_transcriber import deletion_files


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only directory semantics")
def test_project_delete_accepts_readonly_recording_export_and_metadata(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Read-only synced folders", storage_parent=str(tmp_path))
    recording = store.create_recording(project["id"], "synthetic.wav")
    folder = store.directory(recording["id"])
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / "original.wav"
    original.write_bytes(b"synthetic original")
    original.chmod(stat.S_IREAD)
    folder.chmod(stat.S_IREAD)
    export = store.project_folder(project["id"]) / "exports" / recording["id"]
    export.mkdir(parents=True)
    (export / "result.txt").write_text("Synthetic", encoding="utf-8")
    export.chmod(stat.S_IREAD)
    metadata = store.project_folder(project["id"]) / "project.json"
    assert metadata.exists()
    metadata.chmod(stat.S_IREAD)
    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    assert not folder.exists() and not export.exists()
    assert not metadata.exists()
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))


def test_locked_directory_returns_actionable_error_without_erasing_database(client, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Locked folder")
    recording = store.create_recording(project["id"], "synthetic.wav")
    folder = store.directory(recording["id"])
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / "original.wav"
    original.write_bytes(b"synthetic original")

    def locked(*args, **kwargs):
        raise PermissionError("synthetic sharing violation")

    monkeypatch.setattr(deletion_files, "_rename", locked)
    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 409, response.text
    assert "files" in response.json()["detail"]
    assert "retry" in response.json()["detail"].lower()
    assert store.recording(recording["id"])
    assert original.read_bytes() == b"synthetic original"


def test_readonly_retry_does_not_bypass_a_sharing_or_acl_denial(tmp_path):
    target = tmp_path / "writable.txt"
    target.write_bytes(b"Retain on permission failure")
    failure = PermissionError("synthetic ACL or sharing failure")
    with pytest.raises(PermissionError) as caught:
        deletion_files._retry_readonly(os.unlink, target, failure)
    assert caught.value is failure
    assert target.read_bytes() == b"Retain on permission failure"
