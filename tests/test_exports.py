import csv
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from cbs_transcriber.exports import project_archive
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import (
    edit_segment,
    export_filename,
    export_transcript,
    normalize_segments,
)


def passages():
    return normalize_segments(
        [
            {"text": "Hej <CBS> & København.", "start": 0.9996, "end": 2.5, "speaker": "A"},
            {"text": "Sidste passage.", "start": 3, "end": 4, "speaker": "B"},
        ]
    )


@pytest.mark.parametrize("format,clock", [("srt", "00:00:01,000"), ("vtt", "00:00:01.000")])
def test_subtitles_preserve_first_last_unicode_and_escape_markup(format, clock):
    text = export_transcript(passages(), {"A": "Anna", "B": "Bo"}, export_format=format)
    assert clock + " --> " in text
    assert "Hej &lt;CBS&gt; &amp; København." in text
    assert "Sidste passage." in text
    assert text.startswith("WEBVTT\n\n1\n" if format == "vtt" else "1\n")
    assert ("<v Anna>" if format == "vtt" else "Anna:") in text


@pytest.mark.parametrize("interval", [{}, {"start": 1}, {"start": 1, "end": 1.0001}])
@pytest.mark.parametrize("format", ["srt", "vtt"])
def test_subtitles_reject_missing_and_zero_millisecond_timing(interval, format):
    segments = normalize_segments([{"text": "No invented timing.", **interval}])
    with pytest.raises(ValueError):
        export_transcript(segments, export_format=format)


def test_json_exports_current_text_without_stale_original_words_or_settings():
    segments = normalize_segments(
        [
            {
                "start": 0,
                "end": 1,
                "text": "Old secret.",
                "words": [
                    {"word": "Old", "start": 0, "end": 0.5},
                    {"word": "secret", "start": 0.5, "end": 1},
                ],
            }
        ]
    )
    segments[0] = edit_segment(segments[0], "free", text="Reviewed æøå.")
    data = export_transcript(segments, export_format="json")
    assert "Old" not in data and "original_" not in data
    parsed = json.loads(data)
    assert parsed["segments"][0]["text"] == "Reviewed æøå."
    assert parsed["segments"][0]["words"] == []
    assert set(parsed) == {"format", "version", "segments", "speakers"}


def test_csv_quotes_multiline_unicode_and_neutralizes_formulas():
    segments = normalize_segments([{"text": ' =SUM(1,2)\n"æøå"', "speaker": "A"}])
    text = export_transcript(segments, {"A": "+danger"}, export_format="csv")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows == [
        ["start", "end", "speaker", "text"],
        ["", "", "'+danger", '\' =SUM(1,2)\n"æøå"'],
    ]
    assert export_filename("many.dots.wav", "2026-10-21", "srt").endswith("many.dots.srt")
    assert "/" not in export_filename("../../bad:name.wav", export_format="json")
    assert ":" not in export_filename("../../bad:name.wav", export_format="json")


def test_project_zip_is_scoped_unique_reports_skips_and_never_changes_results(client):
    store = client.app.state.store
    project = store.create_project("Research")
    other = store.create_project("Another project")
    one = store.create_recording(project["id"], "same.name.wav")
    two = store.create_recording(project["id"], "same.name.wav")
    store.create_recording(project["id"], "empty.wav")
    excluded = store.create_recording(other["id"], "exclude.wav")
    first = store.add_result(one["id"], passages(), {}, "Reviewed", {})
    store.add_result(one["id"], normalize_segments([{"text": "Untimed alternate."}]), {}, "Alt", {})
    store.add_result(two["id"], passages(), {}, "Other reviewed", {})
    store.add_result(
        excluded["id"], normalize_segments([{"text": "Do not export."}]), {}, "Other", {}
    )
    before = store.recording(one["id"])
    response = client.get(f"/api/projects/{project['id']}/export?format=srt&results=all")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert all(
            not name.startswith(("/", "\\")) and ".." not in name.split("/") for name in names
        )
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["exported"] == 2 and manifest["skipped"] == 2
        assert {e["filename"] for e in manifest["entries"]} == {"same.name.wav", "empty.wav"}
        assert all(e["recording_id"] != excluded["id"] for e in manifest["entries"])
        assert "SKIPPED" in archive.read("EXPORT REPORT.txt").decode()
    response = client.get(f"/api/projects/{project['id']}/export?format=json&results=active")
    with ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["exported"] == 2 and manifest["skipped"] == 1
        assert any(e["result_id"] == first["id"] for e in manifest["entries"])
        assert not any(e["label"] == "Alt" for e in manifest["entries"])
    assert store.recording(one["id"]) == before
    assert (
        client.get(f"/api/recordings/{two['id']}/export?result_id={first['id']}").status_code == 404
    )


