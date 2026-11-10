"""Synthetic local/bootstrap failures preserve safe cause and final disposition."""

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from test_runtime_batches import environment as batch_environment
from test_runtime_batches import run_batch

from cbs_transcriber import enhancement, runtime_batches, runtime_store
from cbs_transcriber import enhancement_api as api
from cbs_transcriber.jobs import JobQueue

environment = batch_environment


@pytest.mark.parametrize("code", ["runtime-prepare", "PRIVATE_UNKNOWN_BOOTSTRAP_DETAIL"])
def test_terminal_bootstrap_failure_is_reported_before_member_preparation(
    environment, monkeypatch, code
):
    env = environment
    job = env.enqueue()
    submit = env.drive.submit_job

    def failed(spec):
        reply = submit(spec)
        folder = env.drive.path(spec["resources"][0]["path"])
        (folder / "supervisor.json").write_text(
            json.dumps({"phase": "failed", "code": code, "generation": folder.name[18:]})
        )
        return reply

    monkeypatch.setattr(env.drive, "submit_job", failed)
    monkeypatch.setattr(
        env.drive,
        "retrieve_job",
        lambda identifier: {"id": identifier, "status": {"state": "SUCCESS"}},
    )
    batch = run_batch(env)
    expected = api.FAILURES.get(code, "The UCloud runtime failed readiness.")
    assert batch["state"] == "failed"
    assert batch["message"] == expected
    assert "PRIVATE_" not in batch["message"]
    assert not batch["members"][0]["input_receipt"]
    assert not any(name.endswith(("/audio.wav", "/request.json")) for name, _ in env.drive.uploads)
    assert not env.drive.worker_count
    assert env.store.job(job["id"])["state"] != "completed"


@pytest.mark.parametrize(
    "error,error_class",
    [
        (ValueError("PRIVATE_TRANSCRIPT_AND_TOKEN_NEVER_PUBLISH"), "ValueError"),
        (OSError("PRIVATE_PATH_NEVER_PUBLISH"), "OSError"),
        (type("PRIVATE_CLASS_NAME", (Exception,), {})("PRIVATE_DETAIL"), "Exception"),
    ],
)
def test_local_child_failure_reports_stage_and_class_without_exception_text(
    environment, monkeypatch, error, error_class
):
    env = environment
    job = env.enqueue()

    def fail(*args):
        raise error

    monkeypatch.setattr(enhancement, "run_enhancement", fail)
    batch = run_batch(env)
    member = batch["members"][0]
    message = (
        f"Local input preparation failed ({error_class}). This attempt did not publish new input."
    )
    assert env.store.job(job["id"])["message"] == message
    assert member["attempt_error_code"] == "processing"
    assert not member["input_receipt"] and not member["remote_receipt"]
    card = next(
        item
        for item in runtime_batches.activity(env.store)
        if item["runtime_batch_id"] == batch["id"]
    )
    assert card["runtime_batch"]["members"][0]["reason"] == message
    assert "PRIVATE_" not in json.dumps(card)


def test_unexpected_terminal_allocation_seals_local_membership_without_inventing_stop(
    environment, monkeypatch
):
    env = environment
    env.enqueue()
    monkeypatch.setattr(
        env.drive,
        "retrieve_job",
        lambda identifier: {"id": identifier, "status": {"state": "SUCCESS"}},
    )
    batch = run_batch(env)
    assert batch["state"] == "failed" and batch["admission"] == "closed"
    assert batch["seal_revision"] is not None
    assert not batch["journal"].get("stop_intent")
    assert batch["message"] == (
        "Runtime allocation ended before accepting any file. No input was published."
    )
    assert not batch["members"][0]["input_receipt"]


def test_outer_coordinator_exception_reports_only_safe_stage_and_class(environment, monkeypatch):
    env = environment
    job = env.enqueue()
    env.worker.coordinator = SimpleNamespace(capture_paused=False)

    def fail(*args):
        env.worker.stopping.set()
        raise TypeError("PRIVATE_EXCEPTION_BODY_AND_PATH")

    monkeypatch.setattr(runtime_batches, "run", fail)
    JobQueue.run_runtime_batches(env.worker)
    batch = runtime_store.for_job(env.store, job["id"])
    assert batch["state"] == "interrupted"
    assert batch["message"] == (
        "Local batch coordination failed (TypeError). Reconnect to recover the same allocation."
    )
    assert "PRIVATE_" not in json.dumps(batch)
    assert not env.drive.submissions


