"""Native allocation changes use fresh synthetic metadata; never submit or upload."""

import copy
import json

import pytest
from test_ucloud_discovery import APPLICATION, PRODUCT, SECRET
from test_ucloud_discovery import account as account

from cbs_transcriber import providers, ucloud_discovery
from cbs_transcriber import ucloud_native as native


@pytest.fixture
def allocation(client, credentials, account):
    app = copy.deepcopy(APPLICATION)
    app["metadata"].update(native.MIG_APPLICATION)
    next(p for p in app["invocation"]["parameters"] if p["name"] == "model")["options"].append(
        {"name": "Turbo", "value": "large-v3-turbo"}
    )
    account.applications = [app]
    account.products.append(
        {
            "product": {
                "name": native.MIG_PRODUCT["id"],
                "category": {"name": native.MIG_PRODUCT["category"], "provider": "ucloud"},
                "gpu": 1,
            },
            "support": {"product": dict(native.MIG_PRODUCT), "docker": {"enabled": True}},
        }
    )
    account.wallets.append(
        {
            "owner": {"projectId": "project-one"},
            "paysFor": {
                "name": native.MIG_PRODUCT["category"],
                "provider": "ucloud",
                "productType": "COMPUTE",
            },
            "maxUsable": 100,
        }
    )
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model=native.MIG_MODEL,
        language="da",
        ucloud_transcriber={
            "project_id": "project-one",
            "storage_path": "/drive-one/CBS-Transcriber",
            "application": native.MIG_APPLICATION,
            "product": PRODUCT,
        },
    )
    saved = providers.save_profile(
        client.app.state.store,
        credentials,
        "Native turbo",
        settings,
        api_key=SECRET,
        remember=False,
    )
    return saved, settings, account


def choose(client, saved, allocation="mig1g"):
    return client.put(
        f"/api/providers/{saved['id']}/native-allocation",
        json={"allocation": allocation, "expected_version": saved["version"]},
    )


def test_choice_changes_only_exact_profile_and_preserves_frozen_jobs(client, allocation):
    saved, settings, account = allocation
    store = client.app.state.store
    project = store.create_project("Synthetic allocation")
    recording = store.create_recording(project["id"], "synthetic.wav")
    job = store.add_job(recording["id"], "transcribe", {"settings": settings.model_dump()})
    before = store.all("SELECT * FROM providers WHERE id<>? ORDER BY id", (saved["id"],))
    frozen = store.job(job["id"])
    response = choose(client, saved)
    assert response.status_code == 200, response.text
    selected = response.json()
    assert selected[providers.NATIVE_PRODUCT] == native.MIG_PRODUCT
    assert selected[providers.NATIVE_CHOICE] == "mig1g"
    assert selected["ucloud_transcriber"]["product"] == PRODUCT
    assert store.all("SELECT * FROM providers WHERE id<>? ORDER BY id", (saved["id"],)) == before
    assert store.job(job["id"]) == frozen
    assert all(
        request.method == "GET" or request.url.path in {"/auth/refresh", "/api/hpc/apps/search"}
        for request in account.calls
    )
    back = choose(client, selected, "full")
    assert back.status_code == 200, back.text
    assert back.json()["ucloud_transcriber"]["product"] == PRODUCT
    assert back.json()[providers.NATIVE_CHOICE] == "full"


@pytest.mark.parametrize(
    "failure",
    [
        "funding",
        "support",
        "identity",
        "application",
        "template",
        "metadata_name",
        "metadata_category",
        "metadata_provider",
    ],
)
def test_requested_mig_never_falls_back_or_changes_profile(
    client, allocation, failure, monkeypatch
):
    saved, _, account = allocation
    store = client.app.state.store
    if failure == "funding":
        account.wallets = account.wallets[:1]
    elif failure == "support":
        account.products[-1]["support"]["docker"]["enabled"] = False
    elif failure == "identity":
        account.products[-1]["support"]["product"]["category"] = "wrong-category"
    elif failure == "metadata_name":
        account.products[-1]["product"]["name"] = "different-product"
    elif failure == "metadata_category":
        account.products[-1]["product"]["category"]["name"] = "different-category"
    elif failure == "metadata_provider":
        account.products[-1]["product"]["category"]["provider"] = "foreign"
    else:
        payload = json.loads(
            store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
        )
        payload["ucloud_transcriber"]["application"]["version"] = (
            "other" if failure == "application" else "1.12"
        )
        if failure == "template":
            payload["ucloud_transcriber"]["template_job_id"] = "explicit-template"
        else:
            monkeypatch.setattr(ucloud_discovery, "_applications", lambda *_: {})
        store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), saved["id"]))
        saved = providers.public_profile(store, client.app.state.credentials, saved["id"])
    before = store.all("SELECT * FROM providers ORDER BY id")
    response = choose(client, saved)
    assert response.status_code == 400, response.text
    assert store.all("SELECT * FROM providers ORDER BY id") == before


@pytest.mark.parametrize("field", ["name", "value"])
def test_lookup_rechecks_version_before_commit(client, allocation, monkeypatch, field):
    saved, _, _ = allocation
    store = client.app.state.store
    discover = ucloud_discovery.discover

    def interleave(*args, **kwargs):
        result = discover(*args, **kwargs)
        if field == "name":
            store.execute(
                "UPDATE providers SET name='Concurrent change' WHERE id=?", (saved["id"],)
            )
        else:
            payload = providers.profile(store, saved["id"])["settings"]
            payload["language"] = "en"
            store.execute(
                "UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), saved["id"])
            )
        return result

    monkeypatch.setattr(ucloud_discovery, "discover", interleave)
    response = choose(client, saved)
    assert response.status_code == 409, response.text
    current = providers.public_profile(store, client.app.state.credentials, saved["id"])
    assert current["ucloud_transcriber"]["product"] == PRODUCT
    assert (
        current["name"] == "Concurrent change" if field == "name" else current["language"] == "en"
    )


