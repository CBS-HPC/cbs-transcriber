"""Whole-file HTTP acceptance never inspects source bytes or runtime/network readiness."""

import sqlite3
import threading

import pytest
from admission_support import inspect_and_materialize
from test_passage_timing import request as timing_request
from test_passage_timing import untimed as untimed
from test_realign import edit, realign_jobs
from test_realign import edited as edited

from cbs_transcriber import (
    enhancement,
    enhancement_api,
    original_inspection,
    providers,
)
from cbs_transcriber.transcripts import normalize_segments


def uploaded(client, wav_bytes):
    project = client.post("/api/projects", json={"name": "Synthetic durable acceptance"}).json()
    response = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
    )
    assert response.status_code == 201, response.text
    return response.json()["recording"]


def no_preflight(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("HTTP acceptance performed source/runtime/network preparation")

    monkeypatch.setattr("cbs_transcriber.audio_inputs.freeze", refuse)
    monkeypatch.setattr(enhancement, "require_local_runtime", refuse)
    monkeypatch.setattr(enhancement_api, "runtime_stamped", refuse)
    monkeypatch.setattr(enhancement_api, "freeze_profile", refuse)


def test_v142_whole_asr_accepts_preparing_playback_without_inspecting_source(
    client, wav_bytes, monkeypatch
):
    recording = uploaded(client, wav_bytes)
    client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
        },
    )
    no_preflight(monkeypatch)
    store = client.app.state.store
    with store.library_lock():
        response = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
    assert response.status_code == 202, response.text
    job = response.json()[0]
    assert job["params"]["input_intent"]["phase"] == "waiting"
    assert "audio_input" not in job["params"]
    assert store.recording(recording["id"])["status"] == "preparing"
    assert store.claim_job("transcribe") is None


def test_v142_whole_enhancement_accepts_exact_reviewed_source_without_runtime_probe(
    client, wav_bytes, monkeypatch
):
    recording = uploaded(client, wav_bytes)
    store = client.app.state.store
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic reviewed wording."}]),
        {},
        "Reviewed",
        {},
    )
    recording = store.recording(recording["id"])
    no_preflight(monkeypatch)
    with store.library_lock():
        response = client.post(
            f"/api/recordings/{recording['id']}/enhancements",
            json={"result_id": result["id"], "revision": recording["revision"]},
        )
    assert response.status_code == 202, response.text
    intent = response.json()["params"]["input_intent"]
    assert intent["source_snapshot"]["id"] == result["id"]
    assert intent["source_snapshot"]["source_version_id"]
    assert intent["accepted"]["options"]["timing_policy"] == "retain-estimates"
    assert store.claim_job("enhance") is None


