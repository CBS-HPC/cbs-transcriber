"""HF task ownership against isolated settings and mocked Hub/model/UCloud work."""

import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from cbs_transcriber import enhancement, huggingface_access
from cbs_transcriber import huggingface_tasks as subject
from cbs_transcriber.store import Store

FIRST = "hf_SYNTHETIC_FIRST_SECRET"
SECOND = "hf_SYNTHETIC_SECOND_SECRET"


class Credentials:
    def __init__(self):
        self.values, self.writes = {}, []

    def get(self, key):
        return self.values.get(key, "")

    def set(self, key, value, remember):
        self.values[key] = value
        self.writes.append((key, value, remember))


@pytest.fixture
def manager(tmp_path, monkeypatch):
    store, credentials = Store(tmp_path / "library"), Credentials()
    manager = subject.HuggingFaceTasks(store, credentials, start_worker=False)
    monkeypatch.setattr(subject.huggingface_access, "check_token", allowed)
    calls = []

    def child(task, mode, interpreter, **kwargs):
        manager._guard(task)
        calls.append((mode, interpreter, kwargs))
        return {
            "status": "ready",
            "cache_targets": {"hf": str(tmp_path / "hf"), "nltk": str(tmp_path / "nltk")},
        }

    monkeypatch.setattr(manager, "_child", child)
    manager.child_calls = calls
    yield manager
    manager.close()


def allowed(token):
    return huggingface_access.token_state(token) | {
        "token_status": "valid",
        "speaker_model_access": "allowed",
        "checked_at": "2026-11-11T12:00:00+00:00",
    }


def run_pending(manager):
    with manager._lock:
        task = manager._tasks[manager._pending]
        manager._pending, manager._active = None, task.id
    try:
        manager._run(task)
    finally:
        manager._active = None
    return manager.activity()[-1]


def outcomes(row):
    return {value["stage"]: value["outcome"] for value in row["stage_outcomes"]}


def test_inert_acceptance_dedup_absent_forget_and_explicit_saved_check(manager, monkeypatch):
    monkeypatch.setattr(subject.huggingface_access, "check_token", lambda _: pytest.fail("network"))
    settings_before = manager.store.all("SELECT * FROM settings")
    initial = manager.save(FIRST, remember=False)
    row = initial["preparation_task"]
    assert row["state"] == "queued" and row["kind"] == "hf_setup"
    assert initial["token_status"] == "unchecked"
    assert manager.save(FIRST)["preparation_task"]["id"] == row["id"]
    assert manager.save(None)["preparation_task"]["id"] == row["id"]
    assert manager.check()["preparation_task"]["id"] == row["id"]
    writes = list(manager.credentials.writes)
    with pytest.raises(subject.Conflict, match="Save this"):
        manager.check(SECOND)
    assert manager.credentials.writes == writes
    assert manager.child_calls == []
    assert manager.save("")["preparation_task"] is None
    assert manager.activity()[0]["state"] == "cancelled"
    with pytest.raises(subject.Conflict, match="Save a Hugging Face token"):
        manager.check()
    assert manager.store.all("SELECT * FROM jobs") == []
    assert manager.store.all("SELECT * FROM settings") == settings_before
    encoded = json.dumps(manager.activity())
    assert FIRST not in encoded and hashlib.sha256(FIRST.encode()).hexdigest() not in encoded


@pytest.mark.parametrize(
    "token_status,access",
    [("invalid", "unknown"), ("valid", "denied"), ("unavailable", "unavailable")],
)
def test_authentication_and_terms_failure_never_prepare_models(
    manager, monkeypatch, token_status, access
):
    monkeypatch.setattr(
        subject.huggingface_access,
        "check_token",
        lambda token: (
            allowed(token) | {"token_status": token_status, "speaker_model_access": access}
        ),
    )
    manager.save(FIRST)
    row = run_pending(manager)
    assert row["state"] == "failed"
    assert outcomes(row) == {"access": "failed"}
    assert manager.child_calls == []
    assert manager.status()["token_status"] == token_status


