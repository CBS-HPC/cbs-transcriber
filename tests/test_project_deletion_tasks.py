"""Server-owned project deletion in Activity (ADR 0190, Codex scenarios A1-M1).

Synthetic libraries and in-memory UCloud doubles only: no live allocation, upload or Trash.
Deterministic events hold the executor at exact seams; polling only observes the outcome.
"""

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_delete_overrules import native_owner, runtime_owner
from test_delete_overrules import remote as remote
from test_delete_staging import assert_preserved, seed

from cbs_transcriber import (
    deletion_files,
    deletions,
    native_batch,
    project_deletion_tasks,
    runtime_store,
)
from cbs_transcriber.app import create_app
from cbs_transcriber.jobs import JobQueue

TaskOwner = project_deletion_tasks.ProjectDeletionTasks


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("The synthetic deletion did not reach the expected state")


def tasks(client):
    return client.app.state.project_deletions


def preview(client, project_id):
    response = client.post(f"/api/projects/{project_id}/deletion-preview")
    assert response.status_code == 200, response.text
    return response.json()


def start(client, project_id, *, shared=None, fingerprint=None, summary=None):
    summary = summary or preview(client, project_id)
    body = {"operation_id": summary["operation_id"]}
    if shared if shared is not None else summary["affected_recordings"]:
        body["confirm_shared"] = True
    trash = any(folder["action"] == "trash" for folder in summary["remote_folders"])
    if fingerprint or trash:
        body["fingerprint"] = fingerprint or summary["fingerprint"]
    return client.post(f"/api/projects/{project_id}/deletion-tasks", json=body)


