"""Permanent deletion of an inactive library: preview, scope, recovery and fences (ADR 0144).

Synthetic libraries in temporary folders only; no real library is ever touched.
"""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import libraries, library_deletion, storage
from cbs_transcriber.app import create_app
from cbs_transcriber.store import SCHEMA_VERSION, Store


class SimulatedCrash(BaseException):
    """Process death: bypasses every ordinary exception handler and rollback."""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(storage, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "redirected_roots", lambda: [])
    return tmp_path


def build(root, external_parent):
    """A listed library with one project inside its folder and one outside it."""
    store = Store(root)
    inside = store.create_project("Inside")
    outside = store.create_project("Outside", storage_parent=str(external_parent))
    for project in (inside, outside):
        recording = store.create_recording(project["id"], "interview.wav")
        folder = store.directory(recording["id"])
        folder.mkdir(parents=True)
        (folder / "original.wav").write_bytes(b"RIFF synthetic " + project["id"].encode())
    store.snapshot_project(outside["id"])
    libraries.register(store.root)
    return store, Path(outside["storage_dir"])


def tree(*tops):
    """Every path with its type, size, modification time and content hash."""
    found = {}
    for top in tops:
        for path in [top, *top.rglob("*")]:
            info = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            found[str(path)] = (info.st_mode, info.st_size, info.st_mtime_ns, digest)
    return found


@pytest.fixture
def scene(home, credentials):
    current = Store(home / "current")
    store, external = build(home / "old", home / "Projects")
    app = create_app(current.root, start_worker=False, credentials=credentials)
    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        yield {
            "client": client,
            "store": store,
            "root": store.root,
            "external": external,
            "current": current.root,
        }


def preview(client, root):
    response = client.post("/api/libraries/delete-preview", json={"path": str(root)})
    assert response.status_code == 200, response.text
    return response.json()


def delete(client, root, fingerprint, name=None):
    return client.post(
        "/api/libraries/delete",
        json={"path": str(root), "fingerprint": fingerprint, "confirm_name": name or root.name},
    )


def wrappers(*parents):
    return [p for parent in parents for p in parent.iterdir() if p.name.startswith(".cbs-")]


def test_preview_is_byte_for_byte_read_only_and_reads_wal_only_rows(scene):
    client, root, external = scene["client"], scene["root"], scene["external"]
    listed = client.get("/api/libraries").json()
    assert listed["can_delete"] is True and listed["pending_deletions"] == []
    # A row that exists only in the WAL, as after an unclean shutdown.
    project = scene["store"].all("SELECT id FROM projects WHERE storage_dir=?", (str(external),))
    raw = sqlite3.connect(root / "library.sqlite3")
    raw.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
    raw.execute("PRAGMA wal_autocheckpoint=0")
    raw.execute(
        "INSERT INTO recordings(id,project_id,name,filename,created) VALUES (?,?,?,?,?)",
        (uuid4().hex, project[0]["id"], "wal only", "w.wav", "2026-11-05T00:00:00+00:00"),
    )
    raw.commit()
    raw.close()
    assert (root / "library.sqlite3-wal").stat().st_size > 0
    (root / "server.lock").unlink()  # Preview must not create the lease file either.
    before = tree(root, external)
    first = preview(client, root)
    second = preview(client, root)
    assert tree(root, external) == before
    assert not (root / "server.lock").exists()
    assert first["fingerprint"] == second["fingerprint"]
    assert first["can_delete"] is True and first["blockers"] == []
    assert first["recordings"] == 3 and first["projects"] == 2
    assert [item["path"] for item in first["delete"]] == [str(root), str(external)]
    assert first["keep"] == [] and first["other_items"] == []
    assert first["bytes"] > 0 and first["files"] > 0


def test_delete_removes_library_exclusive_external_folder_and_registry_entry(scene):
    client, root, external = scene["client"], scene["root"], scene["external"]
    facts = preview(client, root)
    assert str(root) in libraries.load()["recent"]
    response = delete(client, root, facts["fingerprint"])
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "complete"
    assert not root.exists() and not external.exists()
    assert wrappers(root.parent, external.parent) == []
    assert str(root) not in libraries.load()["recent"]
    assert library_deletion.pending() == [] and not library_deletion.reserved(root)
    assert root not in libraries.candidates(include_hidden=True)
    assert client.get("/api/projects").status_code == 200
    # A new library may be created at the freed path afterwards.
    assert libraries.select(str(root), create=True, schema=SCHEMA_VERSION).path == root


