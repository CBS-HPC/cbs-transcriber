"""Real batch mailbox plus synthetic local result pipeline; no network or inference."""

import copy
import json
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement, providers, runtime_store
from cbs_transcriber import enhancement_api as api
from cbs_transcriber import runtime_batch_remote as remote
from cbs_transcriber import runtime_batches as batches
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import Store, new_id, now
from cbs_transcriber.transcripts import normalize_segments


class FinishedChild:
    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class Drive:
    def __init__(self, root):
        self.root = root
        self.submissions, self.uploads, self.terminations, self.trashed = [], [], [], []
        self.controllers, self.states = {}, {}
        self.lose_submit, self.hold_worker, self.corrupt = False, False, False
        self.accepted = threading.Event()
        self.vram = 23
        self.fail_next = False
        self.partial_fail = False
        self.asr_calls = 0
        self.release_until = 0
        self.worker_count = 0
        self.events = []

    def path(self, name):
        return self.root / name.lstrip("/")

    def create_folder(self, name):
        self.path(name).mkdir(parents=True, exist_ok=True)

    def retrieve_file(self, name):
        path = self.path(name)
        return {
            "status": {
                "type": "DIRECTORY" if path.is_dir() else "FILE",
                "sizeInBytes": path.stat().st_size,
            }
        }

    def upload(self, client, server, path, target, guard, update, **kwargs):
        guard()
        self.events.append(("upload", target))
        destination = self.path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
        self.uploads.append((target, threading.current_thread().name))

    def submit_job(self, spec):
        self.events.append(("submit", spec["name"]))
        self.submissions.append(copy.deepcopy(spec))
        identifier = str(len(self.submissions))
        folder = self.path(spec["resources"][0]["path"])
        ready = {
            "version": api.WHISPERX_VERSION,
            "ready": True,
            "gpu": {"available": True, "devices": [{"memory_gb": self.vram}]},
        }
        (folder / "doctor.json").write_text(json.dumps(ready))
        (folder / "cache.json").write_text(
            json.dumps({"models": {key: True for key in api.MODELS}, "punkt_tab": True})
        )
        (folder / "supervisor.json").write_text(
            json.dumps(
                {"phase": "ready", "generation": folder.name.removeprefix("cbs-runtime-batch-")}
            )
        )
        self.controllers[identifier] = remote.BatchController(
            folder, runner=self.worker, cohort_runner=self.cohort_worker
        )
        self.states[identifier] = "RUNNING"
        if self.lose_submit:
            raise api.Transient("Synthetic lost allocation reply.")
        return {"responses": [{"id": identifier}]}

    def cohort_worker(self, folder, lock_fd):
        plan = json.loads((folder / "cohort.json").read_bytes())
        members = [(row, folder.parent.parent / "tasks" / row["task_id"]) for row in plan["tasks"]]
        children = [(row, task, self.worker(task, lock_fd)) for row, task in members]

        class CohortChild:
            def poll(inner):
                pending = False
                for row, task, child in children:
                    code = child.poll()
                    if code is None:
                        pending = True
                        continue
                    remote.write_json(
                        task / "runtime-complete.json",
                        {
                            "protocol": remote.PROTOCOL,
                            "batch_id": plan["batch_id"],
                            "cohort_id": plan["cohort_id"],
                            "task_id": row["task_id"],
                            "inputs_sha256": row["inputs_sha256"],
                            "error_code": None if code == 0 else "processing",
                        },
                        immutable=True,
                    )
                return None if pending else 0

            def wait(inner, timeout=None):
                result = inner.poll()
                if result is None:
                    raise TimeoutError("Synthetic cohort still running")
                return result

            def terminate(inner):
                for _, _, child in children:
                    if child.poll() is None:
                        child.terminate()

            def kill(inner):
                for _, _, child in children:
                    if child.poll() is None:
                        child.kill()

        return CohortChild()

    def worker(self, folder, lock_fd):
        self.events.append(("worker", folder.name))
        self.worker_count += 1
        worker_index = self.worker_count
        self.accepted.set()
        if self.fail_next:
            self.fail_next = False
            if self.partial_fail:
                self.asr_calls += 1
                (folder / "transcription.json").write_text(
                    json.dumps({"segments": [{"start": 0, "end": 3, "text": "Hej København."}]}),
                    encoding="utf-8",
                )
            (folder / "error.json").write_text(json.dumps({"code": "processing"}))

            class FailedChild(FinishedChild):
                def poll(self):
                    return 1

            return FailedChild()
        for name in json.loads((folder / "inputs.json").read_bytes())["expected_outputs"]:
            if (folder / name).exists():
                continue
            if name == "transcription.json":
                self.asr_calls += 1
            value = (
                {"segments": [{"start": 0, "end": 3, "text": "Hej København."}]}
                if name == "transcription.json"
                else {"passages": [{"id": "0", "words": []}]}
            )
            (folder / name).write_text(json.dumps(value), encoding="utf-8")
        if self.hold_worker:

            class WaitingChild(FinishedChild):
                def poll(inner):
                    return None if self.hold_worker and worker_index > self.release_until else 0

                def terminate(inner):
                    self.hold_worker = False

                def kill(inner):
                    self.hold_worker = False

            return WaitingChild()
        return FinishedChild()

    def retrieve_job(self, identifier):
        controller = self.controllers[identifier]
        status = controller.step()
        if controller.terminal:
            self.states[identifier] = "SUCCESS" if status["phase"] == "finished" else "FAILURE"
        return {
            "id": identifier,
            "specification": self.submissions[int(identifier) - 1],
            "status": {"state": self.states[identifier]},
        }

    def terminate_job(self, identifier):
        self.terminations.append(identifier)
        self.hold_worker = False
        self.controllers[identifier].step()
        self.states[identifier] = "CANCELLED"

    def browse_jobs(self, **kwargs):
        return {
            "items": [
                {"id": str(i + 1), "specification": spec} for i, spec in enumerate(self.submissions)
            ]
        }

    def read(self, client, server, path, limit=api.MAX_STATUS_BYTES):
        local = self.path(path)
        if not local.exists():
            return None
        data = local.read_bytes()
        if self.corrupt and path.endswith("alignment.json"):
            data += b" "
        if len(data) > limit:
            raise ValueError("Synthetic oversized remote file.")
        return data

    def trash(self, client, path):
        self.trashed.append(path)


