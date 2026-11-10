"""ADR 0141's schema18 pass; current schema19 intentionally refuses designation."""

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from importlib.resources import files as package_files

import pytest
from fastapi.testclient import TestClient
from test_input_admission import library as library

from cbs_transcriber import input_admission, result_roles, working_existing
from cbs_transcriber import store as store_module
from cbs_transcriber.app import create_app
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture(autouse=True)
def supported_schema18(monkeypatch):
    # Select the real constructor's migration target BEFORE creating any library,
    # including the imported library fixture and create_app's startup Store.
    # Never relabel a schema19 database or add its passage_reviews table to schema18.
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 18)


@pytest.fixture
def store(tmp_path):
    return store_module.Store(tmp_path / "synthetic-library")


def recording(store, project=None, name="synthetic.wav"):
    project = project or store.create_project("Synthetic")
    return store.create_recording(project["id"], name)


def add(store, recording_id, text="Synthetic passage.", **kwargs):
    return store.add_result(
        recording_id,
        normalize_segments([{"start": 0, "end": 1, "text": text}]),
        {"SPEAKER_00": "Synthetic voice"},
        "Synthetic result",
        kwargs.pop("source", {}),
        **kwargs,
    )


def legacy_job_result(store, recording_id, text="Legacy job result."):
    """A completed unmarked job: ADR 0133 grants it no promotion authority."""
    job = store.add_job(recording_id, "transcribe", {"provider_name": "Synthetic"})
    store.update_job(job["id"], state="running")
    return add(store, recording_id, text, job_id=job["id"]), job


def role(store, recording_id):
    with store.connection() as db:
        return result_roles.read(db, store, recording_id, verify_tail=True)


def events(store, recording_id):
    return store.all(
        "SELECT * FROM result_role_events WHERE recording_id=? ORDER BY sequence", (recording_id,)
    )


def mark(store, recording_id, version_id):
    current = store.recording(recording_id)
    state = role(store, recording_id)
    plan = result_roles.preview(
        store, recording_id, version_id, current["revision"], state["revision"]
    )
    return result_roles.apply(
        store, recording_id, version_id, current["revision"], state["revision"], plan["fingerprint"]
    )


def creation(store, recording_id, result_id):
    return store.result_versions(recording_id, result_id)[-1]


def designated(store, recording_id):
    working = role(store, recording_id)["working"]
    return working and working["version_id"]


def files(folder):
    return {
        path.relative_to(folder).as_posix(): (path.stat().st_mtime_ns, path.read_bytes())
        for path in folder.rglob("*")
        if path.is_file()
    }


def assert_catalog_schema(store, version):
    catalog = json.loads(
        package_files("cbs_transcriber")
        .joinpath(f"catalogs/portable_schema_{version}.json")
        .read_text(encoding="utf-8")
    )["structure"]
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert tables == set(catalog["tables"])
    assert ("passage_reviews" in tables) == (version == 19)


def test_supported_fixture_is_actually_schema18(store):
    assert store_module.SCHEMA_VERSION == 18
    assert_catalog_schema(store, 18)


def test_actual_schema19_is_refused_without_changing_library_state(tmp_path, monkeypatch):
    # This test explicitly leaves the legacy module fixture's runtime configuration.
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 19)
    current = store_module.Store(tmp_path / "actual-schema19")
    assert_catalog_schema(current, 19)
    project = current.create_project("Current schema")
    rid = add(current, recording(current, project)["id"])["recording_id"]
    before = current.recording(rid)
    versions = current.result_versions(rid, before["active_transcript_id"])
    outputs = files(current.project_folder(project["id"]))
    with current.connection() as db:
        rows = tuple(db.iterdump())

    summary = working_existing.startup(current)

    assert summary["refused"] == "unsupported_schema"
    assert summary["designated"] == summary["pages"] == summary["job_scans"] == 0
    assert not summary["incomplete"] and summary["aborted"] is None
    assert current.recording(rid) == before
    assert role(current, rid) == {"revision": 0, "working": None}
    assert events(current, rid) == []
    assert current.result_versions(rid, before["active_transcript_id"]) == versions
    with current.connection() as db:
        assert tuple(db.iterdump()) == rows
    assert files(current.project_folder(project["id"])) == outputs


