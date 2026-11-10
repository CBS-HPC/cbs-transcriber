"""Deletion preserves live files and independently retries committed local cleanup."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import deletion_files
from cbs_transcriber.app import create_app
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


class Interrupted(BaseException):
    """A process interruption bypasses ordinary request failure rollback."""


def seed(store, *, count=2, parent=None):
    project = store.create_project(
        "Synthetic deletion", storage_parent=str(parent) if parent else None
    )
    recordings = []
    for index in range(count):
        recording = store.create_recording(project["id"], f"source-{index}.wav")
        folder = store.directory(recording["id"])
        (folder / "nested").mkdir(parents=True)
        (folder / "original.wav").write_bytes(f"Original {index}".encode())
        (folder / "nested" / "raw.json").write_bytes(b'{"synthetic":true}')
        store.add_result(
            recording["id"],
            normalize_segments([{"text": f"Retain these words {index}."}]),
            {},
            "Synthetic",
            {},
            # Current first results are Working at creation (ADR 0133), so reopening tests
            # only deletion recovery, not ADR 0141's one-time existing-result snapshot refresh.
            auto_first=True,
        )
        recordings.append(recording)
    recordings.sort(key=lambda row: row["id"])
    base = store.project_folder(project["id"])
    unrelated = base / "unrelated.txt"
    unrelated.write_bytes(b"Never owned by Delete")
    paths = [
        path
        for recording in recordings
        for path in (store.directory(recording["id"]), base / "exports" / recording["id"])
    ]
    project_file = base / "project.json"
    files = {
        path: path.read_bytes() for folder in paths for path in folder.rglob("*") if path.is_file()
    }
    files[project_file] = project_file.read_bytes()
    files[unrelated] = unrelated.read_bytes()
    return {
        "project": project,
        "recordings": recordings,
        "paths": paths,
        "project_file": project_file,
        "files": files,
    }


def assert_preserved(store, fixture):
    assert store.one("SELECT id FROM projects WHERE id=?", (fixture["project"]["id"],))
    for recording in fixture["recordings"]:
        assert store.recording(recording["id"])["transcripts"]
    for path, expected in fixture["files"].items():
        assert path.read_bytes() == expected, path


def prepare(store, fixture, *, operation_id=None):
    return deletion_files.prepare(
        store,
        operation_id or uuid4().hex,
        "project",
        fixture["project"]["id"],
        [row["id"] for row in fixture["recordings"]],
        fixture["paths"],
        fixture["project_file"],
    )


def payload_bytes(plan):
    return {
        str(path): path.read_bytes()
        for entry in plan["entries"]
        for payload in [Path(entry["payload"])]
        for path in ([payload] if payload.is_file() else payload.rglob("*"))
        if path.is_file()
    }


def assert_deleted(store, fixture):
    assert not store.all("SELECT id FROM projects WHERE id=?", (fixture["project"]["id"],))
    for recording in fixture["recordings"]:
        assert not store.all("SELECT id FROM recordings WHERE id=?", (recording["id"],))


@pytest.mark.parametrize("kind", ["recording", "project"])
def test_later_rename_failure_restores_all_live_files_before_returning_409(
    client, monkeypatch, tmp_path, kind
):
    store = client.app.state.store
    fixture = seed(store, parent=tmp_path / "chosen-storage")
    denied = fixture["paths"][1 if kind == "recording" else -1]
    renamed, removed = [], []
    rename = deletion_files._rename

    def fail_later(path, destination):
        if path == denied:
            raise PermissionError("Synthetic sharing violation on a later export")
        result = rename(path, destination)
        if path in fixture["paths"]:
            renamed.append(path)
        return result

    def reject_recursive_cleanup(*args, **kwargs):
        removed.append(args[0])
        pytest.fail("A failed precommit Delete must never recursively remove live files")

    monkeypatch.setattr(deletion_files, "_rename", fail_later)
    monkeypatch.setattr(deletion_files.shutil, "rmtree", reject_recursive_cleanup)
    target = fixture["recordings"][0]["id"] if kind == "recording" else fixture["project"]["id"]
    response = client.delete(f"/api/{kind}s/{target}")
    assert response.status_code == 409, response.text
    assert renamed, "The regression must fail after earlier paths have already moved"
    assert not removed
    assert_preserved(store, fixture)


def test_failed_database_commit_restores_originals_exports_and_project_metadata(
    client, monkeypatch
):
    store = client.app.state.store
    fixture = seed(store)
    connection = store.connection
    injected = []

    @contextmanager
    def failed_commit():
        with connection() as db:
            yield db
            deleted = not db.execute(
                "SELECT id FROM projects WHERE id=?", (fixture["project"]["id"],)
            ).fetchone()
            if db.total_changes and deleted and not injected:
                injected.append(True)
                db.rollback()
                raise OSError("Synthetic deletion commit failure")

    monkeypatch.setattr(store, "connection", failed_commit)
    try:
        response = client.delete(f"/api/projects/{fixture['project']['id']}")
    except OSError as error:
        assert "Synthetic deletion commit failure" in str(error)
    else:
        assert response.status_code == 409, response.text
    assert injected
    monkeypatch.setattr(store, "connection", connection)
    assert_preserved(store, fixture)


def test_partial_postcommit_cleanup_keeps_a_durable_retry_card(client, credentials, monkeypatch):
    store = client.app.state.store
    fixture = seed(store)
    rmtree = deletion_files.shutil.rmtree
    removed = []

    def partial_cleanup(path, *args, **kwargs):
        assert_deleted(store, fixture)
        if not removed:
            candidate = next(
                item
                for item in Path(path).rglob("*")
                if item.is_file() and item.name != "marker.json"
            )
            candidate.unlink()
            removed.append(candidate)
        raise PermissionError("Synthetic later locked file after partial cleanup")

    monkeypatch.setattr(deletion_files.shutil, "rmtree", partial_cleanup)
    response = client.delete(f"/api/projects/{fixture['project']['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    cleanup = response.json()["local_cleanup"]
    assert cleanup["state"] == "pending"
    assert cleanup["message"]
    assert removed
    assert_deleted(store, fixture)
    unrelated = fixture["project_file"].parent / "unrelated.txt"
    assert unrelated.read_bytes() == b"Never owned by Delete"

    # The independent filesystem journal outlives project, recording and job rows.
    with TestClient(
        create_app(store.root, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as reopened:
        assert reopened.post("/api/activity/clear").status_code == 200
        cards = reopened.get("/api/jobs").json()
        card_id = "local-deletion-" + cleanup["id"]
        assert any(
            row["id"] == card_id and row.get("local_deletion", {}).get("id") == cleanup["id"]
            for row in cards
        )
        monkeypatch.setattr(deletion_files.shutil, "rmtree", rmtree)
        retried = reopened.post(f"/api/local-deletions/{cleanup['id']}/retry")
        assert retried.status_code == 200, retried.text
        assert not any(row["id"] == card_id for row in reopened.get("/api/jobs").json())
        assert_deleted(reopened.app.state.store, fixture)
    assert unrelated.read_bytes() == b"Never owned by Delete"


@pytest.mark.parametrize("moved", [0, 1, "all"])
def test_recovery_of_uncommitted_staging_only_restores_files(client, monkeypatch, moved):
    store = client.app.state.store
    fixture = seed(store)
    job = store.add_job(fixture["recordings"][0]["id"], "media", {"unchanged": "frozen"})
    before = store.job(job["id"])
    rename = deletion_files._rename
    moves = []
    with store.library_lock():
        plan = prepare(store, fixture)
        existing = sum(entry["existed"] for entry in plan["entries"])

        def interrupt(path, destination):
            result = rename(path, destination)
            if path in fixture["paths"] or path == fixture["project_file"]:
                moves.append(path)
                if len(moves) == (existing if moved == "all" else moved):
                    raise Interrupted()
            return result

        if moved:
            with monkeypatch.context() as patch:
                patch.setattr(deletion_files, "_rename", interrupt)
                with pytest.raises(Interrupted):
                    deletion_files.stage(store, plan)
    reopened = Store(store.root)
    with reopened.library_lock():
        deletion_files.recover(reopened)
        assert not deletion_files.journal_status(reopened)
    assert_preserved(reopened, fixture)
    assert reopened.job(job["id"]) == before


def test_startup_restores_live_staging_before_serving_recordings(client, credentials):
    store = client.app.state.store
    fixture = seed(store)
    with store.library_lock():
        plan = prepare(store, fixture)
        deletion_files.stage(store, plan)
    assert not fixture["paths"][0].exists()
    with TestClient(
        create_app(store.root, start_worker=False, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as reopened:
        assert_preserved(reopened.app.state.store, fixture)
        for recording in fixture["recordings"]:
            assert reopened.get(f"/api/recordings/{recording['id']}").status_code == 200
        response = reopened.delete(
            f"/api/projects/{fixture['project']['id']}",
            params={"operation_id": plan["operation_id"]},
        )
        assert response.status_code == 409, response.text
        assert_preserved(reopened.app.state.store, fixture)


def test_unresolved_startup_blocks_workers_and_mutations_until_explicit_recovery(
    client, credentials, monkeypatch
):
    store = client.app.state.store
    fixture = seed(store)
    with store.library_lock():
        plan = prepare(store, fixture)
        deletion_files.stage(store, plan)
    occupied = fixture["paths"][0]
    occupied.mkdir()
    (occupied / "new-owner.txt").write_bytes(b"Retain this new folder")
    starts, stops = [], []
    monkeypatch.setattr(JobQueue, "start", lambda queue: starts.append(queue))
    monkeypatch.setattr(JobQueue, "stop", lambda queue: stops.append(queue))
    with TestClient(
        create_app(store.root, start_worker=True, credentials=credentials),
        headers={"X-CBS-Request": "1"},
    ) as reopened:
        assert not starts
        route = f"/api/recordings/{fixture['recordings'][0]['id']}"
        assert reopened.patch(route, json={"name": "Must remain blocked"}).status_code == 409
        assert reopened.delete(f"/api/projects/{fixture['project']['id']}").status_code == 409
        cards = reopened.get("/api/jobs").json()
        assert any(row.get("local_deletion", {}).get("id") == plan["operation_id"] for row in cards)
        preserved_collision = occupied.with_name("preserved-new-owner")
        occupied.rename(preserved_collision)
        response = reopened.post(f"/api/local-deletions/{plan['operation_id']}/retry")
        assert response.status_code == 200, response.text
        assert response.json()["local_cleanup"]["state"] == "complete"
        assert len(starts) == 1
        assert_preserved(reopened.app.state.store, fixture)
        assert (preserved_collision / "new-owner.txt").read_bytes() == b"Retain this new folder"
        assert reopened.patch(route, json={"name": "Recovery complete"}).status_code == 200
    assert stops == starts


def test_recovery_admission_does_not_block_the_event_loop_while_queue_lock_is_held(client):
    manager, queue = client.app.state.deletions, client.app.state.queue
    manager._remember_files({"id": uuid4().hex, "state": "recovery_pending", "committed": False})
    held, release = threading.Event(), threading.Event()

    def hold_staging_lock():
        with queue.lock:
            held.set()
            assert release.wait(10), "Test did not release the synthetic staging lock"

    owner = threading.Thread(target=hold_staging_lock, daemon=True)
    owner.start()
    assert held.wait(3)
    requests = ThreadPoolExecutor(max_workers=3)
    try:
        blocked = requests.submit(client.post, "/api/projects", json={"name": "Blocked"})
        health = requests.submit(client.get, "/api/health")
        cross_origin = requests.submit(
            client.post,
            "/api/projects",
            json={"name": "Forbidden"},
            headers={"Origin": "https://untrusted.invalid"},
        )
        assert blocked.result(timeout=3).status_code == 409
        assert health.result(timeout=3).status_code == 200
        assert cross_origin.result(timeout=3).status_code == 403
        assert not release.is_set()
    finally:
        release.set()
        owner.join(3)
        requests.shutdown(wait=True)
    assert not owner.is_alive()


def test_recovery_of_committed_staging_only_finishes_physical_cleanup(client):
    store = client.app.state.store
    fixture = seed(store)
    with store.library_lock():
        plan = prepare(store, fixture)
        deletion_files.stage(store, plan)
        with store.connection() as db:
            db.execute("DELETE FROM projects WHERE id=?", (fixture["project"]["id"],))
    reopened = Store(store.root)
    with reopened.library_lock():
        deletion_files.recover(reopened)
        assert not deletion_files.journal_status(reopened)
    assert_deleted(reopened, fixture)
    assert all(not Path(entry["wrapper"]).exists() for entry in plan["entries"])
    assert (
        fixture["project_file"].parent / "unrelated.txt"
    ).read_bytes() == b"Never owned by Delete"


@pytest.mark.parametrize("change", ["mixed_rows", "changed_owner", "occupied_original"])
def test_ambiguous_recovery_preserves_staged_bytes_and_never_overwrites(client, change):
    store = client.app.state.store
    fixture = seed(store)
    with store.library_lock():
        plan = prepare(store, fixture)
        deletion_files.stage(store, plan)
    before = payload_bytes(plan)
    assert before
    first = fixture["recordings"][0]["id"]
    collision = fixture["paths"][0] / "new-owner.txt"
    if change == "mixed_rows":
        store.execute("DELETE FROM recordings WHERE id=?", (first,))
    elif change == "changed_owner":
        store.execute("UPDATE recordings SET created=? WHERE id=?", ("changed identity", first))
    else:
        collision.parent.mkdir()
        collision.write_bytes(b"Keep the occupied destination")
    reopened = Store(store.root)
    with reopened.library_lock():
        summaries = deletion_files.recover(reopened)
        assert summaries and summaries[0]["state"] == "recovery_pending"
        assert deletion_files.journal_status(reopened)
    assert payload_bytes(plan) == before
    if change == "occupied_original":
        assert collision.read_bytes() == b"Keep the occupied destination"


def test_prepare_refuses_unowned_regular_directory_inside_the_project(client):
    store = client.app.state.store
    fixture = seed(store)
    outsider = fixture["project_file"].parent / "unowned"
    outsider.mkdir()
    sentinel = outsider / "keep.txt"
    sentinel.write_bytes(b"Unowned regular directory")
    with store.library_lock():
        with pytest.raises(ValueError):
            deletion_files.prepare(
                store,
                uuid4().hex,
                "project",
                fixture["project"]["id"],
                [row["id"] for row in fixture["recordings"]],
                [*fixture["paths"], outsider],
                fixture["project_file"],
            )
    assert sentinel.read_bytes() == b"Unowned regular directory"
    assert_preserved(store, fixture)


def test_tampered_journal_cannot_redirect_recovery_to_an_external_directory(client, tmp_path):
    store = client.app.state.store
    fixture = seed(store)
    with store.library_lock():
        plan = prepare(store, fixture)
        deletion_files.stage(store, plan)
    before = payload_bytes(plan)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_bytes(b"Outside deletion scope")
    journal = store.root / "local-deletions" / plan["operation_id"] / "plan.json"
    corrupt = json.loads(journal.read_bytes())
    corrupt["entries"][0]["original"] = str(external)
    journal.write_text(
        json.dumps(corrupt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    reopened = Store(store.root)
    with reopened.library_lock():
        summaries = deletion_files.recover(reopened)
        assert summaries and summaries[0]["state"] == "recovery_pending"
        assert summaries[0]["committed"] is None
    assert payload_bytes(plan) == before
    assert sentinel.read_bytes() == b"Outside deletion scope"


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_windows_locked_later_original_restores_previously_staged_directories(client, monkeypatch):
    import ctypes
    from ctypes import wintypes

    store = client.app.state.store
    fixture = seed(store)
    original = store.directory(fixture["recordings"][-1]["id"]) / "original.wav"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # A normal reader that did not grant FILE_SHARE_DELETE blocks ancestor renaming.
    handle = kernel.CreateFileW(str(original), 0x80000000, 3, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    renamed = []
    rename = Path.rename

    def observe(path, destination):
        result = rename(path, destination)
        if path in fixture["paths"]:
            renamed.append(path)
        return result

    monkeypatch.setattr(Path, "rename", observe)
    try:
        response = client.delete(f"/api/projects/{fixture['project']['id']}")
        assert response.status_code == 409, response.text
        assert renamed, "The sharing failure must happen after an earlier directory moved"
        assert_preserved(store, fixture)
    finally:
        assert kernel.CloseHandle(handle)
    response = client.delete(f"/api/projects/{fixture['project']['id']}")
    assert response.status_code == 200, response.text
