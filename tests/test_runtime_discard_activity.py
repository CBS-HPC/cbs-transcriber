"""Discard is an explicit owner action, separate from Stop, Retry and ordinary cleanup."""

import pytest
from test_runtime_store import enqueue
from test_runtime_store import library as library

from cbs_transcriber import runtime_batches as batches
from cbs_transcriber import runtime_store


def retained_batch(library):
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
        input_update={"publish_intent": True},
        state="failed",
        member_state="failed",
    )
    runtime_store.stop(store, batch["id"])
    return runtime_store.checkpoint(
        store,
        batch["id"],
        batch["epoch"],
        state="failed",
        journal_update={
            "owned_folder": batches.remote_folder(batches._settings(batch), batch["id"]),
            "folder_created": True,
            "job_id": "synthetic-remote",
            "remote_state": "FAILURE",
            "cleanup_state": "retained",
        },
    )


def test_retained_private_owner_offers_distinct_discard_not_cleanup(library):
    store, _, _ = library
    batch = retained_batch(library)
    card = batches.activity(store)[0]
    assert card["runtime_batch"]["can_discard"] is True
    assert card["runtime_batch"]["can_cleanup"] is False
    assert card["runtime_batch"]["discard_confirmed"] is False
    assert not runtime_store.get(store, batch["id"])["journal"].get("discard_confirmed")


@pytest.mark.parametrize("field,value", [("folder_created", False), ("owned_folder", "/other")])
def test_discard_candidate_requires_exact_created_folder(library, field, value):
    store, _, _ = library
    batch = retained_batch(library)
    # Model a damaged persisted receipt; normal checkpoint correctly forbids changing ownership.
    journal = {**batch["journal"], field: value}
    store.execute(
        "UPDATE runtime_batch_runs SET journal=? WHERE id=?",
        (runtime_store.canonical(journal), batch["id"]),
    )
    assert batches.activity(store)[0]["runtime_batch"]["can_discard"] is False


def test_confirmed_abandonment_keeps_failure_visible_without_retry(library):
    store, _, _ = library
    batch = retained_batch(library)
    # Service confirmation has separate CAS/evidence tests; exercise only presentation here.
    runtime_store.checkpoint(store, batch["id"], batch["epoch"], {"discard_confirmed": True})
    card = batches.activity(store)[0]["runtime_batch"]
    assert card["can_discard"] is True
    assert card["discard_confirmed"] is True
    assert card["can_retry"] is False and card["retryable_members"] == 0
    assert card["can_cancel"] is False
    assert batches.activity(store)[0]["state"] == "failed"
    with pytest.raises(ValueError, match="explicitly abandoned"):
        batches.retry(store, None, batch["id"])
