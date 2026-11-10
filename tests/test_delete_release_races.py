"""Delete's remote release must own its exact allocation until Trash has settled."""

import json
import threading

import pytest
from test_delete_overrules import confirmed_delete, native_owner, runtime_owner
from test_delete_overrules import remote as remote
from test_ucloud_native import PAYLOAD

from cbs_transcriber import native_batch, remote_release, runtime_store
from cbs_transcriber import runtime_batches as batches


def test_retry_cannot_reopen_owner_between_delete_release_validation_and_trash(
    client, remote, monkeypatch
):
    store = client.app.state.store
    project = store.create_project("Delete versus explicit recovery")
    owner = runtime_owner(store, project)
    path = f"/api/projects/{project['id']}"
    preview = client.delete(path)
    assert preview.status_code == 409, preview.text
    detail = preview.json()["detail"]
    assert detail["code"] == "confirm_remote_cleanup"
    entered, release = threading.Event(), threading.Event()
    results, errors = [], []
    original = remote_release.release_for_deletion

    def pause_before_release(*args, **kwargs):
        entered.set()
        assert release.wait(5), "The synthetic test must release the remote-control gate"
        return original(*args, **kwargs)

    # A real Retry can queue terminal output recovery without setting state='running'.
    # That must not slip past release's stopped-state check or invalidate consent too late.
    monkeypatch.setattr(
        batches, "_status", lambda *args: {"generation": owner.batch_id, "phase": "finished"}
    )
    monkeypatch.setattr(remote_release, "release_for_deletion", pause_before_release)

    def delete():
        try:
            results.append(
                client.delete(
                    path,
                    params={
                        "operation_id": detail["operation_id"],
                        "fingerprint": detail["fingerprint"],
                    },
                )
            )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=delete)
    worker.start()
    try:
        assert entered.wait(3), "Delete did not reach its synthetic release gate"
        retry = client.post(f"/api/runtime-batches/{owner.batch_id}/retry")
        assert retry.status_code == 409, retry.text
        assert not runtime_store.get(store, owner.batch_id)["journal"].get("recovery_requested")
        assert remote.trashed == []
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors
    assert len(results) == 1 and results[0].status_code == 200, [r.text for r in results]
    assert remote.trashed == [owner.folder]
    assert remote.submitted == []


@pytest.mark.parametrize("receipt,missing", [(False, True), (True, False)])
def test_native_claim_requires_both_delete_receipt_and_absent_job(client, receipt, missing):
    store = client.app.state.store
    _, recordings, batch_id, _ = native_owner(client, state="queued")
    members = store.native_batch(batch_id)["members"]
    removed = next(m for m in members if m["recording_id"] == recordings[0]["id"])
    survivor = next(m for m in members if m["recording_id"] == recordings[1]["id"])
    store.update_job(survivor["job_id"], state="queued")
    if receipt:
        store.update_native_batch(batch_id, run_update={"deleted_members": [removed["job_id"]]})
    if missing:
        store.execute("DELETE FROM recordings WHERE id=?", (recordings[0]["id"],))
    with pytest.raises(ValueError, match="member state changed"):
        store.claim_native_batch(batch_id)
    assert store.native_batch(batch_id)["state"] == "queued"
    assert store.job(survivor["job_id"])["state"] == "queued"


def test_native_survivor_retry_imports_without_recreating_deleted_member(
    client, remote, monkeypatch
):
    store = client.app.state.store
    _, recordings, batch_id, _ = native_owner(client, state="failed")
    before = store.one("SELECT members FROM native_batch_runs WHERE id=?", (batch_id,))["members"]
    members = store.native_batch(batch_id)["members"]
    deleted_id = next(m["job_id"] for m in members if m["recording_id"] == recordings[0]["id"])
    survivor = next(m for m in members if m["recording_id"] == recordings[1]["id"])
    _, response = confirmed_delete(client, f"/api/recordings/{recordings[0]['id']}", shared=True)
    assert response.status_code == 200, response.text
    assert remote.trashed == []
    assert native_batch.deleted_members(store, store.native_batch(batch_id)) == {deleted_id}

    store.execute("UPDATE recordings SET status='ready' WHERE id=?", (recordings[1]["id"],))
    folder = store.directory(recordings[1]["id"])
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / ("ucloud-" + survivor["job_id"] + ".json")
    raw.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    monkeypatch.setattr(native_batch.native, "client_for", remote_release._native_client)
    monkeypatch.setattr(native_batch, "_output_paths", lambda *args: [])
    retry = client.post(f"/api/jobs/{survivor['job_id']}/retry", json={"replace": True})
    assert retry.status_code == 200, retry.text
    assert retry.json()["affected_jobs"] == [survivor["job_id"]]
    claimed = store.claim_job("transcribe")
    assert claimed["id"] == survivor["job_id"]
    queue = client.app.state.queue
    queue.active_batch_id = batch_id
    queue.process(claimed["id"])

    assert store.job(survivor["job_id"])["state"] == "completed"
    assert store.native_batch(batch_id)["state"] == "completed"
    assert len(store.results(recordings[1]["id"])) == 1
    assert not store.all("SELECT id FROM jobs WHERE id=?", (deleted_id,))
    assert not store.all("SELECT id FROM recordings WHERE id=?", (recordings[0]["id"],))
    assert (
        store.one("SELECT members FROM native_batch_runs WHERE id=?", (batch_id,))["members"]
        == before
    )
    assert remote.submitted == []


