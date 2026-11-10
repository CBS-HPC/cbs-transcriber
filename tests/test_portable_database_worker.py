"""Synthetic worker observations; actual SQLite heap settings run only in fresh children."""

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path

import pytest

from cbs_transcriber import portable_database_worker as worker
from cbs_transcriber import portable_rows

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src/cbs_transcriber/portable_database_worker.py"
CODEC = ROOT / "src/cbs_transcriber/portable_rows.py"
CATALOG = files("cbs_transcriber").joinpath("catalogs/portable_schema_12.json").read_bytes()
STRUCTURE = json.loads(CATALOG)["structure"]


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def refresh(sample_request):
    path = Path(sample_request["path"])
    info = path.lstat()
    sample_request["expected_size"] = info.st_size
    sample_request["expected_sha256"] = digest(path.read_bytes())
    sample_request["source_witness"] = {
        "dev": info.st_dev,
        "ino": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }
    return sample_request


def claims(path):
    tables = {}
    db = sqlite3.connect(path)
    try:
        for name, definition in STRUCTURE["tables"].items():
            columns = definition["columns"]
            keys = sorted(
                (column for column in columns if column["pk_position"]),
                key=lambda value: value["pk_position"],
            )
            selection = ",".join('"' + value["name"] + '"' for value in columns)
            order = ",".join('"' + value["name"] + '"' for value in keys)
            cursor = db.execute(f'SELECT {selection} FROM "{name}" ORDER BY {order}')
            try:
                result = portable_rows.digest_table(name, cursor)
            finally:
                cursor.close()
            tables[name] = {key: result[key] for key in ("rows", "sha256")}
    finally:
        db.close()
    return {"schema_version": 12, "schema_fingerprint": worker.SCHEMA_FINGERPRINT, "tables": tables}


