"""Filesystem-only staging uses fresh DB ownership, never a commit phase hint."""

import json
import os
import stat
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cbs_transcriber import deletion_files
from cbs_transcriber.store import Store


@pytest.fixture
def local_files(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic files")
    recording = store.create_recording(project["id"], "source.wav")
    source = store.directory(recording["id"])
    source.mkdir(parents=True)
    (source / "original.wav").write_bytes(b"synthetic original")
    export = store.project_folder(project["id"]) / "exports" / recording["id"]
    export.mkdir(parents=True)
    (export / "result.txt").write_text("synthetic text", encoding="utf-8")
    with store.library_lock():
        plan = deletion_files.prepare(
            store, uuid4().hex, "recording", recording["id"], [recording["id"]], [source, export]
        )
        yield store, project, recording, plan


def test_rollback_restores_exact_files_and_keeps_new_notes(local_files):
    store, project, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.save_notes("projects", project["id"], "New notes after staging", 0)
    restored = deletion_files.settle(store, plan)
    assert restored["state"] == "complete" and restored["committed"] is False
    assert (store.directory(recording["id"]) / "original.wav").read_bytes() == b"synthetic original"
    assert store.one("SELECT notes FROM projects WHERE id=?", (project["id"],))["notes"] == (
        "New notes after staging"
    )
    assert deletion_files.journal_status(store) == []


def test_postcommit_cleanup_only_runs_after_rows_are_gone(local_files, monkeypatch):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    original = deletion_files.shutil.rmtree
    observed = []

    def check(path, **kwargs):
        assert not store.all("SELECT id FROM recordings WHERE id=?", (recording["id"],))
        observed.append(path)
        return original(path, **kwargs)

    monkeypatch.setattr(deletion_files.shutil, "rmtree", check)
    result = deletion_files.settle(store, plan)
    assert result["state"] == "complete" and result["committed"] is True
    assert len(observed) == 2
    assert all(not Path(entry["wrapper"]).exists() for entry in plan["entries"])


def test_failed_cleanup_remains_durable_and_retries(local_files, monkeypatch):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    original = deletion_files.shutil.rmtree

    def locked(*args, **kwargs):
        raise PermissionError("synthetic sharing failure")

    monkeypatch.setattr(deletion_files.shutil, "rmtree", locked)
    assert deletion_files.settle(store, plan)["state"] == "pending"
    assert deletion_files.journal_status(store)[0]["committed"] is True
    monkeypatch.setattr(deletion_files.shutil, "rmtree", original)
    assert deletion_files.retry(store, plan["operation_id"])["state"] == "complete"
    assert not store.all("SELECT id FROM recordings WHERE id=?", (recording["id"],))


def test_caller_plan_cannot_supply_new_path_authority(local_files, tmp_path):
    store, _, _, plan = local_files
    outsider = tmp_path / "outsider"
    outsider.write_bytes(b"retain")
    supplied = json.loads(json.dumps(plan))
    supplied["entries"][0]["original"] = str(outsider)
    with pytest.raises(ValueError):
        deletion_files.stage(store, supplied)
    assert outsider.read_bytes() == b"retain"
    assert deletion_files.settle(store, plan)["committed"] is False


def test_changed_marker_retains_every_payload(local_files):
    store, _, _, plan = local_files
    deletion_files.stage(store, plan)
    marker = Path(plan["entries"][1]["wrapper"]) / "marker.json"
    marker.write_text("{}", encoding="utf-8")
    result = deletion_files.settle(store, plan)
    assert result["state"] == "recovery_pending"
    assert all(Path(entry["payload"]).exists() for entry in plan["entries"])


def test_restore_never_overwrites_a_recreated_destination(local_files):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    source = store.directory(recording["id"])
    source.mkdir()
    (source / "new.txt").write_bytes(b"new owner")
    assert deletion_files.settle(store, plan)["state"] == "recovery_pending"
    assert (source / "new.txt").read_bytes() == b"new owner"
    assert (
        Path(plan["entries"][0]["payload"]) / "original.wav"
    ).read_bytes() == b"synthetic original"


def test_exclusive_rename_refuses_existing_empty_directory(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    with pytest.raises(OSError):
        deletion_files._rename(source, destination)
    assert source.is_dir() and destination.is_dir()


def test_absent_payload_without_cleanup_receipt_is_unresolved(local_files):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    deletion_files.shutil.rmtree(Path(plan["entries"][0]["payload"]))
    result = deletion_files.settle(store, plan)
    assert result["state"] == "pending" and result["committed"] is True
    assert Path(plan["entries"][1]["payload"]).exists()


def test_copy_of_journal_cannot_act_in_another_library(local_files, tmp_path):
    store, _, _, plan = local_files
    deletion_files.stage(store, plan)
    foreign = Store(tmp_path / "foreign")
    deletion_files.shutil.copytree(store.root / "local-deletions", foreign.root / "local-deletions")
    with foreign.library_lock():
        result = deletion_files.recover(foreign)
    assert result[0]["state"] == "recovery_pending" and result[0]["committed"] is None
    assert all(Path(entry["payload"]).exists() for entry in plan["entries"])


def test_operations_require_same_store_lease(tmp_path):
    store = Store(tmp_path / "library")
    with pytest.raises(RuntimeError, match="same Store"):
        deletion_files.recover(store)


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute")
def test_readonly_staged_files_and_directories_are_cleaned(local_files):
    store, _, recording, plan = local_files
    source = store.directory(recording["id"])
    (source / "original.wav").chmod(stat.S_IREAD)
    source.chmod(stat.S_IREAD)
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    assert deletion_files.settle(store, plan)["state"] == "complete"


def test_readonly_retry_retains_a_real_permission_denial(tmp_path):
    target = tmp_path / "writable.txt"
    target.write_bytes(b"retain")
    failure = PermissionError("synthetic ACL failure")
    with pytest.raises(PermissionError) as caught:
        deletion_files._retry_readonly(os.unlink, target, failure)
    assert caught.value is failure
    assert target.read_bytes() == b"retain"


@pytest.mark.parametrize("tag", [0x9000001A, 0x9000101A, 0x9000F01A])
def test_known_cloud_placeholders_are_accepted_in_ancestors_and_payloads(
    tmp_path, monkeypatch, tag
):
    ancestor = tmp_path / "cloud-folder"
    ancestor.mkdir()
    payload = ancestor / "payload"
    payload.mkdir()
    child = payload / "original.wav"
    child.write_bytes(b"synthetic original")
    original = Path.lstat

    def cloud_metadata(path):
        info = original(path)
        if path in {ancestor, child}:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_file_attributes=0x400,
                st_reparse_tag=tag,
            )
        return info

    monkeypatch.setattr(Path, "lstat", cloud_metadata)
    deletion_files._safe(payload)
    deletion_files._tree(payload, "directory")
    assert child.read_bytes() == b"synthetic original"


@pytest.mark.parametrize("tag", [None, 0x9001001A, 0xA0000003, 0xA000000C, 0xB000001A])
def test_unknown_reparse_tags_and_name_surrogates_are_refused(tmp_path, monkeypatch, tag):
    target = tmp_path / "suspect"
    target.mkdir()
    original = Path.lstat

    def suspect_metadata(path):
        info = original(path)
        if path == target:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_file_attributes=0x400,
                st_reparse_tag=tag,
            )
        return info

    monkeypatch.setattr(Path, "lstat", suspect_metadata)
    with pytest.raises(ValueError, match="links"):
        deletion_files._safe(target)


