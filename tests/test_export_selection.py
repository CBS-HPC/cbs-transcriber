"""Explicit recording/format selections produce one portable, snapshot-consistent ZIP."""

import io
import json
import os
import re
import unicodedata
from contextlib import contextmanager
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from test_working_manual_policy import mark

from cbs_transcriber.exports import _folder, project_archive, recording_archive
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments

FORMATS = ["text", "nvivo-windows", "nvivo-mac", "srt", "vtt", "csv", "json", "docx"]
SPACED_DEVICES = ["CON .txt", "AUX .wav", "COM1 .interview", "NUL .txt", "CONIN$ .x"]


@pytest.fixture
def library(client):
    store = client.app.state.store
    project = store.create_project("Selected synthetic exports")
    rows = []
    for name, timed in [("Interview med Åse", True), ("Untimed notes", False), ("Excluded", True)]:
        row = store.create_recording(project["id"], "original.wav")
        store.execute("UPDATE recordings SET name=? WHERE id=?", (name, row["id"]))
        segments = normalize_segments(
            [{"text": name + " — æøå", "speaker": "A", **({"start": 1, "end": 3} if timed else {})}]
        )
        store.add_result(
            row["id"], segments, {"A": "Named speaker"}, "Reviewed", {}, auto_first=True
        )
        rows.append(store.recording(row["id"]))
    return store, project, rows


def unpack(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(response.content)) as bundle:
        contents = {name: bundle.read(name) for name in bundle.namelist()}
        assert len(contents) == len(bundle.namelist()), (
            "ZIP members must never overwrite each other"
        )
    return contents, json.loads(contents["manifest.json"])


def project_export(client, project, rows, formats=None, **kwargs):
    return client.post(
        f"/api/projects/{project['id']}/exports",
        json={
            "recording_ids": [row["id"] for row in rows],
            "formats": formats or ["json"],
            **kwargs,
        },
    )


def test_project_subset_eight_formats_share_named_folder_and_never_change_library(client, library):
    store, project, rows = library
    before = [store.recording(row["id"]) for row in rows]
    contents, report = unpack(project_export(client, project, rows[:1], FORMATS))
    assert report["formats"] == FORMATS and report["format"] == "multiple"
    assert report["recording_ids"] == [rows[0]["id"]]
    assert report["results"] == "working"
    assert report["exported"] == 8 and report["skipped"] == 0
    assert {entry["recording_id"] for entry in report["entries"]} == {rows[0]["id"]}
    assert {entry["format"] for entry in report["entries"]} == set(FORMATS)
    assert {entry["path"].split("/")[1] for entry in report["entries"]} == {"Interview med Åse"}
    text_paths = [entry["path"] for entry in report["entries"] if entry["path"].endswith(".txt")]
    assert len(text_paths) == 3 and len(set(text_paths)) == 3
    assert any(path.endswith(".nvivo-windows.txt") for path in text_paths)
    assert any(path.endswith(".nvivo-mac.txt") for path in text_paths)
    for entry in report["entries"]:
        data = contents[entry["path"]]
        if entry["format"] == "docx":
            with ZipFile(io.BytesIO(data)) as document:
                assert b"Named speaker" in document.read("word/document.xml")
        else:
            assert "Åse" in data.decode("utf-8-sig")
        assert entry["version_id"] == rows[0]["active_version"]["id"]
    assert len(contents) == 10  # Eight selected formats and two readable manifests; no media.
    assert [store.recording(row["id"]) for row in rows] == before


def test_mixed_timing_skips_only_incompatible_formats_and_retains_valid_outputs(client, library):
    _, project, rows = library
    contents, report = unpack(project_export(client, project, rows[:2], ["json", "srt", "csv"]))
    assert report["exported"] == 5 and report["skipped"] == 1
    skipped = [entry for entry in report["entries"] if entry["status"] == "skipped"]
    assert len(skipped) == 1
    assert skipped[0]["recording_id"] == rows[1]["id"] and skipped[0]["format"] == "srt"
    assert "tim" in skipped[0]["reason"].lower()
    assert all(
        entry["path"] in contents for entry in report["entries"] if entry["status"] == "exported"
    )
    assert b"SKIPPED" in contents["EXPORT REPORT.txt"]