def test_untouched_sole_results_become_working_with_the_system_first_event(store):
    project = store.create_project("Synthetic")
    sources = {
        "direct": add(store, recording(store, project, "a.wav")["id"]),
        "job": legacy_job_result(store, recording(store, project, "b.wav")["id"])[0],
        "import": add(store, recording(store, project, "c.wav")["id"], source={"kind": "import"}),
    }
    before = {
        name: (
            store.recording(result["recording_id"]),
            creation(store, result["recording_id"], result["id"]),
        )
        for name, result in sources.items()
    }
    folder = store.project_folder(project["id"])
    outputs = files(folder)

    summary = working_existing.designate_existing(store)

    assert summary["designated"] == 3 and summary["refused"] is summary["aborted"] is None
    for name, result in sources.items():
        rid = result["recording_id"]
        old_recording, version = before[name]
        assert version["action"] in {"provider_result", "imported_result"}
        assert designated(store, rid) == version["id"]
        [event] = events(store, rid)
        assert (event["sequence"], event["action"], event["actor_kind"]) == (
            1,
            "working_auto_first",
            "system",
        )
        assert event["previous_version_id"] is None
        assert event["next_payload_sha256"] == version["payload_sha256"]
        with store.connection() as db:
            history = result_roles.public_history(db, store, rid, "synthetic")
        assert [item["actor_kind"] for item in history["events"]] == ["system"]
        # Only role rows change: no version, projection, revision or job is written.
        current = store.recording(rid)
        assert current["revision"] == old_recording["revision"]
        assert current["segments"] == old_recording["segments"]
        assert store.result_versions(rid, result["id"]) == [version]
    # The database pass alone writes no file; snapshots follow in reconcile_snapshots.
    assert files(folder) == outputs
    assert summary["projects"] == {project["id"]}


def test_initial_result_with_existing_edit_is_not_promoted(store):
    """Codex regression: an imported creation can already carry edited passages."""
    rid = recording(store)["id"]
    segments = normalize_segments([{"start": 0, "end": 1, "text": "Changed."}])
    segments[0]["edited"] = True
    segments[0]["original_text"] = "Original."
    store.add_result(rid, segments, {}, "Imported", {})
    words = recording(store, name="words.wav")["id"]
    segments = normalize_segments([{"start": 0, "end": 1, "text": "Same text."}])
    segments[0]["original_word_timing"] = not segments[0]["word_timing"]
    store.add_result(words, segments, {}, "Imported", {})

    summary = working_existing.designate_existing(store)

    assert summary["skipped"] == {"edited": 2}
    assert role(store, rid) == role(store, words) == {"revision": 0, "working": None}


