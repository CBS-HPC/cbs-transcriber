import json

from admission_support import inspect_and_materialize
from conftest import finish_media

from cbs_transcriber.app import create_app
from cbs_transcriber.providers import OPENAI_URL
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


def upload(client, wav_bytes, project=None):
    project = project or client.post("/api/projects", json={"name": "Interviews"}).json()
    response = client.post(
        "/api/recordings",
        data={"project_id": project["id"]},
        files={
            "file": ("interview.v2.wav", wav_bytes, "audio/wav"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_import_edit_export_and_restart(client, wav_bytes, tmp_path):
    uploaded = upload(client, wav_bytes)
    recording = finish_media(client, uploaded)
    identifier = recording["id"]
    payload = {
        "segments": [
            {
                "start": 0,
                "end": 2,
                "text": "Hej CBS.",
                "speaker": "S0",
                "words": [
                    {"word": "Hej", "start": 0, "end": 1},
                    {"word": "CBS", "start": 1, "end": 2},
                ],
            }
        ]
    }
    response = client.post(
        f"/api/recordings/{identifier}/transcript",
        data={"revision": 0},
        files={
            "file": ("transcript.json", json.dumps(payload).encode(), "application/json"),
        },
    )
    assert response.status_code == 200, response.text
    response = client.patch(
        f"/api/recordings/{identifier}/segments/0",
        json={
            "revision": 1,
            "mode": "word",
            "word_index": 0,
            "text": "Hallo",
        },
    )
    assert response.json()["segments"][0]["text"] == "Hallo CBS."
    conflict = client.patch(
        f"/api/recordings/{identifier}/segments/0",
        json={
            "revision": 1,
            "mode": "free",
            "text": "Stale correction",
        },
    )
    assert conflict.status_code == 409
    response = client.patch(
        f"/api/recordings/{identifier}/segments/0",
        json={
            "revision": 2,
            "mode": "free",
            "text": "En ny sætning med æøå.",
        },
    )
    assert not response.json()["segments"][0]["word_timing"]
    assert Store(tmp_path).recording(identifier)["segments"][0]["text"] == "En ny sætning med æøå."
    response = client.patch(
        f"/api/recordings/{identifier}/segments/0",
        json={
            "revision": 3,
            "mode": "restore",
        },
    )
    assert response.json()["segments"][0]["word_timing"]
    client.patch(
        f"/api/recordings/{identifier}/speakers",
        json={
            "revision": 4,
            "speaker": "S0",
            "name": "Øyvind",
        },
    )
    exported = client.get(f"/api/recordings/{identifier}/export")
    assert exported.text == "INTERVIEW\n\nØyvind\nHej CBS.\n"
    assert "interview.v2.txt" in exported.headers["content-disposition"]
    assert list((client.app.state.store.directory(identifier) / "sources").glob("*.json"))
    assert (client.app.state.store.directory(identifier) / "original.wav").read_bytes() == wav_bytes
    assert (
        create_app(tmp_path, start_worker=False).state.store.recording(identifier)["revision"] == 5
    )


def test_v4_media_byte_ranges(client, wav_bytes):
    recording = finish_media(client, upload(client, wav_bytes))
    response = client.get(
        f"/api/recordings/{recording['id']}/media/audio", headers={"Range": "bytes=0-99"}
    )
    assert response.status_code == 206
    assert len(response.content) == 100
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"].startswith("audio/")


def test_v39_frontend_refresh_does_not_reuse_stale_assets(client):
    for path in ("/", "/static/app.js", "/static/app.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "no-store", path
        # A browser holding a previously cached file must also discard a 304 response.
        # HTML is rendered with a server-instance marker rather than served as an
        # ETagged file. A browser may still send an old HTML validator after update.
        etag = '"previous-html"' if path == "/" else response.headers["etag"]
        response = client.get(path, headers={"If-None-Match": etag})
        assert response.status_code in {200, 304}
        assert response.headers.get("cache-control") == "no-store", path


def test_v5_boundary_and_key_redaction(client, credentials):
    secret = "test-secret-do-not-echo"
    response = client.put("/api/settings", json={"api_key": secret, "remember": False})
    assert response.status_code == 200
    assert response.json()["has_key"]
    assert secret not in response.text
    assert secret not in client.get("/api/settings").text
    assert secret not in client.app.state.store.db_path.read_bytes().decode("latin1")
    assert credentials.get(OPENAI_URL) == secret
    assert (
        client.post(
            "/api/projects", json={"name": "Blocked"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    invalid = client.put(
        "/api/settings", json={"base_url": "http://remote.example/v1", "api_key": secret}
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    assert client.get("/api/settings").json()["base_url"] == OPENAI_URL


def test_project_isolation_and_busy_delete(client, wav_bytes):
    project1 = client.post("/api/projects", json={"name": "One"}).json()
    project2 = client.post("/api/projects", json={"name": "Two"}).json()
    uploaded = upload(client, wav_bytes, project1)
    assert client.get(f"/api/projects/{project2['id']}/recordings").json() == []
    assert client.delete(f"/api/projects/{project1['id']}").status_code == 200
    assert client.get(f"/api/projects/{project2['id']}/recordings").status_code == 200
    assert client.get(f"/api/recordings/{uploaded['recording']['id']}").status_code == 404


def test_transcription_queue_replacement_is_explicit(client, wav_bytes, credentials):
    with client.app.state.store.library_lock():
        credentials.keys[OPENAI_URL] = "fake-key"
        recording = finish_media(client, upload(client, wav_bytes))
        response = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
        assert response.status_code == 202
        assert "fake-key" not in response.text
        [pending] = response.json()
        assert "input_intent" in pending["params"]
        duplicate = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
        assert duplicate.status_code == 409, duplicate.text
        [queued] = inspect_and_materialize(client.app.state.store, credentials, [pending["id"]])
        assert queued["id"] == pending["id"] and queued["recording_id"] == recording["id"]
        assert "input_intent" not in queued["params"]
        assert queued["params"]["audio_input"]["recording_id"] == recording["id"]
        duplicate = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
        assert duplicate.status_code == 409, duplicate.text
        assert client.post(f"/api/jobs/{queued['id']}/cancel").status_code == 200
        store = client.app.state.store
        reviewed = store.add_result(
            recording["id"],
            normalize_segments([{"text": "Reviewed wording.", "start": 0, "end": 3}]),
            {},
            "Reviewed transcript",
            {},
            activate=True,
            revision=store.recording(recording["id"])["revision"],
        )
        before_result = store.result(recording["id"], reviewed["id"])
        before_recording = store.recording(recording["id"])
        refused = client.post(
            "/api/transcriptions", json={"recording_ids": [recording["id"]], "replace": False}
        )
        assert refused.status_code == 409, refused.text
        replacement = client.post(
            "/api/transcriptions", json={"recording_ids": [recording["id"]], "replace": True}
        )
        assert replacement.status_code == 202, replacement.text
        [accepted] = replacement.json()
        assert accepted["recording_id"] == recording["id"]
        assert accepted["params"]["input_intent"]["accepted"]["replace"] is True
        assert "fake-key" not in replacement.text
        assert store.result(recording["id"], reviewed["id"]) == before_result
        after_recording = store.recording(recording["id"])
        for key in ("segments", "speakers", "revision", "active_transcript_id"):
            assert after_recording[key] == before_recording[key]
        assert [result["id"] for result in store.results(recording["id"])] == [reviewed["id"]]
        intent = accepted["params"]["input_intent"]
        cancelled = client.post(
            f"/api/input-intents/{accepted['id']}/cancel",
            json={"epoch": intent["epoch"], "phase": intent["phase"]},
        )
        assert cancelled.status_code == 200, cancelled.text


def test_invalid_uploads_do_not_create_recordings(client):
    project = client.post("/api/projects", json={"name": "One"}).json()
    response = client.post(
        "/api/recordings", data={"project_id": project["id"]}, files={"file": ("bad.exe", b"x")}
    )
    assert response.status_code == 400
    assert client.get(f"/api/projects/{project['id']}/recordings").json() == []
