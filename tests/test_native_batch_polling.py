"""Only read-only native monitoring reconnects; allocation mutations never replay."""

import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue, frozen_native_jobs
from test_native_batch import selection as selection
from test_ucloud_native import SECRET, fresh_native_setup, native_selection
from test_ucloud_native import remote as remote
from ucloud_workflow import UCloudAPIError

from cbs_transcriber import native_batch
from cbs_transcriber.app import create_app
from cbs_transcriber.jobs import JobQueue
from cbs_transcriber.media import Cancelled


def monitor(monkeypatch, outcomes, *, deadline=1000, stop=None):
    clock, waits, calls, notices = [0.0], [], [], []
    worker = SimpleNamespace(cancel_event=threading.Event(), stopping=threading.Event())
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def retrieve(identifier, *, request_timeout):
        calls.append((identifier, request_timeout))
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def guard():
        if worker.cancel_event.is_set() or worker.stopping.is_set():
            raise Cancelled()

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds
        if stop is not None:
            getattr(worker, stop).set()

    monkeypatch.setattr(worker.cancel_event, "wait", wait)

    def poll():
        return native_batch._poll_job(
            SimpleNamespace(retrieve_job=retrieve),
            "recorded-job",
            worker,
            guard,
            lambda: notices.append(True),
            deadline,
        )

    return poll, waits, calls, notices


@pytest.mark.parametrize("status", [502, 503, 504])
def test_read_only_transient_status_retries_beyond_initial_burst_and_resets_after_success(
    monkeypatch, status
):
    running, done = {"status": {"state": "RUNNING"}}, {"status": {"state": "SUCCESS"}}
    poll, waits, calls, notices = monitor(
        monkeypatch,
        [UCloudAPIError(SECRET, status_code=status) for _ in range(5)]
        + [running, httpx.ReadTimeout(SECRET), done],
    )
    assert poll() == running
    assert waits == [2, 5, 10, 30, 30]
    assert poll() == done
    assert waits == [2, 5, 10, 30, 30, 2]
    assert len(notices) == 6
    assert {identifier for identifier, _ in calls} == {"recorded-job"}
    assert all(0 < timeout <= 30 for _, timeout in calls)


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout, httpx.ConnectError, httpx.RemoteProtocolError]
)
def test_read_only_transport_failure_reconnects(monkeypatch, error):
    expected = {"status": {"state": "SUCCESS"}}
    poll, waits, _, _ = monitor(monkeypatch, [error(SECRET), expected])
    assert poll() == expected
    assert waits == [2]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_auth_and_other_nontransient_statuses_fail_immediately_without_retry(monkeypatch, status):
    poll, waits, calls, notices = monitor(monkeypatch, [UCloudAPIError(SECRET, status_code=status)])
    with pytest.raises(ValueError, match=f"HTTP {status}") as caught:
        poll()
    assert SECRET not in str(caught.value)
    assert len(calls) == 1 and not waits and not notices


def test_read_only_reconnect_deadline_bounds_backoff_and_each_get_timeout(monkeypatch):
    poll, waits, calls, _ = monitor(
        monkeypatch, [UCloudAPIError(SECRET, status_code=502) for _ in range(5)], deadline=12
    )
    with pytest.raises(native_batch.MonitoringInterrupted, match="may still be running"):
        poll()
    assert waits == [2, 5, 5]
    assert [timeout for _, timeout in calls] == [12, 10, 5]


@pytest.mark.parametrize("stop", ["cancel_event", "stopping"])
def test_read_only_backoff_honors_cancel_and_shutdown_before_another_poll(monkeypatch, stop):
    poll, waits, calls, _ = monitor(
        monkeypatch, [UCloudAPIError(SECRET, status_code=502)], stop=stop
    )
    with pytest.raises(Cancelled):
        poll()
    assert waits == [2] and len(calls) == 1


