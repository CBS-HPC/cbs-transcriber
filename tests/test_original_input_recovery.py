"""Accepted original-input checkpoints finalize without reopening inference sources."""

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cbs_transcriber import audio_inputs, enhancement, media, providers
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def prepared_job(tmp_path, wav_bytes, *, parts=1):
    store = Store(tmp_path)
    project = store.create_project("Synthetic cached input")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / "original.wav"
    original.write_bytes(wav_bytes)
    snapshot = audio_inputs.validate(
        {
            "policy": "original-v1",
            "recording_id": recording["id"],
            "format": "wav",
            "sha256": hashlib.sha256(wav_bytes).hexdigest(),
            "bytes": len(wav_bytes),
            "audio_stream": "0:a:0",
            "audio_stream_count": 1,
            "timebase": "first-audio-zero-v1",
            "recipe": "mono-16000-pcm16-v1",
            "decoder": {"version": "Synthetic pinned decoder", "sha256": "a" * 64},
            "audio_frames": 48000,
            "duration": 3,
            "duration_basis": "decoded_pcm_frames",
            "container_duration_observation": 3,
            "pcm_sha256": hashlib.sha256(b"\0\0" * 48000).hexdigest(),
            "source_origin": {
                "basis": "decoded_frame",
                "first_pts": 0,
                "normalized_first_pts": 0,
                "has_video": False,
            },
        }
    )
    settings = providers.Settings(
        provider_type="custom", base_url="http://127.0.0.1:9000/v1", model="synthetic"
    )
    job = store.add_job(
        recording["id"],
        "transcribe",
        {
            "settings": settings.model_dump(),
            "audio_input": snapshot,
            "audio_transport": "mp3",
        },
    )
    folder = directory / "chunks" / job["id"]
    folder.mkdir(parents=True)
    plan = {
        "schema": 1,
        "policy": "original-v1",
        "source": snapshot,
        "transport": "mp3",
        "chunks": [],
        "parts": [],
    }
    accepted = []
    for index in range(parts):
        start, end = index * 48000 // parts, (index + 1) * 48000 // parts
        name = f"{index:04d}.mp3"
        raw = f"Synthetic already validated part {index}".encode()
        (folder / name).write_bytes(raw)
        plan["chunks"].append(
            {"file": name, "offset": start / 16000, "duration": (end - start) / 16000}
        )
        plan["parts"].append(
            {
                "schema": 1,
                "policy": "original-v1",
                "source": snapshot,
                "window": {
                    "start_frame": start,
                    "end_frame": end,
                    "timebase": "first-audio-zero-v1",
                },
                "prepared": {
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "bytes": len(raw),
                    "format": "mp3",
                    "frames": end - start,
                    "pcm_sha256": "b" * 64,
                    "sample_rate": 16000,
                    "channels": 1,
                    "sample_width": 2,
                    "sample_basis": "mono-16000-pcm16-v1",
                },
            }
        )
        accepted.append(
            {
                "index": index,
                "offset": start / 16000,
                "segments": normalize_segments(
                    [{"text": "Accepted wording.", "start": 0, "end": (end - start) / 16000}]
                ),
            }
        )
    plan = audio_inputs.validate_evidence(plan, snapshot)
    (folder / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    params = {**job["params"], "audio_preparations": [plan]}
    store.update_job(job["id"], state="running", params=params, chunks=accepted)
    credentials = SimpleNamespace(get=Mock(return_value="synthetic-not-a-key"))
    queue = JobQueue(store, credentials, kind="transcribe")
    return SimpleNamespace(
        store=store,
        queue=queue,
        job=store.job(job["id"]),
        recording=store.recording(recording["id"]),
        original=original,
        snapshot=snapshot,
        plan=plan,
        folder=folder,
    )


def test_completed_api_parts_recover_failed_save_without_original_or_new_inference(
    tmp_path, wav_bytes, monkeypatch
):
    env = prepared_job(tmp_path, wav_bytes)
    provider = Mock(side_effect=AssertionError("No new provider request is permitted."))
    monkeypatch.setattr(providers, "transcribe_file", provider)
    monkeypatch.setattr(audio_inputs, "_run", Mock(side_effect=AssertionError("No decode.")))
    commit = env.store.add_result
    monkeypatch.setattr(
        env.store, "add_result", Mock(side_effect=ValueError("Synthetic local commit failure"))
    )
    with pytest.raises(ValueError, match="Synthetic local commit failure"):
        env.queue.process(env.job["id"])
    assert not env.store.results(env.recording["id"])
    env.original.unlink()
    frozen = copy.deepcopy(env.store.job(env.job["id"])["params"])
    env.queue.credentials.get.reset_mock()
    monkeypatch.setattr(env.store, "add_result", commit)
    no_source = Mock(side_effect=AssertionError("Accepted outcomes must not reopen their source."))
    monkeypatch.setattr(audio_inputs, "_verify", no_source)
    monkeypatch.setattr(media, "prepare_chunks", no_source)
    monkeypatch.setattr(providers, "require_approval", no_source)

    env.queue.process(env.job["id"])

    assert len(env.store.results(env.recording["id"])) == 1
    assert env.store.job(env.job["id"])["params"] == frozen
    assert env.store.job(env.job["id"])["chunks"] == env.job["chunks"]
    assert env.store.recording(env.recording["id"])["duration"] == 3
    assert not provider.called and not no_source.called and not env.queue.credentials.get.called


@pytest.mark.parametrize("source_change", ["missing", "changed"])
def test_partial_api_parts_still_refuse_source_drift_before_upload(
    tmp_path, wav_bytes, monkeypatch, source_change
):
    env = prepared_job(tmp_path, wav_bytes, parts=2)
    env.store.update_job(env.job["id"], chunks=env.job["chunks"][:1])
    frozen = copy.deepcopy(env.store.job(env.job["id"])["params"])
    if source_change == "missing":
        env.original.unlink()
    else:
        env.original.write_bytes(wav_bytes[:-1] + bytes([wav_bytes[-1] ^ 1]))
    provider = Mock(side_effect=AssertionError("Missing source must prevent every upload."))
    monkeypatch.setattr(providers, "transcribe_file", provider)
    monkeypatch.setattr(audio_inputs, "_run", Mock(side_effect=AssertionError("No decode.")))

    with pytest.raises(ValueError, match="original|owned regular"):
        env.queue.process(env.job["id"])

    assert not provider.called
    assert env.store.job(env.job["id"])["params"] == frozen
    assert not env.store.results(env.recording["id"])


def test_duplicate_completed_part_indices_cannot_bypass_pending_source_validation(
    tmp_path, wav_bytes, monkeypatch
):
    env = prepared_job(tmp_path, wav_bytes, parts=2)
    env.store.update_job(env.job["id"], chunks=[env.job["chunks"][0]] * 2)
    env.original.unlink()
    provider = Mock(side_effect=AssertionError("Ambiguous checkpoints cannot start requests."))
    monkeypatch.setattr(providers, "transcribe_file", provider)

    with pytest.raises(ValueError, match="frozen preparation scope"):
        env.queue.process(env.job["id"])

    assert not provider.called
    assert not env.store.results(env.recording["id"])


def test_completed_whole_enhancement_recovers_without_audio_or_runtime(
    tmp_path, wav_bytes, monkeypatch
):
    env = prepared_job(tmp_path, wav_bytes)
    env.store.update_job(env.job["id"], state="cancelled")
    source = env.store.add_result(
        env.recording["id"],
        normalize_segments([{"text": "Synthetic source.", "start": 0, "end": 3}]),
        {},
        "Synthetic source",
        {},
    )
    current = env.store.recording(env.recording["id"])
    options = enhancement.EnhancementOptions(execution="local", device="cpu", diarize=False)
    job = env.store.enqueue_enhancement(
        current["id"],
        source["id"],
        current["revision"],
        {"enhancement": options.model_dump(), "audio_input": env.snapshot},
    )
    env.store.update_job(job["id"], state="running")
    folder = env.original.parent / "enhancements" / job["id"]
    folder.mkdir(parents=True)
    enhancement.write_json(
        folder / "alignment.json",
        {
            "passages": [
                {
                    "id": "0",
                    "words": [
                        {"word": "Synthetic", "start": 0, "end": 1, "score": 1},
                        {"word": "source.", "start": 1, "end": 2, "score": 1},
                    ],
                }
            ]
        },
    )
    env.original.unlink()
    frozen = copy.deepcopy(env.store.job(job["id"])["params"])
    no_inference = Mock(side_effect=AssertionError("Completed stages must finalize locally."))
    monkeypatch.setattr(audio_inputs, "prepare_wav", no_inference)
    monkeypatch.setattr(audio_inputs, "_verify", no_inference)
    monkeypatch.setattr(enhancement, "require_local_runtime", no_inference)
    monkeypatch.setattr(enhancement, "run_worker", no_inference)
    monkeypatch.setattr(media, "ffmpeg", no_inference)
    env.queue.credentials.get.reset_mock()

    env.queue.process(job["id"])

    assert len(env.store.results(env.recording["id"])) == 2
    assert env.store.job(job["id"])["params"] == frozen
    assert not no_inference.called and not env.queue.credentials.get.called
