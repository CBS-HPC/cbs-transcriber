"""Delete overrules remote obligations; old obligations release themselves (ADR 0101).

Synthetic libraries and an in-memory UCloud double only: no live allocation, upload or Trash.
"""

import copy
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from test_runtime_discard import retained as retained
from test_runtime_store import library as library
from test_ucloud_native import SETUP

from cbs_transcriber import native_batch, providers, remote_release, runtime_store
from cbs_transcriber import runtime_batches as batches
from cbs_transcriber import ucloud_native as native
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments


class Remote:
    """Records every remote control request; no request leaves the process."""

    def __init__(self):
        self.state = "SUCCESS"
        self.terminated, self.trashed, self.submitted = [], [], []
        self.retrieved = []
        self.trash_error = None
        self.jobs = []

    def retrieve_job(self, identifier):
        self.retrieved.append(identifier)
        return {"id": identifier, "status": {"state": self.state}}

    def terminate_job(self, identifier):
        self.terminated.append(identifier)
        self.state = "CANCELLED"

    def submit_job(self, *args, **kwargs):
        self.submitted.append(args)
        raise AssertionError("Release never submits a UCloud job.")

    def browse_jobs(self, items_per_page=100, next_page=None):
        return {"items": self.jobs, "next": None}

    def trash(self, client, folder):
        if self.trash_error:
            raise self.trash_error
        self.trashed.append(folder)


@pytest.fixture
def remote(monkeypatch):
    double = Remote()

    @contextmanager
    def connection(*args, **kwargs):
        yield double

    monkeypatch.setattr(batches, "_client", connection)
    monkeypatch.setattr(remote_release, "_native_client", connection)
    monkeypatch.setattr(native, "require_setup", lambda *args: None)
    monkeypatch.setattr(native, "trash_owned_folder", double.trash)
    return double


def runtime_params(store):
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://runtime.invalid",
        model="large-v3",
        approved_for=["protected"],
        ucloud_transcriber={"project_id": "remote-project", "storage_path": "/drive/study"},
    )
    provider_id = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (provider_id, "Synthetic runtime", settings.model_dump_json(), now()),
    )
    return {
        "transcription": True,
        "provider_id": provider_id,
        "enhancement": {"execution": "ucloud", "device": "cuda", "transcribe": True},
        "ucloud_api": {
            "provider_id": provider_id,
            "settings": settings.model_dump(),
            "runtime_id": "synthetic-runtime-id",
            "runtime_product": MIG_PRODUCT,
            "runtime_compute_policy": "mig-v1",
            "prepare_runtime": False,
            "batch_plan": {
                "identity": {
                    "protocol": runtime_store.PROTOCOL,
                    "application": {"name": "Terminal", "version": "synthetic-1"},
                    "code_hashes": {"supervisor": "1" * 64, "controller": "2" * 64},
                    "model_revisions": {"large-v3": "pinned"},
                    "recipe_hash": "3" * 64,
                    "limits": {
                        "max_members": 100,
                        "idle_grace_seconds": 60,
                        "owner_absence_seconds": 300,
                        "late_cutoff_seconds": 120,
                        "gpu_concurrency": 1,
                    },
                }
            },
        },
    }


