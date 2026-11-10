"""Cold-copy synthetic files/SQLite only; no app, credentials, models or network."""

import hashlib
import importlib.util
import json
import os
import sqlite3
import struct
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-qualified cold archive tooling")


@pytest.fixture
def archive(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "cold_archive_under_test", SCRIPTS / "cold_archive.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    library, media = (tmp_path / "l").resolve(), (tmp_path / "m").resolve()
    library.mkdir()
    media.mkdir()
    (library / "server.lock").write_bytes(b"0")
    with sqlite3.connect(library / "library.sqlite3") as db:
        db.executescript("""
            PRAGMA user_version=15;
            CREATE TABLE projects(id TEXT PRIMARY KEY,storage_dir TEXT);
            CREATE TABLE recordings(id TEXT PRIMARY KEY,project_id TEXT,filename TEXT);
            CREATE TABLE jobs(id TEXT PRIMARY KEY,state TEXT,params TEXT);
            CREATE TABLE typed(id INTEGER PRIMARY KEY,n,i INTEGER,r REAL,t TEXT,b BLOB);
            INSERT INTO jobs VALUES('j','completed','synthetic frozen request');
            INSERT INTO typed VALUES(1,NULL,7,0.25,'æ\u0301 🧪',X'00FF');
        """)
        db.execute("INSERT INTO projects VALUES ('p',?)", (str(media),))
        db.execute("INSERT INTO recordings VALUES (?,'p','synthetic.wav')", ("1" * 32,))
    db.close()
    folder = media / "recordings" / ("1" * 32)
    folder.mkdir(parents=True)
    (folder / "original.wav").write_bytes(b"tiny synthetic recording")
    (folder / "raw.json").write_bytes(b'{"literal":"synthetic raw evidence"}')
    (media / "project.json").write_text('"æ\u0301 🧪"', encoding="utf-8")
    (media / ".hidden").write_bytes(b"hidden research artifact")
    (media / "empty").mkdir()
    (library / "zero").write_bytes(b"")
    return library, media, folder / "original.wav"


def take(archive, source, tmp_path):
    library, media, _ = source
    output = (tmp_path / "a").resolve()
    reference = archive.capture(library, [media], output)
    return output, reference, json.loads((output / "ARCHIVE.json").read_text(encoding="utf-8"))


def test_complete_copy_and_archive_only_verification(archive, source, tmp_path, monkeypatch):
    library, media, original = source
    output, reference, receipt = take(archive, source, tmp_path)
    assert receipt["copied_bytes_verified"] and receipt["source_before_after_match"]
    assert receipt["offline_server_lease_held"] and receipt["server_lock_excluded"]
    assert receipt["restore_ready"] is receipt["activation_authority"] is False
    assert receipt["missing_original_ids"] == []
    assert receipt["metadata_policy"] == "windows-ea-size-zero-v1"
    assert len(receipt["roots"]) == 2
    for root in receipt["roots"]:
        source_root, copied = Path(root["source"]), output / root["copy"]
        for name, item in root["entries"].items():
            if item["kind"] == "file":
                assert (copied / name).read_bytes() == (source_root / name).read_bytes()
    assert not (output / "root-000/server.lock").exists()
    assert (library / "server.lock").read_bytes() == b"0"
    # Delete/rename only this synthetic source tree: verify must never reopen source paths.
    media.rename(tmp_path / "old-media")
    library.rename(tmp_path / "old-library")
    assert not original.exists()
    assert archive.verify(output, reference) == {
        "archive_verified": True,
        "restore_ready": False,
        "activation_authority": False,
    }


def test_committed_wal_and_cli_round_trip(archive, source, tmp_path):
    library, media, _ = source
    writer = sqlite3.connect(library / "library.sqlite3")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO typed VALUES(2,NULL,8,0.75,'committed WAL',X'FFFF')")
        writer.commit()
        output = tmp_path / "a"
        captured = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "cold_archive.py"),
                "capture",
                "--library",
                str(library),
                "--project-root",
                str(media),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert captured.returncode == 0, captured.stdout + captured.stderr
        reference = json.loads(captured.stdout)["manifest_sha256"]
        with closing(archive.checkpoint.readonly(output / "checkpoint/library.sqlite3")) as db:
            assert db.execute("SELECT t FROM typed WHERE id=2").fetchone() == ("committed WAL",)
        receipt = json.loads((output / "ARCHIVE.json").read_text(encoding="utf-8"))
        root = next(root for root in receipt["roots"] if root["source"] == str(library))
        assert "library.sqlite3-wal" in root["entries"]
        verified = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "cold_archive.py"),
                "verify",
                "--archive",
                str(output),
                "--expected-manifest-sha256",
                reference,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert verified.returncode == 0, verified.stdout + verified.stderr
        assert json.loads(verified.stdout)["archive_verified"]
    finally:
        writer.close()


