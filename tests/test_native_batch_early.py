"""Early native uploads (ADR 0183, V231): each file uploads once its own inspection passes.

Synthetic mocked UCloud only: no network, real allocation, upload or model download.
"""

import json
import threading
import time

import pytest
from admission_support import inspect_and_materialize
from test_native_batch import batch_remote as batch_remote
from test_native_batch import selection as selection
from test_ucloud_native import remote as remote

from cbs_transcriber import input_admission, native_batch, original_inspection, remote_release
from cbs_transcriber.media import Cancelled


def accept(client, provider, rows):
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [provider["id"]], "recording_ids": [row["id"] for row in rows]},
    )
    assert response.status_code == 202, response.text
    return response.json()


def admit(client, jobs):
    return inspect_and_materialize(
        client.app.state.store, client.app.state.credentials, [job["id"] for job in jobs]
    )


def wait_until(condition, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def add_recording(store, project_id, name, data):
    recording = store.create_recording(project_id, name)
    store.directory(recording["id"]).mkdir(parents=True)
    (store.directory(recording["id"]) / "original.wav").write_bytes(data)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    return recording


class Runner:
    """Run the claimed batch coordinator in a thread, as the native lane would."""

    def __init__(self, client, batch_id):
        self.store, self.queue = client.app.state.store, client.app.state.queue
        self.batch_id, self.errors = batch_id, []
        claimed = self.store.claim_job("transcribe")
        assert claimed is not None and claimed["params"]["native_batch_id"] == batch_id
        self.claimed = claimed
        self.queue.cancel_event.clear()
        self.queue.active_batch_id = batch_id
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self):
        try:
            self.queue.process(self.claimed["id"])
        except Exception as error:  # noqa: BLE001 - asserted by the test
            self.errors.append(error)

    def join(self):
        self.thread.join(30)
        assert not self.thread.is_alive()
        self.queue.active_batch_id = None

    def run_state(self):
        return self.store.native_batch(self.batch_id)["params"]["ucloud_run"]


def test_v231_first_file_uploads_while_others_are_inspected_and_submission_waits(
    client, batch_remote, selection, wav_bytes
):
    provider, rows = selection
    store = client.app.state.store
    batch_remote.store = store
    rows.append(add_recording(store, rows[0]["project_id"], "third.wav", wav_bytes))
    accepted = accept(client, provider, rows)
    (first,) = admit(client, accepted[:1])
    batch_id = first["params"]["native_batch_id"]
    runner = Runner(client, batch_id)
    try:
        assert wait_until(lambda: runner.run_state().get("uploaded") == [first["id"]])
        time.sleep(1.5)  # Several admission polls: still no submission or second upload.
        assert len(batch_remote.uploads) == 1 and not batch_remote.submissions
        fields = native_batch.status_fields(store, store.job(first["id"]))
        assert fields["ucloud_batch_message"] == (
            "Uploading files to UCloud · 1 of 3 uploaded · 2 still being inspected"
        )
        waiting = [m for m in fields["ucloud_batch_members"] if m["job_id"] != first["id"]]
        assert {m["state"] for m in waiting} == {"preparing"}
        assert {m["status_label"] for m in waiting} == {"Inspecting original"}
        second, third = admit(client, accepted[1:])
        assert {second["params"]["native_batch_id"], third["params"]["native_batch_id"]} == {
            batch_id
        }
    finally:
        runner.join()
    assert not runner.errors
    assert len(batch_remote.uploads) == 3 and len(batch_remote.submissions) == 1
    spec = batch_remote.submissions[0]
    assert spec["name"] == "CBS Transcriber batch " + batch_id
    run = runner.run_state()
    assert run["membership_sealed"] is True and run["dropped_members"] == []
    assert sorted(run["uploaded"]) == sorted(job["id"] for job in accepted)
    assert all(len(store.results(row["id"])) == 1 for row in rows)
    assert store.native_batch(batch_id)["state"] == "completed"
    assert len(store.all("SELECT id FROM native_batch_runs")) == 1