@pytest.fixture
def environment(tmp_path, monkeypatch, credentials):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic runtime")
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://runtime.invalid",
        model="large-v3",
        ucloud_transcriber={
            "project_id": "synthetic",
            "storage_path": "/drive/cbs",
            "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            "cleanup_after_success": True,
        },
    )
    pid = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (pid, "Synthetic", settings.model_dump_json(), now()),
    )
    hashes = batches.code_hashes()
    monkeypatch.setattr(batches, "code_hashes", lambda: hashes)
    plan = {
        "identity": {
            "protocol": batches.PROTOCOL,
            "application": {"name": "terminal-ubuntu", "version": "synthetic"},
            "code_hashes": hashes,
            "recipe_hash": api.requirements_hash(),
            "model_revisions": {k: list(v) for k, v in api.MODELS.items()},
            "limits": {**batches.LIMITS, "idle_grace_seconds": 0, "cohort_wait_seconds": 0},
        }
    }
    params = {
        "transcription": True,
        "provider_id": pid,
        "enhancement": enhancement.EnhancementOptions(
            execution="ucloud", device="cuda", transcribe=True
        ).model_dump(),
        "ucloud_api": {
            "provider_id": pid,
            "settings": settings.model_dump(),
            "runtime_id": api.runtime_id(),
            "runtime_product": MIG_PRODUCT,
            "runtime_compute_policy": "mig-v1",
            "batch_plan": plan,
        },
    }
    drive = Drive(tmp_path / "remote")
    drive.errors = []
    settle = batches.Coordinator.settle_item

    def captured(owner, task_id, error=None):
        if error:
            drive.errors.append(str(error))
        return settle(owner, task_id, error)

    monkeypatch.setattr(batches.Coordinator, "settle_item", captured)
    monkeypatch.setattr(api, "read_remote", drive.read)
    monkeypatch.setattr(api, "stamped", lambda *_: True)
    monkeypatch.setattr(api, "listed", lambda _, path: drive.path(path).exists())
    monkeypatch.setattr(batches.native, "require_setup", lambda *_: None)
    monkeypatch.setattr(batches.native, "upload", drive.upload)
    monkeypatch.setattr(batches.native, "trash_owned_folder", drive.trash)

    @contextmanager
    def client(*_):
        yield drive

    monkeypatch.setattr(batches.native, "client_for", client)
    monkeypatch.setattr(batches, "POLL_SECONDS", 0.005)
    # Keep the production heartbeat cadence: 10 ms renewal repeatedly runs the real file
    # controller (including Windows ancestry checks) during every guarded mock upload.
    # The long-transfer test advances a fake clock explicitly to exercise renewal.

    def pipeline(child, job, recording):
        drive.events.append(("prepare", job["id"]))
        folder = store.directory(recording["id"]) / "enhancements" / job["id"]
        folder.mkdir(parents=True, exist_ok=True)
        options = enhancement.EnhancementOptions.model_validate(job["params"]["enhancement"])
        request = {
            "passages": [],
            "options": options.model_dump(exclude={"runtime_python", "prepare_runtime"})
            | {"duration": 3, "whole_recording": False},
        }
        batches.durable_write(folder / "request.json", batches.json_bytes(request))
        batches.durable_write(folder / "audio.wav", b"synthetic audio")

        def validate():
            for name in batches._expected(options):
                assert isinstance(json.loads((folder / name).read_bytes()), dict)

        child.runtime_dispatch(job, recording, folder, options, validate)
        drive.events.append(("save", job["id"]))
        store.add_result(
            recording["id"],
            normalize_segments([{"start": 0, "end": 3, "text": "Hej København."}]),
            {},
            "Synthetic",
            {"kind": "provider"},
            job_id=job["id"],
            runtime_receipt=job["params"]["runtime_output_receipt"],
        )

    def captured_pipeline(child, job, recording):
        try:
            return pipeline(child, job, recording)
        except BaseException as exc:
            drive.events.append(("child_error", job["id"], type(exc).__name__, str(exc)))
            raise

    prepare_retry = batches.Coordinator.prepare_retry

    def captured_retry(owner, member):
        try:
            return prepare_retry(owner, member)
        except (OSError, ValueError) as exc:
            drive.events.append(("retry_preparation_error", member["job_id"], str(exc)))
            raise

    monkeypatch.setattr(enhancement, "run_enhancement", captured_pipeline)
    monkeypatch.setattr(batches.Coordinator, "prepare_retry", captured_retry)
    owner_loop = batches.Coordinator.loop

    def captured_loop(owner, executor):
        try:
            return owner_loop(owner, executor)
        except BaseException as exc:
            drive.events.append(("owner_error", type(exc).__name__, str(exc)))
            raise

    monkeypatch.setattr(batches.Coordinator, "loop", captured_loop)
    worker = SimpleNamespace(
        store=store,
        credentials=credentials,
        stopping=threading.Event(),
        cancel_event=threading.Event(),
        lock=threading.RLock(),
        runtime_preparation_gate=threading.BoundedSemaphore(2),
        runtime_transfer_gate=threading.BoundedSemaphore(2),
    )

    def enqueue(overrides=None):
        recording = store.create_recording(project["id"], "synthetic.wav")
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
        value = copy.deepcopy(params)
        if overrides:
            value["enhancement"].update(overrides)
        return store.add_job(recording["id"], "enhance", value)

    threads, start = [], threading.Thread.start

    def track_start(thread):
        if thread._target is batches.run:
            threads.append(thread)
        return start(thread)

    monkeypatch.setattr(threading.Thread, "start", track_start)
    try:
        yield SimpleNamespace(
            store=store, worker=worker, drive=drive, enqueue=enqueue, params=params
        )
    finally:
        worker.stopping.set()
        drive.hold_worker = False
        for thread in threads:
            thread.join(8)
            assert not thread.is_alive(), "Coordinator outlived its mocked transport."


