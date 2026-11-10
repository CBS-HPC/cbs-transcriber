"""Synthetic-only parent protocol/lifecycle tests; real heap limits never run in pytest."""

import copy
import hashlib
import io
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from cbs_transcriber import portable_database as database
from cbs_transcriber import portable_rows


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def empty_claims():
    return {
        "schema_version": 12,
        "schema_fingerprint": database.SCHEMA_FINGERPRINT,
        "tables": {
            name: {
                key: value
                for key, value in portable_rows.digest_table(name, []).items()
                if key in {"rows", "sha256"}
            }
            for name in database.TABLES
        },
    }


@pytest.fixture
def subject(tmp_path):
    path = tmp_path / "owned-object"
    raw = b"synthetic non-SQLite bytes"
    path.write_bytes(raw)
    return path, {
        "expected_sha256": sha(raw),
        "expected_size": len(raw),
        "expected_claims": empty_claims(),
    }


@pytest.fixture
def processes(monkeypatch):
    original, children = database.subprocess.Popen, []

    def track(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append((process, args, kwargs))
        return process

    monkeypatch.setattr(database.subprocess, "Popen", track)
    yield children
    assert all(process.poll() is not None for process, _, _ in children)
    assert all(process.stdin.closed and process.stdout.closed for process, _, _ in children)
    assert not [
        thread for thread in threading.enumerate() if thread.name.startswith("cbs-portable-")
    ]


FAKE_PREFIX = """import hashlib,json,sys
raw=sys.stdin.buffer.read(262145)
request=json.loads(raw)
tables={name:dict(claim,encoded_bytes=100)
    for name,claim in request['expected_claims']['tables'].items()}
result={'schema_version':12,'schema_fingerprint':request['expected_claims']['schema_fingerprint'],'tables':tables,'sqlite_version':'3.45.3','heap_cap_bytes':67108864,'heap_sentry':'allocation_refused_before_open'}
reply={'protocol':'cbs-portable-database-v1','request_sha256':hashlib.sha256(raw).hexdigest(),'status':'ok','result':result}
"""
FAKE_SEND = (
    "sys.stdout.buffer.write("
    "json.dumps(reply,sort_keys=True,separators=(',',':')).encode('utf-8'))\n"
)


def fake_worker(monkeypatch, code=FAKE_PREFIX + FAKE_SEND, *, large_input=False):
    source = code.encode("utf-8")
    monkeypatch.setattr(database, "WORKER_SHA256_LF", sha(source))
    monkeypatch.setattr(
        database,
        "_sources",
        lambda check: (source, b"not executed", b"x" * 120000 if large_input else b"not executed"),
    )


def test_v140_parent_observation_binds_supplied_reference_and_closes_child(
    subject, processes, monkeypatch
):
    path, args = subject
    fake_worker(monkeypatch)
    before = path.read_bytes()
    result = database.inspect_database_object(path, **args)
    assert result["level"] == "database_claims_only"
    assert result["reference"] == "matched_supplied_database"
    assert result["database_sha256"] == args["expected_sha256"]
    assert result["restore_ready"] is False
    assert "history_payloads_and_active_mirrors" in result["not_checked"]
    assert "sealed_package_binding" in result["not_checked"]
    assert str(path) not in json.dumps(result)
    assert path.read_bytes() == before and len(processes) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "schema13",
        "bool_schema",
        "wrong_schema_hash",
        "missing_table",
        "extra_table",
        "bool_rows",
        "too_many_rows",
        "bad_hash",
        "extra_claim",
        "total_rows",
    ],
)
def test_v140_invalid_claims_fail_before_guard_or_child(subject, monkeypatch, damage):
    path, args = subject
    claims = args["expected_claims"]
    first = database.TABLES[0]
    if damage == "missing":
        del claims["tables"]
    elif damage == "schema13":
        claims["schema_version"] = 13
    elif damage == "bool_schema":
        claims["schema_version"] = True
    elif damage == "wrong_schema_hash":
        claims["schema_fingerprint"] = "0" * 64
    elif damage == "missing_table":
        del claims["tables"][first]
    elif damage == "extra_table":
        claims["tables"]["extra"] = {"rows": 0, "sha256": "0" * 64}
    elif damage == "bool_rows":
        claims["tables"][first]["rows"] = True
    elif damage == "too_many_rows":
        claims["tables"][first]["rows"] = 1_000_001
    elif damage == "bad_hash":
        claims["tables"][first]["sha256"] = "PRIVATE_CANARY"
    elif damage == "extra_claim":
        claims["tables"][first]["path"] = "PRIVATE_CANARY"
    else:
        for name in database.TABLES[:3]:
            claims["tables"][name]["rows"] = 1_000_000
    monkeypatch.setattr(database, "_run_child", lambda *a: pytest.fail("Unexpected child"))
    with pytest.raises(ValueError) as error:
        database.inspect_database_object(
            path, **args, guard=lambda: pytest.fail("Unexpected guard")
        )
    assert "PRIVATE_CANARY" not in str(error.value)