def task_state(client, task_id):
    response = client.get(f"/api/project-deletion-tasks/{task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def settled(client, task_id):
    return wait_for(lambda: (task := task_state(client, task_id))["state"] != "running" and task)


def project_exists(store, project_id):
    return bool(store.all("SELECT id FROM projects WHERE id=?", (project_id,)))


class Gate:
    """Hold one seam until the test releases it; never relies on timing."""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def hold(self):
        self.entered.set()
        assert self.release.wait(10), "The test must release the synthetic gate"


@pytest.fixture
def hold_stop(monkeypatch):
    """Hold the executor right after it enters the stopping phase, before any Stop."""
    gate = Gate()
    original = TaskOwner._phase

    def phase(self, task, value):
        original(self, task, value)
        if value == "stopping":
            gate.hold()

    monkeypatch.setattr(TaskOwner, "_phase", phase)
    return gate


@pytest.fixture
def hold_writer(monkeypatch):
    """Hold the executor inside the Delete writer after staging, before its decision."""
    gate = Gate()
    original = deletions.Deletions._delete_rows

    def delete_rows(db, operation):
        original(db, operation)
        gate.hold()

    monkeypatch.setattr(deletions.Deletions, "_delete_rows", staticmethod(delete_rows))
    return gate


def fake_worker(job_id):
    """A worker still owning a cancelled job, as a real one does until it drains."""
    return SimpleNamespace(
        active_id=job_id,
        active_ids=[],
        active_batch_id=None,
        cancel_event=threading.Event(),
        wakeup=threading.Event(),
    )


def queued_job(store, recording_id):
    return store.add_job(recording_id, "transcribe", {"provider_name": "Synthetic"})


# ---------- Admission ----------


def test_a1_confirmed_delete_hands_off_and_finishes_without_further_requests(client, hold_stop):
    store = client.app.state.store
    fixture = seed(store)
    project_id = fixture["project"]["id"]
    response = start(client, project_id)
    assert response.status_code == 202, response.text
    task = response.json()
    assert task["state"] == "running" and task["can_keep"] is True
    assert task["project_inactive"] is True
    assert hold_stop.entered.wait(5)
    # The browser is gone: nothing else is sent. The receipt is already durable.
    receipt = store.root / "project-deletions" / (task["id"] + ".json")
    assert json.loads(receipt.read_text("utf-8"))["state"] == "running"
    [listed] = [row for row in client.get("/api/projects").json() if row["id"] == project_id]
    assert listed["deleting"] == {"task_id": task["id"], "phase": "stopping"}
    [card] = [job for job in client.get("/api/jobs").json() if job["kind"] == "project-delete"]
    assert card["id"] == "project-deletion:" + task["id"]
    assert card["state"] == "running" and card["message"] == "Stopping jobs…"
    hold_stop.release.set()
    done = settled(client, task["id"])
    assert done["state"] == "deleted" and done["committed"] is True
    assert not project_exists(store, project_id)
    assert all(not path.exists() for path in fixture["paths"])
    assert json.loads(receipt.read_text("utf-8"))["state"] == "deleted"
    [card] = [job for job in client.get("/api/jobs").json() if job["kind"] == "project-delete"]
    assert card["state"] == "completed" and card["message"] == "Project deleted."


def test_a2_lost_reply_replay_returns_same_task_and_changed_body_refuses(client, hold_stop):
    store = client.app.state.store
    project = store.create_project("Replay study")
    recording = store.create_recording(project["id"], "a.wav")
    queued_job(store, recording["id"])
    summary = preview(client, project["id"])
    first = start(client, project["id"], summary=summary)
    assert first.status_code == 202, first.text
    again = start(client, project["id"], summary=summary)
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    changed = client.post(
        f"/api/projects/{project['id']}/deletion-tasks",
        json={"operation_id": summary["operation_id"], "confirm_shared": True},
    )
    assert changed.status_code == 409, changed.text
    retyped = client.post(
        f"/api/projects/{project['id']}/deletion-tasks",
        json={"operation_id": summary["operation_id"], "confirm_shared": "yes"},
    )
    assert retyped.status_code == 422
    assert len(tasks(client).tasks) == 1
    assert len(list((store.root / "project-deletions").glob("*.json"))) == 1
    hold_stop.release.set()
    assert settled(client, first.json()["id"])["state"] == "deleted"


@pytest.mark.parametrize("kind", ["shared", "trash", "wrong_fingerprint"])
def test_a3_missing_consent_accepts_no_task_fence_stop_or_trash(client, remote, kind):
    store = client.app.state.store
    if kind == "shared":
        project, rows, batch_id, _ = native_owner(client, state="running")
        other = store.create_project("Other study")
        store.execute("UPDATE recordings SET project_id=? WHERE id=?", (other["id"], rows[1]["id"]))
        summary = preview(client, project["id"])
        assert summary["affected_recordings"]
        response = start(client, project["id"], shared=False, summary=summary)
        code = "confirm_shared_cancellation"
    else:
        project = store.create_project("Consent study")
        runtime_owner(store, project)
        summary = preview(client, project["id"])
        body = {"operation_id": summary["operation_id"]}
        if kind == "wrong_fingerprint":
            body["fingerprint"] = "0" * 64
        response = client.post(f"/api/projects/{project['id']}/deletion-tasks", json=body)
        code = "confirm_remote_cleanup"
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == code
    assert not tasks(client).tasks and not tasks(client).inactive
    assert not (store.root / "project-deletions").exists()
    assert project_exists(store, project["id"])
    assert remote.trashed == [] and remote.terminated == []
    assert not store.all("SELECT id FROM jobs WHERE state='cancelled'")


def test_a4_capacity_refuses_before_any_stop_and_other_projects_stay_usable(
    client, hold_stop, monkeypatch
):
    monkeypatch.setattr(project_deletion_tasks, "MAX_ACTIVE", 1)
    store = client.app.state.store
    first = store.create_project("First")
    second = store.create_project("Second")
    recording = store.create_recording(second["id"], "b.wav")
    job = queued_job(store, recording["id"])
    accepted = start(client, first["id"])
    assert accepted.status_code == 202, accepted.text
    refused = start(client, second["id"])
    assert refused.status_code == 409 and "Too many" in refused.json()["detail"]
    assert store.job(job["id"])["state"] == "queued"
    renamed = client.patch(f"/api/projects/{second['id']}", json={"name": "Still usable"})
    assert renamed.status_code == 200, renamed.text
    assert second["id"] not in tasks(client).inactive
    hold_stop.release.set()
    assert settled(client, accepted.json()["id"])["state"] == "deleted"


def test_a4_single_job_remote_blocker_refuses_before_any_stop(client, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Blocked study")
    recording = store.create_recording(project["id"], "a.wav")
    queued = queued_job(store, recording["id"])
    failed = store.add_job(recording["id"], "transcribe", {"provider_name": "Single"})
    store.update_job(failed["id"], state="failed")
    monkeypatch.setattr(
        deletions.remote_jobs, "unresolved_for", lambda store, job: job["id"] == failed["id"]
    )
    response = start(client, project["id"])
    assert response.status_code == 409 and "Nothing was stopped" in response.text
    assert store.job(queued["id"])["state"] == "queued"
    assert not tasks(client).inactive and project_exists(store, project["id"])


# ---------- Project isolation ----------


def test_p1_only_the_deleting_project_is_inactive(client, hold_stop, wav_bytes):
    store = client.app.state.store
    deleting = store.create_project("Deleting study")
    other = store.create_project("Other study")
    mine = store.create_recording(deleting["id"], "mine.wav")
    theirs = store.create_recording(other["id"], "theirs.wav")
    job = queued_job(store, mine["id"])
    collection = store.create_collection(deleting["id"], "Interviews")
    response = start(client, deleting["id"])
    assert response.status_code == 202, response.text
    assert hold_stop.entered.wait(5)

    def refused(response):
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "project_deleting"

    refused(client.patch(f"/api/projects/{deleting['id']}", json={"name": "Renamed"}))
    refused(
        client.patch(f"/api/projects/{deleting['id']}/notes", json={"text": "x", "revision": 0})
    )
    refused(client.patch(f"/api/recordings/{mine['id']}", json={"name": "Renamed"}))
    refused(client.patch(f"/api/collections/{collection['id']}", json={"name": "Renamed"}))
    refused(client.post(f"/api/jobs/{job['id']}/retry", json={}))
    refused(client.post(f"/api/projects/{deleting['id']}/deletion-preview"))
    refused(client.delete(f"/api/projects/{deleting['id']}"))
    refused(client.delete(f"/api/recordings/{mine['id']}"))
    upload = client.post(
        "/api/recordings",
        data={"project_id": deleting["id"]},
        files={"file": ("new.wav", wav_bytes, "audio/wav")},
    )
    assert upload.status_code == 409 and "being deleted" in upload.json()["detail"]
    transcribe = client.post("/api/transcriptions", json={"recording_ids": [mine["id"]]})
    assert transcribe.status_code in {400, 409}
    # Reads stay available, and another project is unaffected.
    assert client.get(f"/api/projects/{deleting['id']}/recordings").status_code == 200
    renamed = client.patch(f"/api/recordings/{theirs['id']}", json={"name": "Renamed"})
    assert renamed.status_code == 200, renamed.text
    uploaded = client.post(
        "/api/recordings",
        data={"project_id": other["id"]},
        files={"file": ("other.wav", wav_bytes, "audio/wav")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert store.recording(mine["id"])["name"] == mine["name"]
    hold_stop.release.set()
    assert settled(client, response.json()["id"])["state"] == "deleted"
    assert not tasks(client).inactive
    assert (
        client.patch(f"/api/recordings/{theirs['id']}", json={"name": "Again"}).status_code == 200
    )


@pytest.mark.parametrize("late", ["edit", "result"])
def test_p2_p3_late_write_or_producer_result_refuses_stale_delete_and_keeps_it(
    client, hold_stop, late
):
    store = client.app.state.store
    fixture = seed(store, count=1)
    project_id = fixture["project"]["id"]
    recording_id = fixture["recordings"][0]["id"]
    response = start(client, project_id)
    assert hold_stop.entered.wait(5)
    # A writer admitted before the fence, or accepted work, lands after it.
    if late == "edit":
        store.execute("UPDATE recordings SET name='Late edit' WHERE id=?", (recording_id,))
    else:
        from cbs_transcriber.transcripts import normalize_segments

        store.add_result(recording_id, normalize_segments([{"text": "Late."}]), {}, "Late", {})
    hold_stop.release.set()
    done = settled(client, response.json()["id"])
    assert done["state"] == "failed" and done["committed"] is False
    assert "changed" in done["message"]
    assert project_exists(store, project_id)
    assert not tasks(client).inactive
    assert store.recording(recording_id)["transcripts"]


# ---------- Keep project ----------


def test_k1_keep_before_the_first_stop_stops_nothing(client, hold_stop):
    store = client.app.state.store
    project = store.create_project("Keep early")
    recording = store.create_recording(project["id"], "a.wav")
    job = queued_job(store, recording["id"])
    task = start(client, project["id"]).json()
    assert hold_stop.entered.wait(5)
    kept = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project["id"]}
    )
    assert kept.status_code == 202, kept.text
    assert kept.json()["keep_requested"] is True and kept.json()["can_keep"] is False
    hold_stop.release.set()
    done = settled(client, task["id"])
    assert done["state"] == "kept" and done["project_inactive"] is False
    assert store.job(job["id"])["state"] == "queued"
    assert project_exists(store, project["id"])
    [card] = [row for row in client.get("/api/jobs").json() if row["kind"] == "project-delete"]
    assert card["state"] == "cancelled" and "kept" in card["message"]
    # Keep is idempotent for the same task and never restarts anything.
    again = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project["id"]}
    )
    assert again.status_code == 200 and again.json()["state"] == "kept"
    wrong = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": "0" * 32}
    )
    assert wrong.status_code == 404


