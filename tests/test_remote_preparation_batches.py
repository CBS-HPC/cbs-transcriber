"""Actual local result/coordinator pipeline, fake UCloud/decoder, and synthetic originals."""

import copy
import io
from pathlib import Path

import pytest
import test_repair_bundles as source_fixtures
import test_runtime_batches as fixtures

from cbs_transcriber import audio_inputs, enhancement, runtime_capture
from cbs_transcriber import remote_audio_input as audio

REAL_ENHANCEMENT = enhancement.run_enhancement


@pytest.fixture
def environment(tmp_path, monkeypatch, credentials):
    yield from fixtures.environment.__wrapped__(tmp_path, monkeypatch, credentials)


def prepare(
    environment,
    tmp_path,
    monkeypatch,
    *,
    decoder_ready=True,
    corrupt_pcm=False,
    compressed=True,
    capture=False,
):
    env = environment
    snapshot = source_fixtures.study.__wrapped__(tmp_path / "source-shape")[-2]
    project = env.store.one("SELECT id FROM projects LIMIT 1")
    extension = "flac" if compressed else "wav"
    recording = env.store.create_recording(project["id"], "synthetic." + extension)
    env.store.execute(
        "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
    )
    source, pcm = b"synthetic compressed original", b"\0\1" * (3 * 16000)
    snapshot.update(
        recording_id=recording["id"],
        format=extension,
        bytes=len(source),
        sha256=audio.digest(source),
        duration=3,
        audio_frames=48000,
        pcm_sha256=audio.digest(pcm),
        container_duration_observation=3,
    )
    directory = env.store.directory(recording["id"])
    directory.mkdir(parents=True)
    if compressed:
        (directory / "original.flac").write_bytes(source)
    else:
        source_fixtures.write_wav(directory / "original.wav", pcm)
        source = (directory / "original.wav").read_bytes()
        snapshot.update(
            bytes=len(source), sha256=audio.digest(source), decoder=audio_inputs._decoder()
        )
    params = copy.deepcopy(env.params)
    params["audio_input"] = audio_inputs.validate(snapshot)
    params["ucloud_api"]["batch_plan"]["identity"]["input_protocol"] = audio.PROTOCOL
    if capture:
        params = runtime_capture.freeze(params)
    job = env.store.add_job(recording["id"], "enhance", params)
    monkeypatch.setattr(enhancement, "run_enhancement", REAL_ENHANCEMENT)
    if compressed:
        monkeypatch.setattr(
            audio_inputs,
            "prepare_wav",
            lambda *_a, **_kw: pytest.fail(
                "compressed whole request must not materialize local WAV"
            ),
        )
    submit = env.drive.submit_job

    def allocation(spec):
        value = submit(spec)
        if decoder_ready:
            folder = env.drive.path(spec["resources"][0]["path"])
            (folder / "decoder.json").write_bytes(
                audio.encoded({"protocol": audio.PROTOCOL, "decoder": audio.DECODER})
            )
        return value

    monkeypatch.setattr(env.drive, "submit_job", allocation)
    monkeypatch.setattr(audio, "capability", lambda: ({}, Path("synthetic-pinned-decoder")))

    class Decoder:
        def __init__(self, *_a, **_kw):
            self.stdout = io.BytesIO(b"\0\2" * 48000 if corrupt_pcm else pcm)
            self.returncode = 0

        def wait(self, **_kw):
            return 0

        def kill(self):
            self.returncode = 1

    if compressed:
        monkeypatch.setattr(audio.subprocess, "Popen", Decoder)
    infer = env.drive.worker

    def worker(folder, lock_fd):
        try:
            audio.prepare(folder)
        except ValueError:
            (folder / "error.json").write_bytes(b'{"code":"processing"}')

            class Failed(fixtures.FinishedChild):
                def poll(self):
                    return 1

            return Failed()
        child = infer(folder, lock_fd)
        if (folder / "alignment.json").exists():
            (folder / "alignment.json").write_bytes(
                audio.encoded(
                    {
                        "passages": [
                            {
                                "id": "0",
                                "words": [
                                    {"word": "Hej", "start": 0.1, "end": 1.0, "score": 0.9},
                                    {"word": "København.", "start": 1.0, "end": 2.5, "score": 0.9},
                                ],
                            }
                        ]
                    }
                )
            )
        return child

    monkeypatch.setattr(env.drive, "worker", worker)
    return job, directory


def test_compressed_original_upload_and_remote_receipt_reach_exact_creation_version(
    environment, tmp_path, monkeypatch
):
    job, directory = prepare(environment, tmp_path, monkeypatch)
    batch = fixtures.run_batch(environment)
    assert batch["state"] == "completed", fixtures.batch_diagnostics(environment, batch["id"])
    saved = environment.store.job(job["id"])
    assert saved["state"] == "completed"
    assert environment.drive.asr_calls == 1 and len(environment.drive.submissions) == 1
    folder = directory / "enhancements" / job["id"]
    assert not (folder / "audio.wav").exists()
    receipt = audio.read(folder / audio.PREPARATION)
    assert saved["params"]["remote_audio_preparation"] == receipt
    result = environment.store.one("SELECT id FROM transcript_results WHERE job_id=?", (job["id"],))
    context = environment.store.result_versions(job["recording_id"], result["id"])[0]["context"]
    assert context["remote_audio_preparation"] == receipt
    assert context["audio_preparations"][0]["prepared"] == receipt["prepared"]
    uploaded = [name for name, _ in environment.drive.uploads if "/tasks/" in name]
    assert any(name.endswith("/audio.source.flac") for name in uploaded)
    assert not any(name.endswith(("/audio.wav", "/original.flac")) for name in uploaded)
    assert (
        audio.report_preparation(context)["validation_scope"]
        == "captured_receipt_and_frozen_original_pcm"
    )


def test_pcm_disagreement_never_reaches_inference_or_result_commit(
    environment, tmp_path, monkeypatch
):
    job, _ = prepare(environment, tmp_path, monkeypatch, corrupt_pcm=True)
    batch = fixtures.run_batch(environment)
    assert environment.drive.asr_calls == 0 and len(environment.drive.submissions) == 1
    assert not environment.store.all(
        "SELECT id FROM transcript_results WHERE job_id=?", (job["id"],)
    )
    assert "remote_audio_preparation" not in environment.store.job(job["id"])["params"]
    assert batch["state"] != "completed"


def test_decoder_readiness_is_verified_before_private_payload_upload(
    environment, tmp_path, monkeypatch
):
    job, _ = prepare(environment, tmp_path, monkeypatch, decoder_ready=False)
    batch = fixtures.run_batch(environment)
    assert environment.drive.asr_calls == 0
    assert not any(
        "/tasks/" in name and name.endswith(("request.json", "audio.source.flac", audio.DESCRIPTOR))
        for name, _ in environment.drive.uploads
    )
    assert not environment.store.all(
        "SELECT id FROM transcript_results WHERE job_id=?", (job["id"],)
    )
    assert batch["state"] != "completed"
