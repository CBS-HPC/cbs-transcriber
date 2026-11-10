"""Disposable short-path SQLite fixtures; no app, server, credential or cloud operations."""

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/activation_checkpoint.py"
SPEC = importlib.util.spec_from_file_location("activation_checkpoint", SCRIPT)
checkpoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checkpoint)


@pytest.fixture
def library(tmp_path):
    root = (tmp_path / "l").resolve()
    media = (tmp_path / "m").resolve()
    root.mkdir()
    media.mkdir()
    db = sqlite3.connect(root / "library.sqlite3")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.executescript("""
        PRAGMA user_version=15;
        CREATE TABLE projects(id TEXT PRIMARY KEY,storage_dir TEXT);
        CREATE TABLE recordings(
            id TEXT PRIMARY KEY,project_id TEXT REFERENCES projects(id),filename TEXT);
        CREATE TABLE jobs(id TEXT PRIMARY KEY,state TEXT,params TEXT);
        CREATE TABLE result_versions(id TEXT PRIMARY KEY,payload TEXT,payload_sha256 TEXT);
        CREATE TABLE note_versions(id TEXT PRIMARY KEY,text TEXT,text_sha256 TEXT);
        CREATE TABLE typed(id INTEGER PRIMARY KEY,i INTEGER,r REAL,t TEXT,b BLOB,n);
    """)
    db.execute("INSERT INTO projects VALUES ('p',?)", (str(media),))
    for index in range(1, 4):
        identifier = f"{index:032x}"
        db.execute("INSERT INTO recordings VALUES (?,'p','PRIVATE_NAME.wav')", (identifier,))
        folder = media / "recordings" / identifier
        folder.mkdir(parents=True)
        if index != 3:
            (folder / "original.wav").write_bytes(b"synthetic audio " + bytes([index]))
    db.execute("INSERT INTO jobs VALUES ('j','completed','PRIVATE_FROZEN_SETTINGS')")
    db.execute("INSERT INTO result_versions VALUES ('v','PRIVATE_TRANSCRIPT','hash')")
    db.execute("INSERT INTO note_versions VALUES ('n','PRIVATE_NOTES','hash')")
    db.execute("INSERT INTO typed VALUES (1,7,0.5,'æ\u0301 🧪',X'00FF',NULL)")
    db.commit()
    journal = root / "local-deletions" / "op"
    journal.mkdir(parents=True)
    (journal / "plan.json").write_text('{"path":"NEVER_FOLLOW_THIS_RECOVERY_PATH"}')
    (root / ".env").write_text("PRIVATE_UNUSED_ENV")
    (root / "server.lock").write_bytes(b"0")
    yield root, media, db
    db.close()


def take(library, output, stage="online"):
    root, media, _ = library
    return checkpoint.capture(root, output.resolve(), [media], stage)


def test_wal_online_and_offline_snapshots_preserve_all_rows_and_dynamic_holes(library, tmp_path):
    root, media, writer = library
    before = {
        name: checkpoint.file_receipt(root / name)
        for name in ("library.sqlite3", "library.sqlite3-wal")
    }
    online = take(library, tmp_path / "online")
    assert {name: checkpoint.file_receipt(root / name) for name in before} == before
    assert online["database"]["tables"]["recordings"]["rows"] == 3
    assert online["missing_original_ids"] == [f"{3:032x}"]
    assert len(online["originals"]) == 3
    assert online["source_quiescence"] == "not_checked"
    assert online["activation_authority"] is online["restore_ready"] is False
    assert online["original_bodies_copied"] is online["local_recovery_bodies_copied"] is False
    receipt = (tmp_path / "online" / "checkpoint.json").read_text()
    assert "PRIVATE_" not in receipt and "NEVER_FOLLOW" not in receipt
    assert sorted(path.name for path in (tmp_path / "online").iterdir()) == [
        "checkpoint.json",
        "library.sqlite3",
    ]
    writer.close()
    offline = take(library, tmp_path / "offline", "offline")
    assert offline["offline_server_lease_held"] is True
    assert (root / "server.lock").read_bytes() == b"0"
    assert offline["database"]["tables"] == online["database"]["tables"]
    compared = checkpoint.compare(
        tmp_path / "online", tmp_path / "offline", tmp_path / "compare.json"
    )
    assert compared["preservation_match"]
    assert (
        compared["before_receipt_sha256"]
        == hashlib.sha256((tmp_path / "online" / "checkpoint.json").read_bytes()).hexdigest()
    )
    with checkpoint.readonly(root / "library.sqlite3") as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("UPDATE jobs SET state='queued'")


