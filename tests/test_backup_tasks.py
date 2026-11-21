"""Server-owned local backups in Activity (ADR 0192), synthetic libraries only.

Deterministic events hold the worker at exact capture seams; polling only observes. No
UCloud connection, transfer, real library or recording is used.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from admission_support import write_original_wav
from conftest import MemoryCredentials
from fastapi.testclient import TestClient
from test_project_deletion_tasks import hold_stop as hold_stop
from test_project_deletion_tasks import settled as deletion_settled

from cbs_transcriber import backup_tasks, portable_capture
from cbs_transcriber import portable_transport as transport
from cbs_transcriber.app import create_app

HEADERS = {"X-CBS-Request": "1"}
Owner = backup_tasks.BackupTasks


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("The synthetic backup did not reach the expected state")


def operation(seed="a"):
    return seed * 32


def start(client, op=None, destination=None):
    body = {"operation_id": op or operation()}
    if destination is not None:
        body["destination"] = str(destination)
    return client.post("/api/library/backup-tasks", json=body)


def task(client, identifier):
    response = client.get(f"/api/library/backup-tasks/{identifier}")
    assert response.status_code == 200, response.text
    return response.json()


def settled(client, identifier):
    return wait_for(lambda: (item := task(client, identifier))["state"] != "running" and item)


def library(client, name="Synthetic backup"):
    store = client.app.state.store
    project = store.create_project(name)
    recording = store.create_recording(project["id"], "Private interview.wav")
    write_original_wav(store, recording)
    return store, project, recording


def staging(item):
    return portable_capture.pending_path(item["destination"], item["backup_id"])


def note(store, identifier):
    return json.loads((store.root / "backup-tasks" / f"{identifier}.json").read_text("utf-8"))


class Hold:
    """Hold one seam until the test releases it (or the task is told to stop)."""

    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.task = None

    def hold(self, task=None):
        self.task = task
        self.entered.set()
        deadline = time.monotonic() + 15
        while not self.release.wait(0.02):
            if task is not None and task.closing:
                return
            assert time.monotonic() < deadline, "The test must release the synthetic hold"


@pytest.fixture
def hold_copy(monkeypatch):
    """Hold the worker after the first file has been copied into its staging folder."""
    gate = Hold()
    original = backup_tasks._Observer.copied_file

    def copied_file(self):
        original(self)
        if not gate.entered.is_set():
            gate.hold(self.task)

    monkeypatch.setattr(backup_tasks._Observer, "copied_file", copied_file)
    return gate


@pytest.fixture
def hold_latched(monkeypatch):
    """Hold the worker just after the publication boundary, before the creator reference."""
    gate = Hold()
    original = Owner._latch

    def latch(self, task):
        original(self, task)
        gate.hold()

    monkeypatch.setattr(Owner, "_latch", latch)
    return gate


@pytest.fixture
def hold_verify(monkeypatch):
    """Hold the worker when it starts checking the sealed copy, before the boundary."""
    gate = Hold()
    original = Owner._phase

    def phase(self, task, value):
        original(self, task, value)
        if value == "verify":
            gate.hold()

    monkeypatch.setattr(Owner, "_phase", phase)
    return gate


def test_ready_backup_is_published_checked_and_shown_in_activity(client):
    store, _, recording = library(client)
    response = start(client)
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["state"] == "running" and accepted["can_stop"] is True
    done = settled(client, accepted["id"])
    assert done["state"] == "ready" and done["progress"] == 1
    assert done["published"] is True and done["can_stop"] is False and done["can_dismiss"]
    receipt = done["receipt"]
    assert receipt["credentials"] == "excluded" and receipt["scope"] == "whole_library"
    folder = Path(receipt["folder"])
    assert folder == store.root / "backups" / ("portable-" + done["backup_id"])
    transport.inspect_transport(folder, expected_commit_sha256=receipt["commit_sha256"])
    assert not staging(done).exists()
    # Logical work counted every file actually read, then finished at exactly its total.
    assert done["files_total"] == done["files_done"] == receipt["files"] + 1
    assert done["bytes_total"] == done["bytes_done"] > 0
    listed = client.get("/api/library/portable-backups").json()["captures"]
    assert [item["commit_sha256"] for item in listed] == [receipt["commit_sha256"]]
    [card] = [job for job in client.get("/api/jobs").json() if job["kind"] == "backup"]
    assert card["id"] == "backup:" + done["id"] and card["state"] == "completed"
    assert card["progress"] == 1 and card["backup_task"]["receipt"]["folder"] == str(folder)
    # Activity carries counts and the user's own folder, never recording names or text.
    assert recording["filename"] not in json.dumps(card)
    saved = note(store, done["id"])
    assert saved["state"] == "ready" and saved["published"] is True
    # A capture never copies the task notes, so a restored library carries no task state.
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    assert not any("backup-tasks" in item["relative_path"] for item in manifest["files"])
    assert client.app.state.portable_capture_gate.captures == 0
    assert not client.app.state.queue.capture_paused


def test_exact_replay_returns_the_same_task_and_changed_bodies_are_refused(client, tmp_path):
    library(client)
    first = start(client).json()
    replay = start(client)
    assert replay.status_code == 202 and replay.json()["id"] == first["id"]
    changed = start(client, destination=tmp_path / "elsewhere")
    assert changed.status_code == 409 and "another folder" in changed.json()["detail"]
    retyped = client.post("/api/library/backup-tasks", json={"operation_id": 1})
    assert retyped.status_code == 422
    done = settled(client, first["id"])
    assert done["state"] == "ready"
    assert start(client).json()["id"] == first["id"]  # A late lost-reply replay.
    assert len(client.get("/api/library/portable-backups").json()["captures"]) == 1
    assert not (tmp_path / "elsewhere").exists()


def test_admission_refusal_creates_no_note_reservation_or_folder(client):
    store, _, recording = library(client)
    job = store.add_job(recording["id"], "media")
    store.execute("UPDATE jobs SET state='queued' WHERE id=?", (job["id"],))
    refused = start(client)
    assert refused.status_code == 409
    assert "Finish or cancel queued and running jobs" in refused.json()["detail"]
    assert not (store.root / "backup-tasks").exists()
    assert not (store.root / "backups").exists()
    assert client.app.state.portable_capture_gate.captures == 0
    store.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))
    assert settled(client, start(client).json()["id"])["state"] == "ready"


def test_stop_while_waiting_for_an_earlier_change_copies_nothing(client):
    store, _, _ = library(client)
    gate = client.app.state.portable_capture_gate
    gate.enter_mutation()  # An earlier, still running change that capture must wait for.
    replies = {}
    try:
        accepted = start(client).json()
        assert task(client, accepted["id"])["phase"] == "waiting"
        # Later changes now wait behind the accepted backup; reads continue.
        writer = threading.Thread(
            target=lambda: replies.update(
                put=client.put(
                    "/api/library/portable-retention",
                    json={"keep_last": 4, "minimum_age_days": 7, "holds": []},
                )
            )
        )
        writer.start()
        writer.join(0.3)
        assert writer.is_alive() and "put" not in replies
        assert client.get("/api/library/portable-storage").status_code == 200
        # Stop is an exact signal outside the capture gate: it never waits behind it.
        stopped = client.post(f"/api/library/backup-tasks/{accepted['id']}/stop")
        assert stopped.status_code in (200, 202), stopped.text
        done = settled(client, accepted["id"])
    finally:
        gate.leave_mutation()
    writer.join(10)
    assert replies["put"].status_code == 200
    assert done["state"] == "stopped" and "before anything was copied" in done["message"]
    assert done["kept_paths"] == [] and done["can_dismiss"]
    assert not (store.root / "backups").exists()
    assert client.get("/api/library/portable-backups").json() == {"captures": []}
    assert gate.captures == 0 and not gate.active


def test_stop_during_copy_removes_only_the_owned_staging(client, hold_copy):
    store, _, recording = library(client)
    original = store.directory(recording["id"]) / "original.wav"
    before = original.read_bytes()
    backups = store.root / "backups"
    backups.mkdir()
    foreign = portable_capture.pending_path(backups, "f" * 32)
    foreign.mkdir()
    (foreign / "user-notes.txt").write_text("not ours", encoding="utf-8")
    accepted = start(client).json()
    assert hold_copy.entered.wait(20)
    running = task(client, accepted["id"])
    assert running["phase"] == "copy" and running["files_done"] == 1
    assert 0 < running["progress"] < 1
    assert staging(running).is_dir()
    stop = client.post(f"/api/library/backup-tasks/{accepted['id']}/stop")
    assert stop.status_code == 202 and stop.json()["stop_requested"] is True
    assert stop.json()["can_stop"] is False
    hold_copy.release.set()
    done = settled(client, accepted["id"])
    assert done["state"] == "stopped" and "incomplete copy was removed" in done["message"]
    assert not staging(done).exists() and not done["kept_paths"]
    assert (foreign / "user-notes.txt").read_text(encoding="utf-8") == "not ours"
    assert original.read_bytes() == before
    assert not (store.root / "portable-captures").exists()
    assert not list(backups.glob("portable-*"))
    assert note(store, done["id"])["state"] == "stopped"
    assert client.app.state.portable_capture_gate.captures == 0


def test_second_backup_and_direct_capture_are_refused_while_one_runs(client, hold_copy):
    library(client)
    accepted = start(client).json()
    assert hold_copy.entered.wait(20)
    try:
        second = start(client, op=operation("b"))
        assert second.status_code == 409 and "already being made" in second.json()["detail"]
        direct = client.post("/api/library/portable-backups", json={})
        assert direct.status_code == 409
    finally:
        hold_copy.release.set()
    assert settled(client, accepted["id"])["state"] == "ready"
    assert start(client, op=operation("b")).status_code == 202


def test_stop_after_the_publication_boundary_is_refused_and_the_backup_finishes(
    client, hold_latched
):
    store, _, _ = library(client)
    accepted = start(client).json()
    assert hold_latched.entered.wait(20)
    try:
        latched = task(client, accepted["id"])
        assert latched["phase"] == "publishing" and latched["can_stop"] is False
        assert note(store, accepted["id"])["published"] is True  # Durable before the receipt.
        assert not (store.root / "portable-captures").exists()
        refused = client.post(f"/api/library/backup-tasks/{accepted['id']}/stop")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "backup_publishing"
    finally:
        hold_latched.release.set()
    done = settled(client, accepted["id"])
    assert done["state"] == "ready" and done["stop_requested"] is False


def test_stop_just_before_the_boundary_wins_and_publishes_nothing(client, hold_verify):
    store, _, _ = library(client)
    accepted = start(client).json()
    assert hold_verify.entered.wait(20)
    try:
        assert client.post(f"/api/library/backup-tasks/{accepted['id']}/stop").status_code == 202
    finally:
        hold_verify.release.set()
    done = settled(client, accepted["id"])
    assert done["state"] == "stopped" and done["published"] is False
    assert not (store.root / "portable-captures").exists()
    assert not list((store.root / "backups").iterdir())


def test_failure_before_publication_removes_staging_and_explains(client):
    store, _, recording = library(client)
    (store.directory(recording["id"]) / "original.wav").unlink()
    done = settled(client, start(client).json()["id"])
    assert done["state"] == "failed"
    assert done["message"].startswith("No backup was made: A complete portable backup requires")
    assert "incomplete copy was removed" in done["message"]
    assert not list((store.root / "backups").iterdir())
    [card] = [job for job in client.get("/api/jobs").json() if job["kind"] == "backup"]
    assert card["state"] == "failed"


def test_unexpected_staging_content_is_named_and_never_removed(client, hold_copy):
    store, _, _ = library(client)
    accepted = start(client).json()
    assert hold_copy.entered.wait(20)
    pending = staging(task(client, accepted["id"]))
    stray = pending / "someone-elses.txt"
    stray.write_text("keep me", encoding="utf-8")
    client.post(f"/api/library/backup-tasks/{accepted['id']}/stop")
    hold_copy.release.set()
    done = settled(client, accepted["id"])
    assert done["state"] == "stopped" and done["kept_paths"] == [str(pending)]
    assert str(pending) in done["message"] and done["can_remove_partial"] is True
    assert stray.read_text(encoding="utf-8") == "keep me"
    assert done["can_dismiss"] is False
    refused = client.post(f"/api/library/backup-tasks/{accepted['id']}/dismiss")
    assert refused.status_code == 409
    removal = client.post(f"/api/library/backup-tasks/{accepted['id']}/remove-partial")
    assert removal.status_code == 409 and stray.exists()
    stray.unlink()
    removed = client.post(f"/api/library/backup-tasks/{accepted['id']}/remove-partial")
    assert removed.status_code == 200, removed.text
    assert removed.json()["kept_paths"] == [] and not pending.exists()
    assert "incomplete copy was removed" in removed.json()["message"]
    assert client.post(f"/api/library/backup-tasks/{accepted['id']}/dismiss").json() == {"ok": True}
    assert not (store.root / "backup-tasks" / f"{accepted['id']}.json").exists()


def test_interrupted_publication_is_finished_only_by_check_again(client, monkeypatch):
    store, _, _ = library(client)
    rename = Path.rename
    failed = []

    def flaky(self, target):
        if self.name == "manifest.json" and not failed:
            failed.append(self)
            raise OSError("Synthetic interruption between publication steps")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    done = settled(client, start(client).json()["id"])
    assert failed and done["state"] == "attention" and done["published"] is True
    assert done["can_check"] and not done["can_dismiss"] and not done["can_remove_partial"]
    assert client.post(f"/api/library/backup-tasks/{done['id']}/dismiss").status_code == 409
    target = Path(done["destination"]) / ("portable-" + done["backup_id"])
    assert (target / "objects").is_dir() and (staging(done) / "manifest.json").is_file()
    checked = client.post(f"/api/library/backup-tasks/{done['id']}/check")
    assert checked.status_code == 200, checked.text
    result = checked.json()
    assert result["state"] == "ready" and result["receipt"]["folder"] == str(target)
    assert not staging(done).exists()
    transport.inspect_transport(target, expected_commit_sha256=result["receipt"]["commit_sha256"])


def test_restart_mid_capture_reports_the_owned_partial_and_never_resumes(tmp_path, monkeypatch):
    root = tmp_path / "library"
    first = create_app(root, start_worker=False, credentials=MemoryCredentials())
    gate = Hold()
    copied_file, abandon = backup_tasks._Observer.copied_file, Owner._abandon
    monkeypatch.setattr(
        backup_tasks._Observer,
        "copied_file",
        lambda self: gate.entered.is_set() or gate.hold(self.task),
    )
    # Process death: after the hold nothing else runs, so no cleanup or final note.
    monkeypatch.setattr(Owner, "_abandon", lambda self, task, error: None)
    with TestClient(first, headers=HEADERS) as client:
        library(client)
        accepted = start(client).json()
        assert gate.entered.wait(20)
        pending = staging(task(client, accepted["id"]))
        crashed = note(first.state.store, accepted["id"])
        assert crashed["state"] == "running" and crashed["phase"] == "copy"
        assert crashed["staging"] is not None
        client.post(f"/api/library/backup-tasks/{accepted['id']}/stop")
        gate.release.set()
        wait_for(lambda: not first.state.backup_tasks.tasks[accepted["id"]].thread.is_alive())
    monkeypatch.setattr(backup_tasks._Observer, "copied_file", copied_file)
    monkeypatch.setattr(Owner, "_abandon", abandon)
    # The earlier run's note still says running, exactly as a crashed process leaves it.
    (root / "backup-tasks" / f"{accepted['id']}.json").write_text(
        json.dumps(crashed), encoding="utf-8"
    )
    assert pending.is_dir()
    second = create_app(root, start_worker=False, credentials=MemoryCredentials())
    with TestClient(second, headers=HEADERS) as client:
        recovered = task(client, accepted["id"])
        assert recovered["state"] == "interrupted" and recovered["kept_paths"] == [str(pending)]
        assert "The app closed before this backup finished" in recovered["message"]
        assert recovered["can_remove_partial"] and not recovered["can_dismiss"]
        # The same request after a restart never recaptures the now-changed library.
        assert start(client).json()["id"] == accepted["id"]
        assert not (root / "portable-captures").exists()
        removed = client.post(f"/api/library/backup-tasks/{accepted['id']}/remove-partial")
        assert removed.status_code == 200 and not pending.exists()
        assert removed.json()["can_dismiss"] is True


def test_restart_after_publication_needs_an_explicit_check(tmp_path):
    root = tmp_path / "library"
    first = create_app(root, start_worker=False, credentials=MemoryCredentials())
    with TestClient(first, headers=HEADERS) as client:
        library(client)
        done = settled(client, start(client).json()["id"])
    path = root / "backup-tasks" / f"{done['id']}.json"
    record = json.loads(path.read_text("utf-8"))
    # As if the process died after the renames, before its final note.
    record.update(state="running", phase="publishing", receipt=None)
    path.write_text(json.dumps(record), encoding="utf-8")
    second = create_app(root, start_worker=False, credentials=MemoryCredentials())
    with TestClient(second, headers=HEADERS) as client:
        recovered = task(client, done["id"])
        assert recovered["state"] == "attention" and recovered["can_check"]
        assert "Check again" in recovered["message"]
        checked = client.post(f"/api/library/backup-tasks/{done['id']}/check").json()
        assert checked["state"] == "ready"
        assert checked["receipt"]["commit_sha256"] == done["receipt"]["commit_sha256"]


def test_publication_latch_without_a_receipt_is_reported_not_adopted(tmp_path):
    root = tmp_path / "library"
    first = create_app(root, start_worker=False, credentials=MemoryCredentials())
    with TestClient(first, headers=HEADERS) as client:
        library(client)
        done = settled(client, start(client).json()["id"])
    (root / "portable-captures" / f"{done['backup_id']}.json").unlink()
    path = root / "backup-tasks" / f"{done['id']}.json"
    record = json.loads(path.read_text("utf-8"))
    record.update(state="running", phase="publishing", receipt=None)
    path.write_text(json.dumps(record), encoding="utf-8")
    second = create_app(root, start_worker=False, credentials=MemoryCredentials())
    with TestClient(second, headers=HEADERS) as client:
        checked = client.post(f"/api/library/backup-tasks/{done['id']}/check").json()
        target = Path(done["receipt"]["folder"])
        assert checked["state"] == "failed" and checked["kept_paths"] == [str(target)]
        assert str(target) in checked["message"] and target.is_dir()
        assert client.post(f"/api/library/backup-tasks/{done['id']}/dismiss").status_code == 409


def test_shutdown_stops_an_unpublished_backup_and_cleans_its_staging(tmp_path, monkeypatch):
    root = tmp_path / "library"
    app = create_app(root, start_worker=False, credentials=MemoryCredentials())
    gate = Hold()
    original = backup_tasks._Observer.copied_file

    def copied_file(self):
        original(self)
        if not gate.entered.is_set():
            gate.hold(self.task)

    monkeypatch.setattr(backup_tasks._Observer, "copied_file", copied_file)
    with TestClient(app, headers=HEADERS) as client:
        library(client)
        accepted = start(client).json()
        assert gate.entered.wait(20)
        pending = staging(task(client, accepted["id"]))
        assert pending.is_dir()
    # Lifespan shutdown signalled the task and joined it under the lease.
    record = json.loads((root / "backup-tasks" / f"{accepted['id']}.json").read_text("utf-8"))
    assert record["state"] == "interrupted" and record["kept"] == []
    assert not pending.exists() and not (root / "portable-captures").exists()


def test_running_app_with_workers_borrows_the_serving_lease(tmp_path):
    app = create_app(tmp_path / "library", credentials=MemoryCredentials())
    with TestClient(app, headers=HEADERS) as client:
        assert app.state.store._lease_depth == 1
        library(client)
        done = settled(client, start(client).json()["id"])
        assert done["state"] == "ready"
        assert not app.state.queue.capture_paused


def test_stop_keeps_host_and_origin_checks(client, hold_copy):
    library(client)
    accepted = start(client).json()
    assert hold_copy.entered.wait(20)
    try:
        url = f"/api/library/backup-tasks/{accepted['id']}/stop"
        hostile = client.post(url, headers={"Origin": "https://evil.example"})
        assert hostile.status_code == 403
        assert TestClient(client.app).post(url).status_code == 403
        assert task(client, accepted["id"])["stop_requested"] is False
        assert client.post(f"/api/library/backup-tasks/{'0' * 32}/stop").status_code == 404
    finally:
        hold_copy.release.set()
    assert settled(client, accepted["id"])["state"] == "ready"


def test_unreadable_note_is_inert_and_dismissable(tmp_path):
    root = tmp_path / "library"
    folder = root / "backup-tasks"
    folder.mkdir(parents=True)
    identifier = "c" * 32
    (folder / f"{identifier}.json").write_text('{"format":"other"}', encoding="utf-8")
    app = create_app(root, start_worker=False, credentials=MemoryCredentials())
    with TestClient(app, headers=HEADERS) as client:
        card = task(client, identifier)
        assert card["state"] == "failed" and card["message"] == backup_tasks.UNREADABLE
        assert card["can_dismiss"] and not card["can_remove_partial"] and not card["can_check"]
        assert client.post(f"/api/library/backup-tasks/{identifier}/dismiss").status_code == 200
        assert not (folder / f"{identifier}.json").exists()


# ---------- Reciprocal admission with project deletion (B513, ADR 0190) ----------


def test_accepted_deletion_refuses_backup_admission_before_any_reservation(client, hold_stop):
    store = client.app.state.store
    project = store.create_project("Deleting first")
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview").json()
    deletion = client.post(
        f"/api/projects/{project['id']}/deletion-tasks",
        json={"operation_id": preview["operation_id"]},
    ).json()
    assert hold_stop.entered.wait(10)
    try:
        refused = start(client)
        assert refused.status_code == 409, refused.text
        assert "project deletion" in refused.json()["detail"]
        assert not (store.root / "backup-tasks").exists()
        assert client.app.state.portable_capture_gate.captures == 0
        assert client.app.state.project_deletions.captures == 0
    finally:
        # Keep remains reachable; no circular admission wait.
        client.post(
            f"/api/project-deletion-tasks/{deletion['id']}/keep",
            json={"project_id": project["id"]},
        )
        hold_stop.release.set()
    assert deletion_settled(client, deletion["id"])["state"] == "kept"
    assert settled(client, start(client).json()["id"])["state"] == "ready"


def test_accepted_backup_refuses_deletion_before_stop_even_while_waiting(client, hold_stop):
    store = client.app.state.store
    project = store.create_project("Backup first")
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview").json()
    gate = client.app.state.portable_capture_gate
    gate.enter_mutation()  # The accepted backup is still waiting for an earlier change.
    try:
        accepted = start(client).json()
        assert task(client, accepted["id"])["phase"] == "waiting"
        refused = client.app.state.project_deletions
        with pytest.raises(Exception) as error:
            refused.start(project["id"], operation_id=preview["operation_id"])
        assert getattr(error.value, "status_code", None) == 409
        assert not hold_stop.entered.is_set()  # Refused before any Stop.
        client.post(f"/api/library/backup-tasks/{accepted['id']}/stop")
        assert settled(client, accepted["id"])["state"] == "stopped"
    finally:
        gate.leave_mutation()
    # The backup's reservation was released exactly once; deletion is admitted again.
    task_summary, created = client.app.state.project_deletions.start(
        project["id"], operation_id=preview["operation_id"]
    )
    assert created
    assert hold_stop.entered.wait(10)
    client.app.state.project_deletions.keep(task_summary["id"], project["id"])
    hold_stop.release.set()
    assert deletion_settled(client, task_summary["id"])["state"] == "kept"


def test_backup_copying_refuses_deletion_and_releases_after_ready(client, hold_copy, hold_stop):
    store, project, _ = library(client)
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview").json()
    owner = client.app.state.project_deletions
    accepted = start(client).json()
    assert hold_copy.entered.wait(20)
    try:
        # HTTP mutations wait at the capture gate; direct admission shows the fence itself.
        with pytest.raises(Exception) as error:
            owner.start(project["id"], operation_id=preview["operation_id"])
        assert getattr(error.value, "status_code", None) == 409
        assert "backup" in str(getattr(error.value, "detail", ""))
        assert not hold_stop.entered.is_set()
    finally:
        hold_copy.release.set()
    assert settled(client, accepted["id"])["state"] == "ready"
    assert owner.captures == 0
