"""Synthetic schema-12 allocation ownership and atomic result/admission receipts (V113–116)."""

import copy
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from prior_schema import remove_later_additions, without_added_languages

from cbs_transcriber import providers, runtime_store
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import SCHEMA_VERSION, Store, new_id, now
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    project = store.create_project("Synthetic runtime study")
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://runtime.invalid",
        model="large-v3",
        approved_for=["protected"],
    )
    provider_id = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (provider_id, "Synthetic runtime", settings.model_dump_json(), now()),
    )
    params = {
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
    return store, project, params


def recording(store, project, name="synthetic.wav"):
    row = store.create_recording(project["id"], name)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (row["id"],))
    return store.recording(row["id"])


def enqueue(library, *, params=None, project=None):
    store, default_project, default_params = library
    row = recording(store, project or default_project)
    return store.add_job(row["id"], "enhance", copy.deepcopy(params or default_params))


def old_schema11(root):
    store = Store(root)
    store.create_project("Preserved schema-11 study")
    for table in ("runtime_batch_attempts", "runtime_batch_members", "runtime_batch_runs"):
        store.execute(f"DROP TABLE {table}")
    with store.connection() as db:
        remove_later_additions(db, 11)
    store.execute("PRAGMA user_version=11")
    return store


def test_schema11_migration_preserves_rows_backup_and_reopen_once(tmp_path):
    old = old_schema11(tmp_path)
    tables = [r["name"] for r in old.all("SELECT name FROM sqlite_master WHERE type='table'")]
    before = {t: old.all(f'SELECT * FROM "{t}"') for t in tables}
    upgraded = Store(tmp_path)
    assert upgraded.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert {
        t: without_added_languages(t, upgraded.all(f'SELECT * FROM "{t}"')) for t in tables
    } == before
    with upgraded.connection() as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    backups = list((tmp_path / "backups").glob("schema-11-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='runtime_batch_runs'"
        ).fetchall()
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-11-*.sqlite3"))) == 1


def test_migration_lease_precedes_wal_or_schema_writes(tmp_path):
    old = old_schema11(tmp_path)
    with old.library_lock():
        with pytest.raises(RuntimeError, match="already open"):
            Store(tmp_path)
    assert old.one("PRAGMA user_version")["user_version"] == 11
    assert not old.all("SELECT name FROM sqlite_master WHERE name LIKE 'runtime_batch_%'")
    current = Store(tmp_path)
    with current.library_lock():
        # A current Store can be opened for ordinary same-process read-only use.
        assert Store(tmp_path).all("SELECT id FROM projects") == current.all(
            "SELECT id FROM projects"
        )
        with pytest.raises(RuntimeError, match="already open"):
            with Store(tmp_path).library_lock():
                pass


def test_failed_migration_rolls_back_all_three_tables(tmp_path, monkeypatch):
    old_schema11(tmp_path)
    original = Store.connection

    class FailingConnection:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, *args):
            if "CREATE TABLE IF NOT EXISTS runtime_batch_attempts" in sql:
                raise sqlite3.OperationalError("synthetic migration failure")
            return self.db.execute(sql, *args)

        def backup(self, destination):
            return self.db.backup(destination)

    @contextmanager
    def failing(store):
        with original(store) as db:
            yield FailingConnection(db)

    monkeypatch.setattr(Store, "connection", failing)
    with pytest.raises(sqlite3.OperationalError, match="synthetic"):
        Store(tmp_path)
    with sqlite3.connect(tmp_path / "library.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'runtime_batch_%'"
        ).fetchall()


def test_schema11_backup_failure_never_starts_migration(tmp_path, monkeypatch):
    old_schema11(tmp_path)
    original = sqlite3.connect

    def refuse_backup(path, *args, **kwargs):
        if "schema-11-" in str(path):
            raise OSError("synthetic backup refusal")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", refuse_backup)
    with pytest.raises(OSError, match="backup refusal"):
        Store(tmp_path)
    with original(tmp_path / "library.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 11
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'runtime_batch_%'"
        ).fetchall()


def test_exact_groups_and_member_claims_leave_legacy_lanes_unchanged(library):
    store, project, params = library
    a, b = enqueue(library), enqueue(library)
    batch = runtime_store.for_job(store, a)
    assert runtime_store.for_job(store, b)["id"] == batch["id"]
    assert [m["job_id"] for m in batch["members"]] == [a["id"], b["id"]]
    assert len({m["task_id"] for m in batch["members"]}) == 2
    assert {m["attempt"] for m in batch["members"]} == {1}
    assert store.claim_job("enhance-remote") is None
    legacy = copy.deepcopy(params)
    del legacy["ucloud_api"]["batch_plan"]
    singleton = enqueue(library, params=legacy)
    assert "runtime_batch_id" not in singleton["params"]
    assert store.claim_job("enhance-remote")["id"] == singleton["id"]
    claimed = runtime_store.claim(store)
    assert claimed["coordinator_epoch"] == 1
    assert all(m["state"] == "preparing" for m in claimed["members"])
    c = enqueue(library)
    assert c["params"]["runtime_batch_id"] == batch["id"]
    assert runtime_store.claim_member(store, batch["id"], c["id"], 1)["state"] == "preparing"
    assert runtime_store.claim_member(store, batch["id"], c["id"], 1) is None
    assert runtime_store.seal(store, batch["id"], 1, 2) is None
    assert runtime_store.seal(store, batch["id"], 1, 3)["seal_digest"]
    assert enqueue(library)["params"]["runtime_batch_id"] != batch["id"]
    other = store.create_project("Other study")
    assert enqueue(library, project=other)["params"]["runtime_batch_id"] != batch["id"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("application", {"name": "Terminal", "version": "other"}),
        ("code_hashes", {"worker": "f" * 64}),
        ("model_revisions", {"large-v3": "other"}),
        ("recipe_hash", "f" * 64),
    ],
)
def test_each_frozen_protocol_identity_change_splits_groups(library, field, value):
    store, _, params = library
    a = enqueue(library)
    changed = copy.deepcopy(params)
    changed["ucloud_api"]["batch_plan"]["identity"][field] = value
    b = enqueue(library, params=changed)
    assert a["params"]["runtime_batch_id"] != b["params"]["runtime_batch_id"]
    assert runtime_store.for_job(store, b)