def test_v140_claims_are_copied_before_guard_mutates_original(subject, monkeypatch, processes):
    path, args = subject
    original = copy.deepcopy(args["expected_claims"])
    fake_worker(monkeypatch)

    def mutate():
        args["expected_claims"]["tables"][database.TABLES[0]]["sha256"] = "PRIVATE_CANARY"

    result = database.inspect_database_object(path, **args, guard=mutate)
    assert (
        result["tables"][database.TABLES[0]]["sha256"]
        == original["tables"][database.TABLES[0]]["sha256"]
    )


@pytest.mark.parametrize("damage", ["hash", "size", "sidecar", "hardlink", "relative"])
def test_v140_input_bounds_refuse_before_child(subject, monkeypatch, damage):
    path, args = subject
    if damage == "hash":
        args["expected_sha256"] = "0" * 64
    elif damage == "size":
        args["expected_size"] += 1
    elif damage == "sidecar":
        Path(str(path) + "-wal").write_bytes(b"")
    elif damage == "hardlink":
        os.link(path, path.parent / "alias")
    else:
        path = Path("relative")
    monkeypatch.setattr(database, "_run_child", lambda *a: pytest.fail("Unexpected child"))
    with pytest.raises(ValueError):
        database.inspect_database_object(path, **args)


@pytest.mark.parametrize(
    "edit",
    [
        "reply['result']['private']='PRIVATE_CANARY'",
        "reply['request_sha256']='0'*64",
        "reply['result']['schema_version']=13",
        "reply['result']['heap_cap_bytes']=True",
        "reply['result']['tables']['settings']['rows']=True",
        "reply['result']['tables']['settings']['sha256']='0'*64",
        "reply['result']['tables']['settings']['encoded_bytes']=0",
        "reply['status']='unknown'",
    ],
)
def test_v140_reply_is_strict_and_request_bound(subject, monkeypatch, processes, edit):
    path, args = subject
    fake_worker(monkeypatch, FAKE_PREFIX + edit + "\n" + FAKE_SEND)
    with pytest.raises(ValueError) as error:
        database.inspect_database_object(path, **args)
    assert "PRIVATE_CANARY" not in str(error.value)


@pytest.mark.parametrize("suffix", ["\nsys.stdout.buffer.write(b'\\n')", "\nraise SystemExit(3)"])
def test_v140_noisy_or_nonzero_child_cannot_publish(subject, monkeypatch, processes, suffix):
    path, args = subject
    fake_worker(monkeypatch, FAKE_PREFIX + FAKE_SEND + suffix)
    with pytest.raises(ValueError):
        database.inspect_database_object(path, **args)


def test_v140_output_overflow_kills_without_unbounded_capture(subject, monkeypatch, processes):
    path, args = subject
    fake_worker(
        monkeypatch,
        "import sys,time\nsys.stdout.buffer.write(b'x'*100000)\n"
        "sys.stdout.buffer.flush()\ntime.sleep(10)\n",
    )
    start = time.monotonic()
    with pytest.raises(ValueError):
        database.inspect_database_object(path, **args)
    assert time.monotonic() - start < 4


def test_v140_resource_construction_failure_never_leaves_a_spawned_child(monkeypatch, processes):
    source = b"import time\ntime.sleep(10)\n"
    monkeypatch.setattr(database, "WORKER_SHA256_LF", sha(source))

    def no_event():
        raise MemoryError("controlled event construction")

    monkeypatch.setattr(database.threading, "Event", no_event)
    try:
        with pytest.raises(MemoryError):
            database._run_child(source, b"{}", lambda: None)
        alive = [process for process, _, _ in processes if process.poll() is None]
    finally:
        # Retain red evidence without leaking the deliberately reproduced sleep child.
        for process, _, _ in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdin.close()
            process.stdout.close()
    assert not alive


@pytest.mark.parametrize("code", [[], {}])
def test_v140_nonstring_error_codes_are_curated(code):
    raw = database._canonical(
        {"protocol": database.PROTOCOL, "request_sha256": "0" * 64, "status": "error", "code": code}
    )
    with pytest.raises(ValueError):
        database._reply(raw, "0" * 64, empty_claims())


def test_v140_stalled_child_deadline_reaps_and_closes(subject, monkeypatch, processes):
    path, args = subject
    fake_worker(
        monkeypatch,
        "import sys,time\nsys.stdout.buffer.write(b'{')\n"
        "sys.stdout.buffer.flush()\ntime.sleep(10)\n",
    )
    monkeypatch.setitem(database.LIMITS, "inspection_seconds", 0.5)
    with pytest.raises(TimeoutError):
        database.inspect_database_object(path, **args)


class StopInspection(BaseException):
    pass


