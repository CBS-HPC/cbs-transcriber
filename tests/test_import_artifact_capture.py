"""Exact import receipts use synthetic bytes and temporary managed storage only."""

import hashlib
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import store as store_module
from cbs_transcriber.history import encode_payload
from cbs_transcriber.storage import relocate_project
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import MAX_TRANSCRIPT_BYTES, parse_transcript

JSON_BYTES = (
    b'\xef\xbb\xbf{\r\n "segments": [{"start":0,"end":2,'
    b'"text":"Hej K\xc3\xb8benhavn.","speaker":"A"}]\r\n}\r\n'
)
CSV_BYTES = 'text,end,speaker,start\r\n"Hej København.",2,A,0\r\n'.encode()


@pytest.fixture
def imported(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic study")
    recording = store.create_recording(project["id"], "interview.wav")
    return store, project, recording


def receipt(raw=JSON_BYTES, source_id="a" * 32, format="json"):
    return {
        "schema": 1,
        "role": "transcript_import",
        "source_id": source_id,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "format": format,
    }


def preserve(store, recording, raw=JSON_BYTES, evidence=None):
    evidence = evidence or receipt(raw)
    folder = store.directory(recording["id"]) / "sources"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (evidence["source_id"] + "." + evidence["format"])
    path.write_bytes(raw)
    return evidence, path


def add_captured(store, recording, evidence, segments=None, **kwargs):
    return store.add_result(
        recording["id"],
        segments if segments is not None else parse_transcript(JSON_BYTES, "raw.json"),
        kwargs.pop("speakers", {}),
        "Imported",
        kwargs.pop("source", {"kind": "import"}),
        import_artifact_evidence=evidence,
        **kwargs,
    )


def assert_unpublished(store, recording):
    assert store.results(recording["id"]) == []
    assert store.all("SELECT * FROM result_versions") == []
    current = store.recording(recording["id"])
    assert current["revision"] == 0
    assert current["segments"] == []
    assert current["active_transcript_id"] is None


@pytest.mark.parametrize(
    "raw,filename,format",
    [(JSON_BYTES, "private.v2.JSON", "json"), (CSV_BYTES, "private.v2.csv", "csv")],
)
def test_v127_import_binds_exact_bytes_and_origin_atomically(imported, raw, filename, format):
    store, _, recording = imported
    result = store.import_transcript(recording["id"], raw, filename, 0)
    evidence = result["source"]["artifact_evidence"]
    assert evidence == receipt(raw, evidence["source_id"], format) | {"schema": 2}
    assert len(evidence["source_id"]) == 32
    path = store.directory(recording["id"]) / "sources" / (evidence["source_id"] + "." + format)
    assert path.read_bytes() == raw
    assert filename not in path.name
    assert result["segments"] == parse_transcript(raw, filename)
    versions = store.result_versions(recording["id"], result["id"])
    assert len(versions) == 1
    assert versions[0]["action"] == "imported_result"
    assert versions[0]["context"]["artifact_evidence"] == evidence
    assert store.recording(recording["id"])["active_transcript_id"] == result["id"]
    assert (
        store.recording(recording["id"])["result_roles"]["working"]["version_id"]
        == versions[0]["id"]
    )


def test_v127_same_bytes_are_distinct_imports_and_survive_restart(imported):
    store, _, recording = imported
    first = store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 0)
    second = store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 1)
    left, right = (item["source"]["artifact_evidence"] for item in (first, second))
    assert left["sha256"] == right["sha256"]
    assert left["source_id"] != right["source_id"]
    restarted = Store(store.root)
    assert restarted.result(recording["id"], first["id"])["source"] == first["source"]
    assert len(list((restarted.directory(recording["id"]) / "sources").iterdir())) == 2


def test_v127_hint_stripping_and_untrusted_filename_preserve_exact_parser_proof(imported):
    store, _, recording = imported
    raw = b'{"segments":[{"text":"Synthetic words", "start":0,"end":2,"avg_logprob":-2}]}'
    result = store.import_transcript(recording["id"], raw, "../../outside.v2.json", 0)
    assert "_hints" not in result["segments"][0]
    evidence = result["source"]["artifact_evidence"]
    target = store.directory(recording["id"]) / "sources" / (evidence["source_id"] + ".json")
    assert target.read_bytes() == raw
    assert not (store.project_folder(recording["project_id"]) / "outside.v2.json").exists()


