"""Bounded offline /api/jobs projection experiment; no server, workers or real library.

Run with an existing environment and PYTHONPATH=src. The fixture is metadata-only:
it measures ordinary upload-group projection, not media/admission or native/Runtime owners.
Large runs belong after other integration suites, not concurrently with them.
"""

import argparse
import hashlib
import importlib
import json
import os
import platform
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

CANARY = "SYNTHETIC-PRIVATE-ACTIVITY-CANARY"
SOURCE = Path(__file__).resolve().parents[1]
MODULES = ("app", "jobs", "store", "native_batch", "runtime_batches")
SYSTEM = {"win32": "Windows", "darwin": "Darwin", "linux": "Linux"}.get(sys.platform, sys.platform)


def bounds(groups, members, samples):
    if (
        type(groups) is not int
        or not 1 <= groups <= 200
        or type(members) is not int
        or not 2 <= members <= 50
        or groups * members <= 200
        or type(samples) is not int
        or not 1 <= samples <= 9
    ):
        raise ValueError("Use 1–200 groups, 2–50 members, >200 total jobs and 1–9 samples.")


def identifier(value):
    return f"{value:032x}"


def seed(store, groups, members):
    """Seed exact metadata expectations without claiming an import or producer receipt."""
    project = store.create_project("Synthetic Activity scale")
    foreign = store.create_project("Synthetic separate project")
    expected, recordings, jobs = {}, [], []
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for member in range(members):
        for group in range(groups):
            ordinal = member * groups + group
            rid, jid = identifier(ordinal + 1), identifier(1_000_000 + ordinal)
            marker = identifier(2_000_000 + group)
            stamp = (start + timedelta(seconds=ordinal)).isoformat()
            recordings.append((rid, project["id"], f"Synthetic {ordinal}", "synthetic.wav", stamp))
            jobs.append(
                (
                    jid,
                    rid,
                    json.dumps({"upload_batch": marker, "private_payload": CANARY}),
                    json.dumps([{"text": CANARY}]),
                    stamp,
                    stamp,
                )
            )
            expected[jid] = {
                "recording_id": rid,
                "project_id": project["id"],
                "upload_batch": marker,
            }
    # Same upload marker in another project must not expand this older unselected job.
    rid, jid = identifier(3_000_000), identifier(4_000_000)
    old = (start - timedelta(days=1)).isoformat()
    recordings.append((rid, foreign["id"], "Synthetic foreign", "synthetic.wav", old))
    jobs.append((jid, rid, json.dumps({"upload_batch": identifier(2_000_000)}), "[]", old, old))
    with store.connection() as db:
        db.executemany(
            "INSERT INTO recordings(id,project_id,name,filename,created,status) "
            "VALUES (?,?,?,?,?,'ready')",
            recordings,
        )
        db.executemany(
            "INSERT INTO jobs(id,recording_id,kind,state,progress,params,chunks,created,updated) "
            "VALUES (?,?,'media','completed',1,?,?,?,?)",
            jobs,
        )
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    return expected


def verify(rows, expected):
    """Independent public membership/privacy oracle; no Activity helper is called here."""
    assert isinstance(rows, list) and len(rows) == len(expected), "Unexpected response row count"
    assert {row["id"] for row in rows} == expected.keys(), "Missing/extra/foreign job"
    assert CANARY not in json.dumps(rows), "Private parameters/chunks entered the response"
    for row in rows:
        assert all(row.get(key) == value for key, value in expected[row["id"]].items())
        assert row["kind"] == "media" and row["state"] == "completed" and row["progress"] == 1
        assert row["activity_membership_complete"] is True
        assert row["activity_group_limit"] == 1000
        assert "params" not in row and "chunks" not in row


def inventory(store):
    """Same-process SQL-dump fingerprint, not a portable backup or canonical row codec."""
    with store.connection() as db:
        db.execute("BEGIN")
        return hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()


class NoCredentials:
    def get(self, *args, **kwargs):
        raise AssertionError("The Activity experiment must not access credentials.")

    set = get


def refuse(*args, **kwargs):
    raise AssertionError("The Activity experiment must not open a network connection or child.")