def test_confirmation_name_and_stale_preview_are_refused(scene):
    client, root, store = scene["client"], scene["root"], scene["store"]
    facts = preview(client, root)
    assert (
        delete(client, root, facts["fingerprint"], name=root.name.upper() + "x").status_code == 400
    )
    project = store.all("SELECT id FROM projects")[0]["id"]
    store.create_recording(project, "late.wav")
    assert delete(client, root, facts["fingerprint"]).status_code == 409
    assert root.exists() and len(store.all("SELECT id FROM recordings")) == 3
    assert library_deletion.pending() == []


@pytest.mark.parametrize("scope", ["library", "external"])
@pytest.mark.parametrize("change", ["replace", "rename", "rewrite"])
def test_nested_file_changes_invalidate_confirmation_v183(scene, scope, change):
    client, root, external = scene["client"], scene["root"], scene["external"]
    parent = root if scope == "library" else external
    original = next(parent.rglob("original.wav"))
    facts = preview(client, root)
    assert facts["can_delete"]
    data = original.read_bytes()
    if change == "replace":
        # Keep the former inode occupied so even inode-reusing filesystems exercise a
        # replacement identity; totals and the enclosing direct child stay unchanged.
        previous = original.with_name("previous.wav")
        original.rename(previous)
        original.write_bytes(data)
        previous.unlink()
    elif change == "rename":
        original.rename(original.with_name("renamed.wav"))
    else:
        original.write_bytes(b"x" * len(data))
        changed = original.stat()
        os.utime(original, ns=(changed.st_atime_ns, changed.st_mtime_ns + 1_000_000_000))
    latest = preview(client, root)
    assert latest["files"] == facts["files"] and latest["bytes"] == facts["bytes"]
    assert latest["fingerprint"] != facts["fingerprint"]
    assert delete(client, root, facts["fingerprint"]).status_code == 409
    assert root.exists() and external.exists()
    assert library_deletion.pending() == []


def test_current_project_containing_target_library_blocks_deletion_v183(scene, home):
    current = Store(scene["current"])
    project = current.create_project(
        "Current project", storage_parent=str(home / "CurrentProjects")
    )
    root = Path(project["storage_dir"]) / "nested-library"
    old, external = build(root, home / "NestedProjects")
    facts = preview(scene["client"], old.root)
    assert not facts["can_delete"]
    assert any("project files in or around" in reason for reason in facts["blockers"])
    assert delete(scene["client"], old.root, facts["fingerprint"]).status_code == 409
    assert root.exists() and external.exists() and Path(project["storage_dir"]).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path spelling")
def test_inventory_accepts_normal_and_extended_windows_paths_v183(tmp_path):
    top = tmp_path / "inventory"
    child = top / "nested"
    child.mkdir(parents=True)
    (child / "original.wav").write_bytes(b"synthetic")
    normal = library_deletion._walk(top, [10])
    extended = library_deletion._walk(Path("\\\\?\\" + str(top)), [10])
    assert normal[:2] == (1, 9)
    assert extended == normal


def test_current_and_open_libraries_are_refused(scene, home):
    client, root, store = scene["client"], scene["root"], scene["store"]
    response = client.post("/api/libraries/delete-preview", json={"path": str(home / "current")})
    assert response.status_code == 400 and "open library" in response.json()["detail"]
    facts = preview(client, root)
    with store.library_lock():
        busy = preview(client, root)
        assert busy["can_delete"] is False and library_deletion.OPEN in busy["blockers"]
        assert delete(client, root, facts["fingerprint"]).status_code == 409
    assert root.exists() and scene["external"].exists()
    unknown = client.post("/api/libraries/delete-preview", json={"path": str(home / "nowhere")})
    assert unknown.status_code == 400


def _queued(store):
    recording = store.all("SELECT id FROM recordings")[0]["id"]
    store.add_job(recording, "transcribe", {})


def _remote(run, state="completed"):
    def add(store):
        recording = store.all("SELECT id FROM recordings")[0]["id"]
        job = store.add_job(recording, "transcribe", {})
        store.execute(
            "UPDATE jobs SET state=?,params=? WHERE id=?",
            (state, json.dumps({"ucloud_run": run}), job["id"]),
        )

    return add


def _malformed(store):
    recording = store.all("SELECT id FROM recordings")[0]["id"]
    job = store.add_job(recording, "transcribe", {})
    store.execute("UPDATE jobs SET state='failed',params='{bad' WHERE id=?", (job["id"],))


def _local_journal(store):
    (store.root / "local-deletions" / ("0" * 32)).mkdir(parents=True)


def _transfer(store):
    folder = store.root / "portable-transfers"
    folder.mkdir()
    record = {"format": "cbs-portable-transfer-v1", "state": "uploading"}
    (folder / ("1" * 32 + ".json")).write_text(json.dumps(record), encoding="utf-8")


