"""Explicit per-model Automatic opt-in, without starting a provider or downloading weights."""

import copy

import pytest
from admission_support import inspect_and_materialize
from test_runtime_batch_api import add_profile, add_recording, assert_queue_unlocked

from cbs_transcriber import enhancement_api, providers, runtime_batches, runtime_store
from cbs_transcriber.runtime_compute import MIG_PRODUCT


@pytest.fixture
def configured(client, credentials, monkeypatch):
    # These readiness cases retain the explicit preparation-confirmation policy.
    client.put("/api/enhancement-settings", json={"prepare_ucloud_models": False})
    store = client.app.state.store
    project = store.create_project("Synthetic language API")
    runtime = add_profile(store, credentials, "whisperx/large-v3")
    native = add_profile(store, credentials, "large-v3")
    calls = []

    def ready(credentials, selected, options):
        assert_queue_unlocked(client.app.state.queue)
        if options.language == "auto":
            assert {"coarse", "da", "en", "whisper-large-v3"} <= set(
                enhancement_api.required_models(options)
            )
        calls.append(("ready", options.language))
        return True

    def freeze(store, credentials, selected, projects, options):
        assert_queue_unlocked(client.app.state.queue)
        calls.append(("freeze", options.language))
        return options.model_copy(update={"device": "cuda", "allow_downloads": False}), {
            "ucloud_api": {
                "provider_id": selected["id"],
                "settings": copy.deepcopy(selected["settings"]),
                "runtime_id": enhancement_api.runtime_id(),
                "runtime_product": dict(MIG_PRODUCT),
                "runtime_compute_policy": "mig-v1",
                "prepare_runtime": options.prepare_runtime,
                "batch_plan": {
                    "identity": {
                        "protocol": runtime_batches.PROTOCOL,
                        "application": {"name": "terminal-ubuntu", "version": "synthetic"},
                        "code_hashes": runtime_batches.code_hashes(),
                        "recipe_hash": enhancement_api.requirements_hash(),
                        "model_revisions": {
                            key: list(pin) for key, pin in enhancement_api.MODELS.items()
                        },
                        "limits": dict(runtime_batches.LIMITS),
                    }
                },
            }
        }

    monkeypatch.setattr(enhancement_api, "whisperx_ready", ready)
    monkeypatch.setattr(enhancement_api, "freeze_profile", freeze)
    with store.library_lock():
        yield store, project, runtime, native, calls


def change_language(client, provider_id, language, version=None):
    if version is None:
        version = providers.public_profile(
            client.app.state.store, client.app.state.credentials, provider_id
        )["version"]
    return client.put(
        f"/api/providers/{provider_id}/runtime-language",
        json={"language": language, "expected_version": version},
    )


def test_v125_language_endpoint_is_scoped_versioned_and_keeps_native_and_credentials(
    client, configured, credentials, monkeypatch
):
    store, _, runtime, native, calls = configured
    before_native = store.one("SELECT * FROM providers WHERE id=?", (native["id"],))
    count = len(store.all("SELECT id FROM providers"))
    keys = copy.deepcopy(credentials.keys)
    before = providers.public_profile(store, credentials, runtime["id"])

    def no_key_write(*args, **kwargs):
        pytest.fail("Language changes must not write credentials")

    monkeypatch.setattr(credentials, "set", no_key_write)
    response = change_language(client, runtime["id"], "auto", before["version"])
    assert response.status_code == 200, response.text
    assert response.json()["language"] == "auto"
    assert response.json()["approved_for"] == before["approved_for"]
    assert response.json()["enabled"] == before["enabled"]
    assert credentials.keys == keys and calls == []
    assert len(store.all("SELECT id FROM providers")) == count
    assert store.one("SELECT * FROM providers WHERE id=?", (native["id"],)) == before_native
    stale = change_language(client, runtime["id"], "en", before["version"])
    assert stale.status_code == 409
    assert providers.profile(store, runtime["id"])["settings"]["language"] == "auto"


@pytest.mark.parametrize("which", ["native", "missing"])
def test_v125_runtime_language_endpoint_refuses_other_identities(client, configured, which):
    store, _, _, native, calls = configured
    identifier = native["id"] if which == "native" else "missing"
    before = store.all("SELECT * FROM providers ORDER BY id")
    response = change_language(client, identifier, "auto", "0" * 16)
    # A valid version is needed before inspecting an existing row's model family.
    if which == "native":
        response = change_language(client, identifier, "auto")
    assert response.status_code == (400 if which == "native" else 404)
    assert store.all("SELECT * FROM providers ORDER BY id") == before and calls == []