def test_duplicate_and_mixed_enqueue_failure_roll_back_every_owner_and_job(library):
    store, project, params = library
    row = recording(store, project)
    with pytest.raises(ValueError, match="active work"):
        store.add_transcription_jobs(
            [
                {"recording_id": row["id"], "kind": "enhance", "params": params},
                {"recording_id": row["id"], "kind": "enhance", "params": params},
            ]
        )
    assert not store.all("SELECT * FROM jobs")
    assert not store.all("SELECT * FROM runtime_batch_runs")
    bad = copy.deepcopy(params)
    bad["ucloud_api"]["runtime_compute_policy"] = "bad"
    with pytest.raises(ValueError, match="compute identity"):
        store.add_transcription_jobs(
            [
                {"recording_id": row["id"], "params": {}},
                {"recording_id": row["id"], "kind": "enhance", "params": bad},
            ]
        )
    assert not store.all("SELECT * FROM jobs")


def test_source_freeze_and_followup_join_rollback_with_the_provider_result(library):
    store, project, params = library
    row = recording(store, project)
    source = normalize_segments([{"text": "Øresund: e\u0301 👋", "start": 0, "end": 3}])
    reviewed = store.add_result(row["id"], source, {}, "Reviewed", {})
    child_params = copy.deepcopy(params)
    child_params.pop("transcription")
    child_params["enhancement"]["transcribe"] = False
    job = store.enqueue_enhancement(row["id"], reviewed["id"], 1, child_params)
    frozen = job["params"]["source_snapshot"]
    assert (
        runtime_store.for_job(store, job)["members"][0]["source_identity"]["source_version_id"]
        == frozen["source_version_id"]
    )
    store.save_transcript(row["id"], normalize_segments([{"text": "Later edits"}]), {}, 1)
    assert runtime_store.for_job(store, job)
    with pytest.raises(ValueError, match="another window"):
        store.enqueue_enhancement(row["id"], reviewed["id"], 1, child_params)
    other = recording(store, project)
    child_id = new_id()
    parent = store.add_job(
        other["id"],
        "transcribe",
        {
            "followup": {"id": child_id, "params": child_params},
        },
    )
    store.execute(
        "CREATE TRIGGER reject_runtime_member BEFORE INSERT ON runtime_batch_members "
        "BEGIN SELECT RAISE(ABORT,'synthetic member failure'); END"
    )
    before = store.all("SELECT * FROM runtime_batch_runs")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic"):
        store.add_result(other["id"], source, {}, "Provider", {}, job_id=parent["id"])
    assert not store.results(other["id"])
    assert not store.all("SELECT id FROM jobs WHERE id=?", (child_id,))
    assert store.all("SELECT * FROM runtime_batch_runs") == before
    store.execute("DROP TRIGGER reject_runtime_member")
    produced = store.add_result(other["id"], source, {}, "Provider", {}, job_id=parent["id"])
    child = store.job(child_id)
    assert child["params"]["source_snapshot"]["id"] == produced["id"]
    assert runtime_store.for_job(store, child)["id"] == job["params"]["runtime_batch_id"]


