"""V167: local staging drains admitted writes and rechecks waiting mutations."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import anyio
import httpx
import pytest

from cbs_transcriber import deletion_files
from cbs_transcriber.portable_capture import CaptureGate


def seed(store, name="Synthetic deletion admission"):
    project = store.create_project(name)
    recording = store.create_recording(project["id"], "synthetic.wav")
    folder = store.directory(recording["id"])
    folder.mkdir(parents=True)
    (folder / "original.wav").write_bytes(b"Synthetic original")
    return project, recording, folder


def test_v167_delete_drains_previously_admitted_metadata_write(client, monkeypatch):
    store, gate = client.app.state.store, client.app.state.portable_capture_gate
    project, recording, folder = seed(store)
    admitted, release, preparing = (threading.Event() for _ in range(3))
    save_notes, prepare = store.save_notes, deletion_files.prepare

    def paused_notes(*args, **kwargs):
        admitted.set()
        assert release.wait(10)
        return save_notes(*args, **kwargs)

    def observed_prepare(*args, **kwargs):
        preparing.set()
        return prepare(*args, **kwargs)

    monkeypatch.setattr(store, "save_notes", paused_notes)
    monkeypatch.setattr(deletion_files, "prepare", observed_prepare)
    with ThreadPoolExecutor(max_workers=2) as requests:
        writing = requests.submit(
            client.patch,
            f"/api/projects/{project['id']}/notes",
            json={"text": "Preserve this newly admitted note", "revision": 0},
        )
        assert admitted.wait(3)
        deleting = requests.submit(client.delete, f"/api/projects/{project['id']}")
        try:
            # Promotion waits without holding the queue lock or blocking the event loop.
            with gate.condition:
                assert gate.condition.wait_for(lambda: gate.promoting is not None, timeout=3)
            assert not preparing.is_set()
            assert client.get("/api/health").status_code == 200
            assert client.app.state.queue.lock.acquire(timeout=1)
            client.app.state.queue.lock.release()
        finally:
            release.set()
        assert writing.result(timeout=5).status_code == 200
        assert deleting.result(timeout=5).status_code == 409
    assert not preparing.is_set()  # Fresh metadata invalidated the earlier Delete snapshot.
    assert store.recording(recording["id"])
    assert (folder / "original.wav").read_bytes() == b"Synthetic original"
    assert gate.mutations == 0 and gate.promoting is None


def test_v167_waiting_write_rechecks_failed_staging_recovery(client, monkeypatch):
    store, manager = client.app.state.store, client.app.state.deletions
    gate = client.app.state.portable_capture_gate
    project, _, folder = seed(store)
    prepared, waiting, release = (threading.Event() for _ in range(3))
    prepare, rename, enter = deletion_files.prepare, deletion_files._rename, gate.enter_mutation

    def paused_prepare(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        prepared.set()
        assert release.wait(10)
        return plan

    def fail_after_move(path, destination):
        rename(path, destination)
        if path == folder:
            folder.mkdir()
            (folder / "collision.txt").write_bytes(b"Retain this occupied restore destination")
            raise OSError("Synthetic staging interruption after a destination collision")

    def observed_admission(allowed=None):
        if prepared.is_set():
            waiting.set()
        return enter(allowed)

    monkeypatch.setattr(deletion_files, "prepare", paused_prepare)
    monkeypatch.setattr(deletion_files, "_rename", fail_after_move)
    monkeypatch.setattr(gate, "enter_mutation", observed_admission)
    with ThreadPoolExecutor(max_workers=2) as requests:
        deleting = requests.submit(client.delete, f"/api/projects/{project['id']}")
        assert prepared.wait(3)
        writing = requests.submit(
            client.patch,
            f"/api/projects/{project['id']}/classification",
            json={"classification": "protected"},
        )
        try:
            assert waiting.wait(3)
            assert client.get("/api/health").status_code == 200
        finally:
            release.set()
        assert deleting.result(timeout=5).status_code == 409
        assert writing.result(timeout=5).status_code == 409
    assert manager.file_recovery_blocked
    assert (
        store.one("SELECT classification FROM projects WHERE id=?", (project["id"],))[
            "classification"
        ]
        == "standard"
    )
    assert gate.mutations == 0 and gate.promoting is None
    (folder / "collision.txt").unlink()
    folder.rmdir()
    monkeypatch.setattr(deletion_files, "_rename", rename)
    operation_id = next(iter(manager.local_files))
    retried = client.post(f"/api/local-deletions/{operation_id}/retry")
    assert retried.status_code == 200 and retried.json()["ok"]
    assert not manager.file_recovery_blocked
    assert (folder / "original.wav").read_bytes() == b"Synthetic original"


@pytest.mark.parametrize("lost_commit_reply", [False, True])
def test_v167_committed_cleanup_allows_stop_and_refuses_backup(
    client, monkeypatch, lost_commit_reply
):
    store, manager = client.app.state.store, client.app.state.deletions
    gate = client.app.state.portable_capture_gate
    project, _, _ = seed(store)
    other, recording, _ = seed(store, "Unrelated active work")
    job = store.add_job(recording["id"], "media", {})
    removing, release = threading.Event(), threading.Event()
    rmtree, connection = deletion_files.shutil.rmtree, store.connection
    injected = []

    @contextmanager
    def committed_then_error():
        with connection() as db:
            yield db
            deleted = not db.execute(
                "SELECT 1 FROM projects WHERE id=?", (project["id"],)
            ).fetchone()
            if lost_commit_reply and deleted and db.total_changes and not injected:
                db.commit()
                injected.append(True)
                raise OSError("Synthetic lost COMMIT response")

    def paused_cleanup(path, *args, **kwargs):
        removing.set()
        assert release.wait(30)
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(store, "connection", committed_then_error)
    monkeypatch.setattr(deletion_files.shutil, "rmtree", paused_cleanup)
    with ThreadPoolExecutor(max_workers=1) as requests:
        deleting = requests.submit(client.delete, f"/api/projects/{project['id']}")
        try:
            assert removing.wait(10), (
                deleting.result(timeout=1).text
                if deleting.done()
                else "Delete did not reach cleanup"
            )
            assert not manager.file_recovery_blocked and gate.promoting is None
            operation = next(
                item for item in manager.operations.values() if item.target == project["id"]
            )
            assert client.delete(
                f"/api/projects/{project['id']}", params={"operation_id": operation.identifier}
            ).json() == {"ok": True}
            assert client.get("/api/health").status_code == 200
            assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 200
            assert store.job(job["id"])["state"] == "cancelled"
            competing = client.delete(f"/api/projects/{other['id']}")
            assert competing.status_code == 409 and "local file operation" in competing.text
            assert store.recording(recording["id"])
            assert (
                client.patch(
                    f"/api/projects/{other['id']}/classification",
                    json={"classification": "protected"},
                ).status_code
                == 200
            )
            backup = client.post("/api/library/backups", json={})
            assert backup.status_code == 400 and "local file" in backup.json()["detail"].lower()
        finally:
            release.set()
        assert deleting.result(timeout=5).status_code == 200
    assert bool(injected) is lost_commit_reply
    assert not manager.local_files and gate.mutations == 0


def test_v167_promotion_refuses_second_promoter_and_preserves_capture_exclusion():
    gate = CaptureGate()
    first, second = gate.enter_mutation(), gate.enter_mutation()
    entered, release, captured = (threading.Event() for _ in range(3))
    errors = []

    def promote():
        try:
            with gate.mutation(first), gate.promote_mutation():
                entered.set()
                assert release.wait(5)
        except BaseException as error:
            errors.append(error)
        finally:
            gate.leave_mutation()

    def capture():
        with gate.capture():
            captured.set()

    owner = threading.Thread(target=promote)
    owner.start()
    with gate.condition:
        assert gate.condition.wait_for(lambda: gate.promoting is not None, timeout=3)
    with gate.mutation(second), pytest.raises(ValueError, match="current local file operation"):
        with gate.promote_mutation():
            pytest.fail("A second promoter cannot wait while occupying a mutation slot")
    gate.leave_mutation()
    assert entered.wait(3)
    backup = threading.Thread(target=capture)
    backup.start()
    assert not captured.wait(0.05)
    release.set()
    owner.join(3)
    backup.join(3)
    assert not owner.is_alive() and not backup.is_alive() and not errors
    assert captured.is_set() and gate.mutations == 0 and gate.promoting is None
    slot = gate.enter_mutation()
    with gate.mutation(slot), pytest.raises(RuntimeError), gate.promote_mutation():
        raise RuntimeError("Synthetic cancellation or failed staging")
    gate.leave_mutation()
    assert gate.promoting is None


def test_v167_cancelled_waiting_request_releases_its_admission_slot(client, monkeypatch):
    app, gate = client.app, client.app.state.portable_capture_gate
    waiting = threading.Event()
    enter = gate.enter_mutation

    def observed_admission(allowed=None):
        waiting.set()
        return enter(allowed)

    monkeypatch.setattr(gate, "enter_mutation", observed_admission)

    async def scenario():
        async def request(*, task_status):
            async def receive():
                await anyio.sleep_forever()

            async def send(_):
                pass

            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "scheme": "http",
                "method": "POST",
                "path": "/api/activity/clear",
                "raw_path": b"/api/activity/clear",
                "query_string": b"",
                "headers": [(b"host", b"127.0.0.1"), (b"x-cbs-request", b"1")],
                "client": ("127.0.0.1", 12345),
                "server": ("127.0.0.1", 80),
            }
            with anyio.CancelScope() as cancel:
                task_status.started(cancel)
                await app(scope, receive, send)

        async with anyio.create_task_group() as tasks:
            with gate.promote_mutation():
                cancel = await tasks.start(request)
                assert await anyio.to_thread.run_sync(waiting.wait, 3)
                cancel.cancel()
            # Shielded admission completes after promotion, then its finally releases.

    anyio.run(scenario)
    assert gate.mutations == 0 and gate.promoting is None
    assert client.post("/api/activity/clear").status_code == 200


def test_v167_admission_waiters_preserve_endpoint_capacity(client, monkeypatch):
    app, gate = client.app, client.app.state.portable_capture_gate
    project = app.state.store.create_project("Synthetic thread-pool drain")
    waiting = threading.Event()
    enter = gate.enter_mutation

    def observed_admission(allowed=None):
        if gate.promoting is not None:
            waiting.set()
        return enter(allowed)

    monkeypatch.setattr(gate, "enter_mutation", observed_admission)

    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 2
        body_waiting, release_body, earlier_done = (anyio.Event() for _ in range(3))
        responses = {}

        async def held_body():
            body_waiting.set()
            await release_body.wait()
            yield b'{"name":"Earlier admitted request"}'

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://127.0.0.1",
            headers={"X-CBS-Request": "1"},
        ) as browser:

            async def earlier():
                responses["earlier"] = await browser.post(
                    "/api/projects",
                    content=held_body(),
                    headers={"Content-Type": "application/json"},
                )
                earlier_done.set()

            async def deleting():
                responses["delete"] = await browser.delete(f"/api/projects/{project['id']}")

            async def later():
                responses["later"] = await browser.post("/api/activity/clear")

            try:
                async with anyio.create_task_group() as requests:
                    requests.start_soon(earlier)
                    await body_waiting.wait()  # Admitted, but not yet using an endpoint worker.
                    requests.start_soon(deleting)
                    with anyio.fail_after(3):
                        while gate.promoting is None:
                            await anyio.sleep(0.001)
                    requests.start_soon(later)
                    with anyio.fail_after(3):
                        while not waiting.is_set():
                            await anyio.sleep(0.001)
                    release_body.set()
                    with anyio.move_on_after(3):
                        await earlier_done.wait()
                    completed = earlier_done.is_set()
                    # Break the demonstrated cycle on failure so the red test also
                    # drains: awaiting an endpoint worker is cancellation-shielded.
                    if not completed:
                        limiter.total_tokens = previous
                assert completed, "Admission waiters starved the earlier endpoint's worker"
                assert {key: response.status_code for key, response in responses.items()} == {
                    "earlier": 201,
                    "delete": 200,
                    "later": 200,
                }
                assert gate.mutations == 0 and gate.promoting is None
            finally:
                limiter.total_tokens = previous

    anyio.run(scenario)
