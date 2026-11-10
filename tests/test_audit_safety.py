"""Regression evidence from the post-implementation audit; synthetic data only."""

import json
from contextlib import contextmanager

import pytest

from cbs_transcriber import remote_jobs
from cbs_transcriber.transcripts import normalize_segments


@pytest.mark.parametrize("kind", ["transcribe", "enhance"])
@pytest.mark.parametrize("run", [{"job_id": "owned-job"}, {"submission_intent": True}])
def test_unresolved_remote_job_survives_recording_and_project_delete(client, kind, run):
    store = client.app.state.store
    project = store.create_project("Owned remote work")
    recording = store.create_recording(project["id"], "synthetic.wav")
    folder = store.directory(recording["id"])
    folder.mkdir(parents=True)
    (folder / "original.wav").write_bytes(b"synthetic original")
    job = store.add_job(recording["id"], kind, {"ucloud_run": run})
    store.update_job(job["id"], state="failed")
    for route in (f"/api/recordings/{recording['id']}", f"/api/projects/{project['id']}"):
        response = client.delete(route)
        assert response.status_code == 409, response.text
        assert store.job(job["id"])["params"]["ucloud_run"] == run
        assert (folder / "original.wav").read_bytes() == b"synthetic original"


def test_malformed_top_words_returns_curated_error(client):
    store = client.app.state.store
    project = store.create_project("Malformed import")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.directory(recording["id"]).mkdir(parents=True)
    response = client.post(
        f"/api/recordings/{recording['id']}/transcript",
        data={"revision": 0},
        files={
            "file": (
                "bad.json",
                json.dumps(
                    {
                        "segments": [{"start": 0, "end": 1, "text": "Test."}],
                        "words": ["not a word object"],
                    }
                ).encode(),
            )
        },
    )
    assert response.status_code == 400
    assert "not a word object" not in response.text
    assert not store.results(recording["id"])


def test_bad_timestamp_does_not_echo_imported_content():
    with pytest.raises(ValueError) as error:
        normalize_segments([{"text": "Test.", "start": "private sentinel content"}])
    assert "private sentinel content" not in str(error.value)


@pytest.mark.parametrize(
    "words",
    [
        [{"word": "One", "start": 1, "end": 1.5}, {"word": "two", "start": 0, "end": 0.5}],
        [{"word": "One", "start": 0, "end": 1}, {"word": "two", "start": 2, "end": 3}],
        [{"word": "One", "start": 0, "end": 0}, {"word": "two", "start": 1, "end": 2}],
    ],
)
def test_unreliable_word_intervals_do_not_enable_word_editing(words):
    segment = normalize_segments([{"text": "One two", "start": 0, "end": 2, "words": words}])[0]
    assert not segment["word_timing"]
    assert segment["original_words"] == segment["words"]


def test_classification_cannot_change_during_remote_enhancement(client):
    store = client.app.state.store
    project = store.create_project("Frozen policy")
    recording = store.create_recording(project["id"], "synthetic.wav")
    job = store.add_job(recording["id"], "enhance", {"enhancement": {"execution": "ucloud"}})
    store.update_job(job["id"], state="running")
    response = client.patch(
        f"/api/projects/{project['id']}/classification", json={"classification": "protected"}
    )
    assert response.status_code == 409
    assert (
        store.one("SELECT classification FROM projects WHERE id=?", (project["id"],))[
            "classification"
        ]
        == "standard"
    )


@pytest.mark.parametrize("kind", ["transcribe", "enhance"])
def test_uncertain_reconnect_checks_exact_run_identity_without_submit(client, monkeypatch, kind):
    store = client.app.state.store
    project = store.create_project("Reconnect")
    recording = store.create_recording(project["id"], "synthetic.wav")
    params = {
        "ucloud_run": {"submission_attempted": True},
        "settings": {"provider_type": "ucloud", "base_url": "https://cloud.invalid"},
        "cloud_settings": {"server": "https://cloud.invalid"},
    }
    job = store.add_job(recording["id"], kind, params)
    store.update_job(job["id"], state="failed")
    calls = []
    name = "CBS enhancement " if kind == "enhance" else "CBS Transcriber "
    remote = {"specification": {"name": name + "other"}, "status": {"state": "RUNNING"}}

    class Client:
        def retrieve_job(self, identifier):
            calls.append(identifier)
            return remote

    @contextmanager
    def connection(*args):
        yield Client()

    monkeypatch.setattr("cbs_transcriber.ucloud_native.client_for", connection)
    monkeypatch.setattr("cbs_transcriber.enhancement_cloud.client_for", connection)
    path = f"/api/jobs/{job['id']}/reconcile"
    assert client.post(path, json={"remote_id": "foreign"}).status_code == 400
    assert "job_id" not in store.job(job["id"])["params"]["ucloud_run"]
    remote["specification"]["name"] = name + job["id"]
    assert client.post(path, json={"remote_id": "owned"}).status_code == 200
    assert calls == ["foreign", "owned"]
    saved = store.job(job["id"])
    assert saved["params"]["ucloud_run"]["job_id"] == "owned"
    assert remote_jobs.unresolved(saved)
    assert client.post(path, json={"remote_id": "again"}).status_code == 400


def test_explicit_absence_receipt_retains_intent_and_allows_delete(client):
    store = client.app.state.store
    project = store.create_project("Reconciled absence")
    recording = store.create_recording(project["id"], "synthetic.wav")
    job = store.add_job(recording["id"], "enhance", {"ucloud_run": {"submission_intent": True}})
    store.update_job(job["id"], state="cancelled")
    rows = client.get("/api/jobs").json()
    assert rows[0]["ucloud_submission_uncertain"]
    path = f"/api/jobs/{job['id']}/reconcile"
    for body in ({}, {"remote_id": "x", "not_created": True}):
        assert client.post(path, json=body).status_code == 400
    assert client.post(path, json={"not_created": True}).status_code == 200
    run = store.job(job["id"])["params"]["ucloud_run"]
    assert run["submission_intent"] and run["reconciled_not_created"] and run["reconciled_at"]
    assert not client.get("/api/jobs").json()[0]["ucloud_submission_uncertain"]
    assert client.delete(f"/api/projects/{project['id']}").status_code == 200


@pytest.mark.parametrize(
    "run",
    [
        {"job_id": "owned", "remote_state": "FAILURE"},
        {"job_id": "owned", "termination_requested": True},
        {"job_id": "owned", "cancellation_requested": True},
    ],
)
def test_terminal_or_acknowledged_remote_work_allows_delete(client, run):
    store = client.app.state.store
    project = store.create_project("Settled remote work")
    recording = store.create_recording(project["id"], "synthetic.wav")
    job = store.add_job(recording["id"], "transcribe", {"ucloud_run": run})
    store.update_job(job["id"], state="failed")
    assert client.delete(f"/api/projects/{project['id']}").status_code == 200


def test_old_unresolved_cloud_jobs_remain_visible_in_activity(client):
    store = client.app.state.store
    project = store.create_project("Long activity history")
    recording = store.create_recording(project["id"], "synthetic.wav")
    old = store.add_job(recording["id"], "enhance", {"ucloud_run": {"job_id": "owned"}})
    store.update_job(old["id"], state="failed")
    for _ in range(201):
        job = store.add_job(recording["id"], "media")
        store.update_job(job["id"], state="completed")
    assert old["id"] in {job["id"] for job in client.get("/api/jobs").json()}