def test_ineligible_recordings_are_skipped_without_any_role_write(store):
    project = store.create_project("Synthetic")
    cases = {}

    def case(name):
        cases[name] = recording(store, project, f"{name}.wav")["id"]
        return cases[name]

    rid = case("two_results")
    add(store, rid, "One.")
    add(store, rid, "Two.")

    rid = case("edited")
    add(store, rid)
    current = store.recording(rid)
    store.save_transcript(  # A pre-ADR-0133 edit: no Working choice was recorded.
        rid,
        normalize_segments([{"start": 0, "end": 1, "text": "Edited passage."}]),
        current["speakers"],
        current["revision"],
    )

    rid = case("trash_restored")
    trashed = add(store, rid)
    revision = store.recording(rid)["revision"]
    store.set_result_trashed(rid, trashed["id"], revision, trashed=True)
    store.set_result_trashed(rid, trashed["id"], revision + 1, trashed=False)

    rid = case("trashed_with_alternative")
    gone = add(store, rid, "Removed alternative.")
    kept = add(store, rid, "Kept.")
    store.set_result_trashed(rid, gone["id"], store.recording(rid)["revision"], trashed=True)
    assert store.recording(rid)["active_transcript_id"] == kept["id"]

    rid = case("cleared")
    cleared = add(store, rid)
    mark(store, rid, creation(store, rid, cleared["id"])["id"])
    mark(store, rid, None)

    rid = case("already_designated")
    chosen = add(store, rid)
    mark(store, rid, creation(store, rid, chosen["id"])["id"])

    for state in ("queued", "running", "interrupted", "unknown-state"):
        rid = case(state)
        add(store, rid)
        job = store.add_job(rid, "media", {})
        store.execute("UPDATE jobs SET state=? WHERE id=?", (state, job["id"]))

    rid = case("legacy_observed")
    legacy = add(store, rid)
    store.execute(
        "UPDATE result_versions SET action='legacy_observed' WHERE result_id=?", (legacy["id"],)
    )

    rid = case("hash_mismatch")
    damaged = add(store, rid)
    store.execute(
        "UPDATE result_versions SET payload_sha256=? WHERE result_id=?", ("0" * 64, damaged["id"])
    )

    rid = case("projection_drift")
    drift = add(store, rid)
    store.execute(
        "UPDATE recordings SET segments=? WHERE id=?",
        (json.dumps(normalize_segments([{"start": 0, "end": 1, "text": "Drift."}])), rid),
    )
    assert drift

    rid = case("observation_metadata")
    stamped = add(store, rid)
    store.execute(
        "UPDATE result_versions SET observed_at='not a time' WHERE result_id=?", (stamped["id"],)
    )

    before = {
        name: (role(store, rid), events(store, rid))
        for name, rid in cases.items()
        if name not in {"hash_mismatch", "observation_metadata"}
    }

    summary = working_existing.designate_existing(store)

    assert summary["designated"] == 0, summary
    for name, rid in cases.items():
        if name in before:
            assert (role(store, rid), events(store, rid)) == before[name], name
        else:
            assert not events(store, rid), name
    skipped = summary["skipped"]
    assert skipped["results"] == 2  # Two live results; a trashed alternative still counts.
    assert skipped["versions"] == 2  # Edited; trashed and restored.
    assert skipped["pending_processing"] == 4
    assert skipped["not_creation"] == 1
    assert skipped["invalid_evidence"] == 2  # Damaged hash or observation metadata.
    assert skipped["projection"] == 1
    # Recordings with any role event (cleared, designated) are never candidates at all.
    assert sum(skipped.values()) == len(cases) - 2


def test_frozen_working_authority_and_stopped_intents_block_designation(library):
    store, _, provider, records = library
    target = records[0]
    add(store, target["id"], "Existing.")
    request = {
        "recording_id": target["id"],
        "choice": input_admission.choice(provider=provider, replace=True),
    }
    with store.library_lock():
        job = input_admission.accept(store, [request])[0]
    assert store.one(
        "SELECT 1 AS present FROM working_job_authorities WHERE job_id=?", (job["id"],)
    )
    # Stopped awaiting Prepare: settled job state, but frozen ADR 0133 authority remains.
    store.execute("UPDATE jobs SET state='failed' WHERE id=?", (job["id"],))
    summary = working_existing.designate_existing(store)
    assert summary["skipped"] == {"pending_processing": 1}
    assert role(store, target["id"]) == {"revision": 0, "working": None}


