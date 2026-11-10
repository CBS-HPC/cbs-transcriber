"""Synthetic startup failures must release handles and drain accepted workers."""

import asyncio
import sqlite3
import threading
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from test_delete_staging import assert_preserved, prepare, seed

from cbs_transcriber import app as app_module
from cbs_transcriber import deletion_files
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.store import SCHEMA_VERSION, Store


@pytest.mark.parametrize("database", ["current", "future", "unreadable"])
def test_constructor_closes_readonly_schema_probe(tmp_path, monkeypatch, database):
    root = tmp_path / "library"
    store = Store(root)
    if database == "future":
        with store.connection() as db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    elif database == "unreadable":
        store.db_path.write_bytes(b"synthetic invalid database")

    handles = []
    connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            return super().close()

    def observe(path, *args, **kwargs):
        db = connect(path, *args, factory=ObservedConnection, **kwargs)
        handles.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", observe)
    try:
        if database == "current":
            Store(root)
        else:
            error = RuntimeError if database == "future" else sqlite3.DatabaseError
            with pytest.raises(error):
                Store(root)
        assert len(handles) == 1
        assert handles[0].closed, "the read-only schema connection outlived its operation"
        # On Windows a leaked SQLite handle prevents removal, even after Store returns.
        store.db_path.unlink()
    finally:
        for db in handles:
            db.close()


def synthetic_workers(queue, monkeypatch):
    """No job/provider/model work: record the lease held when each thread drains."""
    drained = []
    for worker in queue.workers:

        def run(worker=worker):
            worker.stopping.wait()
            drained.append(queue.store._lease_depth)

        monkeypatch.setattr(worker, "run", run)
    return drained


def settle_test_workers(queue):
    # A failing pre-fix assertion must never leave a synthetic thread behind.
    for worker in queue.workers:
        worker.stopping.set()
    for worker in queue.workers:
        if worker.thread and worker.thread.ident is not None:
            worker.thread.join(timeout=3)
            assert not worker.thread.is_alive()


@pytest.mark.parametrize("entry", ["queue", "lifespan"])
def test_partial_worker_start_failure_drains_before_releasing_lease(
    tmp_path, credentials, monkeypatch, entry
):
    if entry == "queue":
        queue = JobQueue(Store(tmp_path), credentials)
    else:
        app = app_module.create_app(tmp_path, credentials=credentials)
        queue = app.state.queue
    drained = synthetic_workers(queue, monkeypatch)
    start = threading.Thread.start
    accepted = []

    def fail_third_worker(thread):
        if thread.name.endswith("-queue"):
            if len(accepted) == 2:
                raise RuntimeError("synthetic worker start refusal")
            accepted.append(thread)
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_third_worker)
    try:
        with pytest.raises(RuntimeError, match="synthetic worker start refusal"):
            if entry == "queue":
                with queue.store.library_lock():
                    queue.start()
            else:
                with TestClient(app):
                    pytest.fail("startup unexpectedly succeeded")
        assert accepted and not any(thread.is_alive() for thread in accepted)
        assert drained == [1, 1], "accepted workers must finish while the serving lease is held"
        assert queue.store._lease_depth == 0
        with Store(tmp_path).library_lock():
            pass
    finally:
        settle_test_workers(queue)


def test_later_startup_failure_drains_successfully_started_queue(
    tmp_path, credentials, monkeypatch
):
    app = app_module.create_app(tmp_path, credentials=credentials)
    queue = app.state.queue
    drained = synthetic_workers(queue, monkeypatch)
    monkeypatch.delenv("CBS_TRANSCRIBER_NO_STARTUP_CHECK", raising=False)
    start = threading.Thread.start

    def refuse_hardware_observer(thread):
        if thread._target is app_module.hardware_status:
            raise RuntimeError("synthetic hardware observer start refusal")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", refuse_hardware_observer)
    try:
        with pytest.raises(RuntimeError, match="synthetic hardware observer start refusal"):
            with TestClient(app):
                pytest.fail("startup unexpectedly succeeded")
        assert not any(worker.thread.is_alive() for worker in queue.workers)
        assert drained == [1] * len(queue.workers)
        assert queue.store._lease_depth == 0
    finally:
        settle_test_workers(queue)


