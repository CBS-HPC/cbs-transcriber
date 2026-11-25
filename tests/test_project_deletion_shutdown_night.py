"""Independent multi-task shutdown interleaving; synthetic libraries only."""

import threading
from contextlib import contextmanager

import pytest
from test_project_deletion_tasks import hold_stop as hold_stop
from test_project_deletion_tasks import project_exists, settled, start, wait_for

from cbs_transcriber import deletions, project_deletion_tasks


def test_shutdown_keeps_every_queued_project_while_first_commit_drains(
    client, hold_stop, monkeypatch
):
    owner, store = client.app.state.project_deletions, client.app.state.store
    first = store.create_project("First COMMIT")
    second = store.create_project("Must be kept on shutdown")
    commit_entered, release_commit = threading.Event(), threading.Event()
    close_acquired, release_close = threading.Event(), threading.Event()
    original_guard, original_stop = owner._commit_guard, owner.deletions.stop_for_task
    stops, close_errors = [], []
    closer = None

    @contextmanager
    def held_commit(task):
        with original_guard(task):
            if task.project_id == first["id"]:
                commit_entered.set()
                assert release_commit.wait(10)
            yield

    def stop(operation, should_continue):
        stops.append(operation.target)
        return original_stop(operation, should_continue)

    def close():
        try:
            owner.close()
        except BaseException as error:
            close_errors.append(error)

    class HeldCloseDecision:
        def __init__(self, lock):
            self.lock = lock

        def __enter__(self):
            self.lock.acquire()
            if threading.current_thread() is closer:
                close_acquired.set()
                assert release_close.wait(10)
            return self

        def __exit__(self, *args):
            self.lock.release()

    monkeypatch.setattr(owner, "_commit_guard", held_commit)
    monkeypatch.setattr(owner.deletions, "stop_for_task", stop)
    first_task = start(client, first["id"]).json()
    assert hold_stop.entered.wait(10)
    second_task = start(client, second["id"]).json()
    hold_stop.release.set()
    assert commit_entered.wait(10)
    task = owner.tasks[first_task["id"]]
    task.decision = HeldCloseDecision(task.decision)
    closer = threading.Thread(target=close, daemon=True)
    closer.start()
    try:
        wait_for(lambda: owner.closed)
        release_commit.set()
        assert close_acquired.wait(10)
        # close() cannot yet mark the second task individually. Its global closing
        # signal must nevertheless stop the executor before any new Stop/Delete.
        done = settled(client, second_task["id"])
        assert done["state"] == "interrupted" and done["committed"] is False
        assert project_exists(store, second["id"])
        assert second["id"] not in stops
    finally:
        release_commit.set()
        release_close.set()
        closer.join(10)
        assert not closer.is_alive() and not close_errors


@pytest.mark.parametrize("boundary", ["decide", "commit_guard"])
def test_global_shutdown_refuses_each_not_yet_claimed_commit_boundary(client, boundary):
    owner = client.app.state.project_deletions
    owner.close()
    task = project_deletion_tasks.Task(
        "a" * 32,
        "b" * 32,
        "Queued when closing",
        "c" * 32,
        "d" * 64,
        "2026-11-20T22:00:00Z",
        "2026-11-20T22:00:00Z",
    )
    assert owner.closed is True and task.closing is False
    with pytest.raises(deletions.Kept):
        if boundary == "decide":
            owner._decide(task, final=True)
        else:
            with owner._commit_guard(task):
                pass
    assert task.committed is False and task.committing is False
