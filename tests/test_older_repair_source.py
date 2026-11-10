"""Older selected repairs prove only requested original clips before mocked remote execution."""

import copy
import struct
import subprocess
import threading
import wave
from types import SimpleNamespace

import pytest
import test_repair_bundles as fixtures

from cbs_transcriber import (
    audio_inputs,
    ffmpeg_runtime,
    repair_admission,
    repair_bundles,
    repair_cloud,
)
from cbs_transcriber.enhancement import EnhancementOptions
from cbs_transcriber.media import Cancelled


@pytest.fixture
def case(tmp_path):
    store, recording, result, pcm, _snapshot, runtime = fixtures.study.__wrapped__(tmp_path)
    recording = store.recording(recording["id"])
    fixtures.write_wav(store.directory(recording["id"]) / "original.wav", pcm)
    units = [{"ids": ["1"], "start": 2, "end": 5}, {"ids": ["4"], "start": 9, "end": 12}]
    params = {
        "enhancement": EnhancementOptions(
            execution="ucloud", device="cuda", timing_policy="retain-estimates"
        ).model_dump(),
        "realign": True,
        "passage_timing": True,
        "segment_ids": ["1", "4"],
        "timing_units": units,
        "repair_source": repair_admission.choice(recording, units),
        "repair_runtime": runtime,
    }
    job = store.enqueue_enhancement(
        recording["id"], result["id"], recording["revision"], params, selected_ids=["1", "4"]
    )
    store.update_job(job["id"], state="running")
    worker = SimpleNamespace(store=store, cancel_event=threading.Event())
    return worker, store.job(job["id"]), recording


def test_bounded_proof_and_bundle_never_full_decode_or_mutate_original_cache(case, monkeypatch):
    worker, job, recording = case
    calls = []
    run = audio_inputs._run

    def observed(args, *a, **kw):
        assert not kw.get("pcm"), "older repair must not decode the complete original"
        calls.append(args)
        return run(args, *a, **kw)

    monkeypatch.setattr(audio_inputs, "_run", observed)
    job = repair_admission.prepare(worker, job, recording)
    snapshot = repair_admission.require_snapshot(job["params"])
    assert snapshot["audio_frames"] is None and snapshot["pcm_sha256"] is None
    assert len(calls) == 1 and "-frames:a" in calls[0] and "copy" in calls[0]
    assert (
        worker.store.one("SELECT original_input FROM recordings WHERE id=?", (recording["id"],))[
            "original_input"
        ]
        is None
    )
    plan = repair_bundles.preparation_plan(worker.store, job["id"])
    monkeypatch.setattr(audio_inputs, "_run", run)
    folders = []
    for index, unit in enumerate(plan["units"]):
        folder = repair_cloud.folder_for(worker.store, job) / "clips" / str(index)
        receipt = audio_inputs.prepare_wav(
            repair_bundles._filesystem_path(worker.store.directory(recording["id"])),
            recording,
            snapshot,
            folder,
            offset=unit["start"],
            duration=unit["end"] - unit["start"],
            cancel=worker.cancel_event,
            bounded_timeline=True,
        )
        worker.store.record_audio_preparation(job["id"], receipt)
        folders.append(folder)
    bundle = repair_bundles.build_bundle(
        worker.store, job["id"], folders, job["params"]["repair_runtime"]
    )
    assert bundle["manifest"]["transfer_frames"] == 6 * 16000
    assert bundle["manifest"]["original_audio"]["audio_frames"] is None
    assert recording["duration"] == worker.store.recording(recording["id"])["duration"] == 20
    monkeypatch.setattr(audio_inputs, "freeze", lambda *_a, **_kw: pytest.fail("proof is frozen"))
    assert (
        repair_admission.prepare(worker, worker.store.job(job["id"]), recording)["params"][
            "audio_input"
        ]
        == snapshot
    )


def test_failed_probe_retains_anchor_and_refuses_changed_bytes_on_retry(case, monkeypatch):
    worker, job, recording = case
    monkeypatch.setattr(
        audio_inputs, "_run", lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("bad timeline"))
    )
    with pytest.raises(ValueError, match="bad timeline"):
        repair_admission.prepare(worker, job, recording)
    saved = worker.store.job(job["id"])
    anchor = copy.deepcopy(saved["params"]["repair_source"]["anchor"])
    assert anchor and "audio_input" not in saved["params"] and "ucloud_run" not in saved["params"]
    (worker.store.directory(recording["id"]) / "original.wav").write_bytes(b"changed")
    with pytest.raises(ValueError, match="byte anchor"):
        repair_admission.prepare(worker, saved, recording)
    assert worker.store.job(job["id"])["params"]["repair_source"]["anchor"] == anchor


def test_stop_after_hash_preserves_anchor_without_prepared_authority(case, monkeypatch):
    worker, job, recording = case
    run = audio_inputs._run

    def stop(*args, **kwargs):
        worker.cancel_event.set()
        return run(*args, **kwargs)

    monkeypatch.setattr(audio_inputs, "_run", stop)
    with pytest.raises(Cancelled):
        repair_admission.prepare(worker, job, recording)
    saved = worker.store.job(job["id"])
    assert saved["params"]["repair_source"]["anchor"] and "audio_input" not in saved["params"]