@pytest.mark.parametrize(
    "obligation",
    [
        _queued,
        _remote({"job_id": "remote", "remote_state": "SUCCESS", "cleanup_state": "pending"}),
        _remote({"submission_attempted": True}, state="failed"),
        _remote({"job_id": "remote", "remote_state": "RUNNING"}, state="interrupted"),
        _malformed,
        _local_journal,
        _transfer,
    ],
    ids=["queued", "cleanup", "uncertain", "remote-running", "malformed", "journal", "transfer"],
)
def test_unsettled_work_blocks_deletion(scene, obligation):
    client, root, store = scene["client"], scene["root"], scene["store"]
    obligation(store)
    facts = preview(client, root)
    assert facts["can_delete"] is False and facts["blockers"]
    response = delete(client, root, facts["fingerprint"])
    assert response.status_code == 409
    assert response.json()["detail"] == facts["blockers"][0]
    assert root.exists() and scene["external"].exists()
    assert library_deletion.pending() == [] and wrappers(root.parent) == []


def test_settled_remote_history_does_not_block(scene):
    client, root, store = scene["client"], scene["root"], scene["store"]
    _remote({"job_id": "remote", "remote_state": "SUCCESS", "cleanup_state": "trashed"})(store)
    _remote({"submission_attempted": True, "reconciled_not_created": True}, "failed")(store)
    assert preview(client, root)["can_delete"] is True


def test_folder_shared_with_another_library_is_kept(scene, home):
    client, root, external = scene["client"], scene["root"], scene["external"]
    third = Store(home / "third")
    shared = third.create_project("Shared copy")
    third.execute("UPDATE projects SET storage_dir=? WHERE id=?", (str(external), shared["id"]))
    libraries.register(third.root)
    facts = preview(client, root)
    assert [item["path"] for item in facts["delete"]] == [str(root)]
    assert facts["keep"][0]["path"] == str(external)
    assert str(third.root) in facts["keep"][0]["reason"]
    assert delete(client, root, facts["fingerprint"]).status_code == 200
    assert not root.exists()
    originals = list(external.glob("recordings/*/original.wav"))
    assert len(originals) == 1 and originals[0].read_bytes().startswith(b"RIFF synthetic")


def _foreign_snapshot(external):
    (external / "project.json").write_text(
        json.dumps({"format_version": 1, "project": {"id": "f" * 32}}, indent=2),
        encoding="utf-8",
    )


def _unknown_item(external):
    (external / "thesis.docx").write_bytes(b"not created by the app")


def _missing(external):
    for path in sorted(external.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    external.rmdir()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (_foreign_snapshot, "another project"),
        (_unknown_item, "not created by CBS Transcriber"),
        (_missing, "no longer exists"),
    ],
    ids=["project-json-mismatch", "unknown-item", "missing"],
)
def test_unverified_external_folders_are_kept(scene, change, reason):
    client, root, external = scene["client"], scene["root"], scene["external"]
    change(external)
    facts = preview(client, root)
    assert facts["can_delete"] is True
    assert facts["keep"] and reason in facts["keep"][0]["reason"]
    assert [item["path"] for item in facts["delete"]] == [str(root)]
    assert delete(client, root, facts["fingerprint"]).status_code == 200
    assert not root.exists()
    assert external.exists() == (change is not _missing)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junctions")
def test_junctions_are_refused_inside_and_kept_outside(scene, home):
    import _winapi

    client, root, external = scene["client"], scene["root"], scene["external"]
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_text("keep", encoding="utf-8")
    _winapi.CreateJunction(str(elsewhere), str(root / "linked"))
    facts = preview(client, root)
    assert facts["can_delete"] is False
    assert any("link to another location" in reason for reason in facts["blockers"])
    assert delete(client, root, facts["fingerprint"]).status_code == 409
    os.rmdir(root / "linked")
    real = external.with_name(external.name + "-real")
    external.rename(real)
    _winapi.CreateJunction(str(real), str(external))
    facts = preview(client, root)
    assert facts["keep"] and "link" in facts["keep"][0]["reason"]
    assert delete(client, root, facts["fingerprint"]).status_code == 200
    assert (elsewhere / "precious.txt").exists() and any(real.rglob("original.wav"))
    os.rmdir(external)


def test_unknown_library_items_are_listed_and_deleted(scene):
    client, root = scene["client"], scene["root"]
    (root / "notes.txt").write_text("scratch", encoding="utf-8")
    facts = preview(client, root)
    assert facts["other_items"] == ["notes.txt"]
    assert delete(client, root, facts["fingerprint"]).status_code == 200
    assert not root.exists()


