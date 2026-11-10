"""Sequential Delete after a settled shared allocation; synthetic libraries only."""

import json

import pytest
from test_delete_overrules import native_owner, runtime_owner
from test_delete_overrules import remote as remote

from cbs_transcriber import runtime_store
from cbs_transcriber.transcripts import normalize_segments


def remove(client, route):
    response = client.delete(route)
    if response.status_code == 409:
        detail = response.json()["detail"]
        response = client.delete(
            route,
            params={
                "operation_id": detail["operation_id"],
                "fingerprint": detail["fingerprint"],
                "confirm_shared": True,
            },
        )
    return response


def owner_for(client, kind, state, journal):
    store = client.app.state.store
    if kind == "runtime":
        project = store.create_project("Synthetic sequential Delete")
        owner = runtime_owner(store, project, 2, state=state, journal=journal)
        return project, owner.recordings, owner.jobs, owner.batch_id
    project, recordings, batch_id, _ = native_owner(client, state=state, run=journal)
    batch = store.native_batch(batch_id)
    jobs = [store.job(member["job_id"]) for member in batch["members"]]
    return project, recordings, jobs, batch_id


def journal_for(store, kind, batch_id):
    if kind == "runtime":
        return runtime_store.get(store, batch_id)["journal"]
    return store.native_batch(batch_id)["params"]["ucloud_run"]


@pytest.mark.parametrize("kind", ["runtime", "native"])
@pytest.mark.parametrize("state", ["interrupted", "failed"])
@pytest.mark.parametrize("cleanup", ["trashed", "deleted"])
def test_v165_project_delete_after_recording_left_settled_owner(
    client, remote, kind, state, cleanup
):
    store = client.app.state.store
    project, recordings, jobs, batch_id = owner_for(client, kind, state, {"cleanup_state": cleanup})
    removed = recordings[0]
    deleted = remove(client, f"/api/recordings/{removed['id']}")
    assert deleted.status_code == 200, deleted.text
    assert not store.all("SELECT id FROM recordings WHERE id=?", (removed["id"],))
    assert not store.all("SELECT id FROM jobs WHERE id=?", (jobs[0]["id"],))
    # This is the existing historical shape: a settled owner retains its member row.
    journal = journal_for(store, kind, batch_id)
    assert journal["cleanup_state"] == cleanup
    assert jobs[0]["id"] not in journal.get("deleted_members", [])
    # A different queued job must still be captured/cancelled by project Delete.
    extra = store.create_recording(project["id"], "queued.wav")
    queued = store.add_job(extra["id"], "media", {})

    preview = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["affected_recordings"] == []
    assert preview.json()["remote_folders"] == []
    assert preview.json()["cancel_jobs"] == 1
    deleted = client.delete(
        f"/api/projects/{project['id']}",
        params={"operation_id": preview.json()["operation_id"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    assert not store.all("SELECT id FROM jobs WHERE id=?", (queued["id"],))
    assert journal_for(store, kind, batch_id) == journal
    assert not remote.terminated and not remote.trashed and not remote.submitted


@pytest.mark.parametrize("kind", ["runtime", "native"])
@pytest.mark.parametrize("cleanup", [None, "pending", "retained", "unknown"])
def test_v165_unsettled_owner_still_requires_deleted_member_receipt(client, remote, kind, cleanup):
    store = client.app.state.store
    project, recordings, jobs, batch_id = owner_for(
        client, kind, "interrupted", {"cleanup_state": cleanup}
    )
    # A missing member without a Delete receipt remains unverified for unresolved owners.
    store.execute("DELETE FROM jobs WHERE id=?", (jobs[0]["id"],))
    store.execute("DELETE FROM recordings WHERE id=?", (recordings[0]["id"],))
    result = store.add_result(
        recordings[1]["id"],
        normalize_segments([{"text": "Synthetic available partial result", "start": 0, "end": 1}]),
        {},
        "Synthetic",
        {},
    )
    preserved = store.all("SELECT * FROM result_versions WHERE result_id=?", (result["id"],))
    journal = journal_for(store, kind, batch_id)
    response = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert response.status_code == 404, response.text
    assert store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    assert (
        store.all("SELECT * FROM result_versions WHERE result_id=?", (result["id"],)) == preserved
    )
    assert journal_for(store, kind, batch_id) == journal
    assert not remote.terminated and not remote.trashed and not remote.submitted


@pytest.mark.parametrize("kind", ["runtime", "native"])
@pytest.mark.parametrize(
    ("state", "remote_state"),
    [
        ("queued", "SUCCESS"),
        ("running", "SUCCESS"),
        ("interrupted", "RUNNING"),
        ("interrupted", None),
    ],
)
def test_v165_active_or_unknown_remote_owner_is_not_treated_as_settled(
    client, remote, kind, state, remote_state
):
    store = client.app.state.store
    project, recordings, jobs, batch_id = owner_for(
        client, kind, state, {"cleanup_state": "trashed", "remote_state": remote_state}
    )
    store.execute("DELETE FROM jobs WHERE id=?", (jobs[0]["id"],))
    store.execute("DELETE FROM recordings WHERE id=?", (recordings[0]["id"],))
    journal = journal_for(store, kind, batch_id)
    response = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert response.status_code == 404, response.text
    assert journal_for(store, kind, batch_id) == journal
    assert not remote.terminated and not remote.trashed and not remote.submitted


@pytest.mark.parametrize("kind", ["runtime", "native"])
def test_v165_new_cleanup_obligation_invalidates_settled_preview(client, remote, kind):
    store = client.app.state.store
    project, recordings, jobs, batch_id = owner_for(
        client, kind, "interrupted", {"cleanup_state": "trashed"}
    )
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["remote_folders"] == []
    journal = journal_for(store, kind, batch_id)
    journal["cleanup_state"] = "pending"
    if kind == "runtime":
        store.execute(
            "UPDATE runtime_batch_runs SET journal=? WHERE id=?", (json.dumps(journal), batch_id)
        )
    else:
        store.update_native_batch(batch_id, run_update={"cleanup_state": "pending"})
    response = client.delete(
        f"/api/projects/{project['id']}",
        params={"operation_id": preview.json()["operation_id"]},
    )
    assert response.status_code == 409, response.text
    assert "allocation changed" in response.json()["detail"]
    assert store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    assert all(store.all("SELECT id FROM jobs WHERE id=?", (job["id"],)) for job in jobs)
    assert all(store.all("SELECT id FROM recordings WHERE id=?", (r["id"],)) for r in recordings)
    assert journal_for(store, kind, batch_id) == journal
    assert not remote.terminated and not remote.trashed and not remote.submitted