def test_empty_final_journal_is_finished_without_data_path_actions(local_files, monkeypatch):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    original = Path.rmdir
    journal = store.root / "local-deletions" / plan["operation_id"]

    def fail_final_rmdir(path, *args, **kwargs):
        if path == journal:
            raise PermissionError("synthetic interruption after plan unlink")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rmdir", fail_final_rmdir)
    assert deletion_files.settle(store, plan)["state"] == "pending"
    assert journal.is_dir() and not any(journal.iterdir())
    monkeypatch.setattr(Path, "rmdir", original)
    assert deletion_files.retry(store, plan["operation_id"])["state"] == "complete"
    assert not journal.exists()


def test_empty_wrapper_after_marker_unlink_finishes_on_retry(local_files, monkeypatch):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    original = Path.rmdir
    wrapper = Path(plan["entries"][0]["wrapper"])

    def fail_wrapper_rmdir(path, *args, **kwargs):
        if path == wrapper:
            raise PermissionError("synthetic interruption after marker unlink")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rmdir", fail_wrapper_rmdir)
    assert deletion_files.settle(store, plan)["state"] == "pending"
    assert wrapper.is_dir() and not any(wrapper.iterdir())
    monkeypatch.setattr(Path, "rmdir", original)
    assert deletion_files.retry(store, plan["operation_id"])["state"] == "complete"