def test_restored_or_unverifiable_libraries_are_refused(store, monkeypatch):
    rid = recording(store)["id"]
    add(store, rid, source={"kind": "import"})  # A restored import has no job to quarantine.
    preservation = store.root / "preservation"

    preservation.mkdir()
    assert working_existing.designate_existing(store)["refused"] == "restored_library"
    preservation.rmdir()
    preservation.write_bytes(b"")  # Any entry, not only a directory, is restore evidence.
    assert working_existing.designate_existing(store)["refused"] == "restored_library"
    preservation.unlink()

    store.execute(
        "INSERT INTO restored_owners VALUES ('job',?,?,?)", ("a" * 32, "b" * 32, "c" * 32)
    )
    assert working_existing.designate_existing(store)["refused"] == "restored_library"
    store.execute("DELETE FROM restored_owners")

    real = os.lstat

    def denied(path, *args, **kwargs):
        if os.fspath(path).endswith("preservation"):
            raise PermissionError("synthetic denial")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(working_existing.os, "lstat", denied)
    assert working_existing.designate_existing(store)["refused"] == "restore_evidence_unreadable"
    monkeypatch.setattr(working_existing.os, "lstat", real)
    assert not events(store, rid)
    assert working_existing.designate_existing(store)["designated"] == 1


def test_rerun_is_idempotent(store):
    project = store.create_project("Synthetic")
    rids = [
        add(store, recording(store, project, f"{n}.wav")["id"])["recording_id"] for n in range(3)
    ]
    assert working_existing.designate_existing(store, page=2)["designated"] == 3
    snapshot = {rid: events(store, rid) for rid in rids}
    again = working_existing.designate_existing(store, page=2)
    assert again["designated"] == 0 and again["skipped"] == {} and again["pages"] == 0
    assert {rid: events(store, rid) for rid in rids} == snapshot


def test_user_choices_win_over_the_pass(store):
    project = store.create_project("Synthetic")
    # A preview made before the pass cannot be applied afterwards.
    stale = add(store, recording(store, project, "stale.wav")["id"])
    rid = stale["recording_id"]
    version_id = creation(store, rid, stale["id"])["id"]
    revision = store.recording(rid)["revision"]
    plan = result_roles.preview(store, rid, None, revision, 0)  # Explicitly keep unassigned.
    # An edit whose Working choice was captured at role revision 0.
    edited = add(store, recording(store, project, "edit.wav")["id"])
    edit_id = edited["recording_id"]
    edit_choice = {
        "version_id": creation(store, edit_id, edited["id"])["id"],
        "role_revision": 0,
        "make_working": False,
    }
    edit_view = store.recording(edit_id)

    assert working_existing.designate_existing(store)["designated"] == 2

    with pytest.raises(ValueError, match="changed"):
        result_roles.apply(store, rid, None, revision, 0, plan["fingerprint"])
    assert designated(store, rid) == version_id
    with pytest.raises(ValueError, match="changed"):
        store.save_transcript(
            edit_id,
            normalize_segments([{"start": 0, "end": 1, "text": "Late edit."}]),
            edit_view["speakers"],
            edit_view["revision"],
            working_choice=edit_choice,
        )
    assert len(store.result_versions(edit_id, edited["id"])) == 1
    # The researcher can still review and clear it explicitly; the pass never re-designates.
    mark(store, rid, None)
    assert working_existing.designate_existing(store)["designated"] == 0
    assert role(store, rid)["working"] is None


class CommitHook:
    """Delegate to a real connection, running a callback after each of its commits."""

    def __init__(self, db, after_commit):
        self._db, self._after = db, after_commit

    def __getattr__(self, name):
        return getattr(self._db, name)

    def commit(self):
        self._db.commit()
        self._after()