def test_v140_guard_baseexception_during_blocked_stdin_preserves_identity(
    subject, monkeypatch, processes
):
    path, args = subject
    fake_worker(monkeypatch, "import time\ntime.sleep(10)\n", large_input=True)
    failure = StopInspection("caller cancellation")
    began = None

    def stop():
        nonlocal began
        if processes:
            began = began or time.monotonic()
            if time.monotonic() - began > 0.1:
                assert any(
                    thread.name == "cbs-portable-input" and thread.is_alive()
                    for thread in threading.enumerate()
                )
                raise failure

    with pytest.raises(StopInspection) as error:
        database.inspect_database_object(path, **args, guard=stop)
    assert error.value is failure


def test_v140_child_is_isolated_and_never_inherits_provider_secrets(
    subject, monkeypatch, processes
):
    path, args = subject
    code = (
        "import os,sys\n"
        "assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode\n"
        "assert not any(k in os.environ "
        "for k in ['OPENAI_API_KEY','HF_TOKEN','UCLOUD_TOKEN','PYTHONPATH'])\n"
        + FAKE_PREFIX
        + FAKE_SEND
    )
    fake_worker(monkeypatch, code)
    for name in ("OPENAI_API_KEY", "HF_TOKEN", "UCLOUD_TOKEN", "PYTHONPATH"):
        monkeypatch.setenv(name, "PRIVATE_CANARY")
    database.inspect_database_object(path, **args)
    _, positional, options = processes[0]
    assert str(path) not in repr(positional)
    assert options["close_fds"] and not options["shell"]
    assert options["stderr"] == database.subprocess.DEVNULL
    assert set(options["env"]) <= {"SystemRoot", "WINDIR"}


@pytest.mark.parametrize("change", ["bytes", "sidecar"])
def test_v140_post_child_input_drift_refuses_success(subject, monkeypatch, processes, change):
    path, args = subject
    mutation = (
        "open(request['path'],'wb').write(b'changed')"
        if change == "bytes"
        else "open(request['path']+'-wal','wb').close()"
    )
    fake_worker(monkeypatch, FAKE_PREFIX + mutation + "\n" + FAKE_SEND)
    with pytest.raises(ValueError):
        database.inspect_database_object(path, **args)


def test_v140_sources_are_bound_to_executed_lf_bytes_and_exact_catalog(monkeypatch):
    codec = b"# codec\n"
    worker = b"# worker\n"
    catalog = b"{}"
    mapping = {
        "portable_database_worker.py": worker.replace(b"\n", b"\r\n"),
        "portable_rows.py": codec.replace(b"\n", b"\r\n"),
        "catalogs/portable_schema_12.json": catalog,
    }

    class Resources:
        def joinpath(self, name):
            class File:
                def open(self, mode):
                    return io.BytesIO(mapping[name])

            return File()

    monkeypatch.setattr(database, "files", lambda name: Resources())
    monkeypatch.setattr(database, "WORKER_SHA256_LF", sha(worker))
    monkeypatch.setattr(database, "CODEC_SHA256_LF", sha(codec))
    monkeypatch.setattr(database, "CATALOG_SHA256", sha(catalog))
    assert database._sources(lambda: None) == (worker, codec, catalog)
    mapping["catalogs/portable_schema_12.json"] = b"{}\n"
    with pytest.raises(ValueError):
        database._sources(lambda: None)


@pytest.mark.parametrize("stage", ["open", "read"])
def test_v140_trusted_source_io_errors_are_curated_without_masking_guard(monkeypatch, stage):
    failure = PermissionError("PRIVATE_CANARY_SOURCE_PATH")

    class Stream(io.BytesIO):
        def read(self, size):
            raise failure

    class Resource:
        def joinpath(self, name):
            return self

        def open(self, mode):
            if stage == "open":
                raise failure
            return Stream(b"")

    monkeypatch.setattr(database, "files", lambda name: Resource())
    with pytest.raises(ValueError) as error:
        database._sources(lambda: None)
    assert "PRIVATE_CANARY" not in str(error.value)

    def guard():
        raise failure

    with pytest.raises(PermissionError) as error:
        database._sources(guard)
    assert error.value is failure


def test_v140_actual_isolated_reader_accepts_exact_empty_synthetic_catalog(tmp_path):
    catalog = json.loads(
        database.files("cbs_transcriber").joinpath("catalogs/portable_schema_12.json").read_bytes()
    )
    path = tmp_path / "synthetic.sqlite3"
    connection = sqlite3.connect(path)
    try:
        for kind in ("table", "index"):
            for item in catalog["structure"]["objects"]:
                if item["type"] == kind and item["sql"] is not None:
                    connection.execute(item["sql"])
        connection.execute("PRAGMA user_version=12")
        connection.commit()
    finally:
        connection.close()
    raw = path.read_bytes()
    result = database.inspect_database_object(
        path, expected_sha256=sha(raw), expected_size=len(raw), expected_claims=empty_claims()
    )
    assert result["heap_sentry"] == "allocation_refused_before_open"
    assert all(item["rows"] == 0 for item in result["tables"].values())
    assert path.read_bytes() == raw