def test_k2_keep_during_stop_dispatch_sends_no_further_controls(client, monkeypatch):
    store = client.app.state.store
    queue = client.app.state.queue
    project = store.create_project("Keep mid stop")
    jobs = [
        queued_job(store, store.create_recording(project["id"], f"{name}.wav")["id"])
        for name in ("a", "b", "c")
    ]
    gate, cancelled = Gate(), []
    original = queue.cancel

    def cancel(identifier, **kwargs):
        cancelled.append(identifier)
        result = original(identifier, **kwargs)
        if len(cancelled) == 1:
            gate.hold()
        return result

    monkeypatch.setattr(queue, "cancel", cancel)
    task = start(client, project["id"]).json()
    assert gate.entered.wait(5)
    response = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project["id"]}
    )
    assert response.status_code == 202, response.text
    gate.release.set()
    assert settled(client, task["id"])["state"] == "kept"
    assert len(cancelled) == 1
    states = sorted(store.job(job["id"])["state"] for job in jobs)
    # The accepted Stop stays stopped; nothing resumes and no other job is touched.
    assert states == ["cancelled", "queued", "queued"]
    assert project_exists(store, project["id"])


def test_k3_k5_keep_during_staging_reaches_task_and_restores_every_file(
    client, remote, hold_writer
):
    store = client.app.state.store
    project = store.create_project("Keep while staging")
    owner = runtime_owner(store, project, 2)
    files = {
        path: path.read_bytes()
        for recording in owner.recordings
        for path in store.directory(recording["id"]).rglob("*")
        if path.is_file()
    }
    other = store.create_project("Ordinary edit")
    task = start(client, project["id"]).json()
    assert hold_writer.entered.wait(10)
    # Every directory is staged, release receipts are written but not committed.
    assert all(not store.directory(r["id"]).exists() for r in owner.recordings)
    # Ordinary writes stay fenced while files are staged (the existing global block)...
    ordinary = client.patch(f"/api/projects/{other['id']}", json={"name": "Later"})
    assert ordinary.status_code == 409, ordinary.text
    # ...but the exact Keep signal passes both staging gates without waiting for them.
    keep = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project["id"]}
    )
    assert keep.status_code == 202, keep.text
    hold_writer.release.set()
    done = settled(client, task["id"])
    assert done["state"] == "kept" and done["committed"] is False
    assert project_exists(store, project["id"])
    for path, data in files.items():
        assert path.read_bytes() == data
    journal = runtime_store.get(store, owner.batch_id)["journal"]
    assert "release_decision" not in journal and "deleted_members" not in journal
    assert remote.trashed == [] and remote.submitted == []
    assert not list((store.root / "local-deletions").glob("*"))