def run_batch(env):
    batch = runtime_store.claim(env.store)
    assert batch
    batches.run(env.worker, batch)
    return runtime_store.get(env.store, batch["id"])


def batch_diagnostics(env, identifier):
    """Synthetic-only failure context distinguishes a stuck fixture from a failed task."""
    batch = runtime_store.get(env.store, identifier)
    return json.dumps(
        {
            "owner": {k: batch[k] for k in ("state", "admission", "message", "epoch")},
            "remote": {
                k: batch["journal"].get(k) for k in ("remote_state", "accepting", "cleanup_state")
            },
            "members": [
                {
                    k: m[k]
                    for k in (
                        "job_id",
                        "state",
                        "attempt",
                        "task_id",
                        "attempt_state",
                        "attempt_error_code",
                    )
                }
                | {
                    "input_flags": {
                        k: m["input_receipt"].get(k) for k in ("publish_intent", "published")
                    },
                    "remote_flags": {
                        k: m["remote_receipt"].get(k)
                        for k in ("accepted", "outcome", "worker_gone")
                    },
                }
                for m in batch["members"]
            ],
            "jobs": [
                {k: env.store.job(m["job_id"])[k] for k in ("id", "state", "message")}
                for m in batch["members"]
            ],
            "errors": list(env.drive.errors),
            "events": env.drive.events[-24:],
            "remote_phases": {
                identifier: controller.phase
                for identifier, controller in env.drive.controllers.items()
            },
            "workers": env.drive.worker_count,
            "thread_stacks": {
                thread.name: [
                    (Path(row.filename).name, row.name, row.lineno)
                    for row in traceback.extract_stack(frame, limit=6)
                ]
                for thread in threading.enumerate()
                if ("runtime-member" in thread.name or thread.name.endswith("(run)"))
                and (frame := sys._current_frames().get(thread.ident)) is not None
            },
        },
        indent=2,
    )


