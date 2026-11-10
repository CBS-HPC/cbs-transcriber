"""Explicit provider capabilities and credentials kept on the Python side."""

import hashlib
import json
import os
import re
from typing import Literal
from urllib.parse import urlparse

import keyring
from openai import APIConnectionError, APIStatusError, APITimeoutError, DefaultHttpxClient, OpenAI
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from .catalog import fixed_runtime_language, provider_catalog, ucloud_models, whisperx_model
from .transcripts import normalize_segments
from .ucloud_native import (
    DEFAULT_NATIVE_JOB_HOURS,
    NATIVE_AUTO_LANGUAGE_ERROR,
    NativeSetup,
    require_setup,
)

OPENAI_URL = "https://api.openai.com/v1"
OPENAI_AUTO_LANGUAGE_ERROR = (
    "Leave the OpenAI language hint blank for automatic language detection; do not enter 'auto'."
)
FIXED_RUNTIME_LANGUAGE_ERROR = "This Danish CBS Runtime model requires language 'da' (Danish)."
OPENAI_MODELS = {
    m["model"]: (m["name"], m["capability"]) for m in provider_catalog()["openai"]["models"]
}
CLASSIFICATIONS = ("standard", "protected")
LEGACY_CLASSIFICATIONS = {"sensitive", "restricted", "confidential"}
LiteralClassification = Literal["protected"]
PROTECTED_DEFAULTS = {"openai": False, "custom": False, "ucloud-vllm": True, "ucloud": True}


def protected_approvals(values):
    """Consolidate old approvals without granting previously unapproved access."""
    if (
        isinstance(values, list)
        and all(isinstance(value, str) for value in values)
        and set(values) <= LEGACY_CLASSIFICATIONS | {"protected"}
    ):
        return (
            ["protected"] if "protected" in values or LEGACY_CLASSIFICATIONS <= set(values) else []
        )
    return values


class UCloudSetup(BaseModel):
    server: str = "https://cloud.sdu.dk"
    project_id: str = Field(default="", max_length=200)
    hf_model: str = Field(default="", max_length=200)
    hf_revision: str = Field(default="main", min_length=1, max_length=200)

    @field_validator("hf_model")
    @classmethod
    def validate_hf_model(cls, value):
        if value and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError("Use a Hugging Face owner/model identifier.")
        return value

    @field_validator("server")
    @classmethod
    def validate_server(cls, value):
        parsed = urlparse(value.strip().rstrip("/"))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise ValueError("Use the HTTPS UCloud server root without credentials or a path.")
        return value.strip().rstrip("/")


def setup_key_scopes(settings):
    return {
        "ucloud_token": "ucloud-control:" + settings.ucloud.server,
        "hf_token": "huggingface:https://huggingface.co",
    }


def normalize_vllm_model(settings):
    """Canonicalize saved configuration identity, leaving old queued snapshots untouched."""
    if settings.provider_type == "ucloud-vllm":
        model = settings.ucloud.hf_model or settings.model
        UCloudSetup.validate_hf_model(model)
        settings.ucloud = settings.ucloud.model_copy(update={"hf_model": model})
        settings.model = model


def _uses_openai_language_hint(settings):
    return (
        settings.provider_type == "openai"
        or urlparse(settings.base_url).hostname == "api.openai.com"
    )


class Settings(BaseModel):
    provider_type: Literal["openai", "custom", "ucloud-vllm", "ucloud"] | None = None
    base_url: str = OPENAI_URL
    model: str = Field(default="whisper-1", min_length=1, max_length=200)
    language: str = Field(default="", max_length=20)
    capability: str = "words"
    enabled: bool = Field(default=True, strict=True)
    max_upload_mb: int = Field(default=20, ge=1, le=25)
    approved_for: list[LiteralClassification] = Field(default_factory=list)
    concurrent_chunks: int = Field(default=1, ge=1, le=4)
    ucloud: UCloudSetup | None = None
    ucloud_transcriber: NativeSetup | None = None

    @field_validator("approved_for", mode="before")
    @classmethod
    def normalize_approvals(cls, value):
        return protected_approvals(value)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value, info: ValidationInfo):
        value = value.strip().rstrip("/")
        if info.data.get("provider_type") == "ucloud":
            return UCloudSetup.validate_server(value)
        if not value and info.data.get("provider_type") == "ucloud-vllm":
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Use an HTTP(S) API base URL, including /v1 where required.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Do not include credentials, query strings, or fragments in the URL.")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for remote APIs; HTTP is allowed only on loopback.")
        if info.data.get("provider_type") == "ucloud-vllm" and not parsed.path.endswith("/v1"):
            value += "/v1"
        return value

    @field_validator("capability")
    @classmethod
    def validate_capability(cls, value):
        if value not in {"words", "words_speakers", "segments", "speakers", "json"}:
            raise ValueError(
                "Choose words, words_speakers, segments, speakers, or json capabilities."
            )
        return value

    @model_validator(mode="after")
    def supported_openai_format(self):
        if self.provider_type is None:
            self.provider_type = (
                "openai"
                if self.base_url == OPENAI_URL and self.model in OPENAI_MODELS
                else "custom"
            )
        if self.provider_type == "ucloud-vllm" and self.base_url == OPENAI_URL:
            raise ValueError("Use your UCloud vLLM serving endpoint, not OpenAI's endpoint.")
        if self.provider_type == "ucloud-vllm" and self.ucloud is None:
            self.ucloud = UCloudSetup()
        if self.provider_type != "ucloud-vllm":
            self.ucloud = None
        if self.provider_type == "ucloud":
            self.ucloud_transcriber = self.ucloud_transcriber or NativeSetup()
        else:
            self.ucloud_transcriber = None
        # Old saved configurations and job snapshots may pair diarize with Whisper fields.
        if self.base_url == OPENAI_URL and self.model in OPENAI_MODELS:
            self.capability = OPENAI_MODELS[self.model][1]
        return self