def run(groups=200, members=10, samples=5):
    if not __debug__:
        raise RuntimeError(
            "Run without Python optimization; the correctness assertions are required."
        )
    bounds(groups, members, samples)
    # providers imports its catalog eagerly: refuse inherited private catalogs first.
    if "CBS_TRANSCRIBER_MODELS" in os.environ:
        raise ValueError("Remove CBS_TRANSCRIBER_MODELS before this offline experiment.")
    from fastapi.testclient import TestClient

    socket_pair, socket_connect = socket.socketpair, socket.socket.connect
    control = threading.local()

    def pair(*args, **kwargs):
        # Windows asyncio's self-pipe uses the standard library's loopback socket pair.
        # Only that trusted constructor may connect, on its own calling thread.
        control.pair = True
        try:
            return socket_pair(*args, **kwargs)
        finally:
            control.pair = False

    def connect_control(sock, *args, **kwargs):
        if getattr(control, "pair", False):
            return socket_connect(sock, *args, **kwargs)
        return refuse()

    with (
        tempfile.TemporaryDirectory(prefix="cbs-activity-poll-") as temporary,
        patch.dict(
            os.environ,
            {
                "CBS_TRANSCRIBER_NO_STARTUP_CHECK": "1",
                "CBS_TRANSCRIBER_NO_REMOTE_RELEASE": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
        ),
        patch.object(socket, "socketpair", pair),
        patch.object(socket, "getaddrinfo", refuse),
        patch.object(socket.socket, "connect", connect_control),
        patch.object(socket.socket, "connect_ex", refuse),
        patch.object(socket.socket, "sendto", refuse),
        patch.object(subprocess, "Popen", refuse),
        # Some Windows Python builds let platform.system() fall back to `cmd /c ver`.
        # sys.platform gives the same OS classification without launching that child.
        patch.object(platform, "system", return_value=SYSTEM),
    ):
        imported = {name: importlib.import_module(f"cbs_transcriber.{name}") for name in MODULES}
        for name, module in imported.items():
            expected = SOURCE / "src/cbs_transcriber" / f"{name}.py"
            if Path(module.__file__).resolve() != expected.resolve():
                raise ValueError("Use PYTHONPATH=src from the matching benchmark checkout.")

        app = imported["app"].create_app(
            Path(temporary) / "library", start_worker=False, credentials=NoCredentials()
        )
        store = app.state.store
        with TestClient(app, base_url="http://127.0.0.1") as client:
            expected = seed(store, groups, members)
            before = inventory(store)

            def poll():
                started = time.perf_counter()
                response = client.get("/api/jobs")
                received = time.perf_counter()
                assert response.status_code == 200, response.text
                rows = response.json()
                decoded = time.perf_counter()
                verify(rows, expected)
                return {
                    "request_response_seconds": received - started,
                    "json_decode_seconds": decoded - received,
                    "response_bytes": len(response.content),
                    "returned_rows": len(rows),
                }

            poll()  # Warmup is explicit and excluded from the reported warm samples.
            observed = [poll() for _ in range(samples)]
            statements = Counter()
            connect = sqlite3.connect

            def traced_connect(*args, **kwargs):
                db = connect(*args, **kwargs)
                db.set_trace_callback(
                    lambda sql: statements.update([sql.lstrip().split()[0].upper()])
                )
                return db

            # Instrumentation overhead is excluded from the latency samples above.
            with patch.object(sqlite3, "connect", traced_connect):
                poll()
            assert not (
                statements.keys()
                & {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}
            )
            assert not tracemalloc.is_tracing(), "Do not interfere with an existing memory observer"
            tracemalloc.start()
            try:
                poll()
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            assert inventory(store) == before, "Read-only polls changed the synthetic database"

    return {
        "format": "cbs-activity-poll-experiment-v1",
        "scope": "in-process ASGI GET /api/jobs; ordinary upload groups; metadata-only fixture",
        "groups": groups,
        "members_per_group": members,
        "history_rows": groups * members + 1,
        "recent_row_limit_exercised": 200,
        "samples": observed,
        "warm_request_response_median_seconds": statistics.median(
            item["request_response_seconds"] for item in observed
        ),
        "sql_statements_in_separate_poll": dict(sorted(statements.items())),
        "python_tracemalloc_peak_bytes_in_separate_poll": peak,
        "exact_membership_and_project_scope": True,
        "private_parameters_and_chunks_excluded": True,
        "database_sql_dump_unchanged": True,
        "environment": {
            "os": SYSTEM,
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        },
        "source_sha256": {
            path: hashlib.sha256((SOURCE / path).read_bytes()).hexdigest()
            for path in (
                "scripts/benchmark_activity_poll.py",
                *(f"src/cbs_transcriber/{name}.py" for name in MODULES),
            )
        },
        "not_measured": [
            "browser DOM/rendering and real HTTP/network",
            "cold startup or fixture construction",
            "populated native/Runtime owners, intents, inspection or restored-history projection",
            "concurrent mutations, workers or remote execution",
            "process RSS, native/SQLite memory or real device/provider acceptance",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=int, default=200)
    parser.add_argument("--members", type=int, default=10)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.groups, args.members, args.samples), indent=2))


if __name__ == "__main__":
    main()