def runtime_owner(store, project, count=1, *, state="failed", journal=None, saved=()):
    """One terminal owner like the live case: remote SUCCESS and cleanup pending."""
    params = runtime_params(store)
    jobs, recordings = [], []
    for index in range(count):
        row = store.create_recording(project["id"], f"file-{index}.wav")
        store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (row["id"],))
        directory = store.directory(row["id"])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "original.wav").write_bytes(b"synthetic original " + bytes([index]))
        recordings.append(store.recording(row["id"]))
        jobs.append(store.add_job(row["id"], "enhance", copy.deepcopy(params)))
    batch = runtime_store.for_job(store, jobs[0])
    assert {runtime_store.for_job(store, job)["id"] for job in jobs} == {batch["id"]}
    folder = batches.remote_folder(batches._settings(batch), batch["id"])
    values = {
        "job_id": "owned-remote-job",
        "run_name": "CBS Runtime batch " + batch["id"],
        "folder_created": True,
        "owned_folder": folder,
        "remote_state": "SUCCESS",
        "stop_intent": True,
        "cleanup_state": "pending",
        **(journal or {}),
    }
    store.execute(
        "UPDATE runtime_batch_runs SET state=?,admission='closed',journal=? WHERE id=?",
        (state, json.dumps(values), batch["id"]),
    )
    for index, job in enumerate(jobs):
        member = "completed" if index in saved else "failed"
        store.execute(
            "UPDATE runtime_batch_members SET state=? WHERE job_id=?", (member, job["id"])
        )
        store.execute(
            "UPDATE runtime_batch_attempts SET state=? WHERE job_id=?",
            ("complete" if index in saved else "failed", job["id"]),
        )
        store.execute(
            "UPDATE jobs SET state=? WHERE id=?",
            ("completed" if index in saved else "failed", job["id"]),
        )
        if index in saved:
            result = store.add_result(
                recordings[index]["id"],
                normalize_segments([{"text": f"Saved {index}.", "start": 0, "end": 1}]),
                {},
                "Saved",
                {},
            )
            store.execute(
                "UPDATE runtime_batch_members SET result_id=? WHERE job_id=?",
                (result["id"], job["id"]),
            )
    return SimpleNamespace(
        batch_id=batch["id"], folder=folder, jobs=jobs, recordings=recordings, project=project
    )


def confirmed_delete(client, route, *, shared=False):
    response = client.delete(route)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    params = {"operation_id": detail["operation_id"], "fingerprint": detail["fingerprint"]}
    if shared:
        params["confirm_shared"] = True
    return detail, client.delete(route, params=params)


def test_project_delete_trashes_failed_batch_folder_and_leaves_no_obligation(client, remote):
    store = client.app.state.store
    project = store.create_project("Failed batch study")
    owner = runtime_owner(store, project, 4)
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview")
    assert preview.status_code == 200, preview.text
    [folder] = preview.json()["remote_folders"]
    assert folder["folder"] == owner.folder and folder["action"] == "trash"
    assert folder["server"] == "https://runtime.invalid"
    assert "synthetic-not" not in preview.text and remote.trashed == []
    detail, response = confirmed_delete(client, f"/api/projects/{project['id']}")
    assert detail["code"] == "confirm_remote_cleanup"
    assert response.status_code == 200, response.text
    assert remote.trashed == [owner.folder] and remote.terminated == []
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    assert not store.all("SELECT id FROM recordings WHERE project_id=?", (project["id"],))
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["cleanup_state"] == "trashed"
    assert batch["journal"]["release_decision"]["target"] == {
        "kind": "project",
        "name": "Failed batch study",
    }
    assert sorted(batch["journal"]["deleted_members"]) == sorted(j["id"] for j in owner.jobs)
    assert not runtime_store.obligations(store)
    assert remote.submitted == []