def test_default_working_exports_immutable_pinned_version_and_reports_missing_designation(
    client, library
):
    store, project, rows = library
    old = rows[0]
    current = store.save_transcript(
        old["id"], normalize_segments([{"text": "Current newer text"}]), {}, old["revision"]
    )
    mark(store, old["id"], old["active_version"]["id"])
    mark(store, rows[1]["id"], None)
    contents, report = unpack(project_export(client, project, rows[:2], ["json", "csv"]))
    assert report["exported"] == 2 and report["skipped"] == 2
    exported = [entry for entry in report["entries"] if entry["status"] == "exported"]
    assert all(entry["version_id"] == old["active_version"]["id"] for entry in exported)
    assert all("Åse" in contents[entry["path"]].decode("utf-8-sig") for entry in exported)
    assert all(
        "Working" in entry["reason"] for entry in report["entries"] if entry["status"] == "skipped"
    )
    active_contents, active = unpack(project_export(client, project, rows[:1], results="active"))
    assert "Current newer text" in active_contents[active["entries"][0]["path"]].decode()
    assert store.recording(old["id"])["active_version"]["id"] == current["active_version"]["id"]


def test_all_saved_results_have_unique_label_subfolders_but_one_recording_folder(client, library):
    store, project, rows = library
    store.add_result(
        rows[0]["id"], normalize_segments([{"text": "Second result"}]), {}, "reviewed", {}
    )
    contents, report = unpack(
        project_export(client, project, rows[:1], ["json", "text"], results="all")
    )
    assert report["exported"] == 4 and report["skipped"] == 0
    paths = [entry["path"] for entry in report["entries"]]
    assert {path.split("/")[1] for path in paths} == {"Interview med Åse"}
    assert {path.split("/")[2].casefold() for path in paths} == {"reviewed", "reviewed (2)"}
    assert len(contents) == 6


@pytest.mark.parametrize(
    "body",
    [
        {"recording_ids": []},
        {"recording_ids": [True]},
        {"formats": []},
        {"formats": ["json", "json"]},
        {"formats": ["not-a-format"]},
        {"grouping": "other"},
        {"results": "other"},
        {"collection": ""},
        {"unexpected": "field"},
    ],
)
def test_project_selection_rejects_invalid_or_empty_explicit_requests(client, library, body):
    _, project, rows = library
    response = client.post(
        f"/api/projects/{project['id']}/exports",
        json={"recording_ids": [rows[0]["id"]], "formats": ["json"], **body},
    )
    assert response.status_code == 422, response.text


def test_recording_selection_cannot_be_omitted_duplicated_foreign_or_stale(client, library):
    store, project, rows = library
    endpoint = f"/api/projects/{project['id']}/exports"
    assert client.post(endpoint, json={"formats": ["json"]}).status_code == 422
    assert (
        client.post(
            endpoint, json={"formats": ["json"], "recording_ids": [rows[0]["id"]] * 2}
        ).status_code
        == 422
    )
    other_project = store.create_project("Foreign")
    foreign = store.create_recording(other_project["id"], "foreign.wav")
    for unknown in (foreign["id"], "deleted-recording"):
        response = client.post(
            endpoint, json={"formats": ["json"], "recording_ids": [rows[0]["id"], unknown]}
        )
        assert response.status_code == 404, response.text
    assert (
        client.post(
            "/api/projects/missing/exports",
            json={"formats": ["json"], "recording_ids": [rows[0]["id"]]},
        ).status_code
        == 404
    )