@pytest.mark.parametrize(
    "payload", [{"language": "auto"}, {"language": "fr", "expected_version": "0" * 16}]
)
def test_v125_runtime_language_request_needs_explicit_supported_choice_and_version(
    client, configured, payload
):
    store, _, runtime, _, calls = configured
    before = providers.profile(store, runtime["id"])
    response = client.put(f"/api/providers/{runtime['id']}/runtime-language", json=payload)
    assert response.status_code == 422
    assert providers.profile(store, runtime["id"]) == before and calls == []


def test_v125_explicit_auto_profile_queues_shared_full_asr_and_freezes_choice(client, configured):
    store, project, runtime, native, calls = configured
    assert change_language(client, runtime["id"], "auto").status_code == 200
    recordings = [add_recording(store, project, f"clip-{i}.wav", reviewed=True) for i in range(2)]
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [runtime["id"]], "recording_ids": [r["id"] for r in recordings]},
    )
    assert response.status_code == 202, response.text
    jobs = response.json()
    assert len(jobs) == 2 and calls == []
    assert all(job["params"]["input_intent"]["phase"] == "waiting" for job in jobs)
    jobs = inspect_and_materialize(store, client.app.state.credentials, [job["id"] for job in jobs])
    assert calls == [("ready", "auto"), ("freeze", "auto")] * len(jobs)
    owner = runtime_store.for_job(store, jobs[0]["id"])
    assert owner["id"] == runtime_store.for_job(store, jobs[1]["id"])["id"]
    assert owner["identity"]["options"]["language"] == "auto"
    frozen = [store.job(job["id"])["params"] for job in jobs]
    for params in frozen:
        assert params["transcription"] is True
        assert params["enhancement"]["language"] == "auto"
        assert params["enhancement"]["align"] is True
        assert params["ucloud_api"]["settings"]["language"] == "auto"
    assert change_language(client, runtime["id"], "en").status_code == 200
    assert [store.job(job["id"])["params"] for job in jobs] == frozen
    assert providers.profile(store, native["id"])["settings"]["language"] == ""
    assert all(store.recording(r["id"])["active_transcript_id"] for r in recordings)


@pytest.mark.parametrize("language", ["da", "en"])
def test_v125_empty_runtime_language_retains_processing_fallback(client, configured, language):
    store, project, runtime, _, calls = configured
    assert client.put("/api/enhancement-settings", json={"language": language}).status_code == 200
    recording = add_recording(store, project)
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [runtime["id"]], "recording_ids": [recording["id"]]},
    )
    assert response.status_code == 202, response.text
    assert calls == []
    [job] = inspect_and_materialize(store, client.app.state.credentials, [response.json()[0]["id"]])
    assert job["params"]["enhancement"]["language"] == language
    assert calls == [("ready", language), ("freeze", language)]


def test_v125_unprepared_auto_stops_admission_for_confirmation_without_executable_work(
    client, configured, monkeypatch
):
    store, project, runtime, _, calls = configured
    assert change_language(client, runtime["id"], "auto").status_code == 200
    recording = add_recording(store, project)
    monkeypatch.setattr(enhancement_api, "whisperx_ready", lambda *args: False)
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [runtime["id"]], "recording_ids": [recording["id"]]},
    )
    assert response.status_code == 202, response.text
    [accepted] = response.json()
    assert accepted["params"]["input_intent"]["phase"] == "waiting" and calls == []
    [job] = inspect_and_materialize(store, client.app.state.credentials, [accepted["id"]])
    assert job["state"] == "failed"
    assert job["params"]["input_intent"]["error"]["code"] == "needs_confirmation"
    assert store.all("SELECT id FROM runtime_batch_runs") == []
    assert store.claim_job("enhance") is None
    assert calls == []


def test_v125_auto_profile_preserves_explicit_protected_denial(client, configured):
    store, project, runtime, _, calls = configured
    settings = providers.Settings.model_validate(runtime["settings"])
    settings.approved_for = []
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (settings.model_dump_json(), runtime["id"])
    )
    store.execute("UPDATE projects SET classification='protected' WHERE id=?", (project["id"],))
    assert change_language(client, runtime["id"], "auto").status_code == 200
    recording = add_recording(store, project)
    response = client.post(
        "/api/transcriptions",
        json={"provider_ids": [runtime["id"]], "recording_ids": [recording["id"]]},
    )
    assert response.status_code == 403, response.text
    assert store.all("SELECT id FROM jobs") == []
    assert providers.profile(store, runtime["id"])["settings"]["approved_for"] == []
    # Protected policy rejects metadata acceptance before inspection or remote preparation.
    assert calls == []
