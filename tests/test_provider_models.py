import copy
import json

import pytest

from cbs_transcriber.catalog import provider_catalog, ucloud_models, whisperx_model
from cbs_transcriber.providers import OPENAI_MODELS, Settings


def test_v55_shared_presets_match_backend_and_documented_native_models(client, monkeypatch):
    monkeypatch.setattr("httpx.Client.send", lambda *a, **kw: pytest.fail("Offline catalog"))
    data = client.get("/api/providers/model-presets").json()
    assert {
        m["model"]: (m["name"], m["capability"]) for m in data["openai"]["models"]
    } == OPENAI_MODELS
    for model in data["openai"]["models"]:
        assert Settings(model=model["model"], capability="json").capability == model["capability"]
    models = data["ucloud_transcriber"]["models"]
    assert {m["model"] for m in models} == {
        "base",
        "small",
        "medium",
        "large-v3",
        "large-v3-turbo",
        "nvidia/parakeet-tdt-0.6b-v3",
    }
    assert all(m.get("engine", "native") == "native" for m in models)
    assert all(m["name"].endswith("(Transcriber)") for m in models)
    runtime = data["ucloud_cbs_runtime"]["models"]
    assert {m["model"]: m["whisper_model"] for m in runtime} == {
        "whisperx/large-v3": "large-v3",
        "whisperx/large-v3-turbo": "large-v3-turbo",
        "whisperx/edda-turbo": "edda-turbo",
        "whisperx/edda-large": "edda-large",
        "whisperx/roest-v3": "roest-v3",
    }
    assert {m["model"]: m["fixed_language"] for m in runtime if "fixed_language" in m} == {
        "whisperx/edda-turbo": "da",
        "whisperx/edda-large": "da",
        "whisperx/roest-v3": "da",
    }
    assert all(
        m["engine"] == "whisperx"
        and m["name"].endswith(
            "(CBS Runtime) (Danish)" if m.get("fixed_language") == "da" else "(CBS Runtime)"
        )
        for m in runtime
    )
    assert {m["model"] for m in ucloud_models()} == {m["model"] for m in models + runtime}
    for model in models + runtime:
        chosen = Settings(provider_type="ucloud", model=model["model"])
        assert whisperx_model(chosen) == model.get("whisper_model")
    assert (
        Settings(
            provider_type="custom", model="new/private-model", capability="segments"
        ).capability
        == "segments"
    )


@pytest.mark.parametrize(
    "change", ["duplicate", "capability", "default", "language", "missing", "json"]
)
def test_v55_invalid_catalog_has_clear_redacted_error(tmp_path, monkeypatch, change):
    data = copy.deepcopy(provider_catalog())
    if change == "duplicate":
        data["openai"]["models"].append(data["openai"]["models"][0])
    elif change == "capability":
        data["openai"]["models"][0]["capability"] = "private-secret"
    elif change == "default":
        data["openai"]["default"] = "unknown"
    elif change == "language":
        data["openai"]["models"][0]["language_parameter"] = "private-secret"
    elif change == "missing":
        del data["ucloud_transcriber"]
    path = tmp_path / "models.json"
    path.write_text("private-secret" if change == "json" else json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(path))
    provider_catalog.cache_clear()
    try:
        with pytest.raises(ValueError, match="Invalid provider model catalog") as error:
            provider_catalog()
        assert "private-secret" not in str(error.value)
    finally:
        provider_catalog.cache_clear()


def test_v55_catalog_override_accepts_new_presets(tmp_path, monkeypatch):
    data = copy.deepcopy(provider_catalog())
    data["openai"]["models"].append(
        {
            "key": "future",
            "model": "future-transcribe",
            "name": "OpenAI future-transcribe",
            "capability": "segments",
        }
    )
    path = tmp_path / "models.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(path))
    provider_catalog.cache_clear()
    try:
        assert provider_catalog()["openai"]["models"][-1]["model"] == "future-transcribe"
    finally:
        provider_catalog.cache_clear()


@pytest.mark.parametrize("include_runtime", [True, False])
def test_legacy_catalog_override_splits_engines_without_changing_file_or_ids(
    tmp_path, monkeypatch, include_runtime
):
    data = copy.deepcopy(provider_catalog())
    data["schema_version"] = 1
    runtime = data.pop("ucloud_cbs_runtime")["models"]
    if include_runtime:
        data["ucloud_transcriber"]["models"].extend(runtime)
    data["ucloud_transcriber"]["models"][0]["name"] = "Researcher's preserved custom label"
    original = json.dumps(data, ensure_ascii=False)
    path = tmp_path / "models.json"
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(path))
    provider_catalog.cache_clear()
    try:
        normalized = provider_catalog()
        assert normalized["schema_version"] == 2
        assert len(normalized["ucloud_transcriber"]["models"]) == 6
        assert (
            normalized["ucloud_transcriber"]["models"][0]["name"]
            == "Researcher's preserved custom label"
        )
        assert normalized["ucloud_cbs_runtime"]["models"] == (runtime if include_runtime else [])
        assert path.read_text(encoding="utf-8") == original
    finally:
        provider_catalog.cache_clear()


@pytest.mark.parametrize(
    "bad",
    ["wrong_engine", "cross_group_duplicate", "missing_runtime", "not_object", "malformed_models"],
)
def test_catalog_rejects_misclassified_or_malformed_model_groups(tmp_path, monkeypatch, bad):
    data = copy.deepcopy(provider_catalog())
    if bad == "wrong_engine":
        data["ucloud_transcriber"]["models"].append(data["ucloud_cbs_runtime"]["models"].pop())
    elif bad == "cross_group_duplicate":
        data["ucloud_transcriber"]["models"][0]["model"] = data["ucloud_cbs_runtime"]["models"][0][
            "model"
        ]
    elif bad == "missing_runtime":
        del data["ucloud_cbs_runtime"]
    elif bad == "not_object":
        data = []
    else:
        data["ucloud_cbs_runtime"]["models"] = [None]
    path = tmp_path / "models.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", str(path))
    provider_catalog.cache_clear()
    try:
        with pytest.raises(ValueError, match="Invalid provider model catalog"):
            provider_catalog()
    finally:
        provider_catalog.cache_clear()
