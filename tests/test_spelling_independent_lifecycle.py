"""Independent local cache lifecycle checks using tiny synthetic public dictionaries."""

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cbs_transcriber import language_preferences, spelling, spelling_worker
from cbs_transcriber.store import Store


class Worker:
    callback = None

    def reset(self):
        pass

    def close(self):
        pass

    def request(self, entry, folder, command, **values):
        if self.callback is not None:
            self.callback()
        if command == "check":
            return {"status": "ready", "correct": [False] * len(values["words"])}
        return {"status": "ready", "suggestions": ["synthetic"]}


def installed(tmp_path):
    catalog = {
        "schema_version": 1,
        "dictionaries": [copy.deepcopy(next(iter(spelling._catalog().values())))],
    }
    entry = catalog["dictionaries"][0]
    entry.pop("catalog_sha256")
    entry["capabilities"] = {"check": True, "suggest": True, "max_suggestions": 5}
    body = {"aff": b"SET UTF-8\n", "dic": b"1\nsynthetic\n", "license": b"Synthetic licence\n"}
    for kind, raw in body.items():
        entry["files"][kind].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    service = spelling.SpellingService(
        Store(tmp_path / "library"), tmp_path / "cache", catalog=catalog, worker=Worker()
    )
    active = next(iter(service.catalog.values()))
    folder = service._folder(active)
    folder.mkdir(parents=True)
    for kind, name in (
        ("aff", "dictionary.aff"),
        ("dic", "dictionary.dic"),
        ("license", "license.txt"),
    ):
        (folder / name).write_bytes(body[kind])
    (folder / "installed.json").write_text(
        json.dumps(
            {"format": "cbs-spelling-dictionary-v1", "catalog_sha256": active["catalog_sha256"]}
        )
    )
    return service, active, folder


def test_dictionary_removal_does_not_follow_a_late_parent_link(tmp_path, monkeypatch):
    service, entry, folder = installed(tmp_path)
    outside = tmp_path / "unrelated"
    outside.mkdir()
    names = ["dictionary.aff", "dictionary.dic", "license.txt", "installed.json"]
    for name in names:
        (outside / name).write_text("Unrelated private sentinel")
    assert outside.resolve().is_relative_to(tmp_path.resolve())
    original, observations, swapped = spelling._plain, [], False
    retained = folder.with_name("retained-original")

    def plain(path, **options):
        nonlocal swapped
        result = original(path, **options)
        observations.append(path)
        if not swapped and len(observations) == 6:
            # All owned files were checked. Change only this synthetic cache entry
            # before removal, with both the retained and unrelated targets in Temp.
            folder.rename(retained)
            if os.name == "nt":
                import _winapi

                _winapi.CreateJunction(str(outside), str(folder))
            else:
                folder.symlink_to(outside, target_is_directory=True)
            swapped = True
        return result

    monkeypatch.setattr(spelling, "_plain", plain)
    try:
        try:
            service.remove(entry["language"], 0)
        except (ValueError, OSError):
            pass
        assert swapped
        assert all((outside / name).read_text() == "Unrelated private sentinel" for name in names)
    finally:
        if folder.is_junction() or folder.is_symlink():
            folder.unlink() if folder.is_symlink() else folder.rmdir()
        service.close()


def test_closed_service_cannot_remove_retained_cache(tmp_path):
    service, entry, folder = installed(tmp_path)
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    service.close()
    with pytest.raises(ValueError, match="closing"):
        service.remove(entry["language"], 0)
    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before


def test_partially_removed_owned_dictionary_can_be_retried(tmp_path, monkeypatch):
    service, entry, folder = installed(tmp_path)
    original, refused = Path.unlink, False

    def locked(path, *args, **kwargs):
        nonlocal refused
        if path.name == "dictionary.dic" and not refused:
            refused = True
            raise PermissionError("Synthetic temporary file lock")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    try:
        with pytest.raises(PermissionError):
            service.remove(entry["language"], 0)
        assert refused and folder.exists()
        assert not (folder / "dictionary.aff").exists()
        assert (folder / "installed.json").exists()
        assert service.remove(entry["language"], 0)["ready"] is False
        assert not folder.exists()
    finally:
        service.close()


