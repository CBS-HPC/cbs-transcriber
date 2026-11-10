"""Original-media exports are explicit, bounded and bound to owned stable file handles."""

import hashlib
import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_export_selection import project_export, unpack
from test_working_manual_policy import mark

from cbs_transcriber import exports
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def originals(client):
    store = client.app.state.store
    project = store.create_project("Synthetic original exports")
    rows, sources = [], {}
    for filename, text in [("_Interview.Name.WAV", "Reviewed synthetic words"), ("Clip.MP4", None)]:
        row = store.create_recording(project["id"], filename)
        directory = store.directory(row["id"])
        directory.mkdir(parents=True)
        source = directory / ("original" + Path(filename).suffix.lower())
        data = (b"synthetic media\0" * 16384) + filename.encode()
        source.write_bytes(data)
        sources[row["id"]] = (source, data)
        if text:
            store.add_result(
                row["id"],
                normalize_segments([{"text": text, "start": 1, "end": 2}]),
                {},
                "Reviewed",
                {},
                auto_first=True,
            )
        rows.append(store.recording(row["id"]))
    return store, project, rows, sources


def test_originals_are_off_by_default_and_legacy_get_never_adds_them(client, originals):
    _, project, rows, _ = originals
    for response in (
        project_export(client, project, rows[:1], ["json"]),
        client.get(f"/api/projects/{project['id']}/export", params={"format": "json"}),
    ):
        _, report = unpack(response)
        assert report["include_originals"] is False
        assert all(entry.get("kind") != "source_media" for entry in report["entries"])
    response = client.post(
        f"/api/projects/{project['id']}/exports",
        json={"recording_ids": [rows[0]["id"]], "formats": [], "include_originals": False},
    )
    assert response.status_code == 422
    for invalid in (1, "true", None):
        assert (
            client.post(
                f"/api/projects/{project['id']}/exports",
                json={
                    "recording_ids": [rows[0]["id"]],
                    "formats": [],
                    "include_originals": invalid,
                },
            ).status_code
            == 422
        )


def test_originals_only_include_selected_untranscribed_files_with_exact_bytes_and_names(
    client, originals
):
    _, project, rows, sources = originals
    response = client.post(
        f"/api/projects/{project['id']}/exports",
        json={
            "recording_ids": [row["id"] for row in rows],
            "formats": [],
            "include_originals": True,
        },
    )
    contents, report = unpack(response)
    assert report["include_originals"] is True and report["formats"] == []
    assert report["exported"] == 2 and report["skipped"] == 0
    assert len(contents) == 4
    for entry in report["entries"]:
        assert entry["kind"] == "source_media" and entry["format"] == "original"
        assert entry["result_id"] is None
        assert "/original/" in entry["path"] and entry["path"].endswith(entry["filename"])
        data = sources[entry["recording_id"]][1]
        assert contents[entry["path"]] == data
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
    assert b"Original recordings are not included" not in contents["EXPORT REPORT.txt"]


@pytest.mark.parametrize("omit_result", [False, True])
def test_current_record_originals_only_allows_null_or_omitted_result(
    client, originals, omit_result
):
    _, _, rows, sources = originals
    row = rows[1]
    body = {"formats": [], "include_originals": True}
    if not omit_result:
        body["result_id"] = None
    contents, report = unpack(client.post(f"/api/recordings/{row['id']}/exports", json=body))
    assert report["results"] == "selected" and report["exported"] == 1
    assert contents[report["entries"][0]["path"]] == sources[row["id"]][1]


def test_originals_are_once_per_recording_beside_all_result_folders(client, originals):
    store, project, rows, sources = originals
    row = rows[0]
    store.add_result(row["id"], normalize_segments([{"text": "Alternative"}]), {}, "original", {})
    contents, report = unpack(
        project_export(
            client,
            project,
            rows[:1],
            ["json", "text"],
            results="all",
            include_originals=True,
        )
    )
    media = [entry for entry in report["entries"] if entry.get("kind") == "source_media"]
    transcripts = [entry for entry in report["entries"] if entry.get("kind") != "source_media"]
    assert len(media) == 1 and len(transcripts) == 4
    assert contents[media[0]["path"]] == sources[row["id"]][1]
    assert all("/original/" not in entry["path"] for entry in transcripts)
    assert any("/original (2)/" in entry["path"] for entry in transcripts)


