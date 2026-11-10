"""Real coordinator/Store artifact fences with synthetic input and mocked remote inference."""

import copy
import io
import json
import threading
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
import test_remote_preparation_batches as preparations
import test_runtime_batches as fixtures

from cbs_transcriber import enhancement, research_reports, runtime_capture, runtime_store
from cbs_transcriber import runtime_batches as batches


@pytest.fixture
def environment(tmp_path, monkeypatch, credentials):
    yield from fixtures.environment.__wrapped__(tmp_path, monkeypatch, credentials)


@pytest.mark.parametrize("compressed", [False, True])
def test_actual_collected_files_bind_creation_before_terminal_and_cached_reuse_never_observes_again(
    environment, tmp_path, monkeypatch, compressed
):
    env = environment
    job, directory = preparations.prepare(
        env, tmp_path, monkeypatch, compressed=compressed, capture=True
    )
    received, terminal = [], []
    read = env.drive.read

    def raw_outcome(client, server, path, limit=batches.api.MAX_STATUS_BYTES):
        raw = read(client, server, path, limit)
        if raw is not None and path.endswith("/outcome.json"):
            raw = b" \n" + json.dumps(json.loads(raw), indent=2).encode() + b"\r\n"
            received.append(raw)
        return raw

    monkeypatch.setattr(batches.api, "read_remote", raw_outcome)
    checkpoint = runtime_store.checkpoint_attempt

    def fenced(store, identifier, job_id, epoch, task_id, **fields):
        if "artifact_checkpoints" in (fields.get("remote_update") or {}):
            current = runtime_store.get(store, identifier)
            member = next(item for item in current["members"] if item["job_id"] == job_id)
            assert member["attempt_state"] not in runtime_store.ATTEMPT_TERMINAL
            assert fields["state"] == "complete"
            terminal.append(copy.deepcopy(fields["remote_update"]))
        return checkpoint(store, identifier, job_id, epoch, task_id, **fields)

    monkeypatch.setattr(runtime_store, "checkpoint_attempt", fenced)
    batch = fixtures.run_batch(env)
    assert batch["state"] == "completed", fixtures.batch_diagnostics(env, batch["id"])
    assert len(terminal) == 1 and env.drive.asr_calls == 1
    member = batch["members"][0]
    origin = member["input_receipt"]["artifact_origin"]
    assert origin["input_manifest_sha256"] == member["input_receipt"]["manifest_sha256"]
    folder = directory / "enhancements" / job["id"]
    raw = (folder / "outcome.json").read_bytes()
    assert received and raw == received[-1] and raw.startswith(b" \n")
    expected = {"transcription.json", "alignment.json", "outcome.json"}
    if compressed:
        expected.add("audio.preparation.json")
    assert set(member["remote_receipt"]["artifact_checkpoints"]) == expected
    assert set(member["remote_receipt"]["artifact_files"]) == expected
    result = env.store.one("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    version = env.store.result_versions(job["recording_id"], result["id"])[0]
    bound = version["context"]["runtime_artifacts"]
    assert bound["creation"] == {
        "result_id": result["id"],
        "version_id": version["id"],
        "payload_sha256": version["payload_sha256"],
    }
    assert (
        bound["origin"]["request_checkpoint"]["artifacts"]["request"]["sha256"]
        == member["input_receipt"]["files"]["request.json"]["sha256"]
    )
    outcome = bound["checkpoints"]["outcome.json"]["artifacts"]
    assert outcome["output"]["bytes"] == len(raw)
    assert outcome["raw_response"] == "not_captured" and outcome["environment"] == "not_captured"
    selection = [
        {
            "recording_id": job["recording_id"],
            "result_id": result["id"],
            "version_id": version["id"],
        }
    ]
    project_id = env.store.recording(job["recording_id"])["project_id"]
    with monkeypatch.context() as historical:
        historical.setattr(
            Path,
            "read_bytes",
            lambda *_a, **_kw: pytest.fail("historical report cannot read files"),
        )
        historical.setattr(
            runtime_capture.artifacts,
            "capture_file",
            lambda *_a, **_kw: pytest.fail("historical report cannot observe artifacts"),
        )
        data = research_reports.project_report(env.store, project_id, selection)
        with ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        report = manifest["versions"][0]
        assert report["runtime_artifacts"]["worker_environment"] == "not_captured"
        if compressed:
            assert report["remote_audio_preparation"]["current_files"] == "not_checked"
            assert report["audio_preparations"][0]["observation"] == "remote_decoded_pcm"
            original_context = env.store.one(
                "SELECT context FROM result_versions WHERE id=?", (version["id"],)
            )["context"]
            corrupted = json.loads(original_context)
            corrupted["remote_audio_preparation"]["unexpected_claim"] = True
            env.store.execute(
                "UPDATE result_versions SET context=? WHERE id=?",
                (json.dumps(corrupted), version["id"]),
            )
            try:
                with pytest.raises(ValueError):
                    research_reports.project_report(env.store, project_id, selection)
            finally:
                env.store.execute(
                    "UPDATE result_versions SET context=? WHERE id=?",
                    (original_context, version["id"]),
                )
    captured = copy.deepcopy(member["remote_receipt"])
    monkeypatch.setattr(
        runtime_capture,
        "origin",
        lambda *_a, **_kw: pytest.fail("cached publication cannot reobserve"),
    )
    monkeypatch.setattr(
        runtime_capture, "collect", lambda *_a, **_kw: pytest.fail("cached output cannot reobserve")
    )
    owner = batches.Coordinator(env.worker, batch)
    current = env.store.job(job["id"])

    def cached(job, recording, folder, options, validate):
        item = SimpleNamespace(
            job=job,
            recording=recording,
            folder=folder,
            options=options,
            validate=validate,
            done=threading.Event(),
        )
        owner.pending[member["task_id"]] = item
        assert owner.cached(item, member)
        assert item.done.is_set()

    child = SimpleNamespace(
        store=env.store,
        credentials=env.worker.credentials,
        cancel_event=threading.Event(),
        runtime_dispatch=cached,
    )
    enhancement.run_enhancement(child, current, env.store.recording(job["recording_id"]))
    assert runtime_store.get(env.store, batch["id"])["members"][0]["remote_receipt"] == captured
    assert len(env.store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))) == 1


