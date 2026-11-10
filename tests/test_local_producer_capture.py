"""Actual public admission/worker/Store seams with synthetic media and no inference."""

import copy
import json
import sys
from types import SimpleNamespace

import pytest
from admission_support import inspect_and_materialize

from cbs_transcriber import enhancement, local_asr_capture, research_reports
from cbs_transcriber.enhancement_worker import processing_environment, write_json
from cbs_transcriber.transcripts import normalize_segments


def prepare(client, monkeypatch, wav_bytes):
    store = client.app.state.store
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    monkeypatch.setattr(enhancement, "detected_device", lambda *a: "cpu")
    project = store.create_project("Synthetic producer")
    recording = store.create_recording(project["id"], "synthetic.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej verden", "start": 0, "end": 3}]),
        {},
        "Original",
        {},
    )
    response = client.post(
        f"/api/recordings/{recording['id']}/enhancements",
        json={
            "result_id": result["id"],
            "revision": 1,
            "device": "cpu",
            "runtime_python": sys.executable,
        },
    )
    assert response.status_code == 202, response.text
    job = inspect_and_materialize(store, client.app.state.credentials, [response.json()["id"]])[0]
    assert job["params"]["producer_artifact_policy"] == local_asr_capture.ENHANCEMENT_POLICY
    store.update_job(job["id"], state="running")
    return store, project, recording, result, store.job(job["id"])


def output(folder):
    write_json(
        folder / "alignment.json",
        {
            "passages": [
                {
                    "id": "0",
                    "words": [
                        {"word": "Hej", "start": 0, "end": 1, "score": 0.9},
                        {"word": "verden", "start": 1, "end": 3, "score": 0.9},
                    ],
                }
            ]
        },
    )


def test_public_local_capture_bound_to_exact_creation_and_inherited(client, monkeypatch, wav_bytes):
    store = client.app.state.store
    with store.library_lock():
        store, project, recording, source, job = prepare(client, monkeypatch, wav_bytes)

        def worker(folder, options, *args, **kwargs):
            persisted = store.job(job["id"])["params"]["producer_artifact_capture"]
            assert persisted["origin"]["job_id"] == job["id"]
            assert not persisted["checkpoints"]
            assert (folder / "producer-origin.json").exists()
            processing_environment(folder, "alignment", options.model_dump(), SimpleNamespace())
            output(folder)

        monkeypatch.setattr(enhancement, "run_worker", worker)
        client.app.state.queue.process(job["id"])
        created = store.all("SELECT * FROM transcript_results WHERE job_id=?", (job["id"],))[0]
        version = store.all("SELECT * FROM result_versions WHERE result_id=?", (created["id"],))[0]
        context = json.loads(version["context"])
        receipt = context["producer_artifacts"]
        creation = {
            "result_id": created["id"],
            "version_id": version["id"],
            "payload_sha256": version["payload_sha256"],
        }
        projected = local_asr_capture.public_projection(
            receipt, recording_id=recording["id"], job_id=job["id"], creation=creation
        )
        assert projected["stages"]["alignment"]["artifacts"]["environment"] != "not_captured"
        assert projected["stages"]["transcription"] == "not_captured"
        assert projected["retained_files"] == "not_checked"
        assert (
            local_asr_capture.inventory(
                receipt, recording_id=recording["id"], job_id=job["id"], creation=creation
            )[1]["audio_frames"]
            == 48000
        )
        current = store.recording(recording["id"])
        store.activate_result(recording["id"], created["id"], current["revision"])
        current = store.recording(recording["id"])
        store.save_transcript(
            recording["id"], current["segments"], current["speakers"], current["revision"]
        )
        head = store.result_versions(recording["id"], created["id"])[0]
        assert (
            store.result_version(recording["id"], created["id"], head["id"])["context"][
                "producer_artifacts"
            ]
            == receipt
        )
        chosen = [
            {"recording_id": recording["id"], "result_id": created["id"], "version_id": head["id"]}
        ]
        monkeypatch.setattr(
            "cbs_transcriber.provenance_artifacts.capture_file",
            lambda *a, **kw: pytest.fail("historical report reread an artifact"),
        )
        report = research_reports._capture(store, project["id"], chosen)
        assert report[1][head["id"]]["producer_artifact_evidence"] == projected
        with pytest.raises(ValueError):
            local_asr_capture.public_projection(
                receipt,
                recording_id=recording["id"],
                job_id=job["id"],
                creation={**creation, "payload_sha256": "0" * 64},
            )
        params = copy.deepcopy(store.job(job["id"])["params"])
        params["enhancement"]["language"] = "en"
        with pytest.raises(ValueError):
            store.update_job(job["id"], params=params)
        other = store.add_job(recording["id"], "enhance", {})
        store.execute(
            "UPDATE transcript_results SET job_id=? WHERE id=?", (other["id"], created["id"])
        )
        with pytest.raises(ValueError, match="job ownership"):
            research_reports._capture(store, project["id"], chosen)


