import json

import httpx
import httpx2
import pytest
from openai import DefaultHttpxClient

from cbs_transcriber.providers import (
    OPENAI_MODELS,
    Credentials,
    Settings,
    check_models,
    normalize_response,
    transcribe_file,
)


@pytest.mark.parametrize("capability", ["words", "words_speakers"])
def test_openai_compatible_request_and_word_response(tmp_path, capability):
    path = tmp_path / "part.mp3"
    path.write_bytes(b"synthetic audio")

    def reply(request):
        assert request.url == "https://example.test/v1/audio/transcriptions"
        body = request.read()
        assert b"verbose_json" in body
        assert b"timestamp_granularities[]" in body
        assert b"whisper-1" in body
        return httpx.Response(
            200,
            json={
                "text": "Hello.",
                "segments": [{"text": "Hello.", "start": 0, "end": 1, "speaker": "Speaker 1"}],
                "words": [{"word": "Hello", "start": 0, "end": 1}],
            },
        )

    result = transcribe_file(
        path,
        Settings(base_url="https://example.test/v1", capability=capability),
        "secret",
        httpx.Client(transport=httpx.MockTransport(reply)),
    )
    assert normalize_response(result)[0]["word_timing"]
    assert normalize_response(result)[0]["speaker"] == "Speaker 1"


@pytest.mark.parametrize("operation", ["models", "transcription"])
@pytest.mark.parametrize("status", [307, 308])
def test_default_sdk_transport_never_follows_provider_redirects(
    tmp_path, monkeypatch, operation, status
):
    original_init = DefaultHttpxClient.__init__
    calls = []

    def reply(request):
        calls.append(request.url.host)
        if request.url.host == "approved.invalid":
            return httpx2.Response(
                status, headers={"Location": "https://other.invalid/v1/redirected"}
            )
        return httpx2.Response(200, json={"text": "Synthetic.", "data": [{"id": "whisper-1"}]})

    def initialize(self, *args, **kwargs):
        kwargs["transport"] = httpx2.MockTransport(reply)
        kwargs["trust_env"] = False
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(DefaultHttpxClient, "__init__", initialize)
    settings = Settings(base_url="https://approved.invalid/v1")
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"synthetic audio")
    with pytest.raises(ValueError) as error:
        if operation == "models":
            check_models(settings, "synthetic-key")
        else:
            transcribe_file(path, settings, "synthetic-key")
    assert calls == ["approved.invalid"], (str(error.value), repr(error.value.__cause__))


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500])
def test_api_errors_never_echo_upstream_body_or_key(tmp_path, status):
    path = tmp_path / "part.mp3"
    path.write_bytes(b"synthetic audio")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": {"message": "secret-private-audio"}})
    )
    with pytest.raises(ValueError) as error:
        transcribe_file(path, Settings(), "secret-private-audio", httpx.Client(transport=transport))
    assert "secret-private-audio" not in str(error.value)


@pytest.mark.parametrize("operation", ["models", "transcription"])
def test_redirect_enabled_injected_transport_is_refused_before_any_request(tmp_path, operation):
    calls = []

    def reply(request):
        calls.append(request.url.host)
        if request.url.host == "approved.invalid":
            return httpx.Response(307, headers={"Location": "https://other.invalid/v1/redirected"})
        return httpx.Response(200, json={"text": "Synthetic.", "data": [{"id": "whisper-1"}]})

    settings = Settings(base_url="https://approved.invalid/v1")
    path = tmp_path / "synthetic.mp3"
    path.write_bytes(b"synthetic private media")
    with (
        httpx.Client(transport=httpx.MockTransport(reply), follow_redirects=True) as client,
        pytest.raises(ValueError, match="redirects"),
    ):
        if operation == "models":
            check_models(settings, "synthetic-key", client)
        else:
            transcribe_file(path, settings, "synthetic-key", client)
    assert calls == []


