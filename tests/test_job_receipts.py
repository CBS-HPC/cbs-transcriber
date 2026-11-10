"""Committed results are job receipts: late stops, cancels and claim errors cannot undo them."""

import copy
import time

from cbs_transcriber import native_batch, providers, runtime_store
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments


def ready_recording(store, name="a.wav", project=None):
    project = project or store.create_project("Receipts")
    recording = store.create_recording(project["id"], name)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    return recording


def segments():
    return normalize_segments([{"text": "Hej.", "start": 0, "end": 1}])


def test_saving_a_result_completes_its_running_job_in_the_same_transaction(client):
    store = client.app.state.store
    recording = ready_recording(store)
    job = store.add_job(recording["id"], "transcribe", {"provider_name": "P"})
    store.update_job(job["id"], state="running")
    store.add_result(recording["id"], segments(), {}, "R", {}, job_id=job["id"])
    assert store.job(job["id"])["state"] == "completed"


def test_cancelling_a_completed_parent_keeps_its_followup(client):
    store, queue = client.app.state.store, client.app.state.queue
    recording = ready_recording(store)
    child_id = "f" * 32
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"provider_name": "P", "followup": {"id": child_id, "params": {"enhancement": {}}}},
    )
    store.update_job(job["id"], state="running")
    store.add_result(recording["id"], segments(), {}, "R", {}, job_id=job["id"])
    assert store.job(child_id)["state"] == "queued"
    queue.cancel(job["id"])  # A Stop click that arrives just after the commit.
    assert store.job(job["id"])["state"] == "completed"
    assert store.job(child_id)["state"] == "queued"


def test_a_stop_after_commit_never_downgrades_the_worker_receipt(client, monkeypatch):
    store, queue = client.app.state.store, client.app.state.queue
    recording = ready_recording(store)
    job = store.add_job(recording["id"], "transcribe", {"provider_name": "P"})
    for worker in queue.workers:

        def process(identifier, worker=worker):
            store.add_result(recording["id"], segments(), {}, "R", {}, job_id=identifier)
            worker.cancel_event.set()  # The stop lands between commit and the final update.

        monkeypatch.setattr(worker, "process", process)
    with store.library_lock():
        queue.start()
        try:
            deadline = time.monotonic() + 10
            while store.job(job["id"])["state"] in {"queued", "running"}:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        finally:
            queue.stop()
        assert store.job(job["id"])["state"] == "completed"
        assert len(store.results(recording["id"])) == 1


def test_a_batch_whose_members_changed_fails_once_instead_of_blocking_claims(client):
    store = client.app.state.store
    project = store.create_project("Batch")
    first = ready_recording(store, "a.wav", project)
    second = ready_recording(store, "b.wav", project)
    settings = {"provider_type": "ucloud", "base_url": "https://cloud.invalid", "model": "m"}
    jobs = store.add_transcription_jobs(
        [
            {"recording_id": r["id"], "params": {"settings": settings, "provider_name": "U"}}
            for r in (first, second)
        ]
    )
    store.update_job(jobs[1]["id"], state="failed")
    assert store.claim_job("transcribe") is None
    batch = store.native_batch(jobs[0]["params"]["native_batch_id"])
    assert batch["state"] == "failed" and "member state changed" in batch["message"]
    assert store.claim_job("transcribe") is None


def test_stopping_a_shared_batch_also_stops_member_followups(client):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Batch")
    recordings = [ready_recording(store, name, project) for name in ("a.wav", "b.wav")]
    settings = {"provider_type": "ucloud", "base_url": "https://cloud.invalid", "model": "m"}
    jobs = store.add_transcription_jobs(
        [
            {
                "recording_id": r["id"],
                "params": {
                    "settings": settings,
                    "provider_name": "U",
                    "followup": {"id": str(index) * 32, "params": {"enhancement": {}}},
                },
            }
            for index, r in enumerate(recordings, 1)
        ]
    )
    claimed = store.claim_job("transcribe")
    assert claimed is not None
    # The first member's transcript is saved; its follow-up enhancement is queued.
    store.add_result(recordings[0]["id"], segments(), {}, "R", {}, job_id=jobs[0]["id"])
    child = jobs[0]["params"]["followup"]["id"]
    assert store.job(child)["state"] == "queued"
    queue.cancel(jobs[1]["id"])
    assert store.job(jobs[0]["id"])["state"] == "completed"
    assert store.job(jobs[1]["id"])["state"] == "cancelled"
    assert store.job(child)["state"] == "cancelled"