@pytest.mark.parametrize("mode", ["working", "active", "all"])
def test_missing_original_keeps_valid_transcripts_and_original_only_skip_report(
    client, originals, mode
):
    _, project, rows, sources = originals
    sources[rows[0]["id"]][0].unlink()
    contents, report = unpack(
        project_export(
            client,
            project,
            rows[:1],
            ["json"],
            results=mode,
            include_originals=True,
        )
    )
    assert report["exported"] == report["skipped"] == 1
    media = next(entry for entry in report["entries"] if entry.get("kind") == "source_media")
    assert media["status"] == "skipped" and "missing" in media["reason"].lower()
    assert len(contents) == 3
    _, report = unpack(
        client.post(
            f"/api/projects/{project['id']}/exports",
            json={
                "recording_ids": [rows[0]["id"]],
                "formats": [],
                "results": mode,
                "include_originals": True,
            },
        )
    )
    assert report["exported"] == 0 and report["skipped"] == 1


def test_no_transcript_can_export_original_and_explicit_transcript_skip(client, originals):
    _, _, rows, _ = originals
    contents, report = unpack(
        client.post(
            f"/api/recordings/{rows[1]['id']}/exports",
            json={"result_id": None, "formats": ["json"], "include_originals": True},
        )
    )
    assert report["exported"] == report["skipped"] == 1
    assert len(contents) == 3
    assert any(
        entry["status"] == "skipped" and "No saved transcript" in entry["reason"]
        for entry in report["entries"]
    )


