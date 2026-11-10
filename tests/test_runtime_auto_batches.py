"""Real host/coordinator receipts with synthetic Auto ASR; no services or model inference."""

import copy
import hashlib
import json
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runtime_batches import FinishedChild, batch_diagnostics, run_batch, wait_for
from test_runtime_batches import environment as environment

from cbs_transcriber import enhancement, runtime_batches, runtime_store
from cbs_transcriber import enhancement_api as api
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber.transcripts import normalize_segments

REAL_PIPELINE = enhancement.run_enhancement


def write_wave(path, sample=0):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * 48000)


def write_stages(folder, case, *, asr_only=False):
    options = json.loads((folder / "request.json").read_bytes())["options"]
    stage = folder / "transcription.json"
    new_asr = not stage.exists()
    if new_asr:
        rows = [] if case == "empty" else [{"text": "Synthetic wording.", "start": 0.2, "end": 2.0}]
        info = SimpleNamespace(
            language="fr" if case == "unsupported" else "en" if case == "en" else "da",
            language_probability=0.95,
            duration_after_vad=0.0 if case == "empty" else 2.0,
        )
        decision = worker._asr_language_decision(
            worker.language_binding(folder), options, info, len(rows)
        )
        worker.write_json(stage, {"segments": rows, "language_decision": decision})
    saved = json.loads(stage.read_bytes())
    rows, decision = saved["segments"], saved["language_decision"]
    if options["diarize"] and not (folder / "diarization.json").exists():
        worker.write_json(
            folder / "diarization.json",
            {"turns": [{"start": 0.0, "end": 3.0, "speaker": "A"}]},
        )
    if options["align"] and not asr_only and not (folder / "alignment.json").exists():
        aligned = decision["status"] == "resolved"
        worker.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": str(index),
                        "words": [
                            {
                                "word": word,
                                "start": 0.3 + token * 0.4 if aligned else None,
                                "end": 0.6 + token * 0.4 if aligned else None,
                                "score": 0.99 if aligned else None,
                            }
                            for token, word in enumerate(row["text"].split())
                        ],
                    }
                    for index, row in enumerate(rows)
                ],
                "language_outcome": {
                    "schema": 1,
                    "state": "aligned" if aligned else "unavailable_language",
                    "decision_sha256": worker.decision_digest(decision),
                },
            },
        )
    return new_asr


@pytest.fixture
def automatic(environment, monkeypatch):
    env = environment
    env.cases, env.reviewed, env.fail_once = {}, {}, set()
    env.params["enhancement"]["language"] = "auto"
    env.params["enhancement"]["diarize"] = True
    prepare_count = [0]

    def prepare(args, cancel):
        prepare_count[0] += 1
        write_wave(Path(args[-1]), prepare_count[0])

    old_worker = env.drive.worker

    def fake_worker(folder, lock_fd):
        job_id = json.loads((folder / "inputs.json").read_bytes())["job_id"]
        failed = job_id in env.fail_once
        new_asr = write_stages(folder, env.cases[job_id], asr_only=failed)
        env.drive.asr_calls += int(new_asr)
        if failed:
            env.fail_once.remove(job_id)
            env.drive.worker_count += 1
            env.drive.events.append(("worker", folder.name))
            env.drive.accepted.set()
            # A bad later stage must not contaminate valid ASR/speaker carry.
            (folder / "alignment.json").write_bytes(b"null")
            worker.write_json(folder / "error.json", {"code": "processing"})

            class Failed(FinishedChild):
                def poll(self):
                    return 1

            return Failed()
        return old_worker(folder, lock_fd)

    enqueue = env.enqueue

    def add(case="da", overrides=None):
        job = enqueue(overrides)
        env.cases[job["id"]] = case
        reviewed = env.store.add_result(
            job["recording_id"],
            normalize_segments([{"text": "Reviewed text stays.", "start": 0, "end": 3}]),
            {},
            "Reviewed",
            {"kind": "import"},
        )
        env.reviewed[job["id"]] = reviewed
        return job

    monkeypatch.setattr(enhancement, "run_enhancement", REAL_PIPELINE)
    monkeypatch.setattr(enhancement.media, "ffmpeg", prepare)
    monkeypatch.setattr(api, "remote_speakers_complete", lambda *_: True)
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda _: "1.2.1")
    monkeypatch.setattr(env.drive, "worker", fake_worker)
    env.add = add
    return env


