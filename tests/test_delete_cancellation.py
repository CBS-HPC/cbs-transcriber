import copy
import threading
from contextlib import contextmanager

import pytest
from admission_support import inspect_and_materialize
from test_api import upload
from test_runtime_batch_api import add_recording, transcribe
from test_runtime_batch_api import prepared as prepared
from test_ucloud_native import SETUP

from cbs_transcriber import input_admission, original_inspection, providers
from cbs_transcriber.store import new_id


def test_delete_cancels_queued_media_without_cancelling_other_recordings(client, wav_bytes):
    first = upload(client, wav_bytes)
    second = upload(client, wav_bytes)
    response = client.delete(f"/api/recordings/{first['recording']['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert client.get(f"/api/recordings/{first['recording']['id']}").status_code == 404
    assert client.app.state.store.job(second["job"]["id"])["state"] == "queued"


def pending_media_delete(client, wav_bytes, *, project=None):
    uploaded = upload(client, wav_bytes, project)
    store, queue = client.app.state.store, client.app.state.queue
    job = uploaded["job"]
    store.update_job(job["id"], state="running")
    worker = next(worker for worker in queue.workers if worker.kind == "media")
    worker.active_id = job["id"]
    route = f"/api/recordings/{uploaded['recording']['id']}"
    response = client.delete(route)
    assert response.status_code == 202, response.text
    assert worker.cancel_event.is_set()
    return uploaded, worker, route, response.json()["operation_id"]


def drain_media(client, uploaded, worker):
    client.app.state.store.update_job(uploaded["job"]["id"], state="cancelled")
    worker.active_id = None


def test_running_owner_must_release_file_before_deletion(client, wav_bytes):
    uploaded, worker, route, operation_id = pending_media_delete(client, wav_bytes)
    store = client.app.state.store
    original = store.directory(uploaded["recording"]["id"]) / "original.wav"
    with original.open("rb") as handle:
        # Terminal database status alone is insufficient: the worker still owns a handle.
        store.update_job(uploaded["job"]["id"], state="cancelled")
        response = client.delete(route, params={"operation_id": operation_id})
        assert response.status_code == 202, response.text
        assert handle.read(4) == b"RIFF"
        assert original.exists()
    worker.active_id = None
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 200, response.text
    assert not original.exists()
    # A lost final response can be retried without attempting a second deletion.
    assert client.delete(route, params={"operation_id": operation_id}).json() == {"ok": True}


def test_working_designation_change_keeps_recording_during_pending_delete(client, wav_bytes):
    from test_result_roles import mark

    from cbs_transcriber.transcripts import normalize_segments

    uploaded = upload(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    identifier, job_id = uploaded["recording"]["id"], uploaded["job"]["id"]
    result = store.add_result(identifier, normalize_segments([{"text": "Keep this."}]), {}, "A", {})
    version = store.result_versions(identifier, result["id"])[0]
    revision = store.recording(identifier)["revision"]
    store.update_job(job_id, state="running")
    worker = next(worker for worker in queue.workers if worker.kind == "media")
    worker.active_id = job_id
    route = f"/api/recordings/{identifier}"
    response = client.delete(route)
    assert response.status_code == 202, response.text
    operation_id = response.json()["operation_id"]
    marked = mark(store, identifier, version["id"])
    assert marked["revision"] == revision  # Role changes do not edit transcript content.
    drain_media(client, uploaded, worker)
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 409, response.text
    assert store.recording(identifier)["result_roles"]["working"]["version_id"] == version["id"]
    assert (store.directory(identifier) / "original.wav").exists()


@pytest.mark.parametrize("change", ["job", "name", "notes", "result", "retry"])
def test_pending_delete_does_not_cancel_new_work_or_remove_edits(client, wav_bytes, change):
    uploaded, worker, route, operation_id = pending_media_delete(client, wav_bytes)
    store = client.app.state.store
    identifier = uploaded["recording"]["id"]
    drain_media(client, uploaded, worker)
    if change == "job":
        newer = store.add_job(identifier, "media")
    elif change == "name":
        assert client.patch(route, json={"name": "Changed while cancelling"}).status_code == 200
    elif change == "notes":
        assert (
            client.patch(route + "/notes", json={"text": "New note", "revision": 0}).status_code
            == 200
        )
    elif change == "result":
        from cbs_transcriber.transcripts import normalize_segments

        store.add_result(
            identifier, normalize_segments([{"text": "Saved meanwhile."}]), {}, "New", {}
        )
    else:
        store.update_job(uploaded["job"]["id"], state="queued")
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 409, response.text
    assert client.get(route).status_code == 200
    assert (store.directory(identifier) / "original.wav").exists()
    if change == "job":
        assert store.job(newer["id"])["state"] == "queued"
    elif change == "retry":
        assert store.job(uploaded["job"]["id"])["state"] == "queued"


def test_project_delete_cancels_queued_jobs_and_keeps_other_project(client, wav_bytes):
    project = client.post("/api/projects", json={"name": "Delete"}).json()
    first = upload(client, wav_bytes, project)
    second = upload(client, wav_bytes, project)
    unrelated = upload(client, wav_bytes)
    response = client.delete(f"/api/projects/{project['id']}")
    assert response.status_code == 200, response.text
    for row in (first, second):
        assert client.get(f"/api/recordings/{row['recording']['id']}").status_code == 404
    assert client.app.state.store.job(unrelated["job"]["id"])["state"] == "queued"


def test_pending_project_delete_rejects_a_new_recording(client, wav_bytes):
    project = client.post("/api/projects", json={"name": "Delete"}).json()
    uploaded = upload(client, wav_bytes, project)
    store, queue = client.app.state.store, client.app.state.queue
    store.update_job(uploaded["job"]["id"], state="running")
    worker = next(worker for worker in queue.workers if worker.kind == "media")
    worker.active_id = uploaded["job"]["id"]
    route = f"/api/projects/{project['id']}"
    response = client.delete(route)
    assert response.status_code == 202, response.text
    added = upload(client, wav_bytes, project)
    drain_media(client, uploaded, worker)
    response = client.delete(route, params={"operation_id": response.json()["operation_id"]})
    assert response.status_code == 409, response.text
    assert store.job(added["job"]["id"])["state"] == "queued"
    assert client.get(f"/api/recordings/{uploaded['recording']['id']}").status_code == 200


def native_jobs(client):
    store = client.app.state.store
    project = store.create_project("Shared native delete")
    rows = [store.create_recording(project["id"], f"{name}.wav") for name in ("one", "two")]
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=copy.deepcopy(SETUP),
    ).model_dump(mode="json")
    jobs = store.add_transcription_jobs(
        [
            {"recording_id": row["id"], "params": {"settings": settings, "provider_name": "Native"}}
            for row in rows
        ]
    )
    return rows, jobs


def test_shared_native_cancel_requires_concrete_confirmation_before_any_cancel(client):
    store = client.app.state.store
    recordings, jobs = native_jobs(client)
    route = f"/api/recordings/{recordings[0]['id']}"
    response = client.delete(route, params={"confirm_shared": True})
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "confirm_shared_cancellation"
    assert detail["affected_recordings"] == [
        {"id": recordings[1]["id"], "name": recordings[1]["name"]}
    ]
    assert {store.job(job["id"])["state"] for job in jobs} == {"queued"}
    response = client.delete(
        route, params={"operation_id": detail["operation_id"], "confirm_shared": True}
    )
    assert response.status_code == 200, response.text
    assert store.recording(recordings[1]["id"])
    assert store.job(jobs[1]["id"])["state"] == "cancelled"


def test_shared_membership_change_invalidates_confirmation_without_cancelling(client):
    import json

    store = client.app.state.store
    recordings, jobs = native_jobs(client)
    route = f"/api/recordings/{recordings[0]['id']}"
    response = client.delete(route)
    detail = response.json()["detail"]
    batch_id = jobs[0]["params"]["native_batch_id"]
    batch = store.native_batch(batch_id)
    third = store.create_recording(recordings[0]["project_id"], "late.wav")
    late = store.add_job(third["id"], "media")
    batch["members"].append(
        {"job_id": late["id"], "recording_id": third["id"], "filename": "late.wav"}
    )
    store.execute(
        "UPDATE native_batch_runs SET members=? WHERE id=?",
        (json.dumps(batch["members"]), batch_id),
    )
    response = client.delete(
        route, params={"operation_id": detail["operation_id"], "confirm_shared": True}
    )
    assert response.status_code == 409, response.text
    assert {store.job(job["id"])["state"] for job in jobs} == {"queued"}


@pytest.mark.parametrize("run", [{"submission_intent": True}, {"cleanup_state": "pending"}])
def test_remote_obligations_are_retained_and_never_imply_discard(client, wav_bytes, run):
    uploaded = upload(client, wav_bytes)
    store = client.app.state.store
    store.update_job(uploaded["job"]["id"], state="completed")
    job = store.add_job(uploaded["recording"]["id"], "enhance", {"ucloud_run": run})
    store.update_job(job["id"], state="failed")
    response = client.delete(f"/api/recordings/{uploaded['recording']['id']}")
    assert response.status_code == 409, response.text
    assert store.job(job["id"])["params"]["ucloud_run"] == run
    assert (store.directory(uploaded["recording"]["id"]) / "original.wav").read_bytes() == wav_bytes


def test_reserved_inspection_and_intent_use_fenced_cancel(client, wav_bytes, monkeypatch):
    uploaded = upload(client, wav_bytes)
    store = client.app.state.store
    provider = providers.public_profiles(store, client.app.state.credentials)["providers"][0]
    provider = providers.profile(store, provider["id"])
    calls = []
    cancel_source, cancel_input = original_inspection.cancel, input_admission.cancel

    def source(*args, **kwargs):
        calls.append(("source", kwargs))
        return cancel_source(*args, **kwargs)

    def intent(*args, **kwargs):
        calls.append(("intent", kwargs))
        return cancel_input(*args, **kwargs)

    monkeypatch.setattr(original_inspection, "cancel", source)
    monkeypatch.setattr(input_admission, "cancel", intent)
    with store.library_lock():
        input_admission.accept(
            store,
            [
                {
                    "recording_id": uploaded["recording"]["id"],
                    "choice": input_admission.choice(provider=provider),
                }
            ],
        )
        response = client.delete(f"/api/recordings/{uploaded['recording']['id']}")
    assert response.status_code == 200, response.text
    assert {kind for kind, _ in calls} == {"source", "intent"}
    assert all(
        set(token) == ({"generation", "epoch"} if kind == "source" else {"epoch", "phase"})
        for kind, token in calls
    )


def test_native_pending_cohort_is_resolved_before_deleting_its_member(client, wav_bytes):
    store = client.app.state.store
    project = store.create_project("Pending cohort")
    recordings = [upload(client, wav_bytes, project)["recording"] for _ in range(2)]
    saved = providers.save_profile(
        store,
        client.app.state.credentials,
        "Native",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            ucloud_transcriber=copy.deepcopy(SETUP),
        ),
        remember=False,
    )
    provider = providers.profile(store, saved["id"])
    with store.library_lock():
        jobs = input_admission.accept(
            store,
            [
                {"recording_id": row["id"], "choice": input_admission.choice(provider=provider)}
                for row in recordings
            ],
        )
        route = f"/api/recordings/{recordings[0]['id']}"
        response = client.delete(route)
        assert response.status_code == 202, response.text
        operation_id = response.json()["operation_id"]
        assert store.job(jobs[0]["id"])["state"] == "cancelled"
        assert store.job(jobs[1]["id"])["state"] == "queued"
        assert store.recording(recordings[0]["id"])
        # Normal admission still needs both original member rows to resolve the cohort.
        remaining = store.job(jobs[1]["id"])["params"]["input_intent"]
        input_admission.cancel(
            store, jobs[1]["id"], epoch=remaining["epoch"], phase=remaining["phase"]
        )
        token = input_admission.claim(store)
        assert token is not None
        input_admission.materialize(
            store, token, {job["id"]: {"error": "cancelled"} for job in jobs}
        )
        response = client.delete(route, params={"operation_id": operation_id})
        assert response.status_code == 200, response.text
    assert store.recording(recordings[1]["id"])
    assert not store.all("SELECT id FROM native_batch_runs")