def test_foreign_and_stale_selections_fail_before_any_original_path_is_opened(
    client, originals, monkeypatch
):
    store, project, rows, _ = originals
    foreign_project = store.create_project("Foreign original")
    foreign = store.create_recording(foreign_project["id"], "private.wav")

    def refuse(*args):
        pytest.fail("Invalid selections must fail before touching any original")

    monkeypatch.setattr(exports, "_stage_original", refuse)
    for bad_id in (foreign["id"], "deleted-recording"):
        assert (
            project_export(
                client, project, [rows[0], {"id": bad_id}], include_originals=True
            ).status_code
            == 404
        )
    response = client.post(
        f"/api/recordings/{rows[0]['id']}/exports",
        json={"result_id": "foreign-result", "formats": [], "include_originals": True},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("link", ["source", "directory", "hardlink"])
def test_original_links_are_skipped_without_exporting_external_bytes(
    client, originals, tmp_path, link
):
    _, project, rows, sources = originals
    source = sources[rows[0]["id"]][0]
    outside = tmp_path / "external-media.wav"
    outside.write_bytes(b"external synthetic bytes must never be exported")
    try:
        if link == "source":
            source.unlink()
            source.symlink_to(outside)
        elif link == "directory":
            directory = source.parent
            moved = directory.with_name("moved-owned-directory")
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
        else:
            os.link(source, tmp_path / "linked-original.wav")
    except OSError as error:
        pytest.skip(f"Synthetic link setup unavailable: {error.__class__.__name__}")
    contents, report = unpack(project_export(client, project, rows[:1], include_originals=True))
    media = next(entry for entry in report["entries"] if entry.get("kind") == "source_media")
    assert media["status"] == "skipped"
    assert all("/original/" not in name for name in contents)
    assert all(b"external synthetic bytes" not in data for data in contents.values())


@pytest.mark.parametrize("change", ["grow", "move_metadata", "delete_metadata", "none"])
def test_bounded_media_reads_and_zip_writes_hold_no_shared_locks_and_detect_races(
    client, originals, monkeypatch, change
):
    store, project, rows, sources = originals
    row = rows[0]
    source, _ = sources[row["id"]]
    data = b"synthetic bounded source\0" * 100_000
    source.write_bytes(data)
    source_reads, zip_writes, changed = [], [], []
    fdopen, zip_open = exports.os.fdopen, ZipFile.open

    def unlocked():
        assert not store.snapshot_lock._is_owned()
        assert not client.app.state.queue.lock._is_owned()

    class Source:
        def __init__(self, incoming):
            self.incoming = incoming

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.incoming.close()

        def fileno(self):
            return self.incoming.fileno()

        def read(self, size):
            unlocked()
            assert 0 < size <= 1024 * 1024
            source_reads.append(size)
            block = self.incoming.read(size)
            if not changed:
                changed.append(True)
                if change == "grow":
                    with source.open("ab") as output:
                        output.write(b"changed")
                elif change == "move_metadata":
                    store.execute(
                        "UPDATE projects SET storage_dir=? WHERE id=?",
                        (str(source.parent / "new-layout"), project["id"]),
                    )
                elif change == "delete_metadata":
                    store.execute("DELETE FROM recordings WHERE id=?", (row["id"],))
            return block

    class Output:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.output.close()

        def write(self, block):
            unlocked()
            assert len(block) <= 1024 * 1024
            zip_writes.append(len(block))
            return self.output.write(block)

    def monitored_zip_open(bundle, name, mode="r", *args, **kwargs):
        output = zip_open(bundle, name, mode, *args, **kwargs)
        if mode == "w" and isinstance(name, str) and "/original/" in name:
            return Output(output)
        return output

    monkeypatch.setattr(
        exports.os, "fdopen", lambda *args, **kwargs: Source(fdopen(*args, **kwargs))
    )
    monkeypatch.setattr(ZipFile, "open", monitored_zip_open)
    contents, report = unpack(project_export(client, project, rows[:1], include_originals=True))
    media = next(entry for entry in report["entries"] if entry.get("kind") == "source_media")
    assert len(source_reads) > 1
    if change == "none":
        assert media["status"] == "exported" and contents[media["path"]] == data
        assert zip_writes
    else:
        assert media["status"] == "skipped" and not zip_writes
        assert all("/original/" not in name for name in contents)
        assert any(
            entry["format"] == "json" and entry["status"] == "exported"
            for entry in report["entries"]
        )


def test_original_replacement_during_open_is_rejected_before_reading(
    client, originals, monkeypatch, tmp_path
):
    _, project, rows, sources = originals
    source = sources[rows[0]["id"]][0]
    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(b"replacement data")
    original_open = exports.os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if Path(path) == source:
            os.replace(replacement, source)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(exports.os, "open", replace_before_open)
    contents, report = unpack(project_export(client, project, rows[:1], include_originals=True))
    media = next(entry for entry in report["entries"] if entry.get("kind") == "source_media")
    assert media["status"] == "skipped"
    assert all("/original/" not in name for name in contents)


def test_export_eligibility_is_boolean_live_and_uses_exact_nonempty_working_pin(client, originals):
    store, project, rows, _ = originals
    pinned = rows[0]["active_version"]["id"]
    current = store.save_transcript(rows[0]["id"], [], {}, rows[0]["revision"])
    mark(store, rows[0]["id"], pinned)
    unassigned = store.create_recording(project["id"], "unassigned.wav")
    store.add_result(unassigned["id"], normalize_segments([{"text": "Unassigned"}]), {}, "R", {})
    trashed = store.create_recording(project["id"], "trashed.wav")
    removed = store.add_result(
        trashed["id"], normalize_segments([{"text": "Removed"}]), {}, "R", {}
    )
    with store.connection() as db:
        db.execute("INSERT INTO result_trash VALUES (?,?)", (removed["id"], "synthetic"))
    response = client.get(f"/api/projects/{project['id']}/recordings")
    assert response.status_code == 200, response.text
    flags = {row["id"]: row["export_eligible"] for row in response.json()}
    assert flags[rows[0]["id"]] == {"working": True, "active": False, "all": False}
    assert flags[rows[1]["id"]] == {"working": False, "active": False, "all": False}
    assert flags[unassigned["id"]] == {"working": False, "active": True, "all": True}
    assert flags[trashed["id"]] == {"working": False, "active": False, "all": False}
    assert all(type(flag) is bool for modes in flags.values() for flag in modes.values())
    mark(store, rows[0]["id"], current["active_version"]["id"])
    flags = {
        row["id"]: row["export_eligible"]
        for row in client.get(f"/api/projects/{project['id']}/recordings").json()
    }
    assert flags[rows[0]["id"]] == {"working": False, "active": False, "all": False}


def test_truncation_cannot_create_reserved_windows_folder_or_original_filename():
    name = "CON" + " " * 178 + "X"
    folder = exports._folder(name, set())
    filename = exports._original_filename(name + ".wav")
    assert folder == "_CON" and filename == "_CON.wav"
    if os.name == "nt":
        assert not os.path.isreserved(folder) and not os.path.isreserved(filename)