def test_concurrent_writes_between_pages_are_observed(store, monkeypatch):
    project = store.create_project("Synthetic")
    rids = sorted(
        add(store, recording(store, project, f"{n}.wav")["id"])["recording_id"] for n in range(4)
    )
    original = store.connection
    commits = []

    def concurrent():
        commits.append(None)
        if len(commits) == 1:
            # Another window queues work for a later candidate and designates another one.
            store.add_job(rids[2], "media", {})
            later = store.results(rids[3])[0]
            mark(store, rids[3], creation(store, rids[3], later["id"])["id"])

    @contextmanager
    def hooked():
        with original() as db:
            yield CommitHook(db, concurrent)

    monkeypatch.setattr(store, "connection", hooked)
    summary = working_existing.designate_existing(store, page=1)
    monkeypatch.setattr(store, "connection", original)
    assert summary["job_scans"] == 2  # Rescanned after the foreign commit, not per page.
    assert designated(store, rids[0]) and designated(store, rids[1])
    assert role(store, rids[2]) == {"revision": 0, "working": None}
    assert [e["actor_kind"] for e in events(store, rids[3])] == ["local_user"]


def test_interleaved_user_designation_waits_and_then_fails_closed(store, monkeypatch):
    rid = add(store, recording(store)["id"])["recording_id"]
    revision = store.recording(rid)["revision"]
    plan = result_roles.preview(store, rid, None, revision, 0)
    inside, outcome = threading.Event(), {}
    original = working_existing.result_roles.move_automatic

    def slow(*args, **kwargs):
        written = original(*args, **kwargs)
        inside.set()
        time.sleep(0.3)  # Keep the writer transaction open while the user's save waits.
        return written

    def user():
        inside.wait(5)
        try:
            result_roles.apply(store, rid, None, revision, 0, plan["fingerprint"])
            outcome["result"] = "applied"
        except ValueError as error:
            outcome["result"] = str(error)

    monkeypatch.setattr(working_existing.result_roles, "move_automatic", slow)
    thread = threading.Thread(target=user)
    thread.start()
    assert working_existing.designate_existing(store)["designated"] == 1
    thread.join(10)
    assert "changed" in outcome["result"]
    assert [e["action"] for e in events(store, rid)] == ["working_auto_first"]


def test_interrupted_pass_commits_whole_pages_only(store, monkeypatch):
    project = store.create_project("Synthetic")
    rids = sorted(
        add(store, recording(store, project, f"{n}.wav")["id"])["recording_id"] for n in range(5)
    )
    original = working_existing.result_roles.move_automatic
    calls = []

    def crash(db, *args, **kwargs):
        calls.append(args[1])
        written = original(db, *args, **kwargs)
        if len(calls) == 3:
            raise KeyboardInterrupt  # A process stop after the role rows were written.
        return written

    monkeypatch.setattr(working_existing.result_roles, "move_automatic", crash)
    with pytest.raises(KeyboardInterrupt):
        working_existing.designate_existing(store, page=2)
    monkeypatch.setattr(working_existing.result_roles, "move_automatic", original)
    # Page one committed; the interrupted second page left none of its written rows.
    assert calls == rids[:3]
    assert [bool(designated(store, rid)) for rid in rids] == [True, True, False, False, False]
    assert len(store.all("SELECT * FROM result_role_events")) == 2
    assert not store.all(
        "SELECT q.recording_id FROM recording_result_roles q WHERE NOT EXISTS "
        "(SELECT 1 FROM result_role_events e WHERE e.recording_id=q.recording_id)"
    )
    for rid in rids:
        role(store, rid)  # Every state row agrees with its event tail.
    assert working_existing.designate_existing(store)["designated"] == 3
    assert all(designated(store, rid) for rid in rids)


def test_database_errors_abort_without_blocking_startup(store, monkeypatch):
    add(store, recording(store)["id"])
    monkeypatch.setattr(working_existing, "_BLOCKING", "SELECT * FROM missing_synthetic_table")
    summary = working_existing.designate_existing(store)
    assert summary["aborted"] == "OperationalError" and summary["designated"] == 0
    assert not store.all("SELECT * FROM result_role_events")


