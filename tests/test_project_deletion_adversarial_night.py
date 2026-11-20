"""Independent ADR 0190 interleaving and lost-reply probes, all synthetic."""

import threading
from contextlib import contextmanager
from pathlib import PurePosixPath

import httpx
import pytest
from fastapi import HTTPException
from test_delete_overrules import native_owner, runtime_owner
from test_delete_overrules import remote as remote
from test_project_deletion_tasks import hold_stop as hold_stop
from test_project_deletion_tasks import project_exists, settled, start

from cbs_transcriber import portable_routes, project_deletion_tasks, runtime_store
from cbs_transcriber import ucloud_native as native

_REAL_TRASH = native.trash_owned_folder


@pytest.mark.parametrize("kind", ["runtime_batch", "native_batch"])
def test_r5_lost_trash_reply_preserves_committed_delete_and_exact_retry(
    client, remote, monkeypatch, kind
):
    store = client.app.state.store
    if kind == "runtime_batch":
        project = store.create_project("Lost cleanup reply")
        owner = runtime_owner(store, project, 2)
        identifier, folder = owner.batch_id, owner.folder
    else:
        project, _, identifier, folder = native_owner(client, state="failed")
    requests, listings, moved = [], [], set()
    decision = None

    def request(method, path, *, json):
        nonlocal decision
        assert not project_exists(store, project["id"])
        batch = (
            runtime_store.get(store, identifier)
            if kind == "runtime_batch"
            else store.native_batch(identifier)
        )
        journal = batch["journal"] if kind == "runtime_batch" else batch["params"]["ucloud_run"]
        if decision is None:
            decision = journal["release_decision"].copy()
        assert journal["release_decision"] == decision
        assert method == "POST" and path == "/api/files/trash"
        assert json == {"items": [{"id": folder}]}
        requests.append(folder)
        if folder not in moved:
            moved.add(folder)
            raise httpx.ConnectError("Synthetic lost reply after the remote move")
        raise native.UCloudAPIError("Synthetic already absent", status_code=400)

    def browse(parent, *, items_per_page, next_page):
        assert parent == str(PurePosixPath(folder).parent)
        assert items_per_page == 250 and next_page is None
        listings.append(parent)
        return {"items": [{"id": parent + "/unrelated-sibling"}], "next": None}

    remote.request, remote.browse_files = request, browse
    monkeypatch.setattr(
        native, "trash_owned_folder", lambda c, f: _REAL_TRASH(c, f, wait=lambda _: None)
    )
    task = start(client, project["id"]).json()
    done = settled(client, task["id"])
    assert done["state"] == "deleted" and done["committed"] is True
    assert done["remote_cleanup_pending"] is True
    assert moved == {folder} and requests == [folder] and listings == []
    assert any(card.get("remote_release") for card in client.get("/api/jobs").json())
    response = client.post(f"/api/remote-releases/{kind}/{identifier}/retry")
    assert response.status_code == 200, response.text
    assert response.json()["cleanup_state"] == "trashed"
    assert requests == [folder, folder]
    assert listings == [str(PurePosixPath(folder).parent)]
    batch = (
        runtime_store.get(store, identifier)
        if kind == "runtime_batch"
        else store.native_batch(identifier)
    )
    journal = batch["journal"] if kind == "runtime_batch" else batch["params"]["ucloud_run"]
    assert journal["release_decision"] == decision
    assert journal["cleanup_state"] == "trashed"
    assert not project_exists(store, project["id"])
    assert remote.submitted == [] and remote.terminated == []


def test_backup_capture_refuses_an_accepted_deletion_before_any_file_journal(
    client, hold_stop, monkeypatch, tmp_path
):
    store = client.app.state.store
    project = store.create_project("Waiting deletion")
    task = start(client, project["id"]).json()
    assert hold_stop.entered.wait(10)
    entered = []

    def capture(*args, **kwargs):
        entered.append(True)
        return {"id": "synthetic-capture"}

    monkeypatch.setattr(portable_routes, "capture_library", capture)
    try:
        response = client.post(
            "/api/library/portable-backups", json={"destination": str(tmp_path / "backup")}
        )
        assert response.status_code == 409, (response.status_code, response.text)
        assert entered == []
    finally:
        client.post(
            f"/api/project-deletion-tasks/{task['id']}/keep", json={"project_id": project["id"]}
        )
        hold_stop.release.set()
        assert settled(client, task["id"])["state"] == "kept"