def test_completed_hash_is_anchored_when_cancel_arrives_before_observer(case, monkeypatch):
    worker, job, recording = case
    original_hash = audio_inputs._hash

    def completed(*args, **kwargs):
        identity = original_hash(*args, **kwargs)
        worker.store.update_job(job["id"], state="cancelled")
        worker.cancel_event.set()
        return identity

    monkeypatch.setattr(audio_inputs, "_hash", completed)
    monkeypatch.setattr(
        audio_inputs.subprocess,
        "Popen",
        lambda *_a, **_kw: pytest.fail("Stop after hashing must not launch decoder"),
    )
    with pytest.raises(Cancelled):
        repair_admission.prepare(worker, job, recording)
    saved = worker.store.job(job["id"])
    assert saved["state"] == "cancelled"
    assert saved["params"]["repair_source"]["anchor"] is not None
    assert "audio_input" not in saved["params"]


def test_stale_source_worker_cannot_publish_after_retry_epoch_cycles_to_running(case, monkeypatch):
    worker, job, recording = case
    original_hash = audio_inputs._hash

    def retried(*args, **kwargs):
        identity = original_hash(*args, **kwargs)
        saved = worker.store.job(job["id"])
        saved["params"]["repair_source"]["epoch"] += 1
        worker.store.update_job(job["id"], params=saved["params"], state="running")
        return identity

    monkeypatch.setattr(audio_inputs, "_hash", retried)
    with pytest.raises(ValueError, match="changed while"):
        repair_admission.prepare(worker, job, recording)
    saved = worker.store.job(job["id"])
    assert saved["params"]["repair_source"]["epoch"] == 2
    assert saved["params"]["repair_source"]["anchor"] is None
    assert "audio_input" not in saved["params"]


def test_scoped_receipt_cannot_broaden_selection_or_enter_full_source_cache(case):
    worker, job, recording = case
    job = repair_admission.prepare(worker, job, recording)
    params = copy.deepcopy(job["params"])
    params["timing_units"][0]["start"] = 0
    with pytest.raises(ValueError, match="accepted selection"):
        repair_admission.require_snapshot(params)
    params = copy.deepcopy(job["params"])
    params["repair_source"]["anchor"]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="accepted selection"):
        repair_admission.require_snapshot(params)


def test_actual_selected_timeline_preserves_absolute_seek_and_rejects_gap(tmp_path, monkeypatch):
    from cbs_transcriber.store import Store

    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "gap.mkv")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    pcm = b"".join(struct.pack("<h", ((index * 37) % 65536) - 32768) for index in range(4 * 16000))
    raw = tmp_path / "source.wav"
    fixtures.write_wav(raw, pcm)
    subprocess.run(
        [
            ffmpeg_runtime.resolve(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(raw),
            "-af",
            "asetpts=PTS+gte(T\\,2)/TB",
            "-c:a",
            "pcm_s16le",
            str(directory / "original.mkv"),
        ],
        check=True,
        timeout=30,
    )
    store.execute("UPDATE recordings SET status='ready',duration=5 WHERE id=?", (recording["id"],))
    recording = store.recording(recording["id"])
    snapshot = audio_inputs.freeze(store, recording, scoped=True)
    assert snapshot["audio_frames"] is None and snapshot["source_origin"]["first_pts"] == 0
    run, observed = audio_inputs._run, []

    def inspect(*args, **kwargs):
        value = run(*args, **kwargs)
        if kwargs.get("timeline"):
            first = audio_inputs._FRAME_SAMPLES.search(
                next(
                    line
                    for line in value[0].splitlines()
                    if audio_inputs._FRAME_SAMPLES.search(line)
                )
            )
            observed.append(int(first[2]))
        return value

    monkeypatch.setattr(audio_inputs, "_run", inspect)
    for index, (offset, source_offset) in enumerate(((0.5, 0.5), (3.25, 2.25))):
        folder = directory / "clips" / str(index)
        receipt = audio_inputs.prepare_wav(
            directory,
            recording,
            snapshot,
            folder,
            offset=offset,
            duration=0.5,
            cancel=threading.Event(),
            bounded_timeline=True,
        )
        assert receipt["timeline"] == {
            "policy": "selected-pcm-contiguous-v1",
            "start_frame": round(offset * 16000),
            "end_frame": round((offset + 0.5) * 16000),
            "frames": 8000,
        }
        with wave.open(str(folder / "audio.wav"), "rb") as source:
            assert (
                source.readframes(source.getnframes())
                == pcm[round(source_offset * 16000) * 2 : round((source_offset + 0.5) * 16000) * 2]
            )
    assert observed == [8000, 52000], (
        "the inspector must see original absolute PTS, before clip-zero rewrite"
    )
    folder = directory / "clips" / "gap"
    with pytest.raises(ValueError, match="timeline"):
        audio_inputs.prepare_wav(
            directory,
            recording,
            snapshot,
            folder,
            offset=1.5,
            duration=2,
            cancel=threading.Event(),
            bounded_timeline=True,
        )
    assert not (folder / "audio.input.json").exists() and not (folder / "audio.wav").exists()
