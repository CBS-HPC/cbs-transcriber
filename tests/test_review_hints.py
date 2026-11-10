"""Model review hints: captured beside results, never in text, history or exports."""

import json
import math
from types import SimpleNamespace

import httpx
import pytest

from cbs_transcriber import providers
from cbs_transcriber.enhancement import EnhancementOptions, merge_output
from cbs_transcriber.providers import Settings
from cbs_transcriber.transcripts import (
    normalize_segments,
    pop_hints,
    token_word_confidences,
)


def logprobs(*pieces):
    return [
        {"token": token, "bytes": list(token.encode("utf-8")), "logprob": value}
        for token, value in pieces
    ]


def test_token_probabilities_map_to_words_through_utf8_bytes():
    text = "Hej København"
    tokens = logprobs(("Hej", -0.01), (" K", -0.2), ("ø", -2.3), ("benhavn", -0.05))
    words = token_word_confidences(text, tokens)
    assert [w[:2] for w in words] == [[0, 3], [4, 13]]
    assert words[0][2] == round(math.exp(-0.01), 4)
    assert words[1][2] == round(math.exp(-2.3), 4)  # The least likely token decides.
    # A split multi-byte character still maps exactly.
    split = [
        {"token": "?", "bytes": [0xC3], "logprob": -0.5},
        {"token": "?", "bytes": [0xB8], "logprob": -0.1},
    ]
    assert token_word_confidences("ø", split) == [[0, 1, round(math.exp(-0.5), 4)]]
    assert token_word_confidences(" Hej", logprobs(("Hej", -0.1))) == [[1, 4, 0.9048]]
    assert token_word_confidences("Other text", tokens) == []
    assert token_word_confidences(text, [{"token": "x", "logprob": 0.5}]) == []


def test_untimed_json_response_keeps_word_hints_without_timing():
    payload = {"text": "Hej København", "logprobs": logprobs(("Hej København", -0.7))}
    (segment,) = providers.normalize_response(payload)
    assert segment["start"] is None and not segment["word_timing"]
    assert segment["_hints"]["words"] == [[0, 3, 0.4966], [4, 13, 0.4966]]
    assert segment["_hints"]["word_kind"] == "transcription"


def test_logprobs_are_requested_only_where_openai_supports_them(tmp_path):
    forms = []
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"ID3")

    def handle(request):
        forms.append(request.content)
        return httpx.Response(200, json={"text": "Hej"})

    for model, capability in (("gpt-4o-transcribe", "json"), ("whisper-1", "words")):
        settings = Settings(provider_type="openai", model=model, capability=capability)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            providers.transcribe_file(audio, settings, "sk-test", http_client=client)
    assert b'name="include[]"' in forms[0] and b"logprobs" in forms[0]
    assert b"include" not in forms[1]


def whisperx_rows():
    return [
        {
            "text": "Hej verden.",
            "start": 0,
            "end": 2,
            "avg_logprob": -1.2,
            "no_speech_prob": 0.02,
            "compression_ratio": 1.1,
            "words": [
                {"word": "Hej", "start": 0, "end": 0.5, "score": 0.12},
                {"word": "verden.", "start": 0.5, "end": 2, "score": 0.93},
            ],
        }
    ]


def test_hints_are_stored_beside_the_result_not_in_text_or_history(client):
    store = client.app.state.store
    project = store.create_project("Hints")
    recording = store.create_recording(project["id"], "a.wav")
    result = store.add_result(recording["id"], normalize_segments(whisperx_rows()), {}, "R", {})
    saved = store.result(recording["id"], result["id"])
    assert "_hints" not in json.dumps(saved) and "avg_logprob" not in json.dumps(saved)
    (version,) = store.result_versions(recording["id"], result["id"])
    payload = store.result_version(recording["id"], result["id"], version["id"])["payload"]
    assert "_hints" not in json.dumps(payload)
    hints = client.get(f"/api/recordings/{recording['id']}/transcripts/{result['id']}/hints")
    passage = hints.json()["passages"]["0"]
    assert passage["avg_logprob"] == -1.2 and passage["words"][0] == [0, 3, 0.12]
    assert passage["word_kind"] == "alignment" and len(passage["text_sha256"]) == 64
    export = client.get(
        f"/api/recordings/{recording['id']}/export",
        params={"format": "json", "result_id": result["id"]},
    )
    assert "score" not in export.text and "avg_logprob" not in export.text


