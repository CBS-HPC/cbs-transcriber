"""Discard is an explicit same-origin whole-allocation operation, never a cleanup alias."""

import pytest

from cbs_transcriber import runtime_discard


@pytest.fixture
def service(monkeypatch):
    calls = []

    def preview(store, credentials, identifier):
        calls.append(("preview", identifier))
        return {"runtime_batch_id": identifier, "fingerprint": "a" * 64, "members": []}

    def confirm(store, credentials, identifier, **kwargs):
        calls.append(("confirm", identifier, kwargs))
        return {"runtime_batch_id": identifier, "cleanup_state": "trashed"}

    monkeypatch.setattr(runtime_discard, "preview", preview)
    monkeypatch.setattr(runtime_discard, "confirm", confirm)
    return calls


def test_discard_requires_preview_identity_and_explicit_confirmation(client, service):
    identifier = "b" * 32
    response = client.post(f"/api/runtime-batches/{identifier}/discard-preview")
    assert response.status_code == 200
    assert response.json()["fingerprint"] == "a" * 64
    response = client.post(
        f"/api/runtime-batches/{identifier}/discard",
        json={"confirmed": True, "fingerprint": "a" * 64},
    )
    assert response.status_code == 200
    assert response.json()["cleanup_state"] == "trashed"
    assert service == [
        ("preview", identifier),
        ("confirm", identifier, {"confirmed": True, "fingerprint": "a" * 64}),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"fingerprint": "a" * 64},
        {"confirmed": False, "fingerprint": "a" * 64},
        {"confirmed": 1, "fingerprint": "a" * 64},
        {"confirmed": "true", "fingerprint": "a" * 64},
        {"confirmed": True, "fingerprint": "A" * 64},
        {"confirmed": True, "fingerprint": "a" * 63},
        {"confirmed": True, "fingerprint": "g" * 64},
        {"confirmed": True, "fingerprint": "a" * 64 + "\n"},
        {"confirmed": True, "fingerprint": "a" * 64, "job_id": "one-file-only"},
    ],
)
def test_invalid_discard_cannot_reach_service(client, service, body):
    response = client.post("/api/runtime-batches/synthetic/discard", json=body)
    assert response.status_code == 422
    assert service == []


@pytest.mark.parametrize("action", ["discard-preview", "discard"])
def test_discard_boundary_rejects_cross_origin(client, service, action):
    response = client.post(
        f"/api/runtime-batches/synthetic/{action}",
        headers={"Origin": "https://unrelated.invalid"},
        json={"confirmed": True, "fingerprint": "a" * 64},
    )
    assert response.status_code == 403
    assert service == []


@pytest.mark.parametrize("action", ["discard-preview", "discard"])
def test_discard_is_never_a_get_action(client, service, action):
    assert client.get(f"/api/runtime-batches/synthetic/{action}").status_code == 405
    assert service == []


@pytest.mark.parametrize("action", ["discard-preview", "discard"])
def test_discard_boundary_requires_local_request_marker(client, service, action):
    response = client.post(
        f"/api/runtime-batches/synthetic/{action}",
        headers={"X-CBS-Request": ""},
        json={"confirmed": True, "fingerprint": "a" * 64},
    )
    assert response.status_code == 403
    assert service == []