def test_terminal_collection_uses_only_persisted_observations_and_rejects_replaced_bytes(
    monkeypatch,
):
    owner = object.__new__(batches.Coordinator)
    origin = {"synthetic": "origin"}
    member = {
        "input_receipt": {"artifact_origin": origin},
        "attempt_state": "complete",
        "remote_receipt": {
            "artifact_checkpoints": {"transcription.json": {"recorded": True}},
            "artifact_files": {"transcription.json": {"size": 1, "sha256": "a" * 64}},
        },
    }
    monkeypatch.setattr(
        runtime_capture,
        "collect",
        lambda *_a, **_kw: pytest.fail("terminal collection cannot capture"),
    )
    monkeypatch.setattr(runtime_capture, "validate_checkpoints", lambda value, *_a, **_kw: value)
    item = SimpleNamespace(folder=None)
    assert owner.artifact_collection(item, member, member["remote_receipt"]["artifact_files"]) == {}
    with pytest.raises(ValueError, match="differ from recovered files"):
        owner.artifact_collection(
            item, member, {"transcription.json": {"size": 1, "sha256": "b" * 64}}
        )


def test_failed_task_captures_only_validated_partial_stage_and_observed_controls(
    environment, tmp_path, monkeypatch
):
    env = environment
    job, _ = preparations.prepare(env, tmp_path, monkeypatch, capture=True)
    env.drive.fail_next, env.drive.partial_fail = True, True
    checkpoint = runtime_store.checkpoint_attempt
    observed = []

    def fenced(store, identifier, job_id, epoch, task_id, **fields):
        update = fields.get("remote_update") or {}
        if "artifact_checkpoints" in update:
            before = runtime_store.get(store, identifier)["members"][0]
            assert before["attempt_state"] not in runtime_store.ATTEMPT_TERMINAL
            assert fields["state"] == "failed"
            observed.append(update)
        return checkpoint(store, identifier, job_id, epoch, task_id, **fields)

    monkeypatch.setattr(runtime_store, "checkpoint_attempt", fenced)
    batch = fixtures.run_batch(env)
    assert len(observed) == 1
    remote = batch["members"][0]["remote_receipt"]
    assert set(remote["files"]) == {"transcription.json"}
    assert set(remote["artifact_files"]) == {
        "transcription.json",
        "audio.preparation.json",
        "outcome.json",
    }
    assert set(remote["artifact_checkpoints"]) == set(remote["artifact_files"])
    assert not env.store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    assert env.drive.asr_calls == 1


def test_complete_output_rejected_by_result_validator_never_claims_validated_artifact_capture(
    environment, tmp_path, monkeypatch
):
    env = environment
    job, directory = preparations.prepare(env, tmp_path, monkeypatch, capture=True)
    worker = env.drive.worker

    def invalid(folder, lock_fd):
        child = worker(folder, lock_fd)
        path = folder / "alignment.json"
        value = json.loads(path.read_bytes())
        value["passages"][0]["words"][0]["word"] = "WRONG"
        path.write_text(json.dumps(value))
        return child

    monkeypatch.setattr(env.drive, "worker", invalid)
    batch = fixtures.run_batch(env)
    member = batch["members"][0]
    assert "artifact_origin" in member["input_receipt"]
    assert "artifact_checkpoints" not in member["remote_receipt"]
    assert not env.store.all("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    assert (directory / "enhancements" / job["id"] / "alignment.json").exists()
    assert env.drive.asr_calls == 1


def test_retry_between_old_future_snapshot_and_checkpoint_preserves_new_attempt(
    environment, monkeypatch
):
    env = environment
    job = env.enqueue()
    batch = runtime_store.claim(env.store)
    owner = batches.Coordinator(env.worker, batch)
    member = batch["members"][0]
    runtime_store.checkpoint_attempt(
        env.store,
        batch["id"],
        job["id"],
        batch["epoch"],
        member["task_id"],
        state="failed",
        member_state="failed",
        error_code="processing",
        remote_update={"outcome": "failed", "worker_gone": True, "files": {}},
    )
    future = Future()
    future.set_exception(ValueError("old drained child failed"))
    owner.futures[future] = (job["id"], member["task_id"])
    lookup = owner.member
    injected = False

    def raced(job_id):
        nonlocal injected
        current = lookup(job_id)
        if not injected:
            injected = True
            runtime_store.retry_member(
                env.store,
                batch["id"],
                job_id,
                batch["epoch"],
                task_id=current["task_id"],
                carried_checkpoints={},
            )
        return current

    monkeypatch.setattr(owner, "member", raced)
    owner.completed_futures()
    latest = runtime_store.get(env.store, batch["id"])["members"][0]
    assert latest["attempt"] == 2 and latest["state"] == "queued"
    assert env.store.job(job["id"])["state"] == "queued"