def test_wrong_or_missing_fingerprint_never_cancels_or_trashes(client, remote):
    store = client.app.state.store
    project = store.create_project("Fingerprint study")
    owner = runtime_owner(store, project)
    route = f"/api/projects/{project['id']}"
    detail = client.delete(route).json()["detail"]
    response = client.delete(
        route, params={"operation_id": detail["operation_id"], "fingerprint": "0" * 64}
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "confirm_remote_cleanup"
    assert remote.trashed == [] and store.recording(owner.recordings[0]["id"])


def test_offline_trash_keeps_obligation_visible_and_retry_clears_it(client, remote):
    store = client.app.state.store
    project = store.create_project("Offline study")
    owner = runtime_owner(store, project, 2)
    remote.trash_error = httpx.ConnectError("offline")
    _, response = confirmed_delete(client, f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    [retained] = runtime_store.obligations(store)
    assert retained["id"] == owner.batch_id
    assert retained["journal"]["cleanup_state"] == "pending"
    assert retained["journal"]["cleanup_error_code"] == "connection_failed"
    [card] = [job for job in client.get("/api/jobs").json() if job.get("remote_release")]
    assert card["recording_name"] == "UCloud cleanup for a deleted project"
    assert card["remote_release"]["folder"] == owner.folder
    assert card["remote_release"]["can_retry"] is True
    assert card["runtime_batch"]["unresolved"] is True
    assert not card["runtime_batch"]["can_retry"] and not card["runtime_batch"]["can_discard"]
    assert {member["recording_name"] for member in card["runtime_batch"]["members"]} == {
        "Removed recording"
    }
    # Ordinary recovery can never resubmit or reopen a released owner.
    response = client.post(f"/api/runtime-batches/{owner.batch_id}/retry")
    assert response.status_code == 400 and "released" in response.json()["detail"]
    remote.trash_error = None
    response = client.post(f"/api/remote-releases/runtime_batch/{owner.batch_id}/retry")
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_state"] == "trashed"
    assert remote.trashed == [owner.folder] and not runtime_store.obligations(store)
    assert remote.submitted == []


def test_running_runtime_batch_is_stopped_then_trashed(client, remote):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Running study")
    owner = runtime_owner(
        store,
        project,
        2,
        state="running",
        journal={"remote_state": "RUNNING", "stop_intent": False},
    )
    store.execute("UPDATE runtime_batch_runs SET admission='open' WHERE id=?", (owner.batch_id,))
    for job in owner.jobs:
        store.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
        store.execute(
            "UPDATE runtime_batch_members SET state='running' WHERE job_id=?", (job["id"],)
        )
    remote.state = "RUNNING"
    worker = next(worker for worker in queue.workers if worker.kind == "runtime-batch")
    worker.active_batch_id = owner.batch_id
    route = f"/api/projects/{project['id']}"
    detail, response = confirmed_delete(client, route)
    assert detail["remote_folders"][0]["stop_remote"] is True
    assert response.status_code == 202, response.text
    assert runtime_store.get(store, owner.batch_id)["journal"]["stop_intent"] is True
    assert remote.trashed == [] and store.recording(owner.recordings[0]["id"])
    # The coordinator drains without confirming remote termination.
    store.execute("UPDATE runtime_batch_runs SET state='cancelled' WHERE id=?", (owner.batch_id,))
    for job in owner.jobs:
        store.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (job["id"],))
        store.execute(
            "UPDATE runtime_batch_members SET state='interrupted' WHERE job_id=?", (job["id"],)
        )
    worker.active_batch_id = None
    response = client.delete(route, params={"operation_id": detail["operation_id"]})
    assert response.status_code == 200, response.text
    assert remote.terminated == ["owned-remote-job"]
    assert remote.trashed == [owner.folder]
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["remote_state"] == "CANCELLED"
    assert {m["state"] for m in batch["members"]} == {"cancelled"}
    assert not runtime_store.obligations(store)


def test_unconfirmed_stop_keeps_waiting_obligation_until_remote_ends(client, remote):
    store = client.app.state.store
    project = store.create_project("Stubborn study")
    owner = runtime_owner(store, project, journal={"remote_state": "RUNNING"})
    remote.state = "RUNNING"
    remote.terminate_job = lambda identifier: remote.terminated.append(identifier)
    _, response = confirmed_delete(client, f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["cleanup_error_code"] == "remote_not_terminal"
    assert remote.terminated == ["owned-remote-job"] and remote.trashed == []
    remote.state = "CANCELLED"
    assert (
        remote_release.release_one(
            store, client.app.state.credentials, "runtime_batch", owner.batch_id
        )
        == "trashed"
    )
    assert remote.terminated == ["owned-remote-job"]  # termination is never re-requested
    assert not runtime_store.obligations(store)


def test_shared_allocation_needs_existing_confirmation_and_keeps_other_results(client, remote):
    store = client.app.state.store
    project = store.create_project("Shared study")
    owner = runtime_owner(store, project, 2, saved=(1,))
    other = owner.recordings[1]
    before = store.all("SELECT * FROM transcript_results WHERE recording_id=?", (other["id"],))
    route = f"/api/recordings/{owner.recordings[0]['id']}"
    detail, response = confirmed_delete(client, route)
    assert detail["code"] == "confirm_shared_cancellation"
    assert detail["affected_recordings"] == [{"id": other["id"], "name": other["name"]}]
    assert detail["remote_folders"][0]["action"] == "trash"
    assert remote.trashed == []
    response = client.delete(
        route,
        params={
            "operation_id": detail["operation_id"],
            "fingerprint": detail["fingerprint"],
            "confirm_shared": True,
        },
    )
    assert response.status_code == 200, response.text
    assert remote.trashed == [owner.folder]
    assert store.recording(other["id"])["id"] == other["id"]
    after = store.all("SELECT * FROM transcript_results WHERE recording_id=?", (other["id"],))
    assert after == before
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["deleted_members"] == [owner.jobs[0]["id"]]
    assert batch["journal"]["release_decision"]["target"]["kind"] == "recording"


def test_shared_allocation_with_unsaved_other_file_keeps_folder_for_that_file(
    client, remote, monkeypatch
):
    from cbs_transcriber import runtime_discard as discard

    store = client.app.state.store
    project = store.create_project("Unsaved sibling study")
    owner = runtime_owner(store, project, 2)
    route = f"/api/recordings/{owner.recordings[0]['id']}"
    response = client.delete(route)
    detail = response.json()["detail"]
    assert detail["code"] == "confirm_shared_cancellation"
    assert detail["remote_folders"][0]["action"] == "retain"
    response = client.delete(
        route, params={"operation_id": detail["operation_id"], "confirm_shared": True}
    )
    assert response.status_code == 200, response.text
    assert remote.trashed == []
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["deleted_members"] == [owner.jobs[0]["id"]]
    assert "release_decision" not in batch["journal"]
    assert runtime_store.obligations(store)[0]["id"] == owner.batch_id
    # Later automatic release skips the deleted file and preserves the other one first.
    remote.retrieve_job = lambda identifier: {
        "id": identifier,
        "status": {"state": "SUCCESS"},
        "specification": {
            "name": "CBS Runtime batch " + owner.batch_id,
            "application": batch["identity"]["application"],
            "product": batch["identity"]["runtime_product"],
            "resources": [{"type": "file", "path": owner.folder, "readOnly": False}],
        },
    }
    monkeypatch.setattr(discard.api, "read_remote", lambda *args, **kwargs: None)
    state = remote_release.release_one(
        store, client.app.state.credentials, "runtime_batch", owner.batch_id
    )
    assert state == "trashed", remote_release.note(store, "runtime_batch", owner.batch_id)
    assert remote.trashed == [owner.folder]
    decision = runtime_store.get(store, owner.batch_id)["journal"]["discard_decision"]
    assert decision["decided_by"] == "automatic"
    assert [a["job_id"] for a in decision["evidence"]["attempts"]] == [owner.jobs[1]["id"]]
    assert not runtime_store.obligations(store)


def test_recording_delete_of_single_member_owner_trashes_its_folder(client, remote):
    store = client.app.state.store
    project = store.create_project("Single file study")
    owner = runtime_owner(store, project)
    keep = store.create_recording(project["id"], "keep.wav")
    detail, response = confirmed_delete(client, f"/api/recordings/{owner.recordings[0]['id']}")
    assert detail["code"] == "confirm_remote_cleanup"
    assert response.status_code == 200, response.text
    assert remote.trashed == [owner.folder]
    assert store.recording(keep["id"])
    assert not runtime_store.obligations(store)


def test_runtime_recovery_skips_deleted_published_member(client, remote):
    store = client.app.state.store
    project = store.create_project("Recover surviving sibling")
    owner = runtime_owner(store, project, 2)
    job_id = owner.jobs[0]["id"]
    store.execute(
        "UPDATE runtime_batch_attempts SET input_receipt=? WHERE job_id=?",
        ('{"publish_intent":true}', job_id),
    )
    route = f"/api/recordings/{owner.recordings[0]['id']}"
    detail = client.delete(route).json()["detail"]
    response = client.delete(
        route, params={"operation_id": detail["operation_id"], "confirm_shared": True}
    )
    assert response.status_code == 200, response.text
    batch = runtime_store.get(store, owner.batch_id)
    batch["members"][1]["state"] = "completed"
    coordinator = object.__new__(batches.Coordinator)
    coordinator.store, coordinator.fresh = store, lambda: batch
    coordinator.started, coordinator.futures = set(), {}
    coordinator.launch(
        SimpleNamespace(submit=lambda *args: pytest.fail("Deleted file was launched"))
    )
    assert not store.all("SELECT id FROM jobs WHERE id=?", (job_id,))
    assert remote.trashed == []
    assert runtime_store.obligations(store)  # Surviving unsaved output remains recoverable.


def native_owner(client, *, run=None, state="completed"):
    store = client.app.state.store
    project = store.create_project("Native study")
    rows = [store.create_recording(project["id"], f"{name}.wav") for name in ("one", "two")]
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=copy.deepcopy(SETUP),
    )
    jobs = store.add_transcription_jobs(
        [
            {
                "recording_id": row["id"],
                "params": {"settings": settings.model_dump(mode="json"), "provider_name": "N"},
            }
            for row in rows
        ]
    )
    batch_id = jobs[0]["params"]["native_batch_id"]
    folder = SETUP["storage_path"] + "/cbs-batch-" + batch_id
    store.update_native_batch(
        batch_id,
        state=state,
        run_update={
            "job_id": "native-remote-job",
            "remote_state": "SUCCESS",
            "owned_folder": folder,
            "root_created": True,
            "folders_created": True,
            "cleanup_state": "pending",
            **(run or {}),
        },
    )
    for job in jobs:
        store.update_job(job["id"], state="failed")
    return project, rows, batch_id, folder


def test_native_batch_pending_cleanup_is_trashed_or_retained_with_retry(client, remote):
    store = client.app.state.store
    project, rows, batch_id, folder = native_owner(client)
    remote.trash_error = native.CleanupError("Synthetic refusal", "trash", "trash_refused")
    detail, response = confirmed_delete(client, f"/api/projects/{project['id']}")
    assert detail["remote_folders"][0]["owner_kind"] == "native_batch"
    assert detail["remote_folders"][0]["folder"] == folder
    assert response.status_code == 200, response.text
    assert not store.all("SELECT id FROM recordings WHERE project_id=?", (project["id"],))
    batch = store.native_batch(batch_id)
    assert native_batch.unresolved(batch)
    [card] = [
        job for job in client.get("/api/jobs").json() if job["id"] == "native-batch-" + batch_id
    ]
    assert card["recording_name"] == "UCloud cleanup for a deleted project"
    assert card["remote_release"]["can_retry"] and card["ucloud_cleanup_state"] == "pending"
    remote.trash_error = None
    response = client.post(f"/api/remote-releases/native_batch/{batch_id}/retry")
    assert response.status_code == 200, response.text
    assert remote.trashed == [folder]
    assert not native_batch.unresolved(store.native_batch(batch_id))
    assert not [job for job in client.get("/api/jobs").json() if job.get("remote_release")]


def test_restored_owner_is_untouched_by_delete(client, remote):
    store = client.app.state.store
    project = store.create_project("Restored study")
    owner = runtime_owner(store, project)
    for kind, identifier in (("runtime_batch", owner.batch_id), ("job", owner.jobs[0]["id"])):
        store.execute(
            "INSERT INTO restored_owners VALUES (?,?,?,?)", (kind, identifier, "r" * 32, "b" * 32)
        )
    before = store.all("SELECT * FROM runtime_batch_runs WHERE id=?", (owner.batch_id,))
    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    assert store.all("SELECT * FROM runtime_batch_runs WHERE id=?", (owner.batch_id,)) == before
    assert remote.trashed == [] and remote.terminated == []
    assert ("runtime_batch", owner.batch_id) not in remote_release.candidates(store)
    assert (
        remote_release.release_one(
            store, client.app.state.credentials, "runtime_batch", owner.batch_id
        )
        is None
    )
    assert "historical" in remote_release.note(store, "runtime_batch", owner.batch_id)
    assert remote.retrieved == [] and remote.trashed == []
    assert store.all("SELECT * FROM runtime_batch_runs WHERE id=?", (owner.batch_id,)) == before


def test_uncertain_submission_is_deleted_locally_but_stays_explicit(client, remote):
    store = client.app.state.store
    project = store.create_project("Uncertain study")
    owner = runtime_owner(
        store, project, journal={"job_id": None, "submission_intent": True, "remote_state": None}
    )
    _, response = confirmed_delete(client, f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    assert not store.all("SELECT id FROM projects WHERE id=?", (project["id"],))
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["cleanup_error_code"] == "submission_uncertain"
    [card] = [job for job in client.get("/api/jobs").json() if job.get("remote_release")]
    assert (
        card["remote_release"]["submission_uncertain"] and not card["remote_release"]["can_retry"]
    )
    for _ in range(2):
        remote_release.release_one(
            store, client.app.state.credentials, "runtime_batch", owner.batch_id
        )
    assert remote.trashed == [] and remote.submitted == []
    # The existing explicit absence confirmation settles it; the folder then moves to Trash.
    response = client.post(
        f"/api/remote-releases/runtime_batch/{owner.batch_id}/reconcile", json={"not_created": True}
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/api/remote-releases/runtime_batch/{owner.batch_id}/retry")
    assert response.json()["cleanup_state"] == "trashed"
    assert remote.trashed == [owner.folder] and remote.submitted == []
    assert not runtime_store.obligations(store)


def test_single_job_journals_still_keep_their_recording(client, wav_bytes, remote):
    from test_api import upload

    uploaded = upload(client, wav_bytes)
    store = client.app.state.store
    store.update_job(uploaded["job"]["id"], state="completed")
    job = store.add_job(
        uploaded["recording"]["id"], "enhance", {"ucloud_run": {"cleanup_state": "pending"}}
    )
    store.update_job(job["id"], state="failed")
    route = f"/api/recordings/{uploaded['recording']['id']}"
    preview = client.post(route + "/deletion-preview").json()
    assert preview["remote_blockers"] == [
        {"id": uploaded["recording"]["id"], "name": uploaded["recording"]["name"]}
    ]
    response = client.delete(route)
    assert response.status_code == 409 and "single UCloud job" in response.json()["detail"]
    assert store.recording(uploaded["recording"]["id"])


@pytest.mark.parametrize(
    "path",
    [
        "/api/remote-releases/runtime_batch/" + "a" * 32 + "/retry",
        "/api/projects/" + "a" * 32 + "/deletion-preview",
    ],
)
def test_release_routes_keep_same_origin_protection(client, path):
    response = client.post(path, headers={"Origin": "https://example.invalid"})
    assert response.status_code == 403
    response = client.post(path, headers={"X-CBS-Request": "0"})
    assert response.status_code == 403


# ---------- automatic release of old obligations ----------


def make_releasable(env):
    env.store.execute(
        "UPDATE runtime_batch_runs SET journal=json_set(journal,'$.cleanup_state','pending') "
        "WHERE id=?",
        (env.batch_id,),
    )


def test_nothing_unsaved_failed_batch_releases_automatically(retained):
    env = retained
    make_releasable(env)
    env.publish()  # Published inputs, a failed outcome and no stage output.
    state = remote_release.release_one(env.store, env.credentials, "runtime_batch", env.batch_id)
    assert state == "trashed", remote_release.note(env.store, "runtime_batch", env.batch_id)
    assert env.trashed == [env.remote_folder]
    journal = runtime_store.get(env.store, env.batch_id)["journal"]
    assert journal["discard_decision"]["decided_by"] == "automatic"
    assert not runtime_store.obligations(env.store)
    [card] = batches.activity(env.store)
    assert card["remote_release"]["kind"] == "automatic"


def test_partial_outputs_are_preserved_locally_before_automatic_release(retained):
    env = retained
    make_releasable(env)
    raw = b'{"segments":[{"text":"Synthetic partial."}]}'
    task, _ = env.publish(outputs={"transcription.json": raw})
    archive = env.store.root / "runtime-batches" / env.batch_id / "discard-evidence" / task
    assert (
        remote_release.release_one(env.store, env.credentials, "runtime_batch", env.batch_id)
        == "trashed"
    )
    assert (archive / "transcription.json").read_bytes() == raw
    assert env.trashed == [env.remote_folder]
    assert env.original.read_bytes() == b"immutable synthetic audio"


def test_complete_unsaved_result_is_not_released_automatically(retained):
    env = retained
    make_releasable(env)
    outputs = {
        name: b'{"segments":[]}'
        for name in runtime_store._required_stages(
            runtime_store.get(env.store, env.batch_id)["identity"]
        )
    }
    env.publish(state="complete", outputs=outputs)
    assert (
        remote_release.release_one(env.store, env.credentials, "runtime_batch", env.batch_id)
        is None
    )
    assert "Reconnect and save" in remote_release.note(env.store, "runtime_batch", env.batch_id)
    assert env.trashed == [] and runtime_store.obligations(env.store)


def test_uncertain_submission_stays_explicit_for_automatic_release(retained):
    env = retained
    env.store.execute(
        "UPDATE runtime_batch_runs SET journal=json_remove(json_set(journal,"
        "'$.submission_intent',1,'$.cleanup_state','pending'),'$.job_id') WHERE id=?",
        (env.batch_id,),
    )
    before = runtime_store.get(env.store, env.batch_id)
    assert (
        remote_release.release_one(env.store, env.credentials, "runtime_batch", env.batch_id)
        is None
    )
    assert "uncertain" in remote_release.note(env.store, "runtime_batch", env.batch_id)
    assert env.trashed == [] and runtime_store.obligations(env.store)
    assert runtime_store.get(env.store, env.batch_id) == before  # no journal churn


def test_startup_evaluation_releases_an_old_obligation_once_per_interval(retained):
    env = retained
    make_releasable(env)
    env.publish()
    moments = iter([1000.0, 1001.0])
    first = remote_release.tick(env.store, env.credentials, clock=lambda: next(moments))
    assert first == ("runtime_batch", env.batch_id, "trashed")
    assert env.trashed == [env.remote_folder] and not runtime_store.obligations(env.store)
    assert remote_release.tick(env.store, env.credentials, clock=lambda: next(moments)) is None


def test_clean_native_history_does_not_fill_the_automatic_scan_limit(client, monkeypatch):
    store = client.app.state.store
    _, _, cleaned, _ = native_owner(client, run={"cleanup_state": "trashed"})
    _, _, pending, _ = native_owner(client, run={"cleanup_state": "pending"})
    store.execute("UPDATE native_batch_runs SET updated=? WHERE id=?", ("2000-01-01", cleaned))
    monkeypatch.setattr(remote_release, "MAX_CANDIDATES", 1)
    assert remote_release.candidates(store) == [("native_batch", pending)]


def test_automatic_release_reserves_owner_against_http_retry(client, remote, monkeypatch):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Automatic release versus Retry")
    owner = runtime_owner(store, project)
    responses = []

    def release(*args):
        responses.append(client.post(f"/api/runtime-batches/{owner.batch_id}/retry"))
        return "checked"

    monkeypatch.setattr(remote_release, "release_one", release)
    result = remote_release.tick(store, queue.credentials, queue)
    assert result == ("runtime_batch", owner.batch_id, "checked")
    assert responses[0].status_code == 409
    assert not queue.control_job_ids
    assert remote.trashed == [] and remote.submitted == []


def test_runtime_lane_reevaluates_retained_obligations_at_startup(tmp_path, monkeypatch):
    from cbs_transcriber.store import Store

    monkeypatch.delenv("CBS_TRANSCRIBER_NO_REMOTE_RELEASE", raising=False)
    store = Store(tmp_path)
    called = threading.Event()
    seen = []

    def tick(store_arg, credentials, queue=None, **kwargs):
        seen.append((store_arg, queue))
        called.set()

    monkeypatch.setattr(remote_release, "tick", tick)
    queue = JobQueue(store, SimpleNamespace(get=lambda scope: ""))
    with store.library_lock():
        queue.start()
        try:
            assert called.wait(10), "The idle Runtime lane did not re-evaluate obligations."
        finally:
            queue.stop()
    assert seen[0] == (store, queue)
