"""Direct service refusal is separate from compatible protocol and frozen local recovery."""

import copy
import json
import threading

import httpx
import pytest

from cbs_transcriber import (
    audio_inputs,
    input_admission,
    media,
    provider_connection,
    providers,
    ucloud_discovery,
    ucloud_native,
)
from cbs_transcriber.jobs import completed_audio_plan
from cbs_transcriber.store import new_id, now

COMPATIBLE = "https://compatible.invalid/v1"
OUTPUT = {
    "text": "Preserved words.",
    "segments": [{"text": "Preserved words.", "start": 0, "end": 1}],
}
DIRECT_CASES = [
    ("openai", COMPATIBLE),
    ("custom", providers.OPENAI_URL),
    ("custom", "https://API.OPENAI.COM:443/other"),
    ("custom", "https://api.openai.com./v1"),
    ("custom", "https://api\u3002openai.com/v1"),
    ("custom", "https://api\uff0eopenai\uff61com./v1"),
    ("ucloud-vllm", "https://api.openai.com/other/v1"),
    ("ucloud", "https://api.openai.com"),
]


def historical_profile(store, settings):
    """Seed an exact saved row from before service deactivation, without a current Save."""
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, "Preserved provider", settings.model_dump_json(), now()),
    )
    return providers.profile(store, identifier)


def historical_job(client, credentials, monkeypatch, *, raw=False, partial=False):
    store = client.app.state.store
    project = store.create_project("Preserved work")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute(
        "UPDATE recordings SET status='ready',duration=1800 WHERE id=?", (recording["id"],)
    )
    settings = providers.Settings()
    credentials.keys[settings.base_url] = "synthetic-key"
    job = store.add_job(
        recording["id"], "transcribe", {"settings": settings.model_dump(), "revision": 0}
    )
    folder = store.directory(recording["id"]) / "preserved-parts"
    folder.mkdir(parents=True)
    plan = [{"file": "a.mp3", "offset": 0}, {"file": "b.mp3", "offset": 900}]
    monkeypatch.setattr(media, "prepare_chunks", lambda *args: (folder, plan))
    if raw:
        for index in range(2):
            (folder / f"response-{index:04d}.json").write_text(json.dumps(OUTPUT), encoding="utf-8")
    if partial:
        (folder / "response-0000.json").write_text(json.dumps(OUTPUT), encoding="utf-8")
        store.update_job(
            job["id"],
            chunks=[{"index": 0, "offset": 0, "segments": providers.normalize_response(OUTPUT)}],
        )
    return store, recording, store.job(job["id"]), folder


@pytest.mark.parametrize("kind,url", DIRECT_CASES)
@pytest.mark.parametrize("operation", ["models", "transcription"])
def test_family_and_normalized_host_refuse_before_sdk_or_audio(monkeypatch, kind, url, operation):
    settings = providers.Settings(provider_type=kind, base_url=url)
    assert providers.direct_openai(settings)
    monkeypatch.setattr(providers, "OpenAI", lambda **_: pytest.fail("No SDK may be constructed"))

    class Unopened:
        def open(self, *args, **kwargs):
            pytest.fail("No audio may be opened")

    with pytest.raises(ValueError, match="Direct OpenAI service is disabled"):
        if operation == "models":
            providers.check_models(settings, "synthetic-key")
        else:
            providers.transcribe_file(Unopened(), settings, "synthetic-key")


@pytest.mark.parametrize(
    "url", [COMPATIBLE, "http://127.0.0.1:9000/v1", "https://api.openai.com.example.invalid/v1"]
)
def test_nonofficial_compatible_request_remains_supported(tmp_path, url):
    settings = providers.Settings(provider_type="custom", base_url=url, capability="segments")
    assert not providers.direct_openai(settings)
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"synthetic bytes")
    calls = []

    def reply(request):
        calls.append(request.url)
        assert request.url.path == "/v1/audio/transcriptions"
        assert b"verbose_json" in request.read()
        return httpx.Response(200, json=OUTPUT)

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        result = providers.transcribe_file(path, settings, "synthetic-key", client)
    assert providers.normalize_response(result) == providers.normalize_response(OUTPUT)
    assert len(calls) == 1


