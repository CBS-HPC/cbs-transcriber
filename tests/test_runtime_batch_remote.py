"""Synthetic file protocol acceptance; no inference runtime, Bash, GPU or provider."""

import hashlib
import json
import os
import subprocess
import uuid

import pytest

from cbs_transcriber import runtime_batch_remote as remote


def identifier():
    return uuid.uuid4().hex


def read(path):
    return json.loads(path.read_bytes())


class Child:
    def __init__(self):
        self.returncode = None
        self.terminated = self.killed = self.reaped = False
        self.ignore_terminate = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic", timeout)
        self.reaped = True
        return self.returncode


class Harness:
    def __init__(self, directory):
        self.directory = directory
        self.batch_id = identifier()
        self.now = 0
        self.children, self.started, self.rows = [], [], []
        self.revision = 0
        self.manifest = {
            "protocol": remote.PROTOCOL,
            "batch_id": self.batch_id,
            "identity_hash": "1" * 64,
            "code_hashes": {"enhancement_worker.py": "2" * 64},
            "limits": {"max_members": 100, "gpu_concurrency": 1, "owner_absence_seconds": 300},
        }
        remote.write_json(directory / "batch.json", self.manifest)
        self.controller = self.engine()

    def engine(self):
        return remote.BatchController(self.directory, runner=self.run, clock=lambda: self.now)

    def run(self, folder, lock_fd):
        assert read(folder / "accepted.json")["task_id"] == folder.name
        assert not self.children or self.children[-1].reaped
        self.started.append(folder.name)
        child = Child()
        self.children.append(child)
        return child

    def enable_resident(self):
        self.manifest["limits"].update(
            resident_models="single-slot-v1", cohort_max_members=8, cohort_wait_seconds=2
        )
        remote.write_json(self.directory / "batch.json", self.manifest)

        def run_cohort(folder, lock_fd):
            plan = read(folder / "cohort.json")
            assert all(
                read(self.directory / "tasks" / row["task_id"] / "accepted.json") == row
                for row in plan["tasks"]
            )
            assert not self.children or self.children[-1].reaped
            self.started.append([row["task_id"] for row in plan["tasks"]])
            child = Child()
            self.children.append(child)
            return child

        self.controller = remote.BatchController(
            self.directory, runner=self.run, cohort_runner=run_cohort, clock=lambda: self.now
        )

    def resident_done(self, row):
        folder = self.folder(row)
        remote.write_json(folder / "alignment.json", {"passages": []})
        remote.write_json(
            folder / "runtime-complete.json",
            {
                "protocol": remote.PROTOCOL,
                "batch_id": self.batch_id,
                "cohort_id": self.controller.cohort_id,
                "task_id": row["task_id"],
                "inputs_sha256": read(folder / "accepted.json")["inputs_sha256"],
                "error_code": None,
            },
            immutable=True,
        )

    def reserve(self, *, job_id=None, attempt=1, ordinal=None, disposition="reserved"):
        row = {
            "task_id": identifier(),
            "job_id": job_id or identifier(),
            "attempt": attempt,
            "ordinal": len({r["job_id"] for r in self.rows}) if ordinal is None else ordinal,
            "disposition": disposition,
        }
        self.rows.append(row)
        self.membership()
        return row

    def membership(self):
        self.revision += 1
        remote.write_json(
            self.directory / "membership.json",
            {
                "protocol": remote.PROTOCOL,
                "batch_id": self.batch_id,
                "revision": self.revision,
                "tasks": self.rows,
                "current_attempts": {row["job_id"]: row["task_id"] for row in self.rows},
            },
        )

    def folder(self, row):
        return self.directory / "tasks" / row["task_id"]

    def publish(self, row, *, request=None):
        folder = self.folder(row)
        folder.mkdir(parents=True, exist_ok=True)
        request = request or {
            "options": {
                "execution": "ucloud",
                "device": "cuda",
                "language": "da",
                "align": True,
                "diarize": False,
                "allow_downloads": False,
                "duration": 2,
                "timeout_minutes": 10,
            },
            "passages": [{"id": "1", "text": "Synthetic æøå", "start": 0, "end": 2}],
        }
        remote.write_json(folder / "request.json", request)
        (folder / "audio.wav").write_bytes(b"synthetic audio")
        inputs = {
            "protocol": remote.PROTOCOL,
            "batch_id": self.batch_id,
            "identity_hash": self.manifest["identity_hash"],
            **{key: row[key] for key in ("task_id", "job_id", "attempt")},
            "files": {
                name: remote.file_receipt(folder / name, remote.OUTPUT_LIMIT)
                for name in ("request.json", "audio.wav")
            },
            "expected_outputs": [
                name + ".json"
                for flag, name in (
                    ("align", "alignment"),
                    ("diarize", "diarization"),
                    ("transcribe", "transcription"),
                    ("retranscribe", "retranscription"),
                )
                if request["options"].get(flag)
            ],
        }
        remote.write_json(folder / "inputs.json", inputs)
        self.ready(row)

    def ready(self, row):
        folder = self.folder(row)
        raw = (folder / "inputs.json").read_bytes()
        remote.write_json(
            folder / "ready.json",
            {
                "protocol": remote.PROTOCOL,
                "batch_id": self.batch_id,
                "task_id": row["task_id"],
                "inputs_sha256": hashlib.sha256(raw).hexdigest(),
                "inputs_size": len(raw),
            },
        )

    def finish(self, row, result=0):
        folder = self.folder(row)
        if result == 0:
            remote.write_json(folder / "alignment.json", {"passages": []})
        self.children[-1].returncode = result
        return self.controller.step()

    def seal(self):
        raw = (self.directory / "membership.json").read_bytes()
        remote.write_json(
            self.directory / "seal.json",
            {
                "protocol": remote.PROTOCOL,
                "batch_id": self.batch_id,
                "revision": self.revision,
                "membership_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )

    def heartbeat(self, epoch, revision):
        remote.write_json(
            self.directory / "heartbeat.json",
            {
                "protocol": remote.PROTOCOL,
                "batch_id": self.batch_id,
                "epoch": epoch,
                "revision": revision,
            },
        )

    def stop(self):
        remote.write_json(
            self.directory / "stop.json",
            {
                "protocol": remote.PROTOCOL,
                "batch_id": self.batch_id,
                "revision": 1,
            },
        )


@pytest.fixture
def batch(tmp_path):
    return Harness(tmp_path)


def test_resident_cohort_is_finite_and_late_acceptance_runs_in_next_child(batch):
    batch.enable_resident()
    first, second = batch.reserve(), batch.reserve()
    for row in (first, second):
        batch.publish(row)
        batch.controller.step()
    assert not batch.started
    batch.now = 2
    batch.controller.step()
    assert batch.started == [[first["task_id"], second["task_id"]]]
    late = batch.reserve()
    batch.publish(late)
    batch.controller.step()
    batch.resident_done(first)
    batch.resident_done(second)
    batch.children[-1].returncode = 0
    batch.controller.step()
    assert batch.children[-1].reaped
    assert len(batch.started) == 1
    batch.now = 4
    batch.controller.step()
    assert batch.started[-1] == [late["task_id"]]
    assert all(
        read(batch.folder(row) / "outcome.json")["state"] == "complete" for row in (first, second)
    )


def test_resident_stop_preserves_done_task_and_partial_sibling_without_replay(batch):
    batch.enable_resident()
    complete, partial = batch.reserve(), batch.reserve()
    for row in (complete, partial):
        batch.publish(row)
        batch.controller.step()
    batch.now = 2
    batch.controller.step()
    batch.resident_done(complete)
    remote.write_json(batch.folder(partial) / "alignment.json", {"passages": []})
    batch.stop()
    assert batch.controller.step()["phase"] == "stopped"
    child = batch.children[-1]
    assert child.terminated and child.reaped
    assert read(batch.folder(complete) / "outcome.json")["state"] == "complete"
    outcome = read(batch.folder(partial) / "outcome.json")
    assert outcome["state"] == "interrupted" and "alignment.json" in outcome["outputs"]
    recovered = batch.engine()
    assert recovered.step()["phase"] == "stopped"
    assert len(batch.started) == 1


def test_resident_child_crash_preserves_finished_member_and_restart_never_repeats(batch):
    batch.enable_resident()
    complete, uncertain = batch.reserve(), batch.reserve()
    for row in (complete, uncertain):
        batch.publish(row)
        batch.controller.step()
    batch.now = 2
    batch.controller.step()
    batch.resident_done(complete)
    batch.children[-1].returncode = -9
    batch.controller.step()
    assert read(batch.folder(complete) / "outcome.json")["state"] == "complete"
    assert read(batch.folder(uncertain) / "outcome.json")["state"] == "failed"
    batch.controller = batch.engine()
    batch.controller.step()
    assert len(batch.started) == 1


def test_resident_restart_before_any_outcome_keeps_all_accepted_tasks_interrupted(batch):
    batch.enable_resident()
    rows = [batch.reserve(), batch.reserve()]
    for row in rows:
        batch.publish(row)
        batch.controller.step()
    batch.now = 2
    batch.controller.step()
    # The real allocation flock must prove the entire child is gone before reconstruction.
    batch.children[-1].returncode = -9
    batch.children[-1].wait()
    batch.controller = batch.engine()
    batch.controller.step()
    assert len(batch.started) == 1
    for row in rows:
        assert read(batch.folder(row) / "outcome.json")["state"] == "interrupted"


@pytest.mark.parametrize("fail_second", [False, True])
def test_captured_resident_child_verifies_inputs_and_finishes_healthy_siblings(batch, fail_second):
    batch.enable_resident()
    rows = [batch.reserve(), batch.reserve()]
    for row in rows:
        batch.publish(row)
        batch.controller.step()
    source = """
import json
from cbs_transcriber.enhancement_worker import RuntimeModelSlot
def process_directory(folder, *, stage):
    if stage == "alignment":
        if folder.name == FAIL:
            (folder / "error.json").write_text('{"code":"processing"}')
            return "processing"
        RUNTIME_CACHE.get({"engine":"synthetic-align","pin":"fixed"}, object)
        (folder / "alignment.json").write_text('{"passages":[]}')
    return None
"""
    source += "\nFAIL = " + repr(rows[1]["task_id"] if fail_second else None)
    paths = {"enhancement_worker.py": source.encode(), "enhancement_remote.py": b"# synthetic"}
    for name, raw in paths.items():
        (batch.directory / name).write_bytes(raw)
    batch.manifest["code_hashes"] = {
        name: hashlib.sha256(raw).hexdigest() for name, raw in paths.items()
    }
    remote.write_json(batch.directory / "batch.json", batch.manifest)
    batch.now = 2
    batch.controller.step()
    cohort = batch.directory / "cohorts" / batch.controller.cohort_id
    remote.run_cohort(cohort)
    summary = read(cohort / "model-summary.json")
    assert summary["loads"] == 1 and summary["hits"] == (0 if fail_second else 1)
    assert summary["released"] is True
    assert (
        read(cohort / "model-001.json")["worker_sha256"]
        == batch.manifest["code_hashes"]["enhancement_worker.py"]
    )
    assert (
        read(cohort / "cohort-status.json")["task_id"] == rows[0 if fail_second else 1]["task_id"]
    )
    batch.children[-1].returncode = 0
    batch.controller.step()
    assert read(batch.folder(rows[0]) / "outcome.json")["state"] == "complete"
    assert read(batch.folder(rows[1]) / "outcome.json")["state"] == (
        "failed" if fail_second else "complete"
    )


def test_resident_active_status_tracks_actual_stage_owner(batch):
    batch.enable_resident()
    rows = [batch.reserve(), batch.reserve()]
    for row in rows:
        batch.publish(row)
        batch.controller.step()
    batch.now = 2
    batch.controller.step()
    cohort = batch.directory / "cohorts" / batch.controller.cohort_id
    remote.write_json(
        cohort / "cohort-status.json",
        {
            "protocol": remote.PROTOCOL,
            "batch_id": batch.batch_id,
            "cohort_id": cohort.name,
            "task_id": rows[1]["task_id"],
            "stage": "alignment",
        },
    )
    assert batch.controller.step()["running_task_id"] == rows[1]["task_id"]


def test_acceptance_precedes_inference_and_late_task_joins_running_child(batch):
    first = batch.reserve()
    batch.publish(first)
    assert batch.controller.step()["phase"] == "running"
    accepted = (batch.folder(first) / "accepted.json").read_bytes()
    late = batch.reserve()
    batch.publish(late)
    batch.controller.step()
    assert read(batch.folder(late) / "accepted.json")["task_id"] == late["task_id"]
    assert batch.started == [first["task_id"]]
    assert (batch.folder(first) / "accepted.json").read_bytes() == accepted
    batch.finish(first)
    assert batch.started == [first["task_id"], late["task_id"]]
    status = batch.finish(late)
    assert status["completed"] == 2 and status["phase"] == "ready"
    batch.seal()
    status = batch.controller.step()
    assert status["phase"] == "finished" and status["seal_digest"]
    for row in (first, late):
        outcome = read(batch.folder(row) / "outcome.json")
        assert outcome["outputs"]["alignment.json"] == remote.file_receipt(
            batch.folder(row) / "alignment.json", remote.OUTPUT_LIMIT
        )


def test_lost_acceptance_reply_and_repeated_ready_never_replay(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    for _ in range(3):
        batch.ready(row)
        batch.controller.step()
    batch.finish(row)
    batch.controller.step()
    assert batch.started == [row["task_id"]]


def test_recovered_acceptance_without_outcome_is_interrupted_never_replayed(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    batch.children[-1].returncode = -9
    batch.children[-1].wait()  # Models an acquired flock proving the former child gone.
    batch.controller = batch.engine()
    status = batch.controller.step()
    outcome = read(batch.folder(row) / "outcome.json")
    assert outcome["state"] == "interrupted" and status["failed"] == 1
    assert len(batch.started) == 1
    retry = batch.reserve(job_id=row["job_id"], attempt=2, ordinal=row["ordinal"])
    batch.publish(retry)
    batch.controller.step()
    batch.finish(retry)
    assert len(batch.started) == 2
    batch.seal()
    status = batch.controller.step()
    assert status["phase"] == "finished" and status["failed"] == 0 and status["completed"] == 1


def test_partial_membership_on_recovery_does_not_allow_later_replay(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    batch.children[-1].returncode = -9
    batch.children[-1].wait()
    raw = (batch.directory / "membership.json").read_bytes()
    (batch.directory / "membership.json").write_bytes(b"{")
    batch.controller = batch.engine()
    batch.controller.step()
    (batch.directory / "membership.json").write_bytes(raw)
    batch.controller.step()
    assert read(batch.folder(row) / "outcome.json")["state"] == "interrupted"
    assert len(batch.started) == 1


@pytest.mark.parametrize("name", ["ready.json", "inputs.json", "membership.json"])
def test_partial_publication_waits_for_exact_complete_descriptor(batch, name):
    row = batch.reserve()
    batch.publish(row)
    path = batch.directory / name if name == "membership.json" else batch.folder(row) / name
    raw = path.read_bytes()
    path.write_bytes(b'{"protocol":')
    assert batch.controller.step()["phase"] == "ready"
    assert not batch.started
    path.write_bytes(raw)
    assert batch.controller.step()["phase"] == "running"


@pytest.mark.parametrize(
    "damage",
    [
        "hash",
        "size",
        "unknown_output",
        "traversal",
        "duplicate",
        "nonfinite",
        "wrong_identity",
        "unknown_option",
        "downloads",
    ],
)
def test_untrusted_task_descriptor_never_executes(batch, damage):
    row = batch.reserve()
    batch.publish(row)
    folder = batch.folder(row)
    inputs = read(folder / "inputs.json")
    if damage == "hash":
        inputs["files"]["audio.wav"]["sha256"] = "f" * 64
    elif damage == "size":
        inputs["files"]["audio.wav"]["size"] += 1
    elif damage == "unknown_output":
        inputs["expected_outputs"] = ["private.json"]
    elif damage == "traversal":
        inputs["files"]["../request.json"] = inputs["files"].pop("request.json")
    elif damage == "wrong_identity":
        inputs["identity_hash"] = "f" * 64
    elif damage in {"unknown_option", "downloads"}:
        request = read(folder / "request.json")
        request["options"]["arbitrary" if damage == "unknown_option" else "allow_downloads"] = True
        remote.write_json(folder / "request.json", request)
        inputs["files"]["request.json"] = remote.file_receipt(
            folder / "request.json", remote.OUTPUT_LIMIT
        )
    remote.write_json(folder / "inputs.json", inputs)
    if damage == "duplicate":
        raw = (folder / "inputs.json").read_bytes()
        (folder / "inputs.json").write_bytes(raw[:-1] + b',"protocol":"bad"}')
    if damage == "nonfinite":
        (folder / "inputs.json").write_bytes(b'{"number":1e999}')
    batch.ready(row)
    assert batch.controller.step()["phase"] == "ready"
    assert not batch.started and not (folder / "accepted.json").exists()


def test_changed_valid_digest_after_acceptance_conflicts_and_reaps_child(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    folder = batch.folder(row)
    inputs = read(folder / "inputs.json")
    inputs["files"]["audio.wav"]["size"] += 1
    remote.write_json(folder / "inputs.json", inputs)
    batch.ready(row)
    assert batch.controller.step()["phase"] == "failed"
    assert batch.children[0].terminated and batch.children[0].reaped
    assert len(batch.started) == 1


def test_seal_waits_for_reserved_unpublished_and_not_dispatched_member(batch):
    first, unpublished = batch.reserve(), batch.reserve()
    failed = batch.reserve(disposition="not_dispatched")
    batch.publish(first)
    batch.seal()
    assert batch.controller.step()["phase"] == "draining"
    assert batch.finish(first)["phase"] == "draining"
    assert batch.controller.step()["seal_digest"] is None
    assert read(batch.folder(failed) / "outcome.json")["state"] == "not_dispatched"
    batch.publish(unpublished)
    batch.controller.step()
    assert batch.finish(unpublished)["phase"] == "finished"


def test_seal_for_older_membership_cannot_acknowledge_new_member(batch):
    first = batch.reserve(disposition="not_dispatched")
    batch.seal()
    batch.reserve()
    status = batch.controller.step()
    assert status["seal_digest"] is None and status["phase"] == "ready"
    assert read(batch.folder(first) / "outcome.json")["state"] == "not_dispatched"


def test_sealed_membership_cannot_change_or_shrink(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.seal()
    batch.controller.step()
    batch.reserve()
    assert batch.controller.step()["phase"] == "failed"
    assert batch.children[0].reaped


def test_failed_child_is_reaped_before_healthy_sibling_and_error_is_curated(batch):
    first, second = batch.reserve(), batch.reserve()
    batch.publish(first)
    batch.publish(second)
    batch.controller.step()
    remote.write_json(batch.folder(first) / "error.json", {"code": "private body api-key"})
    batch.finish(first, 1)
    assert batch.children[0].reaped and batch.started[-1] == second["task_id"]
    assert read(batch.folder(first) / "outcome.json")["error_code"] == "processing"
    batch.finish(second)
    status = batch.controller.step()
    assert status["completed"] == 1 and status["failed"] == 1
    assert "private" not in json.dumps(status)


@pytest.mark.parametrize("contents", [None, b'{"invalid":', b'{"n":NaN}'])
def test_successful_exit_requires_complete_bounded_json_outputs(batch, contents):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    if contents is not None:
        (batch.folder(row) / "alignment.json").write_bytes(contents)
    batch.children[-1].returncode = 0
    batch.controller.step()
    assert read(batch.folder(row) / "outcome.json")["error_code"] == "invalid_output"


def test_stop_is_shared_monotonic_reaps_child_and_preserves_completed(batch):
    first, active, pending = batch.reserve(), batch.reserve(), batch.reserve()
    for row in (first, active):
        batch.publish(row)
    batch.controller.step()
    batch.finish(first)
    before = (batch.folder(first) / "outcome.json").read_bytes()
    batch.children[-1].ignore_terminate = True
    batch.stop()
    status = batch.controller.step()
    assert status["phase"] == "stopped" and status["completed"] == 1
    assert batch.children[-1].killed and batch.children[-1].reaped
    assert read(batch.folder(active) / "outcome.json")["error_code"] == "stopped"
    assert read(batch.folder(pending) / "outcome.json")["state"] == "not_dispatched"
    assert (batch.folder(first) / "outcome.json").read_bytes() == before
    (batch.directory / "stop.json").unlink()
    batch.publish(pending)
    assert batch.controller.step()["phase"] == "stopped"
    assert len(batch.started) == 2
    assert batch.engine().step() == status


def test_stop_observes_just_completed_output_before_cancelling_siblings(batch):
    row, pending = batch.reserve(), batch.reserve()
    batch.publish(row)
    batch.controller.step()
    remote.write_json(batch.folder(row) / "alignment.json", {"passages": []})
    batch.children[-1].returncode = 0
    batch.stop()
    status = batch.controller.step()
    assert status["phase"] == "stopped" and status["completed"] == 1
    assert read(batch.folder(pending) / "outcome.json")["error_code"] == "stopped"


def test_owner_absence_drains_accepted_but_rejects_late_unaccepted(batch):
    first, accepted, late = batch.reserve(), batch.reserve(), batch.reserve()
    batch.publish(first)
    batch.publish(accepted)
    batch.heartbeat(1, 1)
    batch.controller.step()
    batch.now = 299
    batch.controller.step()  # Re-reading the same nonce must not renew owner liveness.
    batch.publish(late)
    batch.now = 300
    assert batch.controller.step()["phase"] == "draining"
    assert not (batch.folder(late) / "accepted.json").exists()
    batch.finish(first)
    assert batch.started[-1] == accepted["task_id"]
    status = batch.finish(accepted)
    assert status["phase"] == "orphan-drained" and status["completed"] == 2
    batch.heartbeat(2, 1)
    assert batch.controller.step()["phase"] == "orphan-drained"
    assert len(batch.started) == 2


def test_heartbeat_new_epoch_advances_but_old_epoch_never_keeps_owner_alive(batch):
    batch.heartbeat(2, 1)
    batch.controller.step()
    batch.now = 250
    batch.heartbeat(3, 0)
    batch.controller.step()
    batch.now = 500
    batch.heartbeat(2, 999)
    assert batch.controller.step()["phase"] == "ready"
    batch.now = 550
    assert batch.controller.step()["phase"] == "orphan-drained"


def test_child_timeout_reaps_without_replaying_and_continues_sibling(batch):
    first, second = batch.reserve(), batch.reserve()
    for row in (first, second):
        batch.publish(row)
        request = read(batch.folder(row) / "request.json")
        request["options"]["timeout_minutes"] = 1
        batch.publish(row, request=request)
    batch.controller.step()
    batch.now = 60
    batch.controller.step()
    assert batch.children[0].reaped and batch.started[-1] == second["task_id"]
    assert read(batch.folder(first) / "outcome.json")["error_code"] == "timeout"


def test_membership_cannot_omit_accepted_task_after_controller_restart(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    batch.children[-1].returncode = -9
    batch.children[-1].wait()
    batch.rows = []
    batch.membership()
    batch.controller = batch.engine()
    assert batch.controller.step()["phase"] == "failed"
    assert len(batch.started) == 1


def test_oversized_controls_and_output_limits_are_enforced(batch, monkeypatch):
    row = batch.reserve()
    batch.publish(row)
    (batch.folder(row) / "ready.json").write_bytes(b" " * (remote.CONTROL_LIMIT + 1))
    assert batch.controller.step()["phase"] == "ready"
    batch.ready(row)
    batch.controller.step()
    monkeypatch.setattr(remote, "OUTPUT_LIMIT", 5)
    (batch.folder(row) / "alignment.json").write_bytes(b'{"passages":[]}')
    batch.children[-1].returncode = 0
    batch.controller.step()
    assert read(batch.folder(row) / "outcome.json")["error_code"] == "invalid_output"


def test_source_hashes_verified_before_worker_execution(batch):
    for name in ("enhancement_worker.py", "enhancement_remote.py"):
        (batch.directory / name).write_text("# synthetic", encoding="utf-8")
        batch.manifest["code_hashes"][name] = remote.file_receipt(batch.directory / name, 100)[
            "sha256"
        ]
    remote.validate_sources(batch.directory, batch.manifest)
    (batch.directory / "enhancement_worker.py").write_text("# changed", encoding="utf-8")
    with pytest.raises(remote.OwnershipConflict, match="source hash"):
        remote.validate_sources(batch.directory, batch.manifest)


def test_symlinked_task_input_is_never_followed(batch, tmp_path):
    row = batch.reserve()
    batch.publish(row)
    audio = batch.folder(row) / "audio.wav"
    target = tmp_path / "outside.wav"
    target.write_bytes(audio.read_bytes())
    audio.unlink()
    try:
        audio.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks requires platform permission")
    assert batch.controller.step()["phase"] == "ready"
    assert not batch.started


def test_runner_inherits_allocation_and_shared_runtime_locks_only(batch, monkeypatch):
    row = batch.reserve()
    batch.publish(row)
    (batch.directory / "enhancement_remote.py").write_text(
        "def worker_environment():\n    return {'PATH':'synthetic', 'CBS_RUNTIME_LOCK_FD':'9'}\n"
        "def inherited_runtime_fds():\n    return (9,)\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        remote.subprocess, "Popen", lambda command, **kwargs: calls.append((command, kwargs))
    )
    remote.start_worker(batch.folder(row), 7)
    command, kwargs = calls[0]
    assert command[1] == "-I" and command[-1] == str(batch.folder(row))
    assert kwargs["pass_fds"] == (7, 9)
    assert set(kwargs["env"]) == {"PATH", "CBS_RUNTIME_LOCK_FD"}
    assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL


def test_immutable_receipts_and_unchanged_status_skip_replacement(tmp_path, monkeypatch):
    path = tmp_path / "accepted.json"
    remote.write_json(path, {"state": "accepted"}, immutable=True)
    monkeypatch.setattr(
        os, "replace", lambda *args: pytest.fail("Unchanged bytes must not replace")
    )
    remote.write_json(path, {"state": "accepted"}, immutable=True)
    with pytest.raises(remote.OwnershipConflict):
        remote.write_json(path, {"state": "different"}, immutable=True)


def test_wall_budget_survives_controller_reconstruction_and_blocks_new_work(batch):
    batch.manifest["limits"]["wall_seconds"] = 120
    remote.write_json(batch.directory / "batch.json", batch.manifest)
    batch.controller = batch.engine()
    first, pending = batch.reserve(), batch.reserve()
    batch.publish(first)
    batch.controller.step()
    batch.children[-1].returncode = -9
    batch.children[-1].wait()
    batch.now = 119
    batch.controller = batch.engine()
    batch.controller.step()
    assert read(batch.folder(first) / "outcome.json")["state"] == "interrupted"
    batch.now = 120
    batch.publish(pending)
    status = batch.controller.step()
    assert status["phase"] == "failed"
    assert read(batch.folder(pending) / "outcome.json")["error_code"] == "timeout"
    assert len(batch.started) == 1


def test_new_attempt_waits_until_previous_child_is_reaped(batch):
    first = batch.reserve()
    batch.publish(first)
    batch.controller.step()
    retry = batch.reserve(job_id=first["job_id"], attempt=2, ordinal=first["ordinal"])
    batch.publish(retry)
    batch.controller.step()
    assert not (batch.folder(retry) / "accepted.json").exists()
    batch.finish(first, 1)
    assert batch.children[0].reaped and batch.started[-1] == retry["task_id"]


def test_public_status_contains_only_curated_identifiers_and_counts(batch):
    batch.reserve()
    batch.controller.step()
    value = read(batch.directory / "batch-status.json")
    value.update(private_text="secret", running_task_id="private body", seal_digest="private")
    remote.write_json(batch.directory / "batch-status.json", value)
    status = remote.status(batch.directory)
    assert status["phase"] == "ready"
    assert status["running_task_id"] is None and status["seal_digest"] is None
    assert "private" not in json.dumps(status) and "secret" not in json.dumps(status)


def test_same_heartbeat_cannot_renew_liveness_after_controller_restart(batch):
    batch.heartbeat(1, 1)
    batch.controller.step()
    batch.now = 299
    batch.controller = batch.engine()
    assert batch.controller.step()["phase"] == "ready"
    batch.now = 300
    assert batch.controller.step()["phase"] == "orphan-drained"


@pytest.mark.parametrize("control", ["stop", "orphan"])
def test_observed_stop_and_orphan_latch_survive_restart_before_terminal_status(batch, control):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    if control == "stop":
        batch.stop()
    else:
        batch.now = 300
    batch.controller._controls()  # A crash after control acknowledgement, before status.
    batch.children[-1].returncode = -9
    batch.children[-1].wait()
    if control == "stop":
        (batch.directory / "stop.json").write_bytes(b"{")
    batch.heartbeat(2, 0)
    batch.controller = batch.engine()
    assert batch.controller.step()["phase"] == (
        "stopped" if control == "stop" else "orphan-drained"
    )
    assert len(batch.started) == 1


def test_restart_retains_unpublished_reservations_when_membership_upload_is_partial(batch):
    batch.reserve()
    batch.controller.step()
    batch.seal()
    batch.controller.step()
    (batch.directory / "membership.json").write_bytes(b"{")
    batch.controller = batch.engine()
    assert batch.controller.step()["phase"] == "draining"
    assert batch.controller.step()["seal_digest"] is None


def test_restart_retains_seal_even_if_uploaded_seal_later_disappears(batch):
    batch.reserve()
    batch.seal()
    batch.controller.step()
    (batch.directory / "seal.json").unlink()
    batch.controller = batch.engine()
    batch.reserve()
    assert batch.controller.step()["phase"] == "failed"


def test_new_heartbeat_after_elapsed_absence_cannot_reopen_admission(batch):
    batch.heartbeat(1, 1)
    batch.controller.step()
    batch.now = 301
    batch.heartbeat(2, 1)
    assert batch.controller.step()["phase"] == "orphan-drained"


def test_failed_later_stage_retains_exact_transcription_for_explicit_new_attempt(batch):
    first = batch.reserve()
    batch.publish(first)
    request = read(batch.folder(first) / "request.json")
    request["options"]["transcribe"] = True
    request["passages"] = []
    batch.publish(first, request=request)
    batch.controller.step()
    remote.write_json(
        batch.folder(first) / "transcription.json",
        {
            "segments": [{"text": "Synthetic speech", "start": 0, "end": 2}],
        },
    )
    (batch.folder(first) / "alignment.json").write_bytes(b'{"passages":')
    batch.finish(first, 1)
    previous_bytes = (batch.folder(first) / "outcome.json").read_bytes()
    previous = read(batch.folder(first) / "outcome.json")
    assert previous["state"] == "failed" and set(previous["outputs"]) == {"transcription.json"}
    assert previous["outputs"]["transcription.json"] == remote.file_receipt(
        batch.folder(first) / "transcription.json", remote.OUTPUT_LIMIT
    )
    retry = batch.reserve(job_id=first["job_id"], attempt=2, ordinal=first["ordinal"])
    batch.publish(retry, request=request)
    (batch.folder(retry) / "transcription.json").write_bytes(
        (batch.folder(first) / "transcription.json").read_bytes()
    )
    inputs = read(batch.folder(retry) / "inputs.json")
    inputs["files"]["transcription.json"] = previous["outputs"]["transcription.json"]
    remote.write_json(batch.folder(retry) / "inputs.json", inputs)
    batch.ready(retry)
    batch.controller.step()
    assert batch.started == [first["task_id"], retry["task_id"]]
    batch.finish(retry)
    assert read(batch.folder(retry) / "outcome.json")["state"] == "complete"
    assert (batch.folder(first) / "outcome.json").read_bytes() == previous_bytes


@pytest.mark.parametrize(
    "damage", ["unrequested", "wrong_hash", "changed_request", "malformed_stage"]
)
def test_carried_stage_requires_previous_outcome_and_same_frozen_request(batch, damage):
    first = batch.reserve()
    batch.publish(first)
    batch.controller.step()
    remote.write_json(batch.folder(first) / "alignment.json", {"passages": []})
    batch.finish(first, 1)
    retry = batch.reserve(job_id=first["job_id"], attempt=2, ordinal=first["ordinal"])
    request = read(batch.folder(first) / "request.json")
    if damage == "changed_request":
        request["passages"][0]["text"] = "Different source"
    batch.publish(retry, request=request)
    name = "diarization.json" if damage == "unrequested" else "alignment.json"
    remote.write_json(batch.folder(retry) / name, {"passages": []})
    if damage == "malformed_stage":
        (batch.folder(retry) / name).write_bytes(b"null")
    receipt = remote.file_receipt(batch.folder(retry) / name, remote.OUTPUT_LIMIT)
    if damage == "wrong_hash":
        receipt["sha256"] = "f" * 64
    inputs = read(batch.folder(retry) / "inputs.json")
    inputs["files"][name] = receipt
    remote.write_json(batch.folder(retry) / "inputs.json", inputs)
    batch.ready(retry)
    batch.controller.step()
    assert not (batch.folder(retry) / "accepted.json").exists()
    assert batch.started == [first["task_id"]]


def test_stop_receipt_retains_completed_stage_without_replaying_task(batch):
    row = batch.reserve()
    batch.publish(row)
    batch.controller.step()
    remote.write_json(batch.folder(row) / "alignment.json", {"passages": []})
    batch.stop()
    batch.controller.step()
    outcome = read(batch.folder(row) / "outcome.json")
    assert outcome["state"] == "interrupted" and set(outcome["outputs"]) == {"alignment.json"}
    assert len(batch.started) == 1 and batch.children[-1].reaped
