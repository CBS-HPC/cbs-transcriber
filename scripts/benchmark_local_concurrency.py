"""Synthetic local-lane concurrency benchmark (ADR 0146): no network, no real recordings.

Run: uv run python scripts/benchmark_local_concurrency.py --output NEW_DIRECTORY

Generates seeded tone/noise WAV, MP3 and M4A files with the bundled FFmpeg, hard-links them into
fresh temporary libraries and drives the real queue lanes: playback media preparation, original
source inspection, input admission and API transcription with a mocked provider that sleeps for
a fixed latency. Reports wall time, time to first completion, per-job latency, machine CPU,
process-tree CPU, peak private memory, SQLite write-lock waits and claim-lock waits as JSON.
"""

import argparse
import itertools
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from cbs_transcriber import (
    audio_inputs,
    ffmpeg_runtime,
    input_admission,
    jobs,
    media,
    original_inspection,
    providers,
)
from cbs_transcriber.store import Store

FORMATS = ("wav", "mp3", "m4a")
LANES = ("media", "inspect", "admission", "api", "pipeline", "idle", "idle-default")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")


# --- Instrumentation -------------------------------------------------------------------------


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.connections = 0
            self.immediate = []
            self.locked = 0
            self.polls = {}
            self.spans = {}
            self.errors = []

    def span(self, kind, key, start, end):
        with self.lock:
            self.spans.setdefault(kind, []).append((key, start, end))

    def poll(self, name, seconds):
        with self.lock:
            self.polls.setdefault(name, []).append(seconds)


STATS = Stats()
_real_connect = sqlite3.connect