def fresh_native_settings(settings):
    """Choose the new-request default without changing legacy/frozen Settings parsing."""
    setup = settings.ucloud_transcriber
    if (
        settings.provider_type == "ucloud"
        and not whisperx_model(settings)
        and setup is not None
        and "hours" not in setup.model_fields_set
    ):
        return settings.model_copy(
            update={
                "ucloud_transcriber": setup.model_copy(update={"hours": DEFAULT_NATIVE_JOB_HOURS})
            }
        )
    return settings


def require_key(settings, key):
    if settings.provider_type == "ucloud":
        from .ucloud_native import require_setup

        require_setup(settings, key)
        return
    if settings.provider_type == "ucloud-vllm" and not settings.base_url:
        raise ValueError("Add the deployed UCloud vLLM /v1 endpoint before transcribing.")
    if not key:
        if settings.provider_type == "ucloud-vllm":
            raise ValueError("Add the UCloud vLLM serving API key in Settings.")
        if settings.base_url == OPENAI_URL:
            raise ValueError("Add your OpenAI API key in Settings before transcribing.")


class Credentials:
    def __init__(self):
        self.session_keys = {}

    @staticmethod
    def account(base_url):
        return hashlib.sha256(base_url.encode()).hexdigest()

    def get(self, base_url):
        if base_url in self.session_keys:
            return self.session_keys[base_url]
        try:
            saved = keyring.get_password("CBS Transcriber", self.account(base_url))
        except keyring.errors.KeyringError:
            saved = None
        if saved:
            return saved
        return os.environ.get("OPENAI_API_KEY", "") if base_url == OPENAI_URL else ""

    def set(self, base_url, value, persist):
        if persist:
            try:
                if value:
                    keyring.set_password("CBS Transcriber", self.account(base_url), value)
                else:
                    try:
                        keyring.delete_password("CBS Transcriber", self.account(base_url))
                    except keyring.errors.PasswordDeleteError:
                        if (
                            keyring.get_password("CBS Transcriber", self.account(base_url))
                            is not None
                        ):
                            raise
            except keyring.errors.KeyringError as exc:
                raise ValueError(
                    "The OS credential store is unavailable. Turn off Remember key "
                    "to use it for this server session."
                ) from exc
        self.session_keys[base_url] = value


def load_settings(store):
    return Settings.model_validate(profile(store, default_provider_id(store))["settings"])


def default_provider_id(store):
    return json.loads(store.one("SELECT value FROM settings WHERE id=2")["value"])[
        "default_provider_id"
    ]


def profile(store, identifier):
    row = store.one("SELECT * FROM providers WHERE id=?", (identifier,))
    return {
        "id": row["id"],
        "name": row["name"],
        "settings": Settings.model_validate_json(row["value"]).model_dump(),
    }


NATIVE_CHOICE = "native_allocation_choice"
NATIVE_PRODUCT = "native_allocation_product"


def _native_preference(settings, payload):
    """Profile metadata only: never add preferences to normalized/frozen Settings."""
    from .ucloud_native import COMPUTE_ID, MIG_PRODUCT

    if settings.provider_type != "ucloud" or whisperx_model(settings):
        return None
    if NATIVE_CHOICE not in payload:
        if NATIVE_PRODUCT in payload:
            raise ValueError("The saved native allocation preference is incomplete.")
        if settings.ucloud_transcriber.template_job_id:
            return None  # Explicit template bindings keep their captured machine.
        return "mig1g", dict(MIG_PRODUCT)
    choice = payload[NATIVE_CHOICE]
    product = payload.get(NATIVE_PRODUCT)
    if choice not in ("full", "mig1g") or not isinstance(product, dict):
        raise ValueError("The saved native allocation preference is invalid.")
    product = NativeSetup(product=product).product
    if (choice == "mig1g" and product != MIG_PRODUCT) or (
        choice == "full" and product["id"] != COMPUTE_ID
    ):
        raise ValueError("The saved native allocation preference contradicts its product.")
    if settings.ucloud_transcriber.template_job_id:
        if product != settings.ucloud_transcriber.product:
            raise ValueError("Reconnect without the template before changing its allocation.")
        return None
    return choice, product


