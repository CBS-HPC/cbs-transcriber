"""Early native batches never submit after a confirmed Delete or recorded Stop (ADR 0183, B463).

Synthetic mocked UCloud only: no network, real allocation, upload or model download.
"""

import time

import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import frozen_native_jobs
from test_native_batch import selection as selection
from test_native_batch_early import Runner, accept, add_recording, admit, wait_until
from test_native_batch_transfers import process
from test_ucloud_native import remote as remote

from cbs_transcriber import input_admission, native_batch, original_inspection
from cbs_transcriber.media import Cancelled
from cbs_transcriber.transcripts import normalize_segments


def confirmed_delete(client, recording_id):
    route = "/api/recordings/" + recording_id
    response = client.delete(route)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "confirm_shared_cancellation"
    params = {"operation_id": detail["operation_id"], "confirm_shared": True}
    return client.delete(route, params=params)


@pytest.mark.parametrize("admission", ["waiting", "mid-admission"])
def test_v232_delete_of_joined_file_never_submits_before_its_stop(
    client, batch_remote, selection, wav_bytes, monkeypatch, admission
):
    provider, rows = selection
    store, queue = client.app.state.store, client.app.state.queue
    credentials = client.app.state.credentials
    batch_remote.store = store
    batch_remote.states = ["IN_QUEUE"] * 200
    rows.append(add_recording(store, rows[0]["project_id"], "third.wav", wav_bytes))
    accepted = accept(client, provider, rows)
    first, _ = admit(client, accepted[:2])  # Two files joined; the third is not admitted yet.
    batch_id = first["params"]["native_batch_id"]
    token = None
    if admission == "mid-admission":
        # The third file is being admitted when the Delete marks it for cancellation.
        cache = original_inspection.read(store, rows[2]["id"])
        original_inspection.run(store, original_inspection.claim(store, cache["inspection_job_id"]))
        token = input_admission.claim(store)
        assert [member["id"] for member in token["jobs"]] == [accepted[2]["id"]]
    runner = Runner(client, batch_id)
    original_cancel, observed = queue.cancel, {}

    def finish_admission():
        # Its admission worker finishes after the Delete marked it: this seals the batch.
        if token is not None and "sealed" not in observed:
            outcomes = input_admission.prepare(store, credentials, token)
            input_admission.materialize(store, token, outcomes)
            observed["sealed"] = native_batch.sealed(store.native_batch(batch_id))

    def delayed_cancel(identifier, **kwargs):
        # Widen the window between the admission cancel (which can seal) and the shared Stop
        # (the reviewer's reproduction). The coordinator may already have stopped, in which
        # case the Delete has no generic cancellation left to make.
        finish_admission()
        time.sleep(1.5)
        observed.setdefault("submissions_before_stop", len(batch_remote.submissions))
        return original_cancel(identifier, **kwargs)

    try:
        assert wait_until(lambda: len(runner.run_state().get("uploaded", [])) == 2)
        assert not batch_remote.submissions
        monkeypatch.setattr(queue, "cancel", delayed_cancel)
        response = confirmed_delete(client, rows[0]["id"])
        assert response.status_code in {200, 202}, response.text
        finish_admission()
        time.sleep(1.5)  # Several coordinator polls after the seal.
    finally:
        queue.cancel_event.set()
        runner.join()
    assert observed.get("submissions_before_stop", 0) == 0
    assert not batch_remote.submissions, "A sealed batch submitted after a confirmed Delete"
    if token is not None:
        assert observed["sealed"] is True
    run = runner.run_state()
    assert run["membership_sealed"] is True and accepted[2]["id"] in run["dropped_members"]
    assert run["cancel_requested"] is True and not run.get("submission_attempted")
    assert len(runner.errors) == 1 and isinstance(runner.errors[0], Cancelled)
    assert store.native_batch(batch_id)["state"] == "cancelled"
    assert store.job(accepted[2]["id"])["state"] == "cancelled"


def test_v232_stop_recorded_after_the_last_guard_refuses_the_submission_intent(
    client, batch_remote, selection, monkeypatch
):
    provider, rows = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = frozen_native_jobs(client, provider, rows)
    batch_id = jobs[0]["params"]["native_batch_id"]
    projection = native_batch.native_capture.projection

    def stop_then_project(*args, **kwargs):
        # After the coordinator's final guard, without waking it: only the durable record.
        store.update_native_batch(batch_id, run_update={"cancel_requested": True})
        return projection(*args, **kwargs)

    monkeypatch.setattr(native_batch.native_capture, "projection", stop_then_project)
    with pytest.raises(Cancelled):
        process(client, jobs)
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    assert len(batch_remote.uploads) == 2 and not batch_remote.submissions
    assert not run.get("submission_attempted") and not run.get("job_id")
    assert store.native_batch(batch_id)["state"] == "cancelled"


def test_v229_completed_total_excludes_files_released_by_delete(client, selection, wav_bytes):
    provider, rows = selection
    store = client.app.state.store
    rows.append(add_recording(store, rows[0]["project_id"], "third.wav", wav_bytes))
    jobs = frozen_native_jobs(client, provider, rows)
    batch_id = jobs[0]["params"]["native_batch_id"]
    segments = normalize_segments([{"start": 0, "end": 1, "text": "Synthetic"}])
    for job in jobs[:2]:
        store.add_result(
            job["recording_id"], segments, {}, "Synthetic", {"kind": "provider"}, job_id=job["id"]
        )
        store.update_job(job["id"], state="completed")
    store.update_native_batch(batch_id, state="completed", message="UCloud batch complete")
    fields = native_batch.status_fields(store, store.job(jobs[0]["id"]))
    assert fields["ucloud_batch_message"] == "UCloud batch complete · 2 of 3 transcripts saved"
    # The third recording is deleted after completion; its file left the allocation.
    native_batch.record_deleted_members(store, batch_id, [jobs[2]["id"]])
    store.execute("DELETE FROM jobs WHERE id=?", (jobs[2]["id"],))
    fields = native_batch.status_fields(store, store.job(jobs[0]["id"]))
    assert fields["ucloud_batch_message"] == "UCloud batch complete · 2 of 2 transcripts saved"
