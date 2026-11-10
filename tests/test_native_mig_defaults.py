"""Native defaults are profile-only preferences; legacy execution identities stay exact."""

import copy
import json
from pathlib import Path

import pytest
from test_input_admission import inspect, native_provider
from test_input_admission import library as library
from test_runtime_preparation import prepared as prepared

from cbs_transcriber import (
    enhancement,
    enhancement_api,
    input_admission,
    providers,
    runtime_batches,
    runtime_preparation,
    runtime_store,
)
from cbs_transcriber import ucloud_native as native

FULL = {"id": native.COMPUTE_ID, "category": "gpu", "provider": "ucloud"}


def settings_for(model="large-v3", product=FULL):
    return providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model=model,
        language="da",
        approved_for=["protected"],
        ucloud_transcriber={
            "project_id": "project",
            "storage_path": "/drive/cbs",
            "application": native.MIG_APPLICATION,
            "product": product,
        },
    )


def raw_profile(store, identifier):
    return json.loads(store.one("SELECT value FROM providers WHERE id=?", (identifier,))["value"])


def put_raw(store, identifier, payload):
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), identifier))


def test_marker_transition_preserves_frozen_protected_and_runtime_identities(client, credentials):
    store = client.app.state.store
    settings = settings_for()
    saved = providers.save_profile(store, credentials, "Legacy native", settings)
    payload = raw_profile(store, saved["id"])
    for field in (providers.NATIVE_CHOICE, providers.NATIVE_PRODUCT):
        payload.pop(field)
    put_raw(store, saved["id"], payload)
    project = store.create_project("Protected", classification="protected")
    recording = store.create_recording(project["id"], "synthetic.wav")
    params = {"provider_id": saved["id"], "settings": settings.model_dump()}
    job = store.add_job(recording["id"], "transcribe", params)
    frozen_job = store.job(job["id"])
    chosen = providers.profile(store, saved["id"])
    fingerprint = runtime_preparation.fingerprint(chosen)
    # Literal pre-feature Runtime identities normalize only Settings, never profile metadata.
    identity = {"provider_id": saved["id"], "settings": settings.model_dump()}
    assert providers.migrate_native_allocation_defaults(store) == 1
    assert providers.migrate_native_allocation_defaults(store) == 0
    assert providers.profile(store, saved["id"]) == chosen
    assert runtime_preparation.fingerprint(chosen) == fingerprint
    assert store.job(job["id"]) == frozen_job
    providers.require_approval(store, project["id"], saved["id"], settings)
    with store.connection() as db:
        runtime_store._check_profile(db, {"project_id": project["id"]}, identity)
    # The original strict approval boundary still refuses an actual connection change.
    payload = raw_profile(store, saved["id"])
    payload["ucloud_transcriber"]["project_id"] = "changed"
    put_raw(store, saved["id"], payload)
    with pytest.raises(providers.ApprovalDenied):
        providers.require_approval(store, project["id"], saved["id"], settings)
    with store.connection() as db, pytest.raises(ValueError, match="changed"):
        runtime_store._check_profile(db, {"project_id": project["id"]}, identity)


def test_native_defaults_and_explicit_full_survive_backfill_without_runtime_preference(
    client,
    credentials,
):
    store = client.app.state.store
    saved = providers.save_profile(
        store, credentials, "New MIG connection", settings_for(product=native.MIG_PRODUCT)
    )
    profiles = providers.public_profiles(store, credentials)["providers"]
    native_profiles = [
        p
        for p in profiles
        if p["provider_type"] == "ucloud"
        and not providers.whisperx_model(providers.Settings.model_validate(p))
    ]
    runtime_profiles = [
        p
        for p in profiles
        if p["provider_type"] == "ucloud"
        and providers.whisperx_model(providers.Settings.model_validate(p))
    ]
    assert len(native_profiles) > 1 and runtime_profiles
    assert all(p[providers.NATIVE_CHOICE] == "mig1g" for p in native_profiles)
    assert all(p[providers.NATIVE_PRODUCT] == native.MIG_PRODUCT for p in native_profiles)
    assert all(
        providers.NATIVE_CHOICE not in p and providers.NATIVE_PRODUCT not in p
        for p in runtime_profiles
    )
    for p in runtime_profiles:
        native.require_connection(providers.Settings.model_validate(p))
    # A seed's explicit Full opt-out must not spread to missing model rows.
    payload = raw_profile(store, saved["id"])
    payload[providers.NATIVE_CHOICE], payload[providers.NATIVE_PRODUCT] = "full", FULL
    put_raw(store, saved["id"], payload)
    store.execute(
        "DELETE FROM providers WHERE id<>? AND json_extract(value,'$.provider_type')='ucloud'",
        (saved["id"],),
    )
    assert providers.backfill_native_profiles(store) > 0
    assert providers.backfill_native_profiles(store) == 0
    assert providers.migrate_native_allocation_defaults(store) == 0
    profiles = providers.public_profiles(store, credentials)["providers"]
    assert next(p for p in profiles if p["id"] == saved["id"])[providers.NATIVE_CHOICE] == "full"
    assert all(
        p[providers.NATIVE_CHOICE] == "mig1g"
        for p in profiles
        if p["provider_type"] == "ucloud"
        and p["id"] != saved["id"]
        and not providers.whisperx_model(providers.Settings.model_validate(p))
    )


