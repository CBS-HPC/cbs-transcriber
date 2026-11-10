"""Real Store creation transactions with synthetic attempt rows and no remote finalizer."""

import copy
import io
import json
from zipfile import ZipFile

import pytest
import test_runtime_capture as samples

from cbs_transcriber import research_reports, runtime_capture, runtime_store
from cbs_transcriber.store import Store, now
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def ready(tmp_path, monkeypatch, request):
    sample = samples.TestRuntimeCapture()
    sample.setUp()
    request.addfinalizer(sample.doCleanups)
    store = Store(tmp_path / "synthetic-library")
    project = store.create_project("Synthetic Runtime creation")
    with monkeypatch.context() as fixed_identity:
        fixed_identity.setattr("cbs_transcriber.store.new_id", lambda: samples.REC)
        store.create_recording(project["id"], "source.wav")
    stamp = now()
    with store.connection() as db:
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,state,params,created,updated) "
            "VALUES (?,?,'enhance','running',?,?,?)",
            (samples.JOB, samples.REC, json.dumps(sample.job["params"]), stamp, stamp),
        )
        db.execute(
            "INSERT INTO runtime_batch_runs(id,project_id,compatibility_hash,identity,"
            "created,updated) "
            "VALUES (?,?,?,?,?,?)",
            (
                samples.BINDING["batch_id"],
                project["id"],
                samples.BINDING["identity_hash"],
                json.dumps(samples.IDENTITY),
                stamp,
                stamp,
            ),
        )
        db.execute(
            "INSERT INTO runtime_batch_members(job_id,batch_id,recording_id,ordinal,"
            "source_identity,created,updated) "
            "VALUES (?,?,?,0,'{}',?,?)",
            (samples.JOB, samples.BINDING["batch_id"], samples.REC, stamp, stamp),
        )
        db.execute(
            "INSERT INTO runtime_batch_attempts(job_id,attempt,task_id,state,input_receipt,"
            "remote_receipt,created,updated) "
            "VALUES (?,1,?,'complete',?,?,?,?)",
            (
                samples.JOB,
                samples.BINDING["task_id"],
                json.dumps(sample.inputs),
                json.dumps(sample.remote),
                stamp,
                stamp,
            ),
        )

    # This scope verifies Store atomic binding, not the separately tested ledger validator.
    def finalize(db, store, parent, result_id, *args, **kwargs):
        db.execute(
            "UPDATE runtime_batch_members SET result_id=? WHERE job_id=?", (result_id, parent["id"])
        )

    monkeypatch.setattr(runtime_store, "record_result", finalize)
    return store, sample


def create(store, sample, **kwargs):
    return store.add_result(
        samples.REC,
        normalize_segments([{"text": "Private words", "start": 0, "end": 1}]),
        {},
        "Synthetic Runtime output",
        {"kind": "provider", "engine": "whisperx", "execution": "ucloud", "language": "da"},
        job_id=samples.JOB,
        runtime_receipt=sample.output,
        **kwargs,
    )


def test_store_binds_exact_creation_and_keeps_provider_meaning(ready):
    store, sample = ready
    result = create(store, sample, runtime_artifacts=sample.value)
    version = store.result_versions(samples.REC, result["id"])[0]
    proof = version["context"]["runtime_artifacts"]
    creation = {
        "result_id": result["id"],
        "version_id": version["id"],
        "payload_sha256": version["payload_sha256"],
    }
    assert proof["creation"] == creation
    assert proof["origin"]["request_checkpoint"]["creation"] == creation
    assert version["source_version_id"] is None
    assert "producer_artifacts" not in version["context"]
    current = store.recording(samples.REC)
    store.save_transcript(
        samples.REC, current["segments"], current["speakers"], current["revision"]
    )
    assert (
        store.result_versions(samples.REC, result["id"])[0]["context"]["runtime_artifacts"] == proof
    )