def test_speaker_preset_omits_word_parameters(tmp_path):
    path = tmp_path / "part.mp3"
    path.write_bytes(b"synthetic audio")

    def reply(request):
        body = request.read()
        assert b"diarized_json" in body and b"chunking_strategy" in body
        assert b"timestamp_granularities" not in body
        return httpx.Response(
            200,
            json={
                "text": "Hello.",
                "segments": [
                    {
                        "text": "Hello.",
                        "start": 0,
                        "end": 1,
                        "speaker": "A",
                        "id": "0",
                        "type": "transcript.text.segment",
                    }
                ],
            },
        )

    result = transcribe_file(
        path,
        Settings(model="gpt-4o-transcribe-diarize", capability="speakers"),
        "secret",
        httpx.Client(transport=httpx.MockTransport(reply)),
    )
    segment = normalize_response(result)[0]
    assert segment["speaker"] == "A" and not segment["word_timing"]


def test_v16_legacy_diarize_settings_use_supported_format(tmp_path):
    """A saved words capability must not send Whisper-only fields to diarize."""
    path = tmp_path / "part.mp3"
    path.write_bytes(b"synthetic audio")

    def reply(request):
        body = request.read()
        assert b"diarized_json" in body
        assert b"timestamp_granularities" not in body
        return httpx.Response(200, json={"text": "Hej.", "segments": []})

    transcribe_file(
        path,
        Settings(model="gpt-4o-transcribe-diarize", capability="words"),
        "synthetic-key",
        httpx.Client(transport=httpx.MockTransport(reply)),
    )


def test_credentials_are_scoped_to_endpoint(monkeypatch):
    saved = {}
    monkeypatch.setattr("keyring.get_password", lambda service, account: saved.get(account))
    monkeypatch.setattr(
        "keyring.set_password", lambda service, account, value: saved.update({account: value})
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    credentials = Credentials()
    credentials.set("https://one.test/v1", "secret", True)
    assert Credentials().get("https://one.test/v1") == "secret"
    assert credentials.get("https://two.test/v1") == ""
    assert "secret" not in json.dumps({"base_url": "https://one.test/v1"})


@pytest.mark.parametrize("model", ["gpt-transcribe", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"])
def test_v16_plain_models_send_json_without_invented_timing(tmp_path, model):
    path = tmp_path / "part.mp3"
    path.write_bytes(b"synthetic audio")

    def reply(request):
        body = request.read()
        assert b'name="response_format"\r\n\r\njson' in body
        assert b"timestamp_granularities" not in body and b"chunking_strategy" not in body
        if model == "gpt-transcribe":
            assert b'name="languages[]"' in body and b'name="language"' not in body
        else:
            assert b'name="language"' in body and b'name="languages[]"' not in body
        return httpx.Response(200, json={"text": "Hej København."})

    payload = transcribe_file(
        path,
        Settings(model=model, language="da"),
        "synthetic-key",
        httpx.Client(transport=httpx.MockTransport(reply)),
    )
    segment = normalize_response(payload)[0]
    assert segment["start"] is None and not segment["word_timing"]
    assert Settings(base_url="https://custom.test/v1", model=model).capability == "words"


def test_v17_model_check_only_lists_models():
    requests = []

    def reply(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert not request.content
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "whisper-1", "created": 1, "object": "model", "owned_by": "openai"},
                    {
                        "id": "unrelated-model",
                        "created": 1,
                        "object": "model",
                        "owned_by": "openai",
                    },
                ],
            },
        )

    result = check_models(
        Settings(), "synthetic-secret", httpx.Client(transport=httpx.MockTransport(reply))
    )
    assert len(requests) == 1
    assert {m["id"] for m in result["models"]} == set(OPENAI_MODELS)
    assert [m["id"] for m in result["models"] if m["listed"]] == ["whisper-1"]
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_v17_model_check_errors_are_redacted(status):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status,
            json={"error": {"message": "private-key-and-transcript", "param": "private-field"}},
        )
    )
    with pytest.raises(ValueError) as error:
        check_models(Settings(), "private-key-and-transcript", httpx.Client(transport=transport))
    assert str(status) in str(error.value)
    assert "private" not in str(error.value)