@pytest.mark.parametrize("winner", ["keep", "commit"])
def test_k4_keep_and_commit_have_exactly_one_winner(client, monkeypatch, winner):
    store = client.app.state.store
    fixture = seed(store, count=1)
    project_id = fixture["project"]["id"]
    gate = Gate()
    original = TaskOwner._decide

    def decide(self, task, *, final):
        if final and winner == "keep":
            gate.hold()  # Keep arrives before the decision lock is taken.
        original(self, task, final=final)
        if final and winner == "commit":
            gate.hold()  # The decision is taken; Keep arrives before the COMMIT.

    monkeypatch.setattr(TaskOwner, "_decide", decide)
    task = start(client, project_id).json()
    assert gate.entered.wait(10)
    response = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project_id}
    )
    gate.release.set()
    done = settled(client, task["id"])
    if winner == "keep":
        assert response.status_code == 202, response.text
        assert done["state"] == "kept" and project_exists(store, project_id)
        assert_preserved(store, fixture)
    else:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "deletion_committed"
        assert done["state"] == "deleted" and not project_exists(store, project_id)


@pytest.mark.parametrize("committed", [True, False])
def test_k6_uncertain_commit_is_classified_from_fresh_database_facts(
    client, monkeypatch, committed
):
    store = client.app.state.store
    fixture = seed(store)
    project_id = fixture["project"]["id"]
    connection = store.connection
    injected = []

    @contextmanager
    def uncertain():
        with connection() as db:
            yield db
            gone = not db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if gone and db.total_changes and not injected and not committed:
                injected.append(True)
                db.rollback()
                raise OSError("Synthetic COMMIT failure before success")
        if committed and not injected and not project_exists_raw(connection, project_id):
            injected.append(True)
            raise OSError("Synthetic lost COMMIT reply after success")

    monkeypatch.setattr(store, "connection", uncertain)
    task = start(client, project_id).json()
    done = settled(client, task["id"])
    monkeypatch.setattr(store, "connection", connection)
    assert injected
    if committed:
        assert done["state"] == "deleted" and done["committed"] is True
        assert not project_exists(store, project_id)
    else:
        assert done["state"] == "failed" and done["committed"] is False
        assert_preserved(store, fixture)
    assert not tasks(client).inactive


