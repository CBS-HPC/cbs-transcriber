"""Rejected/stale provider saves cannot change policy, targets or setup credentials."""

import json

import pytest

from cbs_transcriber import providers


def vllm_payload():
    return {
        "name": "Synthetic vLLM",
        "provider_type": "ucloud-vllm",
        "base_url": "https://speech.invalid/v1",
        "model": "org/model",
        "ucloud": {
            "server": "https://cloud.invalid",
            "project_id": "project",
            "hf_model": "org/model",
        },
    }


def test_default_settings_honors_a_supplied_version_before_restoring_policy_or_key(
    client, credentials
):
    loaded = client.get("/api/settings").json()
    approved = client.put(
        "/api/providers/" + loaded["id"],
        json={**loaded, "approved_for": ["protected"], "expected_version": loaded["version"]},
    ).json()
    revoked = client.put(
        "/api/providers/" + loaded["id"],
        json={**approved, "approved_for": [], "expected_version": approved["version"]},
    )
    assert revoked.status_code == 200
    credentials.keys[loaded["base_url"]] = "current-key"
    stale = client.put(
        "/api/settings",
        json={
            **approved,
            "language": "da",
            "expected_version": approved["version"],
            "api_key": "stale-key",
        },
    )
    assert stale.status_code == 409, stale.text
    assert client.get("/api/settings").json() == revoked.json() | {"has_key": True}
    assert credentials.keys[loaded["base_url"]] == "current-key"
    fresh = client.put(
        "/api/settings",
        json={**revoked.json(), "language": "da", "expected_version": revoked.json()["version"]},
    )
    assert fresh.status_code == 200 and fresh.json()["approved_for"] == []


def test_default_switch_during_save_cannot_retarget_the_selected_profile(client, monkeypatch):
    store = client.app.state.store
    first = client.get("/api/settings").json()
    second = client.post(
        "/api/providers",
        json={
            "name": "Other target",
            "provider_type": "custom",
            "base_url": "https://other.invalid/v1",
            "model": "other",
        },
    ).json()
    second_before = providers.profile(store, second["id"])
    choose = providers.default_provider_id
    selected = []

    def interleave(store):
        identifier = choose(store)
        if not selected:
            selected.append(identifier)
            providers.set_default_provider(store, second["id"])
        return identifier

    monkeypatch.setattr(providers, "default_provider_id", interleave)
    saved = client.put(
        "/api/settings",
        json={
            "provider_type": "openai",
            "base_url": first["base_url"],
            "model": first["model"],
            "language": "da",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] == first["id"]
    assert providers.profile(store, second["id"]) == second_before
    assert choose(store) == second["id"]


@pytest.mark.parametrize(
    "operation,reason",
    [
        ("create", "name"),
        ("create", "models"),
        ("create", "endpoint"),
        ("named", "name"),
        ("named", "models"),
        ("named", "endpoint"),
        ("named", "stale"),
        ("default", "endpoint"),
        ("default", "stale"),
    ],
)
def test_rejected_provider_save_preserves_all_rows_and_credentials(
    client, credentials, operation, reason
):
    store = client.app.state.store
    payload = vllm_payload()
    if operation != "create":
        created = client.post("/api/providers", json=payload)
        assert created.status_code == 201, created.text
        payload = created.json()
        payload["expected_version"] = payload["version"]
        if reason == "stale":
            newer = client.put(
                "/api/providers/" + payload["id"], json={**payload, "language": "en"}
            )
            assert newer.status_code == 200
        if operation == "default":
            providers.set_default_provider(store, payload["id"])
    if reason == "name":
        payload["name"] = "   "
    elif reason == "models":
        payload["enabled_models"] = ["not-a-configured-model"]
    elif reason == "endpoint":
        payload["base_url"] = ""
    credentials.keys.update(
        {
            "ucloud-control:https://cloud.invalid": "current-control",
            "huggingface:https://huggingface.co": "current-hf",
            "https://speech.invalid/v1": "current-inference",
        }
    )
    rows = store.all("SELECT * FROM providers ORDER BY id")
    keys = dict(credentials.keys)
    payload.update(
        api_key="rejected-inference", ucloud_token="rejected-control", hf_token="rejected-hf"
    )
    if operation == "create":
        response = client.post("/api/providers", json=payload)
    else:
        response = client.put(
            "/api/settings" if operation == "default" else "/api/providers/" + payload["id"],
            json=payload,
        )
    assert response.status_code == (409 if reason == "stale" else 400), response.text
    assert store.all("SELECT * FROM providers ORDER BY id") == rows
    assert credentials.keys == keys


@pytest.mark.parametrize("remember", [False, True])
def test_valid_setup_save_scopes_tokens_preserves_omissions_and_supports_explicit_forget(
    client, credentials, monkeypatch, remember
):
    calls = []
    save = credentials.set

    def record(scope, value, persist):
        calls.append((scope, value, persist))
        save(scope, value, persist)

    monkeypatch.setattr(credentials, "set", record)
    created = client.post(
        "/api/providers",
        json={
            **vllm_payload(),
            "api_key": "synthetic-inference",
            "ucloud_token": "synthetic-control",
            "hf_token": "synthetic-hf",
            "remember": remember,
        },
    )
    assert created.status_code == 201, created.text
    current = created.json()
    assert set(calls) == {
        ("https://speech.invalid/v1", "synthetic-inference", remember),
        ("ucloud-control:https://cloud.invalid", "synthetic-control", remember),
        ("huggingface:https://huggingface.co", "synthetic-hf", remember),
    }
    persisted = json.dumps(client.app.state.store.all("SELECT * FROM providers"))
    assert all(
        token not in persisted + created.text
        for token in ("synthetic-inference", "synthetic-control", "synthetic-hf")
    )
    unchanged = client.put(
        "/api/providers/" + current["id"],
        json={**current, "expected_version": current["version"], "language": "da"},
    )
    assert unchanged.status_code == 200 and len(calls) == 3
    current = unchanged.json()
    forgotten = client.put(
        "/api/providers/" + current["id"],
        json={
            **current,
            "expected_version": current["version"],
            "ucloud_token": "",
            "hf_token": "",
            "remember": remember,
        },
    )
    assert forgotten.status_code == 200
    assert credentials.keys == {
        "https://speech.invalid/v1": "synthetic-inference",
        "ucloud-control:https://cloud.invalid": "",
        "huggingface:https://huggingface.co": "",
    }