def test_selected_files_one_post_independent_results_durable_then_trash(environment):
    env = environment
    jobs = [env.enqueue() for _ in range(3)]
    batch = run_batch(env)
    assert batch["state"] == "completed", (batch["message"], env.drive.errors)
    assert len(env.drive.submissions) == 1
    assert len(env.store.all("SELECT id FROM transcript_results")) == 3
    assert {env.store.job(j["id"])["state"] for j in jobs} == {"completed"}
    assert batch["journal"]["cleanup_state"] == "trashed"
    assert env.drive.trashed == [batches.remote_folder(batches._settings(batch), batch["id"])]
    assert all(m["output_receipt"]["epoch"] == batch["epoch"] for m in batch["members"])
    assert len({thread for _, thread in env.drive.uploads}) == 1
    for member in batch["members"]:
        assert {"transcription.json", "alignment.json"} <= member["output_receipt"]["files"].keys()


def test_late_file_joins_running_allocation_and_seal(environment):
    env = environment
    first = env.enqueue()
    env.drive.hold_worker = True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    assert env.drive.accepted.wait(8)
    second = env.enqueue()
    assert second["params"]["runtime_batch_id"] == first["params"]["runtime_batch_id"]
    env.drive.hold_worker = False
    thread.join(10)
    assert not thread.is_alive()
    batch = runtime_store.get(env.store, claimed["id"])
    assert batch["state"] == "completed", batch
    assert len(env.drive.submissions) == 1
    assert len(batch["members"]) == 2


def test_different_settings_allocate_separately_and_capacity_is_bounded(environment):
    env = environment
    first, second, third = (
        env.enqueue(),
        env.enqueue({"min_score": 0.4}),
        env.enqueue({"min_score": 0.5}),
    )
    assert len({j["params"]["runtime_batch_id"] for j in (first, second, third)}) == 3
    assert runtime_store.claim(env.store)
    assert runtime_store.claim(env.store)
    assert runtime_store.claim(env.store) is None


def test_uncertain_submit_no_second_post_and_exact_discovery_reconnects(environment):
    env = environment
    env.enqueue()
    env.drive.lose_submit = True
    batch = run_batch(env)
    assert len(env.drive.submissions) == 1
    assert runtime_store._uncertain(batch["journal"])
    assert runtime_store.claim(env.store) is None
    with pytest.raises(ValueError, match="Reconcile"):
        batches.retry(env.store, env.worker.credentials, batch["id"])
    batches.reconcile(env.store, env.worker.credentials, batch["id"])
    assert runtime_store.get(env.store, batch["id"])["journal"]["job_id"] == "1"
    assert len(env.drive.submissions) == 1


def test_readiness_failure_stops_before_private_upload_and_retains_owner(environment):
    env = environment
    env.enqueue()
    env.drive.vram = 0
    batch = run_batch(env)
    assert batch["state"] == "failed"
    assert env.drive.terminations == ["1"]
    assert not any(
        target.endswith(("audio.wav", "request.json")) for target, _ in env.drive.uploads
    )
    assert not env.drive.trashed
    assert all(env.store.job(m["job_id"])["state"] != "running" for m in batch["members"])


def test_hash_mismatch_never_commits_or_trashes_shared_output(environment):
    env = environment
    env.enqueue()
    env.drive.corrupt = True
    batch = run_batch(env)
    assert batch["state"] == "failed"
    assert not env.store.all("SELECT id FROM transcript_results")
    assert not env.drive.trashed
    with pytest.raises(ValueError, match="Save every"):
        batches.retry_cleanup(env.store, env.worker.credentials, batch["id"])