@pytest.mark.parametrize("route", ["check-models", "check-ucloud", "discover-ucloud"])
def test_check_refusal_precedes_credentials_and_check_observation_mutation(
    client, monkeypatch, route
):
    before = client.get("/api/settings").json()
    if route != "check-models":
        before = providers.Settings(
            provider_type="ucloud", base_url="https://api.openai.com", model="large-v3"
        ).model_dump()
    store = client.app.state.store
    store.execute("INSERT OR REPLACE INTO settings(id,value) VALUES (10,?)", ('{"preserved":1}',))
    saved = store.one("SELECT value FROM settings WHERE id=10")["value"]
    monkeypatch.setattr(client.app.state.credentials, "get", lambda *_: pytest.fail("No key read"))
    monkeypatch.setattr(provider_connection.Checks, "begin", lambda *_: pytest.fail("No attempt"))
    monkeypatch.setattr(
        provider_connection, "failed", lambda *_a, **_k: pytest.fail("No revocation")
    )
    monkeypatch.setattr("cbs_transcriber.app.check_models", lambda *_: pytest.fail("No model call"))
    response = client.post("/api/providers/" + route, json=before)
    assert response.status_code == 400 and "Direct OpenAI service is disabled" in response.text
    assert store.one("SELECT value FROM settings WHERE id=10")["value"] == saved


@pytest.mark.parametrize(
    "route", ["create", "update", "settings", "default", "recording", "enqueue"]
)
def test_fresh_http_paths_refuse_without_provider_or_credential_changes(client, credentials, route):
    store = client.app.state.store
    saved = client.get("/api/settings").json()
    project = store.create_project("Fresh request")
    recording = store.create_recording(project["id"], "synthetic.wav")
    credentials.keys[providers.OPENAI_URL] = "preserved-key"
    before = copy.deepcopy(store.all("SELECT * FROM providers ORDER BY id"))
    key_before = dict(credentials.keys)
    payload = {**saved, "name": "Refused direct service", "api_key": "replacement-key"}
    if route == "create":
        response = client.post("/api/providers", json=payload)
    elif route == "update":
        response = client.put(f"/api/providers/{saved['id']}", json=payload)
    elif route == "settings":
        response = client.put("/api/settings", json=payload)
    elif route == "default":
        response = client.put("/api/providers/default", json={"provider_id": saved["id"]})
    elif route == "recording":
        response = client.patch(
            f"/api/recordings/{recording['id']}/provider", json={"provider_id": saved["id"]}
        )
    else:
        response = client.post(
            "/api/transcriptions",
            json={"recording_ids": [recording["id"]], "provider_ids": [saved["id"]]},
        )
    assert response.status_code == 400 and "Direct OpenAI service is disabled" in response.text
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert credentials.keys == key_before
    assert not store.all("SELECT id FROM jobs")


@pytest.mark.parametrize("supply_credentials", [False, True])
def test_shared_acceptance_refuses_even_without_http_or_readiness_argument(
    client, credentials, supply_credentials
):
    store = client.app.state.store
    project = store.create_project("Shared admission")
    recording = store.create_recording(project["id"], "synthetic.wav")
    chosen = providers.profile(store, providers.default_provider_id(store))
    accepted = input_admission.choice(provider=chosen)
    with store.library_lock(), pytest.raises(ValueError, match="Direct OpenAI service is disabled"):
        input_admission.accept(
            store,
            [{"recording_id": recording["id"], "choice": accepted}],
            credentials=credentials if supply_credentials else None,
        )
    assert not store.all("SELECT id FROM jobs")


