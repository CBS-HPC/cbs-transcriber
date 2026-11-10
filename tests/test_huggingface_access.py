import json

import httpx
import pytest

from cbs_transcriber import huggingface_access
from cbs_transcriber.enhancement import HF_SCOPE


@pytest.mark.parametrize(
    "identity,access,expected,model",
    [
        (200, 200, "valid", "allowed"),
        (200, 403, "valid", "denied"),
        (200, 503, "valid", "unavailable"),
        (401, 200, "invalid", "unknown"),
        (503, 200, "unavailable", "unknown"),
        (302, 200, "unavailable", "unknown"),
    ],
)
def test_explicit_metadata_check_distinguishes_token_and_gated_access(
    identity, access, expected, model
):
    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "GET" and request.url.host == "huggingface.co"
        assert request.headers["Authorization"] == "Bearer hf_SYNTHETIC_SECRET"
        assert not request.content
        return httpx.Response(
            identity if len(seen) == 1 else access,
            json={"private_account": "do not return", "secret": "hf_SYNTHETIC_SECRET"},
            headers={"Location": "https://attacker.invalid/secret"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        result = huggingface_access.check_token("hf_SYNTHETIC_SECRET", client=client)
    assert result["token_status"] == expected
    assert result["speaker_model_access"] == model
    assert len(seen) == (2 if identity == 200 else 1)
    assert seen[0].url.path == "/api/whoami-v2"
    if len(seen) == 2:
        assert seen[1].url.path.endswith("/auth-check")
    assert result["checked_at"]
    assert "SECRET" not in json.dumps(result) and "private_account" not in json.dumps(result)


def test_empty_offline_invalid_and_default_transport_never_follow_redirects(monkeypatch):
    assert huggingface_access.check_token("")["token_status"] == "missing"
    with pytest.raises(ValueError):
        huggingface_access.check_token("hf_bad\nsecret")
    factory = httpx.Client
    seen = []

    def handle(request):
        seen.append(request)
        raise httpx.ConnectError("hf_PRIVATE must never appear", request=request)

    def create(**kwargs):
        assert kwargs["follow_redirects"] is False
        return factory(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(huggingface_access.httpx, "Client", create)
    result = huggingface_access.check_token("hf_SYNTHETIC")
    assert result["token_status"] == "unavailable"
    assert "PRIVATE" not in json.dumps(result)
    assert len(seen) == 1


def test_settings_share_existing_scope_retain_forget_redact_and_bind_checks(client, monkeypatch):
    credentials = client.app.state.queue.credentials
    assert client.get("/api/huggingface-settings").json()["token_status"] == "missing"
    response = client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
    assert response.status_code == 200
    assert credentials.get(HF_SCOPE) == "hf_FIRST"
    assert client.get("/api/enhancement-settings").json()["has_hf_token"]
    assert "hf_FIRST" not in response.text
    assert client.put("/api/huggingface-settings", json={}).json()["has_token"]
    assert credentials.get(HF_SCOPE) == "hf_FIRST"
    assert (
        client.put("/api/huggingface-settings", json={"hf_token": "bad\nsecret"}).status_code == 400
    )
    assert credentials.get(HF_SCOPE) == "hf_FIRST"

    def check(token):
        return {
            **huggingface_access.token_state(token),
            "token_status": "valid",
            "speaker_model_access": "allowed",
            "checked_at": "2026-10-22T12:00:00+00:00",
        }

    monkeypatch.setattr(huggingface_access, "check_token", check)
    checked = client.post("/api/huggingface-settings/check", json={}).json()
    assert checked["token_status"] == "valid"
    assert client.get("/api/huggingface-settings").json() == checked
    # Checking an unsaved token does not save it or validate a different saved credential.
    client.post("/api/huggingface-settings/check", json={"hf_token": "hf_UNSAVED"})
    assert credentials.get(HF_SCOPE) == "hf_FIRST"
    client.put("/api/huggingface-settings", json={"hf_token": "hf_SECOND"})
    assert client.get("/api/huggingface-settings").json()["token_status"] == "unchecked"
    client.put("/api/huggingface-settings", json={"hf_token": ""})
    assert credentials.get(HF_SCOPE) == ""
    assert client.get("/api/huggingface-settings").json()["token_status"] == "missing"
    assert not client.get("/api/enhancement-settings").json()["has_hf_token"]