def test_shutdown_preserves_owned_job_and_same_task_ids_for_explicit_recovery(environment):
    env = environment
    job = env.enqueue()
    env.drive.hold_worker = True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    assert env.drive.accepted.wait(8)
    env.worker.stopping.set()
    thread.join(10)
    assert not thread.is_alive()
    interrupted = runtime_store.get(env.store, claimed["id"])
    assert interrupted["state"] == "interrupted"
    task_id = interrupted["members"][0]["task_id"]
    assert not env.drive.terminations
    env.worker.stopping.clear()
    env.drive.hold_worker = False
    batches.retry(env.store, env.worker.credentials, interrupted["id"])
    resumed = runtime_store.claim(env.store)
    assert resumed and resumed["members"][0]["task_id"] == task_id
    batches.run(env.worker, resumed)
    assert env.store.job(job["id"])["state"] == "completed"
    assert len(env.drive.submissions) == 1


def test_activity_and_obligations_are_owned_independent_of_job_horizon(environment):
    env = environment
    job = env.enqueue()
    owner = batches.activity(env.store)[0]
    assert owner["runtime_batch"]["members"][0]["job_id"] == job["id"]
    assert owner["runtime_batch"]["unresolved"] is True
    assert "settings" not in owner and "identity" not in owner
    with pytest.raises(ValueError, match="CBS Runtime"):
        batches.require_no_obligations(env.store, recording_ids=[job["recording_id"]])


def test_lock_requires_regular_file_and_never_replaces_existing_inode(environment, tmp_path):
    env = environment
    settings = providers.Settings.model_validate(env.params["ucloud_api"]["settings"])
    target = env.drive.path(api.runtime_folder(settings) + "/.in-use")
    target.mkdir(parents=True)
    with pytest.raises(ValueError, match="regular file"):
        batches.ensure_use_lock(env.drive, settings, tmp_path, lambda: None)
    assert not env.drive.uploads


def wait_for(predicate, timeout=15, diagnostic=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), (
        diagnostic()
        if diagnostic
        else "Synthetic coordinator did not reach its expected checkpoint."
    )


def test_stop_harvests_output_completed_during_termination(environment):
    env = environment
    job = env.enqueue()
    env.drive.hold_worker = True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    assert env.drive.accepted.wait(8)
    batches.cancel(env.store, env.worker.credentials, claimed["id"])
    thread.join(10)
    assert not thread.is_alive()
    assert env.store.job(job["id"])["state"] == "completed"
    assert len(env.store.all("SELECT id FROM transcript_results")) == 1
    assert env.drive.terminations == ["1"]


def test_verified_local_outputs_finalize_without_readiness_or_private_upload(
    environment, monkeypatch
):
    env = environment
    job = env.enqueue()
    original = env.store.add_result
    monkeypatch.setattr(
        env.store,
        "add_result",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("Synthetic local commit failure")),
    )
    failed = run_batch(env)
    assert failed["members"][0]["attempt_state"] == "complete"
    assert env.store.job(job["id"])["params"]["runtime_output_receipt"]
    monkeypatch.setattr(env.store, "add_result", original)
    private_count = sum(
        path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads
    )
    retrieve = env.drive.retrieve_job
    calls = [0]

    def lagging(identifier):
        value = retrieve(identifier)
        calls[0] += 1
        if calls[0] <= 3:
            value["status"]["state"] = "RUNNING"
        return value

    monkeypatch.setattr(env.drive, "retrieve_job", lagging)
    monkeypatch.setattr(
        batches.Coordinator,
        "readiness",
        lambda _: (_ for _ in ()).throw(AssertionError("Recovery must not run doctor")),
    )
    batches.retry(env.store, env.worker.credentials, failed["id"])
    batches.run(env.worker, runtime_store.claim(env.store))
    assert env.store.job(job["id"])["state"] == "completed"
    assert len(env.drive.submissions) == 1
    assert (
        sum(path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads)
        == private_count
    )


def test_remote_draining_closes_local_admission_before_late_attach(environment):
    env = environment
    first = env.enqueue()
    claimed = runtime_store.claim(env.store)
    owner = batches.Coordinator(env.worker, claimed)
    owner.state, owner.status = "RUNNING", {"phase": "draining"}
    owner.project_status()
    second = env.enqueue()
    assert second["params"]["runtime_batch_id"] != first["params"]["runtime_batch_id"]
    assert not env.drive.uploads