def test_payload_replacement_during_unlocked_walk_is_retained(local_files, monkeypatch, tmp_path):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    payload = Path(plan["entries"][0]["payload"])
    preserved = tmp_path / "preserved-original-payload"
    original = deletion_files._tree

    def replace_after_walk(path, kind):
        original(path, kind)
        if path == payload:
            path.rename(preserved)
            path.mkdir()
            (path / "new-owner.txt").write_bytes(b"retain replacement")

    monkeypatch.setattr(deletion_files, "_tree", replace_after_walk)
    result = deletion_files.settle(store, plan)
    assert result["state"] == "pending" and result["committed"] is True
    assert (payload / "new-owner.txt").read_bytes() == b"retain replacement"
    assert (preserved / "original.wav").read_bytes() == b"synthetic original"


def test_concurrent_same_operation_never_waits_under_snapshot_lock(local_files):
    store, _, _, plan = local_files
    outcome = []

    def competing_action():
        with store.snapshot_lock:
            with pytest.raises(ValueError, match="already being settled"):
                deletion_files.stage(store, plan)
            outcome.append(deletion_files.settle(store, plan))

    with deletion_files._action(store, plan["operation_id"]):
        worker = threading.Thread(target=competing_action)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert outcome[0]["state"] == "recovery_pending"
    assert outcome[0]["committed"] is None
    assert deletion_files.settle(store, plan)["committed"] is False


@pytest.mark.parametrize("interrupted_file", ["plan.json", "complete.json"])
def test_final_metadata_unlink_interruption_is_retryable(
    local_files, monkeypatch, interrupted_file
):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    original = deletion_files._unlink

    def fail_metadata_unlink(path):
        if path.name == interrupted_file:
            raise PermissionError("synthetic metadata unlink interruption")
        return original(path)

    monkeypatch.setattr(deletion_files, "_unlink", fail_metadata_unlink)
    assert deletion_files.settle(store, plan)["state"] == "pending"
    assert all(not Path(entry["payload"]).exists() for entry in plan["entries"])
    monkeypatch.setattr(deletion_files, "_unlink", original)
    assert deletion_files.retry(store, plan["operation_id"])["state"] == "complete"


def test_unexpected_journal_file_prevents_any_payload_cleanup(local_files):
    store, _, recording, plan = local_files
    deletion_files.stage(store, plan)
    store.execute("DELETE FROM recordings WHERE id=?", (recording["id"],))
    folder = store.root / "local-deletions" / plan["operation_id"]
    (folder / "unexpected.json").write_bytes(b"retain unknown material")
    assert deletion_files.settle(store, plan)["state"] == "recovery_pending"
    assert all(Path(entry["payload"]).exists() for entry in plan["entries"])