@pytest.mark.parametrize("kind", ["runtime", "native"])
def test_partial_delete_stops_failed_local_owner_and_retains_unsaved_sibling(
    client, remote, monkeypatch, kind
):
    store = client.app.state.store
    remote.state = "RUNNING"
    monkeypatch.setattr(native_batch.native, "client_for", remote_release._native_client)
    if kind == "runtime":
        project = store.create_project("Failed monitor with live allocation")
        owner = runtime_owner(
            store,
            project,
            2,
            journal={"remote_state": "RUNNING", "stop_intent": False},
        )
        recordings, batch_id, remote_id = owner.recordings, owner.batch_id, "owned-remote-job"
        # Submitted private tasks must stay recoverable for the surviving file.
        store.execute(
            "UPDATE runtime_batch_attempts SET input_receipt=? WHERE job_id IN (?,?)",
            (json.dumps({"publish_intent": True}), *(job["id"] for job in owner.jobs)),
        )
    else:
        _, recordings, batch_id, _ = native_owner(
            client, state="failed", run={"remote_state": "RUNNING"}
        )
        remote_id = "native-remote-job"
    detail, response = confirmed_delete(
        client, f"/api/recordings/{recordings[0]['id']}", shared=True
    )
    assert detail["remote_folders"][0]["action"] == "retain"
    assert detail["remote_folders"][0]["stop_remote"] is True
    assert response.status_code == 200, response.text
    assert remote.terminated == [remote_id]
    assert remote.trashed == []
    assert store.recording(recordings[1]["id"])
    assert not store.all("SELECT id FROM recordings WHERE id=?", (recordings[0]["id"],))
    if kind == "runtime":
        batch = runtime_store.get(store, batch_id)
        assert not batch["journal"].get("release_decision")
        assert batch["journal"]["owned_folder"] == owner.folder
    else:
        batch = store.native_batch(batch_id)
        assert not batch["params"]["ucloud_run"].get("release_decision")
    assert remote.submitted == []


@pytest.mark.parametrize("kind", ["runtime", "native"])
def test_sequential_deletes_keep_owner_until_last_unsaved_sibling_is_deleted(
    client, remote, monkeypatch, kind
):
    store = client.app.state.store
    monkeypatch.setattr(native_batch.native, "client_for", remote_release._native_client)
    if kind == "runtime":
        project = store.create_project("Delete one batch member at a time")
        owner = runtime_owner(store, project, 2)
        recordings, batch_id, folder = owner.recordings, owner.batch_id, owner.folder
        table = "runtime_batch_runs"
    else:
        _, recordings, batch_id, folder = native_owner(client, state="failed")
        table = "native_batch_runs"
    column = "params" if kind == "native" else "journal"
    frozen_members = (
        store.one("SELECT members FROM native_batch_runs WHERE id=?", (batch_id,))["members"]
        if kind == "native"
        else store.all(
            "SELECT job_id,recording_id,ordinal FROM runtime_batch_members "
            "WHERE batch_id=? ORDER BY ordinal",
            (batch_id,),
        )
    )
    first, response = confirmed_delete(
        client, f"/api/recordings/{recordings[0]['id']}", shared=True
    )
    assert response.status_code == 200, response.text
    assert first["remote_folders"][0]["action"] == "retain"
    assert remote.trashed == []
    second, response = confirmed_delete(
        client, f"/api/recordings/{recordings[1]['id']}", shared=True
    )
    assert response.status_code == 200, response.text
    assert second["remote_folders"][0]["action"] == "trash"
    assert remote.trashed == [folder]
    assert remote.terminated == [] and remote.submitted == []
    for recording in recordings:
        assert not store.all("SELECT id FROM recordings WHERE id=?", (recording["id"],))
    raw = json.loads(store.one(f"SELECT {column} FROM {table} WHERE id=?", (batch_id,))[column])
    run = raw["ucloud_run"] if kind == "native" else raw
    assert run["cleanup_state"] == "trashed"
    assert len(run["deleted_members"]) == 2
    current_members = (
        store.one("SELECT members FROM native_batch_runs WHERE id=?", (batch_id,))["members"]
        if kind == "native"
        else store.all(
            "SELECT job_id,recording_id,ordinal FROM runtime_batch_members "
            "WHERE batch_id=? ORDER BY ordinal",
            (batch_id,),
        )
    )
    assert current_members == frozen_members