def test_long_upload_renews_heartbeat_and_checks_approval_per_chunk(environment, monkeypatch):
    env = environment
    env.enqueue()
    owner = batches.Coordinator(env.worker, runtime_store.claim(env.store))
    owner.client = env.drive
    owner.submit()
    clock = [100.0]
    monkeypatch.setattr(batches.time, "monotonic", lambda: clock[0])
    upload = env.drive.upload

    def chunks(client, server, path, target, guard, update, **kwargs):
        if target.endswith("audio.wav"):
            for _ in range(4):
                clock[0] += 16
                guard()
        return upload(client, server, path, target, guard, update, **kwargs)

    monkeypatch.setattr(batches.native, "upload", chunks)
    path = owner.folder / "audio.wav"
    path.write_bytes(b"synthetic")
    owner.upload(path, owner.remote + "/audio.wav", private=True)
    assert sum(target.endswith("heartbeat.json") for target, _ in env.drive.uploads) >= 4


def test_explicit_failed_task_retry_joins_same_live_allocation(environment):
    env = environment
    first, second = env.enqueue(), env.enqueue()
    env.drive.fail_next, env.drive.hold_worker = True, True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    try:
        wait_for(lambda: batches._retryable(runtime_store.get(env.store, claimed["id"])))
    except AssertionError:
        env.worker.stopping.set()
        thread.join(8)
        current = runtime_store.get(env.store, claimed["id"])
        raise AssertionError(
            (
                current["state"],
                current["admission"],
                current["message"],
                current["journal"],
                current["members"],
                env.drive.errors,
            )
        ) from None
    before = batches._retryable(runtime_store.get(env.store, claimed["id"]))[0]
    assert batches.activity(env.store)[0]["runtime_batch"]["retryable_members"] == 1, (
        runtime_store.get(env.store, claimed["id"]),
        env.drive.errors,
    )
    batches.retry(env.store, env.worker.credentials, claimed["id"])
    after = next(
        m
        for m in runtime_store.get(env.store, claimed["id"])["members"]
        if m["job_id"] == before["job_id"]
    )
    assert after["attempt"] == 2 and after["task_id"] != before["task_id"]
    env.drive.hold_worker = False
    thread.join(10)
    assert not thread.is_alive()
    assert [env.store.job(j["id"])["state"] for j in (first, second)] == [
        "completed",
        "completed",
    ], batch_diagnostics(env, claimed["id"])
    assert len(env.drive.submissions) == 1


def test_public_script_failure_can_cancel_and_cleanup_without_allocating(environment, monkeypatch):
    env = environment
    env.enqueue()
    upload = env.drive.upload

    def fail_script(client, server, path, target, guard, update, **kwargs):
        if target.endswith("ucloud_supervisor.sh"):
            raise OSError("Synthetic script upload failure")
        return upload(client, server, path, target, guard, update, **kwargs)

    monkeypatch.setattr(batches.native, "upload", fail_script)
    failed = run_batch(env)
    assert failed["journal"].get("owned_folder")
    assert not env.drive.submissions
    assert batches.activity(env.store)[0]["runtime_batch"]["can_cancel"]
    batches.cancel(env.store, env.worker.credentials, failed["id"])
    assert runtime_store.get(env.store, failed["id"])["journal"]["cleanup_state"] == "trashed"
    assert not runtime_store.obligations(env.store)


def test_legacy_stop_acknowledgement_does_not_allow_destructive_preparation(
    environment, monkeypatch
):
    env = environment
    job = env.enqueue()
    params = copy.deepcopy(env.params)
    del params["ucloud_api"]["batch_plan"]
    params["ucloud_run"] = {
        "job_id": "legacy",
        "remote_state": "RUNNING",
        "termination_requested": True,
    }
    legacy = env.store.add_job(job["recording_id"], "enhance", params)
    settings = providers.Settings.model_validate(params["ucloud_api"]["settings"])
    monkeypatch.setattr(api, "stamped", lambda *_: False)
    with pytest.raises(ValueError, match="older UCloud"):
        batches.require_preparation_idle(env.store, settings, env.drive)
    params["ucloud_run"]["remote_state"] = "SUCCESS"
    env.store.update_job(legacy["id"], params=params)
    batches.require_preparation_idle(env.store, settings, env.drive)