def test_cached_hints_load_one_result_and_keep_text_and_owner_checks(tmp_path, monkeypatch):
    from cbs_transcriber import review_hints
    from cbs_transcriber.store import Store

    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic hints")
    recording = store.create_recording(project["id"], "a.wav")
    other = store.create_recording(project["id"], "b.wav")
    result = store.add_result(recording["id"], normalize_segments(whisperx_rows()), {}, "R", {})
    original, calls = store.result, []

    def tracked(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "result", tracked)
    first = review_hints.current_hints(store, recording["id"], result["id"])
    assert len(calls) == 1 and first["passages"]["0"]["avg_logprob"] == -1.2
    current = store.recording(recording["id"])
    current["segments"][0]["text"] = "Changed text"
    store.save_transcript(
        recording["id"], current["segments"], current["speakers"], current["revision"]
    )
    calls.clear()
    changed = review_hints.current_hints(store, recording["id"], result["id"])
    assert len(calls) == 1 and changed["passages"] == {} and changed["edited_passages"] == 1
    with pytest.raises(KeyError):
        review_hints.current_hints(store, other["id"], result["id"])


def test_enhancement_fragments_keep_their_slice_of_alignment_hints():
    source = {
        "id": "source",
        "segments": normalize_segments([{"text": "Hej verden igen", "start": 0, "end": 3}]),
        "speakers": {},
    }
    alignment = {
        "passages": [
            {
                "id": "0",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1, "score": 0.35},
                    {"word": "verden", "start": 1, "end": 2, "score": 0.9},
                    {"word": "igen", "start": 2, "end": 3, "score": 0.4},
                ],
            }
        ]
    }
    turns = {
        "turns": [{"start": 0, "end": 1, "speaker": "A"}, {"start": 1, "end": 3, "speaker": "B"}]
    }
    segments, _ = merge_output(source, alignment, turns, EnhancementOptions(diarize=True), 3, "run")
    hints = pop_hints(segments)
    assert [s["text"] for s in segments] == ["Hej ", "verden igen"]
    assert hints["0"]["words"] == [[0, 3, 0.35]]
    assert hints["1"]["words"] == [[0, 6, 0.9], [7, 11, 0.4]]


def native_result(client, payload, text_override=None):
    """A result produced by a native UCloud job before hints were stored."""
    store = client.app.state.store
    project = store.create_project("Backfill")
    recording = store.create_recording(project["id"], "a.wav")
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"settings": {"provider_type": "ucloud"}, "provider_name": "U"},
    )
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ("ucloud-" + job["id"] + ".json")).write_text(json.dumps(payload), "utf-8")
    segments = normalize_segments(payload["segments"])
    if text_override:
        segments[0]["text"] = segments[0]["original_text"] = text_override
    result = store.add_result(recording["id"], segments, {}, "U", {}, job_id=job["id"])
    store.execute("DELETE FROM result_hints WHERE result_id=?", (result["id"],))
    return recording, result


def test_store_only_cache_miss_recovers_from_transient_lock(tmp_path, monkeypatch):
    from cbs_transcriber import review_hints
    from cbs_transcriber.store import Store

    store = Store(tmp_path / "library")
    client = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(store=store)))
    recording, result = native_result(client, {"segments": whisperx_rows()})
    original, calls = review_hints.rebuild, []

    def locked_once(*args):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("synthetic transient lock")
        return original(*args)

    monkeypatch.setattr(review_hints, "rebuild", locked_once)
    first = review_hints.current_hints(store, recording["id"], result["id"])
    assert first == {"origin": "unavailable", "passages": {}, "edited_passages": 0}
    assert store.result_hints(recording["id"], result["id"]) is None
    second = review_hints.current_hints(store, recording["id"], result["id"])
    assert second["origin"] == "rebuilt" and second["passages"]["0"]["avg_logprob"] == -1.2
    raw = store.directory(recording["id"]) / ("ucloud-" + result["job_id"] + ".json")
    raw.unlink()
    assert review_hints.current_hints(store, recording["id"], result["id"]) == second
    assert len(calls) == 2


def test_older_native_results_rebuild_hints_once_from_the_saved_response(client):
    recording, result = native_result(client, {"segments": whisperx_rows()})
    url = f"/api/recordings/{recording['id']}/transcripts/{result['id']}/hints"
    first = client.get(url).json()
    assert first["origin"] == "rebuilt" and first["passages"]["0"]["avg_logprob"] == -1.2
    raw = client.app.state.store.directory(recording["id"]) / (
        "ucloud-" + result["job_id"] + ".json"
    )
    raw.unlink()
    assert client.get(url).json() == first  # Cached; no second rebuild.


def test_rebuild_refuses_when_saved_text_differs_from_the_raw_response(client):
    recording, result = native_result(client, {"segments": whisperx_rows()}, "Hej anden tekst.")
    hints = client.get(f"/api/recordings/{recording['id']}/transcripts/{result['id']}/hints")
    assert hints.json() == {"origin": "unavailable", "passages": {}, "edited_passages": 0}


