"""Synthetic public bounded timing, immutable unit receipts and atomic Apply integration."""

import copy
import io
import json
import wave
from pathlib import Path
from zipfile import ZipFile

import pytest

from cbs_transcriber import enhancement, research_reports, timing_capture
from cbs_transcriber.enhancement_worker import write_json
from cbs_transcriber.transcripts import normalize_segments


def prepared(client, monkeypatch):
    store = client.app.state.store
    project = store.create_project("Timing capture")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    source = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Hej", "start": 0, "end": 1, "speaker": "VOICE"},
                {"text": "Untouched", "start": 1, "end": 2},
                {"text": "Verden", "start": 2, "end": 3, "speaker": "VOICE"},
            ]
        ),
        {"VOICE": "Reviewed voice"},
        "Reviewed",
        {},
    )
    monkeypatch.setattr(
        "cbs_transcriber.routing.repair_route",
        lambda store, credentials, recording, source, units, options, *, cloud=True: (
            options.model_copy(update={"device": "cpu"}),
            {},
        ),
    )
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *a, **kw: {"ready": True})

    def ffmpeg(arguments, cancel):
        path = Path(arguments[-1])
        duration = float(arguments[arguments.index("-t") + 1])
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * int(16000 * duration))

    def worker(folder, options, *args, **kwargs):
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        assert request["producer_artifact_policy"] == timing_capture.POLICY
        assert (folder / "producer-origin.json").exists()
        passage = request["passages"][0]
        write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": passage["id"],
                        "words": [{"word": passage["text"], "start": 0, "end": 1, "score": 0.9}],
                    }
                ]
            },
        )

    monkeypatch.setattr("cbs_transcriber.media.ffmpeg", ffmpeg)
    monkeypatch.setattr(enhancement, "run_worker", worker)
    response = client.post(
        f"/api/recordings/{recording['id']}/passage-timing",
        json={"result_id": source["id"], "revision": 1, "segment_ids": ["0", "2"]},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["params"]["timing_artifact_policy"] == timing_capture.POLICY
    store.update_job(job["id"], state="running")
    return store, recording, source, store.job(job["id"])


def test_public_units_capture_distinct_origins_and_seal_normalized_output(client, monkeypatch):
    store, recording, source, job = prepared(client, monkeypatch)
    client.app.state.queue.process(job["id"])
    job = store.job(job["id"])
    value = job["params"]["timing_artifact_capture"]
    assert len(value["units"]) == 2
    first, last = value["units"].values()
    assert first["capture"]["origin"]["generation"] != last["capture"]["origin"]["generation"]
    assert (
        first["capture"]["origin"]["audio_binding"]["request_sha256"]
        != last["capture"]["origin"]["audio_binding"]["request_sha256"]
    )
    assert set(job["params"]["realign_output"]["passages"]) == {"0", "2"}
    pending_inventory = timing_capture.inventory(value, job)
    assert {item["filename"] for item in pending_inventory} == {
        f"units/{index:05d}/{leaf}"
        for index in range(2)
        for leaf in ("request.json", "audio.wav", "alignment.json")
    }
    assert job["params"]["realign_output"]["passages"]["2"]["words"][0]["start"] == 2
    proposed = copy.deepcopy(job["params"])
    proposed["realign_output"]["passages"]["2"]["words"][0]["start"] = 2.25
    with pytest.raises(ValueError):
        store.update_job(job["id"], params=proposed)
    store.update_job(job["id"], state="completed")
    before = store.recording(recording["id"])
    saved = store.apply_local_timing(recording["id"], source["id"], job["id"], before["revision"])
    assert saved["segments"][1] == before["segments"][1]
    assert saved["speakers"] == before["speakers"]
    version = store.result_versions(recording["id"], source["id"])[0]
    assert version["action"] == "timing_realign"
    assert version["context"]["timing_artifacts"]["creation"]["version_id"] == version["id"]
    assert version["context"]["timing_artifacts"]["applied_ids"] == ["0", "2"]
    proof = version["context"]["timing_artifacts"]
    assert (
        timing_capture.inventory(
            proof,
            store.job(job["id"]),
            creation={
                "result_id": source["id"],
                "version_id": version["id"],
                "payload_sha256": version["payload_sha256"],
            },
        )
        == pending_inventory
    )
    assert (
        store.apply_local_timing(recording["id"], source["id"], job["id"], before["revision"])[
            "revision"
        ]
        == saved["revision"]
    )
    monkeypatch.setattr(
        "cbs_transcriber.provenance_artifacts.capture_file",
        lambda *a, **kw: pytest.fail("historical report reread current timing artifacts"),
    )
    selected = [
        {"recording_id": recording["id"], "result_id": source["id"], "version_id": version["id"]}
    ]
    captured = research_reports._capture(store, recording["project_id"], selected)[1]
    assert captured[version["id"]]["context"]["timing_artifacts"] == proof
    current = store.recording(recording["id"])
    store.save_transcript(
        recording["id"], current["segments"], current["speakers"], current["revision"]
    )
    head = store.result_versions(recording["id"], source["id"])[0]
    selected[0]["version_id"] = head["id"]
    inherited = research_reports._capture(store, recording["project_id"], selected)[1]
    assert inherited[head["id"]]["context"]["timing_artifacts"] == proof
    with ZipFile(
        io.BytesIO(research_reports.project_report(store, recording["project_id"], selected))
    ) as archive:
        methods = archive.read("methods.md").decode("utf-8")
        assert "local timing source=" in methods and "captured alignment units=2" in methods
        assert (
            "captured request SHA-256=" in methods
            and "retained-file availability=not_checked" in methods
        )


def test_unreceipted_cached_unit_remains_gap_without_runtime(client, monkeypatch):
    store, recording, source, job = prepared(client, monkeypatch)
    folder = store.directory(recording["id"]) / "enhancements" / job["id"] / "units" / "00000"
    folder.mkdir(parents=True)
    write_json(
        folder / "alignment.json",
        {"passages": [{"id": "0", "words": [{"word": "Hej", "start": 0, "end": 1, "score": 0.9}]}]},
    )
    client.app.state.queue.process(job["id"])
    value = store.job(job["id"])["params"]["timing_artifact_capture"]
    assert value["units"]["00000"] == "not_captured"
    assert "alignment" in value["units"]["00001"]["capture"]["checkpoints"]
    assert store.job(job["id"])["params"]["realign_output"]["passages"]["0"]["words"]


def test_fresh_single_segment_uses_one_root_unit_and_keeps_other_passages(client, monkeypatch):
    store, recording, source, original_job = prepared(client, monkeypatch)
    options = original_job["params"]["enhancement"]
    job = store.enqueue_enhancement(
        recording["id"],
        source["id"],
        store.recording(recording["id"])["revision"],
        {"enhancement": options, "segment_id": "0", "realign": True},
    )
    assert job["params"]["timing_artifact_policy"] == timing_capture.POLICY
    store.update_job(job["id"], state="running")
    client.app.state.queue.process(job["id"])
    finished = store.job(job["id"])
    assert set(finished["params"]["timing_artifact_capture"]["units"]) == {"00000"}
    assert "words" in finished["params"]["realign_output"]
    assert {
        item["filename"]
        for item in timing_capture.inventory(
            finished["params"]["timing_artifact_capture"], finished
        )
    } == {"request.json", "audio.wav", "alignment.json"}
    store.update_job(job["id"], state="completed")
    before = store.recording(recording["id"])
    saved = store.apply_local_timing(recording["id"], source["id"], job["id"], before["revision"])
    assert saved["segments"][1:] == before["segments"][1:]
