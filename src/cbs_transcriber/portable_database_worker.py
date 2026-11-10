"""Disposable, stdlib-only SQLite claim inspection (ADR 0088).

Only the parent executes these captured source bytes in a fresh isolated interpreter. The
SQLite heap policy is process-wide, so this worker must never run inside the serving process.
This is not a security sandbox, history validator, portable-package verifier or restorer.
"""

import base64
import binascii
import hashlib
import io
import json
import re
import sqlite3
import stat
import sys
import time
import types
from pathlib import Path

PROTOCOL = "cbs-portable-database-v1"
CODEC_SHA256 = "e7e0200ca86757590f1ce95cb31c86657062bdb6a946fd6899bed31877efb862"
CATALOG_SHA256 = "c971fcad9df182f484e6978f85c9b8fc290994219afd01e04391b036c1a03330"
SCHEMA_FINGERPRINT = "038150c5376a827bec4a8d9c3f61c6d19e95fda7cd8a401dd8b3a81c8a20ef42"
HEAP_CAP_BYTES = 64 * 1024**2
LIMITS = {
    "request_bytes": 256 * 1024,
    "reply_bytes": 16 * 1024,
    "codec_bytes": 16 * 1024,
    "catalog_bytes": 128 * 1024,
    "database_bytes": 8 * 1024**3,
    "rows": 2_000_000,
    "encoded_bytes": 8 * 1024**3,
    "vm_steps": 100_000_000,
    "metadata_bytes": 4096,
}
REQUEST_FIELDS = {
    "protocol",
    "path",
    "expected_sha256",
    "expected_size",
    "expected_claims",
    "source_witness",
    "codec_source_b64",
    "catalog_b64",
    "deadline_ns",
}
ERROR_CODES = {
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
CHECKS = {
    "annotation_items": ("sequence>0",),
    "annotation_review_events": ("revision>0", "decision IN ('accepted','rejected')"),
    "annotation_runs": ("sequence>0",),
    "codebook_versions": ("sequence>0",),
    "note_versions": (
        "scope IN ('project','recording')",
        "notes_revision>=0",
        "(scope='project' AND recording_id IS NULL) OR "
        "(scope='recording' AND recording_id IS NOT NULL)",
    ),
    "result_versions": ("sequence>0",),
}


class WorkerError(Exception):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "processing"
        super().__init__(self.code)


def _fail(code):
    raise WorkerError(code)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _record(value, fields):
    if type(value) is not dict or set(value) != fields:
        _fail("input")


def _integer(value, maximum, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("input")


def _hex(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        _fail("input")


def _decoded(value, maximum, expected):
    if type(value) is not str or len(value) > ((maximum + 2) // 3) * 4:
        _fail("input")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        _fail("input")
    if (
        len(decoded) > maximum
        or base64.b64encode(decoded).decode("ascii") != value
        or hashlib.sha256(decoded).hexdigest() != expected
    ):
        _fail("input")
    return decoded


def _load_codec(request):
    source = _decoded(request["codec_source_b64"], LIMITS["codec_bytes"], CODEC_SHA256)
    raw = _decoded(request["catalog_b64"], LIMITS["catalog_bytes"], CATALOG_SHA256)
    codec = types.ModuleType("_cbs_portable_rows")
    exec(compile(source, "<captured-portable-rows>", "exec"), codec.__dict__)

    class Resource:
        def joinpath(self, name):
            if name != "catalogs/portable_schema_12.json":
                _fail("input")
            return self

        def open(self, mode):
            if mode != "rb":
                _fail("input")
            return io.BytesIO(raw)

    def resource(name):
        if name != "cbs_transcriber":
            _fail("input")
        return Resource()

    codec.files = resource
    codec._catalog()
    catalog = json.loads(raw)
    return codec, catalog["structure"]


def _validate_request(request, structure):
    _record(request, REQUEST_FIELDS)
    if request["protocol"] != PROTOCOL:
        _fail("protocol")
    _hex(request["expected_sha256"])
    _integer(request["expected_size"], LIMITS["database_bytes"], 1)
    name = request["path"]
    if type(name) is not str or len(name) > 4096 or "\0" in name:
        _fail("input")
    try:
        if len(name.encode("utf-8")) > 4096:
            _fail("input")
        path = Path(name)
        if not path.is_absolute() or path.drive.startswith("\\\\"):
            _fail("input")
    except (ValueError, UnicodeError):
        _fail("input")
    witness = request["source_witness"]
    _record(witness, {"dev", "ino", "size", "mtime_ns", "ctime_ns"})
    for key in ("dev", "ino"):
        _integer(witness[key], 2**128 - 1)
    for key in ("mtime_ns", "ctime_ns"):
        _integer(witness[key], 2**63 - 1, -(2**63))
    if witness["size"] != request["expected_size"] or type(witness["size"]) is not int:
        _fail("input")
    claims = request["expected_claims"]
    _record(claims, {"schema_version", "schema_fingerprint", "tables"})
    if type(claims["schema_version"]) is not int or claims["schema_version"] != 12:
        _fail("input")
    if claims["schema_fingerprint"] != SCHEMA_FINGERPRINT:
        _fail("input")
    _record(claims["tables"], set(structure["tables"]))
    count = 0
    for value in claims["tables"].values():
        _record(value, {"rows", "sha256"})
        _integer(value["rows"], 1_000_000)
        _hex(value["sha256"])
        count += value["rows"]
        if count > LIMITS["rows"]:
            _fail("input")
    _integer(request["deadline_ns"], 2**63 - 1, 1)
    if request["deadline_ns"] > time.monotonic_ns() + 120 * 10**9:
        _fail("input")


def _guard(request):
    if time.monotonic_ns() >= request["deadline_ns"]:
        _fail("timeout")


def _isolated():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        _fail("unsupported_runtime")


def _one(db, statement, parameters=()):
    cursor = db.execute(statement, parameters)
    try:
        return cursor.fetchone()
    finally:
        cursor.close()


def _heap_cap():
    _isolated()
    db = sqlite3.connect(":memory:", timeout=0, isolation_level=None, cached_statements=0)
    try:
        cursor = db.execute("PRAGMA compile_options")
        try:
            options = cursor.fetchmany(513)
        finally:
            cursor.close()
        if (
            not 0 < len(options) <= 512
            or any(type(row[0]) is not str or len(row[0]) > 512 for row in options)
            or any(row[0] in {"DEFAULT_MEMSTATUS=0", "OMIT_COMPILEOPTION_DIAGS"} for row in options)
        ):
            _fail("unsupported_runtime")
        if _one(db, f"PRAGMA hard_heap_limit={HEAP_CAP_BYTES}") != (HEAP_CAP_BYTES,):
            _fail("unsupported_runtime")
        if _one(db, "SELECT length(randomblob(1024))") != (1024,):
            _fail("unsupported_runtime")
        try:
            cursor = db.execute("SELECT randomblob(?)", (HEAP_CAP_BYTES + 1,))
        except MemoryError:
            pass
        else:
            # Never fetch an unexpectedly successful large value into Python.
            cursor.close()
            _fail("unsupported_runtime")
        if _one(db, "PRAGMA hard_heap_limit") != (HEAP_CAP_BYTES,) or _one(
            db, "SELECT length(randomblob(1024))"
        ) != (1024,):
            _fail("unsupported_runtime")
    finally:
        db.close()


def _witness(path):
    try:
        parent = path.parent
        for candidate, directory in ((parent, True), (path, False)):
            info = candidate.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink != 1)
            ):
                _fail("changed_input")
        if not path.resolve(strict=True).is_relative_to(parent.resolve(strict=True)):
            _fail("changed_input")
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                path.with_name(path.name + suffix).lstat()
            except FileNotFoundError:
                continue
            _fail("changed_input")
        return {
            "dev": info.st_dev,
            "ino": info.st_ino,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
        }
    except (OSError, RuntimeError):
        _fail("changed_input")


def _hardening(db, request, structure):
    settings = [
        ("SQLITE_DBCONFIG_DEFENSIVE", True),
        ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
        ("SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False),
        ("SQLITE_DBCONFIG_ENABLE_TRIGGER", False),
        ("SQLITE_DBCONFIG_ENABLE_VIEW", False),
        ("SQLITE_DBCONFIG_WRITABLE_SCHEMA", False),
        ("SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE", True),
        ("SQLITE_DBCONFIG_DQS_DDL", False),
        ("SQLITE_DBCONFIG_DQS_DML", False),
    ]
    try:
        for name, value in settings:
            flag = getattr(sqlite3, name)
            db.setconfig(flag, value)
            if db.getconfig(flag) is not value:
                _fail("unsupported_runtime")
        for name, value in [
            ("SQLITE_LIMIT_LENGTH", LIMITS["metadata_bytes"]),
            ("SQLITE_LIMIT_SQL_LENGTH", 64 * 1024),
            ("SQLITE_LIMIT_COLUMN", 64),
            ("SQLITE_LIMIT_EXPR_DEPTH", 64),
            ("SQLITE_LIMIT_COMPOUND_SELECT", 16),
            ("SQLITE_LIMIT_ATTACHED", 0),
            ("SQLITE_LIMIT_FUNCTION_ARG", 16),
            ("SQLITE_LIMIT_VDBE_OP", 200_000),
            ("SQLITE_LIMIT_WORKER_THREADS", 0),
        ]:
            flag = getattr(sqlite3, name)
            db.setlimit(flag, value)
            if db.getlimit(flag) > value:
                _fail("unsupported_runtime")
    except (AttributeError, sqlite3.Error):
        _fail("unsupported_runtime")
    state = {"phase": "setup", "table": None, "steps": 0, "interrupt": None}
    table_names = set(structure["tables"])
    index_names = set(structure["indexes"])
    setup = {
        "query_only": {None, "ON"},
        "mmap_size": {None, "0"},
        "temp_store": {None, "MEMORY"},
        "automatic_index": {None, "OFF"},
        "threads": {None, "0"},
        "cache_size": {None, "-2048"},
        "foreign_keys": {None, "ON"},
        "hard_heap_limit": {None},
    }

    def authorize(action, first, second, database, source):
        if source is not None or database not in (None, "main"):
            return sqlite3.SQLITE_DENY
        phase = state["phase"]
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if (
                first in {"sqlite_master", "sqlite_schema"}
                or (phase == "rows" and first == state["table"])
                or (phase == "checks" and first in CHECKS)
            ):
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA:
            if phase == "setup" and first in setup and second in setup[first]:
                return sqlite3.SQLITE_OK
            if phase == "schema" and (
                (first in {"user_version", "encoding"} and second is None)
                or (
                    first in {"table_xinfo", "foreign_key_list", "index_list"}
                    and second in table_names
                )
                or (first == "index_xinfo" and second in index_names)
            ):
                return sqlite3.SQLITE_OK
            if phase == "checks" and (
                (first == "integrity_check" and second == "1")
                or (first == "foreign_key_check" and second is None)
            ):
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def progress():
        state["steps"] += 1000
        if time.monotonic_ns() >= request["deadline_ns"]:
            state["interrupt"] = "timeout"
            return 1
        if state["steps"] > LIMITS["vm_steps"]:
            state["interrupt"] = "resource"
            return 1
        return 0

    db.set_authorizer(authorize)
    db.set_progress_handler(progress, 1000)
    for statement, readback, expected in [
        ("query_only=ON", "query_only", 1),
        ("mmap_size=0", "mmap_size", 0),
        ("temp_store=MEMORY", "temp_store", 2),
        ("automatic_index=OFF", "automatic_index", 0),
        ("threads=0", "threads", 0),
        ("cache_size=-2048", "cache_size", -2048),
        ("foreign_keys=ON", "foreign_keys", 1),
    ]:
        cursor = db.execute("PRAGMA " + statement)
        cursor.close()
        if _one(db, "PRAGMA " + readback) != (expected,):
            _fail("unsupported_runtime")
    if _one(db, "PRAGMA hard_heap_limit") != (HEAP_CAP_BYTES,):
        _fail("unsupported_runtime")
    return state


def _bounded_rows(db, statement, maximum):
    cursor = db.execute(statement)
    try:
        rows = cursor.fetchmany(maximum + 1)
        if len(rows) > maximum:
            _fail("schema")
        return rows
    finally:
        cursor.close()


def _schema(db, structure, state):
    state["phase"] = "schema"
    observed = {
        "schema_version": _one(db, "PRAGMA user_version")[0],
        "encoding": _one(db, "PRAGMA encoding")[0],
        "objects": [],
        "tables": {},
        "indexes": {},
    }
    objects = _bounded_rows(
        db,
        "SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name LIMIT 56",
        len(structure["objects"]),
    )
    observed["objects"] = [
        dict(zip(("type", "name", "table", "sql"), row, strict=True)) for row in objects
    ]
    if observed["objects"] != structure["objects"]:
        _fail("schema")
    # Names below come only from the hash-locked catalog, never observed database fields.
    for name in structure["tables"]:
        observed["tables"][name] = {
            "columns": [
                dict(
                    zip(
                        (
                            "position",
                            "name",
                            "declared_type",
                            "not_null",
                            "default",
                            "pk_position",
                            "hidden",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in _bounded_rows(db, f'PRAGMA table_xinfo("{name}")', 64)
            ],
            "foreign_keys": [
                dict(
                    zip(
                        (
                            "id",
                            "sequence",
                            "table",
                            "from",
                            "to",
                            "on_update",
                            "on_delete",
                            "match",
                        ),
                        row,
                        strict=True,
                    )
                )
                for row in _bounded_rows(db, f'PRAGMA foreign_key_list("{name}")', 64)
            ],
            "indexes": sorted(
                [
                    dict(zip(("name", "unique", "origin", "partial"), row[1:], strict=True))
                    for row in _bounded_rows(db, f'PRAGMA index_list("{name}")', 64)
                ],
                key=lambda row: row["name"],
            ),
        }
    for name in structure["indexes"]:
        observed["indexes"][name] = {
            "columns": [
                dict(
                    zip(
                        ("sequence", "column_position", "name", "descending", "collation", "key"),
                        row,
                        strict=True,
                    )
                )
                for row in _bounded_rows(db, f'PRAGMA index_xinfo("{name}")', 64)
            ]
        }
    if (
        observed != structure
        or hashlib.sha256(b"cbs-portable-schema-v1\0" + _canonical(observed)).hexdigest()
        != SCHEMA_FINGERPRINT
    ):
        _fail("schema")


def _claims(db, request, codec, structure, state):
    state["phase"] = "checks"
    # These nine literal predicates belong to the exact accepted DDL family. Do not parse
    # or execute expressions supplied by a database. SQL NOT retains CHECK's NULL and
    # storage-affinity behavior; a demonstrated SQLite 3.45.3 read-only pragma gap is B212.
    for table, expressions in CHECKS.items():
        for expression in expressions:
            _guard(request)
            if _one(db, f'SELECT 1 FROM "{table}" WHERE NOT({expression}) LIMIT 1') is not None:
                _fail("integrity")
    cursor = db.execute("PRAGMA integrity_check(1)")
    try:
        if cursor.fetchmany(2) != [("ok",)]:
            _fail("integrity")
    finally:
        cursor.close()
    if _one(db, "PRAGMA foreign_key_check") is not None:
        _fail("foreign_keys")
    state["phase"] = "rows"
    tables, total_rows, total_bytes = {}, 0, 0
    for table, definition in structure["tables"].items():
        _guard(request)
        state["table"] = table
        columns = definition["columns"]
        keys = sorted(
            (column for column in columns if column["pk_position"]),
            key=lambda column: column["pk_position"],
        )
        select = ",".join('"' + column["name"] + '"' for column in columns)
        order = ",".join(
            '"'
            + column["name"]
            + '"'
            + (" COLLATE BINARY" if column["declared_type"] == "TEXT" else "")
            for column in keys
        )
        codec.LIMITS["rows"] = min(codec.LIMITS["rows"], LIMITS["rows"] - total_rows)
        codec.LIMITS["total_bytes"] = min(
            codec.LIMITS["total_bytes"], LIMITS["encoded_bytes"] - total_bytes
        )
        cursor = db.execute(f'SELECT {select} FROM "{table}" ORDER BY {order}')

        def rows(current=cursor):
            nonlocal total_rows
            for row in current:
                _guard(request)
                if total_rows >= LIMITS["rows"]:
                    _fail("resource")
                total_rows += 1
                yield row

        try:
            result = codec.digest_table(table, rows(), guard=lambda: _guard(request))
        except ValueError:
            _fail("resource")
        finally:
            cursor.close()
        actual = {field: result[field] for field in ("rows", "sha256", "encoded_bytes")}
        expected = request["expected_claims"]["tables"][table]
        if actual["rows"] != expected["rows"] or actual["sha256"] != expected["sha256"]:
            _fail("claims")
        total_bytes += actual["encoded_bytes"]
        tables[table] = actual
    return tables


def execute(request):
    """Inspect one caller-bound observation, only inside a fresh isolated child."""
    _isolated()
    _record(request, REQUEST_FIELDS)
    codec, structure = _load_codec(request)
    _validate_request(request, structure)
    _guard(request)
    _heap_cap()
    _guard(request)
    path = Path(request["path"])
    if _witness(path) != request["source_witness"]:
        _fail("changed_input")
    db, state = None, None
    try:
        db = sqlite3.connect(
            path.resolve(strict=True).as_uri() + "?mode=ro&immutable=1",
            uri=True,
            timeout=0,
            isolation_level=None,
            cached_statements=0,
        )
        state = _hardening(db, request, structure)
        _schema(db, structure, state)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32 * 1024**2)
        if db.getlimit(sqlite3.SQLITE_LIMIT_LENGTH) > 32 * 1024**2:
            _fail("unsupported_runtime")
        tables = _claims(db, request, codec, structure, state)
    except sqlite3.Error:
        if state and state["interrupt"]:
            _fail(state["interrupt"])
        _fail("schema" if state is None or state["phase"] in {"setup", "schema"} else "processing")
    except MemoryError:
        _fail("resource")
    finally:
        if db is not None:
            db.close()
    _guard(request)
    if _witness(path) != request["source_witness"]:
        _fail("changed_input")
    return {
        "schema_version": 12,
        "schema_fingerprint": SCHEMA_FINGERPRINT,
        "tables": tables,
        "sqlite_version": sqlite3.sqlite_version,
        "heap_cap_bytes": HEAP_CAP_BYTES,
        "heap_sentry": "allocation_refused_before_open",
    }


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            _fail("protocol")
        value[key] = item
    return value


def _bad_number(_):
    _fail("protocol")


def read_request(raw):
    if type(raw) is not bytes or len(raw) > LIMITS["request_bytes"]:
        _fail("protocol")
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 8:
                _fail("protocol")
        elif byte in (93, 125):
            depth -= 1
    try:
        request = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_bad_number,
            parse_constant=_bad_number,
        )
        if _canonical(request) != raw:
            _fail("protocol")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("protocol")
    return request


def main():
    raw = sys.stdin.buffer.read(LIMITS["request_bytes"] + 1)
    request_sha256 = hashlib.sha256(raw).hexdigest()
    reply = {"protocol": PROTOCOL, "request_sha256": request_sha256}
    status = 0
    try:
        result = execute(read_request(raw))
        reply.update(status="ok", result=result)
    except WorkerError as error:
        reply.update(status="error", code=error.code)
    except BaseException:
        reply.update(status="error", code="processing")
    encoded = _canonical(reply)
    if len(encoded) > LIMITS["reply_bytes"]:
        encoded = _canonical(
            {
                "protocol": PROTOCOL,
                "request_sha256": request_sha256,
                "status": "error",
                "code": "processing",
            }
        )
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