def admission_profiles(store, identifiers):
    """Freeze the fixed native MIG allocation once for each fresh request."""
    from .ucloud_native import MIG_PRODUCT

    selected, allocations = [], {}
    with store.connection() as db:
        db.execute("BEGIN")
        for identifier in identifiers:
            row = db.execute("SELECT * FROM providers WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            payload = json.loads(row["value"])
            settings = Settings.model_validate(payload)
            selected.append(
                {"id": identifier, "name": row["name"], "settings": settings.model_dump()}
            )
            if settings.provider_type == "ucloud" and not whisperx_model(settings):
                if settings.ucloud_transcriber.template_job_id:
                    raise ValueError(
                        "New native requests require a 1g MIG. Reconnect UCloud without "
                        "the existing template to select the fixed allocation."
                    )
                # Legacy allocation metadata is not execution authority for new work.
                # Keep it untouched alongside frozen jobs and the saved connection.
                allocations[identifier] = {
                    "policy": "native-allocation-v1",
                    "product": dict(MIG_PRODUCT),
                }
    return selected, allocations


def migrate_native_allocation_defaults(store):
    """Marker-only, idempotent default transition under the caller's serving lease.

    The pre-feature app forced Full B200; it had no explicit-choice marker. Never rewrite
    connection Settings, templates, Runtime rows, historical jobs or saved observations.
    """
    from .ucloud_native import MIG_PRODUCT

    changed = 0
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT id,value FROM providers").fetchall():
            payload = json.loads(row["value"])
            settings = Settings.model_validate(payload)
            if (
                settings.provider_type != "ucloud"
                or whisperx_model(settings)
                or settings.ucloud_transcriber.template_job_id
                or NATIVE_CHOICE in payload
                or NATIVE_PRODUCT in payload
            ):
                continue
            payload[NATIVE_CHOICE], payload[NATIVE_PRODUCT] = "mig1g", dict(MIG_PRODUCT)
            db.execute(
                "UPDATE providers SET value=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), row["id"]),
            )
            changed += 1
    return changed


def credential_scope(settings):
    return (
        "ucloud-control:" + settings.base_url
        if settings.provider_type == "ucloud"
        else settings.base_url
    )


class ApprovalDenied(ValueError):
    pass


class StaleProvider(ValueError):
    """The saved provider changed after this form loaded it."""


def stored_version(name, value):
    """Opaque version of one saved profile, compared on save to reject stale forms."""
    return hashlib.sha256((name + "\0" + value).encode("utf-8")).hexdigest()[:16]


def public_profile(store, credentials, identifier):
    raw = store.one("SELECT * FROM providers WHERE id=?", (identifier,))
    settings = Settings.model_validate_json(raw["value"])
    result = {
        "id": raw["id"],
        "version": stored_version(raw["name"], raw["value"]),
        "name": raw["name"],
        **settings.model_dump(),
        "has_key": bool(settings.base_url and credentials.get(credential_scope(settings))),
    }
    if any(
        json.loads(raw["value"]).get(key) is True
        for key in ("_initial_model_defaults", "_pending_runtime_defaults")
    ):
        result["initial_model_defaults"] = True
    if settings.provider_type == "ucloud" and not whisperx_model(settings):
        try:
            preference = _native_preference(settings, json.loads(raw["value"]))
            result[NATIVE_CHOICE] = preference[0] if preference else "template"
            result[NATIVE_PRODUCT] = (
                preference[1] if preference else settings.ucloud_transcriber.product
            )
        except ValueError:
            result[NATIVE_CHOICE], result[NATIVE_PRODUCT] = "invalid", None
    if settings.provider_type == "ucloud-vllm":
        result.update(
            {
                "has_" + name: bool(credentials.get(scope))
                for name, scope in setup_key_scopes(settings).items()
            }
        )
    return result


def public_profiles(store, credentials):
    return {
        "protected_defaults": PROTECTED_DEFAULTS,
        "default_provider_id": default_provider_id(store),
        "providers": [
            public_profile(store, credentials, row["id"])
            for row in store.all("SELECT id FROM providers ORDER BY created,id")
        ],
    }


def save_runtime_language(store, credentials, identifier, language, expected_version):
    """Change one Runtime model's raw language field, with no connection/model fan-out."""
    if not isinstance(language, str) or language not in {"", "da", "en", "auto"}:
        raise ValueError("Choose a blank, Danish, English, or Automatic Runtime language.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM providers WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        if (
            not isinstance(expected_version, str)
            or not re.fullmatch(r"[a-f0-9]{16}", expected_version)
            or stored_version(row["name"], row["value"]) != expected_version
        ):
            raise StaleProvider(
                "These settings changed in another window. Reopen "
                "Settings to see the current values; nothing was saved."
            )
        payload = json.loads(row["value"])
        settings = Settings.model_validate(payload)
        if settings.provider_type != "ucloud" or not whisperx_model(settings):
            raise ValueError("Choose an existing CBS Runtime / WhisperX model configuration.")
        if (fixed := fixed_runtime_language(settings)) and language != fixed:
            raise ValueError(FIXED_RUNTIME_LANGUAGE_ERROR)
        if language == "auto" and _uses_openai_language_hint(settings):
            raise ValueError(OPENAI_AUTO_LANGUAGE_ERROR)
        if payload.get("language") != language or "language" not in payload:
            payload["language"] = language
            db.execute(
                "UPDATE providers SET value=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), identifier),
            )
    return public_profile(store, credentials, identifier)


def save_native_language(store, credentials, identifier, language, expected_version):
    """Change one native Transcriber profile without rewriting its connection or jobs."""
    if not isinstance(language, str) or len(language) > 20 or language.strip().casefold() == "auto":
        raise ValueError("Use the UCloud default or a supported language code; do not enter auto.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM providers WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        if (
            not isinstance(expected_version, str)
            or not re.fullmatch(r"[a-f0-9]{16}", expected_version)
            or stored_version(row["name"], row["value"]) != expected_version
        ):
            raise StaleProvider("These settings changed. Reopen Settings; nothing was saved.")
        payload = json.loads(row["value"])
        settings = Settings.model_validate(payload)
        if settings.provider_type != "ucloud" or whisperx_model(settings):
            raise ValueError("Choose an existing UCloud Transcriber model configuration.")
        if payload.get("language") != language or "language" not in payload:
            payload["language"] = language
            db.execute(
                "UPDATE providers SET value=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), identifier),
            )
    return public_profile(store, credentials, identifier)


def save_native_allocation(store, credentials, identifier, allocation, expected_version):
    """Resolve fresh eligibility outside locks, then change one row's execution preference."""
    from . import ucloud_discovery
    from . import ucloud_native as native

    if allocation not in {"full", "mig1g"}:
        raise ValueError("Choose full B200 or native 1g MIG.")
    row = store.one("SELECT * FROM providers WHERE id=?", (identifier,))
    if stored_version(row["name"], row["value"]) != expected_version:
        raise StaleProvider("These settings changed. Reopen Settings; nothing was saved.")
    payload = json.loads(row["value"])
    settings = Settings.model_validate(payload)
    if settings.provider_type != "ucloud" or whisperx_model(settings):
        raise ValueError("Choose a native Transcriber model, not a CBS Runtime model.")
    setup = settings.ucloud_transcriber
    if setup.template_job_id or not setup.application:
        raise ValueError(
            "Reconnect to a direct Transcriber Batch application without a template first."
        )
    key = credentials.get(credential_scope(settings))
    require_setup(settings, key)
    target = native.MIG_PRODUCT["id"] if allocation == "mig1g" else native.COMPUTE_ID
    product = (
        native.MIG_PRODUCT
        if allocation == "mig1g"
        else setup.product
        if (setup.product or {}).get("id") == native.COMPUTE_ID
        else None
    )
    candidate = settings.model_copy(
        update={"ucloud_transcriber": setup.model_copy(update={"product": product})}
    )
    resolved = ucloud_discovery.discover(candidate, key, compute_id=target)
    if not resolved["ready"]:
        raise ValueError(resolved["note"])
    observed = native.NativeSetup.model_validate(resolved["setup"])
    if observed.model_dump(exclude={"product"}) != setup.model_dump(exclude={"product"}):
        raise ValueError("The UCloud connection changed during lookup. Reconnect before choosing.")
    native.require_compute(candidate.model_copy(update={"ucloud_transcriber": observed}))
    if observed.product["id"] != target:
        raise ValueError("UCloud did not confirm the requested allocation. Nothing was saved.")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT name,value FROM providers WHERE id=?", (identifier,)
        ).fetchone()
        if current is None:
            raise KeyError(identifier)
        if stored_version(*current) != expected_version:
            raise StaleProvider("These settings changed during lookup. Nothing was saved.")
        if (
            payload.get(NATIVE_CHOICE) != allocation
            or payload.get(NATIVE_PRODUCT) != observed.product
        ):
            payload[NATIVE_CHOICE], payload[NATIVE_PRODUCT] = allocation, observed.product
            db.execute(
                "UPDATE providers SET value=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), identifier),
            )
    return public_profile(store, credentials, identifier)