def test_collection_scope_requires_every_selected_recording_to_remain_in_that_collection(
    client, library
):
    store, project, rows = library
    collection = store.create_collection(project["id"], "Selected collection")
    store.execute(
        "UPDATE recordings SET collection_id=? WHERE id=?", (collection["id"], rows[0]["id"])
    )
    _, report = unpack(project_export(client, project, rows[:1], collection=collection["id"]))
    assert report["collection"] == collection["id"] and report["exported"] == 1
    assert project_export(client, project, rows[:2], collection=collection["id"]).status_code == 404
    assert project_export(client, project, rows[:1], collection="unsorted").status_code == 404
    assert (
        project_export(client, project, rows[:1], collection="foreign-collection").status_code
        == 404
    )


def test_recording_multi_format_export_uses_exact_owned_live_result_and_keeps_legacy_get(
    client, library
):
    store, _, rows = library
    row = rows[0]
    alternate = store.add_result(
        row["id"], normalize_segments([{"text": "Untimed alternative"}]), {}, "Alternative", {}
    )
    before = store.recording(row["id"])
    endpoint = f"/api/recordings/{row['id']}/exports"
    contents, report = unpack(
        client.post(endpoint, json={"result_id": alternate["id"], "formats": ["json", "srt"]})
    )
    assert report["results"] == "selected" and report["exported"] == report["skipped"] == 1
    exported = next(entry for entry in report["entries"] if entry["status"] == "exported")
    assert exported["result_id"] == alternate["id"]
    assert "Untimed alternative" in contents[exported["path"]].decode()
    assert store.recording(row["id"]) == before
    legacy = client.get(
        f"/api/recordings/{row['id']}/export",
        params={"result_id": alternate["id"], "format": "json"},
    )
    assert legacy.status_code == 200 and legacy.content == contents[exported["path"]]
    for result_id in ("missing-result", rows[1]["active_transcript_id"]):
        response = client.post(endpoint, json={"result_id": result_id, "formats": ["json", "text"]})
        assert response.status_code == 404, response.text
    with store.connection() as db:
        db.execute("INSERT INTO result_trash VALUES (?,?)", (alternate["id"], "synthetic"))
    assert (
        client.post(endpoint, json={"result_id": alternate["id"], "formats": ["json"]}).status_code
        == 404
    )
    assert client.post(endpoint, json={"formats": ["json"]}).status_code == 422


def test_named_folders_are_portable_unique_and_safe_for_windows_and_unicode(client):
    store = client.app.state.store
    project = store.create_project("Portable names")
    names = [
        "Interview Åse",
        "Interview Åse",
        "interview åse",
        "é",
        "e\u0301",
        "CON",
        "con.txt",
        "AUX.wav",
        "COM¹",
        "../../escape\\..\\NUL?.txt",
        ".",
        "..",
        "🙂" * 250,
        "A/B",
        "A\\B",
    ]
    rows = []
    for name in names:
        row = store.create_recording(project["id"], "../../unsafe:name.wav")
        store.execute("UPDATE recordings SET name=? WHERE id=?", (name, row["id"]))
        store.add_result(row["id"], normalize_segments([{"text": "Synthetic"}]), {}, "R", {})
        rows.append(row)
    contents, report = unpack(project_export(client, project, rows, results="active"))
    folders = [entry["path"].split("/")[1] for entry in report["entries"]]
    assert folders[:3] == ["Interview Åse", "Interview Åse (2)", "interview åse (3)"]
    assert len({unicodedata.normalize("NFKC", folder).casefold() for folder in folders}) == len(
        names
    )
    for path in contents:
        assert not path.startswith(("/", "\\")) and "\\" not in path
        for part in path.split("/"):
            assert part not in {"", ".", ".."}
            assert not re.search(r'[<>:"/\\|?*\x00-\x1f]', part)
            assert not part.endswith((".", " "))
            assert len(part.encode("utf-8")) <= 255
            assert len(part.encode("utf-16-le")) // 2 <= 255
            reserved = unicodedata.normalize("NFKC", part.split(".", 1)[0]).casefold()
            assert reserved not in {"con", "prn", "aux", "nul"}
            assert not re.fullmatch(r"(?:com|lpt)[1-9]", reserved)


@pytest.mark.parametrize("name", SPACED_DEVICES)
def test_windows_spaced_device_names_are_prefixed_before_export(name):
    safe = _folder(name, set())
    assert safe == "_" + name
    if os.name == "nt":
        assert not os.path.isreserved(safe)