def test_failed_alignment_retry_reuses_verified_asr_checkpoint(environment):
    env = environment
    jobs = [env.enqueue(), env.enqueue()]
    env.drive.fail_next, env.drive.partial_fail, env.drive.hold_worker = True, True, True
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    try:
        wait_for(
            lambda: batches._retryable(runtime_store.get(env.store, claimed["id"])),
            diagnostic=lambda: batch_diagnostics(env, claimed["id"]),
        )
        before = batches._retryable(runtime_store.get(env.store, claimed["id"]))[0]
        assert set(before["remote_receipt"]["files"]) == {"transcription.json"}
        batches.retry(env.store, env.worker.credentials, claimed["id"])
        env.drive.hold_worker = False
        thread.join(15)
        assert not thread.is_alive(), batch_diagnostics(env, claimed["id"])
        assert [env.store.job(j["id"])["state"] for j in jobs] == ["completed", "completed"], (
            batch_diagnostics(env, claimed["id"])
        )
        after = next(
            m
            for m in runtime_store.get(env.store, claimed["id"])["members"]
            if m["job_id"] == before["job_id"]
        )
        assert after["input_receipt"]["carried_checkpoints"] == before["remote_receipt"]["files"]
        task_path = "/tasks/" + after["task_id"] + "/transcription.json"
        assert any(path.endswith(task_path) for path, _ in env.drive.uploads)
        assert env.drive.asr_calls == 2
        assert len(env.drive.submissions) == 1
    finally:
        env.worker.stopping.set()
        env.drive.hold_worker = False
        thread.join(8)


@pytest.mark.parametrize("accepted", [False, True])
def test_publication_commit_recovery_never_rewrites_inputs(environment, accepted):
    env = environment
    env.enqueue()
    owner = batches.Coordinator(env.worker, runtime_store.claim(env.store))
    owner.client = env.drive
    member = owner.batch["members"][0]
    binding = {
        "protocol": batches.PROTOCOL,
        "batch_id": owner.id,
        "identity_hash": owner.batch["compatibility_hash"],
        "task_id": member["task_id"],
        "job_id": member["job_id"],
        "attempt": member["attempt"],
    }
    files = {name: {"size": 3, "sha256": "a" * 64} for name in ("request.json", "audio.wav")}
    raw = batches.json_bytes(
        binding | {"files": files, "expected_outputs": batches._expected(owner.options)}
    )
    digest = batches.hashlib.sha256(raw).hexdigest()
    folder = env.drive.path(owner.remote + "/tasks/" + member["task_id"])
    folder.mkdir(parents=True)
    (folder / "inputs.json").write_bytes(raw)
    (folder / "ready.json").write_bytes(
        batches.json_bytes(
            {
                "protocol": batches.PROTOCOL,
                "batch_id": owner.id,
                "task_id": member["task_id"],
                "inputs_sha256": digest,
                "inputs_size": len(raw),
            }
        )
    )
    if accepted:
        (folder / "accepted.json").write_bytes(
            batches.json_bytes(binding | {"inputs_sha256": digest, "inputs_size": len(raw)})
        )
    owner.task_checkpoint(
        member,
        state="publishing",
        input_update={
            "files": files,
            "manifest_sha256": digest,
            "publish_intent": True,
        },
    )
    assert owner.publication(owner.member(member["job_id"])) is True
    assert owner.member(member["job_id"])["input_receipt"]["published"] is True
    assert not env.drive.uploads


def test_stop_cancels_unpublished_local_preparation(environment, monkeypatch):
    env = environment
    env.enqueue()
    preparing = threading.Event()

    def blocked(child, job, recording):
        preparing.set()
        assert child.cancel_event.wait(10), "Stop did not cancel unpublished preparation."
        raise batches.Cancelled()

    monkeypatch.setattr(enhancement, "run_enhancement", blocked)
    claimed = runtime_store.claim(env.store)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    try:
        assert preparing.wait(8)
        batches.cancel(env.store, env.worker.credentials, claimed["id"])
        thread.join(10)
        assert not thread.is_alive()
        assert runtime_store.get(env.store, claimed["id"])["members"][0]["state"] == "cancelled"
        assert not any(
            path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads
        )
        retrieve = env.drive.retrieve_job

        def still_running(identifier):
            value = retrieve(identifier)
            value["status"]["state"] = "RUNNING"
            return value

        monkeypatch.setattr(env.drive, "retrieve_job", still_running)
        with pytest.raises(ValueError, match="still writing"):
            batches.retry_cleanup(env.store, env.worker.credentials, claimed["id"])
        assert not env.drive.trashed
        assert batches.activity(env.store)[0]["runtime_batch"]["can_cleanup"]
        monkeypatch.setattr(env.drive, "retrieve_job", retrieve)
        batches.retry_cleanup(env.store, env.worker.credentials, claimed["id"])
        assert env.drive.trashed
        assert not runtime_store.obligations(env.store)
    finally:
        env.worker.stopping.set()
        thread.join(8)