@pytest.mark.parametrize("stage", ["write", "flush", "fsync", "close"])
def test_v127_durable_write_failures_publish_nothing_and_keep_raw(imported, monkeypatch, stage):
    store, _, recording = imported
    original_open = Path.open

    class FailedWrite:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()
            if stage == "close":
                raise OSError("synthetic close failure")

        def write(self, data):
            if stage == "write":
                self.stream.write(data[:5])
                raise OSError("synthetic write failure")
            return self.stream.write(data)

        def flush(self):
            if stage == "flush":
                raise OSError("synthetic flush failure")
            return self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    def fail_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return FailedWrite(stream) if mode == "xb" else stream

    monkeypatch.setattr(Path, "open", fail_open)
    if stage == "fsync":
        monkeypatch.setattr(
            store_module.os,
            "fsync",
            lambda _: (_ for _ in ()).throw(OSError("synthetic fsync failure")),
        )
    with pytest.raises(ValueError, match="could not be preserved"):
        store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 0)
    assert_unpublished(store, recording)
    files = list((store.directory(recording["id"]) / "sources").iterdir())
    assert len(files) == 1 and files[0].read_bytes()


def test_v127_sql_failure_keeps_durable_raw_without_receipt(imported, monkeypatch):
    store, _, recording = imported

    def fail_version(*args, **kwargs):
        raise RuntimeError("synthetic SQL publication failure")

    monkeypatch.setattr(store, "_append_version", fail_version)
    with pytest.raises(RuntimeError, match="SQL publication"):
        store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 0)
    assert_unpublished(store, recording)
    files = list((store.directory(recording["id"]) / "sources").iterdir())
    assert len(files) == 1 and files[0].read_bytes() == JSON_BYTES


@pytest.mark.parametrize(
    "raw,filename",
    [
        (b"", "raw.json"),
        (b"bad", "raw.json"),
        (b"{}", "raw.txt"),
        (b"x" * (MAX_TRANSCRIPT_BYTES + 1), "raw.json"),
    ],
    ids=["empty", "malformed", "unsupported", "oversized"],
)
def test_v127_invalid_import_publishes_nothing(imported, raw, filename):
    store, _, recording = imported
    with pytest.raises(ValueError):
        store.import_transcript(recording["id"], raw, filename, 0)
    assert_unpublished(store, recording)
    assert not (store.directory(recording["id"]) / "sources").exists()


def test_v127_stale_revision_and_missing_recording_write_nothing(imported):
    store, _, recording = imported
    with pytest.raises(ValueError, match="another window"):
        store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 1)
    with pytest.raises(KeyError):
        store.import_transcript("f" * 32, JSON_BYTES, "raw.json", 0)
    assert_unpublished(store, recording)
    assert not (store.directory(recording["id"]) / "sources").exists()


def test_v127_exclusive_creation_never_overwrites_colliding_file(imported, monkeypatch):
    store, _, recording = imported
    evidence, path = preserve(store, recording, b"Keep this exact original")
    monkeypatch.setattr(store_module, "new_id", lambda: evidence["source_id"])
    with pytest.raises(ValueError, match="could not be preserved"):
        store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 0)
    assert path.read_bytes() == b"Keep this exact original"
    assert_unpublished(store, recording)


@pytest.mark.parametrize(
    "change", ["missing", "hash", "size", "payload", "speakers", "format", "directory"]
)
def test_v127_direct_capture_refuses_unproven_file_or_parser_payload(imported, change):
    store, _, recording = imported
    evidence, path = preserve(store, recording)
    segments = parse_transcript(JSON_BYTES, "raw.json")
    kwargs = {}
    if change == "missing":
        path.unlink()
    elif change == "hash":
        path.write_bytes(JSON_BYTES.replace(b"Hej", b"Nej"))
    elif change == "size":
        evidence["bytes"] += 1
    elif change == "payload":
        segments[0]["text"] = "Unrelated invented wording"
    elif change == "speakers":
        kwargs["speakers"] = {"A": "An invented name"}
    elif change == "format":
        evidence["format"] = "csv"
        path.rename(path.with_suffix(".csv"))
    else:
        path.unlink()
        path.mkdir()
    with pytest.raises(ValueError):
        add_captured(store, recording, evidence, segments, **kwargs)
    assert_unpublished(store, recording)


