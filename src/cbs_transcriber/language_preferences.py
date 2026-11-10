"""Project and recording language preferences, independently revisioned from transcripts."""

import json
import re
import sqlite3
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files

from .store import new_id

# Exact native application identifiers shipped for the same unverified Parakeet model.
# Do not infer identities from substrings or user-editable model catalogs.
_NATIVE_PARAKEET_IDENTIFIERS = frozenset(
    {"nvidia/parakeet-tdt-0.6b-v3", "parakeet-tdt-0.6b-v3", "parakeet"}
)


class Conflict(ValueError):
    """The language preference changed since the user read it."""


@lru_cache(maxsize=1)
def catalog():
    return json.loads(files("cbs_transcriber").joinpath("catalogs/languages.json").read_bytes())


@lru_cache(maxsize=1)
def codes():
    return frozenset(item["code"] for item in catalog()["languages"])


def validate(value):
    if value is not None and (not isinstance(value, str) or value not in codes()):
        raise ValueError("Choose a language from the list, or inherit.")
    return value


def validate_frozen(value):
    if type(value) is not str or re.fullmatch(r"[a-z]{2,8}", value) is None:
        raise ValueError("Invalid frozen language code.")
    return value


def migrate(store):
    with store.library_lock(), store.connection() as db:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 17:
            return
        if store.initial_version == 16:
            (store.root / "backups").mkdir(exist_ok=True)
            backup = sqlite3.connect(store.root / "backups" / f"schema-16-{new_id()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("BEGIN IMMEDIATE")
        for table, column in (
            ("projects", "default_language"),
            ("recordings", "language_override"),
        ):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            db.execute(
                f"ALTER TABLE {table} ADD COLUMN language_revision INTEGER NOT NULL DEFAULT 0"
            )
        db.execute("PRAGMA user_version=17")


def save(store, scope, identifier, language, revision):
    validate(language)
    if type(revision) is not int or not 0 <= revision < 2**63:
        raise ValueError("Choose a valid language revision.")
    if scope == "project":
        table, column = "projects", "default_language"
    elif scope == "recording":
        table, column = "recordings", "language_override"
    else:
        raise ValueError("Choose a project or recording language.")
    with store.snapshot_lock:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
            if current is None:
                raise KeyError(identifier)
            if current["language_revision"] != revision:
                raise Conflict("The language changed in another window. Reload before saving.")
            if current[column] == language:
                return {"language": language, "revision": revision}
            if revision == 2**63 - 1:
                raise ValueError("The language revision limit has been reached.")
            db.execute(
                f"UPDATE {table} SET {column}=?,language_revision=language_revision+1 "
                "WHERE id=? AND language_revision=?",
                (language, identifier, revision),
            )
            project = identifier if scope == "project" else current["project_id"]
        store.snapshot_project(project)
    return {"language": language, "revision": revision + 1}


def save_recordings(store, project_id, language, recordings):
    """Apply one language to an exact, revision-checked selection atomically."""
    validate(language)
    ids = [item["id"] for item in recordings]
    if not ids or len(ids) > 1000 or len(ids) != len(set(ids)):
        raise ValueError("Select between 1 and 1000 distinct recordings.")
    with store.snapshot_lock:
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id,project_id,language_override,language_revision FROM recordings "
                "WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),),
            ).fetchall()
            observed = {row["id"]: row for row in rows}
            if len(observed) != len(ids) or any(
                observed[item["id"]]["project_id"] != project_id
                or observed[item["id"]]["language_revision"] != item["revision"]
                for item in recordings
            ):
                raise Conflict("A selected recording changed or was removed. Reload and try again.")
            if any(
                row["language_override"] != language and row["language_revision"] >= 2**63 - 1
                for row in rows
            ):
                raise ValueError("The language revision limit has been reached.")
            for row in rows:
                if row["language_override"] != language:
                    db.execute(
                        "UPDATE recordings SET language_override=?,"
                        "language_revision=language_revision+1 WHERE id=?",
                        (language, row["id"]),
                    )
            result = [
                {
                    "id": item["id"],
                    "language": language,
                    "revision": observed[item["id"]]["language_revision"]
                    + (observed[item["id"]]["language_override"] != language),
                }
                for item in recordings
            ]
        store.snapshot_project(project_id)
    return {"recordings": result, "snapshot_error": store.snapshot_errors.get(project_id)}