def test_failed_current_schema_probe_does_not_modify_future_database(tmp_path):
    path = tmp_path / "library.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        db.execute("CREATE TABLE witness (text TEXT)")
        db.execute("INSERT INTO witness VALUES ('Bevar Øresund\\r\\n')")
        db.commit()
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="newer"):
        Store(tmp_path)
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["library.sqlite3"]


@pytest.mark.parametrize("entry", ["queue", "worker-lifespan", "observer-lifespan", "shutdown"])
def test_cleanup_error_drains_all_workers_and_preserves_startup_error(
    tmp_path, credentials, monkeypatch, entry
):
    app = app_module.create_app(tmp_path, credentials=credentials)
    queue = app.state.queue
    drained = synthetic_workers(queue, monkeypatch)
    closed = []
    close_observations = []
    second = queue.workers[1]

    def delayed_second():
        second.stopping.wait()
        # Guarantee a later accepted worker remains alive when the old stop loop
        # reaches the first runtime close. The fixed loop joins it before any close.
        threading.Event().wait(0.2)
        drained.append(queue.store._lease_depth)

    monkeypatch.setattr(second, "run", delayed_second)

    class FailingRuntime:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)
            close_observations.append(second.thread.is_alive())
            raise OSError("synthetic runtime cleanup refusal")

    queue.workers[0].passage_runtime = FailingRuntime("first")
    second.passage_runtime = FailingRuntime("second")
    start = threading.Thread.start
    accepted = []
    monkeypatch.delenv("CBS_TRANSCRIBER_NO_STARTUP_CHECK", raising=False)

    def refuse_start(thread):
        if thread.name.endswith("-queue"):
            if entry in {"queue", "worker-lifespan"} and len(accepted) == 2:
                raise RuntimeError("synthetic primary worker refusal")
            accepted.append(thread)
        elif entry == "observer-lifespan" and thread._target is app_module.hardware_status:
            raise RuntimeError("synthetic primary observer refusal")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", refuse_start)
    try:
        error = OSError if entry == "shutdown" else RuntimeError
        message = "runtime cleanup" if entry == "shutdown" else "synthetic primary"
        with pytest.raises(error, match=message) as failure:
            if entry in {"queue", "shutdown"}:
                with queue.store.library_lock():
                    queue.start()
                    queue.stop()
            else:
                with TestClient(app):
                    pytest.fail("startup unexpectedly succeeded")
        assert not any(thread.is_alive() for thread in accepted)
        assert drained == [1] * len(accepted)
        assert set(closed) == {"first", "second"}
        assert close_observations == [False, False]
        if entry != "shutdown":
            assert isinstance(failure.value.__cause__, OSError)
            assert "runtime cleanup" in str(failure.value.__cause__)
        assert queue.store._lease_depth == 0
    finally:
        settle_test_workers(queue)


def test_successful_lifespan_cleanup_does_not_rethrow_ambient_handled_exception(
    tmp_path, credentials, monkeypatch
):
    app = app_module.create_app(tmp_path, credentials=credentials)
    monkeypatch.setenv("CBS_TRANSCRIBER_NO_STARTUP_CHECK", "1")
    monkeypatch.setattr(app.state.queue, "start", lambda: None)

    def refuse_cleanup():
        raise OSError("synthetic ordinary cleanup refusal")

    monkeypatch.setattr(app.state.queue, "stop", refuse_cleanup)

    async def enter_inside_handled_error():
        try:
            raise LookupError("unrelated already handled exception")
        except LookupError:
            async with app.router.lifespan_context(app):
                pass

    with pytest.raises(OSError, match="synthetic ordinary cleanup refusal"):
        asyncio.run(enter_inside_handled_error())
    assert app.state.store._lease_depth == 0