def test_public_projection_preserves_saved_profile_receipt_and_results(client, credentials):
    store = client.app.state.store
    settings = providers.Settings(provider_type="custom", base_url="https://api.openai.com./v1")
    chosen = historical_profile(store, settings)
    project = store.create_project("Saved direct-service evidence")
    recording = store.create_recording(project["id"], "synthetic.wav")
    saved_result = store.add_result(
        recording["id"],
        providers.normalize_response(OUTPUT),
        {},
        "Preserved direct-service result",
        {"kind": "provider", **settings.model_dump()},
    )
    credentials.keys[settings.base_url] = "preserved-key"
    checks = provider_connection.Checks()
    proof = checks.issue(settings, "preserved-key", checks.begin(settings), models=[settings.model])
    with store.connection() as db:
        checks.save(db, proof, settings, "preserved-key")
    before = store.one("SELECT value FROM providers WHERE id=?", (chosen["id"],))["value"]
    receipt = store.one("SELECT value FROM settings WHERE id=10")["value"]
    result = providers.public_profile(store, credentials, chosen["id"])
    assert result["has_key"] and result["enabled"]
    assert result["service_available"] is False and result["connection_ready"] is False
    assert result["connection_state"] == "service_disabled"
    assert store.one("SELECT value FROM providers WHERE id=?", (chosen["id"],))["value"] == before
    assert store.one("SELECT value FROM settings WHERE id=10")["value"] == receipt
    assert chosen["id"] in {row["id"] for row in client.get("/api/providers").json()["providers"]}
    path = f"/api/recordings/{recording['id']}/transcripts/{saved_result['id']}"
    assert client.get(path).json()["source"]["base_url"] == settings.base_url
    assert len(client.get(path + "/versions").json()) == 1
    exported = client.get(
        f"/api/recordings/{recording['id']}/export",
        params={"result_id": saved_result["id"], "format": "json"},
    )
    assert exported.status_code == 200 and OUTPUT["text"] in exported.text


@pytest.mark.parametrize("retry,partial", [(False, False), (True, False), (True, True)])
def test_queued_and_partial_retry_never_send_a_new_direct_request(
    client, credentials, monkeypatch, retry, partial
):
    store, recording, job, folder = historical_job(
        client, credentials, monkeypatch, partial=partial
    )
    frozen = copy.deepcopy(job["params"])
    prior = copy.deepcopy(job["chunks"])
    monkeypatch.setattr(providers, "OpenAI", lambda **_: pytest.fail("No direct outbound request"))
    if retry:
        store.update_job(job["id"], state="failed")
        response = client.post(f"/api/jobs/{job['id']}/retry", json={})
        assert response.status_code == 200, response.text
    with pytest.raises(ValueError, match="Direct OpenAI service is disabled"):
        client.app.state.queue.process(job["id"])
    assert store.job(job["id"])["chunks"] == prior
    assert store.job(job["id"])["params"] == frozen
    assert not store.results(recording["id"])
    if partial:
        assert json.loads((folder / "response-0000.json").read_text()) == OUTPUT


def test_complete_legacy_raw_retry_finalizes_without_network(client, credentials, monkeypatch):
    store, recording, job, folder = historical_job(client, credentials, monkeypatch, raw=True)
    raw_before = {path.name: path.read_bytes() for path in folder.glob("response-*.json")}
    frozen = copy.deepcopy(job["params"])
    assert (
        completed_audio_plan(job) is None
    )  # This legacy path is deliberately not the fresh helper.
    monkeypatch.setattr(providers, "OpenAI", lambda **_: pytest.fail("No direct outbound request"))
    store.update_job(job["id"], state="failed")
    assert client.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 200
    client.app.state.queue.process(job["id"])
    [result] = store.results(recording["id"])
    context = store.one(
        "SELECT context FROM result_versions WHERE result_id=? AND sequence=1", (result["id"],)
    )["context"]
    assert json.loads(context)["producer_job_id"] == job["id"]
    assert [part["offset"] for part in store.job(job["id"])["chunks"]] == [0, 900]
    assert store.job(job["id"])["params"] == frozen
    assert raw_before == {path.name: path.read_bytes() for path in folder.glob("response-*.json")}


