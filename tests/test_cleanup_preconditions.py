"""Refused native cleanup records diagnostics without inventing remote obligations."""

from copy import deepcopy

import pytest
from test_native_batch import frozen_native_jobs
from test_native_batch import selection as selection  # noqa: F401
from test_ucloud_native import remote as remote  # noqa: F401

from cbs_transcriber import native_batch, remote_jobs, ucloud_native


@pytest.mark.parametrize("shared", [False, True], ids=["standalone", "shared-batch"])
@pytest.mark.parametrize("refusal", ["disabled", "wrong-ownership"])
@pytest.mark.parametrize("initial_state", [None, "retained", "pending"])
def test_cleanup_refusal_preserves_existing_obligation(
    client, selection, monkeypatch, shared, refusal, initial_state
):
    provider, recordings = selection
    store = client.app.state.store
    # Cleanup of older frozen runs does not rewrite a new input_acceptance receipt.
    jobs = frozen_native_jobs(
        client,
        provider,
        recordings if shared else recordings[:1],
        cleanup_after_success=refusal != "disabled",
    )
    job = jobs[0]
    batch_id = job["params"].get("native_batch_id")
    folder = "/drive/cbs/" + ("cbs-batch-" + batch_id if shared else "cbs-" + job["id"])
    run = {
        "job_id": "synthetic-remote-job",
        "remote_state": "SUCCESS",
        "owned_folder": folder if refusal == "disabled" else "/drive/cbs/unowned",
        "folders_created": True,
    }
    if initial_state is not None:
        run["cleanup_state"] = initial_state
    for member in jobs:
        params = deepcopy(member["params"])
        if not shared:
            params["ucloud_run"] = deepcopy(run)
        store.update_job(member["id"], state="completed", params=params)
    if shared:
        store.update_native_batch(batch_id, state="completed", run_update=deepcopy(run))

    def forbid_remote(*args, **kwargs):
        pytest.fail("Refused cleanup must not construct a remote client or contact Trash.")

    monkeypatch.setattr(ucloud_native, "client_for", forbid_remote)
    monkeypatch.setattr(ucloud_native, "trash_owned_folder", forbid_remote)
    assert bool(remote_jobs.unresolved_for(store, store.job(job["id"]))) == (
        initial_state == "pending"
    )

    response = client.post("/api/jobs/" + job["id"] + "/cleanup-ucloud")

    assert response.status_code == 400, response.text
    saved = (
        store.native_batch(batch_id)["params"]["ucloud_run"]
        if shared
        else store.job(job["id"])["params"]["ucloud_run"]
    )
    assert saved.get("cleanup_state") == initial_state
    assert saved["cleanup_stage"] == "preconditions"
    assert saved["cleanup_error_code"] == "ownership_or_success_missing"
    assert saved["cleanup_attempted_at"]
    assert bool(remote_jobs.unresolved_for(store, store.job(job["id"]))) == (
        initial_state == "pending"
    )
    if shared:
        assert native_batch.unresolved(store.native_batch(batch_id)) == (initial_state == "pending")
