"""Fresh native UCloud Transcriber evidence (ADR 0100); mocked UCloud and synthetic audio only."""

import copy
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from report_support import selected_view_report
from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue
from test_native_batch import selection as selection
from test_ucloud_native import SECRET
from test_ucloud_native import legacy_native_job as legacy_native_job
from test_ucloud_native import native_job as native_job
from test_ucloud_native import native_lease as native_lease
from test_ucloud_native import remote as remote

from cbs_transcriber import native_capture, portable_capture

PASSWORD = "SYNTHETIC_ARCHIVE_PASSWORD_CANARY"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def creation(store, recording_id):
    [result] = store.results(recording_id)
    row = store.one(
        "SELECT id,payload_sha256,context FROM result_versions WHERE result_id=? AND sequence=1",
        (result["id"],),
    )
    return result, row, json.loads(row["context"])


def downloads(remote):
    return [call for call in remote.calls if call.url.path == "/api/files/download"]


def test_fresh_single_job_binds_submission_and_exact_output(client, remote, native_job):
    _, recording, job = native_job
    assert job["params"]["native_artifact_policy"] == native_capture.POLICY
    # A free-text application default is submitted but never recorded.
    declared = remote.template["status"]["resolvedApplication"]["invocation"]["parameters"]
    declared.append({"name": "archivePassword", "type": "text", "defaultValue": PASSWORD})
    store = client.app.state.store
    client.app.state.queue.process(job["id"])
    assert remote.submissions[0]["parameters"]["archivePassword"]["value"] == PASSWORD
    saved = store.job(job["id"])
    capture = saved["params"]["native_artifact_capture"]
    [submission] = capture["submissions"]
    directory = store.directory(recording["id"])
    request_raw = (directory / f"ucloud-{job['id']}.request-0000.json").read_bytes()
    output_raw = (directory / f"ucloud-{job['id']}.json").read_bytes()
    descriptor = json.loads(request_raw)
    assert descriptor == submission["descriptor"]
    assert descriptor["scope"] == "single" and descriptor["batch_id"] is None
    assert descriptor["input"]["remote_name"] == "recording.wav"
    assert descriptor["input"]["sha256"] == job["params"]["audio_input"]["sha256"]
    recorded = descriptor["submission"]
    assert recorded["withheld_parameters"] == ["archivePassword"]
    assert recorded["specification"]["product"] == remote.submissions[0]["product"]
    assert recorded["specification"]["parameters"]["model"]["value"] == "large-v3"
    assert descriptor["output_selector"] == {"model": "large-v3", "language": "da"}
    assert submission["request"]["artifacts"]["request"]["sha256"] == sha(request_raw)
    # Policy jobs retain the exact downloaded bytes, observed as the raw output.
    output = capture["output"]
    assert output["match"]["remote_name"] == "recording.json"
    raw = output["response"]["artifacts"]["raw_response"]
    assert (raw["sha256"], raw["bytes"]) == (sha(output_raw), len(output_raw))
    assert saved["params"]["ucloud_run"]["artifact_submission_sha256"] == (
        native_capture.submission_sha256(capture)
    )
    result, version, context = creation(store, recording["id"])
    bound = context["native_artifacts"]
    reference = {
        "result_id": result["id"],
        "version_id": version["id"],
        "payload_sha256": version["payload_sha256"],
    }
    assert bound["creation"] == reference
    assert bound["assembly_sha256"] == version["payload_sha256"]
    assert (
        native_capture.unbound(
            bound, recording_id=recording["id"], job_id=job["id"], creation=reference
        )
        == capture
    )
    for blob in (request_raw, json.dumps(saved["params"]).encode(), version["context"].encode()):
        for secret in (SECRET, PASSWORD, "transfer-token", "Bearer"):
            assert secret.encode() not in blob
    # A repeat reuses the allocation and output, re-observing and rebinding nothing.
    client.app.state.queue.process(job["id"])
    assert len(remote.submissions) == 1 and len(downloads(remote)) == 1
    assert store.job(job["id"])["params"]["native_artifact_capture"] == capture