def test_complete_original_audio_chunks_finalize_without_key_decoder_or_network(
    client, credentials, monkeypatch, wav_bytes
):
    store = client.app.state.store
    project = store.create_project("Frozen complete chunks")
    recording = store.create_recording(project["id"], "synthetic.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    settings = providers.Settings()
    with store.library_lock():
        frozen = audio_inputs.freeze(store, recording)
        transport = audio_inputs.transport_for(frozen, settings)
        job = store.add_job(
            recording["id"],
            "transcribe",
            {
                "settings": settings.model_dump(),
                "revision": 0,
                "audio_input": frozen,
                "audio_transport": transport,
            },
        )
        folder, plan = audio_inputs.prepare_chunks(
            directory,
            job["id"],
            frozen["duration"],
            settings.max_upload_mb,
            threading.Event(),
            audio_input=frozen,
            transport=transport,
        )
        store.record_audio_preparation(job["id"], audio_inputs.prepared_evidence(folder, frozen))
        store.update_job(
            job["id"],
            chunks=[
                {
                    "index": 0,
                    "offset": plan[0]["offset"],
                    "segments": providers.normalize_response(OUTPUT),
                }
            ],
        )
        assert completed_audio_plan(store.job(job["id"])) == plan
        monkeypatch.setattr(credentials, "get", lambda *_: pytest.fail("No provider key needed"))
        monkeypatch.setattr(media, "prepare_chunks", lambda *_a, **_k: pytest.fail("No decoder"))
        monkeypatch.setattr(providers, "OpenAI", lambda **_: pytest.fail("No network"))
        client.app.state.queue.process(job["id"])
    assert len(store.results(recording["id"])) == 1


def test_stop_remains_available_for_frozen_direct_work(client, credentials, monkeypatch):
    store, _, job, _ = historical_job(client, credentials, monkeypatch, partial=True)
    before = store.job(job["id"])
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 200, response.text
    after = store.job(job["id"])
    assert after["state"] == "cancelled"
    assert after["chunks"] == before["chunks"] and after["params"] == before["params"]


@pytest.mark.parametrize(
    "module,name", [(ucloud_native, "client_for"), (ucloud_discovery, "_client")]
)
def test_native_client_seams_refuse_official_host_before_client_creation(monkeypatch, module, name):
    settings = providers.Settings(provider_type="ucloud", base_url="https://api\u3002openai.com.")
    monkeypatch.setattr(
        module, "UCloudClient", lambda *_a, **_k: pytest.fail("No native HTTP client")
    )
    with pytest.raises(ValueError, match="Direct OpenAI service is disabled"):
        getattr(module, name)(settings, "synthetic-token")


def test_frozen_native_label_cannot_bypass_last_mile_service_policy(
    client, credentials, monkeypatch
):
    from test_native_model_profiles import native_settings

    store = client.app.state.store
    project = store.create_project("Frozen native-shaped request")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET status='ready',duration=1 WHERE id=?", (recording["id"],))
    settings = native_settings(base_url="https://api.openai.com")
    credentials.keys[providers.credential_scope(settings)] = "synthetic-token"
    job = store.add_job(
        recording["id"], "transcribe", {"settings": settings.model_dump(), "revision": 0}
    )
    monkeypatch.setattr(
        ucloud_native, "UCloudClient", lambda *_a, **_k: pytest.fail("No native HTTP")
    )
    with pytest.raises(ValueError, match="Direct OpenAI service is disabled"):
        client.app.state.queue.process(job["id"])
    assert store.job(job["id"])["params"] == job["params"]
    assert not store.results(recording["id"])


@pytest.mark.parametrize("route", ["runtime-language", "native-language", "native-allocation"])
def test_specialized_provider_saves_refuse_before_credentials_or_mutation(
    client, credentials, monkeypatch, route
):
    from test_native_model_profiles import native_settings

    store = client.app.state.store
    settings = native_settings(
        base_url="https://api.openai.com",
        model="whisperx/large-v3" if route == "runtime-language" else "large-v3",
    )
    chosen = historical_profile(store, settings)
    row = store.one("SELECT name,value FROM providers WHERE id=?", (chosen["id"],))
    credentials.keys[providers.credential_scope(settings)] = "preserved-token"
    before = store.all("SELECT * FROM providers ORDER BY id")
    keys = dict(credentials.keys)
    monkeypatch.setattr(credentials, "get", lambda *_: pytest.fail("No credential read"))
    payload = {"expected_version": providers.stored_version(row["name"], row["value"])}
    payload.update({"allocation": "mig1g"} if route == "native-allocation" else {"language": "en"})
    response = client.put(f"/api/providers/{chosen['id']}/{route}", json=payload)
    assert response.status_code == 400 and "Direct OpenAI service is disabled" in response.text
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert credentials.keys == keys