@pytest.fixture
def sample_request(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    db = sqlite3.connect(path)
    try:
        # Only the frozen trusted catalog is executed, never a submitted manifest or database.
        for value in STRUCTURE["objects"]:
            if value["type"] == "table":
                db.execute(value["sql"])
        for value in STRUCTURE["objects"]:
            if value["type"] == "index" and value["sql"] is not None:
                db.execute(value["sql"])
        db.execute("PRAGMA user_version=12")
        db.commit()
    finally:
        db.close()
    value = {
        "protocol": worker.PROTOCOL,
        "path": str(path),
        "expected_claims": claims(path),
        "codec_source_b64": base64.b64encode(CODEC.read_bytes().replace(b"\r\n", b"\n")).decode(),
        "catalog_b64": base64.b64encode(CATALOG).decode(),
        "deadline_ns": time.monotonic_ns() + 90 * 10**9,
    }
    return refresh(value)


def child(sample_request=None, *, raw=None, injection=""):
    source = WORKER.read_bytes().replace(b"\r\n", b"\n")
    expected = digest(source)
    bootstrap = (
        "import hashlib,sys;"
        "n=int.from_bytes(sys.stdin.buffer.read(8),'big');"
        "assert 0<n<=65536;"
        "s=sys.stdin.buffer.read(n);"
        f"assert hashlib.sha256(s).hexdigest()=={expected!r};"
    )
    if injection:
        bootstrap += (
            "g={'__name__':'_worker_test'};"
            "exec(compile(s,'<captured-worker>','exec'),g);"
            + injection
            + ";raise SystemExit(g['main']())"
        )
    else:
        bootstrap += "exec(compile(s,'<captured-worker>','exec'),{'__name__':'__main__'})"
    raw = canonical(sample_request) if raw is None else raw
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    # Captured output here is from fixed trusted code with a 16KiB response contract, not the
    # production parent's untrusted-output transport implementation.
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", bootstrap],
        input=len(source).to_bytes(8, "big") + source + raw,
        capture_output=True,
        env=env,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == b"" and len(result.stdout) <= 16 * 1024
    reply = json.loads(result.stdout)
    assert result.stdout == canonical(reply)
    assert reply["protocol"] == worker.PROTOCOL
    assert reply["request_sha256"] == digest(raw)
    return reply


def update_rows(sample_request, statement, parameters=()):
    db = sqlite3.connect(sample_request["path"])
    try:
        db.execute(statement, parameters)
        db.commit()
    finally:
        db.close()
    sample_request["expected_claims"] = claims(sample_request["path"])
    return refresh(sample_request)


def test_v140_worker_refuses_to_apply_a_heap_cap_in_the_pytest_parent(monkeypatch):
    def forbidden():
        pytest.fail("The serving/pytest process must never change its SQLite heap limit")

    monkeypatch.setattr(worker, "_heap_cap", forbidden)
    with pytest.raises(worker.WorkerError, match="unsupported_runtime"):
        worker.execute({})
    with pytest.raises(worker.WorkerError, match="unsupported_runtime"):
        worker._isolated()


def test_v140_actual_child_inspects_exact_empty_schema_and_closes_without_writes(sample_request):
    before = Path(sample_request["path"]).read_bytes()
    reply = child(sample_request)
    assert reply["status"] == "ok", reply
    result = reply["result"]
    assert result["schema_version"] == 12
    assert result["schema_fingerprint"] == worker.SCHEMA_FINGERPRINT
    assert result["heap_cap_bytes"] == 64 * 1024**2
    assert result["heap_sentry"] == "allocation_refused_before_open"
    assert len(result["tables"]) == 19
    for table, expected in sample_request["expected_claims"]["tables"].items():
        assert result["tables"][table]["rows"] == expected["rows"]
        assert result["tables"][table]["sha256"] == expected["sha256"]
    assert Path(sample_request["path"]).read_bytes() == before
    assert not list(Path(sample_request["path"]).parent.glob("*-wal"))
    assert not list(Path(sample_request["path"]).parent.glob("*-shm"))
    assert not list(Path(sample_request["path"]).parent.glob("*-journal"))


@pytest.mark.parametrize("value", ["PRIVATE_CANARY æøå e\u0301\r\n", b"\x00\xff", "x" * 12_000])
def test_v140_exact_observed_values_are_hashed_without_history_or_text_claims(
    sample_request, value
):
    update_rows(sample_request, "INSERT INTO settings(id,value) VALUES (?,?)", (1, value))
    reply = child(sample_request)
    assert reply["status"] == "ok", reply
    assert (
        reply["result"]["tables"]["settings"]["sha256"]
        == sample_request["expected_claims"]["tables"]["settings"]["sha256"]
    )
    assert "PRIVATE_CANARY" not in json.dumps(reply)


@pytest.mark.parametrize("change", ["extra", "version", "view", "trigger"])
def test_v140_schema_gate_rejects_unknown_objects_before_integrity_or_data_queries(
    sample_request, change
):
    db = sqlite3.connect(sample_request["path"])
    try:
        if change == "extra":
            db.execute("CREATE TABLE PRIVATE_CANARY(secret TEXT CHECK(length(secret)>0))")
        elif change == "version":
            db.execute("PRAGMA user_version=13")
        elif change == "view":
            db.execute("CREATE VIEW PRIVATE_CANARY AS SELECT randomblob(999999999)")
        else:
            db.execute("CREATE TRIGGER PRIVATE_CANARY AFTER INSERT ON settings BEGIN SELECT 1; END")
        db.commit()
    finally:
        db.close()
    refresh(sample_request)
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "schema"
    assert "PRIVATE_CANARY" not in json.dumps(reply)


def test_v140_foreign_keys_are_verified_before_a_successful_observation(sample_request):
    update_rows(
        sample_request,
        "INSERT INTO recordings(id,project_id,name,filename,created) VALUES (?,?,?,?,?)",
        ("r" * 32, "missing", "n", "original.wav", "today"),
    )
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "foreign_keys"


def test_v140_accepted_checked_blob_value_uses_data_limits_after_schema_gate(sample_request):
    update_rows(
        sample_request,
        "INSERT INTO projects(id,name,created) VALUES (?,?,?)",
        ("p" * 32, "synthetic", "today"),
    )
    update_rows(
        sample_request,
        "INSERT INTO codebook_versions(id,project_id,sequence,payload,payload_sha256,"
        "observed_at,action,actor_kind) VALUES (?,?,?,?,?,?,?,?)",
        (
            "c" * 32,
            "p" * 32,
            b"x" * 6000,
            "literal payload",
            "not-history-proof",
            "today",
            "create",
            "researcher",
        ),
    )
    reply = child(sample_request)
    assert reply["status"] == "ok", reply
    assert (
        reply["result"]["tables"]["codebook_versions"]["sha256"]
        == sample_request["expected_claims"]["tables"]["codebook_versions"]["sha256"]
    )


def test_v140_integrity_checks_evaluate_only_the_accepted_static_check_constraints(sample_request):
    update_rows(
        sample_request,
        "INSERT INTO projects(id,name,created) VALUES (?,?,?)",
        ("p" * 32, "synthetic", "today"),
    )
    db = sqlite3.connect(sample_request["path"])
    try:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(
            "INSERT INTO codebook_versions(id,project_id,sequence,payload,payload_sha256,"
            "observed_at,action,actor_kind) VALUES (?,?,?,?,?,?,?,?)",
            ("i", "p" * 32, 0, "PRIVATE_CANARY", "wrong", "today", "create", "researcher"),
        )
        db.commit()
    finally:
        db.close()
    sample_request["expected_claims"] = claims(sample_request["path"])
    refresh(sample_request)
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "integrity"
    assert "PRIVATE_CANARY" not in json.dumps(reply)


@pytest.fixture
def research_request(sample_request):
    rows = {
        "projects": {"id": "p", "name": "synthetic", "created": "T"},
        "recordings": {
            "id": "r",
            "project_id": "p",
            "name": "audio",
            "filename": "a.wav",
            "created": "T",
        },
        "transcript_results": {
            "id": "result",
            "recording_id": "r",
            "label": "review",
            "source": "{}",
            "segments": "[]",
            "speakers": "{}",
            "created": "T",
            "updated": "T",
        },
        "result_versions": {
            "id": "version",
            "result_id": "result",
            "sequence": 1,
            "payload": "literal",
            "payload_sha256": "not-history-proof",
            "observed_at": "T",
            "action": "create",
            "context": "{}",
            "actor_kind": "researcher",
        },
        "codebook_versions": {
            "id": "book",
            "project_id": "p",
            "sequence": 1,
            "payload": "literal",
            "payload_sha256": "not-history-proof",
            "observed_at": "T",
            "action": "create",
            "actor_kind": "researcher",
        },
        "annotation_runs": {
            "id": "run",
            "project_id": "p",
            "recording_id": "r",
            "sequence": 1,
            "result_id": "result",
            "source_version_id": "version",
            "source_payload_sha256": "fake",
            "codebook_version_id": "book",
            "codebook_payload_sha256": "fake",
            "kind": "topic",
            "passage_ids": "[]",
            "producer": "{}",
            "created": "T",
            "request_id": "request",
            "request_sha256": "fake",
        },
        "annotation_items": {
            "id": "item",
            "run_id": "run",
            "sequence": 1,
            "origin": "machine",
            "created": "T",
            "request_id": "request",
            "request_sha256": "fake",
        },
        "annotation_review_events": {
            "id": "event",
            "item_id": "item",
            "revision": 1,
            "payload": "literal",
            "payload_sha256": "fake",
            "decision": "accepted",
            "action": "review",
            "actor_kind": "researcher",
            "observed_at": "T",
            "request_id": "request",
            "request_sha256": "fake",
        },
        "note_versions": {
            "id": "note",
            "project_id": "p",
            "scope": "project",
            "notes_revision": 0,
            "text": "literal",
            "text_sha256": "fake",
            "observed_at": "T",
            "actor_kind": "researcher",
            "action": "notes_save",
        },
    }
    db = sqlite3.connect(sample_request["path"])
    try:
        for table, fields in rows.items():
            names = ",".join(fields)
            parameters = ",".join("?" for _ in fields)
            db.execute(
                f'INSERT INTO "{table}"({names}) VALUES ({parameters})', tuple(fields.values())
            )
        db.commit()
    finally:
        db.close()
    sample_request["expected_claims"] = claims(sample_request["path"])
    return refresh(sample_request)


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("annotation_items", "sequence", 0),
        ("annotation_review_events", "revision", 0),
        ("annotation_review_events", "decision", "neither"),
        ("annotation_runs", "sequence", 0),
        ("codebook_versions", "sequence", 0),
        ("note_versions", "scope", "other"),
        ("note_versions", "notes_revision", -1),
        ("note_versions", "recording_id", "r"),
        ("result_versions", "sequence", 0),
    ],
)
def test_v140_each_approved_check_is_verified_on_the_full_readonly_family(
    research_request, table, column, value
):
    db = sqlite3.connect(research_request["path"])
    try:
        db.execute("PRAGMA ignore_check_constraints=ON")
        db.execute(f'UPDATE "{table}" SET "{column}"=?', (value,))
        db.commit()
    finally:
        db.close()
    research_request["expected_claims"] = claims(research_request["path"])
    refresh(research_request)
    reply = child(research_request)
    assert reply["status"] == "error" and reply["code"] == "integrity"