def counts(root):
    store = Store(root)
    return (
        len(store.all("SELECT id FROM projects")),
        len(store.all("SELECT id FROM recordings")),
    )


@pytest.mark.parametrize("stage", ["published", "staging", "staged", "committed", "finishing"])
def test_crash_at_each_boundary_recovers_at_startup(scene, home, monkeypatch, stage):
    client, root, external = scene["client"], scene["root"], scene["external"]
    constructed = Store(root)  # Built before the reservation; its lease is fenced later.
    before = counts(root)
    facts = preview(client, root)

    def crash(reached):
        if reached == stage:
            raise SimulatedCrash(reached)

    monkeypatch.setattr(library_deletion, "_checkpoint", crash)
    with pytest.raises(SimulatedCrash):
        library_deletion.delete(
            str(root),
            fingerprint=facts["fingerprint"],
            confirm_name=root.name,
            current=scene["current"],
        )
    monkeypatch.setattr(library_deletion, "_checkpoint", lambda reached: None)
    committed = stage in {"committed", "finishing"}

    # Fail closed on reopen while the deletion is unresolved.
    assert library_deletion.reserved(root)
    with pytest.raises(RuntimeError, match="unfinished permanent deletion"):
        Store(root)
    # After the folder itself is gone the lease file cannot even be opened.
    with pytest.raises(RuntimeError if stage != "finishing" else FileNotFoundError):
        with constructed.library_lock():
            pass
    assert root.exists() == (stage != "finishing")
    with pytest.raises(ValueError, match="unfinished permanent deletion"):
        libraries.select(str(root), create=True, schema=SCHEMA_VERSION)
    assert root not in libraries.candidates(include_hidden=True)
    pending = client.get("/api/libraries").json()["pending_deletions"]
    assert [item["state"] for item in pending] == [
        "cleanup_pending" if committed else "restore_pending"
    ]
    assert pending[0]["path"] == str(root)
    # The registry changes only after commit.
    assert (str(root) in libraries.load()["recent"]) == (stage != "finishing")

    outcomes = library_deletion.recover_all()
    assert [item["state"] for item in outcomes] == ["complete" if committed else "restored"]
    assert not library_deletion.reserved(root) and library_deletion.pending() == []
    assert wrappers(root.parent, external.parent) == []
    if committed:
        assert not root.exists() and not external.exists()
        assert str(root) not in libraries.load()["recent"]
    else:
        assert counts(root) == before
        assert any(external.rglob("original.wav")) and (external / "project.json").exists()
        assert root in libraries.candidates(include_hidden=True)
        assert str(root) in libraries.load()["recent"]


def preview_blocked_by_pending(client, root):
    facts = preview(client, root)
    return library_deletion.PENDING in facts["blockers"]


def test_unresolvable_rollback_stays_visible_until_retry(scene, home, monkeypatch):
    client, root = scene["client"], scene["root"]
    other, _ = build(home / "second", home / "OtherProjects")
    facts = preview(client, root)

    def crash(reached):
        if reached == "staged":
            raise SimulatedCrash(reached)

    monkeypatch.setattr(library_deletion, "_checkpoint", crash)
    with pytest.raises(SimulatedCrash):
        library_deletion.delete(
            str(root),
            fingerprint=facts["fingerprint"],
            confirm_name=root.name,
            current=scene["current"],
        )
    monkeypatch.setattr(library_deletion, "_checkpoint", lambda reached: None)
    # Something occupies an original name: recovery must never overwrite it.
    (root / "library.sqlite3").write_bytes(b"occupied")
    outcome = library_deletion.recover_all()[0]
    assert outcome["state"] == "attention" and "now exists" in outcome["message"]
    listed = client.get("/api/libraries").json()["pending_deletions"]
    assert listed[0]["state"] == "restore_pending" and "now exists" in listed[0]["message"]
    assert library_deletion.reserved(root)
    assert preview_blocked_by_pending(client, other.root)
    (root / "library.sqlite3").unlink()
    response = client.post(f"/api/libraries/deletions/{listed[0]['id']}/retry")
    assert response.status_code == 200 and response.json()["state"] == "restored"
    assert counts(root)[1] == 2 and not library_deletion.reserved(root)
    assert client.post("/api/libraries/deletions/" + "0" * 64 + "/retry").json()["state"] == (
        "complete"
    )