def project_exists_raw(connection, project_id):
    with connection() as db:
        return bool(db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone())


# ---------- Drain ----------


def test_d1_no_staging_until_every_worker_owner_drains(client):
    store = client.app.state.store
    queue = client.app.state.queue
    project = store.create_project("Drain study")
    recording = store.create_recording(project["id"], "a.wav")
    job = queued_job(store, recording["id"])
    worker = fake_worker(job["id"])
    queue.workers.append(worker)
    try:
        task = start(client, project["id"]).json()
        wait_for(lambda: store.job(job["id"])["state"] == "cancelled")
        time.sleep(0.5)
        observed = task_state(client, task["id"])
        assert observed["phase"] == "stopping", observed["message"]
        assert project_exists(store, project["id"])
        worker.active_id = None
        assert settled(client, task["id"])["state"] == "deleted"
    finally:
        queue.workers.remove(worker)


def test_d2_explicit_retry_of_a_captured_job_invalidates_the_task(client):
    store = client.app.state.store
    queue = client.app.state.queue
    project = store.create_project("Retry study")
    recording = store.create_recording(project["id"], "a.wav")
    job = queued_job(store, recording["id"])
    worker = fake_worker(job["id"])
    queue.workers.append(worker)
    try:
        task = start(client, project["id"]).json()
        wait_for(lambda: store.job(job["id"])["state"] == "cancelled")
        # An outsider's explicit Retry of shared work revokes the old consent (ADR 0092).
        client.app.state.deletions.invalidate_jobs([job["id"]])
        worker.active_id = None
        done = settled(client, task["id"])
    finally:
        queue.workers.remove(worker)
    assert done["state"] == "failed" and project_exists(store, project["id"])


# ---------- Owner receipts ----------