def test_v142_http_admission_preserves_conflict_and_policy_statuses(client, wav_bytes, monkeypatch):
    recording = uploaded(client, wav_bytes)
    store = client.app.state.store
    client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
        },
    )
    no_preflight(monkeypatch)
    body = {"recording_ids": [recording["id"]]}
    with store.library_lock():
        accepted = client.post("/api/transcriptions", json=body)
        assert accepted.status_code == 202, accepted.text
        before = store.all("SELECT * FROM jobs ORDER BY id")
        duplicate = client.post("/api/transcriptions", json=body)
        assert duplicate.status_code == 409, duplicate.text
        assert store.all("SELECT * FROM jobs ORDER BY id") == before
        job = accepted.json()[0]
        intent = job["params"]["input_intent"]
        cancelled = client.post(
            f"/api/input-intents/{job['id']}/cancel",
            json={"epoch": intent["epoch"], "phase": intent["phase"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        result = store.add_result(
            recording["id"],
            normalize_segments([{"text": "Synthetic saved wording."}]),
            {},
            "Reviewed",
            {},
        )
        before = store.all("SELECT * FROM jobs ORDER BY id")
        replacement = client.post("/api/transcriptions", json=body)
        assert replacement.status_code == 409, replacement.text
        stale = client.post(
            f"/api/recordings/{recording['id']}/enhancements",
            json={"result_id": result["id"], "revision": recording["revision"]},
        )
        assert stale.status_code == 409, stale.text
        store.execute(
            "UPDATE projects SET classification='protected' WHERE id=?", (recording["project_id"],)
        )
        denied = client.post("/api/transcriptions", json=body | {"replace": True})
        assert denied.status_code == 403, denied.text
        assert store.all("SELECT * FROM jobs ORDER BY id") == before


def test_v142_malformed_inspector_proof_is_visible_without_breaking_activity(client, wav_bytes):
    recording = uploaded(client, wav_bytes)
    store = client.app.state.store
    with store.library_lock():
        from cbs_transcriber import original_inspection

        cache = original_inspection.ensure(store, recording["id"])
        store.execute("UPDATE jobs SET params='{' WHERE id=?", (cache["inspection_job_id"],))
    response = client.get("/api/jobs")
    assert response.status_code == 200, response.text
    inspector = next(job for job in response.json() if job["id"] == cache["inspection_job_id"])
    assert inspector["input_preparation"]["error_code"] == "invalid_evidence"
    assert inspector["input_preparation"]["can_cancel"] is False


def test_v142_original_asr_finishes_before_playback_and_inserts_exact_followup_once(
    client, wav_bytes, monkeypatch
):
    recording = uploaded(client, wav_bytes)
    store, queue, credentials = (
        client.app.state.store,
        client.app.state.queue,
        client.app.state.credentials,
    )
    client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
        },
    )
    calls = []
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda options: None)

    def transcribe(path, settings, key):
        assert path.is_file()
        calls.append(path)
        return {"segments": [{"start": 0, "end": 3, "text": "Synthetic accepted wording."}]}

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    with store.library_lock():
        response = client.post(
            "/api/transcriptions",
            json={
                "recording_ids": [recording["id"]],
                "requested_features": {"word_timing": True},
                "enhancement": {"execution": "local"},
            },
        )
        assert response.status_code == 202, response.text
        parent = inspect_and_materialize(store, credentials, [response.json()[0]["id"]])[0]
        assert (
            parent["params"]["followup"]["params"]["enhancement"]["timing_policy"]
            == "retain-estimates"
        )
        assert store.claim_job("transcribe")["id"] == parent["id"]
        queue.process(parent["id"])
        queue.process(parent["id"])
    assert len(calls) == 1
    assert store.recording(recording["id"])["status"] == "preparing"
    children = store.all("SELECT id FROM jobs WHERE kind='enhance'")
    assert len(children) == 1
    child = store.job(children[0]["id"])
    assert child["params"]["parent_transcription_id"] == parent["id"]
    assert child["params"]["audio_input"] == parent["params"]["audio_input"]
    assert child["params"]["source_snapshot"]["source_version_id"]
    assert len(store.results(recording["id"])) == 1


