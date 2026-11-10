"""Standalone, credential-free file controller for one CBS Runtime allocation.

The importable controller never acquires a platform lock or loads inference packages.
Its CLI holds the allocation flock, inherited by every sequential inference child.
"""

import argparse
import hashlib
import importlib.util
import inspect
import json
import math
import os
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import ModuleType

PROTOCOL = "cbs-runtime-batch-v1"
CONTROL_LIMIT = 64 * 1024
OUTPUT_LIMIT = 32 * 1024 * 1024
AUDIO_LIMIT = 4 * 1024 * 1024 * 1024
SOURCE_LIMIT = 512 * 1024 * 1024
TRANSPORT_DESCRIPTOR = "original.input.json"
PREPARATION_NAME = "audio.preparation.json"
SOURCE_INPUT_NAMES = frozenset(
    "audio.source." + extension for extension in ("mp3", "m4a", "mp4", "flac", "ogg", "aac")
)
OUTPUT_NAMES = frozenset(
    {"transcription.json", "retranscription.json", "alignment.json", "diarization.json"}
)
SOURCE_NAMES = frozenset(
    {
        "supervisor.sh",
        "ucloud_supervisor.sh",
        "runtime_batch_remote.py",
        "enhancement_remote.py",
        "enhancement_worker.py",
        "danish_models.py",
        "remote_audio_input.py",
        "danish_whisper_models.json",
    }
)
ERROR_CODES = frozenset(
    {
        "processing",
        "runtime",
        "dependencies",
        "cache",
        "access",
        "memory",
        "interrupted",
        "stopped",
        "owner_absent",
        "timeout",
        "ownership_conflict",
        "invalid_output",
        "input_changed",
        "input_preparation",
        "not_dispatched",
    }
)
TERMINAL_PHASES = frozenset({"finished", "stopped", "failed", "orphan-drained"})


class OwnershipConflict(ValueError):
    """A complete publication disagrees with an already owned identity."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Nonfinite JSON number")


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


def _parse(raw):
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_constant)
    _finite(value)
    return value


def _safe(path):
    """Reject symlinks/junctions, including a substituted task parent."""
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError("Unsafe runtime path")
    return path


def _read_bytes(path, limit):
    _safe(path)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid runtime file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Oversized runtime file")
    return raw


def read_control(path):
    """An incomplete/non-atomic upload is never an instruction to process."""
    try:
        raw = _read_bytes(path, CONTROL_LIMIT)
        value = _parse(raw)
        return (value, raw) if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def write_json(path, value, *, immutable=False):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    _write_bytes(path, raw, immutable=immutable)


def _write_bytes(path, raw, *, immutable=False):
    if len(raw) > CONTROL_LIMIT:
        raise ValueError("Oversized runtime receipt")
    _safe(path)
    try:
        before = _read_bytes(path, CONTROL_LIMIT)
    except FileNotFoundError:
        before = None
    except ValueError as exc:
        if immutable:
            raise OwnershipConflict("Conflicting immutable runtime receipt") from exc
        before = None  # A complete replacement can repair an oversized mutable publication.
    if before == raw:
        return
    if immutable and before is not None:
        raise OwnershipConflict("Conflicting immutable runtime receipt")
    pending = _safe(path.with_suffix(".pending"))
    descriptor = os.open(
        pending,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_TRUNC
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def _id(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
        return value in {parsed.hex, str(parsed)}
    except ValueError:
        return False


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _hash(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _choice(value, choices):
    return isinstance(value, str) and value in choices


def file_receipt(path, limit):
    """Hash with bounded memory, without following uploaded filesystem links."""
    _safe(path)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= limit:
            raise ValueError("Invalid runtime file")
        digest, size = hashlib.sha256(), 0
        while data := stream.read(1024 * 1024):
            size += len(data)
            if size > limit:
                raise ValueError("Oversized runtime file")
            digest.update(data)
        return {"size": size, "sha256": digest.hexdigest()}


def _receipt(value, limit):
    return (
        isinstance(value, dict)
        and set(value) == {"size", "sha256"}
        and _integer(value["size"])
        and value["size"] <= limit
        and _hash(value["sha256"])
    )


def _matches(value, batch_id):
    return value.get("protocol") == PROTOCOL and value.get("batch_id") == batch_id


def load_manifest(directory):
    loaded = read_control(directory / "batch.json")
    if not loaded:
        raise ValueError("Invalid runtime batch manifest")
    manifest = loaded[0]
    limits = manifest.get("limits")
    hashes = manifest.get("code_hashes")
    if (
        set(manifest) != {"protocol", "batch_id", "identity_hash", "code_hashes", "limits"}
        or manifest.get("protocol") != PROTOCOL
        or not _id(manifest.get("batch_id"))
        or not _hash(manifest.get("identity_hash"))
        or not isinstance(hashes, dict)
        or not hashes
        or not set(hashes) <= SOURCE_NAMES
        or not all(_hash(value) for value in hashes.values())
        or not isinstance(limits, dict)
        or not _integer(limits.get("max_members", 100), 1)
        or limits.get("max_members", 100) > 100
        or type(limits.get("gpu_concurrency", 1)) is not int
        or limits.get("gpu_concurrency", 1) != 1
        or type(limits.get("owner_absence_seconds", 300)) not in {float, int}
        or not 0 < limits.get("owner_absence_seconds", 300) <= 300
        or not _integer(limits.get("wall_seconds", 720 * 60), 1)
        or limits.get("wall_seconds", 720 * 60) > 720 * 60
        or ("resident_models" in limits and limits["resident_models"] != "single-slot-v1")
        or not _integer(limits.get("cohort_max_members", 8), 1)
        or limits.get("cohort_max_members", 8) > 8
        or type(limits.get("cohort_wait_seconds", 2)) not in {int, float}
        or not 0 <= limits.get("cohort_wait_seconds", 2) <= 2
    ):
        raise ValueError("Invalid runtime batch manifest")
    return manifest


def validate_sources(directory, manifest):
    hashes = manifest["code_hashes"]
    if not {"enhancement_worker.py", "enhancement_remote.py"} <= set(hashes):
        raise ValueError("Missing runtime source hashes")
    for name, expected in hashes.items():
        if file_receipt(directory / name, OUTPUT_LIMIT)["sha256"] != expected:
            raise OwnershipConflict("Runtime source hash mismatch")


def input_names(files):
    if TRANSPORT_DESCRIPTOR not in files:
        return {"request.json", "audio.wav"}
    sources = SOURCE_INPUT_NAMES & set(files)
    if len(sources) != 1 or "audio.wav" in files:
        raise ValueError("Invalid compressed runtime input inventory")
    return {"request.json", TRANSPORT_DESCRIPTOR, *sources}


def input_limit(name):
    return (
        SOURCE_LIMIT
        if name in SOURCE_INPUT_NAMES
        else AUDIO_LIMIT
        if name == "audio.wav"
        else CONTROL_LIMIT
        if name == TRANSPORT_DESCRIPTOR
        else OUTPUT_LIMIT
    )


def audio_transport(directory, manifest, folder, files):
    """Load only the captured validator, also used by the resident worker's cohort check."""
    if TRANSPORT_DESCRIPTOR not in files:
        return None, None
    path = directory / "remote_audio_input.py"
    raw = _read_bytes(path, OUTPUT_LIMIT)
    if hashlib.sha256(raw).hexdigest() != manifest["code_hashes"].get(path.name):
        raise OwnershipConflict("Compressed input validator source hash mismatch")
    module = ModuleType("cbs_runtime_audio_validator")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module, module.verify_inputs(folder, files)