def guard(store, recording):
    project = store.one(
        "SELECT default_language,language_revision FROM projects WHERE id=?",
        (recording["project_id"],),
    )
    return {
        "project_language": project["default_language"],
        "project_revision": project["language_revision"],
        "recording_language": recording["language_override"],
        "recording_revision": recording["language_revision"],
    }


def require_current(db, recording, expected):
    if type(expected) is not dict or set(expected) != {
        "project_language",
        "project_revision",
        "recording_language",
        "recording_revision",
    }:
        raise ValueError("Invalid accepted language preference.")
    project = db.execute(
        "SELECT default_language,language_revision FROM projects WHERE id=?",
        (recording["project_id"],),
    ).fetchone()
    if project is None or (
        project["default_language"],
        project["language_revision"],
        recording["language_override"],
        recording["language_revision"],
    ) != (
        expected["project_language"],
        expected["project_revision"],
        expected["recording_language"],
        expected["recording_revision"],
    ):
        raise Conflict(
            "The project or recording language changed. Review the selection and try again."
        )


def selected(guard):
    if guard["recording_language"] is not None:
        return {"language": validate(guard["recording_language"]), "scope": "recording"}
    if guard["project_language"] is not None:
        return {"language": validate(guard["project_language"]), "scope": "project"}
    return None


def enhancement_options(store, recording, options):
    """Freeze a selected language for a new timing, repair or enhancement request."""
    from .enhancement import EnhancementOptions

    frozen_guard = guard(store, recording)
    preference = selected(frozen_guard)
    if preference is None:
        return options, frozen_guard, None
    language = preference["language"]
    if language not in {"da", "en"}:
        if speaker_only(options.model_dump()):
            # Diarization uses no language model. Preserve the historical options value
            # as an unused compatibility field, and keep the real preference separately.
            return options, frozen_guard, preference
        raise ValueError(
            "Word timing and WhisperX processing currently support Danish and English only. "
            "Change the recording or project language to use this operation."
        )
    return (
        EnhancementOptions.model_validate({**options.model_dump(), "language": language}),
        frozen_guard,
        preference,
    )


def speaker_only(options):
    """Recognize only an explicitly speaker-only frozen enhancement request."""
    return (
        isinstance(options, dict)
        and options.get("diarize") is True
        and all(options.get(key) is False for key in ("align", "transcribe", "retranscribe"))
    )


def apply_to_provider(provider, preference):
    """Freeze a requested hint for new work; never mutate the saved provider."""
    from .catalog import fixed_runtime_language, whisperx_model
    from .providers import Settings, _uses_openai_language_hint

    if preference is None:
        return provider
    language = validate(preference["language"])
    settings = Settings.model_validate(provider["settings"])
    if whisperx_model(settings):
        fixed = fixed_runtime_language(settings)
        if fixed and language != fixed:
            raise ValueError(
                "This CBS Runtime model requires Danish. Change the recording or project language."
            )
        if language not in {"da", "en"}:
            raise ValueError(
                "CBS Runtime currently supports only Danish and English "
                "for a fixed language choice."
            )
    elif (
        settings.provider_type == "ucloud"
        and settings.model.casefold() in _NATIVE_PARAKEET_IDENTIFIERS
    ):
        raise ValueError(
            "The UCloud Parakeet model has not been verified to honor a recording "
            "or project language. Use the model default or choose another model."
        )
    elif _uses_openai_language_hint(settings) and len(language) != 2:
        raise ValueError(
            "This OpenAI model requires a two-letter language code; choose a supported language."
        )
    frozen = deepcopy(provider)
    frozen["settings"]["language"] = language
    return frozen