def test_local_save_failure_preserves_complete_output_without_claiming_inputs_were_not_published(
    environment, monkeypatch
):
    env = environment
    job = env.enqueue()

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_SAVE_PATH_AND_TEXT")

    monkeypatch.setattr(env.store, "add_result", fail)
    batch = run_batch(env)
    member = batch["members"][0]
    assert member["attempt_state"] == "complete"
    assert member["input_receipt"]["published"]
    assert member["remote_receipt"]["outcome"] == "complete"
    assert env.store.job(job["id"])["message"] == (
        "Local result validation or saving failed (OSError)."
    )
    assert not member["result_id"]
    assert "PRIVATE_" not in env.store.job(job["id"])["message"]


def test_late_coordinator_failure_cannot_reopen_disposed_owner_or_offer_reconnect(environment):
    env = environment
    env.enqueue()
    claimed = runtime_store.claim(env.store)
    final = runtime_store.checkpoint(
        env.store,
        claimed["id"],
        claimed["epoch"],
        state="cancelled",
        admission="closed",
        message="CBS Runtime batch stopped; saved results kept.",
        journal_update={
            "stop_intent": True,
            "remote_state": "SUCCESS",
            "discard_confirmed": True,
            "cleanup_state": "trashed",
        },
    )
    interrupted = runtime_store.interrupt(
        env.store, final["id"], final["epoch"], "Reconnect to recover the same allocation."
    )
    assert interrupted == final
    # A previously retained stale message is presented against the durable final receipt.
    env.store.execute(
        "UPDATE runtime_batch_runs SET message=? WHERE id=?",
        ("Reconnect to recover the same allocation.", final["id"]),
    )
    card = next(
        item
        for item in runtime_batches.activity(env.store)
        if item["runtime_batch_id"] == final["id"]
    )
    assert "Reconnect" not in card["message"]
    assert not card["runtime_batch"]["can_retry"]


def test_coordinator_finalizer_retains_disposition_after_late_failure(environment, monkeypatch):
    env = environment
    env.enqueue()

    def released_then_failed(owner, executor):
        env.drive.states[owner.batch["journal"]["job_id"]] = "SUCCESS"
        runtime_store.checkpoint(
            env.store,
            owner.id,
            owner.epoch,
            state="cancelled",
            admission="closed",
            message="CBS Runtime batch stopped; saved results kept.",
            journal_update={
                "stop_intent": True,
                "remote_state": "SUCCESS",
                "discard_confirmed": True,
                "cleanup_state": "trashed",
            },
        )
        raise ValueError("PRIVATE_LATE_COORDINATOR_DETAIL")

    monkeypatch.setattr(runtime_batches.Coordinator, "loop", released_then_failed)
    batch = run_batch(env)
    assert batch["state"] == "cancelled"
    assert batch["message"] == "CBS Runtime batch stopped; saved results kept."
    assert batch["journal"]["cleanup_state"] == "trashed"
    assert not env.drive.terminations