def start_worker(task_directory, lock_fd):
    """Use the existing credential allowlist; no PYTHONPATH or token inheritance."""
    directory = task_directory.parent.parent
    spec = importlib.util.spec_from_file_location(
        "cbs_runtime_worker_environment", directory / "enhancement_remote.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return subprocess.Popen(
        [sys.executable, "-I", str(directory / "enhancement_worker.py"), str(task_directory)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=module.worker_environment(),
        pass_fds=tuple(dict.fromkeys((lock_fd, *module.inherited_runtime_fds()))),
    )


def start_cohort(directory, lock_fd):
    root = directory.parent.parent
    path = root / "enhancement_remote.py"
    raw = _read_bytes(path, OUTPUT_LIMIT)
    if hashlib.sha256(raw).hexdigest() != load_manifest(root)["code_hashes"].get(path.name):
        raise OwnershipConflict("Runtime environment source hash mismatch")
    module = ModuleType("cbs_runtime_cohort_environment")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return subprocess.Popen(
        [sys.executable, "-I", str(root / "runtime_batch_remote.py"), "cohort", str(directory)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=module.worker_environment(),
        pass_fds=tuple(dict.fromkeys((lock_fd, *module.inherited_runtime_fds()))),
    )


def run_cohort(directory):
    """One bounded stage cohort. Only controller-owned acceptances authorize execution."""
    directory = _safe(Path(directory).absolute())
    root = directory.parent.parent
    manifest = load_manifest(root)
    validate_sources(root, manifest)
    loaded = read_control(directory / "cohort.json")
    if not loaded:
        raise ValueError("Missing Runtime cohort")
    plan = loaded[0]
    tasks = plan.get("tasks")
    if (
        set(plan) != {"protocol", "batch_id", "identity_hash", "cohort_id", "tasks"}
        or not _matches(plan, manifest["batch_id"])
        or plan["identity_hash"] != manifest["identity_hash"]
        or plan["cohort_id"] != directory.name
        or not _id(directory.name)
        or not isinstance(tasks, list)
        or not 1 <= len(tasks) <= 8
        or len(tasks) > manifest["limits"].get("cohort_max_members", 8)
        or manifest["limits"].get("resident_models") != "single-slot-v1"
        or any(not isinstance(row, dict) or not _id(row.get("task_id")) for row in tasks)
        or len({row["task_id"] for row in tasks}) != len(tasks)
    ):
        raise ValueError("Invalid Runtime cohort")
    path = root / "enhancement_worker.py"
    raw = _read_bytes(path, OUTPUT_LIMIT)
    if hashlib.sha256(raw).hexdigest() != manifest["code_hashes"].get(path.name):
        raise OwnershipConflict("Resident worker source hash mismatch")
    worker = ModuleType("cbs_resident_worker")
    worker.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), worker.__dict__)
    source_hash = manifest["code_hashes"]["enhancement_worker.py"]

    def receipt(identity, ordinal):
        write_json(
            directory / f"model-{ordinal:03d}.json",
            {
                "protocol": PROTOCOL,
                "batch_id": manifest["batch_id"],
                "cohort_id": directory.name,
                "identity_hash": manifest["identity_hash"],
                "worker_sha256": source_hash,
                "identity": identity,
            },
            immutable=True,
        )

    worker.RUNTIME_CACHE = worker.RuntimeModelSlot(receipt)
    active, failures = {}, {}
    try:
        for binding in tasks:
            task_id = binding["task_id"]
            folder = _safe(root / "tasks" / task_id)
            accepted = read_control(folder / "accepted.json")
            inputs_raw = _read_bytes(folder / "inputs.json", CONTROL_LIMIT)
            inputs = _parse(inputs_raw)
            files = inputs.get("files") if isinstance(inputs, dict) else None
            base = input_names(files) if isinstance(files, dict) else None
            if (
                not accepted
                or accepted[0] != binding
                or binding.get("inputs_sha256") != hashlib.sha256(inputs_raw).hexdigest()
                or binding.get("inputs_size") != len(inputs_raw)
                or any(
                    inputs.get(k) != binding.get(k)
                    for k in (
                        "protocol",
                        "batch_id",
                        "identity_hash",
                        "task_id",
                        "job_id",
                        "attempt",
                    )
                )
                or not base
                or not base <= set(files)
                or not set(files) <= base | OUTPUT_NAMES
                or any(
                    file_receipt(folder / name, input_limit(name)) != value
                    for name, value in files.items()
                )
            ):
                raise OwnershipConflict("Runtime cohort input changed")
            audio_transport(root, manifest, folder, files)
            active[task_id] = (folder, inputs)
        for stage in ("transcription", "alignment", "diarization", "complete"):
            for task_id, (folder, inputs) in active.items():
                if task_id in failures:
                    continue
                write_json(
                    directory / "cohort-status.json",
                    {
                        "protocol": PROTOCOL,
                        "batch_id": manifest["batch_id"],
                        "cohort_id": directory.name,
                        "task_id": task_id,
                        "stage": stage,
                    },
                )
                # Recheck the exact frozen request; stage scheduling never rewrites its options.
                if (
                    file_receipt(folder / "request.json", OUTPUT_LIMIT)
                    != inputs["files"]["request.json"]
                ):
                    raise OwnershipConflict("Runtime cohort request changed")
                if stage == "complete" and not all(
                    (folder / name).is_file() for name in inputs["expected_outputs"]
                ):
                    raise ValueError("Runtime cohort stage output is missing")
                error = worker.process_directory(folder, stage=stage)
                if error:
                    failures[task_id] = error
                    worker.RUNTIME_CACHE.clear()
                    if worker.RUNTIME_CACHE.release_failed:
                        raise ValueError("Resident engine release failed; close the cohort.")
                if error or stage == "complete":
                    write_json(
                        folder / "runtime-complete.json",
                        {
                            "protocol": PROTOCOL,
                            "batch_id": manifest["batch_id"],
                            "cohort_id": directory.name,
                            "task_id": task_id,
                            "inputs_sha256": next(
                                row["inputs_sha256"] for row in tasks if row["task_id"] == task_id
                            ),
                            "error_code": error,
                        },
                        immutable=True,
                    )
    finally:
        worker.RUNTIME_CACHE.clear()
        write_json(
            directory / "model-summary.json",
            {
                "protocol": PROTOCOL,
                "batch_id": manifest["batch_id"],
                "cohort_id": directory.name,
                "loads": worker.RUNTIME_CACHE.loads,
                "hits": worker.RUNTIME_CACHE.hits,
                "released": not worker.RUNTIME_CACHE.release_failed,
            },
            immutable=True,
        )


