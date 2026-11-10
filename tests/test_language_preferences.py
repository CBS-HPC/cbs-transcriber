"""Language inheritance remains explicit, revisioned and frozen at acceptance."""

import json

import pytest
from admission_support import inspect_and_materialize, write_original_wav
from test_input_admission import native_provider, runtime_plan

from cbs_transcriber import enhancement_api, input_admission, language_preferences, providers
from cbs_transcriber import store as store_module
from cbs_transcriber.runtime_compute import MIG_PRODUCT
from cbs_transcriber.store import Store, new_id, now


@pytest.mark.parametrize("old_version", [13, 14, 15, 16])
def test_old_schema_migration_preserves_rows_and_starts_with_unset_language(
    tmp_path, monkeypatch, old_version
):
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", old_version)
    root = tmp_path / "library"
    old = Store(root)
    project = old.create_project("Existing")
    recording_id = new_id()
    with old.connection() as db:
        old._create_recording(db, project["id"], "one.wav", recording_id)
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 17)
    new = Store(root)
    assert new.one(
        "SELECT default_language,language_revision FROM projects WHERE id=?", (project["id"],)
    ) == {
        "default_language": None,
        "language_revision": 0,
    }
    row = new.recording(recording_id)
    assert row["language_override"] is None and row["language_revision"] == 0
    assert list((root / "backups").glob(f"schema-{old_version}-*.sqlite3"))


def test_preferences_inherit_and_revisions_preserve_existing_jobs(tmp_path, credentials):
    store = Store(tmp_path / "library")
    project = store.create_project("Languages", default_language="da")
    recording = store.create_recording(project["id"], "one.wav")
    settings = providers.Settings(provider_type="custom", base_url="http://127.0.0.1:9000/v1")
    created = providers.save_profile(store, credentials, "Synthetic", settings, remember=False)
    profile = providers.profile(store, created["id"])
    guard = language_preferences.guard(store, recording)
    preference = language_preferences.selected(guard)
    assert preference == {"language": "da", "scope": "project"}
    frozen = language_preferences.apply_to_provider(profile, preference)
    assert profile["settings"]["language"] == ""
    with store.library_lock():
        job = input_admission.accept(
            store,
            [
                {
                    "recording_id": recording["id"],
                    "language_guard": guard,
                    "choice": input_admission.choice(
                        provider=frozen, language_preference=preference
                    ),
                }
            ],
        )[0]
    assert job["params"]["settings"]["language"] == "da"
    assert language_preferences.save(store, "recording", recording["id"], "en", 0) == {
        "language": "en",
        "revision": 1,
    }
    with pytest.raises(language_preferences.Conflict):
        language_preferences.save(store, "recording", recording["id"], "da", 0)
    assert store.job(job["id"])["params"]["settings"]["language"] == "da"
    assert language_preferences.selected(
        language_preferences.guard(store, store.recording(recording["id"]))
    ) == {
        "language": "en",
        "scope": "recording",
    }


def test_stale_language_guard_refuses_acceptance_and_creates_no_jobs(tmp_path, credentials):
    store = Store(tmp_path / "library")
    project = store.create_project("Languages")
    recording = store.create_recording(project["id"], "one.wav")
    settings = providers.Settings(provider_type="custom", base_url="http://127.0.0.1:9000/v1")
    created = providers.save_profile(store, credentials, "Synthetic", settings, remember=False)
    profile = providers.profile(store, created["id"])
    guard = language_preferences.guard(store, recording)
    language_preferences.save(store, "project", project["id"], "da", 0)
    with store.library_lock(), pytest.raises(input_admission.AdmissionConflict):
        input_admission.accept(
            store,
            [
                {
                    "recording_id": recording["id"],
                    "language_guard": guard,
                    "choice": input_admission.choice(provider=profile),
                }
            ],
        )
    assert not store.all("SELECT id FROM jobs")


def test_language_edits_invalidate_deletion_metadata_snapshot(client):
    store = client.app.state.store
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "one.wav")
    deletions = client.app.state.deletions
    before = deletions._metadata("project", project["id"], [recording["id"]])
    language_preferences.save(store, "recording", recording["id"], "en", 0)
    after_recording = deletions._metadata("project", project["id"], [recording["id"]])
    language_preferences.save(store, "project", project["id"], "da", 0)
    after_project = deletions._metadata("project", project["id"], [recording["id"]])
    assert before != after_recording != after_project