def test_a_held_writer_lock_stops_the_pass_quickly(store):
    rid = add(store, recording(store)["id"])["recording_id"]
    other = sqlite3.connect(store.db_path)
    try:
        other.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        summary = working_existing.designate_existing(store, seconds=30)
        assert time.monotonic() - started < 5  # Short busy timeout, not the ordinary 15 s.
        assert summary["aborted"] == "OperationalError" and summary["incomplete"]
    finally:
        other.rollback()
        other.close()
    assert role(store, rid)["working"] is None
    assert working_existing.designate_existing(store)["designated"] == 1


def test_the_deadline_interrupts_a_long_sqlite_statement(store, monkeypatch):
    rid = add(store, recording(store)["id"])["recording_id"]
    endless = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT x FROM n WHERE x<0"
    )
    monkeypatch.setattr(working_existing, "_BLOCKING", endless)
    started = time.monotonic()
    summary = working_existing.designate_existing(store, seconds=0.5)
    assert time.monotonic() - started < 5
    assert summary["aborted"] == "OperationalError" and summary["incomplete"]
    assert summary["designated"] == 0 and role(store, rid)["working"] is None


def project_json(store, project_id):
    path = store.project_folder(project_id) / "project.json"
    return json.loads(path.read_text(encoding="utf-8")), path.stat().st_mtime_ns


def snapshot_role(store, project_id, recording_id):
    value, _ = project_json(store, project_id)
    [entry] = [item for item in value["recordings"] if item["id"] == recording_id]
    return entry["result_roles"]