class BatchController:
    """One nonblocking poll step; callers must exclusively own the allocation lock.

    Reconstructing this engine means its old child is confirmed gone. Existing accepted
    tasks without an outcome become interrupted; only a new explicit task may run again.
    """

    def __init__(
        self,
        directory,
        *,
        runner=start_worker,
        cohort_runner=start_cohort,
        clock=time.monotonic,
        lock_fd=None,
    ):
        self.directory = _safe(Path(directory).absolute())
        self.manifest = load_manifest(self.directory)
        self.batch_id = self.manifest["batch_id"]
        self.identity_hash = self.manifest["identity_hash"]
        self.runner, self.clock, self.lock_fd = runner, clock, lock_fd
        self.cohort_runner = cohort_runner
        self._auto_module = None
        self.membership, self.membership_raw = None, None
        self.tasks, self.accepted, self.outcomes, self.inputs = {}, {}, {}, {}
        self.child, self.running_task_id = None, None
        self.cohort_tasks, self.cohort_id, self.cohort_wait_started = [], None, None
        self.child_started, self.child_timeout = None, None
        self.heartbeat = (-1, -1)
        self.last_heartbeat = self.clock()
        self.started = self.clock()
        start_path = self.directory / "controller-start.json"
        start = read_control(start_path)
        if start:
            if (
                not _matches(start[0], self.batch_id)
                or set(start[0]) != {"protocol", "batch_id", "monotonic_start"}
                or type(start[0].get("monotonic_start")) not in {float, int}
                or not 0 <= start[0]["monotonic_start"] <= self.started
            ):
                raise OwnershipConflict("Invalid controller lifetime receipt")
            self.started = start[0]["monotonic_start"]
        else:
            write_json(
                start_path,
                {
                    "protocol": PROTOCOL,
                    "batch_id": self.batch_id,
                    "monotonic_start": self.started,
                },
                immutable=True,
            )
        self.stopping, self.orphan, self.seal = False, False, None
        owner = read_control(self.directory / "controller-owner.json")
        if owner:
            value = owner[0]
            if (
                not _matches(value, self.batch_id)
                or set(value)
                != {
                    "protocol",
                    "batch_id",
                    "epoch",
                    "revision",
                    "last_seen",
                    "stopping",
                    "orphan",
                }
                or not _integer(value.get("epoch"), -1)
                or not _integer(value.get("revision"), -1)
                or type(value.get("last_seen")) not in {int, float}
                or not 0 <= value["last_seen"] <= self.clock()
                or type(value.get("stopping")) is not bool
                or type(value.get("orphan")) is not bool
            ):
                raise OwnershipConflict("Invalid controller owner receipt")
            self.heartbeat = value["epoch"], value["revision"]
            self.last_heartbeat = value["last_seen"]
            self.stopping, self.orphan = value["stopping"], value["orphan"]
        elif (self.directory / "controller-owner.json").exists():
            raise OwnershipConflict("Invalid controller owner receipt")
        cached_membership = self.directory / "controller-membership.json"
        if cached_membership.exists():
            self._read_membership(cached_membership, persist=False)
            if self.membership is None:
                raise OwnershipConflict("Invalid retained membership")
        cached_seal = read_control(self.directory / "controller-seal.json")
        if cached_seal:
            if not self._valid_seal(cached_seal[0]):
                raise OwnershipConflict("Invalid retained seal")
            self.seal = cached_seal[0]
        elif (self.directory / "controller-seal.json").exists():
            raise OwnershipConflict("Invalid retained seal")
        self.phase, self.seal_digest = "ready", None
        previous = read_control(self.directory / "batch-status.json")
        if previous and _matches(previous[0], self.batch_id):
            phase = previous[0].get("phase")
            if _choice(phase, TERMINAL_PHASES):
                self.phase = phase
                self.seal_digest = previous[0].get("seal_digest")
        self._terminal_status = status(self.directory) if previous and self.terminal else None

    @property
    def terminal(self):
        return self.phase in TERMINAL_PHASES

    def _task_directory(self, task_id):
        if not _id(task_id):
            raise ValueError("Invalid task identity")
        return _safe(self.directory / "tasks" / task_id)

    def _read_membership(self, path=None, *, persist=True):
        loaded = read_control(path or self.directory / "membership.json")
        if not loaded:
            return
        value, raw = loaded
        if not _matches(value, self.batch_id):
            return
        if set(value) != {"protocol", "batch_id", "revision", "tasks", "current_attempts"}:
            return
        revision, rows, current = (
            value.get("revision"),
            value.get("tasks"),
            value.get("current_attempts"),
        )
        if not _integer(revision) or not isinstance(rows, list) or not isinstance(current, dict):
            return
        tasks, jobs, ordinals = {}, {}, {}
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row)
                - {"task_id", "job_id", "attempt", "ordinal", "disposition", "error_code"}
                or not _id(row.get("task_id"))
                or not _id(row.get("job_id"))
                or not _integer(row.get("attempt"), 1)
                or not _integer(row.get("ordinal"))
                or not _choice(row.get("disposition"), {"reserved", "not_dispatched"})
                or ("error_code" in row and not _choice(row["error_code"], ERROR_CODES))
                or row["task_id"] in tasks
            ):
                return
            tasks[row["task_id"]] = row
            job_id, ordinal = row["job_id"], row["ordinal"]
            if ordinal in ordinals and ordinals[ordinal] != job_id:
                return
            if job_id in jobs and jobs[job_id][0]["ordinal"] != ordinal:
                return
            ordinals[ordinal] = job_id
            jobs.setdefault(job_id, []).append(row)
        if len(jobs) > self.manifest["limits"].get("max_members", 100) or set(current) != set(jobs):
            return
        for job_id, attempts in jobs.items():
            attempts.sort(key=lambda row: row["attempt"])
            if [row["attempt"] for row in attempts] != list(range(1, len(attempts) + 1)):
                return
            if current[job_id] != attempts[-1]["task_id"]:
                return
        if self.membership:
            previous = self.membership["revision"]
            if revision < previous:
                return
            if revision == previous:
                if raw != self.membership_raw:
                    raise OwnershipConflict("Conflicting membership revision")
                return
            if self.seal:
                raise OwnershipConflict("Membership changed after seal")
            for task_id, old in self.tasks.items():
                new = tasks.get(task_id)
                if new != old and not (
                    new
                    and old["disposition"] == "reserved"
                    and new["disposition"] == "not_dispatched"
                    and task_id not in self.accepted
                    and {k: v for k, v in new.items() if k not in {"disposition", "error_code"}}
                    == {k: v for k, v in old.items() if k not in {"disposition", "error_code"}}
                ):
                    raise OwnershipConflict("Membership lost or changed an owned task")
        self.membership, self.membership_raw, self.tasks = value, raw, tasks
        if persist:
            _write_bytes(self.directory / "controller-membership.json", raw)

    def _binding(self, task, digest=None, size=None):
        return {
            "protocol": PROTOCOL,
            "batch_id": self.batch_id,
            "identity_hash": self.identity_hash,
            "task_id": task["task_id"],
            "job_id": task["job_id"],
            "attempt": task["attempt"],
            "inputs_sha256": digest,
            "inputs_size": size,
        }

    def _outcome(self, task, state, error_code=None, outputs=None):
        task_id = task["task_id"]
        binding = self.accepted.get(task_id, self._binding(task))
        value = binding | {"state": state, "error_code": error_code, "outputs": outputs or {}}
        folder = self._task_directory(task_id)
        inputs = self.inputs.get(task_id)
        if inputs and TRANSPORT_DESCRIPTOR in inputs["files"]:
            try:
                module, descriptor = audio_transport(
                    self.directory, self.manifest, folder, inputs["files"]
                )
                module._prepared(folder, descriptor)
                value["preparation"] = file_receipt(folder / PREPARATION_NAME, CONTROL_LIMIT)
            except (OSError, ValueError, RecursionError, TypeError):
                if outputs:
                    raise ValueError(
                        "Inference output has no valid original preparation receipt"
                    ) from None
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "outcome.json", value, immutable=True)
        self.outcomes[task_id] = value

    def _receipts(self):
        if self.membership:
            parent = _safe(self.directory / "tasks")
            if parent.exists():
                for folder in parent.iterdir():
                    if _id(folder.name) and folder.name not in self.tasks:
                        _safe(folder)
                        if (folder / "accepted.json").exists():
                            raise OwnershipConflict("Membership omitted an accepted task")
        for task_id, task in self.tasks.items():
            if task_id in self.outcomes:
                continue
            folder = self._task_directory(task_id)
            owned = task_id in self.accepted
            accepted = read_control(folder / "accepted.json")
            if accepted:
                binding = accepted[0]
                if (
                    not _hash(binding.get("inputs_sha256"))
                    or not _integer(binding.get("inputs_size"), 1)
                    or binding
                    != self._binding(task, binding.get("inputs_sha256"), binding.get("inputs_size"))
                ):
                    raise OwnershipConflict("Conflicting acceptance identity")
                self.accepted[task_id] = binding
            outcome = read_control(folder / "outcome.json")
            if outcome:
                value = outcome[0]
                binding = self.accepted.get(task_id, self._binding(task))
                if (
                    set(value) - {"preparation"}
                    != set(binding) | {"state", "error_code", "outputs"}
                    or any(value.get(key) != item for key, item in binding.items())
                    or not _choice(
                        value.get("state"), {"complete", "failed", "interrupted", "not_dispatched"}
                    )
                    or (
                        value.get("error_code") is not None
                        and not _choice(value["error_code"], ERROR_CODES)
                    )
                    or not isinstance(value.get("outputs"), dict)
                    or not set(value["outputs"]) <= OUTPUT_NAMES
                    or not all(_receipt(item, OUTPUT_LIMIT) for item in value["outputs"].values())
                    or (
                        "preparation" in value and not _receipt(value["preparation"], CONTROL_LIMIT)
                    )
                    or (value["state"] != "not_dispatched" and not accepted)
                    or (
                        value["state"] == "complete"
                        and (value["error_code"] is not None or not value["outputs"])
                    )
                    or (value["state"] != "complete" and value["error_code"] is None)
                ):
                    raise OwnershipConflict("Conflicting terminal receipt")
                self.outcomes[task_id] = value
            elif accepted and not owned:
                self._outcome(
                    task,
                    "interrupted",
                    "stopped" if self.stopping else "interrupted",
                    outputs=self._outputs(task_id, partial=True),
                )

    def _controls(self):
        if self.clock() - self.last_heartbeat >= self.manifest["limits"].get(
            "owner_absence_seconds", 300
        ):
            self.orphan = True
        heartbeat = read_control(self.directory / "heartbeat.json")
        if heartbeat:
            value = heartbeat[0]
            if (
                _matches(value, self.batch_id)
                and set(value) == {"protocol", "batch_id", "epoch", "revision"}
                and _integer(value.get("epoch"))
                and _integer(value.get("revision"))
            ):
                nonce = value["epoch"], value["revision"]
                if nonce > self.heartbeat:
                    self.heartbeat, self.last_heartbeat = nonce, self.clock()
        stop = read_control(self.directory / "stop.json")
        if (
            stop
            and _matches(stop[0], self.batch_id)
            and set(stop[0]) == {"protocol", "batch_id", "revision"}
            and _integer(stop[0].get("revision"))
        ):
            self.stopping = True
        if self.clock() - self.last_heartbeat >= self.manifest["limits"].get(
            "owner_absence_seconds", 300
        ):
            self.orphan = True
        write_json(
            self.directory / "controller-owner.json",
            {
                "protocol": PROTOCOL,
                "batch_id": self.batch_id,
                "epoch": self.heartbeat[0],
                "revision": self.heartbeat[1],
                "last_seen": self.last_heartbeat,
                "stopping": self.stopping,
                "orphan": self.orphan,
            },
        )
        loaded = read_control(self.directory / "seal.json")
        if loaded and self.membership:
            value = loaded[0]
            if self._valid_seal(value):
                if self.seal and value != self.seal:
                    raise OwnershipConflict("Conflicting seal")
                write_json(self.directory / "controller-seal.json", value, immutable=True)
                self.seal = value

    def _valid_seal(self, value):
        return (
            self.membership is not None
            and _matches(value, self.batch_id)
            and set(value) == {"protocol", "batch_id", "revision", "membership_sha256"}
            and value.get("revision") == self.membership["revision"]
            and value.get("membership_sha256") == hashlib.sha256(self.membership_raw).hexdigest()
        )

    def _publication(self, task):
        folder = self._task_directory(task["task_id"])
        ready, loaded = read_control(folder / "ready.json"), read_control(folder / "inputs.json")
        if not ready or not loaded:
            return None
        descriptor, (value, raw) = ready[0], loaded
        digest = hashlib.sha256(raw).hexdigest()
        try:
            base_names = input_names(value.get("files", {}))
        except (TypeError, ValueError):
            return None
        if (
            set(descriptor) != {"protocol", "batch_id", "task_id", "inputs_sha256", "inputs_size"}
            or not _matches(descriptor, self.batch_id)
            or descriptor.get("task_id") != task["task_id"]
            or not _hash(descriptor.get("inputs_sha256"))
            or not _integer(descriptor.get("inputs_size"), 1)
        ):
            return None
        accepted = self.accepted.get(task["task_id"])
        if accepted and (
            descriptor["inputs_sha256"] != accepted["inputs_sha256"]
            or descriptor["inputs_size"] != accepted["inputs_size"]
        ):
            raise OwnershipConflict("Published task changed after acceptance")
        if descriptor["inputs_sha256"] != digest or descriptor["inputs_size"] != len(raw):
            return None
        if (
            set(value)
            != {
                "protocol",
                "batch_id",
                "identity_hash",
                "task_id",
                "job_id",
                "attempt",
                "files",
                "expected_outputs",
            }
            or not _matches(value, self.batch_id)
            or value.get("identity_hash") != self.identity_hash
            or any(value.get(key) != task[key] for key in ("task_id", "job_id", "attempt"))
            or not isinstance(value.get("expected_outputs"), list)
            or not value["expected_outputs"]
            or not all(isinstance(name, str) for name in value["expected_outputs"])
            or len(set(value["expected_outputs"])) != len(value["expected_outputs"])
            or not set(value["expected_outputs"]) <= OUTPUT_NAMES
            or not isinstance(value.get("files"), dict)
            or not base_names <= set(value["files"])
            or not set(value["files"]) <= base_names | set(value["expected_outputs"])
            or not all(
                _receipt(receipt, input_limit(name)) for name, receipt in value["files"].items()
            )
        ):
            return None
        return value, self._binding(task, digest, len(raw))

    def _auto_worker(self, options):
        """Execute only the exact frozen source, never a loader-selected bytecode cache."""
        path = self.directory / "enhancement_worker.py"
        raw = _read_bytes(path, OUTPUT_LIMIT)
        if hashlib.sha256(raw).hexdigest() != self.manifest["code_hashes"].get(path.name):
            raise OwnershipConflict("Automatic validator source hash mismatch")
        converted = options.get("whisper_model") in {"edda-turbo", "edda-large", "roest-v3"}
        if converted:
            for name in ("danish_models.py", "danish_whisper_models.json"):
                companion = _read_bytes(self.directory / name, OUTPUT_LIMIT)
                if hashlib.sha256(companion).hexdigest() != self.manifest["code_hashes"].get(name):
                    raise OwnershipConflict("Danish conversion companion source hash mismatch")
        if self._auto_module is None:
            module = ModuleType("cbs_runtime_auto_validator")
            module.__file__ = str(path)
            try:
                exec(compile(raw, str(path), "exec"), module.__dict__)
            except (ImportError, SyntaxError) as exc:
                raise ValueError("Automatic stage validation is unavailable") from exc
            if not callable(getattr(module, "auto_transcription_metadata", None)):
                raise ValueError("Automatic stage validation is unavailable")
            self._auto_module = module
        try:
            pin = self._auto_module.MODELS[
                self._auto_module.WHISPER_MODELS[options["whisper_model"]]
            ]
            if not isinstance(pin, (tuple, list)) or len(pin) != 2:
                raise ValueError("Invalid frozen Automatic model")
            if converted:
                inspect.signature(self._auto_module.converted_transcription_metadata).bind(
                    self.directory, options, model=pin
                )
            else:
                inspect.signature(self._auto_module.auto_transcription_metadata).bind(
                    self.directory,
                    options,
                    model=pin,
                    require_alignment=False,
                    include_alignment=False,
                )
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError("Incompatible frozen Automatic validator") from exc
        return self._auto_module, pin

    def _converted_metadata(self, folder, inputs, request):
        for name in ("request.json", "audio.wav", "transcription.json"):
            _safe(folder / name)
        module, pin = self._auto_worker(request["options"])
        metadata = module.converted_transcription_metadata(folder, request["options"], model=pin)
        receipt = metadata["asr_conversion"]
        audio = self._audio_receipt(folder, inputs)
        if any(
            receipt[key] != (audio if name == "audio.wav" else inputs["files"][name])["sha256"]
            for key, name in (("request_sha256", "request.json"), ("input_sha256", "audio.wav"))
        ):
            raise ValueError("Danish conversion belongs to other accepted inputs")
        return metadata

    def _auto_metadata(self, folder, inputs, request, *, include_alignment):
        names = {"request.json", "audio.wav", "transcription.json"}
        if include_alignment and request["options"]["align"]:
            names.add("alignment.json")
        for name in names:
            _safe(folder / name)
        module, pin = self._auto_worker(request["options"])
        try:
            metadata = module.auto_transcription_metadata(
                folder,
                request["options"],
                model=pin,
                require_alignment=include_alignment and request["options"]["align"],
                include_alignment=include_alignment,
            )
            decision = metadata["language_decision"]
            audio = self._audio_receipt(folder, inputs)
            if any(
                decision[key] != (audio if name == "audio.wav" else inputs["files"][name])["sha256"]
                for key, name in (
                    ("request_sha256", "request.json"),
                    ("input_sha256", "audio.wav"),
                )
            ):
                raise ValueError("Automatic output belongs to other accepted inputs")
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError("Invalid Automatic stage metadata") from exc
        return metadata

    @staticmethod
    def _auto_diarization(folder, duration):
        value = _parse(_read_bytes(folder / "diarization.json", OUTPUT_LIMIT))
        turns = value.get("turns") if isinstance(value, dict) else None
        if (
            not isinstance(turns, list)
            or len(turns) > 200_000
            or any(
                not isinstance(turn, dict)
                or any(type(turn.get(k)) not in {int, float} for k in ("start", "end"))
                or not 0 <= turn["start"] < turn["end"] <= duration + 0.1
                or not all(math.isfinite(turn[k]) for k in ("start", "end"))
                or not isinstance(turn.get("speaker"), str)
                or not 1 <= len(turn["speaker"]) <= 100
                for turn in turns
            )
        ):
            raise ValueError("Invalid Automatic diarization output")

    def _verify_inputs(self, task, inputs):
        folder = self._task_directory(task["task_id"])
        try:
            verified = all(
                file_receipt(folder / name, input_limit(name)) == expected
                for name, expected in inputs["files"].items()
            )
            if not verified:
                return False
            module, descriptor = audio_transport(
                self.directory, self.manifest, folder, inputs["files"]
            )
            request = _parse(_read_bytes(folder / "request.json", OUTPUT_LIMIT))
            if not self._valid_request(request, inputs["expected_outputs"]):
                return False
            carried = set(inputs["files"]) - input_names(inputs["files"])
            for name in carried:
                raw = self._stage_bytes(folder / name, name)
                if (
                    name == "transcription.json"
                    and "asr_conversion" in _parse(raw)
                    and request["options"].get("whisper_model")
                    not in {"edda-turbo", "edda-large", "roest-v3"}
                ):
                    return False
            if descriptor is not None:
                # Carried stages retain exact predecessor hashes. The owned child prepares
                # the same WAV and validates semantic checkpoints before any resumed inference.
                self._auto_worker(request["options"])
                return True
            if request["options"]["language"] == "auto":
                for name in OUTPUT_NAMES:
                    _safe(folder / name)
                    if (
                        task["task_id"] not in self.accepted
                        and (folder / name).exists()
                        and name not in carried
                    ):
                        return False
                self._auto_worker(request["options"])
                if carried:
                    if "transcription.json" not in carried:
                        return False
                    self._auto_metadata(
                        folder, inputs, request, include_alignment="alignment.json" in carried
                    )
                    if "diarization.json" in carried:
                        self._auto_diarization(folder, request["options"]["duration"])
            elif request["options"].get("whisper_model") in {
                "edda-turbo",
                "edda-large",
                "roest-v3",
            }:
                for name in OUTPUT_NAMES:
                    _safe(folder / name)
                    if (
                        task["task_id"] not in self.accepted
                        and (folder / name).exists()
                        and name not in carried
                    ):
                        return False
                self._auto_worker(request["options"])
                if carried:
                    if "transcription.json" not in carried:
                        return False
                    self._converted_metadata(folder, inputs, request)
            return True
        except OwnershipConflict:
            raise
        except (OSError, ValueError, RecursionError, TypeError):
            return False

    def _audio_receipt(self, folder, inputs):
        module, descriptor = audio_transport(self.directory, self.manifest, folder, inputs["files"])
        if descriptor is None:
            return inputs["files"]["audio.wav"]
        prepared = module._prepared(folder, descriptor)["prepared"]
        return {"size": prepared["bytes"], "sha256": prepared["sha256"]}

    @staticmethod
    def _valid_request(request, expected_outputs):
        if not isinstance(request, dict) or set(request) != {"options", "passages"}:
            return False
        options, passages = request["options"], request["passages"]
        allowed = {
            "device",
            "language",
            "download_models",
            "execution",
            "align",
            "diarize",
            "allow_downloads",
            "min_score",
            "timing_policy",
            "min_speakers",
            "max_speakers",
            "timeout_minutes",
            "transcribe",
            "whisper_model",
            "retranscribe",
            "duration",
            "whole_recording",
        }
        if (
            not isinstance(options, dict)
            or set(options) - allowed
            or options.get("execution") != "ucloud"
            or options.get("device") != "cuda"
            or not _choice(options.get("language"), {"da", "en", "auto"})
            or options.get("allow_downloads") is not False
            or not _integer(options.get("timeout_minutes", 360), 1)
            or options.get("timeout_minutes", 360) > 720
            or type(options.get("duration")) not in {int, float}
            or not 0 < options["duration"] <= 720 * 60
            or not isinstance(passages, list)
            or len(passages) > 20_000
            or not (options.get("align") or options.get("diarize"))
            or (options.get("transcribe") and options.get("retranscribe"))
            or (
                "timing_policy" in options
                and not _choice(
                    options["timing_policy"], {"legacy-score-filter", "retain-estimates"}
                )
            )
        ):
            return False
        if options["language"] == "auto" and (
            options.get("transcribe") is not True
            or options.get("retranscribe") is not False
            or type(options.get("align")) is not bool
            or type(options.get("diarize")) is not bool
            or options.get("whole_recording") is not False
            or not _choice(
                options.get("whisper_model"), {"small", "medium", "large-v3-turbo", "large-v3"}
            )
            or passages != []
            or options["duration"] > 6 * 3600
        ):
            return False
        for key in (
            "align",
            "diarize",
            "transcribe",
            "retranscribe",
            "whole_recording",
            "download_models",
        ):
            if key in options and type(options[key]) is not bool:
                return False
        if "whisper_model" in options and not _choice(
            options["whisper_model"],
            {
                "small",
                "medium",
                "large-v3-turbo",
                "large-v3",
                "edda-turbo",
                "edda-large",
                "roest-v3",
            },
        ):
            return False
        if options.get("whisper_model") in {"edda-turbo", "edda-large", "roest-v3"} and (
            options.get("language") != "da"
            or options.get("transcribe") is not True
            or options.get("retranscribe") is not False
            or options.get("whole_recording") is not False
            or passages != []
            or options["duration"] > 6 * 3600
        ):
            return False
        if "min_score" in options and (
            type(options["min_score"]) not in {int, float} or not 0 <= options["min_score"] <= 1
        ):
            return False
        for key in ("min_speakers", "max_speakers"):
            if options.get(key) is not None and (
                not _integer(options[key], 1) or options[key] > 20
            ):
                return False
        if (
            options.get("min_speakers") is not None
            and options.get("max_speakers") is not None
            and options["min_speakers"] > options["max_speakers"]
        ):
            return False
        outputs = {
            name + ".json"
            for key, name in (
                ("align", "alignment"),
                ("diarize", "diarization"),
                ("transcribe", "transcription"),
                ("retranscribe", "retranscription"),
            )
            if options.get(key)
        }
        if outputs != set(expected_outputs):
            return False
        passage_ids = set()
        for row in passages:
            if (
                not isinstance(row, dict)
                or set(row) != {"id", "text", "start", "end"}
                or not isinstance(row["id"], str)
                or not isinstance(row["text"], str)
                or row["id"] in passage_ids
            ):
                return False
            passage_ids.add(row["id"])
            for key in ("start", "end"):
                if row[key] is not None and (
                    type(row[key]) not in {float, int} or not 0 <= row[key] <= options["duration"]
                ):
                    return False
            if options.get("retranscribe") and row["text"]:
                return False
            if row["start"] is not None and row["end"] is not None and row["end"] < row["start"]:
                return False
            if options.get("retranscribe") and (
                row["start"] is None
                or row["end"] is None
                or not 0 < row["end"] - row["start"] <= 60
            ):
                return False
        return True

    def _accept(self):
        ordered = sorted(
            self.tasks.items(), key=lambda pair: (pair[1]["ordinal"], pair[1]["attempt"])
        )
        for task_id, task in ordered:
            if task_id in self.outcomes:
                continue
            if task["disposition"] == "not_dispatched":
                if task_id in self.accepted:
                    raise OwnershipConflict("Accepted task cannot be withdrawn")
                self._outcome(task, "not_dispatched", task.get("error_code", "not_dispatched"))
                continue
            published = self._publication(task)
            if task_id in self.accepted:
                if published:
                    self.inputs[task_id] = published[0]
                continue
            if self.orphan or self.stopping or not published:
                continue
            if any(
                old["job_id"] == task["job_id"]
                and old["attempt"] < task["attempt"]
                and old_id not in self.outcomes
                for old_id, old in self.tasks.items()
            ):
                continue  # An explicit attempt still waits until its predecessor is confirmed gone.
            inputs, binding = published
            carried = set(inputs["files"]) - input_names(inputs["files"])
            if carried:
                previous = next(
                    (
                        row
                        for row in self.tasks.values()
                        if row["job_id"] == task["job_id"] and row["attempt"] == task["attempt"] - 1
                    ),
                    None,
                )
                if previous is None:
                    continue
                old_id = previous["task_id"]
                old = self.inputs.get(old_id)
                if old is None:
                    publication = self._publication(previous)
                    old = publication[0] if publication else None
                if (
                    old is None
                    or set(old["expected_outputs"]) != set(inputs["expected_outputs"])
                    or any(
                        old["files"][name] != inputs["files"][name]
                        for name in input_names(inputs["files"])
                    )
                    or any(
                        self.outcomes[old_id]["outputs"].get(name) != inputs["files"][name]
                        for name in carried
                    )
                ):
                    continue
            if not self._verify_inputs(task, inputs):
                continue
            self._controls()
            if self.stopping or self.orphan:
                return
            # Publication is a commit: inference is never started before this durable receipt.
            write_json(self._task_directory(task_id) / "accepted.json", binding, immutable=True)
            self.accepted[task_id], self.inputs[task_id] = binding, inputs
            break  # Bound heavy input hashing to one new task between child/control polls.

    @staticmethod
    def _stage_bytes(path, name):
        raw = _read_bytes(path, OUTPUT_LIMIT)
        value = _parse(raw)
        key = {
            "transcription.json": "segments",
            "retranscription.json": "passages",
            "alignment.json": "passages",
            "diarization.json": "turns",
        }[name]
        if (
            not isinstance(value, dict)
            or not isinstance(value.get(key), list)
            or not all(isinstance(row, dict) for row in value[key])
        ):
            raise ValueError("Invalid completed stage")
        return raw

    def _outputs(self, task_id, *, partial=False):
        folder = self._task_directory(task_id)
        outputs = {}
        inputs = self.inputs.get(task_id)
        if inputs is None:
            publication = self._publication(self.tasks[task_id])
            inputs = publication[0] if publication else None
        if inputs is None:
            if partial:
                return outputs
            raise ValueError("Missing accepted input descriptor")
        try:
            if TRANSPORT_DESCRIPTOR in inputs["files"]:
                self._audio_receipt(folder, inputs)
            raw = _read_bytes(folder / "request.json", OUTPUT_LIMIT)
            if hashlib.sha256(raw).hexdigest() != inputs["files"]["request.json"]["sha256"]:
                raise ValueError("The accepted runtime request changed")
            request = _parse(raw)
            if not self._valid_request(request, inputs["expected_outputs"]):
                raise ValueError("The accepted runtime request is invalid")
        except (OSError, ValueError, RecursionError, TypeError):
            if partial:
                return outputs
            raise
        for name in inputs["expected_outputs"]:
            try:
                raw = self._stage_bytes(folder / name, name)
                if (
                    name == "transcription.json"
                    and "asr_conversion" in _parse(raw)
                    and request["options"].get("whisper_model")
                    not in {"edda-turbo", "edda-large", "roest-v3"}
                ):
                    raise ValueError("This model cannot reuse Danish conversion evidence")
            except (OSError, ValueError, RecursionError):
                if partial:
                    continue
                raise
            outputs[name] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if request["options"]["language"] == "auto":
            try:
                if "transcription.json" not in outputs:
                    raise ValueError("Automatic output has no verified ASR source")
                self._auto_metadata(folder, inputs, request, include_alignment=not partial)
            except OwnershipConflict:
                raise
            except (OSError, ValueError, RecursionError, TypeError):
                if partial:
                    return {}
                raise
            if partial and "alignment.json" in outputs:
                try:
                    self._auto_metadata(folder, inputs, request, include_alignment=True)
                except OwnershipConflict:
                    raise
                except (OSError, ValueError, RecursionError, TypeError):
                    del outputs["alignment.json"]
            if "diarization.json" in outputs:
                try:
                    self._auto_diarization(folder, request["options"]["duration"])
                except (OSError, ValueError, RecursionError, TypeError):
                    if not partial:
                        raise
                    del outputs["diarization.json"]
        elif request["options"].get("whisper_model") in {"edda-turbo", "edda-large", "roest-v3"}:
            try:
                if "transcription.json" not in outputs:
                    raise ValueError("Danish output has no verified ASR conversion")
                self._converted_metadata(folder, inputs, request)
            except OwnershipConflict:
                raise
            except (OSError, ValueError, RecursionError, TypeError):
                if not partial:
                    raise
                outputs.pop("transcription.json", None)
                outputs.pop("alignment.json", None)
        return outputs

    def _finish_child(self, result):
        task_id, child = self.running_task_id, self.child
        child.wait()  # poll already established exit; reap before another GPU child starts.
        self.child, self.running_task_id = None, None
        if self.cohort_tasks:
            for member_id in self.cohort_tasks:
                done = self._cohort_done(member_id)
                self._finish_task(member_id, 0 if done and done["error_code"] is None else 1)
            self.cohort_tasks, self.cohort_id = [], None
            return
        self._finish_task(task_id, result)

    def _cohort_done(self, task_id):
        loaded = read_control(self._task_directory(task_id) / "runtime-complete.json")
        if not loaded:
            return None
        value = loaded[0]
        if (
            set(value)
            != {"protocol", "batch_id", "cohort_id", "task_id", "inputs_sha256", "error_code"}
            or not _matches(value, self.batch_id)
            or value["cohort_id"] != self.cohort_id
            or value["task_id"] != task_id
            or value["inputs_sha256"] != self.accepted[task_id]["inputs_sha256"]
            or (value["error_code"] is not None and not _choice(value["error_code"], ERROR_CODES))
        ):
            raise OwnershipConflict("Invalid Runtime cohort completion")
        return value

    def _finish_task(self, task_id, result):
        task = self.tasks[task_id]
        if result == 0:
            try:
                outputs = self._outputs(task_id)
            except (OSError, ValueError, RecursionError):
                self._outcome(
                    task, "failed", "invalid_output", outputs=self._outputs(task_id, partial=True)
                )
            else:
                self._outcome(task, "complete", outputs=outputs)
        else:
            error = read_control(self._task_directory(task_id) / "error.json")
            code = error[0].get("code") if error else None
            self._outcome(
                task,
                "failed",
                code if _choice(code, ERROR_CODES) else "processing",
                outputs=self._outputs(task_id, partial=True),
            )

    def _stop_child(self, error_code="stopped"):
        if self.child is None:
            return
        child, task_id = self.child, self.running_task_id
        if child.poll() is not None:
            self._finish_child(child.poll())
            return
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        self.child, self.running_task_id = None, None
        for member_id in self.cohort_tasks or [task_id]:
            done = self._cohort_done(member_id) if self.cohort_tasks else None
            if done:
                self._finish_task(member_id, 0 if done["error_code"] is None else 1)
            else:
                self._outcome(
                    self.tasks[member_id],
                    "interrupted",
                    error_code,
                    outputs=self._outputs(member_id, partial=True),
                )
        self.cohort_tasks, self.cohort_id = [], None

    def _start_next(self):
        pending = [
            task
            for task_id, task in self.tasks.items()
            if task_id in self.accepted and task_id not in self.outcomes
        ]
        pending.sort(key=lambda row: (row["ordinal"], row["attempt"], row["task_id"]))
        resident = self.manifest["limits"].get("resident_models") == "single-slot-v1"
        if resident and pending:
            if self.cohort_wait_started is None:
                self.cohort_wait_started = self.clock()
            if (
                not self.seal
                and not self.orphan
                and len(pending) < self.manifest["limits"].get("cohort_max_members", 8)
                and self.clock() - self.cohort_wait_started
                < self.manifest["limits"].get("cohort_wait_seconds", 2)
            ):
                return
        selected = []
        for task in pending:
            task_id = task["task_id"]
            if task_id not in self.inputs or not self._verify_inputs(task, self.inputs[task_id]):
                self._outcome(task, "failed", "input_changed")
                continue
            self._controls()
            if self.stopping or self.clock() - self.started >= self.manifest["limits"].get(
                "wall_seconds", 720 * 60
            ):
                return
            if resident:
                selected.append(task)
                if len(selected) >= self.manifest["limits"].get("cohort_max_members", 8):
                    break
                continue
            try:
                self.child = self.runner(self._task_directory(task_id), self.lock_fd)
            except (OSError, ValueError):
                self._outcome(task, "failed", "runtime")
                continue
            self.running_task_id = task_id
            self.child_started = self.clock()
            request = _parse(
                _read_bytes(self._task_directory(task_id) / "request.json", OUTPUT_LIMIT)
            )
            self.child_timeout = request["options"].get("timeout_minutes", 360) * 60
            break
        if selected:
            self.cohort_id = uuid.uuid4().hex
            folder = _safe(self.directory / "cohorts" / self.cohort_id)
            folder.mkdir(parents=True)
            write_json(
                folder / "cohort.json",
                {
                    "protocol": PROTOCOL,
                    "batch_id": self.batch_id,
                    "identity_hash": self.identity_hash,
                    "cohort_id": self.cohort_id,
                    "tasks": [self.accepted[row["task_id"]] for row in selected],
                },
                immutable=True,
            )
            try:
                self.child = self.cohort_runner(folder, self.lock_fd)
            except (OSError, ValueError):
                for task in selected:
                    self._outcome(task, "failed", "runtime")
                self.cohort_id = None
            else:
                self.cohort_tasks = [row["task_id"] for row in selected]
                self.running_task_id = self.cohort_tasks[0]
                self.child_started = self.clock()
                # The whole finite cohort never exceeds any member's frozen wall-time limit.
                self.child_timeout = min(
                    _parse(
                        _read_bytes(
                            self._task_directory(row["task_id"]) / "request.json", OUTPUT_LIMIT
                        )
                    )["options"].get("timeout_minutes", 360)
                    * 60
                    for row in selected
                )
            self.cohort_wait_started = None

    def _status(self):
        if self.child is not None and self.cohort_tasks:
            loaded = read_control(
                self.directory / "cohorts" / self.cohort_id / "cohort-status.json"
            )
            if loaded:
                value = loaded[0]
                if (
                    set(value) == {"protocol", "batch_id", "cohort_id", "task_id", "stage"}
                    and _matches(value, self.batch_id)
                    and value["cohort_id"] == self.cohort_id
                    and value["task_id"] in self.cohort_tasks
                    and _choice(
                        value["stage"], {"transcription", "alignment", "diarization", "complete"}
                    )
                ):
                    self.running_task_id = value["task_id"]
        current = self.membership["current_attempts"].values() if self.membership else ()
        completed, failed = 0, 0
        for task_id in current:
            outcome = self.outcomes.get(task_id)
            if outcome:
                completed += outcome["state"] == "complete"
                failed += outcome["state"] != "complete"
        result = {
            "protocol": PROTOCOL,
            "batch_id": self.batch_id,
            "generation": self.batch_id,
            "phase": self.phase,
            "membership_revision": self.membership["revision"] if self.membership else None,
            "seal_digest": self.seal_digest,
            "completed": completed,
            "failed": failed,
            "running_task_id": self.running_task_id,
        }
        write_json(self.directory / "batch-status.json", result)
        return result

    def step(self):
        if self.terminal:
            return self._terminal_status or self._status()
        try:
            self._read_membership()
            self._controls()
            self._receipts()
            if self.child is not None and (result := self.child.poll()) is not None:
                self._finish_child(result)
            if self.child is not None and self.clock() - self.child_started >= self.child_timeout:
                self._stop_child("timeout")
            expired = self.clock() - self.started >= self.manifest["limits"].get(
                "wall_seconds", 720 * 60
            )
            if self.stopping or expired:
                code = "stopped" if self.stopping else "timeout"
                self._stop_child(code)
                for task_id, task in self.tasks.items():
                    if task_id not in self.outcomes:
                        self._outcome(
                            task,
                            "interrupted" if task_id in self.accepted else "not_dispatched",
                            code,
                        )
                self.phase = "stopped" if self.stopping else "failed"
            else:
                self._accept()
                if self.child is None:
                    self._start_next()
                self.phase = (
                    "draining" if self.seal or self.orphan else "running" if self.child else "ready"
                )
                if (
                    self.orphan
                    and self.child is None
                    and all(task_id in self.outcomes for task_id in self.accepted)
                ):
                    self.phase = "orphan-drained"
                elif self.seal and all(task_id in self.outcomes for task_id in self.tasks):
                    self.seal_digest = self.seal["membership_sha256"]
                    self.phase = "finished"
        except (OSError, ValueError, RecursionError, TypeError):
            try:
                self._stop_child("ownership_conflict")
            except (OSError, ValueError):
                pass
            self.phase = "failed"
        return self._status()