def native_with_runtime_followups(client, *, foreign_parent=False):
    store = client.app.state.store
    project = store.create_project("Synthetic pooled follow-up receipts")
    settings = providers.Settings(
        provider_type="ucloud", base_url="https://runtime.invalid", model="large-v3"
    )
    provider_id = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (provider_id, "Synthetic runtime", settings.model_dump_json(), now()),
    )
    params = {
        "enhancement": {"execution": "ucloud", "device": "cuda", "align": True},
        "ucloud_api": {
            "provider_id": provider_id,
            "settings": settings.model_dump(),
            "runtime_id": "synthetic-runtime",
            "runtime_product": dict(MIG_PRODUCT),
            "runtime_compute_policy": "mig-v1",
            "batch_plan": {
                "identity": {
                    "protocol": runtime_store.PROTOCOL,
                    "application": {"name": "terminal-ubuntu", "version": "synthetic"},
                    "code_hashes": {"supervisor": "1" * 64},
                    "recipe_hash": "2" * 64,
                    "model_revisions": {"da": "pinned"},
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

    def create_parents():
        recordings = [ready_recording(store, project=project) for _ in range(2)]
        jobs = store.add_transcription_jobs(
            [
                {
                    "recording_id": row["id"],
                    "params": {
                        "settings": {
                            "provider_type": "ucloud",
                            "base_url": "https://native.invalid",
                            "model": "synthetic-native",
                        },
                        "provider_name": "Synthetic native",
                        "followup": {"id": new_id(), "params": copy.deepcopy(params)},
                    },
                }
                for row in recordings
            ]
        )
        assert store.claim_job("transcribe") is not None
        for row, parent in zip(recordings, jobs, strict=True):
            store.add_result(row["id"], segments(), {}, "Native result", {}, job_id=parent["id"])
        return jobs

    parents = create_parents()
    foreign = create_parents() if foreign_parent else []
    followups = [store.job(parent["params"]["followup"]["id"]) for parent in parents]
    owner = runtime_store.for_job(store, followups[0])
    assert len(owner["members"]) == (4 if foreign_parent else 2)
    return parents, followups, owner, foreign


def test_native_stop_preserves_runtime_owner_shared_with_another_parent_batch(client):
    store = client.app.state.store
    parents, followups, owner, foreign = native_with_runtime_followups(client, foreign_parent=True)
    response = client.post(f"/api/jobs/{parents[0]['id']}/cancel")
    assert response.status_code == 200, response.text
    assert runtime_store.get(store, owner["id"]) == owner
    assert all(store.job(member["job_id"])["state"] == "queued" for member in owner["members"])
    assert response.json()["continued_runtime_batches"] == [owner["id"]]
    fields = native_batch.status_fields(store, store.job(parents[0]["id"]))
    assert fields["ucloud_batch_continued_runtime_batches"] == [owner["id"]]
    assert "shared CBS Runtime" in fields["ucloud_batch_message"]
    store.finish_native_batch(fields["ucloud_batch_id"], "cancelled", "Native worker settled")
    assert (
        "shared CBS Runtime"
        in native_batch.status_fields(store, store.job(parents[0]["id"]))["ucloud_batch_message"]
    )
    assert all(store.job(parent["id"])["state"] == "completed" for parent in parents + foreign)
    assert len(followups) == 2


def test_native_stop_cancels_runtime_owner_contained_in_its_own_followups(client):
    store, queue = client.app.state.store, client.app.state.queue
    parents, followups, owner, _ = native_with_runtime_followups(client)
    response = queue.cancel(parents[0]["id"])
    stopped = runtime_store.get(store, owner["id"])
    assert stopped["state"] == "cancelled" and stopped["journal"]["stop_intent"]
    assert all(store.job(child["id"])["state"] == "cancelled" for child in followups)
    assert response["continued_runtime_batches"] == []
    assert all(store.job(parent["id"])["state"] == "completed" for parent in parents)


def test_native_stop_preserves_a_foreign_runtime_member_attached_before_stop_cas(
    client, monkeypatch
):
    store, queue = client.app.state.store, client.app.state.queue
    parents, followups, owner, _ = native_with_runtime_followups(client)
    original = runtime_store.stop
    attached = []

    def attach_then_stop(received_store, batch_id, *, allowed_job_ids=None):
        if allowed_job_ids is not None and not attached:
            recording = ready_recording(
                store,
                project=store.one("SELECT * FROM projects WHERE id=?", (owner["project_id"],)),
            )
            result = store.add_result(recording["id"], segments(), {}, "Direct source", {})
            params = copy.deepcopy(followups[0]["params"])
            for key in ("runtime_batch_id", "parent_transcription_id", "source_snapshot"):
                params.pop(key, None)
            attached.append(
                store.enqueue_enhancement(
                    recording["id"],
                    result["id"],
                    store.recording(recording["id"])["revision"],
                    params,
                )
            )
        return original(received_store, batch_id, allowed_job_ids=allowed_job_ids)

    monkeypatch.setattr(runtime_store, "stop", attach_then_stop)
    response = queue.cancel(parents[0]["id"])
    assert response["continued_runtime_batches"] == [owner["id"]]
    retained = runtime_store.get(store, owner["id"])
    assert retained["admission"] == "open" and not retained["journal"].get("stop_intent")
    assert len(retained["members"]) == 3
    assert attached[0]["params"]["runtime_batch_id"] == owner["id"]
    assert all(store.job(m["job_id"])["state"] == "queued" for m in retained["members"])
