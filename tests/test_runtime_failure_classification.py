"""The local future wrapper must retain a collected runtime outcome's diagnostic."""

import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from test_runtime_batches import environment as environment  # noqa: F401

from cbs_transcriber import runtime_batches as batches
from cbs_transcriber import runtime_store
from cbs_transcriber.media import Cancelled


@pytest.mark.parametrize(
    ("stop_intent", "remote_code", "expected_code"),
    [
        (True, "stopped", "stopped"),
        (False, "interrupted", "interrupted"),
        (False, "unknown", "processing"),
    ],
)
def test_collected_terminal_diagnostic_survives_completed_member_future(
    environment, stop_intent, remote_code, expected_code
):
    env = environment
    job = env.enqueue()
    owner = batches.Coordinator(env.worker, runtime_store.claim(env.store))
    owner.client, owner.state = env.drive, "RUNNING"
    member = owner.batch["members"][0]
    digest = "a" * 64
    member = owner.task_checkpoint(
        member,
        state="accepted",
        member_state="accepted",
        input_update={"publish_intent": True, "published": True, "manifest_sha256": digest},
        remote_update={"accepted": True},
    )
    if stop_intent:
        runtime_store.stop(env.store, owner.id)
    binding = {
        "protocol": batches.PROTOCOL,
        "batch_id": owner.id,
        "identity_hash": owner.batch["compatibility_hash"],
        "task_id": member["task_id"],
        "job_id": job["id"],
        "attempt": member["attempt"],
        "inputs_sha256": digest,
        "inputs_size": 12,
    }
    remote = env.drive.path(owner.remote + "/tasks/" + member["task_id"])
    remote.mkdir(parents=True)
    (remote / "accepted.json").write_bytes(batches.json_bytes(binding))
    (remote / "outcome.json").write_bytes(
        batches.json_bytes(
            binding | {"state": "interrupted", "error_code": remote_code, "outputs": {}}
        )
    )
    folder = owner.folder / "synthetic-member"
    folder.mkdir()
    item = SimpleNamespace(
        job=env.store.job(job["id"]),
        recording=env.store.recording(job["recording_id"]),
        options=owner.options,
        folder=folder,
        done=threading.Event(),
        error=None,
    )
    future = Future()
    owner.pending[member["task_id"]] = item
    owner.futures[future] = (job["id"], member["task_id"])

    assert owner.collect(item, member)
    collected = owner.member(job["id"])
    assert item.done.is_set()
    assert isinstance(item.error, Cancelled if stop_intent else ValueError)
    assert collected["attempt_error_code"] == expected_code
    assert collected["remote_receipt"] == {
        "accepted": True,
        "outcome": "interrupted",
        "worker_gone": True,
        "error_code": expected_code,
        "files": {},
    }
    # A member wakes from runtime_dispatch and raises the exact error settled by collect.
    future.set_exception(item.error)
    owner.completed_futures()

    settled = owner.member(job["id"])
    assert settled["attempt_error_code"] == expected_code
    assert settled["remote_receipt"] == collected["remote_receipt"]
    assert env.store.job(job["id"])["message"] == expected_code
    assert settled["state"] == ("cancelled" if stop_intent else "failed")
    activity = batches.activity(env.store)
    assert activity[0]["runtime_batch"]["members"][0]["message"] == expected_code
    assert not owner.pending and not owner.futures
    assert not env.drive.submissions and not env.drive.uploads
    assert not env.drive.terminations and not env.drive.trashed
    assert env.drive.worker_count == 0
    assert not env.store.all("SELECT id FROM transcript_results")


def test_unclassified_local_member_failure_keeps_processing_code_and_safe_reason(environment):
    env = environment
    job = env.enqueue()
    owner = batches.Coordinator(env.worker, runtime_store.claim(env.store))
    member = owner.batch["members"][0]
    future = Future()
    future.set_exception(ValueError("Synthetic local failure before any remote outcome."))
    owner.futures[future] = (job["id"], member["task_id"])

    owner.completed_futures()

    member = owner.member(job["id"])
    assert member["state"] == member["attempt_state"] == "failed"
    assert member["attempt_error_code"] == "processing"
    message = "Local input preparation failed (ValueError). This attempt did not publish new input."
    assert env.store.job(job["id"])["message"] == message
    assert batches.activity(env.store)[0]["runtime_batch"]["members"][0]["reason"] == message
    assert not member["remote_receipt"].get("outcome")
    assert not env.drive.submissions and env.drive.worker_count == 0
