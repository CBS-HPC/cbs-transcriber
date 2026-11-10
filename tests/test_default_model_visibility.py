"""First-use defaults retain explicit and historical provider choices."""

import sqlite3

from test_native_model_profiles import raw_profile

from cbs_transcriber.catalog import provider_catalog, ucloud_models
from cbs_transcriber.providers import Settings, load_settings
from cbs_transcriber.store import Store


def test_new_library_starts_with_recommended_openai_without_changing_legacy_defaults(tmp_path):
    store = Store(tmp_path / "new-library")
    assert load_settings(store).model == "gpt-transcribe"
    assert load_settings(store).capability == "json"
    assert Settings.model_validate({}).model == "whisper-1"


def test_existing_schema_one_library_without_settings_keeps_legacy_default(tmp_path):
    library = tmp_path / "legacy-library"
    library.mkdir()
    with sqlite3.connect(library / "library.sqlite3") as db:
        db.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE recordings (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                name TEXT NOT NULL, filename TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'audio',
                status TEXT NOT NULL DEFAULT 'preparing', duration REAL,
                segments TEXT NOT NULL DEFAULT '[]', speakers TEXT NOT NULL DEFAULT '{}',
                revision INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                params TEXT NOT NULL DEFAULT '{}', chunks TEXT NOT NULL DEFAULT '[]',
                created TEXT NOT NULL, updated TEXT NOT NULL
            );
            CREATE TABLE settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            PRAGMA user_version=1;
        """)
    assert load_settings(Store(library)).model == "whisper-1"


def test_builtin_visibility_defaults_are_explicit_and_danish_models_are_identified():
    openai = provider_catalog()["openai"]["models"]
    cloud = ucloud_models()
    assert {m["model"] for m in openai if m.get("default_enabled")} == {"gpt-transcribe"}
    assert {m["model"] for m in cloud if m.get("default_enabled")} == {"large-v3"}
    runtime = provider_catalog()["ucloud_cbs_runtime"]["models"]
    assert runtime and all(m.get("default_enabled") is False for m in runtime)
    assert all(m["name"].endswith("(Danish)") for m in cloud if m.get("fixed_language") == "da")


def test_model_delta_preserves_untouched_duplicates_and_rejects_stale_versions(client):
    store = client.app.state.store
    source = client.get("/api/settings").json()
    duplicate = raw_profile(
        store,
        Settings(provider_type="openai", model="gpt-transcribe", enabled=False),
        "Disabled duplicate",
    )
    body = {
        "name": source["name"],
        "provider_type": "openai",
        "model": source["model"],
        "expected_version": source["version"],
        "enabled_models": ["gpt-transcribe"],
        "enabled_models_touched": ["whisper-1"],
        "enabled_model_versions": {},
    }
    assert client.put(f"/api/providers/{source['id']}", json=body).status_code == 200
    duplicate_before = store.one("SELECT * FROM providers WHERE id=?", (duplicate,))
    assert Settings.model_validate_json(duplicate_before["value"]).enabled is False

    profiles = client.get("/api/providers").json()["providers"]
    versions = {p["id"]: p["version"] for p in profiles if p["model"] == "gpt-transcribe"}
    current = next(p for p in profiles if p["id"] == source["id"])
    body.update(
        expected_version=current["version"],
        enabled_models_touched=["gpt-transcribe"],
        enabled_model_versions=versions,
    )
    changed = Settings.model_validate_json(duplicate_before["value"]).model_copy(
        update={"language": "en"}
    )
    store.execute("UPDATE providers SET value=? WHERE id=?", (changed.model_dump_json(), duplicate))
    before = store.all("SELECT * FROM providers ORDER BY id")
    assert client.put(f"/api/providers/{source['id']}", json=body).status_code == 409
    assert store.all("SELECT * FROM providers ORDER BY id") == before

    profiles = client.get("/api/providers").json()["providers"]
    body["enabled_model_versions"] = {
        p["id"]: p["version"] for p in profiles if p["model"] == "gpt-transcribe"
    }
    raw_profile(store, Settings(provider_type="openai", model="gpt-transcribe"), "New duplicate")
    before = store.all("SELECT * FROM providers ORDER BY id")
    assert client.put(f"/api/providers/{source['id']}", json=body).status_code == 409
    assert store.all("SELECT * FROM providers ORDER BY id") == before
