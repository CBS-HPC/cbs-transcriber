"""Independent review changes participate in destructive-operation consent."""

import pytest

from cbs_transcriber import deletions
from cbs_transcriber.transcripts import normalize_segments


def seed(client, project=None):
    store = client.app.state.store
    project = project or store.create_project("Synthetic review deletion")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.directory(recording["id"]).mkdir(parents=True)
    (store.directory(recording["id"]) / "original.wav").write_bytes(b"synthetic")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "First"}, {"text": "Second"}]),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    return project, recording["id"], result["id"]


def mark(client, rid, result_id, status="checked", index=0):
    loaded = client.get(f"/api/recordings/{rid}/passage-reviews", params={"result_id": result_id})
    assert loaded.status_code == 200, loaded.text
    loaded = loaded.json()
    passage = loaded["passages"][index]
    response = client.patch(
        f"/api/recordings/{rid}/segments/{passage['segment_id']}/review",
        json={
            "result_id": result_id,
            "revision": loaded["revision"],
            "status": status,
            "review_revision": passage["review_revision"],
            "passage_sha256": passage["passage_sha256"],
        },
    )
    assert response.status_code == 200, response.text


def deletion_preview(client, kind, project, rid):
    route = f"/api/projects/{project['id']}" if kind == "project" else f"/api/recordings/{rid}"
    response = client.post(route + "/deletion-preview")
    assert response.status_code == 200, response.text
    return route, response.json()


@pytest.mark.parametrize("kind", ["project", "recording"])
@pytest.mark.parametrize("change", ["new", "update", "reset"])
def test_review_change_after_delete_preview_keeps_recording(client, kind, change):
    project, rid, result_id = seed(client)
    store = client.app.state.store
    if change != "new":
        mark(client, rid, result_id)
    route, preview = deletion_preview(client, kind, project, rid)
    previous = store.recording(rid)
    mark(client, rid, result_id, "not_checked" if change == "reset" else "needs_review")
    assert store.recording(rid) == previous  # Review has its own revision.
    response = client.delete(route, params={"operation_id": preview["operation_id"]})
    assert response.status_code == 409, response.text
    assert store.recording(rid)["id"] == rid
    assert (store.directory(rid) / "original.wav").read_bytes() == b"synthetic"


@pytest.mark.parametrize("kind", ["project", "recording"])
def test_unchanged_review_allows_delete_and_rows_cascade(client, kind):
    project, rid, result_id = seed(client)
    store = client.app.state.store
    mark(client, rid, result_id)
    route, preview = deletion_preview(client, kind, project, rid)
    response = client.delete(route, params={"operation_id": preview["operation_id"]})
    assert response.status_code == 200, response.text
    assert store.all("SELECT * FROM passage_reviews WHERE result_id=?", (result_id,)) == []


@pytest.mark.parametrize("kind", ["project", "recording"])
def test_review_outside_delete_scope_does_not_invalidate_preview(client, kind):
    project, rid, _ = seed(client)
    other_project, other_rid, other_result = seed(client, project if kind == "recording" else None)
    route, preview = deletion_preview(client, kind, project, rid)
    mark(client, other_rid, other_result)
    response = client.delete(route, params={"operation_id": preview["operation_id"]})
    assert response.status_code == 200, response.text
    assert client.app.state.store.recording(other_rid)["project_id"] == other_project["id"]


def test_deletion_review_metadata_is_bounded(client, monkeypatch):
    project, rid, result_id = seed(client)
    mark(client, rid, result_id)
    mark(client, rid, result_id, index=1)
    monkeypatch.setattr(deletions, "MAX_JOBS", 1)
    response = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert response.status_code == 409, response.text
    assert client.app.state.store.recording(rid)["id"] == rid


@pytest.mark.parametrize("field,size", [("segment_id", 801), ("updated", 65)])
def test_oversized_review_metadata_refuses_delete_preview(client, field, size):
    project, rid, result_id = seed(client)
    mark(client, rid, result_id)
    # These are fixed field names, not request-controlled SQL.
    client.app.state.store.execute(
        f"UPDATE passage_reviews SET {field}=? WHERE result_id=?", ("x" * size, result_id)
    )
    response = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert response.status_code == 409, response.text
    assert client.app.state.store.recording(rid)["id"] == rid


def test_prior_schema_metadata_does_not_require_new_review_table(client):
    project, rid, _ = seed(client)
    store = client.app.state.store
    with store.connection() as db:
        db.execute("DROP TABLE passage_reviews")
        db.execute("PRAGMA user_version=18")
    digest = client.app.state.deletions._metadata("project", project["id"], (rid,))
    assert len(digest) == 64