@pytest.mark.parametrize(
    "kind,job", [("provider", None), ("enhancement", None), ("import", "j" * 32)]
)
def test_v127_capture_refuses_other_origins_and_producing_jobs(imported, kind, job):
    store, _, recording = imported
    evidence, _ = preserve(store, recording)
    with pytest.raises(ValueError, match="Only a new transcript import"):
        add_captured(store, recording, evidence, source={"kind": kind}, job_id=job)
    assert_unpublished(store, recording)


def test_v127_source_dictionary_cannot_self_assert_capture(imported):
    store, _, recording = imported
    evidence, _ = preserve(store, recording)
    with pytest.raises(ValueError, match="cannot assert"):
        store.add_result(
            recording["id"],
            parse_transcript(JSON_BYTES, "raw.json"),
            {},
            "Raw",
            {"kind": "import", "artifact_evidence": evidence},
        )
    assert_unpublished(store, recording)
    legacy = store.add_result(
        recording["id"], parse_transcript(JSON_BYTES, "raw.json"), {}, "Legacy", {"kind": "import"}
    )
    assert (
        store.result_versions(recording["id"], legacy["id"])[0]["context"]["artifact_evidence"]
        == "not_captured"
    )


def test_v127_cross_recording_source_is_not_owned(imported):
    store, project, recording = imported
    evidence, _ = preserve(store, recording)
    other = store.create_recording(project["id"], "other.wav")
    with pytest.raises(ValueError):
        add_captured(store, other, evidence)
    assert_unpublished(store, other)


def test_v127_path_and_descriptor_ctime_use_their_own_observation_pairs(imported, monkeypatch):
    store, _, recording = imported
    evidence, _ = preserve(store, recording)
    original_fstat = os.fstat

    def distinct_descriptor_ctime(descriptor):
        info = original_fstat(descriptor)
        return SimpleNamespace(
            **{
                key: getattr(info, key)
                for key in ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns")
            },
            st_ctime_ns=info.st_ctime_ns + 1000000,
        )

    monkeypatch.setattr(store_module.os, "fstat", distinct_descriptor_ctime)
    assert add_captured(store, recording, evidence)["source"]["artifact_evidence"] == evidence


def test_v127_file_drift_after_read_refuses_publication_even_when_read_hash_matches(
    imported, monkeypatch
):
    store, _, recording = imported
    evidence, path = preserve(store, recording)
    original_fstat = os.fstat
    calls = 0

    def drift_after_read(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        return original_fstat(descriptor)

    monkeypatch.setattr(store_module.os, "fstat", drift_after_read)
    with pytest.raises(ValueError, match="integrity check"):
        add_captured(store, recording, evidence)
    assert path.read_bytes() == JSON_BYTES
    assert_unpublished(store, recording)


def make_link(path, target, kind):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction acceptance")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(target)], capture_output=True
        )
        if completed.returncode:
            pytest.skip("This Windows account cannot create a temporary junction")
    else:
        try:
            path.symlink_to(target, target_is_directory=target.is_dir())
        except OSError:
            pytest.skip("This account cannot create a temporary symlink")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
@pytest.mark.parametrize("location", ["recordings", "recording", "sources", "file"])
def test_v127_managed_links_are_refused_even_inside_project(imported, kind, location):
    store, project, recording = imported
    evidence = receipt()
    base = store.project_folder(project["id"])
    folder = base / "recordings" / recording["id"]
    candidates = {
        "recordings": base / "recordings",
        "recording": folder,
        "sources": folder / "sources",
        "file": folder / "sources" / (evidence["source_id"] + ".json"),
    }
    link = candidates[location]
    link.parent.mkdir(parents=True, exist_ok=True)
    target = base / "unrelated-owned-folder"
    target.mkdir()
    if location == "file" and kind == "symlink":
        target = target / "keep.json"
        target.write_bytes(JSON_BYTES)
    make_link(link, target, kind)
    with pytest.raises(ValueError, match="filesystem links"):
        add_captured(store, recording, evidence)
    assert_unpublished(store, recording)
    if target.is_file():
        assert target.read_bytes() == JSON_BYTES