def test_stop_fences_late_publication_and_epoch_and_preserves_shared_scope(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    member = batch["members"][0]
    stopped = runtime_store.stop(store, batch["id"])
    assert stopped["admission"] == "closed" and stopped["journal"]["stop_intent"]
    assert stopped["members"][0]["state"] == "cancelled"
    with pytest.raises(ValueError, match="stopped"):
        runtime_store.checkpoint_attempt(
            store,
            batch["id"],
            job["id"],
            1,
            member["task_id"],
            input_update={"publish_intent": True},
        )
    with pytest.raises(ValueError, match="cannot be cleared"):
        runtime_store.checkpoint(store, batch["id"], 1, {"stop_intent": False})
    store.recover_jobs()
    with pytest.raises(ValueError, match="no longer owns"):
        runtime_store.checkpoint(store, batch["id"], 1, message="Stale writer")
    assert runtime_store.obligations(store, recording_ids=[job["recording_id"]])


def test_capacity_reservations_include_legacy_uncertainty_and_ignore_stop_ack(library):
    store, _, params = library
    runtime_store.save_settings(store, 1)
    legacy = copy.deepcopy(params)
    del legacy["ucloud_api"]["batch_plan"]
    old = enqueue(library, params=legacy)
    assert runtime_store.reserve_capacity(store, old["id"], legacy=True)
    new = enqueue(library)
    assert runtime_store.claim(store) is None
    old["params"]["ucloud_run"] = {"submission_intent": True, "termination_requested": True}
    store.update_job(old["id"], params=old["params"])
    with pytest.raises(ValueError, match="remains reserved"):
        runtime_store.release_capacity(store, old["id"], legacy=True)
    assert runtime_store.claim(store) is None
    old["params"]["ucloud_run"]["reconciled_not_created"] = True
    store.update_job(old["id"], params=old["params"])
    runtime_store.release_capacity(store, old["id"], legacy=True)
    assert runtime_store.claim(store)["id"] == new["params"]["runtime_batch_id"]
    for invalid in (True, 0, 5, "2"):
        with pytest.raises(ValueError):
            runtime_store.save_settings(store, invalid)


def outputs(store, job, batch):
    folder = store.directory(job["recording_id"]) / "enhancements" / job["id"]
    folder.mkdir(parents=True)
    data = b'{"passages":[]}'
    files = {}
    for name in ("alignment.json", "transcription.json"):
        (folder / name).write_bytes(data)
        files[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    member = next(m for m in batch["members"] if m["job_id"] == job["id"])
    return {
        "batch_id": batch["id"],
        "task_id": member["task_id"],
        "attempt": member["attempt"],
        "epoch": batch["epoch"],
        "files": files,
    }


def test_result_receipt_is_atomic_survives_stop_and_never_restores_trash(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    receipt = outputs(store, job, batch)
    runtime_store.stop(store, batch["id"])
    store.execute(
        "CREATE TRIGGER reject_receipt BEFORE UPDATE ON runtime_batch_members "
        "WHEN NEW.state='completed' BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END"
    )
    segments = normalize_segments([{"text": "Valid completed output", "start": 0, "end": 3}])
    with pytest.raises(sqlite3.IntegrityError, match="synthetic receipt"):
        store.add_result(
            job["recording_id"],
            segments,
            {},
            "Runtime",
            {},
            job_id=job["id"],
            runtime_receipt=receipt,
        )
    assert not store.results(job["recording_id"])
    assert not store.all("SELECT id FROM result_versions")
    store.execute("DROP TRIGGER reject_receipt")
    saved = store.add_result(
        job["recording_id"], segments, {}, "Runtime", {}, job_id=job["id"], runtime_receipt=receipt
    )
    assert runtime_store.get(store, batch["id"])["members"][0]["result_id"] == saved["id"]
    assert store.job(job["id"])["state"] == "completed"
    store.set_result_trashed(job["recording_id"], saved["id"], 1, trashed=True)
    duplicate = store.add_result(
        job["recording_id"], [], {}, "Ignored", {}, job_id=job["id"], runtime_receipt=receipt
    )
    assert duplicate["id"] == saved["id"] and not store.results(job["recording_id"])
    assert len(store.result_versions(job["recording_id"], saved["id"])) == 2


def test_missing_or_corrupt_output_receipt_cannot_commit_result(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    receipt = outputs(store, job, batch)
    receipt["files"]["alignment.json"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash differs"):
        store.add_result(
            job["recording_id"], [], {}, "Runtime", {}, job_id=job["id"], runtime_receipt=receipt
        )
    assert not store.results(job["recording_id"])
    assert runtime_store.get(store, batch["id"])["members"][0]["result_id"] is None


def test_explicit_retry_requires_terminal_evidence_and_preserves_prior_attempt(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    member = batch["members"][0]
    with pytest.raises(ValueError, match="Reconnect"):
        runtime_store.retry_member(store, batch["id"], job["id"], 1, task_id=member["task_id"])
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        1,
        member["task_id"],
        state="failed",
        member_state="failed",
        remote_update={"outcome": "failed", "worker_gone": True},
    )
    prior = store.one("SELECT * FROM runtime_batch_attempts WHERE job_id=?", (job["id"],))
    retried = runtime_store.retry_member(
        store, batch["id"], job["id"], 1, task_id=member["task_id"]
    )
    assert retried["attempt"] == 2 and retried["task_id"] != member["task_id"]
    assert (
        store.one("SELECT * FROM runtime_batch_attempts WHERE job_id=? AND attempt=1", (job["id"],))
        == prior
    )
    assert runtime_store.seal(store, batch["id"], 1, 1) is None
    assert runtime_store.seal(store, batch["id"], 1, 3)
    with pytest.raises(ValueError, match="closed"):
        runtime_store.retry_member(store, batch["id"], job["id"], 1, task_id=retried["task_id"])


def test_interrupted_owner_reconnect_does_not_post_or_reopen_sealed_membership(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    runtime_store.checkpoint(
        store, batch["id"], 1, {"job_id": "exact-remote-id", "readiness_generation": "generation"}
    )
    runtime_store.seal(store, batch["id"], 1, 1)
    store.recover_jobs()
    interrupted = runtime_store.get(store, batch["id"])
    assert interrupted["state"] == "interrupted" and interrupted["admission"] == "sealed"
    assert runtime_store.claim(store) is None
    with pytest.raises(ValueError, match="identity/generation"):
        runtime_store.reconnect(store, batch["id"], interrupted["epoch"], remote_status={})
    resumed = runtime_store.reconnect(
        store,
        batch["id"],
        interrupted["epoch"],
        remote_status={
            "job_id": "exact-remote-id",
            "generation": "generation",
            "state": "RUNNING",
            "accepting": True,
        },
    )
    assert resumed["epoch"] == interrupted["epoch"] + 1 and resumed["admission"] == "sealed"
    assert resumed["members"][0]["task_id"] == batch["members"][0]["task_id"]
    assert runtime_store.for_job(store, job)


def test_owners_and_retained_obligations_survive_recording_deletion(library):
    store, project, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    runtime_store.checkpoint(
        store,
        batch["id"],
        1,
        {
            "owned_folder": "/synthetic-owned-folder",
            "cleanup_state": "retained",
            "remote_state": "FAILURE",
        },
        state="failed",
    )
    store.execute("DELETE FROM recordings WHERE id=?", (job["recording_id"],))
    retained = runtime_store.get(store, batch["id"])
    assert retained["members"][0]["recording_id"] == job["recording_id"]
    assert runtime_store.obligations(store, project_id=project["id"])[0]["id"] == batch["id"]
    assert runtime_store.obligations(store, recording_ids=[job["recording_id"]])


def test_recovery_request_is_claimed_once_even_when_existing_owner_reserves_capacity(library):
    store, _, params = library
    runtime_store.save_settings(store, 1)
    first = enqueue(library)
    batch = runtime_store.claim(store)
    runtime_store.checkpoint(
        store, batch["id"], 1, {"job_id": "exact-remote", "readiness_generation": "generation"}
    )
    changed = copy.deepcopy(params)
    changed["enhancement"]["language"] = "en"
    waiting = enqueue(library, params=changed)
    store.recover_jobs()
    stale = runtime_store.get(store, batch["id"])
    runtime_store.queue_recovery(
        store,
        batch["id"],
        stale["epoch"],
        remote_status={
            "job_id": "exact-remote",
            "generation": "generation",
            "state": "RUNNING",
            "accepting": True,
        },
    )
    barrier = threading.Barrier(4)

    def claim_once():
        barrier.wait()
        return runtime_store.claim(store)

    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda _: claim_once(), range(4)))
    owners = [b for b in claimed if b]
    assert len(owners) == 1 and owners[0]["id"] == batch["id"]
    assert owners[0]["epoch"] == stale["epoch"] + 1
    assert owners[0]["admission"] == "open"
    assert owners[0]["members"][0]["task_id"] == batch["members"][0]["task_id"]
    assert store.job(first["id"])["state"] == "running"
    assert store.job(waiting["id"])["state"] == "queued"


def test_attach_and_seal_race_respects_the_winning_membership_transaction(library):
    store, project, params = library
    initial = enqueue(library)
    owner = runtime_store.claim(store)
    late = recording(store, project)
    barrier = threading.Barrier(2)

    def attach_late():
        barrier.wait()
        return store.add_job(late["id"], "enhance", params)

    def seal_current():
        barrier.wait()
        return runtime_store.seal(store, owner["id"], owner["epoch"], 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        attached, sealed = executor.submit(attach_late), executor.submit(seal_current)
        attached, sealed = attached.result(), sealed.result()
    current = runtime_store.get(store, initial["params"]["runtime_batch_id"])
    if sealed:
        assert attached["params"]["runtime_batch_id"] != current["id"]
        assert current["membership_revision"] == 1
    else:
        assert attached["params"]["runtime_batch_id"] == current["id"]
        assert current["membership_revision"] == 2 and current["admission"] == "open"


def test_exact_membership_bytes_include_prior_attempts_and_cannot_change_after_seal(library):
    store, _, _ = library
    job = enqueue(library)
    owner = runtime_store.claim(store)
    member = owner["members"][0]
    runtime_store.checkpoint_attempt(
        store,
        owner["id"],
        job["id"],
        1,
        member["task_id"],
        state="failed",
        member_state="failed",
        remote_update={"outcome": "failed", "worker_gone": True},
    )
    retried = runtime_store.retry_member(
        store, owner["id"], job["id"], 1, task_id=member["task_id"]
    )
    envelope = runtime_store.membership(store, owner["id"])
    assert [t["attempt"] for t in envelope["tasks"]] == [1, 2]
    assert envelope["tasks"][0]["disposition"] == "not_dispatched"
    assert envelope["current_attempts"] == {job["id"]: retried["task_id"]}
    raw = json.dumps(envelope, indent=2).encode("utf-8")
    revision = envelope["revision"]
    damaged = copy.deepcopy(envelope)
    damaged["tasks"].pop(0)
    with pytest.raises(ValueError, match="exact durable attempts"):
        runtime_store.seal(
            store, owner["id"], 1, revision, membership_bytes=json.dumps(damaged).encode()
        )
    sealed = runtime_store.seal(store, owner["id"], 1, revision, membership_bytes=raw)
    assert sealed["seal_digest"] == hashlib.sha256(raw).hexdigest()
    assert runtime_store.seal(store, owner["id"], 1, revision, membership_bytes=raw)
    with pytest.raises(ValueError, match="cannot change"):
        runtime_store.seal(store, owner["id"], 1, revision, membership_bytes=raw + b"\n")


def test_frozen_source_pointer_and_deadline_checks_cannot_be_bypassed(library):
    store, _, params = library
    first = enqueue(library)
    batch = runtime_store.claim(store)
    runtime_store.checkpoint(store, batch["id"], 1, {"deadline_at": "2000-01-01T00:00:00+00:00"})
    assert enqueue(library)["params"]["runtime_batch_id"] != batch["id"]
    changed = copy.deepcopy(first["params"])
    changed["ucloud_api"]["settings"]["model"] = "tampered"
    store.update_job(first["id"], params=changed)
    with pytest.raises(ValueError, match="identity disagrees"):
        runtime_store.for_job(store, first["id"])
    store.update_job(first["id"], params=first["params"])
    with pytest.raises(ValueError, match="cannot be released"):
        runtime_store.checkpoint(store, batch["id"], 1, {"capacity_reserved": False})
    runtime_store.stop(store, batch["id"])
    # A lower slot setting changes future admission; the recorded owner remains recoverable.
    runtime_store.save_settings(store, 1)
    assert runtime_store.for_job(store, first)


def test_existing_producer_result_repairs_member_projection_without_restoring_trash(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    receipt = outputs(store, job, batch)
    result = store.add_result(
        job["recording_id"], [], {}, "Saved", {}, job_id=job["id"], runtime_receipt=receipt
    )
    store.set_result_trashed(job["recording_id"], result["id"], 1, trashed=True)
    store.execute(
        "UPDATE runtime_batch_members SET result_id=NULL,state='interrupted' WHERE job_id=?",
        (job["id"],),
    )
    store.update_job(job["id"], state="interrupted")
    repaired = store.add_result(
        job["recording_id"], [], {}, "Ignored", {}, job_id=job["id"], runtime_receipt=receipt
    )
    assert repaired["id"] == result["id"] and not store.results(job["recording_id"])
    assert runtime_store.get(store, batch["id"])["members"][0]["state"] == "completed"
    assert store.job(job["id"])["state"] == "completed"


def test_interrupt_fences_all_active_claims_but_keeps_failed_siblings_and_attempt_receipts(library):
    store, _, _ = library
    first, failed = enqueue(library), enqueue(library)
    batch = runtime_store.claim(store)
    runtime_store.checkpoint(
        store, batch["id"], 1, {"job_id": "remote-owned", "readiness_generation": "generation"}
    )
    late = enqueue(library)
    member = next(m for m in batch["members"] if m["job_id"] == failed["id"])
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        failed["id"],
        1,
        member["task_id"],
        state="failed",
        member_state="failed",
        remote_update={"outcome": "failed"},
        error_code="processing",
    )
    attempt = store.one("SELECT * FROM runtime_batch_attempts WHERE job_id=?", (first["id"],))
    interrupted = runtime_store.interrupt(store, batch["id"], 1, "Synthetic connection loss")
    assert interrupted["epoch"] == 2
    assert (
        store.one("SELECT * FROM runtime_batch_attempts WHERE job_id=?", (first["id"],)) == attempt
    )
    assert store.job(first["id"])["state"] == "interrupted"
    assert store.job(failed["id"])["state"] == "failed"
    assert store.job(late["id"])["state"] == "interrupted"
    with pytest.raises(ValueError, match="no longer owns"):
        runtime_store.interrupt(store, batch["id"], 1, "Late failure")
    runtime_store.queue_recovery(
        store,
        batch["id"],
        2,
        remote_status={
            "job_id": "remote-owned",
            "generation": "generation",
            "state": "RUNNING",
            "accepting": True,
        },
    )
    recovered = runtime_store.claim(store)
    assert recovered["epoch"] == 3
    states = {m["job_id"]: m["state"] for m in recovered["members"]}
    assert states == {first["id"]: "preparing", failed["id"]: "failed", late["id"]: "preparing"}
    assert store.job(first["id"])["state"] == store.job(late["id"])["state"] == "running"


def test_previously_frozen_followup_survives_model_disable_but_not_revoked_approval(library):
    store, project, params = library
    row = recording(store, project)
    child_params = copy.deepcopy(params)
    child_params.pop("transcription")
    child_params["enhancement"]["transcribe"] = False
    child_id = new_id()
    parent = store.add_job(
        row["id"],
        "transcribe",
        {
            "followup": {"id": child_id, "params": child_params},
        },
    )
    disabled = {**params["ucloud_api"]["settings"], "enabled": False}
    store.execute(
        "UPDATE providers SET value=? WHERE id=?",
        (json.dumps(disabled), params["ucloud_api"]["provider_id"]),
    )
    store.add_result(row["id"], [], {}, "Provider", {}, job_id=parent["id"])
    assert runtime_store.for_job(store, child_id)
    with pytest.raises(ValueError, match="changed after preflight"):
        enqueue(library)
    protected = store.create_project("Protected frozen work", classification="protected")
    target = recording(store, protected)
    refused_id = new_id()
    original = store.add_job(
        target["id"],
        "transcribe",
        {
            "followup": {"id": refused_id, "params": child_params},
        },
    )
    disabled["approved_for"] = []
    store.execute(
        "UPDATE providers SET value=? WHERE id=?",
        (json.dumps(disabled), params["ucloud_api"]["provider_id"]),
    )
    with pytest.raises(ValueError, match="explicitly approved"):
        store.add_result(target["id"], [], {}, "Provider", {}, job_id=original["id"])
    assert not store.results(target["id"])
    assert not store.all("SELECT id FROM jobs WHERE id=?", (refused_id,))


def test_saved_result_receipt_remains_idempotent_when_local_raw_file_is_later_lost(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    receipt = outputs(store, job, batch)
    saved = store.add_result(
        job["recording_id"], [], {}, "Saved", {}, job_id=job["id"], runtime_receipt=receipt
    )
    (store.directory(job["recording_id"]) / "enhancements" / job["id"] / "alignment.json").unlink()
    duplicate = store.add_result(job["recording_id"], [], {}, "Ignored", {}, job_id=job["id"])
    assert duplicate["id"] == saved["id"]
    assert len(store.result_versions(job["recording_id"], saved["id"])) == 1
    with pytest.raises(ValueError, match="missing or invalid"):
        runtime_store._verify_files(store, job["id"], job["recording_id"], receipt["files"])


def test_stop_resolves_interrupted_unpublished_members_after_proven_pre_post_failure(library):
    store, _, _ = library
    job = enqueue(library)
    owner = runtime_store.claim(store)
    interrupted = runtime_store.interrupt(store, owner["id"], owner["epoch"], "Before POST")
    stopped = runtime_store.stop(store, owner["id"])
    assert stopped["members"][0]["state"] == "cancelled"
    assert stopped["members"][0]["attempt_state"] == "cancelled"
    assert store.job(job["id"])["state"] == "cancelled"
    runtime_store.checkpoint(
        store, owner["id"], interrupted["epoch"], {"no_post": True}, state="cancelled"
    )
    runtime_store.release_capacity(store, owner["id"], epoch=interrupted["epoch"])
    assert runtime_store.obligations(store) == []


def test_stop_does_not_invent_absence_for_interrupted_published_inputs(library):
    store, _, _ = library
    job = enqueue(library)
    owner = runtime_store.claim(store)
    member = owner["members"][0]
    runtime_store.checkpoint_attempt(
        store,
        owner["id"],
        job["id"],
        owner["epoch"],
        member["task_id"],
        state="publishing",
        member_state="uploading",
        input_update={"publish_intent": True},
    )
    runtime_store.interrupt(store, owner["id"], owner["epoch"], "Unknown input acceptance")
    stopped = runtime_store.stop(store, owner["id"])
    assert stopped["members"][0]["state"] == "interrupted"
    assert stopped["members"][0]["input_receipt"]["publish_intent"] is True
    assert runtime_store.obligations(store)


def test_legacy_submission_slot_and_intent_commit_or_roll_back_together(library):
    store, _, params = library
    legacy = copy.deepcopy(params)
    del legacy["ucloud_api"]["batch_plan"]
    old = enqueue(library, params=legacy)
    store.execute(
        "CREATE TRIGGER refuse_submission_intent BEFORE UPDATE ON jobs "
        "WHEN json_extract(NEW.params,'$.ucloud_run.submission_intent')=1 "
        "BEGIN SELECT RAISE(ABORT,'synthetic intent failure'); END"
    )
    before = store.job(old["id"])
    with pytest.raises(sqlite3.IntegrityError, match="synthetic intent"):
        runtime_store.begin_legacy_submission(store, old["id"])
    assert store.job(old["id"]) == before
    store.execute("DROP TRIGGER refuse_submission_intent")
    assert runtime_store.begin_legacy_submission(store, old["id"])
    journal = store.job(old["id"])["params"]["ucloud_run"]
    assert journal["capacity_reserved"] and journal["submission_intent"]
    assert journal["submission_attempted_at"] and journal["reconciled_not_created"] is False
    with pytest.raises(ValueError, match="uncertain"):
        runtime_store.begin_legacy_submission(store, old["id"])
    runtime_store.save_settings(store, 1)
    other = enqueue(library, params=legacy)
    before = store.job(other["id"])
    assert runtime_store.begin_legacy_submission(store, other["id"]) is False
    assert store.job(other["id"]) == before
    original = store.job(old["id"])
    original["params"]["ucloud_run"].update(
        reconciled_not_created=True, capacity_reserved=False, no_post=True
    )
    store.update_job(old["id"], params=original["params"])
    assert runtime_store.begin_legacy_submission(store, old["id"])
    assert store.job(old["id"])["params"]["ucloud_run"]["no_post"] is False


def test_terminal_attempt_receipts_cannot_be_rewritten_by_a_late_status_reply(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    member = batch["members"][0]
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        1,
        member["task_id"],
        state="failed",
        member_state="failed",
        remote_update={"outcome": "failed"},
    )
    with pytest.raises(ValueError, match="terminal runtime task receipt"):
        runtime_store.checkpoint_attempt(
            store,
            batch["id"],
            job["id"],
            1,
            member["task_id"],
            remote_update={"outcome": "complete"},
        )


def test_remote_terminal_owner_refuses_late_admission_before_local_collection_finishes(library):
    store, _, _ = library
    first = enqueue(library)
    owner = runtime_store.claim(store)
    runtime_store.checkpoint(
        store, owner["id"], owner["epoch"], {"job_id": "finished-remote", "remote_state": "SUCCESS"}
    )
    assert runtime_store.get(store, owner["id"])["state"] == "running"
    late = enqueue(library)
    assert late["params"]["runtime_batch_id"] != first["params"]["runtime_batch_id"]


def test_new_retry_attempt_atomically_clears_only_its_prior_cached_output_receipt(library):
    store, project, params = library
    row = recording(store, project)
    source = normalize_segments([{"text": "Exact Øresund source", "start": 0, "end": 3}])
    result = store.add_result(row["id"], source, {}, "Reviewed", {})
    options = copy.deepcopy(params)
    options.pop("transcription")
    options["enhancement"]["transcribe"] = False
    options["routing"] = {"mode": "auto", "reason": "Frozen route"}
    job = store.enqueue_enhancement(row["id"], result["id"], 1, options)
    batch = runtime_store.claim(store)
    member = batch["members"][0]
    old_receipt = {
        "batch_id": batch["id"],
        "task_id": member["task_id"],
        "attempt": 1,
        "epoch": batch["epoch"],
        "files": {},
    }
    saved_params = {**job["params"], "runtime_output_receipt": old_receipt}
    store.update_job(job["id"], params=saved_params)
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        batch["epoch"],
        member["task_id"],
        state="failed",
        member_state="failed",
        remote_update={"outcome": "failed", "worker_gone": True},
    )
    store.execute(
        "CREATE TRIGGER refuse_retry BEFORE UPDATE ON jobs WHEN NEW.state='queued' "
        "BEGIN SELECT RAISE(ABORT,'synthetic retry failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic retry"):
        runtime_store.retry_member(
            store, batch["id"], job["id"], batch["epoch"], task_id=member["task_id"]
        )
    assert store.job(job["id"])["params"] == saved_params
    assert runtime_store.get(store, batch["id"])["members"][0]["attempt"] == 1
    store.execute("DROP TRIGGER refuse_retry")
    retried = runtime_store.retry_member(
        store, batch["id"], job["id"], batch["epoch"], task_id=member["task_id"]
    )
    assert retried["attempt"] == 2 and retried["task_id"] != member["task_id"]
    assert store.job(job["id"])["params"] == job["params"]
    with pytest.raises(ValueError, match="another member attempt"):
        store.add_result(
            row["id"], source, {}, "Stale output", {}, job_id=job["id"], runtime_receipt=old_receipt
        )
    assert len(store.results(row["id"])) == 1


def unsaved_completed_task(library):
    store, _, _ = library
    job, sibling = enqueue(library), enqueue(library)
    batch = runtime_store.claim(store)
    member = next(m for m in batch["members"] if m["job_id"] == job["id"])
    receipt = outputs(store, job, batch)
    store.update_job(job["id"], params={**job["params"], "runtime_output_receipt": receipt})
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        batch["epoch"],
        member["task_id"],
        state="complete",
        member_state="collecting",
        remote_update={
            "outcome": "complete",
            "complete": True,
            "worker_gone": True,
            "files": receipt["files"],
        },
    )
    store.execute(
        "CREATE TRIGGER refuse_result_import BEFORE INSERT ON transcript_results "
        "BEGIN SELECT RAISE(ABORT,'synthetic import failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic import"):
        store.add_result(
            job["recording_id"], [], {}, "Runtime", {}, job_id=job["id"], runtime_receipt=receipt
        )
    store.execute("DROP TRIGGER refuse_result_import")
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        batch["epoch"],
        member["task_id"],
        state="complete",
        member_state="failed",
        error_code="save_failed",
    )
    return store, job, sibling, batch, member, receipt


def test_live_owner_retries_only_completed_cached_result_without_new_attempt_or_approval(library):
    store, job, sibling, batch, member, receipt = unsaved_completed_task(library)
    before = store.one("SELECT * FROM runtime_batch_attempts WHERE job_id=?", (job["id"],))
    sealed = runtime_store.seal(store, batch["id"], batch["epoch"], batch["membership_revision"])
    # Existing output collection remains possible after an approval/model flag is changed.
    api = job["params"]["ucloud_api"]
    settings = {**api["settings"], "enabled": False, "approved_for": []}
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (json.dumps(settings), api["provider_id"])
    )
    queued = runtime_store.queue_result_recovery(
        store, batch["id"], job["id"], batch["epoch"], task_id=member["task_id"]
    )
    assert queued["task_id"] == member["task_id"] and queued["attempt_state"] == "complete"
    assert queued["state"] == store.job(job["id"])["state"] == "queued"
    claimed = runtime_store.claim_member(store, batch["id"], job["id"], batch["epoch"])
    assert claimed["state"] == "collecting" and claimed["attempt_state"] == "complete"
    assert store.one("SELECT * FROM runtime_batch_attempts WHERE job_id=?", (job["id"],)) == before
    assert runtime_store.get(store, batch["id"])["seal_digest"] == sealed["seal_digest"]
    assert store.job(sibling["id"])["state"] == "running"
    saved = store.add_result(
        job["recording_id"],
        [],
        {},
        "Recovered cached output",
        {},
        job_id=job["id"],
        runtime_receipt=receipt,
    )
    assert runtime_store.get(store, batch["id"])["members"][0]["result_id"] == saved["id"]
    assert (
        store.one("SELECT COUNT(*) AS n FROM runtime_batch_attempts WHERE job_id=?", (job["id"],))[
            "n"
        ]
        == 1
    )


def test_cached_result_recovery_is_atomic_fenced_and_requires_actual_matching_files(library):
    store, job, _, batch, member, _ = unsaved_completed_task(library)
    with pytest.raises(ValueError, match="no longer owns"):
        runtime_store.queue_result_recovery(
            store, batch["id"], job["id"], 0, task_id=member["task_id"]
        )
    before = store.job(job["id"])
    store.execute(
        "CREATE TRIGGER refuse_cached_retry BEFORE UPDATE ON jobs WHEN NEW.state='queued' "
        "BEGIN SELECT RAISE(ABORT,'synthetic cached retry failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic cached"):
        runtime_store.queue_result_recovery(
            store, batch["id"], job["id"], batch["epoch"], task_id=member["task_id"]
        )
    assert store.job(job["id"]) == before
    assert runtime_store.get(store, batch["id"])["members"][0]["state"] == "failed"
    store.execute("DROP TRIGGER refuse_cached_retry")
    path = store.directory(job["recording_id"]) / "enhancements" / job["id"] / "alignment.json"
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="size differs"):
        runtime_store.queue_result_recovery(
            store, batch["id"], job["id"], batch["epoch"], task_id=member["task_id"]
        )
    assert store.job(job["id"]) == before


def test_claim_of_queued_cached_complete_attempt_survives_stop_without_reopening_inference(library):
    store, job, _, batch, member, _ = unsaved_completed_task(library)
    runtime_store.queue_result_recovery(
        store, batch["id"], job["id"], batch["epoch"], task_id=member["task_id"]
    )
    runtime_store.stop(store, batch["id"])
    claimed = runtime_store.claim_member(store, batch["id"], job["id"], batch["epoch"])
    assert claimed["state"] == "collecting" and claimed["attempt_state"] == "complete"
    assert claimed["task_id"] == member["task_id"] and claimed["attempt"] == 1


def test_guarded_stop_cannot_mutate_a_batch_containing_an_outside_member(library):
    store, _, _ = library
    selected, outsider = enqueue(library), enqueue(library)
    owner = runtime_store.for_job(store, selected)
    before = runtime_store.get(store, owner["id"])
    jobs_before = [store.job(selected["id"]), store.job(outsider["id"])]
    with pytest.raises(runtime_store.StopScopeChanged, match="includes other work"):
        runtime_store.stop(store, owner["id"], allowed_job_ids={selected["id"]})
    assert runtime_store.get(store, owner["id"]) == before
    assert [store.job(selected["id"]), store.job(outsider["id"])] == jobs_before
    # An explicit whole-allocation Stop keeps its existing scope.
    stopped = runtime_store.stop(store, owner["id"])
    assert stopped["journal"]["stop_intent"]
    assert all(m["state"] == "cancelled" for m in stopped["members"])


def test_guarded_stop_allows_only_the_complete_selected_member_set(library):
    store, _, _ = library
    first, second = enqueue(library), enqueue(library)
    owner = runtime_store.for_job(store, first)
    stopped = runtime_store.stop(store, owner["id"], allowed_job_ids={first["id"], second["id"]})
    assert stopped["journal"]["stop_intent"] and stopped["admission"] == "closed"
    assert all(store.job(j["id"])["state"] == "cancelled" for j in (first, second))


def test_late_attachment_and_scoped_stop_share_the_same_membership_transaction(library):
    store, project, params = library
    selected = enqueue(library)
    owner = runtime_store.for_job(store, selected)
    outsider_recording = recording(store, project)
    barrier = threading.Barrier(2)

    def attach_outsider():
        barrier.wait()
        return store.add_job(outsider_recording["id"], "enhance", params)

    def stop_selection():
        barrier.wait()
        try:
            return runtime_store.stop(store, owner["id"], allowed_job_ids={selected["id"]})
        except runtime_store.StopScopeChanged:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        late, stopped = executor.submit(attach_outsider), executor.submit(stop_selection)
        late, stopped = late.result(), stopped.result()
    latest = runtime_store.get(store, owner["id"])
    if stopped:
        assert late["params"]["runtime_batch_id"] != owner["id"]
        assert latest["journal"]["stop_intent"] and len(latest["members"]) == 1
    else:
        assert late["params"]["runtime_batch_id"] == owner["id"]
        assert not latest["journal"].get("stop_intent") and len(latest["members"]) == 2
        assert all(store.job(m["job_id"])["state"] == "queued" for m in latest["members"])