@pytest.mark.parametrize("sequence", ["2", "non-numeric text", 1.5, b"blob value"])
def test_v140_check_predicates_preserve_sqlite_affinity_and_valid_null_controls(
    research_request, sequence
):
    update_rows(research_request, "UPDATE codebook_versions SET sequence=?", (sequence,))
    reply = child(research_request)
    # The valid project note's recording_id and the predecessor/suggestion cells remain NULL.
    assert reply["status"] == "ok", reply
    assert (
        reply["result"]["tables"]["codebook_versions"]["sha256"]
        == research_request["expected_claims"]["tables"]["codebook_versions"]["sha256"]
    )


def test_v140_every_literal_check_query_is_issued_including_redundant_scope_predicate(monkeypatch):
    expected = {
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
    assert worker.CHECKS == expected
    observed = []

    def inspect_query(db, statement, parameters=()):
        observed.append(statement)
        if len(observed) == 9:
            raise RuntimeError("all literal checks observed")
        return None

    monkeypatch.setattr(worker, "_one", inspect_query)
    with pytest.raises(RuntimeError, match="all literal checks observed"):
        worker._claims(None, {"deadline_ns": time.monotonic_ns() + 10**9}, None, None, {})
    assert observed == [
        f'SELECT 1 FROM "{table}" WHERE NOT({expression}) LIMIT 1'
        for table, expressions in expected.items()
        for expression in expressions
    ]


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_v140_sidecars_cannot_be_silently_ignored_by_immutable_open(sample_request, suffix):
    path = Path(sample_request["path"])
    path.with_name(path.name + suffix).write_bytes(b"PRIVATE_CANARY")
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "changed_input"


def test_v140_source_identity_drift_is_refused(sample_request):
    sample_request["source_witness"]["mtime_ns"] += 1
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "changed_input"


def test_v140_non_sqlite_bytes_fail_with_only_a_static_error(sample_request):
    Path(sample_request["path"]).write_bytes(b"PRIVATE_CANARY not a database")
    refresh(sample_request)
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "schema"
    assert "PRIVATE_CANARY" not in json.dumps(reply)


@pytest.mark.parametrize(
    "damage",
    ["unknown", "bool_size", "hash", "source", "catalog", "claims", "rows_bool", "rows_sum"],
)
def test_v140_worker_protocol_and_claims_are_strict_before_database_open(sample_request, damage):
    if damage == "unknown":
        sample_request["private_extra"] = "PRIVATE_CANARY"
    elif damage == "bool_size":
        sample_request["expected_size"] = True
    elif damage == "hash":
        sample_request["expected_sha256"] = "x" * 64
    elif damage == "source":
        sample_request["codec_source_b64"] = base64.b64encode(b"PRIVATE_CANARY").decode()
    elif damage == "catalog":
        sample_request["catalog_b64"] = base64.b64encode(b"PRIVATE_CANARY").decode()
    elif damage == "claims":
        sample_request["expected_claims"]["tables"]["invented"] = {"rows": 0, "sha256": "0" * 64}
    elif damage == "rows_bool":
        sample_request["expected_claims"]["tables"]["settings"]["rows"] = True
    else:
        for value in sample_request["expected_claims"]["tables"].values():
            value["rows"] = 1_000_000
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "input"
    assert "PRIVATE_CANARY" not in json.dumps(reply)


@pytest.mark.parametrize(
    "raw", [b"{}\n", b' {"a":1}', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.0}', b"[" * 9 + b"]" * 9]
)
def test_v140_noncanonical_or_deep_json_never_reaches_database_open(raw):
    reply = child(raw=raw)
    assert reply["status"] == "error" and reply["code"] == "protocol"


def test_v140_mismatched_table_claims_never_become_an_observation(sample_request):
    sample_request["expected_claims"]["tables"]["settings"]["sha256"] = "0" * 64
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "claims"


def test_v140_expired_deadline_refuses_before_heap_or_database_work(sample_request):
    sample_request["deadline_ns"] = time.monotonic_ns() - 1
    reply = child(sample_request)
    assert reply["status"] == "error" and reply["code"] == "timeout"


def test_v140_actual_global_row_budget_stops_an_underdeclared_stream(sample_request):
    update_rows(sample_request, "INSERT INTO settings(id,value) VALUES (?,?)", (1, "one"))
    update_rows(sample_request, "INSERT INTO settings(id,value) VALUES (?,?)", (2, "two"))
    sample_request["expected_claims"]["tables"]["settings"]["rows"] = 0
    reply = child(sample_request, injection="g['LIMITS']['rows']=1")
    assert reply["status"] == "error" and reply["code"] == "resource"


def test_v140_actual_global_byte_budget_includes_all_table_domains_and_trailers(sample_request):
    minimum = sum(
        portable_rows.digest_table(table, [])["encoded_bytes"] for table in STRUCTURE["tables"]
    )
    assert (
        child(sample_request, injection=f"g['LIMITS']['encoded_bytes']={minimum}")["status"] == "ok"
    )
    reply = child(sample_request, injection=f"g['LIMITS']['encoded_bytes']={minimum - 1}")
    assert reply["status"] == "error" and reply["code"] == "resource"


def test_v140_cooperative_vm_budget_interrupts_with_a_static_resource_error(sample_request):
    db = sqlite3.connect(sample_request["path"])
    try:
        db.executemany(
            "INSERT INTO settings(id,value) VALUES (?,?)",
            ((number, "value") for number in range(2000)),
        )
        db.commit()
    finally:
        db.close()
    sample_request["expected_claims"] = claims(sample_request["path"])
    refresh(sample_request)
    reply = child(sample_request, injection="g['LIMITS']['vm_steps']=0")
    assert reply["status"] == "error" and reply["code"] == "resource"


class Cursor:
    def __init__(self, rows=(), *, fetched=None):
        self.rows = list(rows)
        self.closed = False
        self.fetched = fetched

    def fetchone(self):
        if self.fetched is not None:
            self.fetched.append(True)
        return self.rows[0] if self.rows else None

    def fetchmany(self, size):
        return self.rows[:size]

    def close(self):
        self.closed = True


@pytest.mark.parametrize("damage", ["success", "memstatus", "empty_options", "readback"])
def test_v140_heap_sentry_refuses_unproved_capability_and_never_fetches_success(
    monkeypatch, damage
):
    fetched, cursors = [], []

    class Bootstrap:
        closed = False

        def execute(self, statement, parameters=()):
            if statement == "PRAGMA compile_options":
                rows = (
                    []
                    if damage == "empty_options"
                    else [("DEFAULT_MEMSTATUS=0" if damage == "memstatus" else "THREADSAFE=1",)]
                )
            elif statement.startswith("PRAGMA hard_heap_limit"):
                rows = [(0 if damage == "readback" else worker.HEAP_CAP_BYTES,)]
            elif statement == "SELECT randomblob(?)":
                assert parameters == (worker.HEAP_CAP_BYTES + 1,)
                if damage != "success":
                    raise MemoryError
                cursor = Cursor([(b"not fetched",)], fetched=fetched)
                cursors.append(cursor)
                return cursor
            else:
                assert statement == "SELECT length(randomblob(1024))"
                rows = [(1024,)]
            cursor = Cursor(rows)
            cursors.append(cursor)
            return cursor

        def close(self):
            self.closed = True

    bootstrap = Bootstrap()
    # These are deliberately fake connections: no SQLite heap setting runs in pytest.
    monkeypatch.setattr(worker, "_isolated", lambda: None)
    monkeypatch.setattr(worker.sqlite3, "connect", lambda *args, **kwargs: bootstrap)
    with pytest.raises(worker.WorkerError, match="unsupported_runtime"):
        worker._heap_cap()
    assert not fetched and bootstrap.closed and all(cursor.closed for cursor in cursors)


def test_v140_codec_runs_with_an_in_memory_resource_and_without_importing_the_app(
    sample_request, monkeypatch
):
    codec, structure = worker._load_codec(sample_request)
    assert len(structure["tables"]) == 19
    assert codec.digest_table("settings", [(1, "not necessarily JSON")])["rows"] == 1
    assert "cbs_transcriber.app" not in codec.__dict__
    assert (
        codec.files("cbs_transcriber")
        .joinpath("catalogs/portable_schema_12.json")
        .open("rb")
        .read()
        == CATALOG
    )