def test_empty_or_untimed_project_has_clear_error_and_csv_json_work(client):
    store = client.app.state.store
    project = store.create_project("Untimed")
    recording = store.create_recording(project["id"], "text.wav")
    store.add_result(recording["id"], normalize_segments([{"text": "æøå."}]), {}, "Untimed", {})
    response = client.get(f"/api/projects/{project['id']}/export?format=vtt&results=active")
    assert response.status_code == 400
    assert "Readable text" in response.json()["detail"]
    assert client.get(f"/api/projects/{project['id']}/export?results=invalid").status_code == 422
    for format, content_type in (("csv", "text/csv"), ("json", "application/json")):
        response = client.get(f"/api/recordings/{recording['id']}/export?format={format}")
        assert response.status_code == 200
        assert content_type in response.headers["content-type"]
        assert "æøå." in response.content.decode("utf-8-sig")


def test_project_metadata_and_contents_share_one_read_snapshot(tmp_path, monkeypatch):
    store = Store(tmp_path)
    project = store.create_project("Before")

    def add(filename):
        recording = store.create_recording(project["id"], filename)
        store.add_result(recording["id"], passages(), {}, "R", {})

    add("one.wav")
    connection, changed = store.connection, []

    class Observed:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, parameters=()):
            cursor = self.db.execute(sql, parameters)
            if sql == "SELECT name FROM projects WHERE id=?" and not changed:
                row = cursor.fetchone()
                changed.append(True)
                # A real WAL writer commits after the export observes the project name.
                store.execute("UPDATE projects SET name='After' WHERE id=?", (project["id"],))
                add("two.wav")
                return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [row] if row else [])
            return cursor

        def __getattr__(self, name):
            return getattr(self.db, name)

    @contextmanager
    def observed_connection():
        with connection() as db:
            yield Observed(db)

    monkeypatch.setattr(store, "connection", observed_connection)
    with project_archive(store, project["id"], "json", "speaker", "active") as data:
        with ZipFile(data) as archive:
            manifest = json.loads(archive.read("manifest.json"))
    assert changed
    assert manifest["project"] == "Before"
    assert [entry["filename"] for entry in manifest["entries"]] == ["one.wav"]
    assert store.one("SELECT name FROM projects WHERE id=?", (project["id"],))["name"] == "After"
    assert len(store.all("SELECT id FROM recordings")) == 2


@pytest.mark.parametrize(
    "format", ["text", "nvivo-windows", "nvivo-mac", "srt", "vtt", "csv", "json", "docx"]
)
def test_unicode_export_basenames_fit_portable_component_limits(format):
    # This original is a valid 244-unit Windows filename; the download adds a date/suffix.
    original = "🙂" * 120 + ".wav"
    filename = export_filename(original, "2026-10-30", format)
    assert len(filename.encode("utf-8")) <= 255
    assert len(filename.encode("utf-16-le")) // 2 <= 255
    assert filename.startswith("2026-10-30 - 🙂")
    assert filename.endswith(
        ".txt" if format in {"text", "nvivo-windows", "nvivo-mac"} else "." + format
    )


@pytest.mark.parametrize("format", ["nvivo-windows", "nvivo-mac", "srt", "vtt"])
def test_large_finite_timestamp_refuses_single_export_and_skips_only_that_zip_result(
    client, format
):
    store = client.app.state.store
    project = store.create_project("Finite timestamp")
    unusual = store.create_recording(project["id"], "large.wav")
    large = normalize_segments([{"text": "Large finite bound.", "start": 1e308, "end": 1.1e308}])
    result = store.add_result(unusual["id"], large, {}, "Large", {})
    ordinary = store.create_recording(project["id"], "ordinary.wav")
    store.add_result(ordinary["id"], passages(), {}, "Ordinary", {})
    response = client.get(f"/api/recordings/{unusual['id']}/export", params={"format": format})
    assert response.status_code == 400
    assert "range" in response.json()["detail"]
    response = client.get(
        f"/api/projects/{project['id']}/export", params={"format": format, "results": "active"}
    )
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["exported"] == manifest["skipped"] == 1
        skipped = next(entry for entry in manifest["entries"] if entry["status"] == "skipped")
        assert skipped["recording_id"] == unusual["id"] and "range" in skipped["reason"]
    for supported in ("text", "csv", "json"):
        assert (
            client.get(
                f"/api/recordings/{unusual['id']}/export", params={"format": supported}
            ).status_code
            == 200
        )
    assert store.result(unusual["id"], result["id"])["segments"] == large
