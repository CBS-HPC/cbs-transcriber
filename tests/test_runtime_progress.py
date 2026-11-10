"""Read-only Activity progress comes from durable task and saved-result receipts."""

from test_runtime_store import enqueue
from test_runtime_store import library as library

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_batches, runtime_store


def test_runtime_activity_distinguishes_inference_waiting_and_local_save(library):
    store, _, _ = library
    jobs = [enqueue(library) for _ in range(4)]
    batch = runtime_store.claim(store)
    for index, member in enumerate(batch["members"]):
        if index == 3:
            continue  # Claiming a member for preparation does not start inference.
        runtime_store.checkpoint_attempt(
            store,
            batch["id"],
            member["job_id"],
            batch["epoch"],
            member["task_id"],
            state="complete" if index == 2 else "running" if index == 0 else "accepted",
            member_state="collecting" if index == 2 else "running" if index == 0 else "accepted",
            remote_update={"accepted": True},
        )
        store.update_job(member["job_id"], message=api.STAGES["transcribing"], progress=0.18)
    before = {
        table: store.all(f"SELECT * FROM {table}")
        for table in (
            "jobs",
            "runtime_batch_runs",
            "runtime_batch_members",
            "runtime_batch_attempts",
        )
    }
    owner = runtime_batches.activity(store)[0]
    progress = owner["runtime_batch"]["file_progress"]
    assert progress["counts"] == {
        "total": 4,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "interrupted": 0,
        "waiting": 2,
        "running": 1,
        "collecting": 1,
        "unknown": 0,
    }
    assert progress["active_files"] == [
        {
            "job_id": jobs[0]["id"],
            "recording_name": "synthetic",
            "stage": "transcribing",
            "stage_label": "Transcribing with WhisperX",
        }
    ]
    assert progress["counts"]["completed"] == 0  # Remote completion is not a saved result.
    assert {table: store.all(f"SELECT * FROM {table}") for table in before} == before


def test_runtime_activity_uses_specific_current_receipt_without_rewriting_cancelled(library):
    store, _, _ = library
    job = enqueue(library)
    batch = runtime_store.claim(store)
    member = batch["members"][0]
    runtime_store.checkpoint_attempt(
        store,
        batch["id"],
        job["id"],
        batch["epoch"],
        member["task_id"],
        state="cancelled",
        member_state="cancelled",
        error_code="processing",
        remote_update={"error_code": "stopped", "outcome": "interrupted", "worker_gone": True},
    )
    before = runtime_store.get(store, batch["id"])
    progress = runtime_batches.activity(store)[0]["runtime_batch"]
    outcome = progress["members"][0]
    assert outcome["state"] == "cancelled"
    assert outcome["reason_code"] == "stopped"
    assert outcome["reason"] == "Stopped before completion."
    assert progress["file_progress"]["counts"]["cancelled"] == 1
    assert progress["file_progress"]["counts"]["failed"] == 0
    assert runtime_store.get(store, batch["id"]) == before


def test_runtime_activity_never_infers_a_stage_or_running_from_stale_job_message(library):
    store, _, _ = library
    jobs = [enqueue(library) for _ in range(2)]
    batch = runtime_store.claim(store)
    for index, member in enumerate(batch["members"]):
        runtime_store.checkpoint_attempt(
            store,
            batch["id"],
            member["job_id"],
            batch["epoch"],
            member["task_id"],
            state="running",
            member_state="running",
            remote_update={"accepted": True} if index == 0 else None,
        )
        store.update_job(member["job_id"], message="Unrecognized legacy stage", progress=0.9)
    progress = runtime_batches.activity(store)[0]["runtime_batch"]["file_progress"]
    assert progress["counts"]["running"] == 1
    assert progress["counts"]["unknown"] == 1
    assert progress["active_files"] == [
        {
            "job_id": jobs[0]["id"],
            "recording_name": "synthetic",
            "stage": None,
            "stage_label": "Stage not reported",
        }
    ]