@pytest.mark.parametrize(
    "damage",
    [
        "missing_swap",
        "same_size_mtime",
        "history",
        "notes",
        "job",
        "new_recording",
        "schema",
        "recovery",
    ],
)
def test_compare_detects_changes_without_hard_coded_counts(library, tmp_path, damage):
    root, media, db = library
    take(library, tmp_path / "before")
    first = media / "recordings" / f"{1:032x}" / "original.wav"
    if damage == "missing_swap":
        first.unlink()
        (media / "recordings" / f"{3:032x}" / "original.wav").write_bytes(b"restored synthetic")
    elif damage == "same_size_mtime":
        observed = first.stat()
        first.write_bytes(b"X" * observed.st_size)
        os.utime(first, ns=(observed.st_atime_ns, observed.st_mtime_ns))
    elif damage == "history":
        db.execute("UPDATE result_versions SET payload='OTHER_VALID_BYTES',payload_sha256='other'")
    elif damage == "notes":
        db.execute("UPDATE note_versions SET text='DIFFERENT_OLD_NOTES',text_sha256='other'")
    elif damage == "job":
        db.execute("UPDATE jobs SET params='DIFFERENT_FROZEN_OPTIONS'")
    elif damage == "new_recording":
        db.execute("INSERT INTO recordings VALUES (?,'p','new.wav')", (f"{4:032x}",))
    elif damage == "schema":
        db.execute("ALTER TABLE jobs ADD COLUMN added TEXT")
    else:
        (root / "local-deletions" / "op" / "plan.json").write_text("changed recovery bytes")
    db.commit()
    after = take(library, tmp_path / "after", "post")
    result = checkpoint.compare(
        tmp_path / "before", tmp_path / "after", tmp_path / "comparison.json"
    )
    assert not result["preservation_match"] and result["changed_scopes"]
    if damage == "missing_swap":
        assert after["missing_original_ids"] == [
            f"{1:032x}"
        ]  # Same count, different missing identity.
        assert "originals" in result["changed_scopes"]


@pytest.mark.parametrize("missing", range(4))
def test_zero_one_or_several_missing_originals_are_observed_dynamically(library, tmp_path, missing):
    _, media, _ = library
    for index in range(1, 4):
        original = media / "recordings" / f"{index:032x}" / "original.wav"
        if index <= missing:
            original.unlink(missing_ok=True)
        elif not original.exists():
            original.write_bytes(b"synthetic restored original")
    receipt = take(library, tmp_path / "copy")
    assert receipt["missing_original_ids"] == [f"{index:032x}" for index in range(1, missing + 1)]


def test_legacy_null_project_storage_uses_the_explicit_library_root(library, tmp_path):
    root, _, db = library
    db.execute("UPDATE projects SET storage_dir=NULL")
    db.commit()
    folder = root / "recordings" / f"{1:032x}"
    folder.mkdir(parents=True)
    (folder / "original.wav").write_bytes(b"legacy synthetic original")
    receipt = checkpoint.capture(root, tmp_path / "copy", [root], "offline")
    assert receipt["originals"][f"{1:032x}"]["availability"] == "present"
    assert len(receipt["missing_original_ids"]) == 2


def test_active_job_beyond_recent_api_limit_leaves_only_partial_checkpoint(library, tmp_path):
    root, _, db = library
    db.executemany("INSERT INTO jobs VALUES (?,'completed','{}')", [(str(i),) for i in range(250)])
    db.execute("INSERT INTO jobs VALUES ('old-active','queued','{}')")
    db.commit()
    with pytest.raises(ValueError, match="Active local jobs"):
        take(library, tmp_path / "partial")
    assert (tmp_path / "partial" / "library.sqlite3").exists()
    assert not (tmp_path / "partial" / "checkpoint.json").exists()
    with checkpoint.readonly(root / "library.sqlite3") as source:
        assert source.execute("SELECT state FROM jobs WHERE id='old-active'").fetchone() == (
            "queued",
        )


def test_capture_refuses_overwrite_inside_roots_and_unapproved_media(library, tmp_path):
    root, _, _ = library
    take(library, tmp_path / "copy")
    with pytest.raises(FileExistsError):
        take(library, tmp_path / "copy")
    with pytest.raises(ValueError, match="outside all source roots"):
        take(library, root / "would-write-source")
    with pytest.raises(ValueError, match="outside the explicitly approved"):
        checkpoint.capture(root, tmp_path / "unapproved", [root], "offline")
    with pytest.raises(ValueError, match="comparison destination"):
        checkpoint.compare(tmp_path / "copy", tmp_path / "copy", root / "no-comparison.json")
    assert not (root / "no-comparison.json").exists()


