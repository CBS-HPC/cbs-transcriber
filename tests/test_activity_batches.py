"""Activity presents shared native work without replacing per-recording job identities."""

import json
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app
from cbs_transcriber.store import now


@pytest.fixture
def activity_batch(client):
    store = client.app.state.store
    project = store.create_project("Synthetic Activity batch")
    recordings = [
        store.create_recording(project["id"], name)
        for name in ("first.wav", "second.wav", "third.wav", "fourth.wav")
    ]
    jobs = store.add_transcription_jobs(
        [
            {
                "recording_id": recording["id"],
                "params": {
                    "provider_id": "synthetic-native",
                    "provider_name": "Synthetic UCloud",
                    "settings": {
                        "provider_type": "ucloud",
                        "base_url": "https://cloud.invalid",
                        "model": "large-v3",
                    },
                    "private_field": "private-job-canary",
                },
            }
            for recording in recordings
        ]
    )
    return store, recordings, jobs, jobs[0]["params"]["native_batch_id"]


def listed_batch(client, batch_id):
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    return [row for row in response.json() if row["ucloud_batch_id"] == batch_id]


def age_out_jobs(store, recording_id):
    stamp = now()
    with store.connection() as db:
        db.executemany(
            "INSERT INTO jobs(id,recording_id,kind,state,created,updated) "
            "VALUES (?,?,'media','completed',?,?)",
            [(uuid4().hex, recording_id, stamp, stamp) for _ in range(201)],
        )


def test_v84_activity_uses_shared_batch_state_and_safe_per_file_outcomes(client, activity_batch):
    store, recordings, jobs, batch_id = activity_batch
    for job in jobs:
        store.update_job(job["id"], state="running", chunks=[{"text": "private-chunk-canary"}])
    result = store.add_result(
        recordings[0]["id"],
        [{"id": "p1", "text": "private-transcript-canary", "start": 0, "end": 1, "speaker": "A"}],
        {},
        "Synthetic result",
        {"kind": "provider"},
        job_id=jobs[0]["id"],
    )
    store.update_native_batch(
        batch_id,
        state="running",
        progress=0.65,
        message="Importing the remaining batch outputs",
        run_update={"job_id": "remote-batch", "remote_state": "SUCCESS"},
    )
    batch = store.native_batch(batch_id)
    rows = listed_batch(client, batch_id)
    assert len(rows) == 4
    assert {row["id"] for row in rows} == {job["id"] for job in jobs}
    assert {row["state"] for row in rows} == {"completed", "running"}
    for row in rows:
        assert row["ucloud_batch_state"] == "running"
        assert row["ucloud_batch_progress"] == 0.65
        assert row["ucloud_batch_message"] == batch["message"]
        assert row["ucloud_batch_created"] == batch["created"]
        assert row["ucloud_batch_updated"] == batch["updated"]
        assert row["ucloud_batch_unresolved"] is True
        assert row["ucloud_job_id"] == "remote-batch"
        assert row["ucloud_batch_count"] == 4
        members = row["ucloud_batch_members"]
        assert [member["job_id"] for member in members] == [job["id"] for job in jobs]
        assert [member["recording_name"] for member in members] == [
            recording["name"] for recording in recordings
        ]
        assert members[0]["state"] == "completed" and members[0]["result_id"] == result["id"]
        assert all(member["state"] == "running" for member in members[1:])
        assert set(members[0]) == {
            "job_id",
            "recording_id",
            "recording_name",
            "state",
            "progress",
            "message",
            "result_id",
        }
    payload = json.dumps(rows)
    assert "private-job-canary" not in payload
    assert "private-chunk-canary" not in payload
    assert "private-transcript-canary" not in payload


