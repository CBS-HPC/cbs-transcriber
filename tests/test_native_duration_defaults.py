"""New native defaults last overnight; saved choices and frozen legacy runs stay exact."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_native_model_profiles import native_settings, raw_profile
from test_ucloud_native import SETUP, TEMPLATE

from cbs_transcriber import ucloud_discovery, ucloud_native
from cbs_transcriber.providers import (
    Settings,
    admission_profiles,
    backfill_native_profiles,
    fresh_native_settings,
    profile,
    save_profile,
)


def fresh_payload(hours=None, *, runtime=False):
    payload = native_settings(
        model="whisperx/large-v3" if runtime else "whisper/large-v3-turbo"
    ).model_dump()
    payload["ucloud_transcriber"].pop("hours")
    if hours is not None:
        payload["ucloud_transcriber"]["hours"] = hours
    return payload


def test_fresh_default_is_a_copy_and_legacy_decoder_still_defaults_to_four():
    payload = fresh_payload()
    legacy = Settings.model_validate(payload)
    assert legacy.ucloud_transcriber.hours == 4
    assert "hours" not in legacy.ucloud_transcriber.model_fields_set
    before = legacy.model_dump()
    fresh = fresh_native_settings(legacy)
    assert fresh.ucloud_transcriber.hours == 24
    assert fresh is not legacy and fresh.ucloud_transcriber is not legacy.ucloud_transcriber
    assert legacy.model_dump() == before
    assert Settings.model_validate(payload).ucloud_transcriber.hours == 4


@pytest.mark.parametrize("hours", [1, 4, 7, 12, 24, 168])
def test_fresh_helper_preserves_explicit_durations(hours):
    settings = Settings.model_validate(fresh_payload(hours))
    assert fresh_native_settings(settings) is settings
    assert settings.ucloud_transcriber.hours == hours


def test_runtime_missing_hours_keeps_legacy_default():
    runtime = Settings.model_validate(fresh_payload(runtime=True))
    assert fresh_native_settings(runtime) is runtime
    assert runtime.ucloud_transcriber.hours == 4


@pytest.mark.parametrize("hours,expected", [(None, 24), (4, 4), (12, 12), (168, 168)])
def test_new_profile_api_preserves_omitted_versus_explicit_hours(client, hours, expected):
    response = client.post(
        "/api/providers", json={**fresh_payload(hours), "name": "Synthetic duration choice"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["ucloud_transcriber"]["hours"] == expected
    settings = profile(client.app.state.store, response.json()["id"])["settings"]
    assert settings["ucloud_transcriber"]["hours"] == expected


def test_direct_new_profile_save_uses_twenty_four_without_mutating_caller(client, credentials):
    settings = Settings.model_validate(fresh_payload())
    before = settings.model_dump()
    saved = save_profile(client.app.state.store, credentials, "Direct synthetic save", settings)
    assert saved["ucloud_transcriber"]["hours"] == 24
    assert settings.model_dump() == before


@pytest.mark.parametrize("hours,expected", [(None, 24), (4, 4), (12, 12)])
def test_fresh_discovery_boundary_preserves_hour_intent_without_network(
    client, monkeypatch, hours, expected
):
    seen = []

    def discover(settings, key, **kwargs):
        seen.append(settings.ucloud_transcriber.hours)
        return {"ready": True, "setup": settings.ucloud_transcriber.model_dump()}

    monkeypatch.setattr(ucloud_discovery, "discover", discover)
    response = client.post("/api/providers/discover-ucloud", json=fresh_payload(hours))
    assert response.status_code == 200, response.text
    assert response.json()["setup"]["hours"] == expected and seen == [expected]


def test_runtime_profile_creation_and_discovery_do_not_adopt_native_default(client, monkeypatch):
    monkeypatch.setattr(
        ucloud_discovery,
        "discover",
        lambda settings, key, **kwargs: {"setup": settings.ucloud_transcriber.model_dump()},
    )
    response = client.post("/api/providers/discover-ucloud", json=fresh_payload(runtime=True))
    assert response.status_code == 200 and response.json()["setup"]["hours"] == 4
    response = client.post(
        "/api/providers", json={**fresh_payload(runtime=True), "name": "Parked Runtime unchanged"}
    )
    assert response.status_code == 201 and response.json()["ucloud_transcriber"]["hours"] == 4


@pytest.mark.parametrize("hours", [4, 12])
def test_existing_profile_updates_backfill_and_frozen_jobs_preserve_durations(
    client, credentials, hours
):
    store = client.app.state.store
    settings = Settings.model_validate(fresh_payload(hours))
    provider_id = raw_profile(store, settings, "Existing explicit duration")
    runtime_id = raw_profile(store, Settings.model_validate(fresh_payload(runtime=True)), "Runtime")
    runtime_before = store.one("SELECT * FROM providers WHERE id=?", (runtime_id,))
    project = store.create_project("Frozen synthetic native durations")
    recording = store.create_recording(project["id"], "sample.wav")
    frozen_settings = deepcopy(fresh_payload())  # Legacy frozen settings omitted hours.
    jobs = []
    for state in ("queued", "running", "failed"):
        job = store.add_job(
            recording["id"], "transcribe", {"settings": frozen_settings, "provider_id": provider_id}
        )
        store.update_job(job["id"], state=state)
        jobs.append(store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)))
    before = profile(store, provider_id)
    response = client.put(
        f"/api/providers/{provider_id}",
        json={**before["settings"], "name": "Renamed existing configuration"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ucloud_transcriber"]["hours"] == hours
    backfill_native_profiles(store)
    assert profile(store, provider_id)["settings"]["ucloud_transcriber"]["hours"] == hours
    assert store.one("SELECT * FROM providers WHERE id=?", (runtime_id,)) == runtime_before
    for before_job in jobs:
        assert store.one("SELECT * FROM jobs WHERE id=?", (before_job["id"],)) == before_job
        frozen = Settings.model_validate(json.loads(before_job["params"])["settings"])
        assert frozen.ucloud_transcriber.hours == 4


def test_new_admission_freezes_twenty_four_and_does_not_rewrite_saved_four(client, credentials):
    store = client.app.state.store
    old_id = raw_profile(store, Settings.model_validate(fresh_payload(4)), "Existing four hours")
    old = store.one("SELECT * FROM providers WHERE id=?", (old_id,))
    new = save_profile(
        store, credentials, "New overnight duration", Settings.model_validate(fresh_payload())
    )
    admitted, _ = admission_profiles(store, [old_id, new["id"]])
    assert [row["settings"]["ucloud_transcriber"]["hours"] for row in admitted] == [4, 24]
    assert store.one("SELECT * FROM providers WHERE id=?", (old_id,)) == old


@pytest.mark.parametrize(
    "fresh,hours,expected", [(True, None, 24), (False, None, 4), (True, 12, 12), (True, 168, 168)]
)
def test_actual_job_specification_has_exact_selected_limit_and_no_ssh(fresh, hours, expected):
    setup = deepcopy(SETUP)
    if hours is not None:
        setup["hours"] = hours
    settings = Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3",
        ucloud_transcriber=setup,
    )
    if fresh:
        settings = fresh_native_settings(settings)
    parameters = {
        row["name"]: row
        for row in TEMPLATE["status"]["resolvedApplication"]["invocation"]["parameters"]
    }
    specification = ucloud_native.build_specification(
        settings, TEMPLATE["specification"], parameters, "/drive/owned-run", ".wav", "synthetic-run"
    )
    assert specification["timeAllocation"] == {"hours": expected, "minutes": 0, "seconds": 0}
    assert specification["sshEnabled"] is False
    assert specification["parameters"]["input_file"]["path"] == "/drive/owned-run/recording.wav"


@pytest.mark.parametrize("hours", [0, -1, 169])
def test_existing_one_to_168_hour_validation_remains_enforced(hours):
    with pytest.raises(ValidationError):
        ucloud_native.NativeSetup(hours=hours)