def test_new_frozen_followup_is_kept_and_requires_fresh_delete(client, wav_bytes):
    uploaded = upload(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    store.update_job(uploaded["job"]["id"], state="completed")
    child_id = new_id()
    parent = store.add_job(
        uploaded["recording"]["id"], "transcribe", {"followup": {"id": child_id}}
    )
    store.update_job(parent["id"], state="running")
    queue.active_id = parent["id"]
    route = f"/api/recordings/{uploaded['recording']['id']}"
    response = client.delete(route)
    assert response.status_code == 202, response.text
    operation_id = response.json()["operation_id"]
    store.update_job(parent["id"], state="cancelled")
    queue.active_id = None
    with store.connection() as db:
        store._insert_job(
            db,
            uploaded["recording"]["id"],
            "enhance",
            {"parent_transcription_id": parent["id"]},
            child_id,
        )
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 409, response.text
    assert store.job(child_id)["state"] == "queued"


def test_generic_cancel_runs_outside_outer_queue_and_snapshot_locks(client, wav_bytes, monkeypatch):
    uploaded = upload(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    original = queue.cancel
    checked = []

    def cancel(*args, **kwargs):
        def observe():
            acquired_queue = queue.lock.acquire(blocking=False)
            acquired_snapshot = store.snapshot_lock.acquire(blocking=False)
            checked.append((acquired_queue, acquired_snapshot))
            if acquired_snapshot:
                store.snapshot_lock.release()
            if acquired_queue:
                queue.lock.release()

        observer = threading.Thread(target=observe)
        observer.start()
        observer.join(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, "cancel", cancel)
    response = client.delete(f"/api/recordings/{uploaded['recording']['id']}")
    assert response.status_code == 200, response.text
    assert checked == [(True, True)]


def test_shared_runtime_cancel_requires_consent_and_stops_only_its_allocation(client, prepared):
    store, project, provider = prepared
    recordings = [add_recording(store, project) for _ in range(2)]
    response = transcribe(client, [provider["id"]], [row["id"] for row in recordings])
    assert response.status_code == 202, response.text
    jobs = inspect_and_materialize(
        store, client.app.state.credentials, [row["id"] for row in response.json()]
    )
    route = f"/api/recordings/{recordings[0]['id']}"
    response = client.delete(route)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "confirm_shared_cancellation"
    assert {store.job(job["id"])["state"] for job in jobs} == {"queued"}
    response = client.delete(
        route, params={"operation_id": detail["operation_id"], "confirm_shared": True}
    )
    assert response.status_code == 200, response.text
    assert store.recording(recordings[1]["id"])
    assert store.job(jobs[1]["id"])["state"] == "cancelled"


def test_explicit_retry_revokes_pending_delete_even_after_another_failure(client, wav_bytes):
    uploaded, worker, route, operation_id = pending_media_delete(client, wav_bytes)
    drain_media(client, uploaded, worker)
    store = client.app.state.store
    response = client.post(f"/api/jobs/{uploaded['job']['id']}/retry", json={})
    assert response.status_code == 200, response.text
    store.update_job(uploaded["job"]["id"], state="failed")
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 409, response.text
    assert store.recording(uploaded["recording"]["id"])


def test_remote_cancellation_failure_preserves_recording_and_unacknowledged_journal(
    client, wav_bytes, monkeypatch
):
    uploaded = upload(client, wav_bytes)
    store = client.app.state.store
    store.update_job(uploaded["job"]["id"], state="completed")
    run = {"job_id": "owned-remote", "remote_state": "RUNNING"}
    job = store.add_job(
        uploaded["recording"]["id"], "enhance", {"ucloud_api": {}, "ucloud_run": run}
    )

    def failed(*args):
        raise ValueError("Synthetic unavailable Stop endpoint")

    monkeypatch.setattr("cbs_transcriber.enhancement_api.terminate_owned", failed)
    route = f"/api/recordings/{uploaded['recording']['id']}"
    response = client.delete(route)
    assert response.status_code == 409, response.text
    assert "kept" in response.json()["detail"]
    assert store.job(job["id"])["params"]["ucloud_run"] == run
    assert (store.directory(uploaded["recording"]["id"]) / "original.wav").read_bytes() == wav_bytes


def test_completed_parent_cancel_preserves_child_but_record_delete_captures_child(
    client, wav_bytes
):
    uploaded = upload(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    store.update_job(uploaded["job"]["id"], state="completed")
    child_id = new_id()
    parent = store.add_job(
        uploaded["recording"]["id"], "transcribe", {"followup": {"id": child_id}}
    )
    store.update_job(parent["id"], state="completed")
    with store.connection() as db:
        store._insert_job(
            db,
            uploaded["recording"]["id"],
            "enhance",
            {"parent_transcription_id": parent["id"]},
            child_id,
        )
    queue.cancel(parent["id"])
    assert store.job(child_id)["state"] == "queued"
    response = client.delete(f"/api/recordings/{uploaded['recording']['id']}")
    assert response.status_code == 200, response.text


def test_drain_checks_active_ids_even_after_active_id_has_cleared(client, wav_bytes):
    uploaded, worker, route, operation_id = pending_media_delete(client, wav_bytes)
    drain_media(client, uploaded, worker)
    worker.active_ids = [uploaded["job"]["id"]]
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 202, response.text
    worker.active_ids = []
    assert client.delete(route, params={"operation_id": operation_id}).status_code == 200


def test_delete_rejects_linked_export_parent_without_touching_external_files(
    client, wav_bytes, tmp_path
):
    uploaded = upload(client, wav_bytes)
    store = client.app.state.store
    identifier = uploaded["recording"]["id"]
    external = tmp_path / "external"
    external.mkdir()
    child = external / identifier
    child.mkdir()
    sentinel = child / "keep.txt"
    sentinel.write_text("Preserve")
    exports = store.project_folder(uploaded["recording"]["project_id"]) / "exports"
    try:
        exports.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    response = client.delete(f"/api/recordings/{identifier}")
    assert response.status_code == 409, response.text
    assert sentinel.read_text() == "Preserve"
    assert (store.directory(identifier) / "original.wav").exists()


def test_commit_failure_does_not_cache_successful_deletion(client, wav_bytes, monkeypatch):
    uploaded, worker, route, operation_id = pending_media_delete(client, wav_bytes)
    drain_media(client, uploaded, worker)
    store = client.app.state.store
    connection = store.connection
    identifier = uploaded["recording"]["id"]
    injected = []

    @contextmanager
    def fail_deletion_commit():
        with connection() as db:
            yield db
            deleted = not db.execute(
                "SELECT id FROM recordings WHERE id=?", (identifier,)
            ).fetchone()
            if db.total_changes and deleted and not injected:
                injected.append(True)
                db.rollback()
                raise OSError("Synthetic failed deletion commit")

    monkeypatch.setattr(store, "connection", fail_deletion_commit)
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 409, response.text
    assert not client.app.state.deletions.operations[operation_id].complete
    assert store.recording(identifier)
    assert (store.directory(identifier) / "original.wav").read_bytes() == wav_bytes
    monkeypatch.setattr(store, "connection", connection)
    assert client.delete(route, params={"operation_id": operation_id}).status_code == 200


def test_committed_delete_publishes_success_before_releasing_claim_lock(
    client, wav_bytes, monkeypatch
):
    uploaded, worker, route, operation_id = pending_media_delete(client, wav_bytes)
    drain_media(client, uploaded, worker)
    store, queue = client.app.state.store, client.app.state.queue
    original_lock = queue.lock
    observed = []
    identifier = uploaded["recording"]["id"]

    class ObserveRelease:
        def __enter__(self):
            return original_lock.__enter__()

        def __exit__(self, *args):
            try:
                # A second finish request can acquire this lock immediately after exit.
                # It must already see the committed operation's successful cached reply.
                if not store.all("SELECT id FROM recordings WHERE id=?", (identifier,)):
                    observed.append(client.app.state.deletions.operations[operation_id].complete)
            finally:
                original_lock.__exit__(*args)

    monkeypatch.setattr(queue, "lock", ObserveRelease())
    response = client.delete(route, params={"operation_id": operation_id})
    assert response.status_code == 200, response.text
    assert observed and all(observed)


def test_reserved_inspection_retry_revokes_pending_delete(client, wav_bytes):
    uploaded = upload(client, wav_bytes)
    store, queue = client.app.state.store, client.app.state.queue
    with store.library_lock():
        cache = original_inspection.ensure(store, uploaded["recording"]["id"])
        token = original_inspection.claim(store, cache["inspection_job_id"])
        worker = next(worker for worker in queue.workers if worker.kind == "source-inspect")
        worker.active_id = cache["inspection_job_id"]
        route = f"/api/recordings/{uploaded['recording']['id']}"
        response = client.delete(route)
        assert response.status_code == 202, response.text
        operation_id = response.json()["operation_id"]
        finished = original_inspection.run(store, token)
        assert finished["error"]["code"] == "cancelled"
        worker.active_id = None
        response = client.post(
            f"/api/input-inspections/{cache['inspection_job_id']}/retry",
            json={"generation": finished["generation"], "epoch": finished["epoch"]},
        )
        assert response.status_code == 200, response.text
        response = client.delete(route, params={"operation_id": operation_id})
        assert response.status_code == 409, response.text
        assert original_inspection.read(store, uploaded["recording"]["id"])["state"] == "pending"


def test_shared_batch_worker_must_release_allocation_before_file_deletion(client):
    store, queue = client.app.state.store, client.app.state.queue
    recordings, jobs = native_jobs(client)
    batch_id = jobs[0]["params"]["native_batch_id"]
    assert store.claim_native_batch(batch_id)
    queue.active_batch_id = batch_id
    route = f"/api/recordings/{recordings[0]['id']}"
    response = client.delete(route)
    detail = response.json()["detail"]
    params = {"operation_id": detail["operation_id"], "confirm_shared": True}
    response = client.delete(route, params=params)
    assert response.status_code == 202, response.text
    assert queue.cancel_event.is_set()
    store.finish_native_batch(batch_id, "cancelled", "Synthetic drain finished")
    assert client.delete(route, params=params).status_code == 202
    assert store.recording(recordings[0]["id"])
    queue.active_batch_id = None
    assert client.delete(route, params=params).status_code == 200


@pytest.mark.parametrize("corrupt", [False, True])
def test_job_control_bytes_are_bounded_before_parse_or_cancellation(
    client, wav_bytes, monkeypatch, corrupt
):
    from cbs_transcriber import deletions

    uploaded = upload(client, wav_bytes)
    store = client.app.state.store
    identifier = uploaded["recording"]["id"]
    if corrupt:
        raw = "not-json-private-" * 20
        store.execute("UPDATE jobs SET params=? WHERE id=?", (raw, uploaded["job"]["id"]))
    else:
        store.update_job(uploaded["job"]["id"], params={"marker": "x" * 80})
        store.add_job(identifier, "media", {"marker": "y" * 80})
    monkeypatch.setattr(deletions, "MAX_CONTROL_BYTES", 128)
    response = client.delete(f"/api/recordings/{identifier}")
    assert response.status_code == 409, response.text
    assert "not-json-private" not in response.text
    assert store.all("SELECT state FROM jobs WHERE recording_id=?", (identifier,))
    assert {
        row["state"]
        for row in store.all("SELECT state FROM jobs WHERE recording_id=?", (identifier,))
    } == {"queued"}
    assert (store.directory(identifier) / "original.wav").exists()