def test_cancel_during_failed_get_skips_reconnect_notice_and_backoff(monkeypatch):
    worker = SimpleNamespace(cancel_event=threading.Event())

    def retrieve(*args, **kwargs):
        worker.cancel_event.set()
        raise httpx.ReadTimeout(SECRET)

    def guard():
        if worker.cancel_event.is_set():
            raise Cancelled()

    monkeypatch.setattr(worker.cancel_event, "wait", lambda _: pytest.fail("already stopped"))
    with pytest.raises(Cancelled):
        native_batch._poll_job(
            SimpleNamespace(retrieve_job=retrieve),
            "recorded-job",
            worker,
            guard,
            lambda: pytest.fail("do not publish a reconnect after Stop"),
            native_batch.time.monotonic() + 100,
        )


def intercept_polls(batch_remote, outcomes):
    original, identifiers = batch_remote.handle, []

    def handle(request):
        if request.url.path == "/api/jobs/retrieve" and request.url.params["id"] == "remote-job":
            identifiers.append(request.url.params["id"])
            batch_remote.calls.append(request)
            value = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
            if isinstance(value, Exception):
                raise value
            if isinstance(value, int):
                return httpx.Response(value, text=SECRET)
            return httpx.Response(200, json={"status": {"state": value}})
        return original(request)

    batch_remote.handle = handle
    return identifiers


def frozen_choices(store, jobs):
    return [
        {
            key: deepcopy(store.job(job["id"])["params"][key])
            for key in (
                "settings",
                "provider_id",
                "native_allocation",
                "audio_input",
                "input_acceptance",
            )
            if key in job["params"]
        }
        for job in jobs
    ]


def test_live_style_502_and_timeout_reconnect_to_the_same_job_then_import_without_new_allocation(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    choices = frozen_choices(store, jobs)
    identifiers = intercept_polls(
        batch_remote, [502, 503, 504, 502, 502, "RUNNING", httpx.ReadTimeout(SECRET), "SUCCESS"]
    )
    waits, stages, clock = [], [], [0.0]
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    identifier = jobs[0]["params"]["native_batch_id"]

    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds
        batch = store.native_batch(identifier)
        stages.append(batch["message"])
        assert batch["state"] == "running"
        assert all(store.job(job["id"])["state"] == "running" for job in jobs)

    monkeypatch.setattr(queue.cancel_event, "wait", wait)
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = identifier
    queue.process(claimed["id"])
    assert waits == [2, 5, 10, 30, 30, 5, 2]
    assert stages.count(native_batch.RECONNECTING) == 6
    assert "UCloud batch · RUNNING" in stages
    assert len(identifiers) == 8 and set(identifiers) == {"remote-job"}
    assert len(batch_remote.submissions) == 1
    assert not [r for r in batch_remote.calls if r.url.path == "/api/jobs/terminate"]
    assert frozen_choices(store, jobs) == choices
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)
    assert store.native_batch(identifier)["state"] == "completed"


