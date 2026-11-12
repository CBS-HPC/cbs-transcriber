"""Disabled Stage 1 metadata: actual SQLite races, rollback and hostile bounded data."""

import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_runtime_generations_stage1 import compute, reserve, selection, target
from test_runtime_generations_stage1 import db as db
from test_runtime_generations_stage1 import generation as generation
from test_runtime_generations_stage1 import settings as settings


def disk_database(path):
    database = sqlite3.connect(path, isolation_level=None, timeout=5)
    database.execute("PRAGMA journal_mode=WAL")
    database.execute(
        "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY,value TEXT NOT NULL)"
    )
    database.executemany(
        "INSERT OR IGNORE INTO settings VALUES (?,?)", [(9, "keep-nine"), (10, "keep-ten")]
    )
    return database


@pytest.mark.parametrize("same_candidate", [False, True], ids=["conflicting", "idempotent"])
def test_waiting_sqlite_writer_cannot_replace_an_unresolved_candidate(
    generation, settings, tmp_path, monkeypatch, same_candidate
):
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    path = tmp_path / "registry.sqlite3"
    writer = disk_database(path)
    base = target(generation, settings)
    attempted = threading.Event()

    def waiting_writer():
        connection = sqlite3.connect(path, isolation_level=None, timeout=5)
        connection.set_trace_callback(
            lambda statement: attempted.set() if statement == "BEGIN IMMEDIATE" else None
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = reserve(
                    generation,
                    connection,
                    base,
                    owner="b" * 32 if same_candidate else "e" * 32,
                    token="c" * 32 if same_candidate else "f" * 32,
                )
            except generation.Conflict:
                connection.rollback()
                return None
            connection.commit()
            return result
        finally:
            connection.close()

    try:
        writer.execute("BEGIN IMMEDIATE")
        first = reserve(generation, writer, base)
        expected = writer.execute("SELECT value FROM settings WHERE id=11").fetchone()[0]
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(waiting_writer)
            try:
                assert attempted.wait(5)
                writer.commit()
                later = future.result(timeout=8)
            finally:
                writer.rollback()
        assert later == (first if same_candidate else None)
        assert writer.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == expected
        assert writer.execute(
            "SELECT * FROM settings WHERE id IN (9,10) ORDER BY id"
        ).fetchall() == [
            (9, "keep-nine"),
            (10, "keep-ten"),
        ]
    finally:
        writer.close()


def test_stale_deferred_wal_snapshot_cannot_overwrite_a_new_candidate(
    generation, settings, tmp_path, monkeypatch
):
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    path = tmp_path / "registry.sqlite3"
    writer = disk_database(path)
    stale = sqlite3.connect(path, isolation_level=None, timeout=5)
    base = target(generation, settings)
    try:
        stale.execute("BEGIN")
        assert generation.snapshot(stale, base)["candidate"] is None
        writer.execute("BEGIN IMMEDIATE")
        first = reserve(generation, writer, base)
        writer.commit()
        # A deferred transaction cannot prove caller-held writer ownership. SQLite
        # itself refuses its stale read-to-write upgrade; there is no automatic retry.
        with pytest.raises(sqlite3.OperationalError) as error:
            reserve(generation, stale, base, owner="e" * 32, token="f" * 32)
        assert error.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        stale.rollback()
        assert generation.snapshot(stale, base) == first
    finally:
        stale.close()
        writer.close()


@pytest.mark.parametrize("occupied", [False, True], ids=["new-slot", "existing-registry"])
def test_caller_rollback_restores_exact_prior_rows_without_a_hidden_commit(
    generation, db, settings, monkeypatch, occupied
):
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    base = target(generation, settings)
    if occupied:
        raw = json.dumps({"policy": generation.REGISTRY_POLICY, "targets": {}}, indent=3)
        db.execute("INSERT INTO settings VALUES (11,?)", (raw,))
    before = db.execute("SELECT * FROM settings ORDER BY id").fetchall()
    db.execute("BEGIN IMMEDIATE")
    reserved = reserve(generation, db, base)
    assert reserved["candidate"]["owner_id"] == "b" * 32
    assert db.in_transaction
    db.rollback()
    assert db.execute("SELECT * FROM settings ORDER BY id").fetchall() == before


@pytest.mark.parametrize("change", ["operation", "product"])
def test_idempotent_candidate_retry_must_keep_exact_operation_and_product(
    generation, db, settings, monkeypatch, change
):
    monkeypatch.setattr(generation, "CANDIDATE_RESERVATION_ENABLED", True)
    base = target(generation, settings)
    db.execute("BEGIN IMMEDIATE")
    reserve(generation, db, base)
    before = db.execute("SELECT * FROM settings ORDER BY id").fetchall()
    changed_compute = compute()
    if change == "product":
        changed_compute["product"]["id"] = "a-different-structural-reference"
    with pytest.raises(generation.Conflict):
        generation.reserve_candidate(
            db,
            base,
            expected_revision=0,
            owner_id="b" * 32,
            token="c" * 32,
            operation_sha256="e" * 64 if change == "operation" else "d" * 64,
            compute=changed_compute,
        )
    assert db.execute("SELECT * FROM settings ORDER BY id").fetchall() == before
    db.rollback()


@pytest.mark.parametrize("duplicate", ["target", "candidate-owner"])
def test_nested_duplicate_registry_fields_refuse_without_mutating_other_slots(
    generation, db, settings, duplicate
):
    base = target(generation, settings)
    entry = {"target": base, "revision": 0, "active": None, "candidate": None}
    if duplicate == "target":
        encoded = json.dumps(entry)
        entries = f'"{base["target_sha256"]}":{encoded},"{base["target_sha256"]}":{encoded}'
    else:
        candidate = {
            "token": "c" * 32,
            "owner_id": "b" * 32,
            "expected_revision": 0,
            "operation_sha256": "d" * 64,
            "compute": compute(),
        }
        encoded = json.dumps(candidate)
        encoded = encoded[:-1] + ',"owner_id":"' + "b" * 32 + '"}'
        encoded_entry = json.dumps(entry).replace('"candidate": null', '"candidate": ' + encoded)
        entries = f'"{base["target_sha256"]}":{encoded_entry}'
    raw = '{"policy":"' + generation.REGISTRY_POLICY + '","targets":{' + entries + "}}"
    db.execute("INSERT INTO settings VALUES (11,?)", (raw,))
    before = db.execute("SELECT * FROM settings ORDER BY id").fetchall()
    with pytest.raises(generation.Conflict):
        generation.snapshot(db, base)
    assert db.execute("SELECT * FROM settings ORDER BY id").fetchall() == before


def test_oversized_but_valid_registry_refuses_before_json_decoding(
    generation, db, settings, monkeypatch
):
    base = target(generation, settings)
    raw = json.dumps({"policy": generation.REGISTRY_POLICY, "targets": {}})
    raw += " " * generation.MAX_BYTES
    db.execute("INSERT INTO settings VALUES (11,?)", (raw,))

    def must_not_decode(*args, **kwargs):
        raise AssertionError("oversized stored metadata must not be decoded")

    monkeypatch.setattr(generation.json, "loads", must_not_decode)
    with pytest.raises(generation.Conflict):
        generation.snapshot(db, base)
    assert db.execute("SELECT value FROM settings WHERE id=11").fetchone()[0] == raw


def test_small_blob_registry_is_not_silently_promoted_to_text(generation, db, settings):
    base = target(generation, settings)
    raw = json.dumps({"policy": generation.REGISTRY_POLICY, "targets": {}}).encode()
    db.execute("INSERT INTO settings VALUES (11,?)", (raw,))
    with pytest.raises(generation.Conflict):
        generation.snapshot(db, base)
    assert db.execute("SELECT typeof(value),value FROM settings WHERE id=11").fetchone() == (
        "blob",
        raw,
    )


@pytest.mark.parametrize("change", ["server", "project", "runtime"])
def test_selected_paths_cannot_cross_another_target_boundary(generation, settings, change):
    base = target(generation, settings)
    chosen = selection(generation, base, token="c" * 32)
    other = settings.model_copy(deep=True)
    runtime_id = base["runtime_id"]
    if change == "server":
        other.base_url = "https://another.invalid"
    elif change == "project":
        other.ucloud_transcriber.project_id = "another-project"
    else:
        runtime_id = target(generation, settings, "cpu")["runtime_id"]
    with pytest.raises(generation.Conflict):
        generation.runtime_path(other, runtime_id=runtime_id, selection=chosen)


@pytest.mark.parametrize("missing", ["policy", "target", "generation"])
def test_removing_a_present_selection_field_cannot_downgrade_it_to_legacy(
    generation, settings, missing
):
    base = target(generation, settings)
    chosen = copy.deepcopy(selection(generation, base, token="c" * 32))
    del chosen[missing]
    with pytest.raises(generation.Conflict):
        generation.runtime_path(settings, runtime_id=base["runtime_id"], selection=chosen)
