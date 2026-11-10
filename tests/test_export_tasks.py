"""Synthetic background export lifetime, frozen snapshots and bounded download tests."""

import asyncio
import io
import json
import random
import threading
from contextlib import contextmanager
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_export_originals import originals as originals

from cbs_transcriber import export_tasks, exports
from cbs_transcriber.export_tasks import Conflict, ExportTasks


@pytest.fixture
def manager(client):
    value = ExportTasks(client.app.state.store, acquire_file_lease=True)
    yield value
    value.close()


def identifier(task):
    return task["export_task"]["id"]


def finish(manager, task):
    manager._tasks[identifier(task)].thread.join(timeout=10)
    assert not manager._tasks[identifier(task)].thread.is_alive()
    return next(row for row in manager.activity() if row["id"] == task["id"])


def selection(rows, **changes):
    return {"recording_ids": [row["id"] for row in rows], "formats": ["json"], **changes}


def download(manager, task):
    delivery = manager.open_download(identifier(task))
    chunks = list(delivery.chunks())
    assert all(0 < len(chunk) <= 64 * 1024 for chunk in chunks)
    return b"".join(chunks)


def test_snapshot_and_selection_freeze_before_acknowledgement_while_edits_remain_usable(
    client, manager, originals, monkeypatch
):
    store, project, rows, sources = originals
    entered, release = threading.Event(), threading.Event()
    original = exports._stage_original

    def stalled(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(exports, "_stage_original", stalled)
    choices = selection(rows[:1], include_originals=True)
    task = manager.start(project["id"], **choices)
    assert entered.wait(2)
    assert task["state"] == "running" and not task["export_task"]["ready"]
    choices["recording_ids"].append(rows[1]["id"])
    choices["formats"].append("csv")
    current = store.recording(rows[0]["id"])
    updated = current["segments"]
    updated[0]["text"] = "A later manual edit is not in this frozen export."
    store.save_transcript(rows[0]["id"], updated, current["speakers"], current["revision"])
    assert client.get("/api/projects").status_code == 200
    with pytest.raises(Conflict, match="already being prepared"):
        manager.start(project["id"], **selection(rows[:1]))
    with pytest.raises(Conflict):
        manager.require_idle(recording_ids=[rows[0]["id"]])
    manager.require_idle(project_id="unrelated-project")
    with pytest.raises(Conflict):
        with manager.exclusive():
            pass
    release.set()
    ready = finish(manager, task)
    assert ready["state"] == "completed" and ready["export_task"]["ready"]
    with ZipFile(io.BytesIO(download(manager, task))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["recording_ids"] == [rows[0]["id"]]
        assert manifest["formats"] == ["json"]
        entry = next(row for row in manifest["entries"] if row["format"] == "json")
        assert b"Reviewed synthetic words" in archive.read(entry["path"])
        assert b"later manual edit" not in archive.read(entry["path"])
        media = next(row for row in manifest["entries"] if row["format"] == "original")
        assert archive.read(media["path"]) == sources[rows[0]["id"]][1]
    manager.require_idle()
    assert not store.all("SELECT id FROM jobs")


def test_recording_export_uses_exact_requested_result_and_supports_original_only(
    manager, originals
):
    store, _, rows, sources = originals
    result_id = rows[0]["active_transcript_id"]
    task = manager.start_recording(rows[0]["id"], result_id=result_id, formats=["json", "text"])
    assert finish(manager, task)["state"] == "completed"
    with ZipFile(io.BytesIO(download(manager, task))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert {entry["result_id"] for entry in manifest["entries"]} == {result_id}
    original = manager.start_recording(
        rows[1]["id"], result_id=None, formats=[], include_originals=True
    )
    assert finish(manager, original)["state"] == "completed"
    with ZipFile(io.BytesIO(download(manager, original))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["formats"] == []
        assert archive.read(manifest["entries"][0]["path"]) == sources[rows[1]["id"]][1]
    assert not store.all("SELECT id FROM jobs")


def test_cancellation_during_bounded_source_copy_closes_and_removes_partial_zip(
    manager, originals, monkeypatch
):
    _, project, rows, sources = originals
    source = sources[rows[0]["id"]][0]
    data = b"synthetic original bytes\0" * 200_000
    source.write_bytes(data)
    entered, release = threading.Event(), threading.Event()
    progress = export_tasks._Build.progress

    def pause_first_read(self, stage, *args):
        progress(self, stage, *args)
        if stage == "reading" and not entered.is_set():
            entered.set()
            assert release.wait(10)

    monkeypatch.setattr(export_tasks._Build, "progress", pause_first_read)
    task = manager.start(project["id"], **selection(rows[:1], include_originals=True))
    assert entered.wait(2)
    partial = manager._tasks[identifier(task)].path
    manager.cancel(identifier(task))
    release.set()
    stopped = finish(manager, task)
    assert stopped["state"] == "cancelled" and not stopped["export_task"]["ready"]
    assert not partial.exists() and source.read_bytes() == data
    with pytest.raises(Conflict, match="not ready"):
        manager.open_download(identifier(task))


def test_ready_downloads_are_independent_streams_reserved_against_discard(manager, originals):
    _, project, rows, sources = originals
    sources[rows[0]["id"]][0].write_bytes(random.Random(42).randbytes(2 * 1024 * 1024))
    task = manager.start(project["id"], **selection(rows[:1], include_originals=True))
    assert finish(manager, task)["export_task"]["ready"]
    first = manager.open_download(identifier(task))
    first_chunks = first.chunks()
    prefix = next(first_chunks)
    second = manager.open_download(identifier(task))
    with pytest.raises(Conflict, match="download"):
        manager.discard(identifier(task))
    second_bytes = b"".join(second.chunks())
    first_bytes = prefix + b"".join(first_chunks)
    assert first_bytes == second_bytes
    assert len(prefix) == 64 * 1024 and first.size == len(first_bytes)
    first.close()  # Response background cleanup is safely idempotent.
    second.close()
    assert manager.discard(identifier(task)) == {"ok": True}
    assert manager.activity() == []


def test_ready_capacity_never_silently_discards_previous_download(manager, originals):
    _, project, rows, _ = originals
    ready = []
    for _ in range(export_tasks.MAX_READY):
        task = manager.start(project["id"], **selection(rows[:1]))
        assert finish(manager, task)["state"] == "completed"
        ready.append(task)
    paths = [manager._tasks[identifier(task)].path for task in ready]
    with pytest.raises(Conflict, match="Discard"):
        manager.start(project["id"], **selection(rows[:1]))
    assert all(path.exists() for path in paths)
    manager.discard(identifier(ready[0]))
    assert not paths[0].exists() and all(path.exists() for path in paths[1:])
    assert (
        finish(manager, manager.start(project["id"], **selection(rows[:1])))["state"] == "completed"
    )


def test_exclusive_source_operations_and_switch_seal_fence_new_export_admission(manager, originals):
    _, project, rows, _ = originals
    with manager.exclusive():
        with pytest.raises(Conflict, match="library operation"):
            manager.start(project["id"], **selection(rows[:1]))
    assert (
        finish(manager, manager.start(project["id"], **selection(rows[:1])))["state"] == "completed"
    )
    manager.stop_admission()
    with pytest.raises(Conflict, match="library operation"):
        manager.start(project["id"], **selection(rows[:1]))


def test_shutdown_cancels_drains_before_releasing_lease_and_cleans_private_artifacts(
    manager, originals, monkeypatch
):
    _, project, rows, _ = originals
    entered, release = threading.Event(), threading.Event()
    progress = export_tasks._Build.progress

    def paused(self, stage, *args):
        progress(self, stage, *args)
        if not entered.is_set():
            entered.set()
            assert release.wait(10)

    monkeypatch.setattr(export_tasks._Build, "progress", paused)
    task = manager.start(project["id"], **selection(rows[:1], include_originals=True))
    assert entered.wait(2)
    root = manager._root
    closer = threading.Thread(target=manager.close)
    closer.start()
    assert manager._tasks[identifier(task)].cancel.wait(2)
    assert closer.is_alive()
    release.set()
    closer.join(timeout=10)
    assert not closer.is_alive() and not root.exists()
    assert manager.activity()[0]["state"] == "cancelled"


def test_shutdown_waits_to_unlink_an_open_download_until_its_descriptor_closes(manager, originals):
    _, project, rows, _ = originals
    task = manager.start(project["id"], **selection(rows[:1]))
    finish(manager, task)
    delivery = manager.open_download(identifier(task))
    root = manager._root
    manager.close()
    assert root.exists()
    assert b"".join(delivery.chunks()).startswith(b"PK")
    assert not root.exists()


def test_failures_are_curated_do_not_auto_restart_and_remove_partial_output(
    manager, originals, monkeypatch
):
    _, project, rows, _ = originals

    def fail(*args, **kwargs):
        raise OSError("PRIVATE_CONTENT C:/private/source.wav")

    monkeypatch.setattr(exports, "export_bytes", fail)
    task = manager.start(project["id"], **selection(rows[:1]))
    failed = finish(manager, task)
    assert failed["state"] == "failed" and not failed["export_task"]["ready"]
    assert "PRIVATE_CONTENT" not in json.dumps(failed)
    assert "source.wav" not in json.dumps(failed)
    assert not list(manager._root.iterdir())
    assert manager.activity() == [failed]


def test_no_timing_export_keeps_only_the_fixed_safe_no_results_diagnostic(manager, originals):
    from cbs_transcriber.transcripts import normalize_segments

    store, _, rows, _ = originals
    result = store.add_result(
        rows[0]["id"],
        normalize_segments([{"text": "Synthetic untimed wording."}]),
        {},
        "Untimed",
        {},
    )
    task = manager.start_recording(rows[0]["id"], result_id=result["id"], formats=["srt"])
    failed = finish(manager, task)
    assert failed["state"] == "failed"
    assert failed["message"] == exports.NO_RESULTS_MESSAGE
    assert not failed["export_task"]["ready"]
    assert failed["export_task"]["download_url"] is None
    assert not list(manager._root.iterdir())


def test_changed_ready_file_is_refused_before_any_download_bytes(manager, originals):
    _, project, rows, _ = originals
    task = manager.start(project["id"], **selection(rows[:1]))
    finish(manager, task)
    manager._tasks[identifier(task)].path.write_bytes(b"changed private file")
    with pytest.raises(Conflict, match="changed"):
        manager.open_download(identifier(task))
    with pytest.raises(KeyError):
        manager.open_download("../../outside")


def test_missing_ready_file_and_failed_discard_are_curated_and_retain_recovery_choices(
    manager, originals, monkeypatch
):
    _, project, rows, _ = originals
    task = manager.start(project["id"], **selection(rows[:1]))
    finish(manager, task)
    path = manager._tasks[identifier(task)].path
    unlink = Path.unlink

    def refuse_own_file(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("PRIVATE LOCAL PATH")
        return unlink(candidate, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", refuse_own_file)
        with pytest.raises(Conflict, match="could not be removed"):
            manager.discard(identifier(task))
        assert manager.activity()[0]["export_task"]["ready"] and path.exists()
    path.unlink()
    with pytest.raises(Conflict, match="unavailable"):
        manager.open_download(identifier(task))
    assert manager.discard(identifier(task)) == {"ok": True}


def test_no_worker_export_holds_the_normal_library_lease_until_copy_drains(
    manager, originals, monkeypatch
):
    from cbs_transcriber.store import Store

    store, project, rows, _ = originals
    second = Store(store.root)
    entered, release = threading.Event(), threading.Event()
    original = exports._stage_original

    def stalled(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(exports, "_stage_original", stalled)
    task = manager.start(project["id"], **selection(rows[:1], include_originals=True))
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="already open"):
            with second.library_lock():
                pass
    finally:
        release.set()
    assert finish(manager, task)["state"] == "completed"
    with second.library_lock():
        pass


def test_no_worker_export_does_not_borrow_another_threads_short_lived_lease(
    manager, originals, monkeypatch
):
    store, project, rows, _ = originals
    entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
    original = store.library_lock

    def transient():
        with original():
            entered.set()
            assert release.wait(10)

    holder = threading.Thread(target=transient)
    holder.start()
    assert entered.wait(2)

    @contextmanager
    def observed_lease():
        attempted.set()
        with original():
            yield

    monkeypatch.setattr(store, "library_lock", observed_lease)
    replies = []
    starter = threading.Thread(
        target=lambda: replies.append(manager.start(project["id"], **selection(rows[:1])))
    )
    starter.start()
    try:
        assert attempted.wait(2)
        assert starter.is_alive() and not list(manager._root.iterdir())
    finally:
        release.set()
        holder.join(timeout=5)
        starter.join(timeout=5)
    assert not holder.is_alive() and not starter.is_alive()
    assert finish(manager, replies[0])["state"] == "completed"


def test_ready_task_cannot_be_discarded_before_its_worker_has_released_the_lease(
    manager, originals, monkeypatch
):
    store, project, rows, _ = originals
    leaving, release = threading.Event(), threading.Event()
    original = store.library_lock

    @contextmanager
    def delayed_exit():
        with original():
            yield
            leaving.set()
            assert release.wait(10)

    monkeypatch.setattr(store, "library_lock", delayed_exit)
    task = manager.start(project["id"], **selection(rows[:1]))
    try:
        assert leaving.wait(2)
        current = manager.activity()[0]
        assert current["state"] == "completed" and not current["export_task"]["can_discard"]
        with pytest.raises(Conflict):
            manager.discard(identifier(task))
    finally:
        release.set()
    assert finish(manager, task)["export_task"]["can_discard"]


@pytest.mark.parametrize("failure", ["headers", "body", "cancel"])
def test_download_response_always_releases_descriptor_on_early_client_loss(
    manager, originals, failure
):
    from starlette.requests import ClientDisconnect

    _, project, rows, _ = originals
    task = manager.start(project["id"], **selection(rows[:1]))
    finish(manager, task)
    delivery = manager.open_download(identifier(task))
    response = export_tasks.DownloadResponse(delivery)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if failure == "cancel":
            raise asyncio.CancelledError()
        if message["type"] == (
            "http.response.start" if failure == "headers" else "http.response.body"
        ):
            raise OSError("Client disconnected")

    async def run():
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    asyncio.run(run())
    assert delivery.stream.closed
    assert manager._tasks[identifier(task)].readers == 0
    assert manager.discard(identifier(task)) == {"ok": True}
