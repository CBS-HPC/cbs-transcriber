import re

import pytest

from cbs_transcriber.catalog import speech_catalog


def test_v29_offline_catalog_has_pinned_candidate_provenance(client, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Catalog selection must stay offline")

    monkeypatch.setattr("httpx.Client.send", no_network)
    catalog = client.get("/api/providers/catalog").json()
    assert catalog == speech_catalog()
    assert len(catalog["models"]) == 9
    assert len({m["id"] for m in catalog["models"]}) == 9
    for model in catalog["models"]:
        assert re.fullmatch(r"[0-9a-f]{40}", model["revision"])
        assert model["parameters"] > 0
        assert model["revision"] in model["parameter_source"]
        assert model["revision"] in model["source"]
        assert set(model["languages"]) <= {"da", "en"}
        assert model["status"] == "candidate"
        assert not model["runtime"]["validated"] and model["tested_allocation"] is None
    hviske = next(m for m in catalog["models"] if m["id"] == "syvai/hviske-v5.3")
    assert hviske["suggested_response"] == "json"
    assert hviske["license"] == "cc-by-nc-4.0"


@pytest.mark.parametrize(
    "provider_type,extra,approved",
    [
        ("openai", {}, []),
        ("custom", {"base_url": "https://custom.test/v1"}, []),
        ("ucloud-vllm", {"base_url": "", "model": "openai/whisper-large-v3"}, ["protected"]),
        ("ucloud", {"base_url": "https://cloud.sdu.dk"}, ["protected"]),
    ],
)
def test_v28_new_defaults_and_saved_explicit_choices(client, provider_type, extra, approved):
    body = {"name": "Policy", "provider_type": provider_type, **extra}
    response = client.post("/api/providers", json=body)
    assert response.status_code == 201, response.text
    provider = response.json()
    assert provider["approved_for"] == approved
    identifier = provider["id"]
    # A full update which omits policy must retain the saved choice, not reapply a default.
    chosen = [] if approved else ["protected"]
    assert (
        client.put(f"/api/providers/{identifier}", json={**body, "approved_for": chosen}).json()[
            "approved_for"
        ]
        == chosen
    )
    assert client.put(f"/api/providers/{identifier}", json=body).json()["approved_for"] == chosen
    # Explicit choices on creation take precedence too.
    assert (
        client.post("/api/providers", json={**body, "approved_for": chosen}).json()["approved_for"]
        == chosen
    )


def test_v29_multiple_offline_models_use_hf_served_identity_and_keep_legacy_jobs(client):
    settings = {
        "provider_type": "ucloud-vllm",
        "base_url": "",
        "model": "legacy-alias",
        "ucloud": {"hf_model": "openai/whisper-large-v3"},
    }
    a = client.post("/api/providers", json={"name": "Large", **settings}).json()
    settings["ucloud"]["hf_model"] = "syvai/hviske-v5.3"
    b = client.post(
        "/api/providers", json={"name": "Danish", **settings, "capability": "json"}
    ).json()
    assert a["model"] == "openai/whisper-large-v3"
    assert b["model"] == "syvai/hviske-v5.3"
    assert a["id"] != b["id"] and a["capability"] != b["capability"]
    assert client.get("/api/jobs").json() == []
    # Reading an old snapshot never changes the served identity of work already queued.
    from cbs_transcriber.providers import Settings

    assert Settings.model_validate(settings).model == "legacy-alias"
    assert (
        client.post(
            "/api/providers",
            json={
                "name": "Invalid",
                "provider_type": "ucloud-vllm",
                "base_url": "",
                "model": "alias-only",
            },
        ).status_code
        == 400
    )
