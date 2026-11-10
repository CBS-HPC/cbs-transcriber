"""Enabled model choices affect new requests, preserving prior configurations and work."""

import json
import sqlite3

import pytest
from admission_support import inspect_and_materialize, write_original_wav
from test_native_model_profiles import native_settings, raw_profile

from cbs_transcriber import media, providers
from cbs_transcriber.catalog import provider_catalog
from cbs_transcriber.providers import OPENAI_URL, Settings, save_profile


def by_model(client, provider_type="openai", server=OPENAI_URL, project=None):
    return {
        p["model"]: p
        for p in client.get("/api/providers").json()["providers"]
        if p["provider_type"] == provider_type
        and p["base_url"] == server
        and (project is None or p["ucloud_transcriber"]["project_id"] == project)
    }


def test_openai_connection_saves_all_catalog_models_and_only_selected_are_enabled(
    client, credentials
):
    response = client.post(
        "/api/providers",
        json={
            "name": "OpenAI research",
            "provider_type": "openai",
            "model": "gpt-transcribe",
            "api_key": "synthetic-secret",
            "enabled_models": ["gpt-transcribe", "whisper-1"],
        },
    )
    assert response.status_code == 201, response.text
    saved = by_model(client)
    assert set(saved) == {m["model"] for m in provider_catalog()["openai"]["models"]}
    assert {m for m, p in saved.items() if p["enabled"]} == {"gpt-transcribe", "whisper-1"}
    assert all(p["has_key"] for p in saved.values())
    assert credentials.keys == {OPENAI_URL: "synthetic-secret"}
    assert "enabled_models" not in client.app.state.store.db_path.read_bytes().decode("latin1")
    assert "synthetic-secret" not in json.dumps(
        client.app.state.store.all("SELECT * FROM providers")
    )


def test_omitted_selection_and_enabled_field_preserve_existing_flags(client, credentials):
    store = client.app.state.store
    original = client.get("/api/providers").json()["providers"][0]
    before_settings = client.get("/api/settings").json()
    assert (
        client.put(
            f"/api/providers/{original['id']}",
            json={
                "name": original["name"],
                "provider_type": "openai",
                "model": "whisper-1",
                "enabled_models": [],
            },
        ).status_code
        == 200
    )
    before = store.all("SELECT * FROM providers ORDER BY id")
    assert not any(p["enabled"] for p in by_model(client).values())
    assert (
        client.put(
            f"/api/providers/{original['id']}",
            json={"name": original["name"], "provider_type": "openai", "model": "whisper-1"},
        ).status_code
        == 200
    )
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    save_profile(store, credentials, original["name"], Settings(), identifier=original["id"])
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert client.put("/api/settings", json={"model": "whisper-1"}).status_code == 200
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert client.get("/api/settings").json()["id"] == before_settings["id"]


def test_checklist_updates_flags_but_preserves_existing_settings_approvals_and_references(client):
    store = client.app.state.store
    settings = Settings(
        provider_type="openai",
        model="experimental-speech",
        capability="segments",
        enabled=False,
        approved_for=["protected"],
        language="da",
    )
    legacy_id = raw_profile(store, settings, "Research voice")
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "sample.wav")
    store.execute("UPDATE recordings SET provider_id=? WHERE id=?", (legacy_id, recording["id"]))
    default_id = client.get("/api/settings").json()["id"]
    before = store.one("SELECT * FROM providers WHERE id=?", (legacy_id,))
    response = client.put(
        f"/api/providers/{default_id}",
        json={
            "name": "Default",
            "provider_type": "openai",
            "enabled_models": ["experimental-speech"],
        },
    )
    assert response.status_code == 200, response.text
    after = store.one("SELECT * FROM providers WHERE id=?", (legacy_id,))
    expected = json.loads(before["value"])
    expected["enabled"] = True
    assert json.loads(after["value"]) == expected
    assert {k: after[k] for k in ("id", "name", "created")} == {
        k: before[k] for k in ("id", "name", "created")
    }
    assert store.recording(recording["id"])["provider_id"] == legacy_id
    assert client.get("/api/settings").json()["id"] == default_id
    assert not by_model(client)["whisper-1"]["enabled"]