class TimedConnection(sqlite3.Connection):
    """Counts connections and measures how long BEGIN IMMEDIATE waits for the write lock."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        with STATS.lock:
            STATS.connections += 1

    def execute(self, sql, parameters=(), /):
        if isinstance(sql, str) and sql.lstrip()[:15].upper() == "BEGIN IMMEDIATE":
            start = time.perf_counter()
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError:
                with STATS.lock:
                    STATS.locked += 1
                raise
            finally:
                waited = time.perf_counter() - start
                with STATS.lock:
                    STATS.immediate.append(waited)
        return super().execute(sql, parameters)


def _connect(*args, **kwargs):
    kwargs.setdefault("factory", TimedConnection)
    return _real_connect(*args, **kwargs)


class TimedRLock:
    """Drop-in claim lock that records acquisition waits and outermost hold time."""

    def __init__(self):
        self._lock = threading.RLock()
        self._local = threading.local()
        self.waits = []
        self.hold = 0.0

    def acquire(self, blocking=True, timeout=-1):
        start = time.perf_counter()
        acquired = self._lock.acquire(blocking, timeout)
        if acquired:
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                now = time.perf_counter()
                self.waits.append(now - start)
                self._local.since = now
            self._local.depth = depth + 1
        return acquired

    def release(self):
        self._local.depth -= 1
        if self._local.depth == 0:
            self.hold += time.perf_counter() - self._local.since
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def install_hooks(latency):
    sqlite3.connect = _connect

    def wrap(module, name, kind, key):
        original = getattr(module, name)

        def wrapped(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                STATS.span(kind, key(args), start, time.perf_counter())

        setattr(module, name, wrapped)

    original_process = jobs.JobQueue.process

    def process(self, identifier):
        start = time.perf_counter()
        try:
            return original_process(self, identifier)
        except Exception:
            with STATS.lock:
                STATS.errors.append(traceback.format_exc(limit=6)[-1500:])
            raise
        finally:
            STATS.span("process:" + self.kind, identifier, start, time.perf_counter())

    jobs.JobQueue.process = process
    wrap(media, "prepare_media", "media", lambda args: str(args[0]))
    wrap(original_inspection, "run", "inspect", lambda args: args[1]["job_id"])
    wrap(input_admission, "prepare", "admission", lambda args: args[2]["jobs"][0]["id"])

    for module, name in ((input_admission, "claim"), (original_inspection, "claim")):
        original = getattr(module, name)

        def claim(*args, _original=original, _name=module.__name__ + ".claim", **kwargs):
            start = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                STATS.poll(_name, time.perf_counter() - start)

        setattr(module, name, claim)
    original_claim_job = Store.claim_job

    def claim_job(self, kind):
        start = time.perf_counter()
        try:
            return original_claim_job(self, kind)
        finally:
            STATS.poll("store.claim_job:" + kind, time.perf_counter() - start)

    Store.claim_job = claim_job

    def transcribe_file(path, settings, key, request_fields=None):
        time.sleep(latency)
        return {"segments": [{"start": 0, "end": 2, "text": "Synthetic benchmark segment."}]}

    providers.transcribe_file = transcribe_file


# --- Windows resource sampling ---------------------------------------------------------------

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _ENTRY(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    class _MEMORY(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    _u64 = ctypes.POINTER(ctypes.c_ulonglong)
    _k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ENTRY)]
    _k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ENTRY)]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_MEMORY),
        wintypes.DWORD,
    ]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE, _u64, _u64, _u64, _u64]
    _k32.GetSystemTimes.argtypes = [_u64, _u64, _u64]
    _INVALID = wintypes.HANDLE(-1).value

    def _memory(handle):
        counters = _MEMORY()
        counters.cb = ctypes.sizeof(_MEMORY)
        if _k32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return counters.PrivateUsage
        return 0

    def _cpu(handle):
        values = [ctypes.c_ulonglong() for _ in range(4)]
        if _k32.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return (values[2].value + values[3].value) / 1e7
        return 0.0

    def _system_times():
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        return idle.value / 1e7, (kernel.value + user.value) / 1e7

    def _children():
        snapshot = _k32.CreateToolhelp32Snapshot(0x2, 0)
        if snapshot == _INVALID:
            return []
        found, entry, parent = [], _ENTRY(), os.getpid()
        entry.dwSize = ctypes.sizeof(_ENTRY)
        try:
            ok = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                if entry.th32ParentProcessID == parent:
                    found.append(entry.th32ProcessID)
                ok = _k32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            _k32.CloseHandle(snapshot)
        return found

    def physical_cores():
        size = wintypes.DWORD(0)
        _k32.GetLogicalProcessorInformation(None, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not _k32.GetLogicalProcessorInformation(buffer, ctypes.byref(size)):
            return None
        width = 2 * ctypes.sizeof(ctypes.c_size_t) + 16
        offset = ctypes.sizeof(ctypes.c_size_t)
        return sum(
            int.from_bytes(buffer.raw[start + offset : start + offset + 4], "little") == 0
            for start in range(0, size.value, width)
        )


class Monitor(threading.Thread):
    """Samples machine CPU and the private memory/CPU of this process and its children."""

    def __init__(self, interval=0.1):
        super().__init__(daemon=True)
        self.interval, self.done = interval, threading.Event()
        self.child_cpu, self.peak_private, self.peak_children = {}, 0, 0
        self.peak_python = 0

    def __enter__(self):
        self.started = time.perf_counter()
        self.own_cpu = sum(os.times()[:2])
        self.system = _system_times() if os.name == "nt" else None
        self.start()
        return self

    def __exit__(self, *exc):
        self.done.set()
        self.join()
        self.wall = time.perf_counter() - self.started
        self.own_cpu = sum(os.times()[:2]) - self.own_cpu
        if self.system is not None:
            idle, total = _system_times()
            busy = (total - self.system[1]) - (idle - self.system[0])
            self.machine_cpu = busy / max(total - self.system[1], 1e-9)
        else:
            self.machine_cpu = None

    def run(self):
        while not self.done.wait(self.interval):
            if os.name != "nt":
                continue
            own = _memory(_k32.GetCurrentProcess())
            self.peak_python = max(self.peak_python, own)
            total, count = own, 0
            for pid in _children():
                handle = _k32.OpenProcess(0x1000 | 0x0010, False, pid)
                if not handle:
                    continue
                try:
                    total += _memory(handle)
                    self.child_cpu[pid] = max(self.child_cpu.get(pid, 0.0), _cpu(handle))
                    count += 1
                finally:
                    _k32.CloseHandle(handle)
            self.peak_private = max(self.peak_private, total)
            self.peak_children = max(self.peak_children, count)

    def report(self):
        return {
            "machine_cpu_utilization": round(self.machine_cpu, 3)
            if self.machine_cpu is not None
            else None,
            "python_cpu_seconds": round(self.own_cpu, 2),
            "child_cpu_seconds_sampled": round(sum(self.child_cpu.values()), 2),
            "peak_private_mb": round(self.peak_private / 1e6, 1),
            "peak_python_private_mb": round(self.peak_python / 1e6, 1),
            "peak_child_processes": self.peak_children,
        }


# --- Fixtures --------------------------------------------------------------------------------


class Credentials:
    def get(self, scope):
        return ""

    def set(self, scope, value, persist):
        pass


def generate_sources(directory, count, seconds):
    """Seeded, distinct tone/noise recordings; generation time is excluded from results."""
    directory.mkdir(parents=True, exist_ok=True)
    executable = ffmpeg_runtime.resolve()
    sources = []
    for index in range(count):
        fmt = FORMATS[index % len(FORMATS)]
        target = directory / f"source-{index:02d}-{seconds}s.{fmt}"
        sources.append(target)
        if target.exists():
            continue
        codec = {
            "wav": ["-c:a", "pcm_s16le", "-ar", "48000"],
            "mp3": ["-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100"],
            "m4a": ["-c:a", "aac", "-b:a", "128k", "-ar", "48000"],
        }[fmt]
        graph = (
            f"anoisesrc=d={seconds}:c=pink:r=48000:a=0.08:seed={index + 1}[n];"
            f"sine=f={140 + 7 * index}:d={seconds}:r=48000,tremolo=f=0.6:d=0.9[s];"
            "[n][s]amix=inputs=2:normalize=0[out]"
        )
        pending = target.with_name(target.stem + ".pending." + fmt)
        subprocess.run(
            [executable, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
            + ["-filter_complex", graph, "-map", "[out]", "-ac", "2", *codec, str(pending)],
            check=True,
            stdin=subprocess.DEVNULL,
        )
        pending.replace(target)
    return sources


def library(root, sources, files):
    store = Store(root / "library")
    store.default_parent = root / "projects"
    store.default_parent.mkdir(parents=True, exist_ok=True)
    project = store.create_project("Synthetic concurrency benchmark")
    settings = providers.Settings(
        provider_type="custom",
        base_url="http://127.0.0.1:9/v1",
        model="synthetic",
        capability="segments",
    )
    provider = providers.save_profile(store, Credentials(), "Synthetic", settings, remember=False)
    provider = providers.profile(store, provider["id"])
    rows = []
    for index in range(files):
        source = sources[index % len(sources)]
        row = store.create_recording(project["id"], f"file-{index:02d}{source.suffix}")
        directory = store.directory(row["id"])
        directory.mkdir(parents=True, exist_ok=True)
        os.link(source, directory / ("original" + source.suffix))
        rows.append(row)
    return store, provider, rows


def build_queue(store, counts, *, lock=None):
    """Explicit lane counts through the real queue type; extra lanes are simply not started."""
    lock = lock or TimedRLock()
    settings = {
        {"inspect": "inspection"}.get(lane, lane): count for lane, count in counts.items() if count
    }
    try:
        queue = jobs.JobQueue(store, Credentials(), lock=lock, concurrency=settings)
    except TypeError:
        queue = None  # Builds before configurable lanes (ADR 0146): assemble explicitly.
    if queue is not None:
        wanted = {lane_kind(lane) for lane, count in counts.items() if count}
        queue.workers = [worker for worker in queue.workers if worker.kind in wanted]
        return queue
    kinds = [lane_kind(lane) for lane, count in counts.items() for _ in range(count)]
    queue = jobs.JobQueue(store, Credentials(), kind=kinds[0], lock=lock)
    queue.workers = [queue] + [
        jobs.JobQueue(store, Credentials(), kind=kind, lock=lock) for kind in kinds[1:]
    ]
    for worker in queue.workers:
        worker.coordinator = queue
        worker.runtime_preparation_gate = queue.runtime_preparation_gate
        worker.runtime_transfer_gate = queue.runtime_transfer_gate
    return queue


def lane_kind(lane):
    legacy = not hasattr(jobs, "LOCAL_LANES")
    return {
        "media": "media",
        "inspect": "source-inspect",
        "admission": "input-admission",
        "api": "transcribe" if legacy else "transcribe-api",
    }[lane]


def wait_for(store, sql, expected, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.one(sql)["n"] >= expected:
            return True
        time.sleep(0.05)
    return False


INSPECTED = (
    "SELECT count(*) AS n FROM jobs WHERE kind='source-inspect' AND state IN "
    "('completed','failed','cancelled')"
)
ADMITTED = (
    "SELECT count(*) AS n FROM jobs WHERE kind='transcribe' AND (state IN ('failed','cancelled') "
    "OR json_type(params,'$.input_intent') IS NULL)"
)
TRANSCRIBED = "SELECT count(*) AS n FROM jobs WHERE kind='transcribe' AND state IN " + str(TERMINAL)
MEDIA_DONE = "SELECT count(*) AS n FROM jobs WHERE kind='media' AND state IN " + str(TERMINAL)


def run_queue(store, counts, done_sql, expected, timeout):
    lock = TimedRLock()
    queue = build_queue(store, counts, lock=lock)
    STATS.reset()
    with Monitor() as monitor:
        started = STATS.started = time.perf_counter()
        queue.start()
        firsts = None
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                done = store.one(done_sql)["n"]
                if done and firsts is None:
                    firsts = time.perf_counter() - started
                if done >= expected:
                    break
                time.sleep(0.05)
            wall = time.perf_counter() - started
        finally:
            queue.stop()
    return wall, firsts, monitor, lock


def summarize(store, wall, first, monitor, lock, files, kinds):
    spans = [end - start for kind in kinds for _, start, end in STATS.spans.get(kind, [])]
    waits = sorted(STATS.immediate)
    failures = store.all(
        "SELECT kind,message FROM jobs WHERE state IN ('failed','cancelled','interrupted')"
    )
    return {
        "files": files,
        "wall_seconds": round(wall, 2),
        "first_completed_seconds": round(first, 2) if first is not None else None,
        "files_per_minute": round(60 * files / wall, 2),
        "job_seconds_p50": round(statistics.median(spans), 2) if spans else None,
        "job_seconds_max": round(max(spans), 2) if spans else None,
        **monitor.report(),
        "sqlite_connections": STATS.connections,
        "sqlite_immediate_transactions": len(waits),
        "sqlite_immediate_wait_total_seconds": round(sum(waits), 3),
        "sqlite_immediate_wait_p99_ms": round(1000 * waits[int(0.99 * (len(waits) - 1))], 2)
        if waits
        else None,
        "sqlite_immediate_wait_max_ms": round(1000 * waits[-1], 2) if waits else None,
        "sqlite_locked_errors": STATS.locked,
        "claim_lock_acquisitions": len(lock.waits),
        "claim_lock_wait_max_ms": round(1000 * max(lock.waits), 2) if lock.waits else None,
        "claim_lock_hold_fraction": round(lock.hold / wall, 4),
        "claim_polls": {
            name: {"count": len(values), "total_seconds": round(sum(values), 3)}
            for name, values in sorted(STATS.polls.items())
        },
        "failures": len(failures),
        "failure_messages": sorted({row["message"][:160] for row in failures})[:5],
        "first_traceback": STATS.errors[0] if STATS.errors else None,
    }


def accept_intents(store, provider, rows):
    choice = input_admission.choice(provider=provider)
    return input_admission.accept(
        store, [{"recording_id": row["id"], "choice": choice} for row in rows]
    )


def idle_default(root, args):
    """The complete default queue on an empty library: background claim polling only."""
    store, _, _ = library(root, [], 0)
    lock = TimedRLock()
    with store.library_lock():
        queue = jobs.JobQueue(store, Credentials(), lock=lock)
        STATS.reset()
        with Monitor() as monitor:
            queue.start()
            time.sleep(args.idle_seconds)
            queue.stop()
    report = summarize(store, monitor.wall, None, monitor, lock, 0, [])
    report.update(files_per_minute=None, threads=len(queue.workers))
    return report


def scenario(name, workers, root, sources, args):
    if name == "idle-default":
        return idle_default(root, args)
    store, provider, rows = library(root, sources, args.files)
    files = len(rows)
    with store.library_lock():
        if name == "media":
            for row in rows:
                store.add_job(row["id"], "media")
            result = run_queue(store, {"media": workers}, MEDIA_DONE, files, args.timeout)
            return summarize(store, *result, files, ["media"])
        accept_intents(store, provider, rows)
        if name == "idle":
            # Intents wait on inspections that never start: this measures idle claim polling.
            lock = TimedRLock()
            queue = build_queue(store, {"admission": workers}, lock=lock)
            STATS.reset()
            with Monitor() as monitor:
                queue.start()
                time.sleep(args.idle_seconds)
                queue.stop()
            report = summarize(store, monitor.wall, None, monitor, lock, files, [])
            report["files_per_minute"] = None
            return report
        if name == "inspect":
            result = run_queue(store, {"inspect": workers}, INSPECTED, files, args.timeout)
            return summarize(store, *result, files, ["inspect"])
        assert run_queue(store, {"inspect": 8}, INSPECTED, files, args.timeout)[0]
        if name == "admission":
            result = run_queue(store, {"admission": workers}, ADMITTED, files, args.timeout)
            return summarize(store, *result, files, ["admission"])
        run_queue(store, {"admission": 2}, ADMITTED, files, args.timeout)
        result = run_queue(store, {"api": workers}, TRANSCRIBED, files, args.timeout)
        return summarize(store, *result, files, ["process:" + lane_kind("api")])


def pipeline(counts, root, sources, args):
    """Upload-and-transcribe: playback media, inspection, admission and API lanes together."""
    store, provider, rows = library(root, sources, args.files)
    with store.library_lock():
        for row in rows:
            store.add_job(row["id"], "media")
        accept_intents(store, provider, rows)
        done = f"SELECT ({TRANSCRIBED}) + ({MEDIA_DONE}) AS n"
        result = run_queue(store, counts, done, 2 * len(rows), args.timeout)
        report = summarize(store, *result, len(rows), ["media", "inspect"])
        ends = sorted(
            end - STATS.started for _, _, end in STATS.spans.get("process:" + lane_kind("api"), [])
        )
        playback = sorted(end - STATS.started for _, _, end in STATS.spans.get("media", []))
        report.update(
            first_transcript_seconds=round(ends[0], 2) if ends else None,
            all_transcripts_seconds=round(ends[-1], 2) if ends else None,
            all_playback_seconds=round(playback[-1], 2) if playback else None,
        )
        return report


def parse_counts(value):
    parts = [int(part) for part in value.split("/")]
    if len(parts) != 4 or any(not 0 <= part <= 16 for part in parts):
        raise argparse.ArgumentTypeError("Use MEDIA/INSPECT/ADMISSION/API, for example 4/4/2/4.")
    return dict(zip(("media", "inspect", "admission", "api"), parts, strict=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources", type=Path, help="Reuse generated sources from this folder")
    parser.add_argument("--files", type=int, default=16)
    parser.add_argument("--seconds", type=int, default=300, help="Duration of each source")
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 6, 8])
    parser.add_argument("--lanes", nargs="+", choices=LANES, default=list(LANES[:4]))
    parser.add_argument("--pipeline", type=parse_counts, nargs="*", default=[])
    parser.add_argument("--api-latency", type=float, default=3.0)
    parser.add_argument("--idle-seconds", type=float, default=10.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--keep", action="store_true", help="Keep run libraries")
    args = parser.parse_args(argv)
    if not 1 <= args.files <= 64 or not 10 <= args.seconds <= 3600:
        parser.error("Use 1..64 files of 10..3600 seconds.")
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"
    os.environ["CBS_TRANSCRIBER_NO_REMOTE_RELEASE"] = "1"
    install_hooks(args.api_latency)
    sources = generate_sources(args.sources or args.output / "sources", args.files, args.seconds)
    audio_inputs._decoder()  # Warm the decoder identity cache outside every timed phase.
    report = {
        "schema": "cbs.transcriber.local_concurrency_benchmark.v1",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "host": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "logical_cpus": os.cpu_count(),
            "physical_cores": physical_cores() if os.name == "nt" else None,
            "ffmpeg": ffmpeg_runtime.version(ffmpeg_runtime.for_identity()),
        },
        "parameters": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "source_bytes": {path.suffix: path.stat().st_size for path in sources},
        "runs": [],
    }
    counter = itertools.count()
    plan = [
        (lane, workers)
        for lane in args.lanes
        if lane not in {"pipeline", "idle-default"}
        for workers in args.workers
    ]
    plan += [("idle-default", None)] if "idle-default" in args.lanes else []
    plan += [("pipeline", counts) for counts in args.pipeline]
    for repeat in range(args.repeat):
        for lane, workers in plan if repeat % 2 == 0 else reversed(plan):
            root = args.output / "runs" / f"{next(counter):03d}-{lane}"
            root.mkdir(parents=True)
            background = None
            if os.name == "nt":
                # Other software shares this computer; record its load just before each run.
                before = _system_times()
                time.sleep(1)
                idle, total = _system_times()
                background = round(1 - (idle - before[0]) / max(total - before[1], 1e-9), 3)
            try:
                if lane == "pipeline":
                    result = pipeline(workers, root, sources, args)
                else:
                    result = scenario(lane, workers, root, sources, args)
            finally:
                if not args.keep:
                    shutil.rmtree(root, ignore_errors=True)
            entry = {
                "lane": lane,
                "workers": workers,
                "repeat": repeat,
                "background_cpu_before": background,
                **result,
            }
            report["runs"].append(entry)
            print(
                f"{lane:12} {json.dumps(workers):40} bg={background} "
                f"wall={result['wall_seconds']:7.2f}s "
                f"first={result['first_completed_seconds']} "
                f"cpu={result['machine_cpu_utilization']} mem={result['peak_private_mb']}MB "
                f"lockwait={result['sqlite_immediate_wait_max_ms']}ms fail={result['failures']}",
                flush=True,
            )
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