def test_v231_failed_inspection_is_dropped_and_the_rest_submit_once(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    provider, rows = selection
    store = client.app.state.store
    batch_remote.store = store
    rows.append(add_recording(store, rows[0]["project_id"], "broken.wav", b"not a recording"))
    accepted = accept(client, provider, rows)
    admitted = admit(client, accepted[:2])
    batch_id = admitted[0]["params"]["native_batch_id"]
    runner = Runner(client, batch_id)
    try:
        assert wait_until(lambda: len(runner.run_state().get("uploaded", [])) == 2)
        assert not batch_remote.submissions
        cache = original_inspection.read(store, rows[2]["id"])
        claim = original_inspection.claim(store, cache["inspection_job_id"])
        assert original_inspection.run(store, claim)["state"] == "error"
        assert input_admission.claim(store) is None  # Dropped without a token.
    finally:
        runner.join()
    assert not runner.errors
    run = runner.run_state()
    assert run["dropped_members"] == [accepted[2]["id"]] and run["membership_sealed"] is True
    assert len(batch_remote.uploads) == 2 and len(batch_remote.submissions) == 1
    # The broken file keeps its own outcome and never reaches the allocation.
    failed = store.job(accepted[2]["id"])
    assert failed["state"] == "failed" and "native_batch_id" not in failed["params"]
    assert failed["params"]["input_intent"]["error"]["code"] == "source_error"
    assert not store.results(rows[2]["id"])
    assert all(len(store.results(row["id"])) == 1 for row in rows[:2])
    fields = native_batch.status_fields(store, store.job(accepted[0]["id"]))
    assert fields["ucloud_batch_count"] == 2
    assert fields["ucloud_batch_message"] == "UCloud batch complete · 2 of 2 transcripts saved"
    excluded = [m for m in fields["ucloud_batch_members"] if m["job_id"] == accepted[2]["id"]]
    assert excluded[0]["state"] == "excluded" and excluded[0]["status_label"] == "Not included"
    # Success cleanup needs saved results only for joined files; the dropped file has none.
    batch = store.native_batch(batch_id)
    params = batch["params"]
    params["settings"]["ucloud_transcriber"]["cleanup_after_success"] = True
    params["ucloud_run"]["cleanup_state"] = None
    store.execute(
        "UPDATE native_batch_runs SET params=? WHERE id=?", (json.dumps(params), batch_id)
    )
    trashed = []
    monkeypatch.setattr(
        native_batch.native, "trash_owned_folder", lambda _, folder: trashed.append(folder)
    )
    assert native_batch.cleanup_batch(store, client.app.state.credentials, batch_id) == "trashed"
    assert trashed == [native_batch.folder_for(store.native_batch(batch_id))]


@pytest.mark.parametrize("shutdown", [False, True], ids=["stop", "shutdown"])
def test_v231_stop_or_shutdown_before_submission_keeps_late_files_with_the_one_owner(
    client, batch_remote, selection, wav_bytes, shutdown
):
    provider, rows = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    accepted = accept(client, provider, rows)
    (first,) = admit(client, accepted[:1])
    batch_id = first["params"]["native_batch_id"]
    runner = Runner(client, batch_id)
    try:
        assert wait_until(lambda: runner.run_state().get("uploaded") == [first["id"]])
        if shutdown:
            queue.stopping.set()
            queue.cancel_event.set()
        else:
            response = client.post("/api/jobs/" + first["id"] + "/cancel")
            assert response.status_code == 200, response.text
    finally:
        runner.join()
        queue.stopping.clear()
    assert len(runner.errors) == 1 and isinstance(runner.errors[0], Cancelled)
    stopped = "interrupted" if shutdown else "cancelled"
    assert store.native_batch(batch_id)["state"] == stopped
    assert not batch_remote.submissions
    # The other file keeps its own preparation state while the batch is stopped...
    assert store.job(accepted[1]["id"])["params"]["input_intent"]["phase"] == "waiting"
    # ...and then joins the same stopped owner: no upload or allocation without Retry.
    (late,) = admit(client, accepted[1:])
    assert late["params"]["native_batch_id"] == batch_id and late["state"] == stopped
    assert len(store.all("SELECT id FROM native_batch_runs")) == 1
    assert len(batch_remote.uploads) == 1 and not batch_remote.submissions
    response = client.post("/api/jobs/" + first["id"] + "/retry", json={})
    assert response.status_code == 200, response.text
    runner = Runner(client, batch_id)
    runner.join()
    assert not runner.errors
    # Retry resumes the whole selection: only the missing upload, then one submission.
    assert len(batch_remote.uploads) == 2 and len(batch_remote.submissions) == 1
    assert all(len(store.results(row["id"])) == 1 for row in rows)


def test_v232_startup_reconnects_a_submitted_early_batch_without_its_dropped_file(
    client, batch_remote, selection, wav_bytes, monkeypatch
):
    from test_native_startup_recovery import admitted_queue

    provider, rows = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    rows.append(add_recording(store, rows[0]["project_id"], "third.wav", wav_bytes))
    accepted = accept(client, provider, rows)
    intent = accepted[2]["params"]["input_intent"]
    input_admission.cancel(store, accepted[2]["id"], epoch=intent["epoch"], phase=intent["phase"])
    admitted = admit(client, accepted[:2])
    batch_id = admitted[0]["params"]["native_batch_id"]
    batch_remote.states = ["RUNNING"]
    runner = Runner(client, batch_id)
    try:
        assert wait_until(lambda: runner.run_state().get("job_id") == "remote-job")
        queue.stopping.set()  # Shutdown leaves the submitted allocation running.
        queue.cancel_event.set()
    finally:
        runner.join()
        queue.stopping.clear()
    assert store.native_batch(batch_id)["state"] == "interrupted"
    assert runner.run_state()["dropped_members"] == [accepted[2]["id"]]
    batch_remote.states = ["SUCCESS"]
    restarted = admitted_queue(client, monkeypatch)
    assert {store.job(job["id"])["state"] for job in admitted} == {"queued"}
    assert store.job(accepted[2]["id"])["state"] == "cancelled"
    claimed = store.claim_job("transcribe")
    restarted.active_batch_id = batch_id
    restarted.process(claimed["id"])
    assert len(batch_remote.submissions) == 1 and len(batch_remote.uploads) == 2
    assert all(len(store.results(row["id"])) == 1 for row in rows[:2])
    assert not store.results(rows[2]["id"])
    assert store.native_batch(batch_id)["state"] == "completed"


def test_v231_concurrent_admissions_create_exactly_one_owner(
    client, batch_remote, selection, wav_bytes
):
    provider, rows = selection
    store, credentials = client.app.state.store, client.app.state.credentials
    rows.append(add_recording(store, rows[0]["project_id"], "third.wav", wav_bytes))
    accepted = accept(client, provider, rows)
    for row in rows:
        cache = original_inspection.read(store, row["id"])
        original_inspection.run(store, original_inspection.claim(store, cache["inspection_job_id"]))
    tokens = [input_admission.claim(store) for _ in rows]
    assert sorted(token["jobs"][0]["id"] for token in tokens) == sorted(j["id"] for j in accepted)
    outcomes = [input_admission.prepare(store, credentials, token) for token in tokens]
    barrier, errors = threading.Barrier(len(tokens)), []

    def materialize(token, outcome):
        barrier.wait(10)
        try:
            input_admission.materialize(store, token, outcome)
        except Exception as error:  # noqa: BLE001 - asserted below
            errors.append(error)

    threads = [
        threading.Thread(target=materialize, args=pair)
        for pair in zip(tokens, outcomes, strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not errors
    (batch,) = store.all("SELECT id,params FROM native_batch_runs")
    assert {store.job(job["id"])["params"]["native_batch_id"] for job in accepted} == {batch["id"]}
    run = json.loads(batch["params"])["ucloud_run"]
    assert run["membership_sealed"] is True and run["dropped_members"] == []


def test_v231_delete_impact_trashes_when_only_unjoined_files_remain(
    client, batch_remote, selection, wav_bytes
):
    provider, rows = selection
    store = client.app.state.store
    accepted = accept(client, provider, rows)
    (first,) = admit(client, accepted[:1])
    batch_id = first["params"]["native_batch_id"]
    store.update_native_batch(
        batch_id,
        run_update={
            "root_created": True,
            "owned_folder": native_batch.folder_for(store.native_batch(batch_id)),
            "folders_created": True,
            "uploaded": [first["id"]],
        },
    )
    impact = remote_release.native_impact(store, batch_id, [first["id"]])
    # The other file is still being inspected: Delete stops the shared allocation and
    # cancels its admission, so nothing it uploaded keeps the owned folder (ADR 0183).
    assert impact["action"] == "trash" and impact["other_files"] == 0
    (second,) = admit(client, accepted[1:])
    impact = remote_release.native_impact(store, batch_id, [first["id"]])
    assert impact["action"] == "retain" and impact["other_files"] == 1
