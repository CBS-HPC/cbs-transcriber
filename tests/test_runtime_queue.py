"""Queue/worker seams retain batch ownership without invoking an inference runtime."""

import threading
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement, enhancement_api, jobs, restored_owners


def test_batch_request_cannot_fall_through_to_singleton_submission(monkeypatch):
    # The restored-owner fence is covered separately; these stand-ins have no library.
    monkeypatch.setattr(restored_owners, "require_job", lambda *_: None)
    monkeypatch.setattr(
        enhancement_api.Run, "execute", lambda _: pytest.fail("singleton allocation")
    )
    job = {"id": "b" * 32, "params": {"runtime_batch_id": "a" * 32}}
    with pytest.raises(ValueError, match="shared batch coordinator"):
        enhancement_api.run_api(SimpleNamespace(store=None), job, {}, None, None, None)
    calls = []
    worker = SimpleNamespace(store=None, runtime_dispatch=lambda *args: calls.append(args))
    enhancement_api.run_api(worker, job, {}, None, None, None)
    assert len(calls) == 1 and calls[0][0] is job


def test_complete_batch_checkpoints_still_validate_the_task_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(restored_owners, "require_job", lambda *_: None)
    folder = tmp_path / "enhancement"
    folder.mkdir()
    (folder / "alignment.json").write_text('{"passages": []}', encoding="utf-8")
    options = enhancement.EnhancementOptions(execution="ucloud", device="cuda")
    job = {"id": "b" * 32, "params": {"runtime_batch_id": "a" * 32, "ucloud_api": {}}}
    calls = []
    queue = SimpleNamespace(
        cancel_event=threading.Event(),
        store=SimpleNamespace(update_job=lambda *args, **kwargs: None),
        runtime_dispatch=lambda *args: calls.append(args),
    )
    monkeypatch.setattr(enhancement.media, "ffmpeg", lambda *_: pytest.fail("unneeded decoding"))
    monkeypatch.setattr(enhancement_api, "settle", lambda *_: pytest.fail("singleton cleanup"))
    expected = ([{"text": "Reviewed."}], {})
    result = enhancement.enhance_window(
        queue, job, {"duration": 3}, options, None, folder, merged=lambda: expected
    )
    assert result is expected
    assert len(calls) == 1
    assert not (folder / "audio.wav").exists()


def test_runtime_lanes_share_a_preparation_limit_and_keep_local_lane():
    queue = jobs.JobQueue(None, None)
    assert sum(worker.kind == "runtime-batch" for worker in queue.workers) == 4
    assert sum(worker.kind == "enhance" for worker in queue.workers) == 1
    gate = queue.runtime_preparation_gate
    assert all(worker.runtime_preparation_gate is gate for worker in queue.workers)
    assert all(
        worker.runtime_transfer_gate is queue.runtime_transfer_gate for worker in queue.workers
    )
    assert queue.runtime_transfer_gate is not gate
    assert gate.acquire(blocking=False)
    assert gate.acquire(blocking=False)
    assert not gate.acquire(blocking=False)
    gate.release()
    gate.release()


def test_singleton_checkpoint_preserves_out_of_band_capacity_receipt(client):
    store = client.app.state.store
    project = store.create_project("Synthetic capacity")
    recording = store.create_recording(project["id"], "synthetic.wav")
    job = store.add_job(recording["id"], "enhance", {"ucloud_api": {}})
    run = object.__new__(enhancement_api.Run)
    run.store, run.identifier, run.job = store, job["id"], job
    run.run = {}
    fresh = store.job(job["id"])
    fresh["params"]["ucloud_run"] = {"capacity_reserved": True}
    store.update_job(job["id"], params=fresh["params"])
    run.checkpoint(submission_intent=True)
    assert store.job(job["id"])["params"]["ucloud_run"] == {
        "capacity_reserved": True,
        "submission_intent": True,
    }


def test_failed_stale_interruption_does_not_end_the_runtime_lane(monkeypatch):
    from cbs_transcriber import runtime_batches, runtime_store

    worker = jobs.JobQueue(None, None, kind="runtime-batch")
    pending = iter([{"id": "a" * 32, "epoch": 1}, {"id": "b" * 32, "epoch": 2}])
    monkeypatch.setattr(runtime_store, "claim", lambda _: next(pending))
    processed = []

    def execute(current, batch):
        processed.append(batch["id"])
        if len(processed) == 1:
            raise OSError("synthetic local failure")
        current.stopping.set()

    def stale(*args):
        raise ValueError("new epoch already owns this allocation")

    monkeypatch.setattr(runtime_batches, "run", execute)
    monkeypatch.setattr(runtime_store, "interrupt", stale)
    worker.run_runtime_batches()
    assert processed == ["a" * 32, "b" * 32]
    assert worker.active_batch_id is None


def test_waiting_for_another_batch_preparation_slot_is_cancellable():
    gate = threading.BoundedSemaphore(1)
    gate.acquire()
    cancel, waiting = threading.Event(), threading.Event()
    errors = []

    def guard():
        waiting.set()
        if cancel.is_set():
            raise enhancement.media.Cancelled()

    def prepare():
        try:
            with jobs.preparation_slot(gate, guard):
                pytest.fail("Another batch still owns the preparation slot")
        except enhancement.media.Cancelled:
            errors.append("cancelled")

    thread = threading.Thread(target=prepare)
    thread.start()
    try:
        assert waiting.wait(2)
        cancel.set()
        thread.join(2)
        assert not thread.is_alive()
        assert errors == ["cancelled"]
        assert not gate.acquire(blocking=False)
    finally:
        cancel.set()
        gate.release()
        thread.join(2)
