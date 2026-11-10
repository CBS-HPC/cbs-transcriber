"""A02 synthetic publication/metadata boundaries; no media worker or network."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from test_import_artifact_capture import make_link


@pytest.mark.parametrize("unreadable", [False, True], ids=["observed-live", "unknown-commit"])
def test_upload_keeps_original_after_durable_publication_reply_failure(
    client, wav_bytes, monkeypatch, unreadable
):
    store = client.app.state.store
    project = store.create_project("Synthetic uncertain upload")
    publish = store.publish_media_recording
    connection = store.connection
    observed = []

    @contextmanager
    def unavailable():
        raise OSError("Synthetic unavailable ownership read")
        yield  # pragma: no cover

    def lost_reply(project_id, filename, identifier, *args, **kwargs):
        publish(project_id, filename, identifier, *args, **kwargs)
        observed.append(identifier)
        if unreadable:
            monkeypatch.setattr(store, "connection", unavailable)
        raise OSError("Synthetic publication committed before reply failed")

    monkeypatch.setattr(store, "publish_media_recording", lost_reply)
    request = TestClient(client.app, headers={"X-CBS-Request": "1"}, raise_server_exceptions=False)
    try:
        reply = request.post(
            "/api/recordings",
            data={"project_id": project["id"]},
            files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
        )
    finally:
        monkeypatch.setattr(store, "connection", connection)
        request.close()
    assert reply.status_code == 500
    [identifier] = observed
    recording = store.recording(identifier)
    assert recording["project_id"] == project["id"]
    assert (
        store.one("SELECT state FROM jobs WHERE recording_id=?", (identifier,))["state"] == "queued"
    )
    assert (store.directory(identifier) / "original.wav").read_bytes() == wav_bytes


def test_upload_cleans_exclusive_original_when_publication_is_proved_absent(
    client, wav_bytes, monkeypatch
):
    store = client.app.state.store
    project = store.create_project("Synthetic rolled back upload")
    observed = []

    def refused(project_id, filename, identifier, *args, **kwargs):
        observed.append(identifier)
        raise ValueError("Synthetic publication refused before commit")

    monkeypatch.setattr(store, "publish_media_recording", refused)
    reply = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
    )
    assert reply.status_code == 400
    [identifier] = observed
    assert store.all("SELECT id FROM recordings WHERE id=?", (identifier,)) == []
    assert not store.directory(identifier, project["id"]).exists()


@pytest.mark.parametrize("first,second", [("Øresund", "øresund"), ("Æble", "æBLE"), ("ÅR", "år")])
def test_collection_names_are_case_insensitive_for_danish_letters(client, first, second):
    store = client.app.state.store
    project = store.create_project("Synthetic Danish collections")
    existing = store.create_collection(project["id"], first)
    refused = client.post(f"/api/projects/{project['id']}/collections", json={"name": second})
    assert refused.status_code == 400, refused.text
    assert [item["id"] for item in store.collections(project["id"])] == [existing["id"]]


def test_collection_rename_observes_unicode_clash_but_other_projects_remain_independent(client):
    store = client.app.state.store
    first = store.create_project("Synthetic first")
    other = store.create_project("Synthetic other")
    store.create_collection(first["id"], "Øresund")
    rename = store.create_collection(first["id"], "Unrelated")
    elsewhere = store.create_collection(other["id"], "øresund")
    reply = client.patch(f"/api/collections/{rename['id']}", json={"name": "øRESUND"})
    assert reply.status_code == 400
    assert store.collection(rename["id"])["name"] == "Unrelated"
    assert store.collection(elsewhere["id"])["name"] == "øresund"


def test_project_move_refuses_a_linked_managed_root_without_deleting_its_target(client, tmp_path):
    store = client.app.state.store
    project = store.create_project("Synthetic linked project")
    recording = store.create_recording(project["id"], "synthetic.wav")
    original = store.project_folder(project["id"])
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "original.wav").write_bytes(b"Synthetic external target must survive")
    outside = tmp_path / "unrelated-link-target"
    assert original.resolve().is_relative_to(tmp_path.resolve())
    original.rename(outside)
    make_link(original, outside, "junction")
    target = outside / "recordings" / recording["id"] / "original.wav"
    reply = client.post(
        f"/api/projects/{project['id']}/storage", json={"storage_parent": str(tmp_path / "Moved")}
    )
    assert reply.status_code == 400, reply.text
    assert target.read_bytes() == b"Synthetic external target must survive"
    assert store.project_folder(project["id"]) == original
    assert not (tmp_path / "Moved" / project["id"][:8]).exists()


def test_project_listing_uses_captured_storage_metadata_when_another_project_is_deleted(
    client, monkeypatch
):
    store = client.app.state.store
    removed = store.create_project("Synthetic removed during read")
    survivor = store.create_project("Synthetic retained during read")
    read = store.all
    deleted = []

    def interleave(sql, params=()):
        rows = read(sql, params)
        if sql.startswith("SELECT p.*,COUNT(r.id)") and not deleted:
            deleted.append(client.delete(f"/api/projects/{removed['id']}"))
        return rows

    monkeypatch.setattr(store, "all", interleave)
    response = client.get("/api/projects")
    assert deleted[0].status_code == 200
    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert rows[survivor["id"]]["storage_path"] == survivor["storage_dir"]
    assert client.get(f"/api/projects/{removed['id']}/recordings").status_code == 404