def test_prior_private_attempt_prevents_public_only_cleanup_after_unpublished_retry(environment):
    env = environment
    env.enqueue()
    batch = runtime_store.claim(env.store)
    member = batch["members"][0]
    runtime_store.checkpoint(
        env.store,
        batch["id"],
        batch["epoch"],
        journal_update={
            "job_id": "1",
            "remote_state": "RUNNING",
            "readiness_generation": batch["id"],
            "owned_folder": batches.remote_folder(batches._settings(batch), batch["id"]),
            "folder_created": True,
        },
    )
    runtime_store.checkpoint_attempt(
        env.store,
        batch["id"],
        member["job_id"],
        batch["epoch"],
        member["task_id"],
        state="failed",
        member_state="failed",
        input_update={"publish_intent": True},
        remote_update={"outcome": "failed", "worker_gone": True},
    )
    runtime_store.retry_member(
        env.store, batch["id"], member["job_id"], batch["epoch"], task_id=member["task_id"]
    )
    runtime_store.stop(env.store, batch["id"])
    stopped = runtime_store.checkpoint(
        env.store,
        batch["id"],
        batch["epoch"],
        state="cancelled",
        journal_update={"remote_state": "SUCCESS"},
    )
    assert stopped["members"][0]["input_receipt"] == {"carried_checkpoints": {}}
    assert batches._public_only(env.store, stopped) is False
    with pytest.raises(ValueError, match="Save every"):
        batches.retry_cleanup(env.store, env.worker.credentials, batch["id"])
    assert not env.drive.trashed


def test_refused_cleanup_cannot_create_an_obligation_for_a_never_submitted_batch(environment):
    env = environment
    job = env.enqueue()
    batch_id = job["params"]["runtime_batch_id"]
    with pytest.raises(ValueError, match="closed owned"):
        batches.retry_cleanup(env.store, env.worker.credentials, batch_id)
    assert "cleanup_state" not in runtime_store.get(env.store, batch_id)["journal"]
    batches.cancel(env.store, env.worker.credentials, batch_id)
    assert not runtime_store.obligations(env.store)
    assert not env.drive.submissions and not env.drive.trashed


def test_live_owner_recovers_local_result_without_new_task_or_upload(environment, monkeypatch):
    env = environment
    env.enqueue()
    env.enqueue()
    env.drive.hold_worker = True
    claimed = runtime_store.claim(env.store)
    original = env.store.add_result
    failed_once = [False]

    def fail_commit(*args, **kwargs):
        if not failed_once[0]:
            failed_once[0] = True
            raise ValueError("Synthetic local result failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(env.store, "add_result", fail_commit)
    thread = threading.Thread(target=batches.run, args=(env.worker, claimed))
    thread.start()
    try:
        assert env.drive.accepted.wait(8), batch_diagnostics(env, claimed["id"])
        env.drive.release_until = 1
        wait_for(
            lambda: batches._local_recoverable(
                env.store, runtime_store.get(env.store, claimed["id"])
            ),
            diagnostic=lambda: batch_diagnostics(env, claimed["id"]),
        )
        env.drive.hold_worker = True
        before = batches._local_recoverable(env.store, runtime_store.get(env.store, claimed["id"]))[
            0
        ]
        private_count = sum(
            path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads
        )
        batches.retry(env.store, env.worker.credentials, claimed["id"])
        wait_for(lambda: env.store.job(before["job_id"])["state"] == "completed")
        after = next(
            m
            for m in runtime_store.get(env.store, claimed["id"])["members"]
            if m["job_id"] == before["job_id"]
        )
        assert after["task_id"] == before["task_id"] and after["attempt"] == before["attempt"]
        assert (
            sum(path.endswith(("audio.wav", "request.json")) for path, _ in env.drive.uploads)
            == private_count
        )
        env.drive.hold_worker = False
        thread.join(15)
        assert not thread.is_alive(), batch_diagnostics(env, claimed["id"])
        assert all(
            m["state"] == "completed"
            for m in runtime_store.get(env.store, claimed["id"])["members"]
        ), batch_diagnostics(env, claimed["id"])
    finally:
        env.worker.stopping.set()
        env.drive.hold_worker = False
        thread.join(8)