def test_capture_rejects_generic_injection_and_replacement(client, monkeypatch, wav_bytes):
    store = client.app.state.store
    with store.library_lock():
        store, _, recording, _, job = prepare(client, monkeypatch, wav_bytes)
        proposed = copy.deepcopy(job["params"])
        proposed["producer_artifact_capture"] = {}
        with pytest.raises(ValueError):
            store.update_job(job["id"], params=proposed)
        with pytest.raises(ValueError):
            store.add_result(recording["id"], [], {}, "Forged", {"producer_artifacts": {}})
        with pytest.raises(ValueError):
            store.add_result(
                recording["id"], [], {}, "Forged", {}, job_id=job["id"], producer_artifacts={}
            )


def test_existing_stage_is_recovered_without_invented_capture(client, monkeypatch, wav_bytes):
    store = client.app.state.store
    with store.library_lock():
        store, _, recording, _, job = prepare(client, monkeypatch, wav_bytes)
        folder = store.directory(recording["id"]) / "enhancements" / job["id"]
        folder.mkdir(parents=True)
        output(folder)
        monkeypatch.setattr(
            enhancement, "run_worker", lambda *a, **kw: pytest.fail("reran inference")
        )
        monkeypatch.setattr(
            enhancement, "require_local_runtime", lambda *a, **kw: pytest.fail("checked runtime")
        )
        client.app.state.queue.process(job["id"])
        row = store.all(
            "SELECT v.context FROM result_versions v JOIN transcript_results t "
            "ON t.id=v.result_id WHERE t.job_id=?",
            (job["id"],),
        )[0]
        assert "producer_artifacts" not in json.loads(row["context"])
        assert "producer_artifact_capture" not in store.job(job["id"])["params"]


def test_completed_stage_survives_later_failure_and_finalizes_without_runtime(
    client, monkeypatch, wav_bytes
):
    store = client.app.state.store
    with store.library_lock():
        store, _, recording, _, job = prepare(client, monkeypatch, wav_bytes)

        def worker(folder, *args, **kwargs):
            output(folder)
            raise ValueError("Synthetic failure after completed alignment")

        monkeypatch.setattr(enhancement, "run_worker", worker)
        with pytest.raises(ValueError, match="Synthetic failure"):
            client.app.state.queue.process(job["id"])
        receipt = copy.deepcopy(store.job(job["id"])["params"]["producer_artifact_capture"])
        assert set(receipt["checkpoints"]) == {"alignment"}
        assert not store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
        monkeypatch.setattr(
            enhancement, "run_worker", lambda *a, **kw: pytest.fail("repeated inference")
        )
        monkeypatch.setattr(
            enhancement, "require_local_runtime", lambda *a, **kw: pytest.fail("checked runtime")
        )
        client.app.state.queue.process(job["id"])
        assert store.job(job["id"])["params"]["producer_artifact_capture"] == receipt
        folder = store.directory(recording["id"]) / "enhancements" / job["id"]
        (folder / "alignment.json").write_bytes(b"changed")
        with pytest.raises(ValueError):
            local_asr_capture.verify_files(receipt, folder)


def test_failed_durable_seal_preserves_files_without_claiming_capture(
    client, monkeypatch, wav_bytes
):
    store = client.app.state.store
    with store.library_lock():
        store, _, recording, _, job = prepare(client, monkeypatch, wav_bytes)

        def fail(*args):
            raise OSError("Synthetic fsync failure")

        monkeypatch.setattr(local_asr_capture, "seal", fail)
        monkeypatch.setattr(
            enhancement, "run_worker", lambda *a, **kw: pytest.fail("started inference")
        )
        with pytest.raises(OSError, match="fsync failure"):
            client.app.state.queue.process(job["id"])
        folder = store.directory(recording["id"]) / "enhancements" / job["id"]
        assert (folder / "request.json").exists() and (folder / "audio.wav").exists()
        assert "producer_artifact_capture" not in store.job(job["id"])["params"]


def test_changed_output_after_checkpoint_cannot_commit_under_old_receipt(
    client, monkeypatch, wav_bytes
):
    store = client.app.state.store
    with store.library_lock():
        store, _, _, _, job = prepare(client, monkeypatch, wav_bytes)
        monkeypatch.setattr(enhancement, "run_worker", lambda folder, *a, **kw: output(folder))
        complete = local_asr_capture.complete_stage

        def replace_after_publication(store, job, folder, stage):
            value = complete(store, job, folder, stage)
            changed = json.loads((folder / "alignment.json").read_text(encoding="utf-8"))
            changed["passages"][0]["words"][0]["end"] = 0.5
            write_json(folder / "alignment.json", changed)
            return value

        monkeypatch.setattr(local_asr_capture, "complete_stage", replace_after_publication)
        with pytest.raises(ValueError, match="evidence changed"):
            client.app.state.queue.process(job["id"])
        assert not store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
        assert (
            "alignment"
            in store.job(job["id"])["params"]["producer_artifact_capture"]["checkpoints"]
        )
