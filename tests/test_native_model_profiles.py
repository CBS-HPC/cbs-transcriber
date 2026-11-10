"""A native connection exposes catalog models without copying secrets or changing identities."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from admission_support import write_original_wav
from fastapi.testclient import TestClient

from cbs_transcriber.app import create_app
from cbs_transcriber.catalog import ucloud_models
from cbs_transcriber.providers import (
    Settings,
    backfill_native_profiles,
    credential_scope,
    profile,
    save_profile,
)
from cbs_transcriber.store import Store, new_id, now
from cbs_transcriber.ucloud_native import COMPUTE_ID, MIG_PRODUCT


def native_settings(**changes):
    values = {
        "provider_type": "ucloud",
        "base_url": "https://cloud.example.test",
        "model": "large-v3",
        "capability": "words_speakers",
        "language": "da",
        "approved_for": ["protected"],
        "ucloud_transcriber": {
            "project_id": "synthetic-project",
            "application": {"name": "transcriber", "version": "1.12"},
            "product": {"id": COMPUTE_ID, "category": "gpu", "provider": "synthetic"},
            "storage_path": "/synthetic-drive/transcriber",
            "input_parameter": "inputFile",
            "output_parameter": "outputDir",
            "format_parameter": "outputFormat",
            "cleanup_after_success": True,
        },
    }
    values.update(changes)
    return Settings.model_validate(values)


def raw_profile(store, settings, name="Existing native model"):
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, name, settings.model_dump_json(), now()),
    )
    return identifier


def native_rows(store):
    return [
        row
        for row in store.all("SELECT * FROM providers ORDER BY created,id")
        if json.loads(row["value"]).get("provider_type") == "ucloud"
    ]


def test_save_native_connection_exposes_each_catalog_model_through_api(client, credentials):
    settings = native_settings()
    response = client.post(
        "/api/providers",
        json={**settings.model_dump(), "name": "My native connection", "api_key": "synthetic-key"},
    )
    assert response.status_code == 201, response.text
    source_id = response.json()["id"]
    providers = [
        p
        for p in client.get("/api/providers").json()["providers"]
        if p["provider_type"] == "ucloud"
    ]
    catalog = ucloud_models()
    assert len(providers) == len(catalog)
    assert {p["model"] for p in providers} == {m["model"] for m in catalog}
    by_model = {p["model"]: p for p in providers}
    assert by_model[settings.model]["id"] == source_id
    assert by_model[settings.model]["name"] == "My native connection"
    for model in catalog:
        saved = by_model[model["model"]]
        assert saved["has_key"]
        assert saved["approved_for"] == ["protected"]
        assert saved["ucloud_transcriber"] == settings.ucloud_transcriber.model_dump()
        assert saved["capability"] == model["capability"]
        assert saved["enabled"] is (model.get("engine") != "whisperx")
        if model["model"] != settings.model:
            assert saved["name"] == model["name"]
    assert credentials.keys == {credential_scope(settings): "synthetic-key"}
    assert "synthetic-key" not in json.dumps(native_rows(client.app.state.store))
    store = client.app.state.store
    project = store.create_project("Synthetic protected project", "protected")
    recording = store.create_recording(project["id"], "sample.wav")
    write_original_wav(store, recording)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    # WhisperX models (ADR 0053) need a prepared CBS runtime; queue the native ones here.
    native = [p for p in providers if not p["model"].startswith("whisperx/")]
    with store.library_lock():
        queued = client.post(
            "/api/transcriptions",
            json={"recording_ids": [recording["id"]], "provider_ids": [p["id"] for p in native]},
        )
    assert queued.status_code == 202, queued.text
    # Configuration selection is accepted as metadata; this test never probes UCloud setup.
    assert {
        job["params"]["input_intent"]["accepted"]["provider"]["settings"]["model"]
        for job in queued.json()
    } == {p["model"] for p in native}
    assert all(job["state"] == "queued" for job in queued.json())
    assert all(job["params"]["input_intent"]["phase"] == "waiting" for job in queued.json())
    assert "synthetic-key" not in queued.text


def test_model_expansion_preserves_existing_settings_denials_and_selected_ids(client, credentials):
    store = client.app.state.store
    denied = native_settings(model="small", approved_for=[], language="en", concurrent_chunks=3)
    denied.ucloud_transcriber.hours = 7
    denied_id = raw_profile(store, denied, "Explicitly denied model")
    before_denied = store.one("SELECT * FROM providers WHERE id=?", (denied_id,))
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "sample.wav")
    store.execute("UPDATE recordings SET provider_id=? WHERE id=?", (denied_id, recording["id"]))
    job = store.add_job(
        recording["id"], "transcribe", {"settings": denied.model_dump(), "provider_id": denied_id}
    )
    frozen_before = store.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    default_before = client.get("/api/providers").json()["default_provider_id"]
    result = save_profile(store, credentials, "Configured", native_settings())
    assert result["id"] != denied_id
    assert store.one("SELECT * FROM providers WHERE id=?", (denied_id,)) == before_denied
    assert store.recording(recording["id"])["provider_id"] == denied_id
    assert store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) == frozen_before
    assert client.get("/api/providers").json()["default_provider_id"] == default_before
    assert len(native_rows(store)) == len(ucloud_models())
    assert all(json.loads(row["value"])["enabled"] for row in native_rows(store))


def test_new_connection_explicit_model_list_can_enable_runtime(client):
    settings = native_settings()
    response = client.post(
        "/api/providers",
        json={
            **settings.model_dump(),
            "name": "Explicit selection",
            "enabled_models": ["whisperx/edda-large"],
        },
    )
    assert response.status_code == 201, response.text
    providers = [
        p
        for p in client.get("/api/providers").json()["providers"]
        if p["provider_type"] == "ucloud"
    ]
    assert {p["model"] for p in providers if p["enabled"]} == {"whisperx/edda-large"}


def test_new_incomplete_connection_keeps_defaults_until_completed(client):
    store = client.app.state.store
    incomplete = native_settings()
    incomplete.ucloud_transcriber.storage_path = ""
    response = client.post("/api/providers", json={**incomplete.model_dump(), "name": "Incomplete"})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    assert (
        json.loads(store.one("SELECT value FROM providers WHERE id=?", (identifier,))["value"])[
            "_pending_runtime_defaults"
        ]
        is True
    )
    assert backfill_native_profiles(store) == 0
    complete = native_settings()
    response = client.put(
        f"/api/providers/{identifier}", json={**complete.model_dump(), "name": "Complete"}
    )
    assert response.status_code == 200, response.text
    payload = json.loads(
        store.one("SELECT value FROM providers WHERE id=?", (identifier,))["value"]
    )
    assert "_pending_runtime_defaults" not in payload
    profiles = [
        p
        for p in client.get("/api/providers").json()["providers"]
        if p["provider_type"] == "ucloud"
    ]
    assert all(p["enabled"] is not p["model"].startswith("whisperx/") for p in profiles)


def test_legacy_incomplete_connection_keeps_legacy_expansion_defaults(client, credentials):
    store = client.app.state.store
    incomplete = native_settings()
    incomplete.ucloud_transcriber.storage_path = ""
    identifier = raw_profile(store, incomplete, "Legacy incomplete")
    save_profile(store, credentials, "Complete", native_settings(), identifier=identifier)
    assert all(json.loads(row["value"])["enabled"] for row in native_rows(store))


def test_incomplete_connection_keeps_explicit_model_choices_on_completion(client):
    incomplete = native_settings()
    incomplete.ucloud_transcriber.storage_path = ""
    response = client.post(
        "/api/providers",
        json={
            **incomplete.model_dump(),
            "name": "Incomplete",
            "enabled_models": ["whisperx/edda-large"],
        },
    )
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    store = client.app.state.store
    assert backfill_native_profiles(store) == 0
    response = client.put(
        f"/api/providers/{identifier}",
        json={**native_settings().model_dump(exclude={"enabled"}), "name": "Complete"},
    )
    assert response.status_code == 200, response.text
    providers = [
        p
        for p in client.get("/api/providers").json()["providers"]
        if p["provider_type"] == "ucloud"
    ]
    assert {p["model"] for p in providers if p["enabled"]} == {"whisperx/edda-large"}


def test_startup_backfill_honours_new_connection_pending_defaults(client):
    store = client.app.state.store
    incomplete = native_settings()
    incomplete.ucloud_transcriber.storage_path = ""
    response = client.post("/api/providers", json={**incomplete.model_dump(), "name": "Pending"})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    payload = json.loads(
        store.one("SELECT value FROM providers WHERE id=?", (identifier,))["value"]
    )
    payload["ucloud_transcriber"]["storage_path"] = "/synthetic-drive/transcriber"
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), identifier))
    assert backfill_native_profiles(store) > 0
    rows = native_rows(store)
    assert all(
        json.loads(row["value"])["enabled"]
        is not json.loads(row["value"])["model"].startswith("whisperx/")
        for row in rows
    )
    payload = json.loads(
        store.one("SELECT value FROM providers WHERE id=?", (identifier,))["value"]
    )
    assert "_pending_runtime_defaults" not in payload
    assert backfill_native_profiles(store) == 0


@pytest.mark.parametrize("missing", ["project_id", "storage_path", "application", "product"])
def test_incomplete_native_setup_does_not_expand(client, credentials, missing):
    settings = native_settings()
    setattr(
        settings.ucloud_transcriber, missing, None if missing in {"application", "product"} else ""
    )
    save_profile(client.app.state.store, credentials, "Incomplete", settings)
    assert len(native_rows(client.app.state.store)) == 1


def test_other_compute_does_not_expand(client, credentials):
    settings = native_settings()
    settings.ucloud_transcriber.product["id"] = "legacy-gpu"
    save_profile(client.app.state.store, credentials, "Legacy", settings)
    assert len(native_rows(client.app.state.store)) == 1


def test_expansion_is_scoped_to_server_and_ucloud_project(client, credentials):
    store = client.app.state.store
    one = native_settings()
    another_project = native_settings()
    another_project.ucloud_transcriber.project_id = "other-project"
    another_server = native_settings(base_url="https://other.example.test")
    for settings in (one, another_project, another_server):
        save_profile(store, credentials, "Connection", settings)
    groups = {}
    for row in native_rows(store):
        settings = Settings.model_validate_json(row["value"])
        groups.setdefault((settings.base_url, settings.ucloud_transcriber.project_id), []).append(
            settings.model
        )
    expected = {m["model"] for m in ucloud_models()}
    assert len(groups) == 3
    assert all(
        len(models) == len(expected) and set(models) == expected for models in groups.values()
    )


def test_repeated_and_concurrent_saves_do_not_duplicate_auto_models(client, credentials):
    store = client.app.state.store
    settings = native_settings()
    identifier = raw_profile(store, settings)

    def save(_):
        return save_profile(
            store, credentials, "Connection", settings.model_copy(deep=True), identifier=identifier
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(save, range(8)))
    assert {result["id"] for result in results} == {identifier}
    rows = native_rows(store)
    assert (
        len(rows)
        == len({json.loads(row["value"])["model"] for row in rows})
        == len(ucloud_models())
    )


def test_native_source_save_and_model_expansion_roll_back_together(client, credentials):
    store = client.app.state.store
    settings = native_settings()
    identifier = raw_profile(store, settings)
    before = store.all("SELECT * FROM providers ORDER BY id")
    store.execute("""CREATE TRIGGER fail_native_expansion BEFORE INSERT ON providers
        WHEN json_extract(NEW.value,'$.model')='medium'
        BEGIN SELECT RAISE(ABORT,'synthetic expansion failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic expansion failure"):
        save_profile(store, credentials, "Changed name", settings, identifier=identifier)
    assert store.all("SELECT * FROM providers ORDER BY id") == before


def test_startup_backfills_offline_and_is_persistent_and_idempotent(tmp_path, credentials):
    store = Store(tmp_path)
    settings = native_settings(approved_for=[])
    identifier = raw_profile(store, settings)
    alias = native_settings(model="whisper/small", language="en", approved_for=["protected"])
    alias_id = raw_profile(store, alias, "Saved alias model")
    original = store.all("SELECT * FROM providers ORDER BY id")

    def forbidden_credentials(*args):
        pytest.fail("Offline startup must not inspect or save credentials")

    credentials.get = credentials.set = forbidden_credentials
    with TestClient(create_app(tmp_path, start_worker=False, credentials=credentials)):
        pass
    reopened = Store(tmp_path)
    rows = native_rows(reopened)
    # Keep the old alias and add the canonical catalog model ID.
    assert len(rows) == len(ucloud_models()) + 1
    for saved in original:
        current = reopened.one("SELECT * FROM providers WHERE id=?", (saved["id"],))
        if saved["id"] not in {identifier, alias_id}:
            assert current == saved
            continue
        assert {k: v for k, v in current.items() if k != "value"} == {
            k: v for k, v in saved.items() if k != "value"
        }
        payload = json.loads(current["value"])
        assert payload.pop("native_allocation_choice") == "mig1g"
        assert payload.pop("native_allocation_product") == MIG_PRODUCT
        assert payload == json.loads(saved["value"])
    assert profile(reopened, identifier)["settings"] == settings.model_dump()
    assert profile(reopened, alias_id)["settings"] == alias.model_dump()
    for row in rows:
        if row["id"] not in {identifier, alias_id}:
            assert json.loads(row["value"])["approved_for"] == []
            assert json.loads(row["value"])["enabled"] is True
    before_restart = reopened.all("SELECT * FROM providers ORDER BY id")
    with TestClient(create_app(tmp_path, start_worker=False, credentials=credentials)):
        pass
    assert Store(tmp_path).all("SELECT * FROM providers ORDER BY id") == before_restart
