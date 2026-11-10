"""A03 synthetic parser failures and exact captured-source correspondence."""

import csv
import hashlib
import io
import json
import sqlite3

import pytest

from cbs_transcriber import transcripts
from cbs_transcriber.history import encode_payload


@pytest.mark.parametrize("location", ["segment", "word", "token"])
def test_unrepresentable_optional_confidence_does_not_break_import(client, location):
    store = client.app.state.store
    project = store.create_project("Synthetic confidence")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segment = {"text": "Hej", "start": 0, "end": 1}
    if location == "segment":
        segment["avg_logprob"] = 10**400
    elif location == "word":
        segment["words"] = [{"word": "Hej", "start": 0, "end": 1, "score": 10**400}]
    else:
        segment["_token_logprobs"] = [{"token": "Hej", "logprob": -(10**400)}]
    raw = json.dumps([segment]).encode()
    response = client.post(
        f"/api/recordings/{recording['id']}/transcript",
        data={"revision": 0},
        files={"file": ("synthetic.json", raw)},
    )
    assert response.status_code == 200, response.text
    result = store.results(recording["id"])[0]
    evidence = result["source"]["artifact_evidence"]
    assert evidence["sha256"] == hashlib.sha256(raw).hexdigest()
    assert store.result_hints(recording["id"], result["id"]) is None
    assert response.json()["segments"][0]["text"] == "Hej"


def test_top_level_words_do_not_rescan_all_timestamps_for_every_passage(monkeypatch):
    count = 120
    payload = {
        "segments": [{"text": "Word", "start": i, "end": i + 1} for i in range(count)],
        "words": [{"word": "Word", "start": i, "end": i + 1} for i in range(count)],
    }
    calls = 0
    timestamp = transcripts.timestamp

    def observed(value):
        nonlocal calls
        calls += 1
        return timestamp(value)

    monkeypatch.setattr(transcripts, "timestamp", observed)
    parsed = transcripts.parse_transcript(json.dumps(payload).encode(), "synthetic.json")
    assert len(parsed) == count and all(segment["word_timing"] for segment in parsed)
    assert calls <= count * 20


def test_top_level_word_index_keeps_source_order_and_half_open_midpoint_scope():
    rows = [
        {"start": 0, "end": 1, "text": "Left"},
        {"start": 1, "end": 3, "text": "Later Boundary"},
    ]
    words = [
        {"word": "Later", "start": 2, "end": 3},
        {"word": "Boundary", "start": 0.5, "end": 1.5},
        {"word": "Left", "start": 0, "end": 0.5},
        {"word": "Ignored"},
    ]
    parsed = transcripts.normalize_segments(rows, words)
    assert [word["word"] for word in parsed[0]["words"]] == ["Left"]
    assert [word["word"] for word in parsed[1]["words"]] == ["Later", "Boundary"]
    assert parsed[0]["word_timing"] is True
    assert parsed[1]["word_timing"] is False


@pytest.mark.parametrize("generation", [1, 2])
def test_valid_csv_quotes_multiline_unicode_and_python_word_literals_stay_exact(generation):
    raw = (
        "\ufefftext,end,speaker,start,words\r\n"
        '"Hej, æøå 👩🏽\u200d🔬!\nNæste linje.",2,0,0,'
        "\"[{'word': 'Hej', 'start': 0, 'end': 1}]\"\r\n"
    ).encode("utf-8")
    parsed = transcripts.parse_transcript(raw, "many.dotted.parts.CSV", generation=generation)
    assert len(parsed) == 1
    assert parsed[0]["text"] == "Hej, æøå 👩🏽\u200d🔬!\nNæste linje."
    assert parsed[0]["speaker"] == "0"
    assert parsed[0]["words"][0]["word"] == "Hej"
    assert parsed[0]["word_timing"] is False


@pytest.mark.parametrize("generation", [1, 2])
def test_ignored_overflow_hint_keeps_exact_portable_source_correspondence(tmp_path, generation):
    from test_portable_semantics import build, digest, inspect, manifest, map_body

    path = tmp_path / "synthetic.sqlite3"
    build(path)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["start", "end", "text", "words"])
    writer.writerow(
        [0, 1, "Hej", json.dumps([{"word": "Hej", "start": 0, "end": 1, "score": 10**400}])]
    )
    raw = stream.getvalue().encode()
    parsed = transcripts.parse_transcript(raw, "source.csv", generation=generation)
    transcripts.pop_hints(parsed)
    payload, payload_hash = encode_payload(parsed, {})
    evidence = {
        "schema": generation,
        "role": "transcript_import",
        "source_id": "a" * 32,
        "format": "csv",
        "sha256": digest(raw),
        "bytes": len(raw),
    }
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE result_versions SET context=?,payload=?,payload_sha256=?",
            (json.dumps({"artifact_evidence": evidence}), payload, payload_hash),
        )
        db.execute(
            "UPDATE transcript_results SET source=?,segments=?",
            (json.dumps({"kind": "import", "artifact_evidence": evidence}), json.dumps(parsed)),
        )
        db.execute("UPDATE recordings SET segments=?", (json.dumps(parsed),))
    value = manifest(path)
    map_body(value, path, "sources/" + "a" * 32 + ".csv", raw, "import_source")
    assert inspect(path, value)["semantic_counts"]["file_mappings"] == 2
