"""Selective passage restoration remains reportable and survives isolated library restore."""

import io
import json
from pathlib import Path
from zipfile import ZipFile

from test_portable_restore import destination
from test_restore_deleted_passage import options, remove, restore

from cbs_transcriber import portable_capture, portable_restore, research_reports
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def test_deleted_passage_restore_round_trips_history_and_report(client, tmp_path, wav_bytes):
    source = client.app.state.store
    project = source.create_project("Synthetic restoration evidence")
    uploaded = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("tiny.wav", wav_bytes, "audio/wav")},
    )
    assert uploaded.status_code == 201, uploaded.text
    rid = uploaded.json()["recording"]["id"]
    source.execute("UPDATE jobs SET state='done'")
    source.execute("UPDATE recordings SET status='ready'")
    segments = normalize_segments(
        [
            {"text": "Before", "speaker": "A", "start": 0, "end": 0.2},
            {"text": "Restored exact æøå", "speaker": "A", "start": 0.2, "end": 0.4},
            {"text": "After", "speaker": "A", "start": 0.4, "end": 0.6},
        ]
    )
    result = source.add_result(
        rid, segments, {"A": "Synthetic speaker"}, "Synthetic", {}, auto_first=True
    )
    deleted = remove(client, source.recording(rid), "1")
    [descriptor] = options(client, deleted)["deleted_segments"]
    response = restore(client, deleted, descriptor)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["segments"] == segments
    versions = source.result_versions(rid, result["id"])
    latest = versions[0]
    assert latest["action"] == "passage_delete_undo"
    assert latest["context"]["deleted_version_id"] == descriptor["delete_version_id"]

    with ZipFile(io.BytesIO(research_reports.project_report(source, project["id"]))) as report:
        manifest = json.loads(report.read("manifest.json"))
    assert manifest["manual_change_counts"]["passage_delete"] == 1
    assert manifest["manual_change_counts"]["passage_delete_undo"] == 1
    selected = [item for item in manifest["versions"] if item["selected"]]
    assert len(selected) == 1 and selected[0]["action"] == "passage_delete_undo"
    assert selected[0]["payload_sha256"] == latest["payload_sha256"]
    assert selected[0]["actor_identity_verified"] is False
    # Reports describe the observed action and exact payload, not raw internal references
    # or a separately certified proof that the historical inverse was correct.
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert descriptor["delete_version_id"] not in serialized
    assert "deleted_version_id" not in serialized
    assert "not anonymity or tamper-proof evidence" in " ".join(manifest["limitations"])

    with source.library_lock():
        package = portable_capture.capture_library(
            source,
            client.app.state.queue,
            tmp_path / "packages",
            gate=portable_capture.CaptureGate(),
        )
    target = destination(tmp_path, "restored-deletion-history")
    receipt = portable_restore.restore_library(
        package["folder"],
        expected_commit_sha256=package["commit_sha256"],
        target=target,
        live_root=source.root,
    )
    observation = receipt["research_observation"]
    assert observation["semantic_counts"]["result_versions"] == len(versions)
    assert observation["restore_ready"] is False
    assert "domain_specific_edit_and_inference_transformations" in observation["not_checked"]
    reopened = Store(target)
    assert reopened.recording(rid)["segments"] == segments
    assert reopened.result_versions(rid, result["id"]) == versions
    assert (reopened.directory(rid) / "original.wav").read_bytes() == wav_bytes
    assert Path(receipt["target"]).resolve() == target.resolve()
