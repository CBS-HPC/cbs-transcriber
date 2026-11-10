import json

import httpx
import pytest

from cbs_transcriber.providers import (
    OPENAI_URL,
    Settings,
    check_models,
    normalize_response,
    setup_key_scopes,
    transcribe_file,
)


def ucloud_settings(**extra):
    return Settings(
        provider_type="ucloud-vllm",
        base_url="https://speech.test/v1",
        model="whisper-danish",
        capability="segments",
        **extra,
    )


def test_v20_setup_tokens_are_separate_and_never_persist_in_database(client, credentials):
    response = client.post(
        "/api/providers",
        json={
            "name": "UCloud speech",
            "provider_type": "ucloud-vllm",
            "base_url": "",
            "model": "whisper-danish",
            "capability": "segments",
            "remember": False,
            "ucloud_token": "synthetic-ucloud-control-token",
            "hf_token": "synthetic-hf-token",
            "ucloud": {"hf_model": "openai/whisper-large-v3-turbo", "project_id": "project"},
        },
    )
    assert response.status_code == 201, response.text
    provider = response.json()
    assert provider["has_ucloud_token"] and provider["has_hf_token"] and not provider["has_key"]
    scopes = setup_key_scopes(Settings.model_validate(provider))
    assert credentials.get(scopes["ucloud_token"]) == "synthetic-ucloud-control-token"
    assert credentials.get(scopes["hf_token"]) == "synthetic-hf-token"
    assert credentials.get(OPENAI_URL) == ""
    for secret in ("synthetic-ucloud-control-token", "synthetic-hf-token"):
        assert secret not in client.get("/api/providers").text
        assert secret.encode() not in client.app.state.store.db_path.read_bytes()
        assert secret not in json.dumps(client.app.state.store.all("SELECT * FROM providers"))
    # Blank edits retain keys; clearing them is an explicit separate operation.
    provider["ucloud_token"] = provider["hf_token"] = None
    assert client.put(f"/api/providers/{provider['id']}", json=provider).json()["has_hf_token"]
    provider.update(ucloud_token="", hf_token="", remember=False)
    cleared = client.put(f"/api/providers/{provider['id']}", json=provider).json()
    assert not cleared["has_ucloud_token"] and not cleared["has_hf_token"]


def test_v20_pending_endpoint_cannot_use_control_or_hf_tokens_as_serving_key(client, credentials):
    settings = ucloud_settings()
    for scope in setup_key_scopes(settings).values():
        credentials.keys[scope] = "synthetic-setup-secret"
    response = client.post("/api/providers/check-models", json=settings.model_dump())
    assert response.status_code == 400 and "serving API key" in response.text
    pending = settings.model_dump()
    pending["base_url"] = ""
    response = client.post("/api/providers/check-models", json=pending)
    assert response.status_code == 400 and "endpoint" in response.text
    assert "synthetic-setup-secret" not in response.text
    assert client.get("/api/jobs").json() == []


def test_v20_discovery_returns_actual_served_aliases_without_audio():
    settings = ucloud_settings()

    def reply(request):
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer synthetic-serving-key"
        assert request.content == b""
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "whisper-danish", "object": "model", "created": 1, "owned_by": "vllm"},
                    {"id": "chat-only", "object": "model", "created": 1, "owned_by": "vllm"},
                ],
            },
        )

    result = check_models(
        settings, "synthetic-serving-key", httpx.Client(transport=httpx.MockTransport(reply))
    )
    assert [model["id"] for model in result["models"]] == ["chat-only", "whisper-danish"]
    assert result["selected_model_listed"]
    assert "does not verify speech recognition" in result["note"]


@pytest.mark.parametrize(
    "capability,format_name",
    [
        ("segments", "verbose_json"),
        ("speakers", "diarized_json"),
        ("json", "json"),
    ],
)
def test_v20_asr_request_uses_alias_serving_key_and_supported_parameters(
    tmp_path, capability, format_name
):
    audio = tmp_path / "part.mp3"
    audio.write_bytes(b"synthetic audio")
    settings = Settings(
        provider_type="ucloud-vllm",
        base_url="https://speech.test/v1",
        model="whisper-danish",
        capability=capability,
        language="da",
    )

    def reply(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer synthetic-serving-key"
        body = request.read()
        assert format_name.encode() in body and b"whisper-danish" in body
        assert b"chunking_strategy" not in body and b"timestamp_granularities" not in body
        return httpx.Response(
            200,
            json={
                "text": "Hej København.",
                "segments": [
                    {"text": "Hej København.", "start": 0, "end": 2, "speaker": "A"},
                ],
            },
        )

    result = transcribe_file(
        audio, settings, "synthetic-serving-key", httpx.Client(transport=httpx.MockTransport(reply))
    )
    segment = normalize_response(result)[0]
    assert segment["start"] == 0 and segment["speaker"] == "A"
    assert not segment["word_timing"]


def test_v20_chat_only_endpoint_returns_actionable_redacted_error(tmp_path):
    audio = tmp_path / "part.mp3"
    audio.write_bytes(b"synthetic audio")
    with pytest.raises(ValueError, match="does not expose audio transcription") as error:
        transcribe_file(
            audio,
            ucloud_settings(),
            "synthetic-serving-key",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        404, json={"error": {"message": "private-token"}}
                    )
                )
            ),
        )
    assert "private-token" not in str(error.value)


def test_v20_type_inference_and_url_normalization():
    assert Settings().provider_type == "openai"
    assert Settings(base_url="https://other.test/v1").provider_type == "custom"
    assert ucloud_settings().concurrent_chunks == 1
    assert (
        Settings(provider_type="ucloud-vllm", base_url="https://voice.test").base_url
        == "https://voice.test/v1"
    )
    with pytest.raises(ValueError):
        Settings(provider_type="ucloud-vllm", base_url=OPENAI_URL)
    for limit in (0, 5):
        with pytest.raises(ValueError):
            Settings(concurrent_chunks=limit)