@pytest.mark.parametrize(
    "enabled_models",
    [
        ["whisper-1", "whisper-1"],
        ["not-a-saved-model"],
        [1],
        "whisper-1",
        ["x" * 201],
        ["x" + str(i) for i in range(101)],
    ],
)
def test_invalid_model_checklists_do_not_change_rows_or_credentials(
    client, credentials, enabled_models
):
    store = client.app.state.store
    before = store.all("SELECT * FROM providers ORDER BY id")
    response = client.post(
        "/api/providers",
        json={
            "name": "Invalid",
            "provider_type": "openai",
            "enabled_models": enabled_models,
            "api_key": "do-not-save",
        },
    )
    assert response.status_code in {400, 422}
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert credentials.keys == {}


def test_native_checklists_are_isolated_by_server_and_project(client, credentials):
    store = client.app.state.store
    first = native_settings()
    second = native_settings()
    second.ucloud_transcriber.project_id = "other-project"
    third = native_settings(base_url="https://other.example.test")
    saved = [
        save_profile(store, credentials, "Native", settings) for settings in (first, second, third)
    ]
    assert (
        client.put(
            f"/api/providers/{saved[0]['id']}",
            json={**first.model_dump(), "name": "Native", "enabled_models": ["medium"]},
        ).status_code
        == 200
    )
    assert {
        m
        for m, p in by_model(
            client, "ucloud", first.base_url, first.ucloud_transcriber.project_id
        ).items()
        if p["enabled"]
    } == {"medium"}
    for settings in (second, third):
        assert all(
            p["enabled"] is not p["model"].startswith("whisperx/")
            for p in by_model(
                client, "ucloud", settings.base_url, settings.ucloud_transcriber.project_id
            ).values()
        )


def test_explicit_empty_checklist_disables_unknown_saved_models_too(client):
    store = client.app.state.store
    legacy_id = raw_profile(store, Settings(provider_type="openai", model="experimental-speech"))
    source = client.get("/api/settings").json()
    response = client.put(
        f"/api/providers/{source['id']}", json={"name": source["name"], "enabled_models": []}
    )
    assert response.status_code == 200
    assert not next(p for p in by_model(client).values() if p["id"] == legacy_id)["enabled"]


def test_model_flags_and_source_save_rollback_if_a_later_flag_update_fails(client, credentials):
    store = client.app.state.store
    source = client.get("/api/settings").json()
    assert (
        client.put(f"/api/providers/{source['id']}", json={"name": source["name"]}).status_code
        == 200
    )
    store.execute("""CREATE TRIGGER fail_model_flag BEFORE UPDATE ON providers
        WHEN json_extract(NEW.value,'$.model')='gpt-4o-transcribe'
        AND json_extract(NEW.value,'$.enabled')=0
        BEGIN SELECT RAISE(ABORT,'synthetic flag failure'); END""")
    before = store.all("SELECT * FROM providers ORDER BY id")
    credentials.keys[OPENAI_URL] = "previous-synthetic-key"
    with pytest.raises(sqlite3.IntegrityError, match="synthetic flag failure"):
        client.put(
            f"/api/providers/{source['id']}",
            json={"name": "Changed", "enabled_models": [], "api_key": "do-not-save"},
        )
    assert store.all("SELECT * FROM providers ORDER BY id") == before
    assert credentials.keys == {OPENAI_URL: "previous-synthetic-key"}