def test_generic_save_preserves_preference_and_backfill_defaults_each_model(client, allocation):
    saved, _, _ = allocation
    store = client.app.state.store
    selected = choose(client, saved, "full").json()
    response = client.put(
        f"/api/providers/{saved['id']}",
        json={
            **selected,
            providers.NATIVE_CHOICE: "mig1g",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()[providers.NATIVE_CHOICE] == "full"
    assert response.json()["ucloud_transcriber"]["product"] == PRODUCT
    rows = store.all("SELECT * FROM providers ORDER BY id")
    assert providers.backfill_native_profiles(store) == 0
    assert store.all("SELECT * FROM providers ORDER BY id") == rows
    store.execute(
        "DELETE FROM providers WHERE id<>? AND json_extract(value,'$.provider_type')='ucloud'",
        (saved["id"],),
    )
    assert providers.backfill_native_profiles(store) > 0
    assert (
        providers.public_profile(store, client.app.state.credentials, saved["id"])[
            providers.NATIVE_CHOICE
        ]
        == "full"
    )
    assert all(
        json.loads(row["value"])[providers.NATIVE_CHOICE] == "mig1g"
        for row in store.all(
            "SELECT value FROM providers WHERE id<>? "
            "AND json_extract(value,'$.provider_type')='ucloud'",
            (saved["id"],),
        )
        if not providers.whisperx_model(providers.Settings.model_validate_json(row["value"]))
    )


def test_runtime_classification_rejects_native_selector_before_lookup(
    client, allocation, monkeypatch
):
    saved, _, account = allocation
    before = len(account.calls)
    monkeypatch.setattr(providers, "whisperx_model", lambda _: True)
    response = choose(client, saved)
    assert response.status_code == 400
    assert len(account.calls) == before


@pytest.mark.parametrize("model", ["whisper/large-v3", "nvidia/parakeet-tdt-0.6b-v3"])
def test_mig_is_explicitly_available_to_other_unverified_native_models(model):
    settings = providers.Settings(
        provider_type="ucloud",
        model=model,
        ucloud_transcriber={"application": native.MIG_APPLICATION, "product": native.MIG_PRODUCT},
    )
    native.require_compute(settings)


def test_checked_full_records_explicit_opt_out_even_when_base_product_unchanged(client, allocation):
    saved, _, _ = allocation
    store = client.app.state.store
    payload = json.loads(
        store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    )
    payload.pop(providers.NATIVE_CHOICE)
    payload.pop(providers.NATIVE_PRODUCT)
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), saved["id"]))
    saved = providers.public_profile(store, client.app.state.credentials, saved["id"])
    selected = choose(client, saved, "full")
    assert selected.status_code == 200, selected.text
    assert selected.json()["version"] != saved["version"]
    assert selected.json()[providers.NATIVE_CHOICE] == "full"
    assert selected.json()["ucloud_transcriber"]["product"] == PRODUCT
    assert providers.migrate_native_allocation_defaults(store) == 0


def test_runtime_connection_product_semantics_remain_full_b200(client):
    settings = providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="whisperx/large-v3",
        ucloud_transcriber={"application": native.MIG_APPLICATION, "product": native.MIG_PRODUCT},
    )
    # Legacy raw Runtime settings stay saveable/readable; new preflight keeps its prior gate.
    providers.save_profile(
        client.app.state.store, client.app.state.credentials, "Legacy Runtime", settings
    )
    with pytest.raises(ValueError):
        native.require_compute(settings)


@pytest.mark.parametrize("state", ["template", "invalid"])
def test_read_only_preference_echo_preserves_generic_approval_without_retarget(
    client,
    allocation,
    state,
):
    saved, _, account = allocation
    store = client.app.state.store
    payload = json.loads(
        store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    )
    if state == "template":
        payload["ucloud_transcriber"]["template_job_id"] = "explicit-template"
        payload.pop(providers.NATIVE_CHOICE)
        payload.pop(providers.NATIVE_PRODUCT)
    else:
        payload[providers.NATIVE_CHOICE] = "corrupt-marker"
    store.execute("UPDATE providers SET value=? WHERE id=?", (json.dumps(payload), saved["id"]))
    public = providers.public_profile(store, client.app.state.credentials, saved["id"])
    assert public[providers.NATIVE_CHOICE] == state
    before_calls = len(account.calls)
    response = client.put(
        f"/api/providers/{saved['id']}", json={**public, "approved_for": ["protected"]}
    )
    assert response.status_code == 200, response.text
    current = json.loads(
        store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))["value"]
    )
    assert current == {**payload, "approved_for": ["protected"]}
    assert len(account.calls) == before_calls
    # These public states cannot act as either a default or explicit allocation choice.
    draft = {**public, "api_key": SECRET}
    assert client.post("/api/providers/discover-ucloud", json=draft).status_code == 400
    assert client.post("/api/providers", json=draft).status_code == 400
    assert len(account.calls) == before_calls


def test_fresh_connection_defaults_to_mig_without_fallback_when_only_full_is_funded(
    client,
    account,
):
    from test_ucloud_discovery import settings

    store = client.app.state.store
    before = store.all("SELECT * FROM providers ORDER BY id")
    reply = client.post(
        "/api/providers/discover-ucloud", json={**settings().model_dump(), "api_key": SECRET}
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["ready"] is False
    assert reply.json()["setup"]["product"] is None
    assert store.all("SELECT * FROM providers ORDER BY id") == before