def test_catalog_rejects_unknown_and_accepts_valid_standard_codes():
    assert {"da", "en", "fr", "de"} <= language_preferences.codes()
    assert language_preferences.validate(None) is None
    assert language_preferences.validate("fr") == "fr"
    with pytest.raises(ValueError):
        language_preferences.validate("not-a-language")


def test_malformed_frozen_scope_is_quarantined_as_admission_error():
    settings = providers.Settings(
        provider_type="custom", base_url="http://127.0.0.1:9000/v1", language="da"
    )
    provider = {"id": "a" * 32, "name": "Synthetic", "settings": settings.model_dump()}
    frozen = input_admission.choice(
        provider=provider, language_preference={"language": "da", "scope": "project"}
    )
    frozen["language_preference"]["scope"] = []
    with pytest.raises(input_admission.AdmissionError):
        input_admission._choice(frozen)


def test_language_api_lists_catalog_and_enforces_revision(client):
    languages = client.get("/api/languages")
    assert languages.status_code == 200
    assert [item["code"] for item in languages.json()["languages"][:2]] == ["da", "en"]
    project = client.post(
        "/api/projects", json={"name": "Synthetic", "default_language": "da"}
    ).json()
    assert project["default_language"] == "da" and project["language_revision"] == 0
    response = client.put(
        f"/api/projects/{project['id']}/language", json={"language": "fr", "revision": 0}
    )
    assert response.json() == {"language": "fr", "revision": 1}
    assert (
        client.put(
            f"/api/projects/{project['id']}/language", json={"language": "en", "revision": 0}
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/projects/{project['id']}/language", json={"language": "ZZ", "revision": 1}
        ).status_code
        == 400
    )


def test_transcription_api_uses_recording_override_without_changing_provider(client):
    store = client.app.state.store
    project = store.create_project("Synthetic", default_language="da")
    recording = store.create_recording(project["id"], "one.wav")
    assert client.put(
        f"/api/recordings/{recording['id']}/language", json={"language": "en", "revision": 0}
    ).json() == {"language": "en", "revision": 1}
    settings = {
        "provider_type": "custom",
        "base_url": "http://127.0.0.1:9000/v1",
        "model": "synthetic",
        "capability": "segments",
        "language": "fr",
    }
    assert client.put("/api/settings", json=settings).status_code == 200
    with store.library_lock():
        response = client.post("/api/transcriptions", json={"recording_ids": [recording["id"]]})
    assert response.status_code == 202, response.text
    accepted = response.json()[0]["params"]["input_intent"]["accepted"]
    assert accepted["provider"]["settings"]["language"] == "en"
    assert accepted["language_preference"] == {"language": "en", "scope": "recording"}
    assert client.get("/api/settings").json()["language"] == "fr"


def test_openai_rejects_non_iso639_1_forced_hint():
    openai = {
        "id": "a" * 32,
        "name": "OpenAI",
        "settings": providers.Settings(provider_type="openai", model="whisper-1").model_dump(),
    }
    with pytest.raises(ValueError, match="two-letter"):
        language_preferences.apply_to_provider(openai, {"scope": "project", "language": "ace"})


@pytest.mark.parametrize(
    "model,language,error",
    [
        ("whisperx/roest-v3", "en", "requires Danish"),
        ("whisperx/large-v3", "fr", "Danish and English"),
        ("nvidia/parakeet-tdt-0.6b-v3", "da", "not been verified"),
        ("parakeet-tdt-0.6b-v3", "da", "not been verified"),
        ("parakeet", "da", "not been verified"),
        ("PARAKEET", "da", "not been verified"),
    ],
)
def test_ucloud_rejects_unverified_or_unsupported_preference(model, language, error):
    settings = providers.Settings(
        provider_type="ucloud", base_url="https://cloud.invalid", model=model
    )
    provider = {"id": "a" * 32, "name": "UCloud", "settings": settings.model_dump()}
    assert language_preferences.apply_to_provider(provider, None) is provider
    with pytest.raises(ValueError, match=error):
        language_preferences.apply_to_provider(
            provider, {"scope": "recording", "language": language}
        )


@pytest.mark.parametrize(
    "provider_type,model", [("custom", "parakeet"), ("ucloud", "parakeet-tdt-0.6b-v2")]
)
def test_unrelated_models_are_not_classified_as_known_native_parakeet(provider_type, model):
    settings = providers.Settings(
        provider_type=provider_type, base_url="https://cloud.invalid", model=model
    )
    provider = {"id": "a" * 32, "name": "Synthetic", "settings": settings.model_dump()}
    chosen = language_preferences.apply_to_provider(
        provider, {"scope": "recording", "language": "da"}
    )
    assert chosen["settings"]["language"] == "da"
    assert provider["settings"]["language"] == ""