def test_startup_designates_and_refreshes_the_project_snapshot(tmp_path):
    root = tmp_path / "library"
    store = store_module.Store(root)
    project = store.create_project("Synthetic")
    rid = add(store, recording(store, project)["id"])["recording_id"]
    assert snapshot_role(store, project["id"], rid)["working"] is None
    unrelated = store.project_folder(project["id"]) / "researcher-notes.txt"
    unrelated.write_bytes(b"Not written by the app")
    app = create_app(root, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1", headers={"X-CBS-Request": "1"}) as client:
        summary = app.state.working_existing
        assert summary["designated"] == 1 and summary["snapshots"]["refreshed"] == 1
        recording_view = client.get(f"/api/recordings/{rid}").json()
        assert recording_view["result_roles"]["working"]["version_id"] == designated(store, rid)
        assert recording_view["snapshot_error"] is None
    # The ordinary Working snapshot path wrote the same role into project.json.
    assert snapshot_role(store, project["id"], rid) == role(store, rid)
    assert unrelated.read_bytes() == b"Not written by the app"


def test_snapshot_interrupted_after_commit_is_recovered_once(store):
    # A project designated and snapshotted by an earlier ordinary start.
    current = store.create_project("Current")
    current_rid = add(store, recording(store, current)["id"])["recording_id"]
    working_existing.startup(store)
    current_before = files(store.project_folder(current["id"]))
    assert snapshot_role(store, current["id"], current_rid)["working"] is not None
    project = store.create_project("Synthetic")
    rid = add(store, recording(store, project)["id"])["recording_id"]
    legacy = store.create_project("Unsorted legacy")
    store.execute("UPDATE projects SET storage_dir=NULL WHERE id=?", (legacy["id"],))
    add(store, recording(store, legacy)["id"])
    time.sleep(0.02)
    # A crash after the database commit: the role is durable, project.json is stale.
    working_existing.designate_existing(store)
    assert designated(store, rid) and snapshot_role(store, project["id"], rid)["working"] is None

    summary = working_existing.reconcile_snapshots(store)
    assert summary == {"refreshed": 1, "current": 1, "refused": 0, "failed": 0, "pending": 0}
    assert snapshot_role(store, project["id"], rid) == role(store, rid)
    # Up-to-date and unrelated projects are not rewritten; legacy layouts get no warning.
    assert files(store.project_folder(current["id"])) == current_before
    assert store.snapshot_errors == {}
    _, stamp = project_json(store, project["id"])
    assert working_existing.reconcile_snapshots(store)["current"] == 2
    assert project_json(store, project["id"])[1] == stamp


def stale_project(store):
    project = store.create_project("Synthetic")
    rid = add(store, recording(store, project)["id"])["recording_id"]
    folder = store.project_folder(project["id"])
    working_existing.designate_existing(store)
    assert designated(store, rid)
    return project["id"], rid, folder


def test_newer_file_timestamp_does_not_hide_stale_working_snapshot(store):
    project_id, rid, folder = stale_project(store)
    path = folder / "project.json"
    future = time.time_ns() + 10_000_000_000
    os.utime(path, ns=(future, future))
    assert snapshot_role(store, project_id, rid)["working"] is None
    assert working_existing.reconcile_snapshots(store)["refreshed"] == 1
    assert snapshot_role(store, project_id, rid) == role(store, rid)
    assert store.snapshot_errors == {}


def test_unverified_snapshot_content_is_retained_with_warning(store, monkeypatch):
    project_id, rid, folder = stale_project(store)
    path = folder / "project.json"
    before = path.read_bytes()
    monkeypatch.setattr(working_existing, "SNAPSHOT_CHECK_BYTES", len(before) - 1)
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    assert path.read_bytes() == before
    assert store.recording(rid)["snapshot_error"] == working_existing.UNVERIFIED


def test_foreign_snapshot_in_id_named_folder_is_not_overwritten(store):
    project_id, rid, folder = stale_project(store)
    path = folder / "project.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["project"]["id"] = "f" * 32
    path.write_text(json.dumps(value), encoding="utf-8")
    before = path.read_bytes()
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    assert path.read_bytes() == before
    assert store.recording(rid)["snapshot_error"] == working_existing.UNVERIFIED


def test_unowned_storage_folder_is_never_written(store):
    project_id, rid, folder = stale_project(store)
    elsewhere = folder.with_name("someone-else")
    folder.rename(elsewhere)
    store.execute("UPDATE projects SET storage_dir=? WHERE id=?", (str(elsewhere), project_id))
    before = files(elsewhere)
    summary = working_existing.reconcile_snapshots(store)
    assert summary["refused"] == 1
    assert files(elsewhere) == before
    assert store.snapshot_errors[project_id] == working_existing.UNVERIFIED
    assert store.recording(rid)["snapshot_error"] == working_existing.UNVERIFIED
    assert designated(store, rid)  # SQLite remains authoritative.


def test_missing_folder_and_non_regular_targets_are_refused(store):
    project_id, rid, folder = stale_project(store)
    (folder / "project.json").unlink()
    (folder / "project.json").mkdir()
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    assert (folder / "project.json").is_dir()
    (folder / "project.json").rmdir()
    export = folder / "exports" / rid / f"{store.recording(rid)['active_transcript_id']}.txt"
    export.unlink()
    export.mkdir()
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    assert not (folder / "project.json").exists()
    export.rmdir()
    moved = folder.with_name(folder.name + "-moved")
    folder.rename(moved)
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    moved.rename(folder)
    assert working_existing.reconcile_snapshots(store)["refreshed"] == 1
    assert store.snapshot_errors == {}


def link(path, target):
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(path))
    except (ImportError, AttributeError, OSError):
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("This account cannot create directory links.")


def test_linked_parent_or_export_folder_is_refused(store, tmp_path):
    project_id, rid, folder = stale_project(store)
    outside = tmp_path / "outside"
    outside.mkdir()
    exports = folder / "exports"
    shutil.rmtree(exports)
    link(exports, outside)
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    assert not any(outside.iterdir())
    os.rmdir(exports)  # Removes only the link.

    real = tmp_path / "real-parent"
    real.mkdir()
    folder.rename(real / folder.name)
    linked_parent = tmp_path / "linked-parent"
    link(linked_parent, real)
    store.execute(
        "UPDATE projects SET storage_dir=? WHERE id=?",
        (str(linked_parent / folder.name), project_id),
    )
    before = files(real)
    assert working_existing.reconcile_snapshots(store)["refused"] == 1
    assert files(real) == before
    assert designated(store, rid)