def _model_profiles(db, settings):
    """A built-in connection groups only the same endpoint and native project."""
    return [
        (row, saved)
        for row in db.execute("SELECT * FROM providers").fetchall()
        if (saved := Settings.model_validate_json(row["value"])).provider_type
        == settings.provider_type
        and saved.base_url == settings.base_url
        and (
            settings.provider_type != "ucloud"
            or saved.ucloud_transcriber.project_id == settings.ucloud_transcriber.project_id
        )
    ]


def _catalog_models(settings):
    if settings.provider_type == "ucloud":
        return ucloud_models()
    return provider_catalog()["openai"]["models"] if settings.provider_type == "openai" else []


def _can_expand_models(settings):
    if settings.provider_type != "ucloud":
        return bool(_catalog_models(settings))
    from .ucloud_native import require_connection

    try:
        require_setup(settings, "offline-setup-check")
        require_connection(settings)
    except ValueError:
        return False
    return bool(_catalog_models(settings))


def _append_models(db, settings, *, fresh_runtime_defaults=False):
    """Copy a complete connection into missing catalog identities, leaving existing rows exact."""
    from .store import new_id, now
    from .ucloud_native import MIG_PRODUCT

    models = _catalog_models(settings)
    if not _can_expand_models(settings):
        return 0
    existing = {saved.model for _, saved in _model_profiles(db, settings)}
    added = 0
    for model in models:
        if model["model"] in existing:
            continue
        configured = settings.model_copy(
            update={
                "model": model["model"],
                "capability": model["capability"],
                "enabled": not (
                    fresh_runtime_defaults
                    and model.get("engine") == "whisperx"
                    and model.get("default_enabled", True) is False
                ),
                "language": model.get("fixed_language")
                or ("" if settings.language.strip().casefold() == "auto" else settings.language),
            },
            deep=True,
        )
        payload = configured.model_dump()
        if (
            settings.provider_type == "ucloud"
            and not whisperx_model(configured)
            and not configured.ucloud_transcriber.template_job_id
        ):
            payload[NATIVE_CHOICE], payload[NATIVE_PRODUCT] = "mig1g", dict(MIG_PRODUCT)
        db.execute(
            "INSERT INTO providers VALUES (?,?,?,?)",
            (
                new_id(),
                model["name"],
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now(),
            ),
        )
        existing.add(model["model"])
        added += 1
    return added