def test_deletion_admission_refuses_while_existing_capture_is_reserved(
    client, hold_stop, monkeypatch, tmp_path
):
    store = client.app.state.store
    project = store.create_project("Capture admission")
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview").json()
    entered, release = threading.Event(), threading.Event()
    replies, errors = [], []
    accepted = None

    def capture(*args, **kwargs):
        entered.set()
        assert release.wait(10), "Release the synthetic capture"
        return {"id": "synthetic-capture"}

    def call_capture():
        try:
            replies.append(
                client.post(
                    "/api/library/portable-backups", json={"destination": str(tmp_path / "backup")}
                )
            )
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(portable_routes, "capture_library", capture)
    thread = threading.Thread(target=call_capture, daemon=True)
    thread.start()
    assert entered.wait(10)
    try:
        try:
            accepted, _ = client.app.state.project_deletions.start(
                project["id"], operation_id=preview["operation_id"]
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Deletion was accepted while capture held its guard")
        assert not hold_stop.entered.is_set()
    finally:
        if accepted is not None:
            client.app.state.project_deletions.keep(accepted["id"], project["id"])
        hold_stop.release.set()
        release.set()
        thread.join(10)
        assert not thread.is_alive() and not errors
        assert replies and replies[0].status_code == 201
        if accepted is not None:
            assert settled(client, accepted["id"])["state"] == "kept"


@pytest.mark.parametrize("commit_succeeds", [False, True])
def test_late_keep_reports_actual_transaction_outcome_not_precommit_intent(
    client, monkeypatch, commit_succeeds
):
    store = client.app.state.store
    project = store.create_project("Observe actual COMMIT")
    connection = store.connection
    commit_entered, release_commit = threading.Event(), threading.Event()
    keep_entered, keep_returned = threading.Event(), threading.Event()
    injected, replies, errors = [], [], []
    original_keep = project_deletion_tasks.ProjectDeletionTasks.keep

    @contextmanager
    def observed_connection():
        with connection() as db:
            yield db
            gone = not db.execute("SELECT id FROM projects WHERE id=?", (project["id"],)).fetchone()
            if gone and db.total_changes and not injected:
                injected.append(True)
                commit_entered.set()
                assert release_commit.wait(10), "Release the synthetic COMMIT boundary"
                if not commit_succeeds:
                    db.rollback()
                    raise OSError("Synthetic COMMIT rollback after the decision")

    def keep(self, identifier, project_id):
        keep_entered.set()
        return original_keep(self, identifier, project_id)

    def call_keep(task_id):
        try:
            replies.append(
                client.post(
                    f"/api/project-deletion-tasks/{task_id}/keep",
                    json={"project_id": project["id"]},
                )
            )
        except BaseException as error:
            errors.append(error)
        finally:
            keep_returned.set()

    monkeypatch.setattr(store, "connection", observed_connection)
    monkeypatch.setattr(project_deletion_tasks.ProjectDeletionTasks, "keep", keep)
    task = start(client, project["id"]).json()
    assert commit_entered.wait(10)
    thread = threading.Thread(target=call_keep, args=(task["id"],), daemon=True)
    thread.start()
    try:
        assert keep_entered.wait(10)
        assert not keep_returned.wait(0.2), "Keep claimed a final outcome before COMMIT completed"
    finally:
        release_commit.set()
        thread.join(10)
    assert not thread.is_alive() and not errors and replies
    done = settled(client, task["id"])
    if commit_succeeds:
        assert replies[0].status_code in (200, 409)
        reply = replies[0].json()
        if replies[0].status_code == 409:
            assert reply["detail"]["code"] == "deletion_committed"
        else:
            assert reply["committed"] is True and reply["can_keep"] is False
        assert done["committed"] is True and not project_exists(store, project["id"])
    else:
        assert replies[0].status_code in (200, 202), replies[0].text
        assert done["committed"] is False and project_exists(store, project["id"])


def test_capture_reservation_releases_after_copy_exception(client, hold_stop):
    owner = client.app.state.project_deletions
    store = client.app.state.store
    project = store.create_project("Capture exception")
    preview = client.post(f"/api/projects/{project['id']}/deletion-preview").json()
    with pytest.raises(RuntimeError, match="Synthetic copy failure"):
        with owner.capture_exclusive():
            raise RuntimeError("Synthetic copy failure")
    task, created = owner.start(project["id"], operation_id=preview["operation_id"])
    assert created is True
    try:
        assert hold_stop.entered.wait(10)
        owner.keep(task["id"], project["id"])
    finally:
        hold_stop.release.set()
    assert settled(client, task["id"])["state"] == "kept"
    with owner.capture_exclusive():
        assert not owner.fenced()


def test_capture_reservation_refuses_entry_after_owner_closes(client):
    owner = client.app.state.project_deletions
    owner.close()
    with pytest.raises(HTTPException) as refusal:
        with owner.capture_exclusive():
            raise AssertionError("A closed owner granted a fresh capture reservation")
    assert refusal.value.status_code == 409


def test_unknown_commit_facts_never_offer_keep_or_claim_project_deleted(client):
    owner = client.app.state.project_deletions
    task = project_deletion_tasks.Task(
        "a" * 32,
        "b" * 32,
        "Unknown outcome",
        "c" * 32,
        "d" * 64,
        "2026-11-20T22:00:00Z",
        "2026-11-20T22:00:00Z",
        committed=None,
    )
    with owner.lock:
        owner.tasks[task.identifier] = task
    try:
        assert owner.summary(task)["can_keep"] is False
        with pytest.raises(HTTPException) as refusal:
            owner.keep(task.identifier, task.project_id)
        assert refusal.value.status_code == 409
        assert refusal.value.detail["code"] == "deletion_unresolved"
        assert task.keep_requested is False
    finally:
        with owner.lock:
            del owner.tasks[task.identifier]