def test_local_plan_is_exact_and_later_check_reuses_existing_cache_helpers(manager):
    manager.save(FIRST)
    first = run_pending(manager)
    assert first["state"] == "completed"
    assert outcomes(first) == {
        "access": "complete",
        "speaker_files": "complete",
        "local_public": "complete",
        "ucloud": "skipped",
    }
    assert [call[0] for call in manager.child_calls] == ["inspect", "speakers", "public"]
    assert manager.child_calls[1][1] == sys.executable
    assert manager.status()["speaker_model_local"] == "verified"
    second = manager.check()["preparation_task"]
    assert second["id"] != first["id"]
    assert run_pending(manager)["state"] == "completed"
    assert len(manager.child_calls) == 6


def test_disabled_local_and_unconfigured_cloud_are_truthful_partial_without_children(manager):
    enhancement.save_local_settings(
        manager.store,
        enhancement.ProcessingSettings(download_models=False, prepare_ucloud_models=False),
    )
    manager.save(FIRST)
    row = run_pending(manager)
    assert row["state"] == "completed"
    assert outcomes(row) == {
        "access": "complete",
        "local_public": "skipped",
        "speaker_files": "skipped",
        "ucloud": "skipped",
    }
    assert manager.child_calls == []
    assert "some preparation stages" in row["message"]


def test_cloud_only_uses_app_speaker_staging_and_existing_durable_owner(manager):
    enhancement.save_local_settings(
        manager.store, enhancement.ProcessingSettings(download_models=False)
    )
    plan = {
        "status": "ready",
        "connection_fingerprint": "a" * 64,
        "model_plan": {"models": ["coarse", "da", "en"]},
    }
    manager.freeze_cloud_plan = lambda: plan
    requests = []

    def request(actual, guard):
        guard()
        requests.append(actual)
        return {
            "operation": {"runtime_preparation_id": "b" * 32, "state": "queued"},
            "deduplicated": False,
        }

    manager.request_cloud = request
    manager.cloud_status = lambda _: {"state": "completed", "progress": 1}
    manager.save(FIRST)
    row = run_pending(manager)
    assert row["state"] == "completed"
    assert requests == [plan]
    assert [call[0] for call in manager.child_calls] == ["speakers"]
    assert row["remote_child"]["created_by_task"] is True
    assert row["remote_child"]["runtime_preparation_id"] == "b" * 32
    assert outcomes(row)["ucloud"] == "complete"
    assert manager.store.all("SELECT * FROM jobs") == []


def test_capture_switch_retry_and_frozen_choices_are_fenced(manager):
    row = manager.save(FIRST)["preparation_task"]
    with pytest.raises(subject.Conflict):
        manager.require_idle()
    with pytest.raises(subject.Conflict):
        with manager.exclusive():
            pass
    manager.cancel(row["id"])
    manager.require_idle()
    with manager.exclusive():
        with pytest.raises(subject.Conflict):
            manager.save(SECOND)
    assert manager.credentials.get(enhancement.HF_SCOPE) == FIRST
    successor = manager.retry(row["id"])
    assert successor["id"] != row["id"] and successor["state"] == "queued"
    manager.cancel(successor["id"])
    manager.save(SECOND)
    with pytest.raises(subject.Conflict, match="changed"):
        manager.retry(row["id"])
    manager.stop_admission()
    with pytest.raises(subject.Conflict):
        manager.check()