def test_changed_checkpoint_bytes_and_changed_receipt_are_refused(library, tmp_path):
    take(library, tmp_path / "copy")
    path = tmp_path / "copy" / "checkpoint.json"
    receipt = json.loads(path.read_text())
    receipt["database"]["tables"]["jobs"]["sha256"] = "0" * 64
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="database evidence changed"):
        checkpoint.load_checkpoint(tmp_path / "copy")
    take(library, tmp_path / "another")
    path = tmp_path / "another" / "library.sqlite3"
    with path.open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="database bytes changed"):
        checkpoint.load_checkpoint(tmp_path / "another")


def test_timeout_and_missing_database_never_publish_complete_receipt(library, tmp_path):
    root, media, _ = library
    with pytest.raises(ValueError, match="time budget"):
        checkpoint.capture(root, tmp_path / "timeout", [media], "online", timeout=-1)
    assert not (tmp_path / "timeout" / "checkpoint.json").exists()
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(ValueError, match="already exist"):
        checkpoint.capture(missing, tmp_path / "no-copy", [media], "online")
    assert not (missing / "library.sqlite3").exists() and not (tmp_path / "no-copy").exists()


def test_typed_framing_preserves_numeric_types_and_float_sign():
    assert checkpoint.framed(1) != checkpoint.framed(1.0)
    assert checkpoint.framed(0.0) != checkpoint.framed(-0.0)
    assert checkpoint.framed("1") != checkpoint.framed(b"1")
    assert checkpoint.framed(None) != checkpoint.framed("")


def test_receipt_is_not_published_after_an_interrupted_write(tmp_path, monkeypatch):
    def interrupted(value, stream, **kwargs):
        stream.write('{"incomplete":')
        raise OSError("synthetic full checkpoint volume")

    monkeypatch.setattr(checkpoint.json, "dump", interrupted)
    with pytest.raises(OSError):
        checkpoint.write_json(tmp_path / "checkpoint.json", {"complete": True})
    assert not (tmp_path / "checkpoint.json").exists()
    assert (tmp_path / "checkpoint.json.pending").exists()


def test_cli_uses_no_app_or_credential_imports(library, tmp_path):
    root, media, _ = library
    code = """
import builtins, runpy, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'cbs_transcriber', 'keyring', 'requests', 'httpx'}:
        raise AssertionError('Forbidden app/credential/network import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(SCRIPT),
            "capture",
            "--library",
            str(root),
            "--original-root",
            str(media),
            "--stage",
            "online",
            "--output",
            str(tmp_path / "cli"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["activation_authority"] is False
    assert (root / ".env").read_text() == "PRIVATE_UNUSED_ENV"
    assert hashlib.sha256((tmp_path / "cli" / "library.sqlite3").read_bytes()).hexdigest()


def test_offline_capture_requires_the_existing_server_lease(library, tmp_path):
    root, media, _ = library
    with checkpoint.existing_lease(root):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "capture",
                "--library",
                str(root),
                "--original-root",
                str(media),
                "--stage",
                "offline",
                "--output",
                str(tmp_path / "blocked"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode == 1 and not (tmp_path / "blocked").exists()
    assert (root / "server.lock").read_bytes() == b"0"
    (root / "server.lock").unlink()
    with pytest.raises(FileNotFoundError):
        take(library, tmp_path / "missing-lease", "offline")
    assert not (root / "server.lock").exists() and not (tmp_path / "missing-lease").exists()


def test_complete_current_schema_store_is_checkpointed_without_reopening_it_in_the_tool(tmp_path):
    from cbs_transcriber.store import Store
    from cbs_transcriber.transcripts import normalize_segments

    root = (tmp_path / "l").resolve()
    store = Store(root)
    project = store.create_project("Disposable full-schema study")
    recording = store.create_recording(project["id"], "synthetic.wav")
    folder = store.directory(recording["id"])
    folder.mkdir(parents=True)
    (folder / "original.wav").write_bytes(b"synthetic preserved source")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic immutable wording æøå"}]),
        {},
        "Synthetic",
        {"kind": "import"},
    )
    store.save_notes("recordings", recording["id"], "Synthetic notes\n🧪", 0)
    job = store.add_job(recording["id"], "transcribe", {"ucloud_run": {"submission_intent": True}})
    store.update_job(job["id"], state="interrupted")
    with store.connection() as db:
        before = checkpoint.fingerprint(db)
    captured = checkpoint.capture(root, tmp_path / "cp", [root], "offline")
    assert captured["database"]["tables"] == before["tables"]
    assert captured["database"]["schema_sha256"] == before["schema_sha256"]
    assert captured["missing_original_ids"] == []
    assert captured["remote_obligations"] == "not_interpreted"
    assert captured["database"]["tables"]["jobs"]["rows"] == 1
    with store.connection() as db:
        assert checkpoint.fingerprint(db) == before