@pytest.mark.parametrize("legacy_snapshot", [False, True])
def test_disabled_models_reject_new_transcription_and_selection_but_frozen_work_retries(
    client, credentials, monkeypatch, legacy_snapshot
):
    store = client.app.state.store
    source = client.get("/api/settings").json()
    credentials.keys[OPENAI_URL] = "synthetic-key"
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "sample.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    with store.library_lock():
        if legacy_snapshot:
            # Seed the old frozen contract before insertion; never rewrite a fresh receipt.
            settings = Settings.model_validate(source).model_dump()
            settings.pop("enabled")
            queued = store.add_job(
                recording["id"],
                "transcribe",
                {
                    "provider_id": source["id"],
                    "provider_name": source["name"],
                    "settings": settings,
                },
            )
        else:
            accepted = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
            assert accepted.status_code == 202, accepted.text
            queued = inspect_and_materialize(store, credentials, [accepted.json()[0]["id"]])[0]
        frozen = store.one("SELECT params FROM jobs WHERE id=?", (queued["id"],))
        response = client.put(
            f"/api/providers/{source['id']}", json={"name": source["name"], "enabled_models": []}
        )
        assert response.status_code == 200
        assert store.one("SELECT params FROM jobs WHERE id=?", (queued["id"],)) == frozen
        for path, method, payload in (
            (
                "/api/transcriptions",
                "post",
                {"recording_ids": [recording["id"]], "provider_ids": [source["id"]]},
            ),
            ("/api/providers/default", "put", {"provider_id": source["id"]}),
            (f"/api/recordings/{recording['id']}/provider", "patch", {"provider_id": source["id"]}),
        ):
            rejected = getattr(client, method)(path, json=payload)
            assert rejected.status_code == 400, rejected.text
            assert "disabled" in rejected.json()["detail"].lower()
        store.update_job(queued["id"], state="failed")
        assert client.post(f"/api/jobs/{queued['id']}/retry", json={}).status_code == 200

        if legacy_snapshot:

            def prepare(directory, job_id, *args):
                folder = directory / "chunks" / job_id
                folder.mkdir(parents=True, exist_ok=True)
                return folder, [{"file": "part.mp3", "offset": 0}]

            monkeypatch.setattr(media, "prepare_chunks", prepare)
        monkeypatch.setattr(providers, "transcribe_file", lambda *a, **kw: {"text": "Synthetic."})
        client.app.state.queue.process(queued["id"])
        assert len(store.results(recording["id"])) == 1
        assert store.job(queued["id"])["params"]["settings"] == queued["params"]["settings"]


def test_old_rows_default_to_enabled_without_rewriting_the_database(client):
    store = client.app.state.store
    source = client.get("/api/settings").json()
    raw = json.loads(store.one("SELECT value FROM providers WHERE id=?", (source["id"],))["value"])
    raw.pop("enabled", None)
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(raw), source["id"]))
    before = store.one("SELECT value FROM providers WHERE id=?", (source["id"],))
    assert client.get("/api/settings").json()["enabled"] is True
    assert store.one("SELECT value FROM providers WHERE id=?", (source["id"],)) == before


def test_incomplete_native_profile_can_disable_models_without_setup_or_expansion(client):
    store = client.app.state.store
    settings = native_settings()
    settings.ucloud_transcriber.storage_path = ""
    identifier = raw_profile(store, settings)
    response = client.put(
        f"/api/providers/{identifier}",
        json={**settings.model_dump(), "name": "Incomplete", "enabled_models": []},
    )
    assert response.status_code == 200
    saved = by_model(client, "ucloud", settings.base_url)
    assert list(saved) == [settings.model]
    assert not saved[settings.model]["enabled"]


def test_flags_persist_across_restart_and_fanout_does_not_restore_disabled_existing_models(
    client, credentials
):
    from fastapi.testclient import TestClient

    from cbs_transcriber.app import create_app

    store = client.app.state.store
    original = client.get("/api/settings").json()
    response = client.put(
        f"/api/providers/{original['id']}",
        json={"name": original["name"], "enabled_models": ["whisper-1"]},
    )
    assert response.status_code == 200
    before = store.all("SELECT * FROM providers ORDER BY id")
    with TestClient(
        create_app(store.root, start_worker=False, credentials=credentials)
    ) as restarted:
        assert {
            p["model"] for p in restarted.get("/api/providers").json()["providers"] if p["enabled"]
        } == {"whisper-1"}
    assert store.all("SELECT * FROM providers ORDER BY id") == before


def test_new_native_clones_default_enabled_even_if_the_source_is_disabled(client, credentials):
    store = client.app.state.store
    settings = native_settings()
    settings.enabled = False
    result = save_profile(store, credentials, "Disabled source", settings)
    saved = by_model(client, "ucloud", settings.base_url)
    assert not saved[settings.model]["enabled"]
    assert all(
        p["enabled"] is not p["model"].startswith("whisperx/")
        for p in saved.values()
        if p["id"] != result["id"]
    )


@pytest.mark.parametrize("kind", ["custom", "ucloud-vllm"])
def test_checklist_cannot_change_custom_or_vllm_groups(client, kind):
    settings = {
        "provider_type": kind,
        "name": "Other backend",
        "base_url": "https://inference.example.test/v1",
        "model": "research/speech",
        "enabled_models": [],
    }
    before = client.app.state.store.all("SELECT * FROM providers")
    assert client.post("/api/providers", json=settings).status_code == 400
    assert client.app.state.store.all("SELECT * FROM providers") == before