@pytest.mark.parametrize(
    "mixed,corrupt",
    [(False, False), (True, False), (False, True)],
    ids=["cached", "mixed-cached-last", "corrupt-cache"],
)
def test_failed_supervisor_cannot_block_verified_local_recovery_or_allow_fresh_inputs(
    environment, monkeypatch, mixed, corrupt
):
    env = environment
    jobs = [env.enqueue() for _ in range(3 if mixed else 1)]
    cached_job = jobs[-1]
    pipeline, save = enhancement.run_enhancement, env.store.add_result

    def prepare(child, job, recording):
        if job["id"] != cached_job["id"]:
            raise ValueError("Synthetic unpublished preparation refusal")
        return pipeline(child, job, recording)

    def refuse(*args, **kwargs):
        raise OSError("Synthetic result commit refusal")

    monkeypatch.setattr(enhancement, "run_enhancement", prepare)
    monkeypatch.setattr(env.store, "add_result", refuse)
    failed = run_batch(env)
    before = next(m for m in failed["members"] if m["job_id"] == cached_job["id"])
    assert before["attempt_state"] == "complete"
    preserved_receipt = env.store.job(cached_job["id"])["params"]["runtime_output_receipt"]
    assert preserved_receipt
    monkeypatch.setattr(enhancement, "run_enhancement", pipeline)
    monkeypatch.setattr(env.store, "add_result", save)
    folder = env.drive.path(failed["journal"]["owned_folder"])
    (folder / "supervisor.json").write_text(
        json.dumps({"phase": "failed", "code": "runtime", "generation": failed["id"]})
    )
    if corrupt:
        local = env.store.directory(cached_job["recording_id"]) / "enhancements" / cached_job["id"]
        (local / "alignment.json").write_bytes(b"corrupt synthetic cache")
    private_count = sum(
        name.endswith(("/audio.wav", "/request.json")) for name, _ in env.drive.uploads
    )
    worker_count = env.drive.worker_count
    monkeypatch.setattr(
        runtime_batches.Coordinator,
        "readiness",
        lambda _: (_ for _ in ()).throw(AssertionError("Local recovery must not run doctor")),
    )
    runtime_batches.retry(env.store, env.worker.credentials, failed["id"])
    runtime_batches.run(env.worker, runtime_store.claim(env.store))
    recovered = runtime_store.get(env.store, failed["id"])
    member = next(m for m in recovered["members"] if m["job_id"] == cached_job["id"])
    assert member["task_id"] == before["task_id"] and member["attempt"] == before["attempt"]
    assert len(env.drive.submissions) == 1 and env.drive.worker_count == worker_count
    assert (
        sum(name.endswith(("/audio.wav", "/request.json")) for name, _ in env.drive.uploads)
        == private_count
    )
    if corrupt:
        assert not member["result_id"]
        assert not env.store.all("SELECT id FROM transcript_results")
    else:
        assert env.store.job(cached_job["id"])["state"] == "completed"
        assert member["output_receipt"]["files"] == preserved_receipt["files"]
        assert len(env.store.all("SELECT id FROM transcript_results")) == 1
        assert len(env.store.result_versions(cached_job["recording_id"], member["result_id"])) == 1


@pytest.mark.parametrize(
    "lookup_error", [ValueError("stale"), sqlite3.OperationalError("unavailable")]
)
def test_optional_diagnostic_lookup_cannot_orphan_dispatch_waiter(lookup_error):
    owner = runtime_batches.Coordinator.__new__(runtime_batches.Coordinator)
    original = OSError("PRIVATE_ORIGINAL_FAILURE")
    item = SimpleNamespace(job={"id": "a" * 32}, done=threading.Event(), error=None)
    owner.pending, owner.local_failures, owner.aborting = {"task": item}, {}, False

    def unavailable(*args):
        raise lookup_error

    owner.member = unavailable
    owner.settle_item("task", original)
    assert item.done.is_set() and item.error is original
    assert not owner.pending and not owner.local_failures


def test_unpublished_retry_cannot_deny_prior_private_publication(environment, monkeypatch):
    env = environment
    env.enqueue()
    batch = runtime_store.claim(env.store)
    member = batch["members"][0]
    runtime_store.checkpoint(
        env.store,
        batch["id"],
        batch["epoch"],
        journal_update={
            "job_id": "1",
            "remote_state": "RUNNING",
            "readiness_generation": batch["id"],
            "owned_folder": runtime_batches.remote_folder(
                runtime_batches._settings(batch), batch["id"]
            ),
            "folder_created": True,
        },
    )
    runtime_store.checkpoint_attempt(
        env.store,
        batch["id"],
        member["job_id"],
        batch["epoch"],
        member["task_id"],
        state="failed",
        member_state="failed",
        input_update={"publish_intent": True},
        remote_update={"outcome": "failed", "worker_gone": True},
    )
    current = runtime_store.retry_member(
        env.store, batch["id"], member["job_id"], batch["epoch"], task_id=member["task_id"]
    )
    runtime_store.checkpoint_attempt(
        env.store,
        batch["id"],
        current["job_id"],
        batch["epoch"],
        current["task_id"],
        state="failed",
        member_state="failed",
        error_code="processing",
    )
    terminal = runtime_store.checkpoint(
        env.store, batch["id"], batch["epoch"], journal_update={"remote_state": "SUCCESS"}
    )
    assert not runtime_batches._public_only(env.store, terminal)
    monkeypatch.setattr(runtime_batches.Coordinator, "submit", lambda _: None)
    monkeypatch.setattr(runtime_batches.Coordinator, "loop", lambda *_: None)
    runtime_batches.run(env.worker, terminal)
    final = runtime_store.get(env.store, batch["id"])
    assert final["message"] == "Runtime batch ended; saved results kept."