def test_failed_reader_startup_does_not_leave_a_live_worker(tmp_path, monkeypatch):
    service, entry, folder = installed(tmp_path)
    worker = spelling._Worker()

    def refuse_thread(*args, **kwargs):
        raise RuntimeError("Synthetic reader startup failure")

    monkeypatch.setattr(spelling.threading.Thread, "start", refuse_thread)
    try:
        try:
            worker.request(entry, folder, "check", words=["synthetic"])
        except RuntimeError:
            pass
        assert worker.process is None or worker.process.poll() is not None
    finally:
        # A failed assertion must not itself leave a real child behind.
        process = worker.process
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            worker.process = worker.reader = worker.responses = worker.key = None
        service.close()


def test_public_dictionary_read_allows_access_time_only_changes(tmp_path, monkeypatch):
    source = tmp_path / "synthetic.dic"
    raw = b"1\nsynthetic\n"
    source.write_bytes(raw)
    real = Path.lstat
    observations = []

    def accessed(path, *args, **kwargs):
        result = real(path, *args, **kwargs)
        if path == source:
            observations.append(result)
            if len(observations) > 1:
                fields = {
                    name: getattr(result, name) for name in dir(result) if name.startswith("st_")
                }
                fields["st_atime"] += 1
                fields["st_atime_ns"] += 1_000_000_000
                return SimpleNamespace(**fields)
        return result

    monkeypatch.setattr(Path, "lstat", accessed)
    spelling_worker.verify(source, {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})


def recording(service):
    project = service.store.create_project(
        "Synthetic spelling context",
        default_language="da",
        storage_parent=service.store.root / "projects",
    )
    return service.store.create_recording(project["id"], "synthetic.wav")


def test_check_suggest_and_status_never_retrieve_or_persist_draft_words(tmp_path, monkeypatch):
    service, entry, folder = installed(tmp_path)
    row = recording(service)
    service.save_settings(0, True, False)
    service.save_vocabulary(0, ["PrivateCustom"], row["project_id"])
    before_files = {path.name: path.read_bytes() for path in folder.iterdir()}
    before_settings = service.store.all("SELECT * FROM settings ORDER BY id")
    before_recording = service.store.one("SELECT * FROM recordings WHERE id=?", (row["id"],))

    def refuse(*args, **kwargs):
        raise AssertionError("No network or dictionary retrieval is allowed here")

    monkeypatch.setattr(spelling, "_download_file", refuse)
    monkeypatch.setattr(spelling.httpx, "Client", refuse)
    try:
        assert service.dictionaries()["dictionaries"][0]["ready"]
        response = service.check(row["id"], "😀 PrivateCustom typo", "synthetic-request")
        assert response["flags"] == [{"word": "typo", "start": 17, "end": 21}]
        assert response["offset_unit"] == "utf16" and response["language"] == "da"
        assert service.suggest(row["id"], "typo", "synthetic-suggestion")["suggestions"] == [
            "synthetic"
        ]
        assert {path.name: path.read_bytes() for path in folder.iterdir()} == before_files
        assert service.store.all("SELECT * FROM settings ORDER BY id") == before_settings
        assert (
            service.store.one("SELECT * FROM recordings WHERE id=?", (row["id"],))
            == before_recording
        )
    finally:
        service.close()


@pytest.mark.parametrize("changed", ["language", "preferences", "vocabulary"])
def test_completed_spelling_result_rechecks_effective_context(tmp_path, changed):
    service, _, _ = installed(tmp_path)
    row = recording(service)
    service.save_settings(0, True, False)

    def mutate():
        if changed == "language":
            language_preferences.save(service.store, "project", row["project_id"], "en", 0)
        elif changed == "preferences":
            service.save_settings(1, False, False)
        else:
            service.save_vocabulary(0, ["LaterPrivateTerm"], row["project_id"])

    service.worker.callback = mutate
    try:
        with pytest.raises(spelling.Conflict, match="changed"):
            service.check(row["id"], "typo", "synthetic-request")
    finally:
        service.close()