@pytest.mark.parametrize("broken", [None, "unknown", {}, {"id": "missing"}])
def test_obsolete_malformed_preference_does_not_change_fixed_mig(client, credentials, broken):
    store = client.app.state.store
    saved = providers.save_profile(store, credentials, "Malformed", settings_for())
    payload = raw_profile(store, saved["id"])
    payload[providers.NATIVE_CHOICE] = broken
    put_raw(store, saved["id"], payload)
    assert providers.migrate_native_allocation_defaults(store) == 0
    _, policies = providers.admission_profiles(store, [saved["id"]])
    assert policies[saved["id"]]["product"] == native.MIG_PRODUCT
    assert raw_profile(store, saved["id"])[providers.NATIVE_CHOICE] == broken
    assert (
        providers.public_profile(store, credentials, saved["id"])[providers.NATIVE_CHOICE]
        == "invalid"
    )


def test_fresh_template_request_refuses_retarget_but_runtime_stays_unchanged(client, credentials):
    store = client.app.state.store
    template = settings_for().model_copy(deep=True)
    template.ucloud_transcriber.template_job_id = "explicit-template"
    values = [template, settings_for("whisperx/large-v3")]
    ids = [providers.save_profile(store, credentials, "Legacy binding", v)["id"] for v in values]
    before = [store.one("SELECT value FROM providers WHERE id=?", (i,))["value"] for i in ids]
    assert providers.migrate_native_allocation_defaults(store) == 0
    with pytest.raises(ValueError, match="Reconnect UCloud without"):
        providers.admission_profiles(store, [ids[0]])
    _, policies = providers.admission_profiles(store, [ids[1]])
    assert not policies
    assert [
        store.one("SELECT value FROM providers WHERE id=?", (i,))["value"] for i in ids
    ] == before


def test_new_requests_ignore_stored_full_preference_without_rewriting_frozen_jobs(
    client, credentials
):
    store = client.app.state.store
    settings = settings_for()
    saved = providers.save_profile(
        store, credentials, "Previously full", settings, native_allocation_choice="full"
    )
    project = store.create_project("Synthetic")
    recording = store.create_recording(project["id"], "one.wav")
    full = {"policy": "native-allocation-v1", "product": FULL}
    job = store.add_job(
        recording["id"],
        "transcribe",
        {"settings": settings.model_dump(), "native_allocation": full},
    )
    before = store.job(job["id"])
    raw_before = raw_profile(store, saved["id"])
    _, allocations = providers.admission_profiles(store, [saved["id"]])
    assert allocations[saved["id"]]["product"] == native.MIG_PRODUCT
    assert store.job(job["id"]) == before
    assert raw_profile(store, saved["id"]) == raw_before