@pytest.mark.parametrize("operation", ["import", "add_result"])
def test_v127_direct_calls_take_snapshot_lock_before_paths(imported, monkeypatch, operation):
    store, _, recording = imported
    evidence, _ = preserve(store, recording)
    entered, checked = threading.Event(), threading.Event()
    original = store._import_source_path

    def checked_path(*args, **kwargs):
        checked.set()
        return original(*args, **kwargs)

    def run():
        entered.set()
        if operation == "import":
            return store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 0)
        return add_captured(store, recording, evidence)

    monkeypatch.setattr(store, "_import_source_path", checked_path)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.snapshot_lock:
            future = pool.submit(run)
            assert entered.wait(5)
            assert not checked.wait(0.1)
            assert not future.done()
        assert future.result(timeout=10)["source"]["artifact_evidence"]


def test_v127_relocation_waits_until_durable_import_commit(imported, tmp_path, monkeypatch):
    store, project, recording = imported
    persisted, release, moved = threading.Event(), threading.Event(), threading.Event()
    original_fsync = os.fsync

    def blocked_fsync(descriptor):
        original_fsync(descriptor)
        persisted.set()
        assert release.wait(10)

    def relocate():
        with store.snapshot_lock:
            moved.set()
            return relocate_project(store, project["id"], tmp_path / "moved")

    monkeypatch.setattr(store_module.os, "fsync", blocked_fsync)
    with ThreadPoolExecutor(max_workers=2) as pool:
        importing = pool.submit(store.import_transcript, recording["id"], JSON_BYTES, "raw.json", 0)
        assert persisted.wait(5)
        relocating = pool.submit(relocate)
        try:
            assert not moved.wait(0.1)
        finally:
            release.set()
        result = importing.result(timeout=10)
        assert relocating.result(timeout=10)["path"]
    evidence = result["source"]["artifact_evidence"]
    assert (
        store.directory(recording["id"]) / "sources" / (evidence["source_id"] + ".json")
    ).read_bytes() == JSON_BYTES


def test_v127_receipt_survives_edits_historical_restore_and_trash(imported):
    store, _, recording = imported
    result = store.import_transcript(recording["id"], JSON_BYTES, "raw.json", 0)
    evidence = deepcopy(result["source"]["artifact_evidence"])
    first = store.result_versions(recording["id"], result["id"])[0]
    segments = deepcopy(result["segments"])
    segments[0]["text"] = "Reviewed wording"
    store.save_transcript(recording["id"], segments, {}, 1, result_id=result["id"])
    historical = store.result_version(recording["id"], result["id"], first["id"])
    store.save_transcript(
        recording["id"],
        historical["payload"]["segments"],
        {},
        2,
        action="version_restore",
        context={"restored_from_version_id": first["id"]},
        result_id=result["id"],
    )
    store.set_result_trashed(recording["id"], result["id"], 3, trashed=True)
    store.set_result_trashed(recording["id"], result["id"], 4, trashed=False)
    versions = store.result_versions(recording["id"], result["id"])
    assert len(versions) == 5
    assert all(version["context"]["artifact_evidence"] == evidence for version in versions)
    evidence["sha256"] = "f" * 64
    assert store.result(recording["id"], result["id"])["source"]["artifact_evidence"] != evidence
    assert (
        encode_payload(store.recording(recording["id"])["segments"], {})[0]
        == encode_payload(result["segments"], {})[0]
    )


def test_v127_api_import_retains_busy_replace_and_revision_guards(client):
    store = client.app.state.store
    project = store.create_project("API study")
    recording = store.create_recording(project["id"], "interview.wav")
    url = f"/api/recordings/{recording['id']}/transcript"

    def upload(revision, replace=False):
        return client.post(
            url,
            data={"revision": revision, "replace": str(replace).lower()},
            files={"file": ("raw.v2.json", JSON_BYTES, "application/json")},
        )

    job = store.add_job(recording["id"], "media")
    assert upload(0).status_code == 409
    store.update_job(job["id"], state="completed")
    first = upload(0)
    assert first.status_code == 200, first.text
    assert first.json()["transcripts"][0]["source"]["artifact_evidence"]["bytes"] == len(JSON_BYTES)
    assert upload(1).status_code == 409
    assert upload(0, True).status_code == 409
    assert len(list((store.directory(recording["id"]) / "sources").iterdir())) == 1
    assert upload(1, True).status_code == 200
    assert len(store.results(recording["id"])) == 2
