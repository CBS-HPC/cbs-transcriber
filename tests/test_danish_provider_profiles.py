"""Fixed-Danish Runtime profiles stay distinct, offline and version guarded."""

import copy
import json

import pytest
from test_native_model_profiles import native_settings, raw_profile

from cbs_transcriber import catalog, danish_models, providers

DANISH = {"whisperx/" + choice for choice in danish_models.CHOICES}


def saved_row(store, identifier):
    return store.one("SELECT * FROM providers WHERE id=?", (identifier,))


def row_version(row):
    return providers.stored_version(row["name"], row["value"])


def test_fixed_danish_profiles_are_offline_exact_runtime_choices(client, monkeypatch):
    monkeypatch.setattr("httpx.Client.send", lambda *a, **k: pytest.fail("Offline catalog"))
    monkeypatch.setattr(
        danish_models, "prepare", lambda *a, **k: pytest.fail("Selection never prepares weights")
    )
    data = client.get("/api/providers/model-presets").json()
    assert data["ucloud_cbs_runtime"]["default"] == "whisperx/large-v3"
    assert data["ucloud_transcriber"]["default"] == "large-v3"
    assert data["openai"]["default"] == "gpt-transcribe"
    entries = {
        entry["model"]: entry
        for entry in data["ucloud_cbs_runtime"]["models"]
        if entry["model"] in DANISH
    }
    assert set(entries) == DANISH
    for model, entry in entries.items():
        assert entry["fixed_language"] == "da"
        assert entry["whisper_model"] in danish_models.CHOICES
        assert entry["name"].startswith("UCloud ") and entry["name"].endswith(
            "(CBS Runtime) (Danish)"
        )
        assert entry["engine"] == "whisperx" and entry["capability"] == "words"
        settings = native_settings(model=model)
        assert catalog.whisperx_model(settings) == entry["whisper_model"]
        assert catalog.fixed_runtime_language(settings) == "da"
        assert (
            catalog.fixed_runtime_language(settings.model_copy(update={"provider_type": "custom"}))
            is None
        )
    assert not DANISH.intersection(m["model"] for m in data["ucloud_transcriber"]["models"])
    assert catalog.fixed_runtime_language(native_settings(model="whisperx/large-v3")) is None


@pytest.mark.parametrize(
    "damage",
    [
        "missing-language",
        "english",
        "auto",
        "alias",
        "repository",
        "wrong-choice",
        "foreign",
        "reserved-identity",
    ],
)
def test_danish_catalog_metadata_cannot_redirect_or_change_language(tmp_path, monkeypatch, damage):
    document = copy.deepcopy(catalog.provider_catalog())
    entry = next(m for m in document["ucloud_cbs_runtime"]["models"] if m["model"] in DANISH)
    if damage == "missing-language":
        entry.pop("fixed_language")
    elif damage in {"english", "auto"}:
        entry["fixed_language"] = "en" if damage == "english" else "auto"
    elif damage == "alias":
        entry["model"] = "whisperx/custom-alias"
    elif damage == "repository":
        entry["whisper_model"] = "other-owner/arbitrary-repository"
    elif damage == "wrong-choice":
        entry["whisper_model"] = "small"
    elif damage == "reserved-identity":
        entry["whisper_model"] = "small"
        entry.pop("fixed_language")
    else:
        document["ucloud_cbs_runtime"]["models"][0]["fixed_language"] = "da"
    path = tmp_path / "models.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(path))
    catalog.provider_catalog.cache_clear()
    try:
        with pytest.raises(ValueError, match="Invalid provider model catalog"):
            catalog.provider_catalog()
    finally:
        catalog.provider_catalog.cache_clear()


def test_legacy_catalog_override_does_not_gain_unrequested_danish_models(tmp_path, monkeypatch):
    document = copy.deepcopy(catalog.provider_catalog())
    runtime = document.pop("ucloud_cbs_runtime")
    document["schema_version"] = 1
    existing = [entry for entry in runtime["models"] if entry["model"] not in DANISH]
    document["ucloud_transcriber"]["models"].extend(existing)
    path = tmp_path / "legacy.json"
    original = json.dumps(document, ensure_ascii=False)
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(path))
    catalog.provider_catalog.cache_clear()
    try:
        assert catalog.provider_catalog()["ucloud_cbs_runtime"]["models"] == existing
        assert not DANISH.intersection(entry["model"] for entry in catalog.ucloud_models())
        assert path.read_text(encoding="utf-8") == original
    finally:
        catalog.provider_catalog.cache_clear()


@pytest.mark.parametrize("language", ["", "en", "auto", " AUTO "])
def test_connection_fanout_creates_danish_profiles_with_danish_language(
    client, credentials, language
):
    store = client.app.state.store
    source = providers.save_profile(
        store,
        credentials,
        "Connection",
        native_settings(model="whisperx/large-v3", language=language),
    )
    profiles = providers.public_profiles(store, credentials)["providers"]
    selected = {entry["model"]: entry for entry in profiles if entry["model"] in DANISH}
    assert set(selected) == DANISH
    assert all(entry["language"] == "da" for entry in selected.values())
    assert source["language"] == language
    assert not credentials.keys