@pytest.mark.parametrize("scope", ["recording", "result"])
def test_windows_spaced_device_names_are_safe_in_recording_and_result_zip_folders(client, scope):
    store = client.app.state.store
    project = store.create_project("Windows reserved export names")
    rows = []
    if scope == "result":
        rows.append(store.create_recording(project["id"], "original.wav"))
    for name in SPACED_DEVICES:
        if scope == "recording":
            row = store.create_recording(project["id"], "original.wav")
            store.execute("UPDATE recordings SET name=? WHERE id=?", (name, row["id"]))
            rows.append(row)
        else:
            row = rows[0]
        store.add_result(
            row["id"],
            normalize_segments([{"text": "Synthetic portable text"}]),
            {},
            name if scope == "result" else "Reviewed",
            {},
        )
    contents, report = unpack(
        project_export(client, project, rows, ["json", "text"], results="all")
    )
    assert report["exported"] == 2 * len(SPACED_DEVICES) and report["skipped"] == 0
    component = 1 if scope == "recording" else 2
    folders = {entry["path"].split("/")[component] for entry in report["entries"]}
    assert folders == {"_" + name for name in SPACED_DEVICES}
    for path in contents:
        if os.name == "nt":
            assert all(not os.path.isreserved(part) for part in path.split("/"))


@pytest.mark.parametrize("scope", ["project", "recording"])
def test_all_formats_contents_names_and_metadata_share_one_read_snapshot(
    tmp_path, monkeypatch, scope
):
    store = Store(tmp_path)
    project = store.create_project("Before project")
    row = store.create_recording(project["id"], "original.wav")
    result = store.add_result(
        row["id"], normalize_segments([{"text": "Before words"}]), {}, "Before label", {}
    )
    store.execute("UPDATE recordings SET name='Before name' WHERE id=?", (row["id"],))
    connection, changed = store.connection, []

    class Observed:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, parameters=()):
            cursor = self.db.execute(sql, parameters)
            if sql == "SELECT name FROM projects WHERE id=?" and not changed:
                observed = cursor.fetchone()
                changed.append(True)
                store.execute(
                    "UPDATE projects SET name='After project' WHERE id=?", (project["id"],)
                )
                store.execute("UPDATE recordings SET name='After name' WHERE id=?", (row["id"],))
                current = store.recording(row["id"])
                store.save_transcript(
                    row["id"],
                    normalize_segments([{"text": "After words"}]),
                    {},
                    current["revision"],
                )
                return SimpleNamespace(fetchone=lambda: observed)
            return cursor

        def __getattr__(self, name):
            return getattr(self.db, name)

    @contextmanager
    def observed_connection():
        with connection() as db:
            yield Observed(db)

    monkeypatch.setattr(store, "connection", observed_connection)
    data = (
        project_archive(
            store,
            project["id"],
            results="active",
            recording_ids=[row["id"]],
            formats=["json", "text"],
        )
        if scope == "project"
        else recording_archive(store, row["id"], result["id"], ["json", "text"])
    )
    with data, ZipFile(data) as bundle:
        report = json.loads(bundle.read("manifest.json"))
        assert report["project"] == "Before project"
        assert all("/Before name/" in entry["path"] for entry in report["entries"])
        for entry in report["entries"]:
            assert "Before words" in bundle.read(entry["path"]).decode()
            assert "After words" not in bundle.read(entry["path"]).decode()
    assert changed and store.recording(row["id"])["segments"][0]["text"] == "After words"


@pytest.mark.parametrize(
    "field,value",
    [
        ("recording_ids", []),
        ("recording_ids", ["same", "same"]),
        ("formats", []),
        ("formats", ["json", "json"]),
    ],
)
def test_direct_archive_call_never_turns_invalid_selection_into_export_all(tmp_path, field, value):
    store = Store(tmp_path)
    project = store.create_project("Invalid explicit selection")
    with pytest.raises(ValueError):
        project_archive(store, project["id"], **{field: value})
