"""Staging must not strand valid Windows files by lengthening their I/O paths."""

import os
import shutil
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cbs_transcriber import deletion_files
from cbs_transcriber.store import Store

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH semantics")


@pytest.fixture
def near_limit_files():
    # A short, caller-owned root keeps the original spelling below MAX_PATH.
    root = Path(tempfile.mkdtemp(prefix="cbs-delete-path-")).resolve()
    try:
        store = Store(root)
        project = store.create_project("Synthetic near-limit deletion")
        recording = store.create_recording(project["id"], "synthetic.wav")
        source = store.directory(recording["id"])
        nested = source / ("n" * 70)
        nested.mkdir(parents=True)
        filename = "x" * (250 - len(str(nested)) - 5) + ".wav"
        assert 4 < len(filename) <= 255
        original = nested / filename
        expected = b"synthetic near-limit original"
        original.write_bytes(expected)
        assert len(str(original)) == 250
        export = store.project_folder(project["id"]) / "exports" / recording["id"]
        export.mkdir(parents=True)
        exported = export / "result.txt"
        exported.write_bytes(b"synthetic export")
        with store.library_lock():
            plan = deletion_files.prepare(
                store,
                uuid4().hex,
                "recording",
                recording["id"],
                [recording["id"]],
                [source, export],
            )
            payload_child = Path(plan["entries"][0]["payload"]) / original.relative_to(source)
            assert len(str(payload_child)) > 260
            yield SimpleNamespace(
                store=store,
                recording=recording,
                plan=plan,
                original=original,
                exported=exported,
                expected=expected,
            )
    finally:
        # Cleanup may itself face a stranded long path after a failed regression.
        # Verify this exact generated root before any recursive filesystem operation.
        assert root.is_absolute() and root.parent == Path(tempfile.gettempdir()).resolve()
        assert root.name.startswith("cbs-delete-path-")
        assert not root.is_symlink() and not root.is_junction()
        value = str(root)
        extended = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
        shutil.rmtree(extended, onexc=deletion_files._retry_readonly)


def test_near_limit_staging_rolls_back_to_exact_original_bytes(near_limit_files):
    files = near_limit_files
    deletion_files.stage(files.store, files.plan)
    assert not files.original.exists()
    result = deletion_files.settle(files.store, files.plan)
    assert result["state"] == "complete" and result["committed"] is False
    assert files.original.read_bytes() == files.expected
    assert files.exported.read_bytes() == b"synthetic export"
    source = files.store.directory(files.recording["id"]).stat()
    assert [source.st_dev, source.st_ino] == files.plan["entries"][0]["identity"]
    assert deletion_files.journal_status(files.store) == []


def test_near_limit_committed_cleanup_removes_the_staged_descendant(near_limit_files):
    files = near_limit_files
    deletion_files.stage(files.store, files.plan)
    files.store.execute("DELETE FROM recordings WHERE id=?", (files.recording["id"],))
    result = deletion_files.settle(files.store, files.plan)
    assert result["state"] == "complete" and result["committed"] is True
    assert all(not Path(entry["wrapper"]).exists() for entry in files.plan["entries"])
    assert deletion_files.journal_status(files.store) == []


def test_near_limit_cleanup_retry_keeps_plain_receipts_and_finishes(near_limit_files, monkeypatch):
    files = near_limit_files
    journal = files.store.root / "local-deletions" / files.plan["operation_id"] / "plan.json"
    captured = journal.read_bytes()
    assert all(
        not entry[key].startswith("\\\\?\\")
        for entry in files.plan["entries"]
        for key in ("original", "wrapper", "payload")
    )
    deletion_files.stage(files.store, files.plan)
    files.store.execute("DELETE FROM recordings WHERE id=?", (files.recording["id"],))
    rmtree = shutil.rmtree
    attempted = []

    def locked(path, **kwargs):
        attempted.append(os.fspath(path))
        raise PermissionError("Synthetic cleanup sharing failure")

    monkeypatch.setattr(shutil, "rmtree", locked)
    try:
        pending = deletion_files.settle(files.store, files.plan)
        assert pending["state"] == "pending" and pending["committed"] is True
        assert attempted
        assert journal.read_bytes() == captured
    finally:
        monkeypatch.setattr(shutil, "rmtree", rmtree)
    assert deletion_files.retry(files.store, files.plan["operation_id"])["state"] == "complete"
    assert deletion_files.journal_status(files.store) == []


def test_near_limit_readonly_child_uses_the_guarded_retry(near_limit_files, monkeypatch):
    files = near_limit_files
    files.original.chmod(stat.S_IREAD)
    deletion_files.stage(files.store, files.plan)
    files.store.execute("DELETE FROM recordings WHERE id=?", (files.recording["id"],))
    retry = deletion_files._retry_readonly
    observed = []

    def guarded(action, filename, error):
        observed.append(os.fspath(filename))
        return retry(action, filename, error)

    monkeypatch.setattr(deletion_files, "_retry_readonly", guarded)
    result = deletion_files.settle(files.store, files.plan)
    assert result["state"] == "complete" and result["committed"] is True
    assert any(len(filename) > 260 for filename in observed)
    assert deletion_files.journal_status(files.store) == []


def test_extended_descendant_still_rejects_reparse_redirection(near_limit_files, monkeypatch):
    files = near_limit_files
    deletion_files.stage(files.store, files.plan)
    files.store.execute("DELETE FROM recordings WHERE id=?", (files.recording["id"],))
    lstat, rmtree = Path.lstat, shutil.rmtree
    rejected = []

    def redirected(path):
        info = lstat(path)
        if path.name == files.original.name:
            rejected.append(os.fspath(path))
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_file_attributes=0x400,
                st_reparse_tag=0xA0000003,
            )
        return info

    def refuse_cleanup(*args, **kwargs):
        pytest.fail("A redirected staged descendant must prevent recursive cleanup")

    monkeypatch.setattr(Path, "lstat", redirected)
    monkeypatch.setattr(shutil, "rmtree", refuse_cleanup)
    try:
        result = deletion_files.settle(files.store, files.plan)
        assert result["state"] == "pending" and result["committed"] is True
        assert rejected and all(len(path) > 260 for path in rejected)
    finally:
        monkeypatch.setattr(Path, "lstat", lstat)
        monkeypatch.setattr(shutil, "rmtree", rmtree)


def test_extended_readonly_retry_preserves_a_writable_file_on_acl_denial(near_limit_files):
    files = near_limit_files
    deletion_files.stage(files.store, files.plan)
    source = Path(files.plan["entries"][0]["original"])
    staged = Path(files.plan["entries"][0]["payload"]) / files.original.relative_to(source)
    extended = deletion_files._staged_io_path(staged)
    denial = PermissionError("Synthetic ACL denial on a writable staged file")
    with pytest.raises(PermissionError) as caught:
        deletion_files._retry_readonly(os.unlink, extended, denial)
    assert caught.value is denial
    assert extended.read_bytes() == files.expected
    assert deletion_files.settle(files.store, files.plan)["state"] == "complete"
    assert files.original.read_bytes() == files.expected
