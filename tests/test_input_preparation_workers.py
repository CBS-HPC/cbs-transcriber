"""Bounded independent source/admission lanes and lease-safe shutdown (V142)."""

import threading

import pytest
from test_input_admission import inspect, item
from test_input_admission import library as library

from cbs_transcriber import (
    enhancement,
    input_admission,
    local_concurrency,
    original_inspection,
    providers,
)
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def test_v142_short_original_saves_while_long_inspection_blocks_and_shutdown_drains(
    library, credentials, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    long_started, release_long, saved, stopped = (threading.Event() for _ in range(4))
    original_freeze, original_add = original_inspection.audio_inputs.freeze, store.add_result
    for row in rows:
        directory = store.directory(row["id"])
        directory.mkdir(parents=True)
        (directory / "original.wav").write_bytes(wav_bytes)

    def held_freeze(owner, recording, *args, **kwargs):
        if recording["id"] == rows[0]["id"]:
            long_started.set()
            assert release_long.wait(30)
        return original_freeze(owner, recording, *args, **kwargs)

    def accepted_result(recording_id, *args, **kwargs):
        result = original_add(recording_id, *args, **kwargs)
        if recording_id == rows[1]["id"]:
            saved.set()
        return result

    monkeypatch.setattr(original_inspection.audio_inputs, "freeze", held_freeze)
    monkeypatch.setattr(store, "add_result", accepted_result)
    monkeypatch.setattr(
        providers,
        "transcribe_file",
        lambda *a: {"segments": [{"start": 0, "end": 3, "text": "Synthetic short result."}]},
    )
    with store.library_lock():
        accepted = input_admission.accept(store, [item(provider, row) for row in rows])
        queue = JobQueue(store, credentials)
        sources = [worker for worker in queue.workers if worker.kind == "source-inspect"]
        admissions = [worker for worker in queue.workers if worker.kind == "input-admission"]
        # Each lane starts its maximum threads; the saved limit gates new claims (ADR 0146).
        assert len(sources) == local_concurrency.LANES["inspection"][1]
        assert len(admissions) == local_concurrency.LANES["admission"][1]
        assert queue.lane_limits["source-inspect"] >= 2
        assert queue.lane_limits["input-admission"] == 2
        sources[0]._start()
        assert long_started.wait(5)
        for worker in [sources[1], *admissions, queue]:
            worker._start()
        stopper = None
        try:
            assert saved.wait(20), "A held original blocked another file's inspection/ASR"
            assert store.job(accepted[1]["id"])["state"] == "completed"
            assert original_inspection.read(store, rows[0]["id"])["state"] == "running"
            stopper = threading.Thread(target=lambda: (queue.stop(), stopped.set()))
            stopper.start()
            assert not stopped.wait(0.05)
            with pytest.raises(RuntimeError, match="already open"):
                with Store(store.root).library_lock():
                    pass
        finally:
            release_long.set()
            if stopper is None:
                queue.stop()
            else:
                stopper.join(10)
                assert not stopper.is_alive()
        assert all(
            worker.thread is None or not worker.thread.is_alive() for worker in queue.workers
        )


def test_v142_slow_optional_runtime_preflight_does_not_block_other_api_admission(
    library, credentials, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    source = store.add_result(
        rows[0]["id"],
        normalize_segments([{"start": 0, "end": 3, "text": "Synthetic reviewed source."}]),
        {},
        "Reviewed",
        {},
    )
    current = store.recording(rows[0]["id"])
    started, release, prepared = (threading.Event() for _ in range(3))
    original_materialize = input_admission.materialize

    def runtime(options):
        started.set()
        assert release.wait(30)

    def observed(owner, token, outcomes):
        result = original_materialize(owner, token, outcomes)
        if any(job["recording_id"] == rows[1]["id"] for job in result):
            prepared.set()
        return result

    monkeypatch.setattr(enhancement, "require_local_runtime", runtime)
    monkeypatch.setattr(input_admission, "materialize", observed)
    with store.library_lock():
        choices = [
            {
                "recording_id": rows[0]["id"],
                "choice": input_admission.choice(
                    options={"execution": "local"},
                    source={"result_id": source["id"], "revision": current["revision"]},
                ),
            },
            item(provider, rows[1]),
        ]
        accepted = input_admission.accept(store, choices)
        for row in rows:
            inspect(store, row, wav_bytes)
        queue = JobQueue(store, credentials)
        workers = [worker for worker in queue.workers if worker.kind == "input-admission"]
        workers[0]._start()
        assert started.wait(5)
        workers[1]._start()
        try:
            assert prepared.wait(10), "Optional model/runtime preflight blocked unrelated API work"
            assert "input_acceptance" in store.job(accepted[1]["id"])["params"]
            assert (
                store.job(accepted[0]["id"])["params"]["input_intent"]["phase"] == "materializing"
            )
        finally:
            release.set()
            queue.stop()


def test_v142_unexpected_decoder_failure_settles_owned_job_and_lane_keeps_working(
    library, credentials, wav_bytes, monkeypatch
):
    store, _, provider, rows = library
    finished = threading.Event()
    original = original_inspection.audio_inputs.freeze
    completed = original_inspection.run
    for row in rows:
        directory = store.directory(row["id"])
        directory.mkdir(parents=True)
        (directory / "original.wav").write_bytes(wav_bytes)

    def broken(owner, recording, *args, **kwargs):
        if recording["id"] == rows[0]["id"]:
            raise RuntimeError("Synthetic decoder metadata failure")
        return original(owner, recording, *args, **kwargs)

    def observed(*args, **kwargs):
        result = completed(*args, **kwargs)
        if result and result["state"] == "ready":
            finished.set()
        return result

    monkeypatch.setattr(original_inspection.audio_inputs, "freeze", broken)
    monkeypatch.setattr(original_inspection, "run", observed)
    with store.library_lock():
        input_admission.accept(store, [item(provider, row) for row in rows])
        worker = JobQueue(store, credentials, kind="source-inspect")
        worker._start()
        try:
            assert finished.wait(10)
        finally:
            worker.stop()
        first = original_inspection.read(store, rows[0]["id"])
        assert first["state"] == "error" and first["error"]["code"] == "decode_failed"
        assert first["anchor"] is None