@pytest.mark.parametrize("shutdown", [False, True], ids=["explicit-stop", "shutdown"])
def test_reconnect_wait_stops_locally_and_shutdown_does_not_terminate_the_allocation(
    client, batch_remote, selection, monkeypatch, shutdown
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    jobs = frozen_native_jobs(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    store.update_native_batch(
        identifier, run_update={"job_id": "remote-job", "remote_state": "RUNNING"}
    )
    identifiers = intercept_polls(batch_remote, [502])

    def wait(_):
        (queue.stopping if shutdown else queue.cancel_event).set()

    monkeypatch.setattr(queue.cancel_event, "wait", wait)
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = identifier
    with pytest.raises(Cancelled):
        queue.process(claimed["id"])
    batch = store.native_batch(identifier)
    assert batch["state"] == ("interrupted" if shutdown else "cancelled")
    assert batch["params"]["ucloud_run"]["job_id"] == "remote-job"
    assert identifiers == ["remote-job"] and not batch_remote.submissions
    terminated = [r for r in batch_remote.calls if r.url.path == "/api/jobs/terminate"]
    assert len(terminated) == (0 if shutdown else 1)


@pytest.mark.parametrize("status", [502, 401], ids=["monitor-deadline", "auth-failure"])
def test_real_worker_finalizer_preserves_monitor_interruption_and_normal_auth_failure(
    client, batch_remote, selection, monkeypatch, status
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    jobs = frozen_native_jobs(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    store.update_native_batch(
        identifier,
        run_update={
            "job_id": "remote-job",
            "remote_state": "RUNNING",
            "submission_attempted": True,
        },
    )
    choices = frozen_choices(store, jobs)
    identifiers = intercept_polls(batch_remote, [status])
    worker = JobQueue(store, client.app.state.credentials, kind="transcribe-native")
    clock = [0.0]
    budget = jobs[0]["params"]["settings"]["ucloud_transcriber"]["hours"] * 3600 + 600
    monkeypatch.setattr(native_batch, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    original_process = worker.process

    def process(identifier):
        # The real worker creates a new cancellation event immediately after claiming.
        monkeypatch.setattr(
            worker.cancel_event, "wait", lambda _: clock.__setitem__(0, clock[0] + budget + 1)
        )
        return original_process(identifier)

    monkeypatch.setattr(worker, "process", process)
    original, finished = store.finish_native_batch, []

    def finish(identifier, state, message):
        result = original(identifier, state, message)
        finished.append(state)
        worker.stopping.set()  # End this synchronous worker after its one accepted job.
        return result

    monkeypatch.setattr(store, "finish_native_batch", finish)
    worker.run()
    expected = "interrupted" if status == 502 else "failed"
    batch = store.native_batch(identifier)
    assert finished == [expected, expected]  # Producer and real queue finalizer agree.
    assert batch["state"] == expected
    assert all(store.job(job["id"])["state"] == expected for job in jobs)
    assert batch["params"]["ucloud_run"]["job_id"] == "remote-job"
    assert batch["params"]["ucloud_run"]["remote_state"] == "RUNNING"
    assert (
        "still be running" in batch["message"] if status == 502 else "HTTP 401" in batch["message"]
    )
    assert SECRET not in batch["message"]
    assert identifiers == ["remote-job"] and not batch_remote.submissions
    assert not [r for r in batch_remote.calls if r.url.path == "/api/jobs/terminate"]
    assert frozen_choices(store, jobs) == choices
    if status == 502:
        # Explicit later Retry also reconnects the retained ID, without submitting again.
        intercept_polls(batch_remote, ["SUCCESS"])
        response = client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={})
        assert response.status_code == 200, response.text
        claimed = store.claim_job("transcribe")
        queue = client.app.state.queue
        queue.active_batch_id = identifier
        queue.process(claimed["id"])
        assert not batch_remote.submissions
        assert all(len(store.results(recording["id"])) == 1 for recording in recordings)


def test_submission_502_is_never_retried_automatically_or_by_ordinary_retry(
    client, batch_remote, selection, monkeypatch
):
    provider, recordings = selection
    store, queue = client.app.state.store, client.app.state.queue
    batch_remote.store = store
    jobs = enqueue(client, provider, recordings)
    identifier = jobs[0]["params"]["native_batch_id"]
    original = batch_remote.handle
    posts = []

    def handle(request):
        if request.method == "POST" and request.url.path == "/api/jobs":
            posts.append(json.loads(request.content))
            return httpx.Response(502, text=SECRET)
        return original(request)

    batch_remote.handle = handle
    monkeypatch.setattr(queue.cancel_event, "wait", lambda _: pytest.fail("never retry a POST"))
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = identifier
    with pytest.raises(ValueError, match="HTTP 502"):
        queue.process(claimed["id"])
    assert native_batch.uncertain(store.native_batch(identifier)["params"]["ucloud_run"])
    response = client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={})
    assert response.status_code == 200, response.text
    claimed = store.claim_job("transcribe")
    with pytest.raises(ValueError, match="outcome is unknown"):
        queue.process(claimed["id"])
    assert len(posts) == 1


def test_queue_shutdown_and_library_relaunch_explicitly_retrieve_the_same_allocation(
    tmp_path, credentials, batch_remote, wav_bytes, monkeypatch
):
    library = tmp_path / "relaunch-library"
    first_app = create_app(library, start_worker=False, credentials=credentials)
    running = threading.Event()
    with TestClient(first_app, headers={"X-CBS-Request": "1"}) as first:
        first_store, first_queue = first.app.state.store, first.app.state.queue
        batch_remote.store = first_store
        batch_remote.states = ["RUNNING"]
        with first_store.library_lock():
            provider, recording = native_selection(
                first, wav_bytes, setup=fresh_native_setup(batch_remote, monkeypatch)
            )
            second = first_store.create_recording(recording["project_id"], "second.wav")
            first_store.directory(second["id"]).mkdir(parents=True)
            (first_store.directory(second["id"]) / "original.wav").write_bytes(wav_bytes)
            first_store.execute(
                "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (second["id"],)
            )
            jobs = enqueue(first, provider, [recording, second])
            identifier = jobs[0]["params"]["native_batch_id"]
            choices = frozen_choices(first_store, jobs)
            worker = next(w for w in first_queue.workers if w.kind == "transcribe-native")
            original_process = worker.process

            def process(identifier):
                wait = worker.cancel_event.wait

                def during_poll(seconds):
                    running.set()
                    return wait(seconds)

                monkeypatch.setattr(worker.cancel_event, "wait", during_poll)
                return original_process(identifier)

            monkeypatch.setattr(worker, "process", process)
            worker._start()
            try:
                assert running.wait(30), "The synthetic native worker did not reach monitoring"
                assert (
                    first_store.native_batch(identifier)["params"]["ucloud_run"]["remote_state"]
                    == "RUNNING"
                )
            finally:
                # Signals and joins the real worker while its Store lease is held.
                first_queue.stop()
            assert not worker.thread.is_alive()
            stopped = first_store.native_batch(identifier)
            assert stopped["state"] == "interrupted"
            journal = deepcopy(stopped["params"]["ucloud_run"])
            assert journal["job_id"] == "remote-job" and len(journal["uploaded"]) == 2
            assert not [r for r in batch_remote.calls if r.url.path == "/api/jobs/terminate"]
            assert len(batch_remote.submissions) == 1 and len(batch_remote.uploads) == 2

    # The former serving lease is closed before a new Store serves the same synthetic library.
    second_app = create_app(library, start_worker=False, credentials=credentials)
    with TestClient(second_app, headers={"X-CBS-Request": "1"}) as reopened:
        store, queue = reopened.app.state.store, reopened.app.state.queue
        assert store is not first_store and queue is not first_queue
        batch_remote.store = store
        batch_remote.states = ["SUCCESS"]
        with store.library_lock():
            assert store.native_batch(identifier)["params"]["ucloud_run"] == journal
            assert frozen_choices(store, jobs) == choices
            response = reopened.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={})
            assert response.status_code == 200, response.text
            claimed = store.claim_job("transcribe")
            queue.active_batch_id = identifier
            queue.process(claimed["id"])
            assert store.native_batch(identifier)["params"]["ucloud_run"]["job_id"] == "remote-job"
            assert (
                store.native_batch(identifier)["params"]["ucloud_run"]["uploaded"]
                == journal["uploaded"]
            )
            assert len(batch_remote.submissions) == 1 and len(batch_remote.uploads) == 2
            assert not [r for r in batch_remote.calls if r.url.path == "/api/jobs/terminate"]
            assert frozen_choices(store, jobs) == choices
            assert all(len(store.results(job["recording_id"])) == 1 for job in jobs)
            assert store.native_batch(identifier)["state"] == "completed"
