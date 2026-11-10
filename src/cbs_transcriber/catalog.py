"""Pinned offline speech candidates; selection never contacts Hugging Face or UCloud."""

import json
import os
from functools import cache
from importlib.resources import files
from pathlib import Path

from .danish_models import CHOICES as DANISH_WHISPERX_MODELS

# UCloud catalog entries run on the CBS WhisperX runtime instead of the Transcriber app (T70).
WHISPERX_MODELS = {"small", "medium", "large-v3-turbo", "large-v3"} | set(DANISH_WHISPERX_MODELS)
DANISH_RUNTIME_MODELS = {"whisperx/" + choice: choice for choice in DANISH_WHISPERX_MODELS}


def whisperx_model(settings):
    """The pinned WhisperX model behind a UCloud model profile, or None for native models."""
    if getattr(settings, "provider_type", None) != "ucloud":
        return None
    entry = next(
        (
            m
            for m in provider_catalog()["ucloud_cbs_runtime"]["models"]
            if m["model"] == settings.model
        ),
        None,
    )
    return entry.get("whisper_model") if entry and entry.get("engine") == "whisperx" else None


def runtime_model_name(settings):
    """Display name of an exact CBS Runtime catalog model profile, or None."""
    if not whisperx_model(settings):
        return None
    entry = next(
        m
        for m in provider_catalog()["ucloud_cbs_runtime"]["models"]
        if m["model"] == settings.model
    )
    return entry.get("name") or settings.model


def fixed_runtime_language(settings):
    """Fixed language of an exact catalog Runtime model; legacy models keep their protocol."""
    return "da" if whisperx_model(settings) in DANISH_WHISPERX_MODELS else None


@cache
def speech_catalog():
    return json.loads(
        files("cbs_transcriber").joinpath("catalogs/speech_models.json").read_text(encoding="utf-8")
    )


def ucloud_models():
    """Model profiles share a UCloud connection, but keep their execution catalogs separate."""
    catalog = provider_catalog()
    return catalog["ucloud_transcriber"]["models"] + catalog["ucloud_cbs_runtime"]["models"]


def _legacy_catalog(catalog):
    """Normalize old user overrides without changing their file, IDs or frozen settings."""
    if not isinstance(catalog, dict):
        raise ValueError()
    if catalog.get("schema_version") != 1:
        return catalog
    original = catalog["ucloud_transcriber"]
    if (
        not isinstance(original, dict)
        or not isinstance(original.get("models"), list)
        or any(not isinstance(model, dict) for model in original["models"])
    ):
        raise ValueError()
    native = [m for m in original["models"] if m.get("engine", "native") != "whisperx"]
    runtime = [m for m in original["models"] if m.get("engine") == "whisperx"]
    if "ucloud_cbs_runtime" in catalog:
        raise ValueError()
    catalog["ucloud_transcriber"] = {
        **original,
        "models": native,
        "default": original["default"]
        if any(m["model"] == original["default"] for m in native)
        else native[0]["model"]
        if native
        else None,
    }
    catalog["ucloud_cbs_runtime"] = {
        "models": runtime,
        "default": original["default"]
        if any(m["model"] == original["default"] for m in runtime)
        else runtime[0]["model"]
        if runtime
        else None,
        "source": "https://github.com/m-bain/whisperX",
    }
    catalog["schema_version"] = 2
    return catalog


@cache
def provider_catalog():
    """Editable presets shared by the browser and request validation; restart to reload."""
    override = os.environ.get("CBS_TRANSCRIBER_MODELS")
    source = (
        Path(override)
        if override
        else files("cbs_transcriber").joinpath("catalogs/provider_models.json")
    )
    try:
        catalog = _legacy_catalog(json.loads(source.read_text(encoding="utf-8")))
        if catalog["schema_version"] != 2:
            raise ValueError()
        ucloud_ids = []
        for provider in ("openai", "ucloud_transcriber", "ucloud_cbs_runtime"):
            group = catalog[provider]
            if not isinstance(group, dict) or not isinstance(group.get("models"), list):
                raise ValueError()
            models = group["models"]
            if any(not isinstance(model, dict) for model in models):
                raise ValueError()
            ids = [m["model"] for m in models]
            if (
                len(ids) != len(set(ids))
                or (models and group["default"] not in ids)
                or (not models and (provider == "openai" or group["default"] is not None))
            ):
                raise ValueError()
            if provider != "openai":
                ucloud_ids.extend(ids)
            for model in models:
                if (
                    not isinstance(model["model"], str)
                    or not 0 < len(model["model"]) <= 200
                    or not isinstance(model["name"], str)
                    or model["capability"]
                    not in {"words", "words_speakers", "segments", "speakers", "json"}
                    or model.get("language_parameter", "language") not in {"language", "languages"}
                    or not isinstance(model.get("logprobs", False), bool)
                    or not isinstance(model.get("default_enabled", True), bool)
                    or (
                        provider == "ucloud_transcriber"
                        and model.get("engine", "native") != "native"
                    )
                    or (provider == "ucloud_cbs_runtime" and model.get("engine") != "whisperx")
                    or (provider == "openai" and model.get("engine", "native") != "native")
                    or (
                        "fixed_language" in model
                        and (
                            provider != "ucloud_cbs_runtime"
                            or model["fixed_language"] != "da"
                            or model.get("whisper_model") not in DANISH_WHISPERX_MODELS
                        )
                    )
                    or (
                        (
                            model["model"] in DANISH_RUNTIME_MODELS
                            or model.get("whisper_model") in DANISH_WHISPERX_MODELS
                        )
                        and (
                            provider != "ucloud_cbs_runtime"
                            or DANISH_RUNTIME_MODELS.get(model["model"])
                            != model.get("whisper_model")
                            or model.get("fixed_language") != "da"
                        )
                    )
                    or (
                        model.get("engine") == "whisperx"
                        and (
                            provider != "ucloud_cbs_runtime"
                            or model.get("whisper_model") not in WHISPERX_MODELS
                            or model["capability"] != "words"
                        )
                    )
                ):
                    raise ValueError()
            if provider == "openai":
                keys = [m["key"] for m in models]
                if len(keys) != len(set(keys)):
                    raise ValueError()
        if not ucloud_ids or len(ucloud_ids) != len(set(ucloud_ids)):
            raise ValueError()
        return catalog
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            "Invalid provider model catalog. Check provider_models.json configuration."
        ) from None