def test_r1_release_receipts_roll_back_with_a_failed_delete(client, remote, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Atomic receipt")
    owner = runtime_owner(store, project, 2)
    _, rows, batch_id, _ = native_owner(client)
    store.execute(
        "UPDATE recordings SET project_id=? WHERE id IN (?,?)",
        (project["id"], rows[0]["id"], rows[1]["id"]),
    )
    original = deletions.Deletions._delete_rows

    def fail_after_receipts(db, operation):
        journal = json.loads(
            db.execute(
                "SELECT journal FROM runtime_batch_runs WHERE id=?", (owner.batch_id,)
            ).fetchone()[0]
        )
        assert journal["release_decision"]["kind"] == "deletion"
        original(db, operation)
        raise OSError("Synthetic failure after receipts, before the Delete commit")

    monkeypatch.setattr(deletions.Deletions, "_delete_rows", staticmethod(fail_after_receipts))
    task = start(client, project["id"]).json()
    done = settled(client, task["id"])
    assert done["state"] == "failed" and project_exists(store, project["id"])
    journal = runtime_store.get(store, owner.batch_id)["journal"]
    assert "release_decision" not in journal and journal.get("cleanup_state") == "pending"
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert "release_decision" not in run and "deleted_members" not in run
    assert remote.trashed == [] and remote.terminated == []


def test_r2_remote_failure_after_commit_keeps_local_delete_and_cleanup_card(client, remote):
    import httpx

    store = client.app.state.store
    project = store.create_project("Offline trash")
    owner = runtime_owner(store, project, 2)
    remote.trash_error = httpx.ConnectError("offline")
    task = start(client, project["id"]).json()
    done = settled(client, task["id"])
    assert done["state"] == "deleted" and done["remote_cleanup_pending"] is True
    assert not project_exists(store, project["id"])
    [retained] = runtime_store.obligations(store)
    assert retained["id"] == owner.batch_id
    assert retained["journal"]["release_decision"]["target"]["name"] == "Offline trash"
    cards = client.get("/api/jobs").json()
    assert any(card.get("remote_release") for card in cards)
    assert remote.submitted == []


def test_r2_trash_after_commit_with_release_receipt(client, remote):
    store = client.app.state.store
    project = store.create_project("Failed batch study")
    owner = runtime_owner(store, project, 3)
    task = start(client, project["id"]).json()
    done = settled(client, task["id"])
    assert done["state"] == "deleted" and done["cleanup_pending"] is False
    assert remote.trashed == [owner.folder] and remote.submitted == []
    batch = runtime_store.get(store, owner.batch_id)
    assert batch["journal"]["cleanup_state"] == "trashed"
    assert sorted(batch["journal"]["deleted_members"]) == sorted(j["id"] for j in owner.jobs)


def test_r3_survivor_in_another_project_keeps_folder_with_member_receipt(client, remote):
    store = client.app.state.store
    project, rows, batch_id, folder = native_owner(client, state="failed")
    other = store.create_project("Survivor study")
    store.execute("UPDATE recordings SET project_id=? WHERE id=?", (other["id"], rows[1]["id"]))
    summary = preview(client, project["id"])
    assert [entry["action"] for entry in summary["remote_folders"]] == ["retain"]
    task = start(client, project["id"], summary=summary).json()
    done = settled(client, task["id"])
    assert done["state"] == "deleted"
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert "release_decision" not in run
    deleted = next(
        m["job_id"]
        for m in store.native_batch(batch_id)["members"]
        if m["recording_id"] == rows[0]["id"]
    )
    assert run["deleted_members"] == [deleted]
    assert native_batch.deleted_members(store, store.native_batch(batch_id)) == {deleted}
    assert remote.trashed == [] and store.recording(rows[1]["id"])


# ---------- Files ----------


def test_f1_stage_rollback_keeps_live_rows_and_restores_files(client, monkeypatch, tmp_path):
    store = client.app.state.store
    fixture = seed(store, parent=tmp_path / "chosen")
    denied = fixture["paths"][-1]
    rename = deletion_files._rename

    def fail_later(path, destination):
        if path == denied:
            raise PermissionError("Synthetic sharing violation")
        return rename(path, destination)

    monkeypatch.setattr(deletion_files, "_rename", fail_later)
    task = start(client, fixture["project"]["id"]).json()
    done = settled(client, task["id"])
    assert done["state"] == "failed" and "Close apps" in done["message"]
    assert_preserved(store, fixture)
    assert not tasks(client).inactive


def test_f2_denied_postcommit_cleanup_keeps_local_cleanup_card(client, monkeypatch):
    store = client.app.state.store
    fixture = seed(store)

    def denied(path, *args, **kwargs):
        raise PermissionError("Synthetic Windows reader holds the folder")

    rmtree = deletion_files.shutil.rmtree
    monkeypatch.setattr(deletion_files.shutil, "rmtree", denied)
    task = start(client, fixture["project"]["id"]).json()
    done = settled(client, task["id"])
    monkeypatch.setattr(deletion_files.shutil, "rmtree", rmtree)  # shutil is global
    assert done["state"] == "deleted" and done["local_cleanup_pending"] is True
    assert not project_exists(store, fixture["project"]["id"])
    cards = client.get("/api/jobs").json()
    assert any(card.get("local_deletion") for card in cards)
    assert "Local file cleanup" in done["message"]


def test_v2_empty_project_is_deleted_by_its_project_row(client):
    store = client.app.state.store
    project = store.create_project("Empty study")
    task = start(client, project["id"]).json()
    assert settled(client, task["id"])["state"] == "deleted"
    assert not project_exists(store, project["id"])


# ---------- Shutdown and restart ----------


@pytest.mark.parametrize("phase", ["stopping", "draining", "staging"])
def test_s1_closing_before_the_point_of_no_return_keeps_the_project(
    tmp_path, credentials, monkeypatch, phase
):
    app = create_app(tmp_path, start_worker=False, credentials=credentials)
    gate = Gate()
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        store = app.state.store
        fixture = seed(store)
        project_id = fixture["project"]["id"]
        recording = fixture["recordings"][0]["id"]
        job = queued_job(store, recording)
        worker = None
        if phase == "stopping":
            original = TaskOwner._phase

            def phase_hold(self, task, value):
                original(self, task, value)
                if value == "stopping":
                    gate.hold()

            monkeypatch.setattr(TaskOwner, "_phase", phase_hold)
        elif phase == "draining":
            worker = fake_worker(job["id"])
            app.state.queue.workers.append(worker)
            gate.entered.set()
        else:
            original_rows = deletions.Deletions._delete_rows

            def rows_hold(db, operation):
                original_rows(db, operation)
                gate.hold()

            monkeypatch.setattr(deletions.Deletions, "_delete_rows", staticmethod(rows_hold))
        task = start(client, project_id).json()
        assert gate.entered.wait(10)
        if phase == "draining":
            wait_for(lambda: store.job(job["id"])["state"] == "cancelled")

        def release_after_close_signal():
            wait_for(lambda: all(t.closing for t in app.state.project_deletions.tasks.values()))
            gate.release.set()

        threading.Thread(target=release_after_close_signal, daemon=True).start()
    # The lifespan closed: the deletion was settled under the lease, never left running.
    assert_preserved(store, fixture)
    receipt = json.loads((store.root / "project-deletions" / (task["id"] + ".json")).read_text())
    assert receipt["state"] == "interrupted" and receipt["committed"] is False
    with TestClient(
        create_app(tmp_path, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as reopened:
        [card] = [r for r in reopened.get("/api/jobs").json() if r["kind"] == "project-delete"]
        assert card["state"] == "interrupted" and "closed" in card["message"]
        assert card["project_deletion"]["can_keep"] is False
        [listed] = [p for p in reopened.get("/api/projects").json() if p["id"] == project_id]
        assert listed["deleting"] is None
        # Nothing resumes: the project stays editable and its stopped job stays stopped.
        response = reopened.patch(f"/api/recordings/{recording}", json={"name": "Kept"})
        assert response.status_code == 200, response.text
        assert reopened.app.state.store.job(job["id"])["state"] == (
            "queued" if phase == "stopping" else "cancelled"
        )
        dismissed = reopened.post(f"/api/project-deletion-tasks/{task['id']}/dismiss")
        assert dismissed.status_code == 200
        assert not list((store.root / "project-deletions").glob("*.json"))


def write_receipt(root, project_id, *, phase, state="running", committed=False):
    identifier = "f" * 32
    folder = root / "project-deletions"
    folder.mkdir(exist_ok=True)
    record = {
        "format": project_deletion_tasks.FORMAT,
        "id": identifier,
        "project_id": project_id,
        "project_name": "Crashed study",
        "operation_sha256": "0" * 64,
        "request_sha256": "1" * 64,
        "state": state,
        "phase": phase,
        "revision": 3,
        "committed": committed,
        "message": "",
        "local_cleanup": False,
        "remote_pending": False,
        "created": "2026-11-20T10:00:00+00:00",
        "updated": "2026-11-20T10:00:01+00:00",
    }
    (folder / (identifier + ".json")).write_text(json.dumps(record), encoding="utf-8")
    return identifier


@pytest.mark.parametrize("live", [True, False])
def test_s2_crash_receipt_is_settled_from_database_facts_and_never_executes(
    tmp_path, credentials, live
):
    with TestClient(
        create_app(tmp_path, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as first:
        store = first.app.state.store
        fixture = seed(store, count=1)
        job = queued_job(store, fixture["recordings"][0]["id"])
        project_id = fixture["project"]["id"]
        if not live:
            store.execute("DELETE FROM projects WHERE id=?", (project_id,))
    identifier = write_receipt(
        tmp_path, project_id, phase="removing_library" if live else "removing_files"
    )
    with TestClient(
        create_app(tmp_path, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as client:
        [card] = [r for r in client.get("/api/jobs").json() if r["kind"] == "project-delete"]
        assert card["id"] == "project-deletion:" + identifier
        assert card["state"] == ("interrupted" if live else "completed")
        assert not client.app.state.project_deletions.inactive
        if live:
            assert_preserved(client.app.state.store, fixture)
            assert client.app.state.store.job(job["id"])["state"] == "queued"
            assert client.post(f"/api/projects/{project_id}/deletion-preview").status_code == 200
    receipt = json.loads((tmp_path / "project-deletions" / (identifier + ".json")).read_text())
    assert receipt["state"] == ("interrupted" if live else "deleted")


def test_s3_m1_copied_or_corrupt_receipts_are_inert(tmp_path, credentials):
    with TestClient(
        create_app(tmp_path, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as first:
        store = first.app.state.store
        project = store.create_project("Restored study")
    folder = tmp_path / "project-deletions"
    write_receipt(tmp_path, project["id"], phase="removing_library", state="deleted")
    (folder / ("a" * 32 + ".json")).write_text("{not json", encoding="utf-8")
    (folder / ("b" * 32 + ".json")).write_bytes(b" " * (64 * 1024))
    with TestClient(
        create_app(tmp_path, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as client:
        cards = [r for r in client.get("/api/jobs").json() if r["kind"] == "project-delete"]
        assert {card["state"] for card in cards} == {"completed", "failed"}
        unreadable = [c for c in cards if c["recording_name"] == "Unreadable deletion note"]
        assert len(unreadable) == 2
        assert not client.app.state.project_deletions.inactive
        # The copied "deleted" note grants nothing: the live project is untouched.
        assert project_exists(client.app.state.store, project["id"])
        renamed = client.patch(f"/api/projects/{project['id']}", json={"name": "Still mine"})
        assert renamed.status_code == 200, renamed.text
        for card in unreadable:
            identifier = card["project_deletion"]["id"]
            assert (
                client.post(f"/api/project-deletion-tasks/{identifier}/dismiss").status_code == 200
            )


def test_v1_running_card_cannot_be_dismissed_and_clear_keeps_it(client, hold_stop):
    store = client.app.state.store
    project = store.create_project("Visible study")
    task = start(client, project["id"]).json()
    assert hold_stop.entered.wait(5)
    assert client.post(f"/api/project-deletion-tasks/{task['id']}/dismiss").status_code == 409
    assert client.post("/api/activity/clear").status_code == 200
    assert any(r["kind"] == "project-delete" for r in client.get("/api/jobs").json())
    hold_stop.release.set()
    settled(client, task["id"])
    assert client.post(f"/api/project-deletion-tasks/{task['id']}/dismiss").status_code == 200
    assert not any(r["kind"] == "project-delete" for r in client.get("/api/jobs").json())


def test_l1_production_lease_is_borrowed_without_reentering_the_lifespan_lock(
    tmp_path, credentials, monkeypatch
):
    monkeypatch.setattr(JobQueue, "start", lambda queue: None)
    monkeypatch.setattr(JobQueue, "stop", lambda queue: None)
    app = create_app(tmp_path, start_worker=True, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        fixture = seed(app.state.store)
        task = start(client, fixture["project"]["id"]).json()
        assert settled(client, task["id"])["state"] == "deleted"
        assert all(not Path(path).exists() for path in fixture["paths"])


def test_legacy_delete_cannot_drive_a_task_owned_operation(client, hold_stop):
    store = client.app.state.store
    project = store.create_project("Legacy client")
    summary = preview(client, project["id"])
    task = start(client, project["id"], summary=summary).json()
    assert hold_stop.entered.wait(5)
    # Old continuations never act on an operation a task accepted.
    with client.app.state.queue.lock:
        operation = client.app.state.deletions.operations[summary["operation_id"]]
    assert operation.task == task["id"]
    tasks(client).inactive.clear()  # Bypass the fence to reach the legacy owner itself.
    response = client.delete(
        f"/api/projects/{project['id']}", params={"operation_id": summary["operation_id"]}
    )
    assert response.status_code == 409 and "Activity" in response.json()["detail"]
    tasks(client).inactive[project["id"]] = task["id"]
    hold_stop.release.set()
    assert settled(client, task["id"])["state"] == "deleted"


def test_r4_single_job_blocker_after_stop_keeps_the_project(client, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Single job study")
    recording = store.create_recording(project["id"], "a.wav")
    job = queued_job(store, recording["id"])
    # The legacy single-job journal cascades with its row, so it can never be deleted away.
    monkeypatch.setattr(
        deletions.remote_jobs,
        "unresolved_for",
        lambda store, row: row["id"] == job["id"] and row["state"] == "cancelled",
    )
    task = start(client, project["id"]).json()
    done = settled(client, task["id"])
    assert done["state"] == "failed" and "single UCloud job" in done["message"]
    assert project_exists(store, project["id"])
    assert store.job(job["id"])["state"] == "cancelled"  # Accepted Stops stay stopped.
    assert not tasks(client).inactive


def test_staging_refusal_waits_visibly_and_keep_still_wins(client, monkeypatch):
    from cbs_transcriber.export_tasks import Conflict

    store = client.app.state.store
    fixture = seed(store, count=1)
    project_id = fixture["project"]["id"]
    exports = client.app.state.export_tasks

    def busy(**kwargs):
        raise Conflict("Wait for or cancel export preparation in Activity first.")

    monkeypatch.setattr(exports, "require_idle", busy)
    task = start(client, project_id).json()
    waiting = wait_for(
        lambda: (
            (row := task_state(client, task["id"]))["message"].startswith("Wait for or cancel")
            and row
        )
    )
    assert waiting["state"] == "running" and waiting["phase"] == "removing_library"
    assert waiting["can_keep"] is True
    response = client.post(
        f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project_id}
    )
    assert response.status_code in {200, 202}, response.text
    assert settled(client, task["id"])["state"] == "kept"
    assert_preserved(store, fixture)


def test_d3_reserved_control_epoch_change_refuses_admission(client, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Reserved control study")
    recording = store.create_recording(project["id"], "a.wav")
    job = queued_job(store, recording["id"])
    epoch = [1]
    # A source-inspection or input-intent job's generation/epoch authority, as captured.
    monkeypatch.setattr(
        deletions.Deletions, "_control", lambda self, row: ("intent", "g", epoch[0], False)
    )
    summary = preview(client, project["id"])
    epoch[0] = 2  # Its own authority moved on (e.g. an explicit Prepare) after the preview.
    response = start(client, project["id"], summary=summary)
    assert response.status_code == 409 and "changed" in response.json()["detail"]
    assert not tasks(client).tasks and not tasks(client).inactive
    assert store.job(job["id"])["state"] == "queued"