def result_for(env, member):
    result = env.store.result(member["recording_id"], member["result_id"])
    assert result["job_id"] == member["job_id"]
    version = env.store.result_versions(member["recording_id"], result["id"])[0]
    assert (
        version["context"]["processing"]["language_decision"]
        == result["source"]["language_decision"]
    )
    assert (
        version["context"]["processing"]["alignment_outcome"]
        == result["source"]["alignment_outcome"]
    )
    assert version["context"]["producer_job_id"] == member["job_id"]
    reviewed = env.reviewed[member["job_id"]]
    assert env.store.recording(member["recording_id"])["active_transcript_id"] == reviewed["id"]
    assert (
        env.store.result(member["recording_id"], reviewed["id"])["segments"] == reviewed["segments"]
    )
    files = member["output_receipt"]["files"]
    assert set(files) == {
        "transcription.json",
        "alignment.json",
        "diarization.json",
        "outcome.json",
    }
    folder = env.store.directory(member["recording_id"]) / "enhancements" / member["job_id"]
    for name, expected in files.items():
        raw = (folder / name).read_bytes()
        assert expected == {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    assert env.store.job(member["job_id"])["state"] == "completed"
    return result


def test_v125_one_auto_owner_keeps_member_decisions_and_atomic_results_separate(automatic):
    env = automatic
    jobs = [env.add("da"), env.add("en")]
    frozen = {job["id"]: copy.deepcopy(job["params"]) for job in jobs}
    batch = run_batch(env)
    assert batch["state"] == "completed", batch_diagnostics(env, batch["id"])
    assert len(env.drive.submissions) == 1
    assert batch["identity"]["options"]["language"] == "auto"
    observed, hashes = set(), set()
    for member in batch["members"]:
        result = result_for(env, member)
        decision = result["source"]["language_decision"]
        observed.add(decision["effective"])
        hashes.add(decision["input_sha256"])
        assert decision["effective"] == env.cases[member["job_id"]]
        assert result["source"]["capability"] == "words_speakers"
        params = env.store.job(member["job_id"])["params"]
        assert params["enhancement"] == frozen[member["job_id"]]["enhancement"]
        assert (
            params["ucloud_api"]["batch_plan"]
            == frozen[member["job_id"]]["ucloud_api"]["batch_plan"]
        )
    assert observed == {"da", "en"} and len(hashes) == 2
    assert batch["journal"]["cleanup_state"] == "trashed"
    assert env.drive.trashed == [
        runtime_batches.remote_folder(runtime_batches._settings(batch), batch["id"])
    ]


def test_v125_partial_and_empty_auto_are_completed_receipted_results_with_speakers(automatic):
    env = automatic
    env.add("unsupported")
    env.add("empty")
    batch = run_batch(env)
    assert batch["state"] == "completed", batch_diagnostics(env, batch["id"])
    results = [result_for(env, member) for member in batch["members"]]
    partial = next(result for result in results if result["segments"])
    empty = next(result for result in results if not result["segments"])
    assert partial["source"]["capability"] == empty["source"]["capability"] == "speakers"
    assert partial["source"]["alignment_outcome"]["state"] == "unavailable_language"
    assert partial["segments"][0]["start"] == 0.2 and partial["segments"][0]["end"] == 2.0
    assert partial["segments"][0]["speaker"] != "UNKNOWN"
    assert all(word["start"] is None for word in partial["segments"][0]["words"])
    assert empty["source"]["language_decision"]["status"] == "no_speech"
    assert "no speech detected" in empty["label"]
    assert batch["journal"]["cleanup_state"] == "trashed"


def local_item(env, job, case):
    recording = env.store.recording(job["recording_id"])
    folder = env.store.directory(recording["id"]) / "enhancements" / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    options = enhancement.EnhancementOptions.model_validate(job["params"]["enhancement"])
    write_wave(folder / "audio.wav")
    worker.write_json(
        folder / "request.json",
        {
            "passages": [],
            "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"})
            | {"duration": 3.0, "whole_recording": False},
        },
    )
    write_stages(folder, case)
    return SimpleNamespace(job=job, recording=recording, folder=folder, options=options)