def test_store_and_generic_writes_cannot_forge_or_drop_native_evidence(
    client, remote, native_job, monkeypatch
):
    _, recording, job = native_job
    store = client.app.state.store
    original = store.add_result
    monkeypatch.setattr(store, "add_result", lambda *a, **k: None)  # Stop before creation.
    client.app.state.queue.process(job["id"])
    monkeypatch.setattr(store, "add_result", original)
    parent = store.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    stored = json.loads(parent["params"])["native_artifact_capture"]
    with store.connection() as db:
        with pytest.raises(ValueError, match="native artifact"):
            native_capture.for_creation(db, parent, recording["id"], None)
        altered = copy.deepcopy(stored)
        altered["assembly_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="native artifact"):
            native_capture.for_creation(db, parent, recording["id"], altered)
    with pytest.raises(ValueError, match="cannot assert native"):
        store.add_result(
            recording["id"], [], {}, "Forged", {"kind": "provider", "native_artifacts": {}}
        )
    params = store.job(job["id"])["params"]
    for proposed in (
        {key: value for key, value in params.items() if key != "native_artifact_capture"},
        {**params, "native_batch_id": "a" * 32},
    ):
        with pytest.raises(ValueError, match="native artifact"):
            store.update_job(job["id"], params=proposed)
    # A journal that never recorded this submission cannot bind it either.
    assert params["ucloud_run"]["artifact_submission_sha256"] != "f" * 64
    store.execute(
        "UPDATE jobs SET params=json_set(params,'$.ucloud_run.artifact_submission_sha256',?) "
        "WHERE id=?",
        ("f" * 64, job["id"]),
    )
    with pytest.raises(ValueError, match="native artifact"):
        client.app.state.queue.process(job["id"])
    assert not store.results(recording["id"])


def test_reconciled_resubmission_records_a_new_descriptor(client, remote, native_job):
    _, recording, job = native_job
    store = client.app.state.store
    remote.unknown_submission = True
    with pytest.raises(ValueError, match="connection failed"):
        client.app.state.queue.process(job["id"])
    store.update_job(job["id"], state="failed")
    response = client.post(f"/api/jobs/{job['id']}/reconcile", json={"not_created": True})
    assert response.status_code == 200, response.text
    remote.unknown_submission = False
    client.app.state.queue.process(job["id"])
    assert len(remote.submissions) == 2
    capture = store.job(job["id"])["params"]["native_artifact_capture"]
    first, second = capture["submissions"]
    assert [first["descriptor"]["attempt"], second["descriptor"]["attempt"]] == [0, 1]
    assert first["descriptor"]["generation"] != second["descriptor"]["generation"]
    directory = store.directory(recording["id"])
    assert (directory / f"ucloud-{job['id']}.request-0001.json").is_file()
    _, _, context = creation(store, recording["id"])
    assert len(context["native_artifacts"]["submissions"]) == 2


def test_preserved_output_without_observation_stays_an_explicit_gap(
    client, remote, native_job, monkeypatch
):
    _, recording, job = native_job
    store = client.app.state.store

    def crash(*args):
        raise OSError("Synthetic crash after the output was preserved")

    monkeypatch.setattr(native_capture, "observe_output", crash)
    with pytest.raises(OSError):
        client.app.state.queue.process(job["id"])
    assert (store.directory(recording["id"]) / f"ucloud-{job['id']}.json").is_file()
    monkeypatch.setattr(
        native_capture, "observe_output", lambda *a: pytest.fail("never backfilled")
    )
    client.app.state.queue.process(job["id"])
    assert len(downloads(remote)) == 1
    capture = store.job(job["id"])["params"]["native_artifact_capture"]
    assert capture["output"] == "not_captured" and capture["assembly_sha256"]
    result, version, context = creation(store, recording["id"])
    required = native_capture.inventory(
        context["native_artifacts"],
        recording_id=recording["id"],
        job_id=job["id"],
        creation=context["native_artifacts"]["creation"],
    )
    assert [item["filename"] for item in required] == [f"ucloud-{job['id']}.request-0000.json"]


def test_legacy_native_job_keeps_exact_earlier_behavior(client, remote, legacy_native_job):
    _, recording, job = legacy_native_job
    store = client.app.state.store
    client.app.state.queue.process(job["id"])
    params = store.job(job["id"])["params"]
    assert not {"native_artifact_policy", "native_artifact_capture"} & params.keys()
    assert "artifact_submission_sha256" not in params["ucloud_run"]
    _, _, context = creation(store, recording["id"])
    assert "native_artifacts" not in context
    assert not list(store.directory(recording["id"]).glob("ucloud-*.request-*"))


def test_batch_members_keep_their_own_evidence_with_one_journal(
    client,
    batch_remote,
    selection,
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    batch_id = jobs[0]["params"]["native_batch_id"]
    claimed = store.claim_job("transcribe")
    queue = client.app.state.queue
    queue.active_batch_id = batch_id
    queue.process(claimed["id"])
    run = store.native_batch(batch_id)["params"]["ucloud_run"]
    members = {m["job_id"]: m for m in store.native_batch(batch_id)["members"]}
    projections = set()
    for job in jobs:
        capture = store.job(job["id"])["params"]["native_artifact_capture"]
        [submission] = capture["submissions"]
        descriptor = submission["descriptor"]
        assert descriptor["scope"] == "batch" and descriptor["batch_id"] == batch_id
        assert descriptor["input"]["remote_name"] == members[job["id"]]["filename"]
        assert run["artifact_submission_sha256"] == native_capture.submission_sha256(capture)
        projections.add(json.dumps(descriptor["submission"], sort_keys=True))
        raw = capture["output"]["response"]["artifacts"]["raw_response"]
        assert run["raw_hashes"][job["id"]] == raw["sha256"]
        assert capture["output"]["match"]["remote_name"] == job["id"] + ".json"
        _, version, context = creation(store, job["recording_id"])
        assert context["native_artifacts"]["creation"]["version_id"] == version["id"]
        assert SECRET not in json.dumps(context)
    assert len(projections) == 1  # One shared submission, recorded once per member.
    assert len(batch_remote.submissions) == 1


def test_report_projects_metadata_only_native_evidence(client, remote, native_job):
    _, recording, job = native_job
    store = client.app.state.store
    client.app.state.queue.process(job["id"])
    result, version, context = creation(store, recording["id"])
    with ZipFile(io.BytesIO(selected_view_report(store, recording["project_id"]))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode("utf-8")
    [item] = [entry for entry in manifest["versions"] if "native_artifacts" in entry]
    evidence = item["native_artifacts"]
    encoded = json.dumps(evidence)
    descriptor = context["native_artifacts"]["submissions"][0]["descriptor"]
    for private in (
        recording["id"],
        job["id"],
        result["id"],
        version["id"],
        descriptor["generation"],
        descriptor["endpoint_sha256"],
        "/drive",
        "recording.wav",
        "remote-job",
        SECRET,
        "Hej CBS",
    ):
        assert private not in encoded
    assert evidence["scope"] == "single" and evidence["output"] != "not_captured"
    assert evidence["submissions"][0]["product_id"] == "gpu-nvidia-b200-1-mig.1g"
    assert evidence["remote_execution"] == evidence["retained_files"] == "not_checked"
    assert "Native single submission attempt 1" in methods
    assert any("credential-free projection" in gap for gap in item["provenance_gaps"])


@pytest.mark.parametrize("damage", [None, "request"])
def test_portable_capture_retains_and_verifies_native_evidence(
    client, remote, native_job, tmp_path, damage
):
    _, recording, job = native_job
    store = client.app.state.store
    client.app.state.queue.process(job["id"])
    store.execute("UPDATE jobs SET state='completed' WHERE state IN ('queued','running')")
    request = store.directory(recording["id"]) / f"ucloud-{job['id']}.request-0000.json"
    if damage == "request":
        request.write_bytes(request.read_bytes().replace(b'"attempt":0', b'"attempt": 0'))
        with pytest.raises(ValueError):
            portable_capture.capture_library(
                store,
                client.app.state.queue,
                tmp_path / "packages",
                gate=portable_capture.CaptureGate(),
            )
        return
    result = portable_capture.capture_library(
        store, client.app.state.queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
    )
    manifest = json.loads((Path(result["folder"]) / "manifest.json").read_text(encoding="utf-8"))
    roles = {
        item["relative_path"]: item["role"]
        for item in manifest["files"]
        if item["recording_id"] == recording["id"]
    }
    assert roles[request.name] == "processing_input"
    assert roles[f"ucloud-{job['id']}.json"] == "provider_response"
    assert not [gap for gap in manifest["legacy_gaps"] if gap["job_id"] == job["id"]]
