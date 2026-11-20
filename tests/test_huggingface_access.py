import json

import httpx
import pytest

from cbs_transcriber import enhancement_api, huggingface_access
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
    manager = client.app.state.huggingface_tasks
    monkeypatch.setattr(manager, "freeze_cloud_plan", lambda: {"status": "needs_setup"})

    def refuse(*args, **kwargs):
        pytest.fail("HF Save, Check and GET must not check access or prepare models inline")

    monkeypatch.setattr(huggingface_access, "check_token", refuse)
    monkeypatch.setattr(manager, "_child", refuse)
    monkeypatch.setattr(enhancement_api, "local_speaker_status", refuse)
    assert client.get("/api/huggingface-settings").json()["token_status"] == "missing"
    response = client.put("/api/huggingface-settings", json={"hf_token": "hf_FIRST"})
    assert response.status_code == 202
    assert response.json()["token_status"] == "unchecked"
    assert response.json()["preparation_task"]["state"] == "queued"
    assert credentials.get(HF_SCOPE) == "hf_FIRST"
    assert client.get("/api/enhancement-settings").json()["has_hf_token"]
    assert "hf_FIRST" not in response.text
    for unchanged in ({}, {"hf_token": None}):
        retained = client.put("/api/huggingface-settings", json=unchanged)
        assert retained.status_code == 200
        assert retained.json() == response.json()
    assert credentials.get(HF_SCOPE) == "hf_FIRST"
    assert (
        client.put("/api/huggingface-settings", json={"hf_token": "bad\nsecret"}).status_code == 400
    )
    assert credentials.get(HF_SCOPE) == "hf_FIRST"

    checked = client.post("/api/huggingface-settings/check", json={})
    assert checked.status_code == 202
    assert checked.json() == response.json()
    assert client.get("/api/huggingface-settings").json() == checked.json()
    # A different unsaved token requires Save and cannot validate the saved credential.
    unsaved = client.post("/api/huggingface-settings/check", json={"hf_token": "hf_UNSAVED"})
    assert unsaved.status_code == 409 and "Save" in unsaved.json()["detail"]
    assert credentials.get(HF_SCOPE) == "hf_FIRST"
    assert client.get("/api/huggingface-settings").json() == checked.json()
    assert (
        client.put("/api/huggingface-settings", json={"hf_token": "hf_SECOND"}).status_code == 202
    )
    assert credentials.get(HF_SCOPE) == "hf_SECOND"
    assert client.get("/api/huggingface-settings").json()["token_status"] == "unchecked"
    assert client.put("/api/huggingface-settings", json={"hf_token": ""}).status_code == 200
    assert credentials.get(HF_SCOPE) == ""
    assert client.get("/api/huggingface-settings").json()["token_status"] == "missing"
    assert not client.get("/api/enhancement-settings").json()["has_hf_token"]
    missing = client.post("/api/huggingface-settings/check", json={})
    assert missing.status_code == 409 and "Save" in missing.json()["detail"]