def test_closed_wal_database_readonly_bookkeeping_is_not_a_directory_change(
    archive, source, tmp_path
):
    with sqlite3.connect(source[0] / "library.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("UPDATE typed SET t='closed WAL database'")
    db.close()
    output, reference, _ = take(archive, source, tmp_path)
    assert archive.verify(output, reference)["archive_verified"]


@pytest.mark.parametrize("change", ["bytes", "new_file", "remove", "empty_directory"])
def test_source_changes_refuse_completion(archive, source, tmp_path, monkeypatch, change):
    _, media, original = source
    original_copy = archive.copy_root

    def changing_copy(root, target, expected):
        original_copy(root, target, expected)
        if root != media:
            return
        if change == "bytes":
            before = original.stat()
            original.write_bytes(b"X" * before.st_size)
            os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == "new_file":
            (media / "new").write_bytes(b"new")
        elif change == "remove":
            original.unlink()
        else:
            (media / "new-empty").mkdir()

    monkeypatch.setattr(archive, "copy_root", changing_copy)
    with pytest.raises(ValueError, match="source root changed"):
        take(archive, source, tmp_path)
    assert not (tmp_path / "a/ARCHIVE.json").exists()


def test_interruption_retains_partial_and_cannot_resume_or_overwrite(
    archive, source, tmp_path, monkeypatch
):
    actual = archive.copy_file

    def interrupted(*args):
        actual(*args)
        raise OSError("synthetic interrupted copy")

    monkeypatch.setattr(archive, "copy_file", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        take(archive, source, tmp_path)
    output = tmp_path / "a"
    assert list(output.glob("root-*/*")) and not (output / "ARCHIVE.json").exists()
    with pytest.raises(FileExistsError):
        take(archive, source, tmp_path)
    with pytest.raises(FileNotFoundError):
        archive.verify(output, "0" * 64)


@pytest.mark.parametrize(
    "damage", ["bytes", "new_file", "remove", "directory", "checkpoint", "manifest"]
)
def test_verify_detects_archive_damage(archive, source, tmp_path, damage):
    output, reference, receipt = take(archive, source, tmp_path)
    copied = output / receipt["roots"][1]["copy"]
    if damage == "bytes":
        path = copied / "project.json"
        before = path.stat()
        path.write_bytes(b"X" * before.st_size)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif damage == "new_file":
        (copied / "added").write_bytes(b"new")
    elif damage == "remove":
        (copied / ".hidden").unlink()
    elif damage == "directory":
        (copied / "empty").rmdir()
    elif damage == "checkpoint":
        (output / "checkpoint/library.sqlite3").write_bytes(b"corrupt")
    else:
        (output / "ARCHIVE.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        archive.verify(output, reference)


@pytest.mark.parametrize(
    "bad", ["missing_root", "extra_root", "duplicate_root", "inside", "ancestor", "existing"]
)
def test_root_and_destination_boundaries(archive, source, tmp_path, bad):
    library, media, _ = source
    output = (tmp_path / "a").resolve()
    supplied = [media]
    if bad == "missing_root":
        supplied = []
    elif bad == "extra_root":
        extra = tmp_path / "extra"
        extra.mkdir()
        supplied.append(extra)
    elif bad == "duplicate_root":
        supplied.append(media)
    elif bad == "inside":
        output = media / "archive"
    elif bad == "ancestor":
        output = tmp_path
    else:
        output.mkdir()
        (output / "keep").write_bytes(b"existing")
    with pytest.raises((ValueError, FileExistsError)):
        archive.capture(library, supplied, output)
    assert not (output / "ARCHIVE.json").exists()
    if bad == "existing":
        assert (output / "keep").read_bytes() == b"existing"


@pytest.mark.parametrize("block", ["queued", "running", "recovery", "lease"])
def test_idle_recovery_and_existing_lease_boundaries(archive, source, tmp_path, block):
    library, media, _ = source
    if block in {"queued", "running"}:
        with sqlite3.connect(library / "library.sqlite3") as db:
            db.execute("UPDATE jobs SET state=?", (block,))
        db.close()
    elif block == "recovery":
        folder = library / "local-deletions"
        folder.mkdir()
        (folder / "pending").write_bytes(b"must not parse or follow")
    if block == "lease":
        with archive.checkpoint.existing_lease(library), pytest.raises(OSError):
            archive.capture(library, [media], tmp_path / "a")
    else:
        with pytest.raises(ValueError):
            archive.capture(library, [media], tmp_path / "a")
    assert not (tmp_path / "a/ARCHIVE.json").exists()


def test_missing_original_is_recorded_without_recovery_claim(archive, source, tmp_path):
    source[2].unlink()
    output, reference, receipt = take(archive, source, tmp_path)
    assert receipt["missing_original_ids"] == ["1" * 32]
    assert receipt["restore_ready"] is False
    assert archive.verify(output, reference)["archive_verified"]


@pytest.mark.parametrize("change", ["remove", "bytes", "appeared"])
def test_checkpoint_original_must_match_later_baseline(
    archive, source, tmp_path, monkeypatch, change
):
    original = source[2]
    if change == "appeared":
        original.unlink()
    actual = archive.checkpoint._capture

    def checkpoint_then_change(*args, **kwargs):
        receipt = actual(*args, **kwargs)
        if change == "remove":
            original.unlink()
        else:
            original.write_bytes(b"changed or appeared after checkpoint")
        return receipt

    monkeypatch.setattr(archive.checkpoint, "_capture", checkpoint_then_change)
    with pytest.raises(ValueError, match="Checkpoint original evidence"):
        take(archive, source, tmp_path)
    assert not (tmp_path / "a/ARCHIVE.json").exists()


def test_checkpoint_database_must_match_raw_source_copy(archive, source, tmp_path, monkeypatch):
    actual = archive.checkpoint._capture

    def checkpoint_then_change(*args, **kwargs):
        receipt = actual(*args, **kwargs)
        with sqlite3.connect(source[0] / "library.sqlite3") as db:
            db.execute("INSERT INTO typed VALUES(2,NULL,9,0.5,'later value',X'FF')")
        db.close()
        return receipt

    monkeypatch.setattr(archive.checkpoint, "_capture", checkpoint_then_change)
    with pytest.raises(ValueError, match="Checkpoint database"):
        take(archive, source, tmp_path)
    assert not (tmp_path / "a/ARCHIVE.json").exists()


def test_database_changes_after_logical_probe_before_baseline_refuse(
    archive, source, tmp_path, monkeypatch
):
    actual = archive.inventory
    changed = False

    def change_at_baseline(root, **kwargs):
        nonlocal changed
        if root == source[0] and kwargs.get("bodies", True) and not changed:
            changed = True
            with sqlite3.connect(source[0] / "library.sqlite3") as db:
                db.execute("INSERT INTO typed VALUES(2,NULL,9,0.5,'later value',X'FF')")
            db.close()
        return actual(root, **kwargs)

    monkeypatch.setattr(archive, "inventory", change_at_baseline)
    with pytest.raises(ValueError, match="Database byte anchors"):
        take(archive, source, tmp_path)
    assert changed and not (tmp_path / "a/ARCHIVE.json").exists()


@pytest.mark.parametrize("change", ["appeared", "disappeared"])
def test_wal_presence_changes_around_logical_probe_refuse(
    archive, source, tmp_path, monkeypatch, change
):
    writer = None
    if change == "disappeared":
        writer = sqlite3.connect(source[0] / "library.sqlite3")
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE typed SET t='WAL data'")
        writer.commit()
    actual, calls, observations = archive.database_files, 0, []

    def change_on_second_anchor(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "appeared":
                (root / "library.sqlite3-wal").write_bytes(b"new synthetic sidecar")
            else:
                writer.close()
        value = actual(root)
        observations.append(value)
        return value

    monkeypatch.setattr(archive, "database_files", change_on_second_anchor)
    try:
        with pytest.raises(ValueError, match="Database byte anchors"):
            take(archive, source, tmp_path)
        assert (observations[0]["library.sqlite3-wal"] is None) == (change == "appeared")
        assert (observations[1]["library.sqlite3-wal"] is None) == (change == "disappeared")
        assert not (tmp_path / "a/ARCHIVE.json").exists()
    finally:
        if writer is not None:
            writer.close()


def test_verify_binds_checkpoint_original_to_archive_even_with_new_manifest_reference(
    archive, source, tmp_path
):
    output, _, receipt = take(archive, source, tmp_path)
    root = next(root for root in receipt["roots"] if root["source"] == str(source[1]))
    name = f"recordings/{'1' * 32}/original.wav"
    path = output / root["copy"] / name
    before = path.stat()
    path.write_bytes(b"X" * before.st_size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    root["entries"][name]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    # Even a caller's newly trusted manifest cannot contradict the retained checkpoint.
    raw = json.dumps(receipt).encode()
    (output / "ARCHIVE.json").write_bytes(raw)
    with pytest.raises(ValueError, match="Checkpoint original evidence"):
        archive.verify(output, hashlib.sha256(raw).hexdigest())


def test_nested_project_roots_are_copied_once_and_all_bindings_retained(archive, source, tmp_path):
    library, media, _ = source
    nested = media / "nested"
    nested.mkdir()
    (nested / "retained").write_bytes(b"second project artifact")
    with sqlite3.connect(library / "library.sqlite3") as db:
        db.execute("INSERT INTO projects VALUES('p2',?)", (str(nested),))
    db.close()
    output = tmp_path / "a"
    reference = archive.capture(library, [media, nested], output)
    receipt = json.loads((output / "ARCHIVE.json").read_text(encoding="utf-8"))
    assert len(receipt["roots"]) == 2 and len(receipt["project_roots"]) == 2
    assert archive.verify(output, reference)["archive_verified"]


def test_large_file_same_metadata_mutation_during_copy_is_detected(
    archive, source, tmp_path, monkeypatch
):
    original = source[2]
    original.write_bytes(b"A" * (3 * 1024 * 1024))
    actual_copy, actual_open = archive.copy_file, Path.open
    changed = False

    class ChangingReader:
        def __init__(self, reader):
            self.reader = reader

        def __enter__(self):
            self.reader.__enter__()
            return self

        def __exit__(self, *args):
            return self.reader.__exit__(*args)

        def fileno(self):
            return self.reader.fileno()

        def read(self, count):
            nonlocal changed
            value = self.reader.read(count)
            if value and not changed:
                changed = True
                info = original.stat()
                original.write_bytes(b"X" * info.st_size)
                os.utime(original, ns=(info.st_atime_ns, info.st_mtime_ns))
            return value

    def opening(path, mode="r", *args, **kwargs):
        reader = actual_open(path, mode, *args, **kwargs)
        return ChangingReader(reader) if path == original and mode == "rb" else reader

    def during_copy(path, *args):
        with monkeypatch.context() as local:
            if path == original:
                local.setattr(Path, "open", opening)
            return actual_copy(path, *args)

    monkeypatch.setattr(archive, "copy_file", during_copy)
    with pytest.raises(ValueError, match="bytes changed while copying"):
        take(archive, source, tmp_path)
    assert changed and not (tmp_path / "a/ARCHIVE.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS junction/stream acceptance")
@pytest.mark.parametrize("kind", ["junction", "named_stream", "root_stream", "directory_stream"])
def test_real_windows_nonportable_files_refused(archive, source, tmp_path, kind):
    _, media, original = source
    if kind == "junction":
        external = tmp_path / "outside"
        external.mkdir()
        (external / "keep").write_bytes(b"never copy through a junction")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(media / "linked"), str(external)],
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
    else:
        selected = {
            "named_stream": original,
            "root_stream": media,
            "directory_stream": media / "empty",
        }[kind]
        Path(str(selected) + ":synthetic").write_bytes(b"extra named bytes")
    with pytest.raises(ValueError):
        take(archive, source, tmp_path)
    assert not (tmp_path / "a/ARCHIVE.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS archive metadata streams")
@pytest.mark.parametrize(
    "node",
    [".", "checkpoint", "checkpoint/library.sqlite3", "checkpoint/checkpoint.json", "ARCHIVE.json"],
)
def test_archive_metadata_named_streams_refused(archive, source, tmp_path, node):
    output, reference, _ = take(archive, source, tmp_path)
    path = output if node == "." else output / node
    Path(str(path) + ":synthetic").write_bytes(b"must not silently omit this stream")
    with pytest.raises(ValueError, match="Named file streams"):
        archive.verify(output, reference)


@pytest.mark.parametrize("attribute", [0x4000, 0x1000, 0x40000, 0x400000])
def test_encrypted_offline_recall_refuses_before_body_read(
    archive, source, tmp_path, monkeypatch, attribute
):
    original = source[2]
    actual_stat = Path.stat
    info = original.stat()

    def replaced_stat(path, *args, **kwargs):
        if path == original:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=attribute)
        return actual_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", replaced_stat)
    with pytest.raises(ValueError, match="Encrypted/offline/recall"):
        archive.capture(source[0], [source[1]], tmp_path / "a")
    assert not (tmp_path / "a").exists()


def test_unsupported_lock_refuses_before_lease_open(archive, source, tmp_path, monkeypatch):
    lock = source[0] / "server.lock"
    actual_stat = Path.stat

    info = lock.stat()

    def replaced_stat(path, *args, **kwargs):
        if path == lock:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x1000)
        return actual_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", replaced_stat)
    monkeypatch.setattr(
        archive.checkpoint, "existing_lease", lambda *_: pytest.fail("lease opened")
    )
    with pytest.raises(ValueError, match="Encrypted/offline/recall"):
        archive.capture(source[0], [source[1]], tmp_path / "a")


def test_cli_has_only_capture_verify_and_refuses_bad_reference(archive, source, tmp_path):
    output, _, _ = take(archive, source, tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "cold_archive.py"),
            "verify",
            "--archive",
            str(output),
            "--expected-manifest-sha256",
            "0" * 64,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1 and "manifest_sha256" not in result.stdout
    assert json.loads(result.stdout)["archive_failed"] == "ValueError"
    assert "synthetic" not in result.stdout and "frozen request" not in result.stdout
    assert hashlib.sha256((output / "ARCHIVE.json").read_bytes()).hexdigest() != "0" * 64


def synthetic_ntfs_ea(path):
    """Write only caller-created synthetic EA metadata; never used with real media."""
    import ctypes
    from ctypes import wintypes

    class Status(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    api = ctypes.WinDLL("ntdll")
    api.NtSetEaFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Status),
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    api.NtSetEaFile.restype = wintypes.LONG
    name, value = b"cbs.synthetic", b"owned synthetic metadata"
    raw = struct.pack("<IBBH", 0, 0, len(name), len(value)) + name + b"\0" + value
    buffer = ctypes.create_string_buffer(raw)
    # FILE_WRITE_EA only, OPEN_EXISTING, metadata/reparse/no-recall flags.
    handle = kernel.CreateFileW(str(path), 0x10, 7, None, 3, 0x02300000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Synthetic EA handle failed.")
    try:
        status = Status()
        result = api.NtSetEaFile(handle, ctypes.byref(status), buffer, len(raw))
        if result < 0:
            raise OSError(result & 0xFFFFFFFF, "Synthetic EA creation failed.")
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows NTFS extended attributes")
@pytest.mark.parametrize("node", ["file", "directory"])
def test_ntfs_eas_refuse_before_original_body_read(archive, source, tmp_path, monkeypatch, node):
    selected = source[2] if node == "file" else source[1] / "empty"
    synthetic_ntfs_ea(selected)
    original_open = Path.open

    def no_body(path, mode="r", *args, **kwargs):
        if path == source[2] and "r" in mode:
            pytest.fail("Original body read before unsupported EA refusal")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_body)
    with pytest.raises(ValueError, match="[Ee]xtended.attributes"):
        take(archive, source, tmp_path)
    assert not (tmp_path / "a").exists()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows NTFS extended attributes")
@pytest.mark.parametrize(
    "node", ["copied-root", "checkpoint", "checkpoint-json", "checkpoint-db", "manifest"]
)
def test_ntfs_eas_in_archive_metadata_refuse(archive, source, tmp_path, node):
    output, reference, receipt = take(archive, source, tmp_path)
    selected = {
        "copied-root": output / receipt["roots"][0]["copy"],
        "checkpoint": output / "checkpoint",
        "checkpoint-json": output / "checkpoint/checkpoint.json",
        "checkpoint-db": output / "checkpoint/library.sqlite3",
        "manifest": output / "ARCHIVE.json",
    }[node]
    synthetic_ntfs_ea(selected)
    with pytest.raises(ValueError, match="[Ee]xtended.attributes"):
        archive.verify(output, reference)


@pytest.mark.skipif(os.name != "nt", reason="Windows enumeration metadata qualification")
@pytest.mark.parametrize("attribute", [0x40000, 0x400000])
def test_enumerated_recall_refuses_before_metadata_handle(archive, source, monkeypatch, attribute):
    import ctypes
    from ctypes import wintypes

    original = ctypes.WinDLL

    def replace(name, *args, **kwargs):
        api = original(name, *args, **kwargs)
        if name != "kernel32":
            return api
        first = api.FindFirstFileW
        first.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.WIN32_FIND_DATAW)]
        first.restype = wintypes.HANDLE

        def enumerate_with_recall(path, buffer):
            result = first(path, buffer)
            assert result != ctypes.c_void_p(-1).value
            ctypes.cast(
                buffer, ctypes.POINTER(wintypes.WIN32_FIND_DATAW)
            ).contents.dwFileAttributes |= attribute
            return result

        def forbidden(*args):
            pytest.fail("Metadata handle opened despite enumerated recall flag")

        return SimpleNamespace(
            FindFirstFileW=enumerate_with_recall,
            FindClose=api.FindClose,
            CreateFileW=forbidden,
            CloseHandle=api.CloseHandle,
        )

    monkeypatch.setattr(ctypes, "WinDLL", replace)
    with pytest.raises(ValueError, match="enumeration"):
        archive.regular(source[2])


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata-only EA query")
@pytest.mark.parametrize(("result", "byte_count"), [(0x103, 4), (0, 0)])
def test_pending_or_short_ea_metadata_refuses(archive, source, monkeypatch, result, byte_count):
    import ctypes

    original = ctypes.WinDLL

    def replace(name, *args, **kwargs):
        api = original(name, *args, **kwargs)
        if name != "ntdll":
            return api

        def query(handle, status, size, length, information_class):
            assert length == 4 and information_class == 7
            status._obj.information = byte_count
            size._obj.value = 0
            return result

        return SimpleNamespace(NtQueryInformationFile=query)

    monkeypatch.setattr(ctypes, "WinDLL", replace)
    with pytest.raises(OSError, match="extended-attribute size"):
        archive.regular(source[2])