def test_v142_upload_metadata_and_media_job_publish_atomically_on_insert_failure(client, wav_bytes):
    store = client.app.state.store
    project = store.create_project("Failed atomic synthetic upload")
    store.execute(
        "CREATE TRIGGER refuse_media BEFORE INSERT ON jobs WHEN NEW.kind='media' "
        "BEGIN SELECT RAISE(ABORT,'Synthetic media insert'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="Synthetic"):
        client.post(
            "/api/recordings",
            data={"project_id": project["id"]},
            files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
        )
    assert not store.all("SELECT id FROM recordings WHERE project_id=?", (project["id"],))
    assert not store.all("SELECT id FROM jobs")
    assert not list((store.project_folder(project["id"]) / "recordings").glob("*"))


def test_v142_upload_postcommit_notification_error_preserves_original_and_rows(
    client, wav_bytes, monkeypatch
):
    store, queue = client.app.state.store, client.app.state.queue
    project = store.create_project("Committed synthetic upload")
    monkeypatch.setattr(
        queue,
        "notify",
        lambda: (_ for _ in ()).throw(RuntimeError("Synthetic notification failure")),
    )
    with pytest.raises(RuntimeError, match="Synthetic"):
        client.post(
            "/api/recordings",
            data={"project_id": project["id"]},
            files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
        )
    rows = store.all("SELECT id FROM recordings WHERE project_id=?", (project["id"],))
    assert len(rows) == 1
    assert (store.directory(rows[0]["id"]) / "original.wav").read_bytes() == wav_bytes
    assert len(store.all("SELECT id FROM jobs WHERE recording_id=?", (rows[0]["id"],))) == 1


def test_v142_waiting_actions_are_guarded_and_retained_after_clear(client, wav_bytes):
    recording = uploaded(client, wav_bytes)
    store = client.app.state.store
    client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
        },
    )
    with store.library_lock():
        job = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]}).json()[
            0
        ]
        intent = job["params"]["input_intent"]
        assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
        token = {"epoch": intent["epoch"], "phase": intent["phase"]}
        assert client.post(f"/api/input-intents/{job['id']}/cancel", json=token).status_code == 200
        assert client.post(f"/api/input-intents/{job['id']}/cancel", json=token).status_code == 409
        cache = original_inspection.read(store, recording["id"])
        assert cache["state"] == "pending"
        assert client.post("/api/activity/clear").status_code == 200
        shown = client.get("/api/jobs").json()
        inspector = next(row for row in shown if row["id"] == cache["inspection_job_id"])
        assert inspector["input_preparation"]["kind"] == "inspection"
    assert store.job(job["id"])["state"] == "cancelled"


def test_v142_upload_has_no_claimable_row_before_closed_file_metadata_commit(
    client, wav_bytes, monkeypatch
):
    store = client.app.state.store
    project = store.create_project("Atomic publication barrier")
    entered, release = threading.Event(), threading.Event()
    identifiers, responses = [], []
    insert = store._insert_job

    def held(db, recording_id, kind, params, identifier):
        if kind == "media":
            identifiers.append(recording_id)
            entered.set()
            assert release.wait(10)
        return insert(db, recording_id, kind, params, identifier)

    monkeypatch.setattr(store, "_insert_job", held)
    client.put(
        "/api/settings",
        json={
            "provider_type": "custom",
            "base_url": "http://127.0.0.1:9000/v1",
            "model": "synthetic",
            "capability": "segments",
        },
    )
    with store.library_lock():
        thread = threading.Thread(
            target=lambda: responses.append(
                client.post(
                    "/api/recordings",
                    data={"project_id": project["id"]},
                    files={"file": ("synthetic.wav", wav_bytes, "audio/wav")},
                )
            )
        )
        thread.start()
        try:
            assert entered.wait(5)
            assert (
                store.directory(identifiers[0], project["id"]) / "original.wav"
            ).read_bytes() == wav_bytes
            assert not store.all("SELECT id FROM recordings WHERE id=?", (identifiers[0],))
            assert (
                client.post("/api/transcriptions", json={"recording_ids": identifiers}).status_code
                == 404
            )
        finally:
            release.set()
            thread.join(10)
        assert not thread.is_alive()
        assert responses[0].status_code == 201, responses[0].text
        accepted = client.post("/api/transcriptions", json={"recording_ids": identifiers})
        assert accepted.status_code == 202, accepted.text


def test_v142_fresh_manual_timing_keeps_estimates_without_whole_source_inspection(untimed):
    client, store, rid, result = untimed
    response = timing_request(client, rid, result, ["1"])
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    assert job["params"]["enhancement"]["timing_policy"] == "retain-estimates"
    assert "audio_input" not in job["params"]
    assert not store.all("SELECT id FROM jobs WHERE kind='source-inspect'")


def test_v142_fresh_autoedit_timing_keeps_estimates_without_whole_source_inspection(edited):
    client, store, rid, _ = edited
    client.put("/api/editing-settings", json={"realign_edits": True})
    edit(client, store, rid, "1", "Synthetic edited words.")
    job = store.job(realign_jobs(store, "queued")[0]["id"])
    assert job["params"]["enhancement"]["timing_policy"] == "retain-estimates"
    assert "audio_input" not in job["params"]
    assert not store.all("SELECT id FROM jobs WHERE kind='source-inspect'")