def test_failed_binder_rolls_back_result_version_job_and_keeps_notes(ready, monkeypatch):
    store, sample = ready
    store.save_notes("recordings", samples.REC, "Independent notes", 0)
    before = store.recording(samples.REC)

    def refuse(*args, **kwargs):
        raise ValueError("Synthetic creation binding failure")

    monkeypatch.setattr(runtime_capture, "bind", refuse)
    with pytest.raises(ValueError, match="binding failure"):
        create(store, sample, runtime_artifacts=sample.value)
    assert store.recording(samples.REC) == before
    assert store.all("SELECT * FROM result_versions") == []
    assert store.job(samples.JOB)["state"] == "running"


def test_omitted_or_drifted_receipts_cannot_create_result(ready):
    store, sample = ready
    with pytest.raises(ValueError):
        create(store, sample)
    changed = copy.deepcopy(sample.value)
    changed["origin"]["generation"] = "0" * 32
    with pytest.raises(ValueError):
        create(store, sample, runtime_artifacts=changed)
    assert store.all("SELECT * FROM transcript_results") == []


def test_producer_trash_idempotency_never_backfills_or_restores(ready):
    store, sample = ready
    result = create(store, sample, runtime_artifacts=sample.value)
    proof = store.result_versions(samples.REC, result["id"])[0]["context"]["runtime_artifacts"]
    current = store.recording(samples.REC)
    store.set_result_trashed(samples.REC, result["id"], current["revision"], trashed=True)
    before = store.result_versions(samples.REC, result["id"])
    assert create(store, sample)["id"] == result["id"]
    assert store.result_versions(samples.REC, result["id"]) == before
    assert store.all("SELECT result_id FROM result_trash") == [{"result_id": result["id"]}]
    assert before[0]["context"]["runtime_artifacts"] == proof


def test_legacy_without_policy_or_observation_retains_explicit_gap(ready):
    store, sample = ready
    params = copy.deepcopy(sample.job["params"])
    params.pop("runtime_artifact_policy")
    inputs = {key: value for key, value in sample.inputs.items() if key != "artifact_origin"}
    remote = {key: value for key, value in sample.remote.items() if not key.startswith("artifact_")}
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), samples.JOB))
    store.execute(
        "UPDATE runtime_batch_attempts SET input_receipt=?,remote_receipt=?",
        (json.dumps(inputs), json.dumps(remote)),
    )
    result = create(store, sample)
    assert "runtime_artifacts" not in store.result_versions(samples.REC, result["id"])[0]["context"]


def test_report_uses_trusted_creation_and_attempt_without_current_artifact_reads(
    ready, monkeypatch
):
    store, sample = ready
    result = create(store, sample, runtime_artifacts=sample.value)
    current = store.recording(samples.REC)
    store.save_transcript(
        samples.REC, current["segments"], current["speakers"], current["revision"]
    )
    version = store.result_versions(samples.REC, result["id"])[0]
    selected = [
        {"recording_id": samples.REC, "result_id": result["id"], "version_id": version["id"]}
    ]

    def refuse(*args, **kwargs):
        pytest.fail("Historical report attempted current artifact capture")

    monkeypatch.setattr("cbs_transcriber.provenance_artifacts.capture_file", refuse)
    with ZipFile(
        io.BytesIO(research_reports.project_report(store, current["project_id"], selected))
    ) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode("utf-8")
    chosen = next(item for item in manifest["versions"] if item["selected"])
    assert chosen["runtime_artifacts"]["request_boundary"] == "before_publication"
    assert chosen["runtime_artifacts"]["worker_environment"] == "not_captured"
    assert "Runtime request captured before publication" in methods
    assert "Runtime transcription output SHA-256=" in methods
    for private in (samples.REC, samples.JOB, result["id"], "Private words", str(sample.folder)):
        assert private not in json.dumps(manifest)
    other = store.add_job(samples.REC, "enhance", {})
    store.execute("UPDATE transcript_results SET job_id=? WHERE id=?", (other["id"], result["id"]))
    with pytest.raises(ValueError, match="producer ownership"):
        research_reports.project_report(store, current["project_id"], selected)
