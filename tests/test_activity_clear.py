"""Clear finished only records when Activity was cleared; no job is deleted (V105)."""

from datetime import datetime

from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app


def test_clear_finished_records_a_timestamp_and_keeps_jobs(client, tmp_path, credentials):
    store = client.app.state.store
    project = store.create_project("Activity")
    recording = store.create_recording(project["id"], "a.wav")
    job = store.add_job(recording["id"], "media")
    store.update_job(job["id"], state="completed", progress=1)

    assert client.get("/api/activity").json() == {"cleared_at": None, "hidden": {}}
    cleared = client.post("/api/activity/clear").json()["cleared_at"]
    assert datetime.fromisoformat(cleared) >= datetime.fromisoformat(
        store.job(job["id"])["updated"]
    )
    assert client.get("/api/activity").json() == {"cleared_at": cleared, "hidden": {}}
    # The job row and its listing are untouched; hiding happens only in the Activity view.
    assert [row["id"] for row in client.get("/api/jobs").json()] == [job["id"]]
    assert client.get("/api/jobs").json()[0]["updated"]

    later = client.post("/api/activity/clear").json()["cleared_at"]
    assert datetime.fromisoformat(later) >= datetime.fromisoformat(cleared)

    reopened = create_app(tmp_path, start_worker=False, credentials=credentials)
    with TestClient(reopened, headers={"X-CBS-Request": "1"}) as second:
        assert second.get("/api/activity").json() == {"cleared_at": later, "hidden": {}}


def test_clear_finished_is_a_same_origin_mutation(client):
    response = client.post("/api/activity/clear", headers={"Origin": "https://example.org"})
    assert response.status_code == 403
    assert client.get("/api/activity").json() == {"cleared_at": None, "hidden": {}}
