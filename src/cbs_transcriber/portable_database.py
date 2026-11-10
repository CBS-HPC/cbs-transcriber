"""Internal supplied-database observations in a disposable, bounded-protocol child.

The caller owns a quiescent staging file. This is no package, history, capture, restoration or
security-sandbox approval. SQLite opening happens only in the isolated worker (ADR 0088).
Only synthetic objects are verified; no private-data security baseline is accepted. The
cooperative budgets do not bound Python/OS heap or preempt blocking OS creation/filesystem I/O.
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from importlib.resources import files
from pathlib import Path

from . import portable_transport

PROTOCOL = "cbs-portable-database-v1"
WORKER_SHA256_LF = "d58bbdec746fa3e6967a05093c2fb77f62a3dd75d8bf0b59935c9631f4d7dfee"
CODEC_SHA256_LF = "e7e0200ca86757590f1ce95cb31c86657062bdb6a946fd6899bed31877efb862"
CATALOG_SHA256 = "c971fcad9df182f484e6978f85c9b8fc290994219afd01e04391b036c1a03330"
SCHEMA_FINGERPRINT = "038150c5376a827bec4a8d9c3f61c6d19e95fda7cd8a401dd8b3a81c8a20ef42"
TABLES = (
    "annotation_items",
    "annotation_review_events",
    "annotation_runs",
    "codebook_versions",
    "collections",
    "jobs",
    "native_batch_runs",
    "note_versions",
    "projects",
    "providers",
    "recordings",
    "result_hints",
    "result_trash",
    "result_versions",
    "runtime_batch_attempts",
    "runtime_batch_members",
    "runtime_batch_runs",
    "settings",
    "transcript_results",
)
LIMITS = {
    "database_bytes": 8 * 1024**3,
    "worker_bytes": 64 * 1024,
    "codec_bytes": 16 * 1024,
    "catalog_bytes": 128 * 1024,
    "request_bytes": 256 * 1024,
    "reply_bytes": 16 * 1024,
    "inspection_seconds": 120,
    "cleanup_seconds": 2,
    "rows": 1_000_000,
    "total_rows": 2_000_000,
    "encoded_bytes": 8 * 1024**3,
}
HEAP_BYTES = 64 * 1024**2
NOT_CHECKED = (
    "sealed_package_binding",
    "history_payloads_and_active_mirrors",
    "owner_authority_and_quarantine",
    "file_inventory_and_capture_completeness",
    "credential_exclusion_and_data_policy",
    "restore_and_activation",
)


def _invalid(part):
    raise ValueError(f"The supplied database {part} is invalid or unavailable.")


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _integer(value, maximum, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid("numeric reference")
    return value


def _sha(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        _invalid("hash reference")
    return value


def _claims(value):
    if type(value) is not dict or set(value) != {"schema_version", "schema_fingerprint", "tables"}:
        _invalid("claims")
    if type(value["schema_version"]) is not int or value["schema_version"] != 12:
        _invalid("schema claim")
    if _sha(value["schema_fingerprint"]) != SCHEMA_FINGERPRINT:
        _invalid("schema claim")
    tables = value["tables"]
    if type(tables) is not dict or len(tables) != len(TABLES) or set(tables) != set(TABLES):
        _invalid("table claims")
    copied = {}
    for name in TABLES:
        claim = tables[name]
        if type(claim) is not dict or set(claim) != {"rows", "sha256"}:
            _invalid("table claim")
        copied[name] = {
            "rows": _integer(claim["rows"], LIMITS["rows"]),
            "sha256": _sha(claim["sha256"]),
        }
    if sum(item["rows"] for item in copied.values()) > LIMITS["total_rows"]:
        _invalid("row claims")
    return {"schema_version": 12, "schema_fingerprint": SCHEMA_FINGERPRINT, "tables": copied}


def _sources(check):
    try:
        resources = files("cbs_transcriber")
    except OSError:
        _invalid("trusted resource access")
    captured = []
    for name, limit, pin, source in (
        ("portable_database_worker.py", "worker_bytes", WORKER_SHA256_LF, True),
        ("portable_rows.py", "codec_bytes", CODEC_SHA256_LF, True),
        ("catalogs/portable_schema_12.json", "catalog_bytes", CATALOG_SHA256, False),
    ):
        check()
        try:
            with resources.joinpath(name).open("rb") as stream:
                raw = stream.read(LIMITS[limit] + 1)
        except OSError:
            _invalid("trusted resource access")
        if len(raw) > LIMITS[limit]:
            _invalid("trusted resource size")
        if source:
            raw = raw.replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != pin:
            _invalid("trusted resource identity")
        captured.append(raw)
    return tuple(captured)


def _sidecars(path):
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            Path(str(path) + suffix).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            _invalid("sidecar observation")
        _invalid("sidecar observation")


def _bootstrap():
    return (
        "import hashlib,sys\n"
        "n=int.from_bytes(sys.stdin.buffer.read(8),'big')\n"
        f"if not 0<n<={LIMITS['worker_bytes']}:raise SystemExit(2)\n"
        "source=sys.stdin.buffer.read(n)\n"
        f"if len(source)!=n or hashlib.sha256(source).hexdigest()!={WORKER_SHA256_LF!r}:"
        "raise SystemExit(2)\n"
        "exec(compile(source,'<portable-database-worker>','exec'),{'__name__':'__main__'})\n"
    )


def _run_child(worker, request, check, *, bootstrap=None, reply_bytes=None):
    # The schema-13 semantic reader uses the same bounded pipe/drain protocol with its
    # separately pinned bootstrap. Defaults preserve the schema-12 reader contract.
    reply_limit = LIMITS["reply_bytes"] if reply_bytes is None else reply_bytes
    payload = len(worker).to_bytes(8, "big") + worker + request
    environment = {
        name: os.environ[name] for name in ("SystemRoot", "WINDIR") if name in os.environ
    }
    interpreter = Path(sys.executable).resolve()
    process = None
    output, stop, overflow, io_error = (
        bytearray(),
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def write():
        try:
            data = memoryview(payload)
            while data and not stop.is_set():
                count = process.stdin.write(data[:65536])
                if not count:
                    raise OSError
                data = data[count:]
        except BaseException:
            if not stop.is_set():
                io_error.set()
        finally:
            try:
                process.stdin.close()
            except BaseException:
                io_error.set()

    def read():
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                remaining = reply_limit + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > reply_limit:
                    overflow.set()
        except BaseException:
            if not stop.is_set():
                io_error.set()
        finally:
            try:
                process.stdout.close()
            except BaseException:
                io_error.set()

    writer = threading.Thread(target=write, name="cbs-portable-input", daemon=True)
    reader = threading.Thread(target=read, name="cbs-portable-output", daemon=True)
    failed = None
    cleanup_ok = False
    try:
        try:
            process = subprocess.Popen(
                [str(interpreter), "-I", "-S", "-B", "-c", bootstrap or _bootstrap()],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                close_fds=True,
                shell=False,
                env=environment,
                cwd=interpreter.parent,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            )
        except OSError:
            _invalid("child startup")
        writer.start()
        reader.start()
        while True:
            check()
            if overflow.is_set() or io_error.is_set():
                _invalid("child protocol")
            if process.poll() is not None and not writer.is_alive() and not reader.is_alive():
                break
            time.sleep(0.025)
        if overflow.is_set() or io_error.is_set():
            _invalid("child protocol")
        if process.returncode != 0:
            _invalid("child execution")
    except BaseException as error:
        failed = error
    finally:
        stop.set()
        deadline = time.monotonic() + LIMITS["cleanup_seconds"]
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except (OSError, subprocess.TimeoutExpired):
                pass
        for thread in (writer, reader):
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        if process is not None:
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass
            cleanup_ok = (
                process.poll() is not None
                and not writer.is_alive()
                and not reader.is_alive()
                and process.stdin.closed
                and process.stdout.closed
            )
    if failed is not None:
        raise failed
    if not cleanup_ok:
        _invalid("child closure")
    return bytes(output)


def _reply(raw, request_sha256, claims):
    if len(raw) > LIMITS["reply_bytes"]:
        _invalid("reply size")
    value = portable_transport._canonical(raw)
    if (
        type(value) is not dict
        or value.get("protocol") != PROTOCOL
        or value.get("request_sha256") != request_sha256
    ):
        _invalid("reply binding")
    if value.get("status") == "error":
        if (
            set(value) != {"protocol", "request_sha256", "status", "code"}
            or type(value["code"]) is not str
            or value["code"]
            not in {
                "protocol",
                "input",
                "unsupported_runtime",
                "changed_input",
                "schema",
                "integrity",
                "foreign_keys",
                "claims",
                "resource",
                "timeout",
                "processing",
            }
        ):
            _invalid("error reply")
        if value["code"] == "timeout":
            raise TimeoutError("The database inspection exceeded its time budget.")
        _invalid("inspection (" + value["code"] + ")")
    if set(value) != {"protocol", "request_sha256", "status", "result"} or value["status"] != "ok":
        _invalid("reply fields")
    result = value["result"]
    if type(result) is not dict or set(result) != {
        "schema_version",
        "schema_fingerprint",
        "tables",
        "sqlite_version",
        "heap_cap_bytes",
        "heap_sentry",
    }:
        _invalid("result fields")
    if (
        type(result["schema_version"]) is not int
        or result["schema_version"] != 12
        or result["schema_fingerprint"] != SCHEMA_FINGERPRINT
    ):
        _invalid("observed schema")
    if (
        type(result["heap_cap_bytes"]) is not int
        or result["heap_cap_bytes"] != HEAP_BYTES
        or result["heap_sentry"] != "allocation_refused_before_open"
    ):
        _invalid("heap observation")
    if type(result["sqlite_version"]) is not str or not re.fullmatch(
        r"\d{1,3}(?:\.\d{1,3}){2,3}", result["sqlite_version"]
    ):
        _invalid("runtime observation")
    tables = result["tables"]
    if type(tables) is not dict or set(tables) != set(TABLES):
        _invalid("observed tables")
    for name in TABLES:
        item = tables[name]
        if type(item) is not dict or set(item) != {"rows", "sha256", "encoded_bytes"}:
            _invalid("observed table fields")
        if (
            _integer(item["rows"], LIMITS["rows"]) != claims["tables"][name]["rows"]
            or _sha(item["sha256"]) != claims["tables"][name]["sha256"]
        ):
            _invalid("observed table claim")
        _integer(item["encoded_bytes"], LIMITS["encoded_bytes"], 1)
    if sum(item["encoded_bytes"] for item in tables.values()) > LIMITS["encoded_bytes"]:
        _invalid("observed encoded total")
    return result


def inspect_database_object(path, *, expected_sha256, expected_size, expected_claims, guard=None):
    """Match supplied byte/row claims, without granting database/package/restore authority."""
    expected_sha256 = _sha(expected_sha256)
    expected_size = _integer(expected_size, LIMITS["database_bytes"], 1)
    claims = _claims(expected_claims)
    if guard is not None and not callable(guard):
        _invalid("guard")
    deadline_ns = time.monotonic_ns() + int(LIMITS["inspection_seconds"] * 1_000_000_000)

    def check():
        if guard is not None:
            guard()
        if time.monotonic_ns() >= deadline_ns:
            raise TimeoutError("The database inspection exceeded its time budget.")

    check()
    path = Path(path)
    if not path.is_absolute():
        _invalid("path")
    try:
        parent = path.parent.resolve(strict=True)
        portable_transport._node(path.parent, parent, directory=True)
        portable_transport._node(path, parent)
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        _invalid("path")
    _sidecars(path)
    _, before = portable_transport._read(
        path,
        parent,
        maximum=LIMITS["database_bytes"],
        size=expected_size,
        expected_hash=expected_sha256,
        guard=check,
    )
    worker, codec, catalog = _sources(check)
    request = _canonical(
        {
            "protocol": PROTOCOL,
            "path": str(path),
            "expected_sha256": expected_sha256,
            "expected_size": expected_size,
            "expected_claims": claims,
            "deadline_ns": deadline_ns,
            "source_witness": dict(
                zip(
                    ("dev", "ino", "size", "mtime_ns", "ctime_ns"),
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    ),
                    strict=True,
                )
            ),
            "codec_source_b64": base64.b64encode(codec).decode("ascii"),
            "catalog_b64": base64.b64encode(catalog).decode("ascii"),
        }
    )
    if len(request) > LIMITS["request_bytes"]:
        _invalid("request size")
    raw = _run_child(worker, request, check)
    result = _reply(raw, hashlib.sha256(request).hexdigest(), claims)
    check()
    _sidecars(path)
    _, after = portable_transport._read(
        path,
        parent,
        maximum=LIMITS["database_bytes"],
        size=expected_size,
        expected_hash=expected_sha256,
        guard=check,
    )
    portable_transport._same_path(before, after)
    _sidecars(path)
    check()
    return {
        "level": "database_claims_only",
        "reference": "matched_supplied_database",
        "database_sha256": expected_sha256,
        "database_bytes": expected_size,
        "schema_version": 12,
        "schema_fingerprint": SCHEMA_FINGERPRINT,
        "table_policy": "cbs-portable-table-v1",
        "tables": result["tables"],
        "sqlite_version": result["sqlite_version"],
        "heap_cap_bytes": HEAP_BYTES,
        "heap_sentry": result["heap_sentry"],
        "worker_sha256_lf": WORKER_SHA256_LF,
        "codec_sha256_lf": CODEC_SHA256_LF,
        "catalog_sha256": CATALOG_SHA256,
        "restore_ready": False,
        "not_checked": list(NOT_CHECKED),
    }