def test_profile_and_preferences_are_one_snapshot_even_during_writer_commit(
    client, credentials, monkeypatch
):
    store = client.app.state.store
    ids = [
        providers.save_profile(store, credentials, model, settings_for(model))["id"]
        for model in ("large-v3", "large-v3-turbo")
    ]
    original = providers.Settings.model_validate
    changed = []

    def interleave(value, *args, **kwargs):
        if not changed:
            changed.append(True)
            with store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                for identifier in ids:
                    row = db.execute(
                        "SELECT value FROM providers WHERE id=?", (identifier,)
                    ).fetchone()
                    payload = json.loads(row[0])
                    payload["language"] = "en"
                    payload[providers.NATIVE_CHOICE], payload[providers.NATIVE_PRODUCT] = (
                        "full",
                        FULL,
                    )
                    db.execute(
                        "UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), identifier)
                    )
        return original(value, *args, **kwargs)

    monkeypatch.setattr(providers.Settings, "model_validate", interleave)
    selected, policies = providers.admission_profiles(store, ids)
    assert changed == [True]
    assert all(p["settings"]["language"] == "da" for p in selected)
    assert all(p["product"] == native.MIG_PRODUCT for p in policies.values())
    assert all(
        raw_profile(store, identifier)[providers.NATIVE_CHOICE] == "full" for identifier in ids
    )


