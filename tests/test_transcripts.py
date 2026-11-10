import csv
import io
import json

import pytest

from cbs_transcriber.transcripts import (
    combine_chunks,
    edit_segment,
    export_filename,
    export_nvivo,
    format_time,
    normalize_segments,
    parse_transcript,
)


@pytest.fixture
def passage():
    return normalize_segments(
        [
            {
                "start": 1,
                "end": 4,
                "speaker": "SPEAKER_00",
                "text": "Hej, København!",
                "words": [
                    {"word": "Hej", "start": 1, "end": 2},
                    {"word": "København", "start": 2, "end": 4},
                ],
            }
        ]
    )[0]


def test_v1_transcriber_csv_column_order_and_first_passage(passage):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=["text", "words", "speaker", "end", "start"])
    writer.writeheader()
    writer.writerow(
        {
            "text": passage["text"],
            "words": repr(passage["words"]),
            "speaker": "SPEAKER_00",
            "start": 1,
            "end": 4,
        }
    )
    parsed = parse_transcript(stream.getvalue().encode("utf-8-sig"), "interview.csv")
    assert parsed[0]["word_timing"]
    assert export_nvivo(parsed) == "INTERVIEW\n\nSpeaker_01\nHej, København!\n"


def test_whisper_csv_without_word_timing():
    parsed = parse_transcript(b"id,seek,start,end,text,tokens\n0,0,0,2,Hello,[]\n", "test.csv")
    assert parsed[0]["speaker"] == "UNKNOWN"
    assert not parsed[0]["word_timing"]


def test_v2_word_edits_keep_offsets_and_restore(passage):
    result = edit_segment(passage, "word", "Goddag", 0)
    assert result["text"] == "Goddag, København!"
    assert result["words"][0]["end"] == 2
    assert result["words"][1]["char_start"] == 8
    assert result["word_timing"]
    rewritten = edit_segment(result, "free", "En helt ny sætning.")
    assert not rewritten["word_timing"]
    assert rewritten["start"] == passage["start"]
    with pytest.raises(ValueError, match="no valid word timing"):
        edit_segment(rewritten, "word", "Ny", 0)
    restored = edit_segment(rewritten, "restore")
    assert restored == passage


@pytest.mark.parametrize("text", ["", "two words", "a\nb"])
def test_word_mode_rejects_insertions_and_deletions(passage, text):
    with pytest.raises(ValueError, match="one word"):
        edit_segment(passage, "word", text, 0)


def test_json_top_level_words_and_missing_word_times():
    data = {
        "segments": [{"text": "Hello world.", "start": 0, "end": 2}],
        "words": [{"word": "Hello", "start": 0, "end": 1}, {"word": "world", "start": 1, "end": 2}],
    }
    assert parse_transcript(json.dumps(data).encode(), "test.json")[0]["word_timing"]
    data["segments"][0]["words"] = [{"word": "Hello"}, {"word": "world"}]
    assert not parse_transcript(json.dumps(data).encode(), "test.json")[0]["word_timing"]


@pytest.mark.parametrize("data", [b"{}", b"[]", b"null", b'[{"text":"x","start":-1}]'])
def test_invalid_json(data):
    with pytest.raises(ValueError):
        parse_transcript(data, "test.json")


def test_v1_import_never_executes_python(tmp_path):
    target = tmp_path / "must-not-exist"
    payload = f"__import__('pathlib').Path({str(target)!r}).touch()"
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["start", "end", "text", "words"])
    writer.writerow([0, 1, "Hello", payload])
    with pytest.raises(ValueError):
        parse_transcript(stream.getvalue().encode(), "unsafe.csv")
    assert not target.exists()


def test_v7_chunk_offsets_and_distinct_speaker_ids(passage):
    result = combine_chunks(
        [
            {"offset": 0, "segments": [passage]},
            {"offset": 900, "segments": [passage]},
        ]
    )
    assert result[1]["start"] == 901
    assert result[1]["words"][0]["start"] == 901
    assert result[0]["speaker"] != result[1]["speaker"]
    assert passage["start"] == 1


def test_export_filename_and_consecutive_groups(passage):
    assert export_filename("interview.v2.da.csv", "2026-10-20") == (
        "2026-10-20 - interview.v2.da.txt"
    )
    assert export_nvivo([passage, passage], {"SPEAKER_00": "Anne"}).count("Anne") == 1
    assert "00:00:01" in export_nvivo([passage], grouping="timestamp")
    assert format_time(90061) == "25:01:01"


def test_plain_text_json_does_not_fabricate_timing():
    segment = parse_transcript(b'{"text":"Hello"}', "test.json")[0]
    assert segment["start"] is None
    assert not segment["word_timing"]