def test_changed_token_drains_check_and_cannot_publish_old_success(manager, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    checked = []

    def check(token):
        checked.append(token)
        if token == FIRST:
            entered.set()
            assert release.wait(3)
        return allowed(token)

    monkeypatch.setattr(subject.huggingface_access, "check_token", check)
    manager.start_worker = True
    first = manager.save(FIRST)["preparation_task"]
    assert entered.wait(3)
    second = manager.save(SECOND)["preparation_task"]
    assert second["id"] != first["id"]
    assert manager.status()["token_status"] == "unchecked"
    release.set()
    manager._thread.join(3)
    rows = manager.activity()
    assert [row["state"] for row in rows] == ["cancelled", "completed"]
    assert checked == [FIRST, SECOND]
    assert manager.status()["preparation_task"]["id"] == second["id"]
    assert manager.status()["token_status"] == "valid"
    encoded = json.dumps(rows)
    assert FIRST not in encoded and SECOND not in encoded


def test_close_waits_for_accepted_access_check_under_borrowed_lease(manager, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def check(token):
        entered.set()
        assert release.wait(3)
        return allowed(token)

    monkeypatch.setattr(subject.huggingface_access, "check_token", check)
    manager.start_worker = True
    # The worker borrows this serving lease; re-entering its RLock would deadlock.
    with manager.store.library_lock():
        manager.save(FIRST)
        assert entered.wait(3)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(manager.close)
            assert not closing.done()
            release.set()
            closing.result(timeout=3)
        assert manager.store._lease_depth == 1
    assert manager.activity()[0]["state"] == "interrupted"
    assert manager.child_calls == []


def test_plan_only_changes_hide_old_receipts_and_invalidate_work(manager):
    manager.save(FIRST)
    run_pending(manager)
    assert manager.status()["token_status"] == "valid"
    enhancement.save_local_settings(
        manager.store, enhancement.ProcessingSettings(download_models=False)
    )
    manager.invalidate()
    assert manager.status()["token_status"] == "unchecked"
    assert manager.status()["preparation_task"] is None
    task = manager.check()["preparation_task"]
    enhancement.save_local_settings(
        manager.store, enhancement.ProcessingSettings(download_models=True)
    )
    manager.invalidate()
    assert (
        next(row for row in manager.activity() if row["id"] == task["id"])["state"] == "cancelled"
    )


@pytest.mark.parametrize("change_before_return", [True, False])
def test_access_handoff_binds_the_exact_plaintext_read_not_a_later_credential(
    manager, monkeypatch, change_before_return
):
    manager.save(FIRST)
    original = manager.credentials.get
    reads, sent = [], []

    def credential(scope):
        value = original(scope)
        reads.append(value)
        if len(reads) == 3:
            manager.credentials.values[scope] = SECOND
            return SECOND if change_before_return else value
        return value

    monkeypatch.setattr(manager.credentials, "get", credential)
    monkeypatch.setattr(
        subject.huggingface_access,
        "check_token",
        lambda token: sent.append(token) or allowed(token),
    )
    row = run_pending(manager)
    assert row["state"] == "cancelled"
    assert sent == ([] if change_before_return else [FIRST])
    assert SECOND not in sent and manager.child_calls == []
    assert manager.status()["token_status"] == "unchecked"


def test_speaker_child_does_not_borrow_replacement_token_between_guard_and_pipe(
    manager, monkeypatch
):
    manager.save(FIRST)
    task = manager._tasks[manager._pending]
    original = manager.credentials.get
    reads = []

    def credential(scope):
        reads.append(scope)
        if len(reads) == 2:
            manager.credentials.values[scope] = SECOND
        return original(scope)

    monkeypatch.setattr(manager.credentials, "get", credential)
    monkeypatch.setattr(subject.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned"))
    with pytest.raises(subject.Stopped):
        subject.HuggingFaceTasks._child(manager, task, "speakers", sys.executable)


def test_partial_credential_write_failure_still_invalidates_and_redacts(manager, monkeypatch):
    first = manager.save(FIRST)["preparation_task"]

    def fail(scope, token, remember):
        manager.credentials.values[scope] = token
        raise OSError("secret failure " + token)

    monkeypatch.setattr(manager.credentials, "set", fail)
    with pytest.raises(subject.Conflict) as error:
        manager.save(SECOND)
    assert SECOND not in str(error.value)
    assert manager.credentials.get(enhancement.HF_SCOPE) == SECOND
    assert manager.activity()[0]["id"] == first["id"]
    assert manager.activity()[0]["state"] == "cancelled"
    assert manager.status()["preparation_task"] is None


@pytest.mark.parametrize("deduplicated", [False, True])
def test_stop_drains_accepted_cloud_request_and_only_cancels_new_owned_child(manager, deduplicated):
    entered, release = threading.Event(), threading.Event()
    manager.freeze_cloud_plan = lambda: {"status": "ready", "connection": "synthetic"}
    cancelled = []

    def request(plan, guard):
        guard()
        entered.set()
        assert release.wait(3)
        return {
            "operation": {"runtime_preparation_id": "c" * 32, "state": "queued"},
            "deduplicated": deduplicated,
        }

    manager.request_cloud = request
    manager.cloud_status = lambda _: {"state": "running", "progress": 0.5}
    manager.cancel_cloud = cancelled.append
    manager.start_worker = True
    first = manager.save(FIRST)["preparation_task"]
    assert entered.wait(3)
    row = manager.cancel(first["id"])
    assert row["state"] == "running" and not row["cancellable"]
    assert cancelled == []
    release.set()
    manager._thread.join(3)
    row = manager.activity()[0]
    assert row["state"] == "cancelled" and row["remote_child"]["retained"]
    assert cancelled == ([] if deduplicated else ["c" * 32])
    assert row["remote_child"]["created_by_task"] is (not deduplicated)


def test_shutdown_drains_cloud_handoff_and_leaves_accepted_remote_owner_running(manager):
    entered, release = threading.Event(), threading.Event()
    manager.freeze_cloud_plan = lambda: {"status": "ready", "connection": "synthetic"}
    cancelled = []

    def request(plan, guard):
        guard()
        entered.set()
        assert release.wait(3)
        return {
            "operation": {"runtime_preparation_id": "d" * 32, "state": "queued"},
            "deduplicated": False,
        }

    manager.request_cloud = request
    manager.cloud_status = lambda _: {"state": "running", "progress": 0}
    manager.cancel_cloud = cancelled.append
    manager.start_worker = True
    manager.save(FIRST)
    assert entered.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = pool.submit(manager.close)
        assert not closing.done()
        release.set()
        closing.result(timeout=3)
    row = manager.activity()[0]
    assert row["state"] == "interrupted" and row["remote_child"]["retained"]
    assert cancelled == []


def test_clear_finished_hides_only_ordinary_terminal_parents_and_keeps_retry_attention(manager):
    from cbs_transcriber.jobs import clear_activity

    manager.save(FIRST)
    ordinary = run_pending(manager)
    queued = manager.check()["preparation_task"]
    clear_activity(manager.store)
    assert [row["id"] for row in manager.activity()] == [queued["id"]]
    assert manager.status()["preparation_task"]["id"] == queued["id"]
    task = manager._tasks[manager._pending]
    task.state, task.stage = "failed", "terminal"
    manager._pending = None
    clear_activity(manager.store)
    assert [row["id"] for row in manager.activity()] == [queued["id"]]
    assert ordinary["id"] not in [row["id"] for row in manager.activity()]


def test_local_stop_kills_and_drains_child_and_keeps_token_out_of_argv_and_environment(
    manager, monkeypatch, tmp_path
):
    speakers_started = threading.Event()
    processes = []

    class Input:
        def __init__(self, owner):
            self.owner = owner

        def write(self, raw):
            self.owner.request = json.loads(raw)
            self.owner.written.set()
            if self.owner.request["mode"] == "speakers":
                speakers_started.set()
            return len(raw)

        def close(self):
            pass

    class Output:
        def __init__(self, owner):
            self.owner = owner

        def read(self, limit):
            assert self.owner.written.wait(3)
            request = self.owner.request
            if request["mode"] == "speakers":
                assert self.owner.done.wait(3)
                return b""
            result = {
                key: request[key] for key in ("protocol", "nonce", "helper_sha256", "worker_sha256")
            } | {
                "status": "ready",
                "cache_targets": {"hf": str(tmp_path / "hf"), "nltk": str(tmp_path / "nltk")},
            }
            self.owner.done.set()
            return json.dumps(result).encode()

        def close(self):
            pass

    class Process:
        def __init__(self, args, **kwargs):
            assert FIRST not in json.dumps(args) + json.dumps(kwargs["env"])
            assert "HF_TOKEN" not in kwargs["env"] and "UCLOUD_TOKEN" not in kwargs["env"]
            assert args[1:3] == ["-I", "-B"]
            assert args[3] == str(subject.HELPER)
            self.done, self.written = threading.Event(), threading.Event()
            self.stdin, self.stdout = Input(self), Output(self)
            self.request, self.returncode = None, 0
            self.drained = False
            processes.append(self)

        def poll(self):
            return self.returncode if self.done.is_set() else None

    def stop(process):
        process.done.set()
        process.drained = True

    monkeypatch.setattr(subject.subprocess, "Popen", Process)
    monkeypatch.setattr(subject.enhancement, "stop_process", stop)
    monkeypatch.setattr(manager, "_child", subject.HuggingFaceTasks._child.__get__(manager))
    manager.start_worker = True
    row = manager.save(FIRST)["preparation_task"]
    assert speakers_started.wait(3)
    manager.cancel(row["id"])
    manager._thread.join(3)
    assert manager.activity()[0]["state"] == "cancelled"
    assert len(processes) == 2 and all(process.drained for process in processes)
    assert "token" not in processes[0].request
    assert processes[1].request["token"] == FIRST
    assert FIRST not in json.dumps(manager.activity())


@pytest.fixture
def wire_child(manager, monkeypatch):
    processes = []

    class Pipe:
        def __init__(self, owner, *, output=False):
            self.owner, self.output, self.closed = owner, output, False

        def write(self, raw):
            self.owner.request = json.loads(raw)
            self.owner.written.set()
            return len(raw)

        def read(self, limit):
            assert self.owner.written.wait(3)
            raw = self.owner.reply(self.owner.request)
            self.owner.done.set()
            return raw[:limit]

        def close(self):
            self.closed = True

    class Process:
        def __init__(self, *args, **kwargs):
            self.written, self.done = threading.Event(), threading.Event()
            self.stdin, self.stdout = Pipe(self), Pipe(self, output=True)
            self.returncode, self.drained, self.request = 0, False, None
            self.reply = lambda request: json.dumps(
                {
                    key: request[key]
                    for key in ("protocol", "nonce", "helper_sha256", "worker_sha256")
                }
                | {"status": "ready"}
            ).encode()
            processes.append(self)

        def poll(self):
            return self.returncode if self.done.is_set() else None

    def stop(process):
        process.done.set()
        process.drained = True

    monkeypatch.setattr(subject.subprocess, "Popen", Process)
    monkeypatch.setattr(subject.enhancement, "stop_process", stop)
    manager.save(FIRST)
    task = manager._tasks[manager._pending]
    return task, processes, Process


@pytest.mark.parametrize("failure", ["nonce", "source", "oversize", "json"])
def test_child_replies_are_bounded_and_bound_to_exact_request(
    manager, monkeypatch, wire_child, failure
):
    task, processes, Process = wire_child
    initialize = Process.__init__

    def create(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        if failure == "oversize":
            self.reply = lambda _: b"x" * (subject.helper.MAX_REPLY + 1)
        elif failure == "json":
            self.reply = lambda _: b"{invalid synthetic secret " + FIRST.encode()
        else:

            def reply(request):
                value = {
                    key: request[key]
                    for key in ("protocol", "nonce", "helper_sha256", "worker_sha256")
                } | {"status": "ready"}
                value["nonce" if failure == "nonce" else "worker_sha256"] = "0" * 64
                return json.dumps(value).encode()

            self.reply = reply

    monkeypatch.setattr(Process, "__init__", create)
    with pytest.raises((subject.Conflict, ValueError)):
        subject.HuggingFaceTasks._child(manager, task, "speakers", sys.executable)
    assert processes[0].drained
    assert processes[0].stdin.closed and processes[0].stdout.closed


def test_second_io_thread_start_failure_still_stops_reaps_and_closes_child(
    manager, monkeypatch, wire_child
):
    task, processes, _ = wire_child
    original = threading.Thread.start
    started = []

    def start(thread):
        if thread._target.__name__ == "read":
            raise RuntimeError("synthetic no more threads")
        original(thread)
        started.append(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    with pytest.raises(RuntimeError, match="no more threads"):
        subject.HuggingFaceTasks._child(manager, task, "speakers", sys.executable)
    assert processes[0].drained
    assert processes[0].stdin.closed and processes[0].stdout.closed
    assert len(started) == 1 and not started[0].is_alive()


@pytest.mark.parametrize("existing", ["none", "completed", "cancelled"])
def test_alias_invalidation_without_active_work_never_freezes_or_hashes_plan(
    manager, monkeypatch, existing
):
    if existing != "none":
        row = manager.save(FIRST)["preparation_task"]
        if existing == "completed":
            assert run_pending(manager)["state"] == "completed"
        else:
            manager.cancel(row["id"])

    def unavailable_plan():
        raise AssertionError("plan freeze must only run for actual work or current status")

    manager.freeze_cloud_plan = unavailable_plan
    monkeypatch.setattr(subject, "_source_digest", lambda _: pytest.fail("source hash"))
    manager.invalidate()
    # A real explicit Save still requires the actual plan. The no-op fast path
    # cannot turn an unavailable adapter into a ready or silently skipped stage.
    with pytest.raises(AssertionError, match="plan freeze"):
        manager.save(FIRST)


@pytest.mark.parametrize("later_action", ["save", "invalidate", "shutdown"])
@pytest.mark.parametrize("deduplicated", [False, True])
def test_explicit_stop_survives_later_changes_until_accepted_cloud_owner_is_known(
    manager, monkeypatch, later_action, deduplicated
):
    entered, release, closing_started = threading.Event(), threading.Event(), threading.Event()
    cloud_plan = {"status": "ready", "revision": 0}
    manager.freeze_cloud_plan = lambda: cloud_plan
    monkeypatch.setattr(
        subject.huggingface_access,
        "check_token",
        lambda token: (
            allowed(token) if token == FIRST else allowed(token) | {"token_status": "invalid"}
        ),
    )
    cancelled, requests = [], []

    def request(plan, guard):
        guard()
        requests.append(plan)
        entered.set()
        assert release.wait(3)
        return {
            "operation": {"runtime_preparation_id": "e" * 32, "state": "queued"},
            "deduplicated": deduplicated,
        }

    manager.request_cloud = request
    manager.cloud_status = lambda _: {"state": "running", "progress": 0}
    manager.cancel_cloud = cancelled.append
    manager.start_worker = True
    first = manager.save(FIRST)["preparation_task"]
    assert entered.wait(3)
    worker = manager._thread
    manager.cancel(first["id"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        closing = None
        if later_action == "save":
            manager.save(SECOND)
        elif later_action == "invalidate":
            cloud_plan["revision"] = 1
            manager.invalidate()
        else:
            stop = manager._stop_locked

            def stopped(task, reason):
                stop(task, reason)
                if reason == "shutdown":
                    closing_started.set()

            monkeypatch.setattr(manager, "_stop_locked", stopped)
            closing = pool.submit(manager.close)
            assert closing_started.wait(3)
        assert cancelled == []
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        if closing is not None:
            closing.result(timeout=3)
    old = next(row for row in manager.activity() if row["id"] == first["id"])
    assert old["state"] == ("interrupted" if later_action == "shutdown" else "cancelled")
    assert old["remote_child"]["retained"]
    assert old["remote_child"]["created_by_task"] is (not deduplicated)
    assert cancelled == ([] if deduplicated else ["e" * 32])
    assert len(requests) == 1


def test_native_only_cloud_configuration_names_missing_runtime_model_without_preparation(manager):
    enhancement.save_local_settings(
        manager.store, enhancement.ProcessingSettings(download_models=False)
    )
    manager.freeze_cloud_plan = lambda: {"status": "needs_setup", "reason": "no_runtime_models"}
    manager.request_cloud = lambda *args: pytest.fail(
        "native models cannot request Runtime preparation"
    )
    manager.save(FIRST)
    row = run_pending(manager)
    assert row["state"] == "completed"
    cloud = next(stage for stage in row["stage_outcomes"] if stage["stage"] == "ucloud")
    assert cloud["outcome"] == "skipped"
    assert (
        cloud["message"]
        == "Enable a CBS Runtime model in Settings before UCloud model preparation."
    )
    assert manager.status()["token_status"] == "valid"
    assert manager.child_calls == [] and row["remote_child"] is None
