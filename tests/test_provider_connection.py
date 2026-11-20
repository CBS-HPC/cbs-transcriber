"""Explicit mocked Settings checks gate new work without changing frozen work."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from admission_support import COMPATIBLE_API_URL, configure_compatible
from test_input_admission_api import uploaded
from test_native_model_profiles import native_settings, raw_profile

from cbs_transcriber import provider_connection, providers
from cbs_transcriber.providers import Settings


def models(monkeypatch, *listed):
    def check(settings, key):
        return {
            "models": [{"id": model, "name": model, "listed": True} for model in listed],
            "selected_model_listed": settings.model in listed,
            "note": "Synthetic model visibility only; no transcription.",
        }

    monkeypatch.setattr("cbs_transcriber.app.check_models", check)


def check_save(client, monkeypatch, *, key="synthetic-key", listed=None):
    saved = client.get("/api/settings").json()
    if not saved["service_available"]:
        saved = configure_compatible(client)
    models(monkeypatch, *(listed if listed is not None else (saved["model"],)))
    checked = client.post("/api/providers/check-models", json={**saved, "api_key": key})
    assert checked.status_code == 200, checked.text
    response = client.put(
        "/api/settings",
        json={
            **saved,
            "expected_version": saved["version"],
            "api_key": key,
            "connection_check": checked.json()["connection_check"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), checked.json()["connection_check"]


def test_fresh_presets_and_credentials_alone_do_not_enable_new_choices(client, credentials):
    initial = configure_compatible(client)
    assert initial["connection_state"] == "recheck_required"
    credentials.keys[COMPATIBLE_API_URL] = "synthetic-key"
    configured = client.get("/api/settings").json()
    assert configured["has_key"] and not configured["connection_ready"]
    assert configured["connection_state"] == "recheck_required"
    selected = client.put("/api/providers/default", json={"provider_id": initial["id"]})
    assert selected.status_code == 400 and "Settings" in selected.text
    assert client.get("/api/providers").status_code == 200


def test_check_is_not_a_save_and_only_advertised_models_become_ready(client, monkeypatch):
    providers.save_profile(
        client.app.state.store,
        client.app.state.credentials,
        "Unadvertised compatible model",
        Settings(provider_type="custom", base_url=COMPATIBLE_API_URL, model="not-advertised"),
    )
    advertised = {"whisper-1", client.get("/api/settings").json()["model"]}
    saved, _ = check_save(client, monkeypatch, listed=advertised)
    profiles = client.get("/api/providers").json()["providers"]
    assert saved["connection_ready"]
    assert all(p["connection_ready"] == (p["model"] in advertised) for p in profiles)
    assert all(
        p["connection_state"] == "model_not_listed" for p in profiles if not p["connection_ready"]
    )
    assert (
        client.put("/api/providers/default", json={"provider_id": saved["id"]}).status_code == 200
    )
    public = json.dumps(profiles)
    assert "fingerprint" not in public and "synthetic-key" not in public
    assert "connection_check" not in json.dumps(
        providers.profile(client.app.state.store, saved["id"])
    )


@pytest.mark.parametrize(
    "field,value", [("base_url", "https://other.invalid/v1"), ("api_key", "new-key")]
)
def test_check_proof_cannot_bless_changed_settings_or_credentials(
    client, monkeypatch, field, value
):
    models(monkeypatch, "whisper-1")
    before = configure_compatible(client)
    checked = client.post(
        "/api/providers/check-models", json={**before, "api_key": "checked-key"}
    ).json()
    response = client.put(
        "/api/settings",
        json={
            **before,
            "api_key": "checked-key",
            "connection_check": checked["connection_check"],
            field: value,
        },
    )
    assert response.status_code == 400
    assert client.get("/api/settings").json() == before
    assert not client.app.state.credentials.keys
    forged = client.put(
        "/api/settings", json={**before, "api_key": "checked-key", "connection_check": "invented"}
    )
    assert forged.status_code == 400 and not client.app.state.credentials.keys


def test_credential_changes_invalidate_shared_models_and_saved_proof_without_network(
    client, monkeypatch, credentials
):
    saved, _ = check_save(client, monkeypatch)
    credentials.keys[COMPATIBLE_API_URL] = "out-of-band-change"
    assert all(not p["connection_ready"] for p in client.get("/api/providers").json()["providers"])
    response = client.put("/api/settings", json={**saved, "api_key": "", "remember": False})
    assert response.status_code == 200 and response.json()["connection_state"] == "recheck_required"


def test_explicit_failure_blocks_new_work_but_wrong_draft_key_does_not(client, monkeypatch):
    saved, old_proof = check_save(client, monkeypatch)

    def fail(*args):
        raise ValueError("Synthetic unavailable endpoint.")

    monkeypatch.setattr("cbs_transcriber.app.check_models", fail)
    failed = client.post(
        "/api/providers/check-models", json={**saved, "api_key": "wrong-draft-key"}
    )
    assert failed.status_code == 400 and client.get("/api/settings").json()["connection_ready"]
    assert client.post("/api/providers/check-models", json=saved).status_code == 400
    unavailable = client.get("/api/settings").json()
    assert unavailable["connection_state"] == "unavailable"
    # A check only changes operational observations, never provider form versions.
    assert unavailable["version"] == saved["version"]
    replay = client.put("/api/settings", json={**saved, "connection_check": old_proof})
    assert replay.status_code == 400
    assert client.get("/api/settings").json()["connection_state"] == "unavailable"
    connected, _ = check_save(client, monkeypatch)
    assert connected["connection_ready"]


def test_unsaved_check_and_stale_check_responses_cannot_activate_saved_connection(
    client, monkeypatch
):
    models(monkeypatch, "whisper-1")
    saved = configure_compatible(client)
    assert (
        client.post(
            "/api/providers/check-models", json={**saved, "api_key": "draft-key"}
        ).status_code
        == 200
    )
    assert not client.get("/api/settings").json()["connection_ready"]
    checks = provider_connection.Checks()
    settings = Settings()
    first = checks.begin(settings)
    second = checks.begin(settings)
    with pytest.raises(ValueError, match="newer"):
        checks.issue(settings, "key", first)
    assert checks.issue(settings, "key", second)


def test_resolved_discovery_cannot_supersede_a_newer_saved_connection_check():
    checks = provider_connection.Checks()
    unresolved = Settings(provider_type="ucloud", base_url="https://cloud.invalid")
    resolved = native_settings()
    old_discovery = checks.begin(unresolved)
    checks.begin(resolved)
    with pytest.raises(ValueError, match="newer"):
        checks.issue(resolved, "key", old_discovery)


def test_ucloud_readiness_inherits_only_exact_checked_connection(client, monkeypatch, credentials):
    store = client.app.state.store
    settings = native_settings()
    good = raw_profile(store, settings, "Checked native")
    changed = settings.model_copy(deep=True)
    changed.ucloud_transcriber.storage_path += "-different"
    other = raw_profile(store, changed, "Different storage")
    credentials.keys[providers.credential_scope(settings)] = "synthetic-native-token"
    monkeypatch.setattr("cbs_transcriber.ucloud_native.check_setup", lambda *args: {"ready": True})
    checked = client.post("/api/providers/check-ucloud", json=settings.model_dump()).json()
    before = providers.public_profile(store, credentials, good)
    response = client.put(
        f"/api/providers/{good}",
        json={**before, "connection_check": checked["connection_check"]},
    )
    assert response.status_code == 200 and response.json()["connection_ready"]
    assert not providers.public_profile(store, credentials, other)["connection_ready"]
    assert providers.profile(store, good)["settings"] == settings.model_dump()


def test_successful_saved_receipt_survives_app_session_restart(client, monkeypatch, credentials):
    saved, _ = check_save(client, monkeypatch)
    # Receipt evaluation needs no token registry, runtime, network or current app session.
    assert providers.public_profile(client.app.state.store, credentials, saved["id"])[
        "connection_ready"
    ]
    assert provider_connection.Checks().tokens == {}


def test_unchecked_new_enqueue_rejected_then_checked_enqueue_freezes_no_receipt(
    client, monkeypatch, wav_bytes
):
    recording = uploaded(client, wav_bytes)
    store = client.app.state.store
    assert (
        client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]}).status_code
        == 400
    )
    saved, _ = check_save(client, monkeypatch)
    with store.library_lock():
        response = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
    assert response.status_code == 202, response.text
    params = response.json()[0]["params"]
    assert "connection_" not in json.dumps(params) and "fingerprint" not in json.dumps(params)
    client.app.state.credentials.keys[COMPATIBLE_API_URL] = "changed-after-acceptance"
    assert store.job(response.json()[0]["id"])["params"] == params
    assert saved["connection_ready"]


def test_keyless_deferred_custom_profile_remains_readable(client):
    response = client.post(
        "/api/providers",
        json={
            "name": "Legacy local",
            "provider_type": "custom",
            "base_url": "http://localhost:9000/v1",
            "model": "custom",
        },
    )
    assert response.status_code == 201
    assert response.json()["connection_state"] == "recheck_required"
    assert client.get("/api/providers").status_code == 200


def test_newer_failure_wins_even_when_earlier_save_is_in_credential_store(
    client, monkeypatch, credentials
):
    saved, _ = check_save(client, monkeypatch)
    # Begin a second successful check whose Save is held after publishing its DB row.
    checked = client.post("/api/providers/check-models", json=saved).json()
    entered_save = threading.Event()
    release_save = threading.Event()
    failed_check = threading.Event()
    set_key = credentials.set

    def held_set(*args):
        entered_save.set()
        assert release_save.wait(10)
        return set_key(*args)

    def fail(*args):
        failed_check.set()
        raise ValueError("Synthetic newer failure.")

    monkeypatch.setattr(credentials, "set", held_set)
    monkeypatch.setattr("cbs_transcriber.app.check_models", fail)
    with ThreadPoolExecutor(2) as threads:
        saving = threads.submit(
            client.put,
            "/api/settings",
            json={
                **saved,
                "api_key": "synthetic-key",
                "connection_check": checked["connection_check"],
            },
        )
        assert entered_save.wait(10)
        failing = threads.submit(client.post, "/api/providers/check-models", json=saved)
        try:
            assert failed_check.wait(10)
        finally:
            release_save.set()
        assert saving.result(10).status_code == 200
        assert failing.result(10).status_code == 400
    assert client.get("/api/settings").json()["connection_state"] == "unavailable"


@pytest.mark.parametrize(
    "field,corrupt",
    [
        ("models", None),
        ("models", "whisper-1"),
        ("models", {"model": "whisper-1"}),
        ("models", [None]),
        ("models", ["x" * 201]),
        ("state", []),
        ("state", {}),
    ],
)
def test_malformed_saved_model_observation_fails_closed(client, monkeypatch, field, corrupt):
    saved, _ = check_save(client, monkeypatch)
    store = client.app.state.store
    records = json.loads(store.one("SELECT value FROM settings WHERE id=10")["value"])
    settings = Settings.model_validate(saved)
    records[provider_connection.identity(settings)][field] = corrupt
    store.execute("UPDATE settings SET value=? WHERE id=10", (json.dumps(records),))
    result = client.get("/api/settings")
    assert result.status_code == 200
    assert result.json()["connection_state"] == "recheck_required"


@pytest.mark.parametrize("raw", ["null", "0", "[]", "{", "[" * 1500 + "0" + "]" * 1500])
def test_malformed_operational_settings_fail_closed(client, credentials, raw):
    configure_compatible(client)
    credentials.keys[COMPATIBLE_API_URL] = "synthetic-key"
    client.app.state.store.execute("INSERT INTO settings VALUES(10,?)", (raw,))
    response = client.get("/api/providers")
    assert response.status_code == 200
    assert all(not provider["connection_ready"] for provider in response.json()["providers"])
