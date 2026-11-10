"""Remove from Activity hides one finished card; nothing is deleted (V162)."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from test_input_admission import inspect, item, native_provider
from test_input_preparation_consent import blocked, cbs_provider

from cbs_transcriber import input_admission, jobs
from cbs_transcriber.app import create_app
from cbs_transcriber.store import now

UCLOUD = {"provider_type": "ucloud", "base_url": "https://cloud.invalid", "model": "large-v3"}


def finished(store, recording_id, state="completed", params=None, kind="transcribe"):
    # Inserted directly as finished, as an existing library would hold them.
    identifier = uuid.uuid4().hex
    stamp = now()
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,progress,message,params,created,updated) "
        "VALUES (?,?,?,?,1,'Synthetic outcome',?,?,?)",
        (identifier, recording_id, kind, state, json.dumps(params or {}), stamp, stamp),
    )
    return identifier


@pytest.fixture
def recording(client):
    store = client.app.state.store
    project = store.create_project("Synthetic Activity removal")
    return store, store.create_recording(project["id"], "synthetic.wav")


def hide(client, identifier):
    return client.post("/api/activity/hide", json={"job_id": identifier})


def test_hide_records_update_time_and_keeps_every_row(client, recording, tmp_path, credentials):
    store, row = recording
    identifier = finished(store, row["id"], state="failed")
    before = store.one("SELECT * FROM jobs WHERE id=?", (identifier,))
    assert client.get("/api/activity").json() == {"cleared_at": None, "hidden": {}}

    response = hide(client, identifier)
    assert response.status_code == 200, response.text
    assert response.json() == {"cleared_at": None, "hidden": {identifier: before["updated"]}}
    assert client.get("/api/activity").json()["hidden"] == {identifier: before["updated"]}
    # Only the Activity view changes; the job row and its listing are untouched.
    assert store.one("SELECT * FROM jobs WHERE id=?", (identifier,)) == before
    assert [job["id"] for job in client.get("/api/jobs").json()] == [identifier]

    reopened = create_app(tmp_path, start_worker=False, credentials=credentials)
    with TestClient(reopened, headers={"X-CBS-Request": "1"}) as second:
        assert second.get("/api/activity").json()["hidden"] == {identifier: before["updated"]}


@pytest.mark.parametrize("state", ["queued", "running"])
def test_active_jobs_cannot_be_removed(client, recording, state):
    store, row = recording
    identifier = finished(store, row["id"], state=state)
    response = hide(client, identifier)
    assert response.status_code == 409
    assert "finishes" in response.json()["detail"]
    assert client.get("/api/activity").json()["hidden"] == {}


def test_unknown_or_malformed_identifiers_are_refused(client):
    assert hide(client, "a" * 32).status_code == 404
    assert hide(client, "runtime-batch-" + "b" * 32).status_code == 404
    assert hide(client, "../settings").status_code == 422
    assert client.post("/api/activity/hide", json={}).status_code == 422


@pytest.mark.parametrize(
    "state,run",
    [
        ("completed", {"job_id": "remote", "remote_state": "SUCCESS", "cleanup_state": "pending"}),
        ("interrupted", {"submission_attempted": True}),
        ("failed", {"job_id": "remote", "remote_state": "RUNNING"}),
        ("cancelled", {"submission_intent": True}),
    ],
)
def test_ucloud_obligations_stay_in_activity(client, recording, state, run):
    store, row = recording
    identifier = finished(store, row["id"], state, {"settings": UCLOUD, "ucloud_run": run})
    response = hide(client, identifier)
    assert response.status_code == 409
    assert "UCloud" in response.json()["detail"]
    assert client.get("/api/activity").json()["hidden"] == {}


@pytest.mark.parametrize(
    "state,run",
    [
        ("completed", {"job_id": "remote", "remote_state": "SUCCESS", "cleanup_state": "trashed"}),
        ("interrupted", {"submission_attempted": True, "reconciled_not_created": True}),
        ("failed", {"job_id": "remote", "remote_state": "RUNNING", "termination_requested": True}),
        ("failed", {"job_id": "remote", "remote_state": "FAILURE"}),
    ],
)
def test_resolved_ucloud_jobs_can_be_removed(client, recording, state, run):
    store, row = recording
    identifier = finished(store, row["id"], state, {"settings": UCLOUD, "ucloud_run": run})
    assert hide(client, identifier).status_code == 200


def test_changed_job_reappears_and_retried_job_is_refused(client, recording):
    store, row = recording
    identifier = finished(store, row["id"], state="failed")
    recorded = hide(client, identifier).json()["hidden"][identifier]
    store.update_job(identifier, message="Synthetic later outcome")
    current = store.job(identifier)["updated"]
    # The stored time no longer matches, so the Activity list shows the job again.
    assert current != recorded
    assert client.get("/api/activity").json()["hidden"] == {identifier: recorded}
    assert next(job for job in client.get("/api/jobs").json())["updated"] == current
    assert hide(client, identifier).json()["hidden"] == {identifier: current}
    store.update_job(identifier, state="queued")
    assert hide(client, identifier).status_code == 409


def test_clear_finished_and_removal_preserve_each_other(client, recording):
    store, row = recording
    first = finished(store, row["id"])
    second = finished(store, row["id"], state="cancelled")
    hidden = hide(client, first).json()["hidden"]
    cleared = client.post("/api/activity/clear").json()["cleared_at"]
    assert client.get("/api/activity").json() == {"cleared_at": cleared, "hidden": hidden}
    both = hide(client, second).json()
    assert both["cleared_at"] == cleared
    assert set(both["hidden"]) == {first, second}
    assert client.get("/api/activity").json() == both


def test_hidden_map_is_bounded_and_forgets_deleted_jobs(client, recording, monkeypatch):
    store, row = recording
    monkeypatch.setattr(jobs, "ACTIVITY_HIDDEN_LIMIT", 2)
    identifiers = [finished(store, row["id"]) for _ in range(4)]
    hide(client, identifiers[0])
    store.execute("DELETE FROM jobs WHERE id=?", (identifiers[0],))
    hide(client, identifiers[1])
    assert set(client.get("/api/activity").json()["hidden"]) == {identifiers[1]}
    hide(client, identifiers[2])
    hide(client, identifiers[3])
    # The oldest removal is forgotten first; a re-removal counts as the newest.
    assert list(client.get("/api/activity").json()["hidden"]) == identifiers[2:]
    hide(client, identifiers[2])
    assert list(client.get("/api/activity").json()["hidden"]) == [identifiers[3], identifiers[2]]


def test_hide_is_a_same_origin_mutation(client, recording):
    store, row = recording
    identifier = finished(store, row["id"])
    response = client.post(
        "/api/activity/hide", json={"job_id": identifier}, headers={"Origin": "https://example.org"}
    )
    assert response.status_code == 403
    assert client.get("/api/activity").json()["hidden"] == {}


def native_batch(store, project_id, names=("first.wav", "second.wav")):
    recordings = [store.create_recording(project_id, name) for name in names]
    rows = store.add_transcription_jobs(
        [
            {
                "recording_id": row["id"],
                "params": {
                    "provider_id": "synthetic-native",
                    "provider_name": "Synthetic UCloud",
                    "settings": UCLOUD,
                },
            }
            for row in recordings
        ]
    )
    return rows, rows[0]["params"]["native_batch_id"]


def test_native_batch_hides_as_one_card_only_when_resolved(client, recording):
    store, row = recording
    rows, batch_id = native_batch(store, row["project_id"])
    for job in rows:
        store.update_job(job["id"], state="completed")
    store.update_native_batch(
        batch_id,
        state="completed",
        run_update={"job_id": "remote", "remote_state": "SUCCESS", "cleanup_state": "pending"},
    )
    assert hide(client, rows[1]["id"]).status_code == 409
    store.update_native_batch(batch_id, run_update={"cleanup_state": "trashed"})
    batch = store.native_batch(batch_id)
    response = hide(client, rows[1]["id"])
    assert response.status_code == 200, response.text
    # Every member carries the shared card's time, so any member may represent it.
    assert response.json()["hidden"] == {job["id"]: batch["updated"] for job in rows}
    listed = [job for job in client.get("/api/jobs").json() if job["ucloud_batch_id"] == batch_id]
    assert {job["ucloud_batch_updated"] for job in listed} == {batch["updated"]}


@pytest.fixture
def lease(client):
    store = client.app.state.store
    with store.library_lock():
        yield store


def test_unresolved_native_selection_stays_in_activity(client, lease, credentials, wav_bytes):
    store = lease
    project = store.create_project("Synthetic native selection")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    provider = native_provider(store, credentials)
    accepted = input_admission.accept(store, [item(provider, row) for row in rows])
    inspect(store, rows[0], wav_bytes)
    intent = accepted[1]["params"]["input_intent"]
    input_admission.cancel(store, accepted[1]["id"], epoch=intent["epoch"], phase=intent["phase"])
    assert store.job(accepted[1]["id"])["state"] == "cancelled"
    projection = input_admission.projection(store, accepted[1]["id"])
    assert projection["phase"] == "terminal" and projection["native_group_resolved"] is False
    # The sibling still waits for its allocation decision, so the request stays visible.
    for job in accepted:
        assert hide(client, job["id"]).status_code == 409
    intent = store.job(accepted[0]["id"])["params"]["input_intent"]
    input_admission.cancel(store, accepted[0]["id"], epoch=intent["epoch"], phase=intent["phase"])
    # Both files are terminal, but the admission worker has not resolved the selection yet.
    assert {row["job_id"] for row in input_admission.obligations(store)} == {
        job["id"] for job in accepted
    }
    for job in accepted:
        response = hide(client, job["id"])
        assert response.status_code == 409
        assert "selection" in response.json()["detail"]
    assert client.get("/api/activity").json()["hidden"] == {}


def test_needs_confirmation_request_hides_every_file_together(
    client, lease, credentials, wav_bytes
):
    store = lease
    project = store.create_project("Synthetic consent request")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    provider = cbs_provider(store, credentials)
    old = blocked(store, provider, rows, wav_bytes)
    assert len({job["params"]["input_intent"]["request_id"] for job in old}) == 1
    listed = {job["id"]: job for job in client.get("/api/jobs").json()}
    for job in old:
        preparation = listed[job["id"]]["input_preparation"]
        assert preparation["phase"] == "terminal"
        assert preparation["error_code"] == "needs_confirmation"
        assert preparation["native_group_resolved"] is True
        assert preparation["request_id"] == job["params"]["input_intent"]["request_id"]
    before = [store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in old]
    response = hide(client, old[0]["id"])
    assert response.status_code == 200, response.text
    assert response.json()["hidden"] == {row["id"]: row["updated"] for row in before}
    assert [store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in old] == before
    # A request is one card: one waiting sibling keeps the whole request visible.
    waiting = input_admission.accept(store, [item(provider, row) for row in rows])
    assert {job["state"] for job in waiting} == {"queued"}
    intent = waiting[0]["params"]["input_intent"]
    input_admission.cancel(store, waiting[0]["id"], epoch=intent["epoch"], phase=intent["phase"])
    assert hide(client, waiting[0]["id"]).status_code == 409


def upload(client, project_id, wav_bytes, name, **fields):
    return client.post(
        "/api/recordings",
        data={"project_id": project_id, **fields},
        files={"file": (name, wav_bytes, "audio/wav")},
    )


@pytest.mark.parametrize("batch", ["A" * 32, "a" * 31, "a" * 33, "g" * 32, " " + "a" * 31])
def test_upload_batch_is_validated_before_any_copy(client, wav_bytes, batch):
    store = client.app.state.store
    project = store.create_project("Synthetic upload batch")
    response = upload(client, project["id"], wav_bytes, "one.wav", upload_batch=batch)
    assert response.status_code == 400
    assert "upload batch" in response.json()["detail"]
    assert not store.all("SELECT id FROM recordings")
    assert not store.all("SELECT id FROM jobs")


def test_upload_batch_groups_media_jobs_and_hides_together(client, wav_bytes):
    store = client.app.state.store
    project = store.create_project("Synthetic upload batch")
    batch = uuid.uuid4().hex
    created = [
        upload(client, project["id"], wav_bytes, name, upload_batch=batch).json()["job"]
        for name in ("one.wav", "two.wav", "three.wav")
    ]
    legacy = upload(client, project["id"], wav_bytes, "single.wav")
    assert legacy.status_code == 201, legacy.text
    assert store.job(legacy.json()["job"]["id"])["params"] == {}
    assert {job["params"]["upload_batch"] for job in created} == {batch}
    listed = {job["id"]: job for job in client.get("/api/jobs").json()}
    assert {listed[job["id"]]["upload_batch"] for job in created} == {batch}
    assert listed[legacy.json()["job"]["id"]]["upload_batch"] is None

    for job in created[:2]:
        store.update_job(job["id"], state="completed", progress=1)
    # One file is still waiting, so the whole upload card stays.
    assert hide(client, created[0]["id"]).status_code == 409
    store.update_job(created[2]["id"], state="failed", message="Synthetic decode failure")
    response = hide(client, created[0]["id"])
    assert response.status_code == 200, response.text
    assert response.json()["hidden"] == {
        job["id"]: store.job(job["id"])["updated"] for job in created
    }
    assert store.job(created[2]["id"])["params"] == {"upload_batch": batch}
