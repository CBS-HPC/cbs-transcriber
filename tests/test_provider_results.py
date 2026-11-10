import json
import sqlite3

from admission_support import inspect_and_materialize
from conftest import finish_media

from cbs_transcriber.catalog import provider_catalog
from cbs_transcriber.providers import OPENAI_MODELS, OPENAI_URL, Settings
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import edit_segment, normalize_segments


def provider(client, name, model, **extra):
    response = client.post("/api/providers", json={"name": name, "model": model, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def recording(client, wav_bytes):
    project = client.post("/api/projects", json={"name": "Compare"}).json()
    response = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={"file": ("comparison.wav", wav_bytes)},
    )
    return finish_media(client, response.json())


def test_v12_legacy_migration_preserves_edits_timing_revision_and_backup(tmp_path):
    segments = normalize_segments(
        [
            {
                "text": "Hej København",
                "start": 0,
                "end": 2,
                "speaker": "A",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1},
                    {"word": "København", "start": 1, "end": 2},
                ],
            }
        ]
    )
    segments[0] = edit_segment(segments[0], "word", "Hallo", 0)
    encoded = json.dumps(segments, ensure_ascii=False)
    with sqlite3.connect(tmp_path / "library.sqlite3") as db:
        db.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, created TEXT);
            CREATE TABLE recordings (id TEXT PRIMARY KEY,project_id TEXT,name TEXT,filename TEXT,
                kind TEXT DEFAULT 'audio',status TEXT DEFAULT 'ready',duration REAL,
                segments TEXT,speakers TEXT,revision INTEGER,created TEXT);
            CREATE TABLE settings (id INTEGER PRIMARY KEY,value TEXT);
            INSERT INTO projects VALUES ('project','Original','2026-01-01');
            PRAGMA user_version=1;
        """)
        db.execute(
            "INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "a" * 32,
                "project",
                "Original",
                "old.wav",
                "audio",
                "ready",
                2,
                encoded,
                '{"A":"Øyvind"}',
                7,
                "2026-01-01",
            ),
        )
        db.execute(
            "INSERT INTO settings VALUES (1,?)", (Settings(language="da").model_dump_json(),)
        )
    store = Store(tmp_path)
    current = store.recording("a" * 32)
    assert current["revision"] == 7
    assert current["segments"] == segments
    assert current["speakers"] == {"A": "Øyvind"}
    assert len(current["transcripts"]) == 1
    saved = store.result(current["id"], current["active_transcript_id"])
    assert saved["segments"] == segments
    assert saved["label"] == "Existing transcript"
    assert len(Store(tmp_path).recording(current["id"])["transcripts"]) == 1
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT segments FROM recordings").fetchone()[0] == encoded
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1


def test_v5_v13_named_providers_default_and_endpoint_credentials(client, credentials):
    first = client.get("/api/providers").json()
    original = first["default_provider_id"]
    second = provider(
        client,
        "Danish speakers",
        "gpt-4o-transcribe-diarize",
        capability="speakers",
        language="da",
        api_key="synthetic-secret",
        remember=False,
    )
    configured = client.get("/api/providers").json()["providers"]
    assert {p["model"] for p in configured} == set(OPENAI_MODELS)
    assert {p["id"] for p in configured} >= {original, second["id"]}
    assert "synthetic-secret" not in client.get("/api/providers").text
    assert "synthetic-secret" not in client.app.state.store.db_path.read_bytes().decode("latin1")
    assert credentials.get(OPENAI_URL) == "synthetic-secret"
    client.put("/api/providers/default", json={"provider_id": second["id"]})
    assert client.get("/api/settings").json()["model"] == second["model"]
    client.put("/api/settings", json={"model": "custom-model", "capability": "json"})
    assert (
        next(
            p for p in client.get("/api/providers").json()["providers"] if p["id"] == second["id"]
        )["model"]
        == "custom-model"
    )
    third = provider(
        client, "Local", "local", base_url="http://127.0.0.1:9999/v1", capability="json"
    )
    assert not third["has_key"]
    assert client.delete(f"/api/providers/{second['id']}").status_code == 200
    assert client.get("/api/providers").json()["default_provider_id"] == original
    client.delete(f"/api/providers/{third['id']}")
    remaining = client.get("/api/providers").json()["providers"]
    assert {p["model"] for p in remaining} == set(OPENAI_MODELS) - {second["model"]}
    for configured in remaining:
        if configured["id"] != original:
            assert client.delete(f"/api/providers/{configured['id']}").status_code == 200
    assert client.delete(f"/api/providers/{original}").status_code == 400


def test_v12_v13_multiple_provider_results_are_frozen_and_do_not_overwrite(
    client, wav_bytes, credentials, monkeypatch, tmp_path
):
    r = recording(client, wav_bytes)
    store = client.app.state.store
    store.save_transcript(r["id"], normalize_segments([{"text": "My reviewed text"}]), {}, 0)
    original = store.recording(r["id"])
    first = client.get("/api/providers").json()["providers"][0]
    # A new library starts with the catalog's OpenAI default (ADR 0126: gpt-transcribe).
    assert first["model"] == provider_catalog()["openai"]["default"] == "gpt-transcribe"
    second = provider(client, "Other model", "other", capability="json")
    credentials.keys[OPENAI_URL] = "fake-key"
    chosen = client.patch(f"/api/recordings/{r['id']}/provider", json={"provider_id": second["id"]})
    assert chosen.json()["provider_id"] == second["id"]
    with store.library_lock():
        request = {"recording_ids": [r["id"]], "provider_ids": [first["id"], second["id"]]}
        # Without explicit provider comparison or replace consent, reviewed text is guarded.
        assert (
            client.post("/api/transcriptions", json={"recording_ids": [r["id"]]}).status_code == 409
        )
        assert not store.all("SELECT id FROM jobs WHERE kind='transcribe'")
        response = client.post("/api/transcriptions", json={**request, "replace": True})
        assert response.status_code == 202, response.text
        jobs = response.json()
        assert len(jobs) == 2
        assert (
            client.post(
                "/api/transcriptions",
                json={
                    "recording_ids": [r["id"]],
                    "provider_ids": [second["id"]],
                    "replace": True,
                },
            ).status_code
            == 409
        )
        jobs = inspect_and_materialize(store, credentials, [job["id"] for job in jobs])
        # Freeze executable settings before changing the saved provider configuration.
        client.put(f"/api/providers/{second['id']}", json={"name": "Changed", "model": "new"})
        calls = []

        def transcribe(path, settings, key):
            calls.append(settings.model)
            return {
                "text": settings.model,
                "segments": [{"text": settings.model, "start": 0, "end": 2, "speaker": "A"}],
            }

        monkeypatch.setattr("cbs_transcriber.providers.transcribe_file", transcribe)
        for job in jobs:
            client.app.state.queue.process(job["id"])
            store.update_job(job["id"], state="completed")
        current = store.recording(r["id"])
        assert calls == [first["model"], "other"]
        assert current["segments"] == original["segments"]
        assert current["revision"] == original["revision"]
        assert len(current["transcripts"]) == 3
        other = next(t for t in current["transcripts"] if t["source"].get("model") == "other")
        assert other["label"] == "Other model"
        # Reprocessing completed checkpoint data cannot add another result or paid request.
        client.app.state.queue.process(jobs[-1]["id"])
        assert len(store.recording(r["id"])["transcripts"]) == 3
        assert calls == [first["model"], "other"]
    assert len(Store(tmp_path).recording(r["id"])["transcripts"]) == 3


def test_v12_v14_switching_isolates_edits_speakers_exports_and_rejects_stale_ids(client, wav_bytes):
    r = recording(client, wav_bytes)
    store = client.app.state.store
    results = []
    for text in ["First result æøå", "Second result"]:
        result = store.add_result(
            r["id"],
            normalize_segments([{"text": text, "start": 0, "end": 2, "speaker": "A"}]),
            {},
            text,
            {"kind": "test"},
        )
        results.append(result)
    assert store.recording(r["id"])["active_transcript_id"] == results[0]["id"]
    switch = client.post(
        f"/api/recordings/{r['id']}/transcripts/{results[1]['id']}/activate", json={"revision": 1}
    )
    assert switch.status_code == 200
    assert (
        client.patch(
            f"/api/recordings/{r['id']}/segments/0",
            json={"revision": 1, "mode": "free", "text": "Wrong result"},
        ).status_code
        == 409
    )
    edited = client.patch(
        f"/api/recordings/{r['id']}/segments/0",
        json={"revision": 2, "mode": "free", "text": "Corrected second"},
    ).json()
    client.patch(
        f"/api/recordings/{r['id']}/speakers",
        json={"revision": edited["revision"], "speaker": "A", "name": "Second speaker"},
    )
    first = client.get(f"/api/recordings/{r['id']}/transcripts/{results[0]['id']}").json()
    assert first["segments"][0]["text"] == "First result æøå"
    assert first["speakers"] == {}
    export = client.get(f"/api/recordings/{r['id']}/export", params={"result_id": results[0]["id"]})
    assert "First result æøå" in export.text
    assert "Corrected second" in client.get(f"/api/recordings/{r['id']}/export").text
    another = recording(client, wav_bytes)
    assert (
        client.get(f"/api/recordings/{another['id']}/transcripts/{results[0]['id']}").status_code
        == 404
    )
    assert (
        client.post(
            f"/api/recordings/{another['id']}/transcripts/{results[0]['id']}/activate",
            json={"revision": 0},
        ).status_code
        == 404
    )


def test_v12_imports_create_separate_recoverable_results(client, wav_bytes):
    r = recording(client, wav_bytes)
    for revision, text in enumerate(["First import", "Second import"]):
        response = client.post(
            f"/api/recordings/{r['id']}/transcript",
            data={"revision": revision, "replace": "true"},
            files={"file": ("import.json", json.dumps({"text": text}).encode())},
        )
        assert response.status_code == 200
    current = response.json()
    assert len(current["transcripts"]) == 2
    first = client.get(f"/api/recordings/{r['id']}/transcripts/{current['transcripts'][0]['id']}")
    assert first.json()["segments"][0]["text"] == "First import"


def test_v13_invalid_provider_does_not_queue_partial_work(client, wav_bytes, credentials):
    r = recording(client, wav_bytes)
    credentials.keys[OPENAI_URL] = "fake-key"
    p = client.get("/api/providers").json()["default_provider_id"]
    before = len(client.get("/api/jobs").json())
    response = client.post(
        "/api/transcriptions", json={"recording_ids": [r["id"]], "provider_ids": [p, "missing"]}
    )
    assert response.status_code == 404
    assert len(client.get("/api/jobs").json()) == before
