"""Every supported rendering uses the exact Working version, including timing gaps."""

import csv
import io
import json
from typing import get_args
from zipfile import ZipFile

import pytest
from test_result_roles import mark

from cbs_transcriber.transcripts import ExportFormat, normalize_segments


@pytest.mark.parametrize("format", get_args(ExportFormat))
def test_all_formats_export_the_exact_working_version_despite_newer_heads(client, format):
    store = client.app.state.store
    project = store.create_project("Unicode Working study")
    recording = store.create_recording(project["id"], "Interview æøå 🙂.wav")
    rid = recording["id"]
    segments = normalize_segments(
        [
            {
                "text": '=SUM(1,2)\nFørste é 👩‍💻 <CBS> & "æøå".',
                "start": 0.9996,
                "end": 2.5,
                "speaker": "A",
            },
            {"text": "Sidste Ω🙂.", "start": 3599.9996, "end": 3601.2345, "speaker": "B"},
        ]
    )
    result = store.add_result(
        rid,
        segments,
        {"A": "Anne Ø", "B": "李🙂"},
        "Working candidate",
        {"synthetic_secret": "DO-NOT-EXPORT-SETUP"},
    )
    version = store.result_versions(rid, result["id"])[0]
    mark(store, rid, version["id"])
    store.save_transcript(
        rid,
        normalize_segments([{"text": "NEWER HEAD", "start": 0, "end": 1}]),
        {},
        store.recording(rid)["revision"],
    )
    # ADR 0133: editing the Working head advances Working. Pin the earlier version again.
    assert store.recording(rid)["result_roles"]["working"]["version_id"] != version["id"]
    mark(store, rid, version["id"])
    alternative = store.add_result(
        rid,
        normalize_segments([{"text": "ACTIVE ALTERNATIVE", "start": 0, "end": 1}]),
        {},
        "Alt",
        {},
    )
    store.activate_result(rid, alternative["id"], store.recording(rid)["revision"])
    missing = store.create_recording(project["id"], "missing.wav")
    unassigned = store.create_recording(project["id"], "unassigned.wav")
    store.add_result(unassigned["id"], segments, {}, "No designation", {})
    other_project = store.create_project("Other scope")
    foreign = store.create_recording(other_project["id"], "foreign.wav")
    store.add_result(foreign["id"], segments, {}, "Foreign", {})
    # Even an unreadable later projection cannot replace or obstruct the pinned payload.
    store.execute(
        "UPDATE transcript_results SET segments='broken head' WHERE id=?", (result["id"],)
    )
    before = store.all("SELECT * FROM result_versions ORDER BY id")
    roles = store.all("SELECT * FROM recording_result_roles ORDER BY recording_id")
    expected = client.get(
        f"/api/recordings/{rid}/transcripts/{result['id']}/versions/{version['id']}/export",
        params={"format": format},
    )
    assert expected.status_code == 200, expected.text
    response = client.get(f"/api/projects/{project['id']}/export", params={"format": format})
    assert response.status_code == 200, response.text
    with ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["results"] == "working"
        assert manifest["exported"] == 1 and manifest["skipped"] == 2
        assert {entry["recording_id"] for entry in manifest["entries"]} == {
            rid,
            missing["id"],
            unassigned["id"],
        }
        entry = next(entry for entry in manifest["entries"] if entry["status"] == "exported")
        assert entry["version_id"] == version["id"]
        assert entry["payload_sha256"] == version["payload_sha256"]
        content = archive.read(entry["path"])
        assert content == expected.content
    if format == "docx":
        with ZipFile(io.BytesIO(content)) as document:
            visible = document.read("word/document.xml").decode()
    else:
        visible = content.decode("utf-8-sig")
    for literal in ("Første é 👩‍💻", "Sidste Ω🙂", "Anne Ø", "李🙂"):
        assert literal in visible
    for excluded in ("NEWER HEAD", "ACTIVE ALTERNATIVE", "DO-NOT-EXPORT-SETUP"):
        assert excluded not in visible
    if format in {"nvivo-windows", "nvivo-mac", "csv"}:
        assert content.startswith(b"\xef\xbb\xbf")
    if format == "csv":
        assert list(csv.reader(io.StringIO(visible)))[1][3].startswith("'=SUM(1,2)")
    elif format in {"srt", "vtt"}:
        assert ("01:00:00,000" if format == "srt" else "01:00:00.000") in visible
    elif format == "json":
        assert json.loads(visible)["segments"][0]["text"] == segments[0]["text"]
    assert store.all("SELECT * FROM result_versions ORDER BY id") == before
    assert store.all("SELECT * FROM recording_result_roles ORDER BY recording_id") == roles


@pytest.mark.parametrize("format", get_args(ExportFormat))
def test_working_timing_gaps_never_fall_back_to_a_complete_newer_head(client, format):
    store = client.app.state.store
    project = store.create_project("Timing gap")
    recording = store.create_recording(project["id"], "gap.wav")
    rid = recording["id"]
    result = store.add_result(
        rid, normalize_segments([{"text": "Untimed æøå 🙂."}]), {}, "Pinned", {}
    )
    version = store.result_versions(rid, result["id"])[0]
    mark(store, rid, version["id"])
    store.save_transcript(
        rid,
        normalize_segments([{"text": "NEW TIMED HEAD", "start": 0, "end": 1}]),
        {},
        store.recording(rid)["revision"],
    )
    # ADR 0133: editing the Working head advances Working. Pin the untimed version again.
    assert store.recording(rid)["result_roles"]["working"]["version_id"] != version["id"]
    mark(store, rid, version["id"])
    response = client.get(f"/api/projects/{project['id']}/export", params={"format": format})
    assert response.status_code == 200, response.text
    with ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entry = manifest["entries"][0]
        if format in {"nvivo-windows", "nvivo-mac", "srt", "vtt"}:
            assert manifest["exported"] == 0 and manifest["skipped"] == 1
            assert entry["status"] == "skipped" and "time" in entry["reason"]
            assert not any(name.startswith("transcripts/") for name in archive.namelist())
        else:
            assert manifest["exported"] == 1 and entry["version_id"] == version["id"]
            content = archive.read(entry["path"])
            if format == "docx":
                with ZipFile(io.BytesIO(content)) as document:
                    content = document.read("word/document.xml")
            assert "Untimed æøå 🙂." in content.decode("utf-8-sig")
            assert "NEW TIMED HEAD" not in content.decode("utf-8-sig")
            if format == "json":
                value = json.loads(content)["segments"][0]
                assert value["start"] is value["end"] is None and value["words"] == []