@pytest.mark.parametrize("legacy", [False, True], ids=["new-default", "literal-pre-feature"])
def test_accepted_native_choice_is_frozen_across_preference_change(
    library,
    credentials,
    wav_bytes,
    monkeypatch,
    legacy,
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    if legacy:
        # Generated by immutable 599606a admission source, before allocation policy existed.
        accepted = json.loads(
            (Path(__file__).parent / "fixtures/native_allocation_legacy_choice.json").read_text(
                encoding="utf-8"
            )
        )
        accepted["provider"] = provider
        assert "native_allocation" not in accepted
        input_admission._choice(accepted)
    else:
        selected, policies = providers.admission_profiles(store, [provider["id"]])
        accepted = input_admission.choice(
            provider=selected[0], native_allocation=policies[provider["id"]]
        )
    calls = []

    def discover(settings, key, *, compute_id=None):
        calls.append(compute_id)
        assert compute_id == native.MIG_PRODUCT["id"]
        return {"ready": True, "setup": settings.ucloud_transcriber.model_dump()}

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", discover)
    with store.library_lock():
        jobs = input_admission.accept(
            store, [{"recording_id": r["id"], "choice": copy.deepcopy(accepted)} for r in rows]
        )
        for job in jobs:
            intent = job["params"]["input_intent"]
            stopped = input_admission.cancel(
                store, job["id"], epoch=intent["epoch"], phase=intent["phase"]
            )
            intent = stopped["params"]["input_intent"]
            retried = input_admission.retry(
                store, job["id"], epoch=intent["epoch"], phase=intent["phase"]
            )
            assert retried["params"]["input_intent"]["accepted"] == accepted
        payload = raw_profile(store, provider["id"])
        payload[providers.NATIVE_CHOICE] = "full"
        payload[providers.NATIVE_PRODUCT] = payload["ucloud_transcriber"]["product"]
        put_raw(store, provider["id"], payload)
        for row in rows:
            inspect(store, row, wav_bytes)
        token = input_admission.claim(store)
        outcomes = input_admission.prepare(store, credentials, token)
        result = input_admission.materialize(store, token, outcomes)
    assert len(result) == 2
    assert all(j["params"]["settings"] == provider["settings"] for j in result)
    assert calls == ([] if legacy else [native.MIG_PRODUCT["id"]])
    for job in result:
        assert ("native_allocation" in job["params"]) is not legacy
        if not legacy:
            assert job["params"]["native_allocation"]["product"] == native.MIG_PRODUCT
        original = next(j for j in jobs if j["id"] == job["id"])
        assert (
            job["params"]["input_acceptance"]["accepted_sha256"]
            == original["params"]["input_intent"]["accepted_sha256"]
        )
    assert {j["id"] for j in result} == {j["id"] for j in jobs}


@pytest.mark.parametrize("policy", [None, {}, {"policy": "unknown", "product": native.MIG_PRODUCT}])
def test_present_invalid_execution_policy_never_becomes_legacy(policy):
    value = json.loads(
        (Path(__file__).parent / "fixtures/native_allocation_legacy_choice.json").read_text(
            encoding="utf-8"
        )
    )
    value["native_allocation"] = policy
    with pytest.raises(ValueError):
        input_admission._choice(value)


def test_admission_cohorts_separate_legacy_mig_and_full_and_reject_forged_membership(
    library,
    credentials,
):
    store, project, _, rows = library
    provider = native_provider(store, credentials)
    rows += [store.create_recording(project["id"], f"extra-{i}.wav") for i in range(4)]
    choices = [input_admission.choice(provider=provider)]
    for product in (native.MIG_PRODUCT, provider["settings"]["ucloud_transcriber"]["product"]):
        choices.append(
            input_admission.choice(
                provider=provider,
                native_allocation={"policy": "native-allocation-v1", "product": product},
            )
        )
    with store.library_lock():
        jobs = input_admission.accept(
            store,
            [{"recording_id": row["id"], "choice": choices[i // 2]} for i, row in enumerate(rows)],
        )
        groups = [j["params"]["input_intent"]["native_group"] for j in jobs]
        assert len({g["sha256"] for g in groups}) == 3
        for i, group in enumerate(groups):
            expected = {j["id"] for j in jobs[(i // 2) * 2 : (i // 2) * 2 + 2]}
            assert set(group["job_ids"]) == expected
            with store.connection() as db:
                cohort = input_admission._cohort(db, input_admission._load(db, jobs[i]["id"]))
                assert {j["id"] for j in cohort} == expected
        # An internally consistent group/hash cannot authorize incompatible choice scope.
        forged_ids = [jobs[i]["id"] for i in (0, 2, 4)]
        group = {
            "job_ids": forged_ids,
            "sha256": input_admission._digest(sorted(forged_ids)),
            "resolved": False,
        }
        for identifier in forged_ids:
            raw = store.job(identifier)["params"]
            raw["input_intent"]["native_group"] = group
            store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(raw), identifier))
        with store.connection() as db, pytest.raises(ValueError):
            input_admission._cohort(db, input_admission._load(db, forged_ids[0]))


@pytest.mark.parametrize("failure", ["unavailable", "changed-reference"])
def test_retry_never_falls_back_from_unavailable_frozen_mig_to_new_full_preference(
    library,
    credentials,
    wav_bytes,
    monkeypatch,
    failure,
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    selected, allocations = providers.admission_profiles(store, [provider["id"]])
    accepted = input_admission.choice(
        provider=selected[0], native_allocation=allocations[provider["id"]]
    )
    calls = []

    def refuse(settings, key, *, compute_id=None):
        calls.append(compute_id)
        setup = settings.ucloud_transcriber.model_dump()
        if failure == "changed-reference":
            setup["product"] = provider["settings"]["ucloud_transcriber"]["product"]
        return {
            "ready": failure != "unavailable",
            "setup": setup,
            "note": "Synthetic missing funding",
        }

    monkeypatch.setattr("cbs_transcriber.ucloud_discovery.discover", refuse)
    with store.library_lock():
        input_admission.accept(store, [{"recording_id": r["id"], "choice": accepted} for r in rows])
        payload = raw_profile(store, provider["id"])
        payload[providers.NATIVE_CHOICE], payload[providers.NATIVE_PRODUCT] = (
            "full",
            provider["settings"]["ucloud_transcriber"]["product"],
        )
        put_raw(store, provider["id"], payload)
        for row in rows:
            inspect(store, row, wav_bytes)
        for attempt in range(2):
            token = input_admission.claim(store)
            outcomes = input_admission.prepare(store, credentials, token)
            assert all(set(value) == {"error"} for value in outcomes.values())
            failed = input_admission.materialize(store, token, outcomes)
            assert all(j["state"] == "failed" for j in failed)
            if attempt == 0:
                for job in failed:
                    intent = job["params"]["input_intent"]
                    retried = input_admission.retry(
                        store, job["id"], epoch=intent["epoch"], phase=intent["phase"]
                    )
                    assert retried["params"]["input_intent"]["accepted"] == accepted
    assert calls and set(calls) == {native.MIG_PRODUCT["id"]}
    assert not store.all("SELECT id FROM native_batch_runs")


def test_http_request_freezes_one_profile_choice_for_complete_recording_cohort(
    client,
    credentials,
    monkeypatch,
):
    from cbs_transcriber import app as app_module

    store = client.app.state.store
    saved = providers.save_profile(
        store,
        credentials,
        "All native default",
        settings_for(),
        api_key="synthetic",
        remember=False,
        native_allocation_choice="full",
    )
    project = store.create_project("Cohort")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    reads = []
    read_profiles = app_module.admission_profiles

    def snapshot(*args):
        reads.append(True)
        result = read_profiles(*args)
        payload = raw_profile(store, saved["id"])
        payload[providers.NATIVE_CHOICE], payload[providers.NATIVE_PRODUCT] = "full", FULL
        put_raw(store, saved["id"], payload)
        return result

    monkeypatch.setattr(app_module, "admission_profiles", snapshot)
    monkeypatch.setattr(
        "cbs_transcriber.ucloud_discovery.discover",
        lambda *_a, **_k: pytest.fail("HTTP performed discovery"),
    )
    with store.library_lock():
        reply = client.post(
            "/api/transcriptions",
            json={"recording_ids": [r["id"] for r in rows], "provider_ids": [saved["id"]]},
        )
    assert reply.status_code == 202, reply.text
    assert reads == [True]
    intents = [
        store.job(j["id"])["params"]["input_intent"]
        for j in store.all("SELECT id FROM jobs WHERE kind='transcribe'")
    ]
    assert len(intents) == 2
    assert all(i["accepted"]["native_allocation"]["product"] == native.MIG_PRODUCT for i in intents)
    assert intents[0]["accepted"] == intents[1]["accepted"]
    assert all(
        i["accepted_sha256"] == input_admission._digest(input_admission._accepted_payload(i))
        for i in intents
    )


def test_new_mig_preparation_records_explicit_connection_policy_and_preserves_legacy(prepared):
    from test_runtime_preparation import enqueue

    f = prepared
    full = enqueue(f)
    assert "connection_policy" not in full["frozen"]
    assert full["connection_fingerprint"] == f.fingerprint
    # A profile preference is independent of this literal pre-feature preparation identity.
    payload = raw_profile(f.store, f.profile["id"])
    payload[providers.NATIVE_CHOICE], payload[providers.NATIVE_PRODUCT] = "full", FULL
    put_raw(f.store, f.profile["id"], payload)
    assert runtime_preparation.get(f.store, full["id"]) == full
    payload["ucloud_transcriber"]["product"] = native.MIG_PRODUCT
    put_raw(f.store, f.profile["id"], payload)
    chosen = enhancement_api.connection(f.store, f.credentials, [])
    identity = runtime_preparation._identity(chosen)
    assert identity["connection_policy"] == "native-connection-v1"
    assert identity["settings"]["ucloud_transcriber"]["product"] == native.MIG_PRODUCT
    assert runtime_preparation.fingerprint(chosen) != f.fingerprint


@pytest.mark.parametrize("connection_product", [FULL, native.MIG_PRODUCT], ids=["full", "mig"])
def test_runtime_uses_own_compute_on_fresh_mig_native_connection(
    client, credentials, monkeypatch, connection_product
):
    store = client.app.state.store
    project = store.create_project("Independent Runtime")
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="whisperx/large-v3",
        language="da",
        ucloud_transcriber={
            "project_id": "project",
            "storage_path": "/drive/cbs",
            "application": native.MIG_APPLICATION,
            "product": connection_product,
        },
    )
    saved = providers.save_profile(
        store, credentials, "Runtime", settings, api_key="synthetic-control", remember=False
    )
    called = []

    def own_compute(chosen, keys):
        assert chosen.ucloud_transcriber.project_id == "project"
        called.append(True)
        return {"runtime_product": dict(native.MIG_PRODUCT), "runtime_compute_policy": "mig-v1"}

    monkeypatch.setattr(enhancement_api.runtime_compute, "freeze", own_compute)
    monkeypatch.setattr(
        runtime_batches, "freeze_plan", lambda *_: {"identity": {"protocol": "test"}}
    )
    _, frozen = enhancement_api.freeze_profile(
        store,
        credentials,
        providers.profile(store, saved["id"]),
        [project["id"]],
        enhancement.EnhancementOptions(execution="ucloud", device="cuda", transcribe=True),
    )
    assert called == [True]
    assert frozen["ucloud_api"]["runtime_product"] == native.MIG_PRODUCT
    assert frozen["ucloud_api"]["settings"]["ucloud_transcriber"]["product"] == connection_product