def _apply_pending_model_choices(db, settings, choices, existing):
    if not isinstance(choices, dict):
        return
    for row, saved in _model_profiles(db, settings):
        enabled = choices.get(saved.model)
        if saved.model not in existing and type(enabled) is bool and saved.enabled != enabled:
            payload = json.loads(row["value"])
            payload["enabled"] = enabled
            db.execute(
                "UPDATE providers SET value=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), row["id"]),
            )


def backfill_native_profiles(store):
    """Offline, idempotent startup backfill; the caller holds the running library lease."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id,value FROM providers ORDER BY created,id").fetchall()
        added = 0
        for row in rows:
            settings = Settings.model_validate_json(row["value"])
            if settings.provider_type != "ucloud":
                continue
            payload = json.loads(row["value"])
            pending = payload.get("_pending_runtime_defaults") is True
            group = _model_profiles(db, settings)
            fresh = pending and all(saved_row["id"] == row["id"] for saved_row, _ in group)
            existing = {saved.model for _, saved in group}
            added += _append_models(db, settings, fresh_runtime_defaults=fresh)
            if fresh:
                _apply_pending_model_choices(
                    db, settings, payload.get("_pending_model_choices"), existing
                )
            if pending and (not fresh or _can_expand_models(settings)):
                payload.pop("_pending_runtime_defaults")
                payload.pop("_pending_model_choices", None)
                db.execute(
                    "UPDATE providers SET value=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), row["id"]),
                )
        return added


def save_profile(
    store,
    credentials,
    name,
    settings,
    api_key=None,
    remember=True,
    identifier=None,
    *,
    enabled_models=None,
    enabled_models_touched=None,
    enabled_model_versions=None,
    expected_version=None,
    ucloud_token=None,
    hf_token=None,
    native_allocation_choice=None,
):
    from .store import new_id, now

    if identifier is None:
        settings = fresh_native_settings(settings)
    normalize_vllm_model(settings)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = None
        metadata = {}
        if identifier:
            current = db.execute(
                "SELECT name,value FROM providers WHERE id=?", (identifier,)
            ).fetchone()
            if current is None:
                raise KeyError(identifier)
            if expected_version is not None and stored_version(*current) != expected_version:
                raise StaleProvider(
                    "These settings changed in another window. Reopen "
                    "Settings to see the current values; nothing was saved."
                )
            previous = Settings.model_validate_json(current["value"])
            original = json.loads(current["value"])
            metadata = {
                field: original[field]
                for field in (NATIVE_CHOICE, NATIVE_PRODUCT)
                if field in original
            }
            settings = settings.model_copy(
                update={
                    field: getattr(previous, field)
                    for field in ("approved_for", "enabled")
                    if field not in settings.model_fields_set
                }
            )
            if (
                "language" not in settings.model_fields_set
                and previous.provider_type == settings.provider_type
                and previous.model == settings.model
            ):
                settings = settings.model_copy(update={"language": previous.language})
        if settings.language.strip().casefold() == "auto":
            if _uses_openai_language_hint(settings):
                raise ValueError(OPENAI_AUTO_LANGUAGE_ERROR)
            if settings.provider_type == "ucloud" and not whisperx_model(settings):
                raise ValueError(NATIVE_AUTO_LANGUAGE_ERROR)
        from .ucloud_native import COMPUTE_ID, MIG_PRODUCT

        native = settings.provider_type == "ucloud" and not whisperx_model(settings)
        if native_allocation_choice is not None and native_allocation_choice not in (
            "full",
            "mig1g",
        ):
            raise ValueError("Choose full B200 or native 1g MIG.")
        if identifier and native_allocation_choice is not None:
            current_choice = _native_preference(settings, metadata)
            if current_choice is None or current_choice[0] != native_allocation_choice:
                raise ValueError(
                    "Use the model allocation selector to change an existing preference."
                )
        if native and previous is None and not settings.ucloud_transcriber.template_job_id:
            chosen = native_allocation_choice or "mig1g"
            target = dict(MIG_PRODUCT) if chosen == "mig1g" else settings.ucloud_transcriber.product
            if chosen == "full" and (target or {}).get("id") != COMPUTE_ID:
                raise ValueError("Connect the explicit Full B200 choice before saving it.")
            metadata = {NATIVE_CHOICE: chosen, NATIVE_PRODUCT: target}
        if (fixed := fixed_runtime_language(settings)) and settings.language != fixed:
            raise ValueError(FIXED_RUNTIME_LANGUAGE_ERROR)
        if enabled_models is not None:
            if (
                settings.provider_type not in {"openai", "ucloud"}
                or not isinstance(enabled_models, list)
                or len(enabled_models) > 100
                or any(
                    not isinstance(model, str) or not 0 < len(model) <= 200
                    for model in enabled_models
                )
                or len(enabled_models) != len(set(enabled_models))
                or set(enabled_models)
                - (
                    {m["model"] for m in _catalog_models(settings)}
                    | {saved.model for _, saved in _model_profiles(db, settings)}
                    | {settings.model}
                )
            ):
                raise ValueError("Choose unique configured or catalog model IDs for this provider.")
        if enabled_models_touched is not None:
            allowed = (
                {m["model"] for m in _catalog_models(settings)}
                | {saved.model for _, saved in _model_profiles(db, settings)}
                | {settings.model}
            )
            if (
                enabled_models is None
                or settings.provider_type not in {"openai", "ucloud"}
                or not isinstance(enabled_models_touched, list)
                or len(enabled_models_touched) > 100
                or any(
                    not isinstance(model, str) or model not in allowed
                    for model in enabled_models_touched
                )
                or len(enabled_models_touched) != len(set(enabled_models_touched))
                or not isinstance(enabled_model_versions, dict)
                or len(enabled_model_versions) > 1000
                or any(
                    not isinstance(identifier, str)
                    or not isinstance(version, str)
                    or not re.fullmatch(r"[a-f0-9]{16}", version)
                    for identifier, version in enabled_model_versions.items()
                )
            ):
                raise ValueError("Choose valid model changes and their observed profile versions.")
            touched = set(enabled_models_touched)
            observed = {
                row["id"]: stored_version(row["name"], row["value"])
                for row, saved in _model_profiles(db, settings)
                if saved.model in touched
            }
            if observed != enabled_model_versions:
                raise StaleProvider("Model settings changed in another window. Reopen Settings.")
        elif enabled_model_versions is not None:
            raise ValueError("Model versions require an explicit changed-model list.")
        if api_key and not settings.base_url:
            raise ValueError("Add the serving endpoint before saving its API key.")
        group = _model_profiles(db, settings) if settings.provider_type == "ucloud" else []
        fresh_runtime_defaults = (
            settings.provider_type == "ucloud"
            and (not identifier or original.get("_pending_runtime_defaults") is True)
            and all(row["id"] == identifier for row, _ in group)
        )
        old_choices = original.get("_pending_model_choices") if identifier else None
        pending_choices = dict(old_choices) if isinstance(old_choices, dict) else {}
        if fresh_runtime_defaults and enabled_models is not None:
            changed_models = (
                set(enabled_models_touched)
                if enabled_models_touched is not None
                else {m["model"] for m in _catalog_models(settings)} | {settings.model}
            )
            pending_choices.update({model: model in enabled_models for model in changed_models})
        if fresh_runtime_defaults and not _can_expand_models(settings):
            metadata["_pending_runtime_defaults"] = True
            if pending_choices:
                metadata["_pending_model_choices"] = pending_choices
        if identifier:
            updated = db.execute(
                "UPDATE providers SET name=?,value=? WHERE id=?",
                (
                    name,
                    json.dumps(
                        {**settings.model_dump(), **metadata},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    identifier,
                ),
            )
            if updated.rowcount != 1:
                raise KeyError(identifier)
        else:
            identifier = new_id()
            db.execute(
                "INSERT INTO providers VALUES (?,?,?,?)",
                (
                    identifier,
                    name,
                    json.dumps(
                        {**settings.model_dump(), **metadata},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    now(),
                ),
            )
        _append_models(db, settings, fresh_runtime_defaults=fresh_runtime_defaults)
        if fresh_runtime_defaults:
            _apply_pending_model_choices(
                db,
                settings,
                pending_choices,
                {saved.model for _, saved in group} | {settings.model},
            )
        if enabled_models is not None:
            for row, saved in _model_profiles(db, settings):
                if enabled_models_touched is not None and saved.model not in touched:
                    continue
                enabled = saved.model in enabled_models
                if saved.enabled != enabled:
                    payload = json.loads(row["value"])
                    payload["enabled"] = enabled
                    db.execute(
                        "UPDATE providers SET value=? WHERE id=?",
                        (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), row["id"]),
                    )
        if settings.provider_type == "ucloud-vllm":
            tokens = {"ucloud_token": ucloud_token, "hf_token": hf_token}
            for name, scope in setup_key_scopes(settings).items():
                if tokens[name] is not None:
                    credentials.set(scope, tokens[name], remember)
        if api_key is not None:
            credentials.set(credential_scope(settings), api_key, remember)
    return public_profile(store, credentials, identifier)


def set_default_provider(store, identifier):
    require_enabled(Settings.model_validate(profile(store, identifier)["settings"]))
    store.execute(
        "UPDATE settings SET value=? WHERE id=2", (json.dumps({"default_provider_id": identifier}),)
    )


def require_enabled(settings):
    """Only new selection/submission checks this flag; frozen workers and retries keep running."""
    if not settings.enabled:
        raise ValueError(
            "Model is disabled. Enable it in Settings before starting a new transcription."
        )


def delete_profile(store, identifier):
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT id FROM providers ORDER BY created,id").fetchall()
        if identifier not in {row["id"] for row in rows}:
            raise KeyError(identifier)
        if len(rows) == 1:
            raise ValueError("Keep at least one provider configuration.")
        default = json.loads(db.execute("SELECT value FROM settings WHERE id=2").fetchone()[0])
        if default["default_provider_id"] == identifier:
            replacement = next(row["id"] for row in rows if row["id"] != identifier)
            db.execute(
                "UPDATE settings SET value=? WHERE id=2",
                (json.dumps({"default_provider_id": replacement}),),
            )
        db.execute("DELETE FROM providers WHERE id=?", (identifier,))


def public_settings(store, credentials):
    return public_profile(store, credentials, default_provider_id(store))


def save_settings(
    store,
    credentials,
    settings,
    api_key=None,
    remember=True,
    *,
    expected_version=None,
    ucloud_token=None,
    hf_token=None,
):
    identifier = default_provider_id(store)
    return save_profile(
        store,
        credentials,
        profile(store, identifier)["name"],
        settings,
        api_key,
        remember,
        identifier,
        expected_version=expected_version,
        ucloud_token=ucloud_token,
        hf_token=hf_token,
    )


def status_message(exc):
    if exc.status_code in {301, 302, 303, 307, 308}:
        return (
            "Provider redirected the request. Configure its final endpoint in Settings. "
            f"(HTTP {exc.status_code})"
        )
    messages = {
        400: "Provider rejected the model, audio, or response settings.",
        401: "Provider rejected the API key. Check Settings.",
        403: "This API key does not have permission for this operation or model.",
        404: "Provider endpoint or model was not found. Check the base URL and model.",
        413: "Provider upload limit exceeded. Lower the upload limit in Settings.",
        429: "Provider rate or quota limit reached. Check your quota and retry later.",
    }
    message = messages.get(exc.status_code, "Provider error. Retry later.")
    # Only known protocol field names/codes are safe to expose, never upstream messages.
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    if isinstance(error, dict):
        param = error.get("param")
        if isinstance(param, str) and param in {
            "model",
            "response_format",
            "language",
            "languages",
            "file",
            "timestamp_granularities",
            "chunking_strategy",
        }:
            message += f" Rejected field: {param}."
        if isinstance(error.get("code"), str) and error["code"] in {
            "model_not_found",
            "insufficient_quota",
            "invalid_api_key",
        }:
            message += f" Code: {error['code']}."
    return f"{message} (HTTP {exc.status_code})"


def check_models(settings, key, http_client=None):
    """Inspect account-visible model IDs without sending audio or starting paid work."""
    if settings.provider_type == "ucloud":
        raise ValueError("Use Check UCloud setup for the native Transcriber provider.")
    require_key(settings, key)
    if http_client is not None and http_client.follow_redirects:
        raise ValueError("Provider HTTP clients must not follow redirects.")
    try:
        with OpenAI(
            api_key=key or "local-no-key",
            base_url=settings.base_url,
            timeout=20,
            max_retries=0,
            http_client=http_client or DefaultHttpxClient(follow_redirects=False),
        ) as client:
            # A single list response, not unbounded automatic pagination against a custom API.
            models = client.models.list().data
            available = {
                model.id
                for model in models
                if isinstance(model.id, str)
                and 0 < len(model.id) <= 200
                and not any(ord(char) < 32 for char in model.id)
            }
    except (APIConnectionError, APITimeoutError) as exc:
        raise ValueError("Model check could not reach the endpoint or timed out.") from exc
    except APIStatusError as exc:
        raise ValueError(status_message(exc)) from exc
    except (AttributeError, TypeError) as exc:
        raise ValueError("Endpoint returned an unsupported model list.") from exc
    if settings.provider_type == "ucloud-vllm":
        return {
            "models": [
                {"id": model, "name": model, "listed": True} for model in sorted(available)[:200]
            ],
            "selected_model_listed": settings.model in available,
            "note": "These are model IDs advertised by this running endpoint. Configure its "
            "served model ID to equal the Hugging Face owner/model name. Model listing "
            "does not verify speech recognition, timestamps, or speaker support. "
            "No audio was uploaded and no UCloud job was started.",
        }
    return {
        "models": [
            {"id": model, "name": label, "listed": model in available}
            for model, (label, _) in OPENAI_MODELS.items()
        ],
        "note": "Listed means visible to this API key. Transcription permissions, billing, "
        "quota, and audio compatibility are checked only when transcribing. "
        "No audio was uploaded and no transcription was started.",
    }


def require_approval(store, project_id, provider_id, settings):
    project = store.one("SELECT classification FROM projects WHERE id=?", (project_id,))
    if project["classification"] == "standard":
        return
    try:
        current = Settings.model_validate(profile(store, provider_id)["settings"])
    except KeyError:
        current = None
    if (
        current is None
        or project["classification"] not in current.approved_for
        or (current.base_url, current.model, current.provider_type)
        != (settings.base_url, settings.model, settings.provider_type)
        or (
            settings.provider_type == "ucloud"
            and current.ucloud_transcriber != settings.ucloud_transcriber
        )
    ):
        raise ApprovalDenied(
            f"This {project['classification']} project requires a provider explicitly approved "
            "for its classification. Check the project's classification and Settings."
        )


def transcription_fields(settings):
    """Credential-free logical fields used by the SDK and fresh request descriptors."""
    if _uses_openai_language_hint(settings) and settings.language.strip().casefold() == "auto":
        raise ValueError(OPENAI_AUTO_LANGUAGE_ERROR)
    params = {"model": settings.model}
    definition = (
        next(
            (m for m in provider_catalog()["openai"]["models"] if m["model"] == settings.model), {}
        )
        if settings.provider_type == "openai"
        else {}
    )
    if settings.language:
        if definition.get("language_parameter") == "languages":
            params["languages"] = [settings.language]
        else:
            params["language"] = settings.language
    if settings.capability in {"words", "words_speakers"}:
        params.update(response_format="verbose_json", timestamp_granularities=["word", "segment"])
    elif settings.capability == "segments":
        params["response_format"] = "verbose_json"
    elif settings.capability == "speakers":
        params["response_format"] = "diarized_json"
        if settings.provider_type != "ucloud-vllm":
            params["chunking_strategy"] = "auto"
    else:
        params["response_format"] = "json"
        if definition.get("logprobs"):
            # Token probabilities become review hints; they never change the transcript.
            params["include"] = ["logprobs"]
    return params


def transcribe_file(path, settings, key, http_client=None, *, request_fields=None):
    """Send one part; request_fields replays a frozen descriptor's exact logical fields."""
    defaults = transcription_fields(settings)
    require_key(settings, key)
    if http_client is not None and http_client.follow_redirects:
        raise ValueError("Provider HTTP clients must not follow redirects.")
    params = defaults if request_fields is None else json.loads(json.dumps(request_fields))
    if not isinstance(params, dict) or params.get("model") != settings.model:
        raise ValueError("The frozen transcription request model changed.")
    try:
        # ponytail: one SDK integration serves OpenAI-compatible backends; no provider class tree.
        with (
            OpenAI(
                api_key=key or "local-no-key",
                base_url=settings.base_url,
                timeout=900,
                max_retries=0,
                http_client=http_client or DefaultHttpxClient(follow_redirects=False),
            ) as client,
            path.open("rb") as audio,
        ):
            response = client.audio.transcriptions.create(file=audio, **params)
            payload = (
                response.model_dump(mode="json") if hasattr(response, "model_dump") else response
            )
    except (APIConnectionError, APITimeoutError) as exc:
        raise ValueError(
            "The provider could not be reached or timed out. Check the endpoint and network. "
            "Retry explicitly; the provider may already have processed this request."
        ) from exc
    except APIStatusError as exc:
        if settings.provider_type == "ucloud-vllm" and exc.status_code in {404, 405, 501}:
            raise ValueError(
                "The UCloud vLLM endpoint or model does not expose audio transcription. "
                "Check its /v1 URL, served model alias and speech-model deployment. "
                f"(HTTP {exc.status_code})"
            ) from exc
        raise ValueError(status_message(exc)) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise ValueError("Provider returned an unsupported transcript structure.")
    return payload


def normalize_response(payload):
    rows = payload.get("segments") or [
        {"text": payload["text"], "_token_logprobs": payload.get("logprobs")}
    ]
    return normalize_segments(rows, payload.get("words"))


def preserve_response(path, payload):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)