def test_staging_failure_is_undone_in_the_same_request(scene, monkeypatch):
    client, root, external = scene["client"], scene["root"], scene["external"]
    before = tree(root, external)
    facts = preview(client, root)
    original, calls = library_deletion._rename, []

    def flaky(source, destination):
        calls.append(source)
        if len(calls) == 3:
            raise PermissionError("in use")
        original(source, destination)

    monkeypatch.setattr(library_deletion, "_rename", flaky)
    response = delete(client, root, facts["fingerprint"])
    assert response.status_code == 409
    assert response.json()["detail"].startswith("Nothing was deleted")
    assert not library_deletion.reserved(root) and library_deletion.pending() == []
    assert wrappers(root.parent, external.parent) == []
    after = tree(root, external)
    # The lease file may now exist; every library entry is back with its exact bytes.
    after.pop(str(root / "server.lock"), None)
    before.pop(str(root / "server.lock"), None)
    assert {k: v[3] for k, v in after.items()} == {k: v[3] for k, v in before.items()}


def test_post_commit_cleanup_failure_is_retryable(scene, monkeypatch):
    client, root, external = scene["client"], scene["root"], scene["external"]
    facts = preview(client, root)
    original = library_deletion._remove_tree

    def refuse(path):
        raise PermissionError("in use")

    monkeypatch.setattr(library_deletion, "_remove_tree", refuse)
    response = delete(client, root, facts["fingerprint"])
    assert response.status_code == 200 and response.json()["state"] == "cleanup_pending"
    assert not root.exists() and library_deletion.reserved(root)
    listed = client.get("/api/libraries").json()["pending_deletions"]
    assert listed[0]["state"] == "cleanup_pending" and "in use" in listed[0]["message"]
    with pytest.raises(RuntimeError, match="unfinished permanent deletion"):
        Store(root)
    assert not root.exists()  # The fenced Store never recreated the deleted folder.
    monkeypatch.setattr(library_deletion, "_remove_tree", original)
    retry = client.post(f"/api/libraries/deletions/{listed[0]['id']}/retry").json()
    assert retry["state"] == "complete"
    assert wrappers(root.parent, external.parent) == [] and not external.exists()


def test_another_library_inside_the_folder_blocks(scene, home):
    client, root = scene["client"], scene["root"]
    nested = Store(root / "nested-library")
    libraries.register(nested.root)
    facts = preview(client, root)
    assert any(
        str(nested.root) in reason and "inside this folder" in reason
        for reason in facts["blockers"]
    )


def test_old_schema_library_without_project_folders_is_deleted(scene, home):
    client, root = scene["client"], home / "ancient"
    root.mkdir()
    project, recording = uuid4().hex, uuid4().hex
    with sqlite3.connect(root / "library.sqlite3") as db:
        db.executescript(
            """
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE recordings (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                name TEXT NOT NULL, filename TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE jobs (id TEXT PRIMARY KEY, recording_id TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', params TEXT NOT NULL DEFAULT '{}',
                created TEXT NOT NULL, updated TEXT NOT NULL);
            PRAGMA user_version=1;
            """
        )
        db.execute("INSERT INTO projects VALUES (?,?,?)", (project, "Legacy", "2024-01-01"))
        db.execute(
            "INSERT INTO recordings VALUES (?,?,?,?,?)",
            (recording, project, "old", "old.wav", "2024-01-01"),
        )
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,created,updated) VALUES (?,?,?,?,?,?)",
            (uuid4().hex, recording, "transcribe", "completed", "2024-01-01", "2024-01-01"),
        )
    db.close()
    (root / "recordings" / recording).mkdir(parents=True)
    (root / "recordings" / recording / "original.wav").write_bytes(b"RIFF legacy")
    libraries.register(root)
    facts = preview(client, root)
    assert facts["can_delete"] is True and facts["recordings"] == 1 and facts["projects"] == 1
    assert delete(client, root, facts["fingerprint"]).status_code == 200
    assert not root.exists()


def test_newer_schema_library_is_refused(scene):
    client, root = scene["client"], scene["root"]
    with sqlite3.connect(root / "library.sqlite3") as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    db.close()
    facts = preview(client, root)
    assert facts["can_delete"] is False and "newer" in facts["blockers"][0]
    assert delete(client, root, facts["fingerprint"]).status_code == 409
    assert (root / "library.sqlite3").exists()


def test_chooser_lists_and_retries_but_cannot_delete(home):
    from cbs_transcriber.app import create_chooser_app

    with TestClient(create_chooser_app(), headers={"X-CBS-Request": "1"}) as chooser:
        assert chooser.get("/api/libraries").json()["pending_deletions"] == []
        missing = chooser.post("/api/libraries/delete-preview", json={"path": str(home)})
        assert missing.status_code in {404, 405}