@pytest.mark.parametrize("case", ["da", "empty"])
@pytest.mark.parametrize("alignment", ["missing", "malformed"])
def test_v125_partial_stage_validation_isolates_bad_alignment_from_asr_and_diarization(
    automatic, case, alignment
):
    env = automatic
    job = env.add(case)
    owner = runtime_batches.Coordinator(env.worker, runtime_store.claim(env.store))
    item = local_item(env, job, case)
    asr = (item.folder / "transcription.json").read_bytes()
    request = (item.folder / "request.json").read_bytes()
    path = item.folder / "alignment.json"
    if alignment == "missing":
        path.unlink()
    else:
        path.write_bytes(b"null")
    outputs = {
        name: runtime_batches.hash_file(path)
        for path in item.folder.glob("*.json")
        for name in [path.name]
        if name != "request.json"
    }
    verified = owner.partial_stages(item, outputs)
    assert set(verified) == {"transcription.json", "diarization.json"}
    assert (item.folder / "transcription.json").read_bytes() == asr
    assert (item.folder / "request.json").read_bytes() == request
    if alignment == "malformed":
        assert (item.folder / "invalid-alignment.json").read_bytes() == b"null"


@pytest.mark.parametrize("damage", ["missing", "input", "request", "model", "frozen_options"])
def test_v125_bad_asr_decision_never_advertises_dependent_stages(automatic, damage):
    env = automatic
    job = env.add()
    owner = runtime_batches.Coordinator(env.worker, runtime_store.claim(env.store))
    item = local_item(env, job, "da")
    stage = json.loads((item.folder / "transcription.json").read_bytes())
    if damage == "missing":
        stage.pop("language_decision")
    elif damage in {"input", "request"}:
        stage["language_decision"][damage + "_sha256"] = "0" * 64
    elif damage == "model":
        stage["language_decision"]["model"]["revision"] = "a" * 40
    else:
        item.options = item.options.model_copy(update={"min_score": 0.8})
    worker.write_json(item.folder / "transcription.json", stage)
    raw = (item.folder / "transcription.json").read_bytes()
    # A forged full-ASR source snapshot must not stand in for verified ASR after failure.
    item.job["params"]["source_snapshot"] = env.reviewed[job["id"]]
    outputs = {
        name: runtime_batches.hash_file(item.folder / name)
        for name in ("transcription.json", "alignment.json", "diarization.json")
    }
    assert owner.partial_stages(item, outputs) == {}
    assert (item.folder / "invalid-transcription.json").read_bytes() == raw
    assert len(env.store.results(job["recording_id"])) == 1