def test_native_mixed_language_selection_separates_frozen_allocations(tmp_path):
    store = Store(tmp_path / "library")
    project = store.create_project("Languages", default_language="da")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    language_preferences.save(store, "recording", rows[1]["id"], "en", 0)
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        language="",
        capability="words",
        ucloud_transcriber={
            "project_id": "synthetic-project",
            "storage_path": "/synthetic-drive/inputs",
            "application": {"name": "transcriber/Batch", "version": "synthetic"},
            "product": {
                "id": "gpu-nvidia-b200-1-gpu",
                "category": "synthetic",
                "provider": "synthetic",
            },
            "input_parameter": "input_dir",
        },
    )
    identifier = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (identifier, "Native", settings.model_dump_json(), now()),
    )
    provider = providers.profile(store, identifier)
    items = []
    for row in rows:
        guard = language_preferences.guard(store, store.recording(row["id"]))
        preference = language_preferences.selected(guard)
        chosen = language_preferences.apply_to_provider(provider, preference)
        items.append(
            {
                "recording_id": row["id"],
                "language_guard": guard,
                "choice": input_admission.choice(provider=chosen, language_preference=preference),
            }
        )
    with store.library_lock():
        jobs = input_admission.accept(store, items)
    assert [job["params"]["settings"]["language"] for job in jobs] == ["da", "en"]
    assert [job["params"]["input_intent"]["native_group"]["job_ids"] for job in jobs] == [
        [jobs[0]["id"]],
        [jobs[1]["id"]],
    ]


@pytest.mark.parametrize("backend", ["native", "runtime"])
def test_materialization_preserves_scopes_and_groups_equal_languages(
    tmp_path, credentials, monkeypatch, backend
):
    store = Store(tmp_path / "library")
    project = store.create_project("Synthetic", default_language="da")
    rows = [store.create_recording(project["id"], f"{n}.wav") for n in range(3)]
    language_preferences.save(store, "recording", rows[1]["id"], "da", 0)
    language_preferences.save(store, "recording", rows[2]["id"], "en", 0)
    provider = native_provider(store, credentials)
    if backend == "runtime":
        settings = providers.Settings.model_validate(provider["settings"]).model_copy(
            update={"model": "whisperx/large-v3"}
        )
        store.execute(
            "UPDATE providers SET value=? WHERE id=?", (settings.model_dump_json(), provider["id"])
        )
        provider = providers.profile(store, provider["id"])

        def freeze(_store, _credentials, selected, _projects, options):
            return options, {
                "ucloud_api": {
                    "provider_id": selected["id"],
                    "settings": selected["settings"],
                    "runtime_id": "synthetic-runtime",
                    "runtime_product": dict(MIG_PRODUCT),
                    "runtime_compute_policy": "mig-v1",
                    "prepare_runtime": options.prepare_runtime,
                    "batch_plan": runtime_plan(),
                }
            }

        monkeypatch.setattr(enhancement_api, "freeze_profile", freeze)
        monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *args: True)
    items = []
    for row in rows:
        write_original_wav(store, row)
        guard = language_preferences.guard(store, store.recording(row["id"]))
        preference = language_preferences.selected(guard)
        frozen = language_preferences.apply_to_provider(provider, preference)
        items.append(
            {
                "recording_id": row["id"],
                "language_guard": guard,
                "choice": input_admission.choice(provider=frozen, language_preference=preference),
            }
        )
    with store.library_lock():
        jobs = input_admission.accept(store, items)
        materialized = inspect_and_materialize(store, credentials, [job["id"] for job in jobs])
    receipts = [job["params"]["input_acceptance"]["language_preference"] for job in materialized]
    assert receipts == [
        {"scope": "project", "language": "da"},
        {"scope": "recording", "language": "da"},
        {"scope": "recording", "language": "en"},
    ]
    if backend == "native":
        [owner] = store.all("SELECT id,members FROM native_batch_runs")
        assert {m["job_id"] for m in json.loads(owner["members"])} == {jobs[0]["id"], jobs[1]["id"]}
        assert materialized[2]["params"].get("native_batch_id") is None
    else:
        batches = [job["params"]["runtime_batch_id"] for job in materialized]
        assert batches[0] == batches[1] != batches[2]