def run(directory, *, poll_seconds=0.25):
    import fcntl

    directory = _safe(Path(directory).absolute())
    manifest = load_manifest(directory)
    validate_sources(directory, manifest)
    lock_path = _safe(directory / "runtime-batch.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # A surviving inference child still owns the same allocation.
        controller = BatchController(directory, lock_fd=lock.fileno())
        while not controller.terminal:
            controller.step()
            if not controller.terminal:
                time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["run", "status", "cohort"])
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    if arguments.operation == "run":
        run(arguments.directory)
    elif arguments.operation == "cohort":
        run_cohort(arguments.directory)
    else:
        print(json.dumps(status(arguments.directory)))


def status(directory):
    """Expose only controller-owned summary fields, never arbitrary uploaded JSON."""
    directory = _safe(Path(directory).absolute())
    manifest = load_manifest(directory)
    loaded = read_control(directory / "batch-status.json")
    if not loaded or not _matches(loaded[0], manifest["batch_id"]):
        return {"phase": "starting"}
    value = loaded[0]
    if not _choice(value.get("phase"), TERMINAL_PHASES | {"ready", "running", "draining"}):
        return {"phase": "starting"}
    return {
        "protocol": PROTOCOL,
        "batch_id": manifest["batch_id"],
        "generation": manifest["batch_id"],
        "phase": value["phase"],
        "membership_revision": value.get("membership_revision")
        if _integer(value.get("membership_revision"))
        else None,
        "seal_digest": value.get("seal_digest") if _hash(value.get("seal_digest")) else None,
        "completed": value.get("completed") if _integer(value.get("completed")) else 0,
        "failed": value.get("failed") if _integer(value.get("failed")) else 0,
        "running_task_id": value.get("running_task_id")
        if _id(value.get("running_task_id"))
        else None,
    }


if __name__ == "__main__":
    main()
