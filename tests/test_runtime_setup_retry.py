"""A failed public setup transfer is retryable only with durable proof of no POST."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_runtime_batches import environment, run_batch
from test_runtime_store import enqueue, library

from cbs_transcriber import runtime_batches as batches
from cbs_transcriber import runtime_store

__all__ = ["environment", "library"]  # Register the reused synthetic fixtures with pytest.


def failed_setup(library):
    store, _, _ = library
    enqueue(library)
    owner = runtime_store.claim(store)
    for _ in range(4):
        enqueue(library)
    runtime_store.checkpoint(store, owner["id"], owner["epoch"], journal_update={"no_post": True})
    runtime_store.release_capacity(store, owner["id"], epoch=owner["epoch"])
    owner = runtime_store.interrupt(store, owner["id"], owner["epoch"], "Setup upload interrupted")
    return runtime_store.checkpoint(store, owner["id"], owner["epoch"], state="failed")


def snapshots(store, identifier):
    return {
        "owner": [
            dict(row)
            for row in store.all("SELECT * FROM runtime_batch_runs WHERE id=?", (identifier,))
        ],
        "members": [
            dict(row)
            for row in store.all(
                "SELECT * FROM runtime_batch_members WHERE batch_id=? ORDER BY ordinal",
                (identifier,),
            )
        ],
        "attempts": [
            dict(row)
            for row in store.all(
                "SELECT a.* FROM runtime_batch_attempts a JOIN runtime_batch_members m "
                "ON m.job_id=a.job_id WHERE m.batch_id=? ORDER BY m.ordinal,a.attempt",
                (identifier,),
            )
        ],
        "jobs": [
            dict(row)
            for row in store.all(
                "SELECT j.* FROM jobs j JOIN runtime_batch_members m ON m.job_id=j.id "
                "WHERE m.batch_id=? ORDER BY m.ordinal",
                (identifier,),
            )
        ],
    }


def test_five_file_setup_retry_is_explicit_fenced_and_preserves_frozen_bytes(library, monkeypatch):
    store, _, _ = library
    owner = failed_setup(library)
    before = snapshots(store, owner["id"])
    assert [m["state"] for m in owner["members"]] == ["interrupted", *(["queued"] * 4)]
    monkeypatch.setattr(batches, "require_code", lambda _: None)
    monkeypatch.setattr(batches, "_client", lambda *_: pytest.fail("Retry action contacted UCloud"))
    info = batches.activity(store)[0]["runtime_batch"]
    assert info["can_retry"] and info["retry_kind"] == "setup"
    batches.retry(store, None, owner["id"])
    queued = runtime_store.get(store, owner["id"])
    assert queued["state"] == "queued" and queued["epoch"] == owner["epoch"] + 1
    after = snapshots(store, owner["id"])
    assert before["owner"][0]["identity"] == after["owner"][0]["identity"]
    for previous, current in zip(before["members"], after["members"], strict=True):
        assert {k: v for k, v in previous.items() if k not in {"state", "updated"}} == {
            k: v for k, v in current.items() if k not in {"state", "updated"}
        }
    for previous, current in zip(before["jobs"], after["jobs"], strict=True):
        assert previous["params"] == current["params"]
        assert current["state"] == "queued"
    assert before["attempts"] == after["attempts"]
    with pytest.raises(ValueError):
        runtime_store.checkpoint(store, owner["id"], owner["epoch"], message="stale")
    assert not queued["journal"]["capacity_reserved"]
    claimed = runtime_store.claim(store)
    assert claimed["id"] == owner["id"] and claimed["journal"]["capacity_reserved"]
    assert all(m["state"] == "preparing" for m in claimed["members"])
    runtime_store.checkpoint(
        store, claimed["id"], claimed["epoch"], journal_update={"submission_intent": True}
    )
    assert runtime_store.get(store, claimed["id"])["journal"]["no_post"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("no_post", False),
        ("job_id", "123"),
        ("submission_intent", True),
        ("submission_attempted", True),
        ("stop_intent", True),
        ("close_intent", True),
        ("termination_requested", True),
        ("discard_confirmed", True),
        ("discard_decision", {"confirmed": True}),
        ("release_decision", {"confirmed": True}),
        ("cleanup_state", "trashed"),
        ("cleanup_state", "pending"),
        ("readiness_generation", "anything"),
        ("remote_state", "RUNNING"),
        ("submission_attempted_at", "2026-10-30T12:00:00+00:00"),
        ("capacity_reserved", True),
        ("owned_folder", "/unowned"),
        ("folder_created", True),
        ("stop_acknowledged", True),
        ("close_acknowledged", True),
        ("reconciled_not_created", True),
    ],
)
def test_setup_retry_refuses_every_remote_or_disposition_receipt_without_writes(
    library, field, value
):
    store, _, _ = library
    owner = failed_setup(library)
    journal = {**owner["journal"], field: value}
    store.execute(
        "UPDATE runtime_batch_runs SET journal=? WHERE id=?", (json.dumps(journal), owner["id"])
    )
    before = snapshots(store, owner["id"])
    with pytest.raises(ValueError):
        runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
    assert snapshots(store, owner["id"]) == before


@pytest.mark.parametrize(
    "column,value",
    [
        ("input_receipt", {"publish_intent": True}),
        ("input_receipt", {"published": True}),
        ("input_receipt", {"files": {}}),
        ("input_receipt", {"artifact_origin": {}}),
        ("remote_receipt", {"accepted": True}),
        ("remote_receipt", {"outcome": "complete"}),
        ("remote_receipt", {"files": {}}),
    ],
)
def test_setup_retry_refuses_any_attempt_receipt_without_writes(library, column, value):
    store, _, _ = library
    owner = failed_setup(library)
    store.execute(
        f"UPDATE runtime_batch_attempts SET {column}=? WHERE job_id=?",
        (json.dumps(value), owner["members"][0]["job_id"]),
    )
    before = snapshots(store, owner["id"])
    with pytest.raises(ValueError):
        runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
    assert snapshots(store, owner["id"]) == before
    with store.connection() as db:
        runtime_store.recover(db)
    before = snapshots(store, owner["id"])
    with pytest.raises(ValueError):
        runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
    assert snapshots(store, owner["id"]) == before


def test_setup_retry_has_one_concurrent_winner_and_never_skips_capacity(library):
    store, _, _ = library
    owner = failed_setup(library)
    enqueue(library)
    competing = runtime_store.claim(store)
    runtime_store.save_settings(store, 1)

    def attempt():
        try:
            return runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])["id"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results.count(owner["id"]) == 1 and results.count(None) == 1
    assert runtime_store.claim(store) is None
    assert runtime_store.get(store, competing["id"])["journal"]["capacity_reserved"]


def test_setup_retry_requires_actual_empty_receipt_objects(library):
    store, _, _ = library
    owner = failed_setup(library)
    job_id = owner["members"][0]["job_id"]
    for column in ("input_receipt", "remote_receipt", "output_receipt"):
        table = "runtime_batch_members" if column == "output_receipt" else "runtime_batch_attempts"
        for value in (None, [], False, 0, ""):
            store.execute(
                f"UPDATE {table} SET {column}=? WHERE job_id=?", (json.dumps(value), job_id)
            )
            before = snapshots(store, owner["id"])
            with pytest.raises(ValueError):
                runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
            assert snapshots(store, owner["id"]) == before
        store.execute(f"UPDATE {table} SET {column}='{{}}' WHERE job_id=?", (job_id,))
    job = store.job(job_id)
    for value in (None, {}, [], False):
        params = {**job["params"], "runtime_output_receipt": value}
        store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job_id))
        before = snapshots(store, owner["id"])
        with pytest.raises(ValueError):
            runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
        assert snapshots(store, owner["id"]) == before


def test_five_file_setup_retry_survives_startup_recovery_without_reinterpreting_tasks(
    library, monkeypatch
):
    store, _, _ = library
    owner = failed_setup(library)
    with store.connection() as db:
        runtime_store.recover(db)
    recovered = runtime_store.get(store, owner["id"])
    assert recovered["members"][0]["attempt_state"] == "interrupted"
    before = snapshots(store, owner["id"])
    monkeypatch.setattr(batches, "require_code", lambda _: None)
    monkeypatch.setattr(
        batches, "_client", lambda *_: pytest.fail("Startup retry contacted UCloud")
    )
    batches.retry(store, None, owner["id"])
    after = snapshots(store, owner["id"])
    assert after["attempts"] == before["attempts"]
    assert [row["params"] for row in after["jobs"]] == [row["params"] for row in before["jobs"]]
    assert [row["source_identity"] for row in after["members"]] == [
        row["source_identity"] for row in before["members"]
    ]
    assert all(
        not json.loads(row[key])
        for row in after["attempts"]
        for key in ("input_receipt", "remote_receipt")
    )


def test_setup_retry_rechecks_protected_approval_and_exact_source(library, monkeypatch):
    store, project, _ = library
    owner = failed_setup(library)
    monkeypatch.setattr(batches, "require_code", lambda _: None)
    store.execute("UPDATE projects SET classification='protected' WHERE id=?", (project["id"],))
    provider = owner["identity"]["provider_id"]
    settings = copy.deepcopy(owner["identity"]["settings"])
    settings["approved_for"] = []
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(settings), provider))
    before = snapshots(store, owner["id"])
    with pytest.raises(ValueError, match="approved"):
        batches.retry(store, None, owner["id"])
    assert snapshots(store, owner["id"]) == before
    store.execute(
        "UPDATE recordings SET duration=99 WHERE id=?", (owner["members"][0]["recording_id"],)
    )
    with pytest.raises(ValueError, match="source identity"):
        runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
    assert snapshots(store, owner["id"]) == before


def test_setup_retry_refuses_newer_job_or_attempt_state_and_prior_attempts(library):
    store, _, _ = library
    owner = failed_setup(library)
    job_id = owner["members"][0]["job_id"]
    for table, column, value, reset in (
        ("jobs", "state", "completed", "interrupted"),
        ("runtime_batch_attempts", "state", "failed", "preparing"),
        ("runtime_batch_members", "output_receipt", '{"files":{}}', "{}"),
    ):
        key = "id" if table == "jobs" else "job_id"
        store.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (value, job_id))
        before = snapshots(store, owner["id"])
        with pytest.raises(ValueError):
            runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
        assert snapshots(store, owner["id"]) == before
        store.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (reset, job_id))
    with store.connection() as db:
        db.execute(
            "INSERT INTO runtime_batch_attempts SELECT job_id,2,'prior-task','failed','{}','{}',"
            "NULL,created,updated FROM runtime_batch_attempts WHERE job_id=? AND attempt=1",
            (job_id,),
        )
    before = snapshots(store, owner["id"])
    with pytest.raises(ValueError, match="prior runtime attempt"):
        runtime_store.queue_setup_retry(store, owner["id"], owner["epoch"])
    assert snapshots(store, owner["id"]) == before


def test_setup_failure_diagnostic_rejects_private_or_unbounded_fields(library):
    store, _, _ = library
    owner = failed_setup(library)
    valid = {
        "stage": "setup_script_upload",
        "file": "enhancement_worker.py",
        "code": "upload_interrupted",
        "observed_at": "2026-10-30T12:00:00+00:00",
    }
    for field, value in (
        ("file", "private-recording.wav"),
        ("file", {}),
        ("stage", "https://credential.invalid"),
        ("code", "arbitrary body"),
        ("observed_at", "not-a-date"),
    ):
        before = snapshots(store, owner["id"])
        with pytest.raises(ValueError):
            runtime_store.checkpoint(
                store,
                owner["id"],
                owner["epoch"],
                journal_update={"setup_upload_failure": {**valid, field: value}},
            )
        assert snapshots(store, owner["id"]) == before


def test_public_upload_failure_diagnostic_and_successful_same_owner_retry(environment, monkeypatch):
    env = environment
    for _ in range(5):
        env.enqueue()
    upload = env.drive.upload

    def fail_first_script(client, server, path, target, guard, update, **kwargs):
        if path.name == "enhancement_worker.py":
            raise ValueError("UCloud upload interrupted. No job was started; retry explicitly.")
        return upload(client, server, path, target, guard, update, **kwargs)

    monkeypatch.setattr(batches.native, "upload", fail_first_script)
    failed = run_batch(env)
    assert not env.drive.submissions
    diagnostic = failed["journal"]["setup_upload_failure"]
    assert set(diagnostic) == {"stage", "file", "code", "observed_at"}
    assert diagnostic["stage"] == "setup_script_upload"
    assert diagnostic["file"] == "enhancement_worker.py"
    assert diagnostic["code"] == "upload_interrupted"
    events = list(env.drive.events)
    batches.retry(env.store, env.worker.credentials, failed["id"])
    assert env.drive.events == events
    monkeypatch.setattr(batches.native, "upload", upload)
    completed = run_batch(env)
    assert completed["id"] == failed["id"]
    assert len(env.drive.submissions) == 1
    assert all(m["state"] == "completed" for m in completed["members"])