@pytest.mark.skipif(os.name != "nt", reason="Real Windows NTFS extended attributes")
def test_cli_reports_ea_refusal_without_names_values_or_paths(archive, source, tmp_path):
    synthetic_ntfs_ea(source[2])
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "cold_archive.py"),
            "capture",
            "--library",
            str(source[0]),
            "--project-root",
            str(source[1]),
            "--output",
            str(tmp_path / "a"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["archive_failed"] == "ExtendedAttributesUnsupported"
    assert "cbs.synthetic" not in result.stdout and str(source[2]) not in result.stdout
    assert "owned synthetic metadata" not in result.stdout and not (tmp_path / "a").exists()


@pytest.mark.parametrize("policy", [None, "unknown-ea-policy"])
def test_missing_or_unknown_source_ea_policy_cannot_be_retrofitted(
    archive, source, tmp_path, policy
):
    output, _, receipt = take(archive, source, tmp_path)
    if policy is None:
        del receipt["metadata_policy"]
    else:
        receipt["metadata_policy"] = policy
    raw = json.dumps(receipt).encode()
    (output / "ARCHIVE.json").write_bytes(raw)
    with pytest.raises(ValueError, match="source EA qualification policy"):
        archive.verify(output, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("operation", ["capture", "verify"])
def test_non_windows_cannot_claim_windows_ea_qualification(
    archive, source, tmp_path, monkeypatch, operation
):
    with monkeypatch.context() as local:
        local.setattr(archive.os, "name", "posix")
        with pytest.raises(ValueError, match="require Windows metadata APIs"):
            if operation == "capture":
                archive.capture(source[0], [source[1]], tmp_path / "a")
            else:
                archive.verify(tmp_path / "a", "0" * 64)