def test_v125_explicit_retry_carries_valid_asr_and_speakers_without_repeating_asr(
    automatic, monkeypatch
):
    env = automatic
    first, second = env.add(), env.add("en")
    env.fail_once.add(first["id"])
    sibling_preparing, release_sibling = threading.Event(), threading.Event()
    prepare = enhancement.media.ffmpeg

    def hold_sibling_preparation(args, cancel):
        if Path(args[-1]).parent.name == second["id"]:
            sibling_preparing.set()
            while not release_sibling.wait(0.01):
                if cancel.is_set():
                    raise enhancement.media.Cancelled()
        return prepare(args, cancel)

    # An unfinished local member keeps admission open without occupying the GPU first.
    monkeypatch.setattr(enhancement.media, "ffmpeg", hold_sibling_preparation)
    env.drive.hold_worker = True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=runtime_batches.run, args=(env.worker, claimed))
    thread.start()
    try:
        wait_for(
            lambda: (
                sibling_preparing.is_set()
                and runtime_batches._retryable(runtime_store.get(env.store, claimed["id"]))
            ),
            diagnostic=lambda: batch_diagnostics(env, claimed["id"]),
        )
        failed = runtime_batches._retryable(runtime_store.get(env.store, claimed["id"]))[0]
        assert failed["job_id"] == first["id"] and failed["remote_receipt"]["worker_gone"] is True
        assert set(failed["remote_receipt"]["files"]) == {"transcription.json", "diarization.json"}
        folder = env.store.directory(failed["recording_id"]) / "enhancements" / failed["job_id"]
        original_asr = (folder / "transcription.json").read_bytes()
        original_request = (folder / "request.json").read_bytes()
        runtime_batches.retry(env.store, env.worker.credentials, claimed["id"])
        env.drive.hold_worker = False
        release_sibling.set()
        thread.join(15)
        assert not thread.is_alive(), batch_diagnostics(env, claimed["id"])
        final = runtime_store.get(env.store, claimed["id"])
        assert final["state"] == "completed", batch_diagnostics(env, claimed["id"])
        retried = next(member for member in final["members"] if member["job_id"] == first["id"])
        assert retried["attempt"] == 2 and retried["task_id"] != failed["task_id"]
        assert retried["input_receipt"]["carried_checkpoints"] == failed["remote_receipt"]["files"]
        assert (folder / "transcription.json").read_bytes() == original_asr
        assert (folder / "request.json").read_bytes() == original_request
        assert env.drive.asr_calls == 2 and len(env.drive.submissions) == 1
        assert all(env.store.job(job["id"])["state"] == "completed" for job in (first, second))
        for member in final["members"]:
            result_for(env, member)
    finally:
        env.worker.stopping.set()
        env.drive.hold_worker = False
        release_sibling.set()
        thread.join(8)


def test_v125_cached_auto_commit_recovery_needs_no_readiness_private_upload_or_new_task(
    automatic, monkeypatch
):
    env = automatic
    job = env.add("unsupported")
    save = env.store.add_result
    monkeypatch.setattr(
        env.store,
        "add_result",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("Synthetic commit failure")),
    )
    failed = run_batch(env)
    [before] = failed["members"]
    assert (
        before["attempt_state"] == "complete"
        and env.store.job(job["id"])["params"]["runtime_output_receipt"]
    )
    private = sum(path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads)
    monkeypatch.setattr(env.store, "add_result", save)

    def refuse(*args, **kwargs):
        pytest.fail("Cached result recovery must not check inference readiness or execute ASR")

    monkeypatch.setattr(runtime_batches.Coordinator, "readiness", refuse)
    monkeypatch.setattr(env.drive, "worker", refuse)
    monkeypatch.setattr(enhancement, "MODELS", {})
    runtime_batches.retry(env.store, env.worker.credentials, failed["id"])
    runtime_batches.run(env.worker, runtime_store.claim(env.store))
    final = runtime_store.get(env.store, failed["id"])
    [after] = final["members"]
    assert after["task_id"] == before["task_id"] and after["attempt"] == before["attempt"]
    assert len(env.drive.submissions) == 1 and env.drive.asr_calls == 1
    assert (
        sum(path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads)
        == private
    )
    result_for(env, after)


def test_v125_stop_harvests_completed_auto_output_before_owned_cleanup(automatic):
    env = automatic
    job = env.add("empty")
    env.drive.hold_worker = True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=runtime_batches.run, args=(env.worker, claimed))
    thread.start()
    try:
        assert env.drive.accepted.wait(8)
        runtime_batches.cancel(env.store, env.worker.credentials, claimed["id"])
        thread.join(15)
        assert not thread.is_alive(), batch_diagnostics(env, claimed["id"])
        final = runtime_store.get(env.store, claimed["id"])
        assert env.store.job(job["id"])["state"] == "completed"
        result = result_for(env, final["members"][0])
        assert result["segments"] == []
        assert env.drive.terminations == ["1"]
        assert final["journal"]["cleanup_state"] == "trashed"
        assert env.drive.trashed == [
            runtime_batches.remote_folder(runtime_batches._settings(final), final["id"])
        ]
    finally:
        env.worker.stopping.set()
        env.drive.hold_worker = False
        thread.join(8)