@pytest.mark.parametrize(
    "run",
    [
        {"job_id": "remote-batch", "remote_state": "RUNNING"},
        {"job_id": "remote-batch"},
        {"submission_intent": True},
        {"job_id": "remote-batch", "remote_state": "SUCCESS", "cleanup_state": "pending"},
    ],
)
def test_v84_v105_unresolved_batches_survive_the_recent_job_limit(client, activity_batch, run):
    store, recordings, jobs, batch_id = activity_batch
    store.update_job(jobs[0]["id"], state="completed", progress=1)
    store.finish_native_batch(batch_id, "failed", "Recover this shared allocation")
    store.update_native_batch(batch_id, run_update=run)
    age_out_jobs(store, recordings[0]["id"])
    rows = listed_batch(client, batch_id)
    assert {row["id"] for row in rows} == {job["id"] for job in jobs}
    assert all(row["ucloud_batch_unresolved"] for row in rows)


def test_v105_resolved_terminal_batches_can_age_out(client, activity_batch):
    store, recordings, _, batch_id = activity_batch
    store.finish_native_batch(batch_id, "failed", "Remote allocation ended")
    store.update_native_batch(
        batch_id, run_update={"job_id": "remote-batch", "remote_state": "FAILURE"}
    )
    age_out_jobs(store, recordings[0]["id"])
    assert listed_batch(client, batch_id) == []


def test_v105_clear_and_reload_keep_pending_cleanup_and_canonical_updates(
    client, activity_batch, tmp_path, credentials
):
    store, _, jobs, batch_id = activity_batch
    store.finish_native_batch(batch_id, "completed", "All four results saved")
    store.update_native_batch(
        batch_id,
        run_update={
            "job_id": "remote-batch",
            "remote_state": "SUCCESS",
            "cleanup_state": "pending",
            "cleanup_stage": "confirmation",
            "cleanup_error_code": "folder_still_listed",
            "cleanup_http_status": 403,
            "cleanup_attempted_at": now(),
        },
    )
    original = store.native_batch(batch_id)
    job_updates = {job["id"]: store.job(job["id"])["updated"] for job in jobs}
    cleared = client.post("/api/activity/clear").json()["cleared_at"]
    rows = listed_batch(client, batch_id)
    assert len(rows) == 4
    assert all(row["ucloud_batch_unresolved"] for row in rows)
    assert rows[0]["ucloud_cleanup_stage"] == "confirmation"
    assert rows[0]["ucloud_cleanup_error_code"] == "folder_still_listed"
    assert rows[0]["ucloud_cleanup_http_status"] == 403
    assert rows[0]["ucloud_cleanup_attempted_at"]
    assert store.native_batch(batch_id) == original
    assert datetime.fromisoformat(rows[0]["ucloud_batch_updated"]) <= datetime.fromisoformat(
        cleared
    )

    reopened = create_app(tmp_path, start_worker=False, credentials=credentials)
    with TestClient(reopened, headers={"X-CBS-Request": "1"}) as second:
        assert second.get("/api/activity").json() == {"cleared_at": cleared, "hidden": {}}
        assert listed_batch(second, batch_id) == rows
        # Resolving cleanup updates only the canonical journal, so Activity must use its time.
        reopened.state.store.update_native_batch(batch_id, run_update={"cleanup_state": "trashed"})
        resolved = listed_batch(second, batch_id)
        assert all(not row["ucloud_batch_unresolved"] for row in resolved)
        assert all(row["updated"] == job_updates[row["id"]] for row in resolved)
        assert datetime.fromisoformat(resolved[0]["ucloud_batch_updated"]) > datetime.fromisoformat(
            cleared
        )
    assert store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 4


def test_v84_activity_retains_removed_member_outcomes(client, activity_batch):
    store, recordings, _, batch_id = activity_batch
    store.finish_native_batch(batch_id, "completed", "All four results saved")
    store.execute("DELETE FROM recordings WHERE id=?", (recordings[-1]["id"],))
    rows = listed_batch(client, batch_id)
    assert len(rows) == 3
    removed = rows[0]["ucloud_batch_members"][-1]
    assert rows[0]["ucloud_batch_count"] == 4
    assert removed["recording_name"] == "Removed recording"
    assert removed["state"] == "removed" and removed["result_id"] is None