def test_older_openai_part_results_rebuild_from_part_responses(client):
    store = client.app.state.store
    project = store.create_project("Parts")
    recording = store.create_recording(project["id"], "a.wav")
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"settings": {"provider_type": "openai"}, "provider_name": "O"},
    )
    folder = store.directory(recording["id"]) / "chunks" / job["id"]
    folder.mkdir(parents=True)
    responses = [
        {"text": "Hej.", "segments": [{"text": "Hej.", "start": 0, "end": 1, "avg_logprob": -0.3}]},
        {"text": "Ja.", "segments": [{"text": "Ja.", "start": 0, "end": 1, "avg_logprob": -1.4}]},
    ]
    chunks = []
    for index, payload in enumerate(responses):
        (folder / f"response-{index:04d}.json").write_text(json.dumps(payload), "utf-8")
        chunks.append(
            {
                "index": index,
                "offset": index * 900,
                "segments": providers.normalize_response(payload),
            }
        )
    store.update_job(job["id"], chunks=chunks)
    from cbs_transcriber.transcripts import combine_chunks

    result = store.add_result(
        recording["id"], combine_chunks(chunks), {}, "O", {}, job_id=job["id"]
    )
    store.execute("DELETE FROM result_hints WHERE result_id=?", (result["id"],))
    hints = client.get(f"/api/recordings/{recording['id']}/transcripts/{result['id']}/hints").json()
    assert hints["origin"] == "rebuilt"
    assert [hints["passages"][i]["avg_logprob"] for i in ("0", "1")] == [-0.3, -1.4]


def test_schema9_upgrade_backs_up_and_keeps_every_row(tmp_path, monkeypatch):
    import sqlite3

    from cbs_transcriber import store as store_module
    from cbs_transcriber.history import encode_payload
    from cbs_transcriber.store import SCHEMA_VERSION, Store

    # Build the actual schema through version 9, rather than relabelling a
    # current database that already contains newer columns and tables.
    with monkeypatch.context() as patch:
        patch.setattr(store_module, "SCHEMA_VERSION", 9)
        patch.setattr(Store, "_migrate_result_hints", lambda self: None)
        patch.setattr(Store, "_migrate_collections", lambda self: None)
        store = Store(tmp_path)
    segments = normalize_segments(whisperx_rows())
    pop_hints(segments)
    serialized = json.dumps(segments, ensure_ascii=False)
    payload, digest = encode_payload(segments, {})
    project, recording, result, version = (character * 32 for character in "abcd")
    stamp = "2026-01-02T00:00:00+00:00"
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        db.execute(
            "INSERT INTO projects (id,name,created) VALUES (?,?,?)", (project, "Upgrade", stamp)
        )
        db.execute(
            "INSERT INTO recordings (id,project_id,name,filename,segments,speakers,created,"
            "active_transcript_id,revision) VALUES (?,?,?,?,?,?,?,NULL,1)",
            (recording, project, "a", "a.wav", serialized, "{}", stamp),
        )
        db.execute(
            "INSERT INTO transcript_results (id,recording_id,label,source,segments,speakers,"
            "created,updated) VALUES (?,?,?,?,?,?,?,?)",
            (result, recording, "R", "{}", serialized, "{}", stamp, stamp),
        )
        db.execute("UPDATE recordings SET active_transcript_id=? WHERE id=?", (result, recording))
        db.execute(
            "INSERT INTO result_versions (id,result_id,sequence,payload,payload_sha256,observed_at,"
            "action,context,actor_kind) VALUES (?,?,1,?,?,?,?,?,?)",
            (version, result, payload, digest, stamp, "imported_result", "{}", "local_user"),
        )
    tables = [
        r["name"]
        for r in store.all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]
    before = {t: store.all(f'SELECT * FROM "{t}" ORDER BY rowid') for t in tables}
    upgraded = Store(tmp_path)
    with upgraded.connection() as db:
        after = {
            table: [
                {key: row[key] for key in before[table][0]}
                for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ]
            if before[table]
            else [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }
    assert after == before
    assert upgraded.all("SELECT * FROM result_hints") == []
    backups = list((tmp_path / "backups").glob("schema-9-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        db.row_factory = sqlite3.Row
        for table in tables:
            assert [
                dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ] == before[table]
    with upgraded.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    Store(tmp_path)
    assert len(list((tmp_path / "backups").glob("schema-9-*.sqlite3"))) == 1


def test_a_transient_file_lock_does_not_hide_rebuilt_hints_for_good(client, monkeypatch):
    from cbs_transcriber import review_hints

    recording, result = native_result(client, {"segments": whisperx_rows()})
    url = f"/api/recordings/{recording['id']}/transcripts/{result['id']}/hints"
    original, calls = review_hints.rebuild, []

    def locked_once(store, row):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("locked by a sync client")
        return original(store, row)

    monkeypatch.setattr(review_hints, "rebuild", locked_once)
    assert client.get(url).json()["origin"] == "unavailable"
    assert client.app.state.store.result_hints(recording["id"], result["id"]) is None
    assert client.get(url).json()["origin"] == "rebuilt"