def test_failed_snapshot_write_stays_visible_and_is_retried(store, monkeypatch):
    project_id, rid, folder = stale_project(store)
    original = type(folder).replace

    def refuse(self, target):
        if self.name == "project.pending":
            raise OSError("synthetic full disk")
        return original(self, target)

    monkeypatch.setattr(type(folder), "replace", refuse)
    assert working_existing.reconcile_snapshots(store)["failed"] == 1
    assert store.recording(rid)["snapshot_error"]
    assert snapshot_role(store, project_id, rid)["working"] is None
    monkeypatch.setattr(type(folder), "replace", original)
    # A later start derives staleness again from the durable role event.
    assert working_existing.reconcile_snapshots(store)["refreshed"] == 1
    assert store.recording(rid)["snapshot_error"] is None
    assert snapshot_role(store, project_id, rid) == role(store, rid)


def test_snapshot_budget_leaves_a_visible_pending_warning(store):
    project_id, rid, _ = stale_project(store)
    assert working_existing.reconcile_snapshots(store, seconds=0)["pending"] == 1
    assert store.recording(rid)["snapshot_error"] == working_existing.PENDING
    assert working_existing.reconcile_snapshots(store)["refreshed"] == 1
    assert store.recording(rid)["snapshot_error"] is None


def test_restored_library_snapshots_are_not_reconciled(store):
    project_id, rid, folder = stale_project(store)
    before = files(folder)
    (store.root / "preservation").mkdir()
    assert working_existing.reconcile_snapshots(store)["refreshed"] == 0
    assert files(folder) == before and store.snapshot_errors == {}


def test_large_history_scans_jobs_once(store):
    """Synthetic scale check: candidates are paged by key; jobs are scanned once."""
    project = store.create_project("Synthetic")
    template = add(store, recording(store, project)["id"])
    rows = store.one("SELECT * FROM transcript_results WHERE id=?", (template["id"],))
    version = store.one("SELECT * FROM result_versions WHERE result_id=?", (template["id"],))
    source = store.one("SELECT * FROM recordings WHERE id=?", (template["recording_id"],))
    count, jobs_per = 400, 10
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for index in range(count):
            rid, tid, vid = (hashlib.md5(f"{kind}{index}".encode()).hexdigest() for kind in "rtv")
            db.execute(
                "INSERT INTO recordings(id,project_id,name,filename,created,segments,speakers,"
                "revision,active_transcript_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    project["id"],
                    f"r{index}",
                    "r.wav",
                    source["created"],
                    source["segments"],
                    source["speakers"],
                    source["revision"],
                    tid,
                ),
            )
            db.execute(
                "INSERT INTO transcript_results VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    rid,
                    "Synthetic",
                    rows["source"],
                    rows["segments"],
                    rows["speakers"],
                    None,
                    rows["created"],
                    rows["updated"],
                ),
            )
            db.execute(
                "INSERT INTO result_versions(id,result_id,sequence,predecessor_id,"
                "source_version_id,payload,payload_sha256,observed_at,action,context,actor_kind,"
                "reason) VALUES (?,?,1,NULL,NULL,?,?,?,?,?,?,NULL)",
                (
                    vid,
                    tid,
                    version["payload"],
                    version["payload_sha256"],
                    version["observed_at"],
                    version["action"],
                    version["context"],
                    version["actor_kind"],
                ),
            )
            for job in range(jobs_per):
                db.execute(
                    "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
                    "VALUES (?,?,'transcribe','completed','{}',?,?)",
                    (f"{index:016x}{job:016x}", rid, source["created"], source["created"]),
                )
    summary = working_existing.designate_existing(store, page=64)
    assert summary["designated"] == count + 1
    assert summary["job_scans"] == 1 and summary["pages"] == 7