def test_partial_start_failure_allows_same_queue_retry_after_drain(
    tmp_path, credentials, monkeypatch
):
    queue = JobQueue(Store(tmp_path), credentials)
    drained = synthetic_workers(queue, monkeypatch)
    processed = threading.Event()
    first = queue.workers[0]

    def process_after_retry():
        if not first.stopping.is_set() and not first.cancel_event.is_set():
            processed.set()
        first.stopping.wait()
        drained.append(queue.store._lease_depth)

    monkeypatch.setattr(first, "run", process_after_retry)
    start = threading.Thread.start
    attempts = []

    def refuse_second_once(thread):
        if thread.name.endswith("-queue"):
            attempts.append(thread)
            if len(attempts) == 2:
                raise RuntimeError("synthetic initial start refusal")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", refuse_second_once)
    try:
        with queue.store.library_lock():
            with pytest.raises(RuntimeError, match="synthetic initial start refusal"):
                queue.start()
            assert drained == [1]
            assert all(worker.stopping.is_set() for worker in queue.workers)
            processed.clear()
            queue.start()
            assert processed.wait(3), "the replacement queue must actually run with fresh events"
            with pytest.raises(RuntimeError, match="already running"):
                queue.start()
            queue.stop()
            assert drained == [1] * (1 + len(queue.workers))
    finally:
        settle_test_workers(queue)


def test_deferred_file_recovery_retry_restarts_failed_queue_and_processes_synthetic_job(
    client, credentials, monkeypatch
):
    store = client.app.state.store
    fixture = seed(store, count=1)
    job = store.add_job(fixture["recordings"][0]["id"], "transcribe", {"synthetic": "frozen"})
    with store.library_lock():
        plan = prepare(store, fixture)
        deletion_files.stage(store, plan)
    occupied = fixture["paths"][0]
    occupied.mkdir()
    (occupied / "retain.txt").write_bytes(b"Retain collision bytes")
    app = app_module.create_app(store.root, credentials=credentials)
    queue = app.state.queue
    drained = synthetic_workers(queue, monkeypatch)
    monkeypatch.setenv("CBS_TRANSCRIBER_NO_STARTUP_CHECK", "1")
    first = queue.workers[0]
    processed = threading.Event()
    attempts = []

    def process_synthetic_metadata_only():
        while not first.stopping.is_set():
            with queue.lock:
                claimed = (
                    app.state.store.claim_job("transcribe")
                    if len(attempts) > 2 and not first.stopping.is_set()
                    else None
                )
            if claimed:
                app.state.store.update_job(claimed["id"], state="completed")
                processed.set()
            first.stopping.wait(0.01)
        drained.append(queue.store._lease_depth)

    monkeypatch.setattr(first, "run", process_synthetic_metadata_only)
    start = threading.Thread.start

    def refuse_second_once(thread):
        if thread.name.endswith("-queue"):
            attempts.append(thread)
            if len(attempts) == 2:
                raise RuntimeError("synthetic deferred worker start refusal")
        start(thread)

    monkeypatch.setattr(threading.Thread, "start", refuse_second_once)
    try:
        with TestClient(
            app, headers={"X-CBS-Request": "1"}, raise_server_exceptions=False
        ) as reopened:
            assert app.state.deletions.file_recovery_blocked
            assert not attempts
            retained = occupied.with_name("retained-collision")
            occupied.rename(retained)
            route = f"/api/local-deletions/{plan['operation_id']}/retry"
            assert reopened.post(route).status_code == 500
            assert drained == [1]
            assert not any(worker.thread and worker.thread.is_alive() for worker in queue.workers)
            response = reopened.post(route)
            assert response.status_code == 200 and response.json()["ok"], response.text
            assert processed.wait(3), "deferred recovery must leave a functioning replacement queue"
            current = app.state.store.job(job["id"])
            assert current["state"] == "completed" and current["params"] == job["params"]
            assert_preserved(app.state.store, fixture)
            assert (retained / "retain.txt").read_bytes() == b"Retain collision bytes"
        assert drained == [1] * (1 + len(queue.workers))
        assert app.state.store._lease_depth == 0
    finally:
        settle_test_workers(queue)