def test_backfill_preserves_existing_danish_denial_choices_and_frozen_work(client, credentials):
    store = client.app.state.store
    denied = native_settings(
        model="whisperx/edda-turbo", language="en", approved_for=[], enabled=False
    )
    identifier = raw_profile(store, denied, "Researcher's saved label")
    row = saved_row(store, identifier)
    payload = json.loads(row["value"])
    payload["future_field"] = {"literal": "preserve"}
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (json.dumps(payload, indent=2), identifier)
    )
    raw_profile(store, native_settings(model="whisperx/large-v3", language="auto"))
    project = store.create_project("Synthetic research", "protected")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.execute("UPDATE recordings SET provider_id=? WHERE id=?", (identifier, recording["id"]))
    frozen = store.add_job(
        recording["id"], "transcribe", {"provider_id": identifier, "settings": denied.model_dump()}
    )
    before, job = saved_row(store, identifier), store.job(frozen["id"])
    default = providers.default_provider_id(store)
    assert providers.backfill_native_profiles(store) > 0
    assert providers.backfill_native_profiles(store) == 0
    assert saved_row(store, identifier) == before
    assert store.job(frozen["id"]) == job
    assert store.recording(recording["id"])["provider_id"] == identifier
    assert providers.default_provider_id(store) == default
    for entry in providers.public_profiles(store, credentials)["providers"]:
        if entry["model"] in DANISH and entry["id"] != identifier:
            assert entry["language"] == "da"
    # Permissive historical reads never migrate a prior saved/frozen language.
    assert providers.Settings.model_validate(job["params"]["settings"]).language == "en"


@pytest.mark.parametrize("model", sorted(DANISH))
@pytest.mark.parametrize("language", ["", "en", "auto"])
def test_danish_runtime_language_api_rejects_incompatible_edits(client, model, language):
    store = client.app.state.store
    identifier = raw_profile(store, native_settings(model=model, approved_for=[], enabled=False))
    before = saved_row(store, identifier)
    response = client.put(
        f"/api/providers/{identifier}/runtime-language",
        json={"language": language, "expected_version": row_version(before)},
    )
    assert response.status_code == 400, response.text
    assert "Danish" in response.text
    assert saved_row(store, identifier) == before


def test_danish_language_writes_keep_version_and_denial_guards(client, credentials):
    store = client.app.state.store
    settings = native_settings(model="whisperx/edda-large", approved_for=[], enabled=False)
    identifier = raw_profile(store, settings)
    original = saved_row(store, identifier)
    stale = row_version(original)
    payload = json.loads(original["value"])
    payload["concurrent_chunks"] = 3
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), identifier))
    before = saved_row(store, identifier)
    for write in (
        lambda version: providers.save_runtime_language(
            store, credentials, identifier, "en", version
        ),
        lambda version: providers.save_profile(
            store,
            credentials,
            "Changed",
            settings.model_copy(update={"language": "en"}),
            identifier=identifier,
            expected_version=version,
        ),
    ):
        with pytest.raises(providers.StaleProvider):
            write(stale)
        with pytest.raises(ValueError, match="Danish"):
            write(row_version(before))
        assert saved_row(store, identifier) == before
    result = providers.save_runtime_language(
        store, credentials, identifier, "da", row_version(before)
    )
    assert saved_row(store, identifier) == before
    assert result["approved_for"] == [] and result["enabled"] is False
    assert not credentials.keys


@pytest.mark.parametrize("language", ["", "en", "auto"])
def test_explicit_new_generic_danish_profile_requires_danish(client, credentials, language):
    store = client.app.state.store
    settings = native_settings(model="whisperx/roest-v3", language=language)
    original = store.all("SELECT * FROM providers ORDER BY id")
    with pytest.raises(ValueError, match="Danish"):
        providers.save_profile(store, credentials, "Unsupported language", settings)
    assert store.all("SELECT * FROM providers ORDER BY id") == original
    assert providers.Settings.model_validate_json(settings.model_dump_json()).language == language
    assert not credentials.keys


def test_explicit_new_danish_profile_keeps_disabled_and_denied_choices(client, credentials):
    store = client.app.state.store
    settings = native_settings(
        model="whisperx/roest-v3", language="da", approved_for=[], enabled=False
    )
    returned = providers.save_profile(store, credentials, "Danish research model", settings)
    assert returned["language"] == "da"
    assert returned["approved_for"] == [] and returned["enabled"] is False
    assert returned["name"] == "Danish research model"
    profiles = providers.public_profiles(store, credentials)["providers"]
    assert DANISH <= {profile["model"] for profile in profiles}
    assert all(profile["language"] == "da" for profile in profiles if profile["model"] in DANISH)
    assert providers.default_provider_id(store) != returned["id"]
