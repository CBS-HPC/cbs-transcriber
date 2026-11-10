import csv
import io

import pytest

from cbs_transcriber.transcripts import export_filename, export_nvivo, normalize_segments


def passages():
    return normalize_segments(
        [
            {
                "start": 1.25,
                "end": 4.04,
                "speaker": "A",
                "text": 'Hej\tKøbenhavn.\nHan sagde "ja".',
            },
            {"start": 4.04, "end": 5.97, "speaker": "B", "text": "Sidste passage med æøå."},
        ]
    )


def test_v23_windows_export_is_a_rectangular_timed_transcript(client):
    store = client.app.state.store
    project = store.create_project("NVivo export")
    recording = store.create_recording(project["id"], "interview.v2.wav")
    original = store.add_result(recording["id"], passages(), {"A": "Anne Øster"}, "First", {})
    edited = passages()
    edited[0]["text"] = "Reviewed København."
    selected = store.add_result(recording["id"], edited, {"A": "Øyvind"}, "Reviewed", {})
    response = client.get(
        f"/api/recordings/{recording['id']}/export",
        params={"format": "nvivo-windows", "result_id": selected["id"]},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    assert rows[0] == ["Timespan", "Speaker", "Content"]
    assert rows[1] == ["00:00:01.3-00:00:04.0", "Øyvind", "Reviewed København."]
    # Unnamed raw labels export with the shared default name (B -> Speaker_02).
    assert rows[2] == ["00:00:04.0-00:00:06.0", "Speaker_02", "Sidste passage med æøå."]
    assert all(line.count("\t") == 2 for line in text.splitlines())
    assert "interview.v2.nvivo-windows.txt" in response.headers["content-disposition"]
    assert store.recording(recording["id"])["active_transcript_id"] == original["id"]
    assert store.result(recording["id"], original["id"])["segments"][0]["text"].startswith("Hej")


def test_v23_windows_export_flattens_cell_whitespace_without_changing_saved_text():
    segments = passages()
    text = export_nvivo(segments, {"A": "Anne\tØster"}, export_format="nvivo-windows")
    assert text.splitlines()[1] == (
        '00:00:01.3-00:00:04.0\tAnne Øster\tHej København. Han sagde "ja".'
    )
    assert "\n" in segments[0]["text"] and "\t" in segments[0]["text"]


def test_v23_mac_export_uses_documented_speaker_and_f5_timestamp_paragraphs():
    text = export_nvivo(passages(), {"A": "Anne Øster"}, export_format="nvivo-mac")
    assert text == (
        '#00:00:01-3#\nAnne Øster: Hej København. Han sagde "ja". #00:00:04-0#\n\n'
        "#00:00:04-0#\nSpeaker_02: Sidste passage med æøå. #00:00:06-0#\n"
    )
    assert "INTERVIEW" not in text and "\t" not in text


@pytest.mark.parametrize("export_format", ["nvivo-windows", "nvivo-mac"])
def test_v23_missing_timing_is_explicit_and_no_passage_is_dropped(client, export_format):
    store = client.app.state.store
    project = store.create_project("Untimed")
    recording = store.create_recording(project["id"], "untimed.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"start": 0, "text": "Timed."}, {"text": "Untimed æøå."}]),
        {},
        "Mixed",
        {},
    )
    response = client.get(
        f"/api/recordings/{recording['id']}/export", params={"format": export_format}
    )
    assert response.status_code == 400
    assert "start time for every passage" in response.json()["detail"]
    readable = client.get(f"/api/recordings/{recording['id']}/export", params={"format": "text"})
    assert "Timed." in readable.text and "Untimed æøå." in readable.text


@pytest.mark.parametrize("export_format", ["nvivo-windows", "nvivo-mac"])
def test_v23_start_only_timing_and_multi_dot_filename(export_format):
    segments = normalize_segments([{"start": 3599.99, "text": "Start only.", "speaker": "A"}])
    text = export_nvivo(segments, export_format=export_format)
    assert ("01:00:00.0" if export_format == "nvivo-windows" else "#01:00:00-0#") in text
    assert export_filename("interview.v2.da.m4a", "2026-10-20", export_format) == (
        f"2026-10-20 - interview.v2.da.{export_format}.txt"
    )
