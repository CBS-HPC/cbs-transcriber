"""Settings-only connection observations for new transcription admission (ADR 0167).

Receipts are operational settings, never part of normalized/frozen provider Settings.
Only an explicit successful check can issue a save token. Reads never contact a provider.
"""

import hashlib
import json
import re
import secrets
import threading

SETTING = 10


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def identity(settings):
    connection = {"provider_type": settings.provider_type, "base_url": settings.base_url}
    if settings.provider_type == "ucloud":
        connection["setup"] = settings.ucloud_transcriber.model_dump(
            exclude={"hours", "cleanup_after_success"}
        )
    return hashlib.sha256(_json(connection).encode()).hexdigest()


def _fingerprint(settings, key):
    # One combined digest, kept internal: neither keys nor their standalone hashes escape.
    return hashlib.sha256((identity(settings) + "\0" + (key or "")).encode()).hexdigest()


def _records(db):
    row = db.execute("SELECT value FROM settings WHERE id=?", (SETTING,)).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(db, records):
    db.execute(
        "INSERT INTO settings(id,value) VALUES (?,?) "
        "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
        (SETTING, _json(records)),
    )


def observation(store, settings):
    with store.connection() as db:
        return _records(db).get(identity(settings))


def _valid_receipt(receipt):
    return (
        isinstance(receipt, dict)
        and set(receipt)
        == {
            "identity",
            "fingerprint",
            "state",
            "models",
            "checked_at",
            "check_session",
            "check_sequence",
        }
        and all(
            isinstance(receipt[field], str) and re.fullmatch(r"[a-f0-9]{64}", receipt[field])
            for field in ("identity", "fingerprint")
        )
        and isinstance(receipt["state"], str)
        and receipt["state"] in {"ready", "unavailable"}
        and isinstance(receipt["models"], list)
        and len(receipt["models"]) <= 200
        and all(isinstance(model, str) and 0 < len(model) <= 200 for model in receipt["models"])
        and isinstance(receipt["checked_at"], str)
        and 0 < len(receipt["checked_at"]) <= 80
        and isinstance(receipt["check_session"], str)
        and re.fullmatch(r"[a-f0-9]{32}", receipt["check_session"]) is not None
        and type(receipt["check_sequence"]) is int
        and receipt["check_sequence"] > 0
    )


def state(db, settings, key):
    from .providers import direct_openai, require_key
    from .ucloud_native import require_connection

    if direct_openai(settings):
        return "service_disabled"
    if not key and settings.provider_type in {"openai", "ucloud", "ucloud-vllm"}:
        return "missing_credentials"
    try:
        require_key(settings, key)
        if settings.provider_type == "ucloud":
            require_connection(settings)
    except ValueError:
        return "unconfigured"
    receipt = _records(db).get(identity(settings))
    if (
        not _valid_receipt(receipt)
        or receipt["identity"] != identity(settings)
        or receipt["fingerprint"] != _fingerprint(settings, key)
    ):
        return "recheck_required"
    if receipt.get("state") == "unavailable":
        return "unavailable"
    if receipt.get("state") != "ready":
        return "recheck_required"
    if settings.provider_type != "ucloud" and settings.model not in receipt.get("models", []):
        return "model_not_listed"
    return "ready"


def public_state(store, settings, key):
    with store.connection() as db:
        current = state(db, settings, key)
    return {
        "service_available": current != "service_disabled",
        "connection_ready": current == "ready",
        "connection_state": current,
    }


def require_ready(db, settings, key):
    from .providers import require_service_available

    require_service_available(settings)
    current = state(db, settings, key)
    if current != "ready":
        message = {
            "missing_credentials": "Add the provider credential in Settings and check it.",
            "unconfigured": "Finish provider setup in Settings and check the connection.",
            "unavailable": (
                "The last connection check failed. Check it again in Settings "
                "before new transcription."
            ),
            "model_not_listed": (
                "This model was not advertised by the checked connection. "
                "Check available models in Settings."
            ),
        }.get(current, "Check this saved provider connection in Settings before new transcription.")
        raise ValueError(message)


def failed(store, credentials, settings, key, observed, *, generation=None):
    """Only the exact saved, previously checked connection can become unavailable.

    A wrong draft key/endpoint or a stale response cannot revoke a newer observation.
    """
    from .providers import Settings, credential_scope

    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        records = _records(db)
        identifier = identity(settings)
        current = records.get(identifier)
        if not isinstance(current, dict) or current.get("fingerprint") != _fingerprint(
            settings, key
        ):
            return
        older_save = (
            generation is not None
            and current.get("check_session") == generation[0]
            and type(current.get("check_sequence")) is int
            and current["check_sequence"] < generation[1]
        )
        if current != observed and not older_save:
            return
        matching = any(
            identity(saved := Settings.model_validate_json(row[0])) == identifier
            and credentials.get(credential_scope(saved)) == key
            for row in db.execute("SELECT value FROM providers")
        )
        if matching:
            records[identifier] = {**current, "state": "unavailable"}
            _write(db, records)


class Checks:
    """Bounded server-session tokens authorize saving an actual check, never a claim."""

    def __init__(self):
        self.tokens = {}
        self.latest = {}
        self.sequence = 0
        self.lock = threading.RLock()
        self.session = secrets.token_hex(16)

    def begin(self, settings):
        with self.lock:
            return self._begin(settings)

    def _begin(self, settings):
        identifier = identity(settings)
        self.sequence += 1
        attempt = (identifier, self.sequence)
        self.latest[identifier] = attempt
        self.tokens = {
            token: receipt
            for token, receipt in self.tokens.items()
            if receipt["identity"] != identifier
        }
        while len(self.latest) > 128:
            self.latest.pop(next(iter(self.latest)))
        return attempt

    def current(self, attempt):
        with self.lock:
            return self.latest.get(attempt[0]) == attempt

    def issue(self, settings, key, attempt, *, models=None):
        with self.lock:
            return self._issue(settings, key, attempt, models=models)

    def _issue(self, settings, key, attempt, *, models=None):
        from .store import now

        resolved = identity(settings)
        newer = self.latest.get(resolved)
        if not self.current(attempt) or (newer is not None and newer[1] > attempt[1]):
            raise ValueError("A newer connection check started. Use its result before saving.")
        self.latest[resolved] = (resolved, attempt[1])
        self.tokens = {
            token: receipt
            for token, receipt in self.tokens.items()
            if receipt["identity"] != resolved
        }
        token = secrets.token_urlsafe(32)
        self.tokens[token] = {
            "identity": identity(settings),
            "fingerprint": _fingerprint(settings, key),
            "state": "ready",
            "models": sorted(set(models or [])),
            "checked_at": now(),
            "check_session": self.session,
            "check_sequence": attempt[1],
        }
        while len(self.tokens) > 128:
            self.tokens.pop(next(iter(self.tokens)))
        return token

    def save(self, db, token, settings, key):
        with self.lock:
            self._save(db, token, settings, key)

    def _save(self, db, token, settings, key):
        receipt = self.tokens.get(token)
        if (
            receipt is None
            or receipt["identity"] != identity(settings)
            or receipt["fingerprint"] != _fingerprint(settings, key)
        ):
            raise ValueError(
                "The checked connection changed. Check the current settings again before saving."
            )
        records = _records(db)
        records[receipt["identity"]] = dict(receipt)
        # Retain only currently saved connection identities; old endpoints grant no authority.
        from .providers import Settings

        live = {
            identity(Settings.model_validate_json(row[0]))
            for row in db.execute("SELECT value FROM providers")
        }
        _write(
            db, {identifier: value for identifier, value in records.items() if identifier in live}
        )
