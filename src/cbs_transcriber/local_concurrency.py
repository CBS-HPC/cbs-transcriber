"""Adjustable parallel local tasks per queue lane (ADR 0146).

Each lane starts a fixed number of worker threads (its maximum). A lane limit only gates *new*
claims under the shared claim lock, so lowering it never stops running work and raising it takes
effect at the next claim. Unset values follow this computer's CPU count. Local WhisperX, UCloud
enhancement and CBS Runtime lanes keep their own fixed limits.

Native UCloud Transcriber has two separate Settings → UCloud limits (ADR 0183): how many of its
batches (or single-file runs) this app runs at once, gated like the lanes above, and how many
files one batch uploads at once, read when the batch starts and kept for that run.
"""

import json
import os

from pydantic import BaseModel, ConfigDict, Field, ValidationError

SETTING = 9
# Settings lane -> queue worker kind and hard maximum (= threads started for that lane).
LANES = {
    "media": ("media", 8),
    "inspection": ("source-inspect", 8),
    "admission": ("input-admission", 4),
    "api": ("transcribe-api", 8),
}
# Native UCloud Transcriber: the lane starts its maximum number of batch workers; the saved
# "batches at once" limit gates new claims (ADR 0183). Upload speed depends on the network, not
# this computer's processors, so neither limit follows the CPU count.
NATIVE_SETTING = 12  # 9 is local concurrency; 10 checked providers; 11 runtime generations.
NATIVE_WORKERS = 4
NATIVE_UPLOADS_MAX = 8
NATIVE_DEFAULTS = {"uploads": 4, "batches": 2}


class LocalConcurrency(BaseModel):
    """Saved choices; null means automatic for this computer."""

    model_config = ConfigDict(extra="forbid")
    media: int | None = Field(default=None, strict=True, ge=1, le=LANES["media"][1])
    inspection: int | None = Field(default=None, strict=True, ge=1, le=LANES["inspection"][1])
    admission: int | None = Field(default=None, strict=True, ge=1, le=LANES["admission"][1])
    api: int | None = Field(default=None, strict=True, ge=1, le=LANES["api"][1])


def defaults(cpu_count=None):
    """Use bounded logical-CPU heuristics and the user's provider-request default.

    os.cpu_count() reports logical processors. Media leaves two logical processors available;
    inspection uses half (an approximation of physical cores on common SMT laptops). Neither
    lane uses fewer than the earlier two. Provider API requests default to six
    (user decisions, 6 November 2026; four was about 35% faster than two in the mocked
    measurement). This raises load on a provider's rate limits and a rate-limited part still
    fails for explicit retry, so the setting can be lowered. Admission is metadata-only and
    stays at two.
    """
    cpus = cpu_count if cpu_count is not None else os.cpu_count() or 2
    # Playback preparation kept improving past four on the 8-thread benchmark laptop (45 s at
    # 4, 35 s at 6, 30 s at 8); the user prioritised local throughput (6 November 2026). Leave two
    # logical processors for the server and editing UI. Inspection flattened after four.
    media = max(2, min(8, cpus - 2))
    inspection = max(2, min(4, cpus // 2))
    return {"media": media, "inspection": inspection, "admission": 2, "api": 6}


def _read(db):
    row = db.execute("SELECT value FROM settings WHERE id=?", (SETTING,)).fetchone()
    if row is None:
        return LocalConcurrency()
    try:
        return LocalConcurrency.model_validate_json(row[0])
    except (ValidationError, ValueError):
        # Only throughput depends on this row: an unreadable value falls back to automatic.
        return LocalConcurrency()


def saved(store):
    if store is None:
        return LocalConcurrency()
    with store.connection() as db:
        return _read(db)


def effective(value=None, cpu_count=None):
    value = LocalConcurrency.model_validate(value or {})
    automatic = defaults(cpu_count)
    return {lane: getattr(value, lane) or automatic[lane] for lane in LANES}


def save(store, edit):
    """Replace only the fields the client sent (null restores automatic), atomically."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = _read(db).model_dump()
        current.update({field: getattr(edit, field) for field in edit.model_fields_set})
        value = LocalConcurrency.model_validate(current)
        db.execute(
            "INSERT INTO settings(id,value) VALUES (?,?) "
            "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
            (SETTING, json.dumps(value.model_dump(), sort_keys=True)),
        )
    return value


def status(store, queue=None):
    value = saved(store)
    automatic = defaults()
    return {
        "saved": value.model_dump(),
        "defaults": automatic,
        "effective": effective(value),
        "active": queue.lane_limit_settings() if queue is not None else None,
        "maximum": {lane: maximum for lane, (_, maximum) in LANES.items()},
        "logical_cpus": os.cpu_count(),
    }


class NativeLimits(BaseModel):
    """Settings → UCloud: Transcriber batches at once and files uploaded at once per batch."""

    model_config = ConfigDict(extra="forbid")
    uploads: int = Field(
        default=NATIVE_DEFAULTS["uploads"], strict=True, ge=1, le=NATIVE_UPLOADS_MAX
    )
    batches: int = Field(default=NATIVE_DEFAULTS["batches"], strict=True, ge=1, le=NATIVE_WORKERS)


def _read_native(db):
    row = db.execute("SELECT value FROM settings WHERE id=?", (NATIVE_SETTING,)).fetchone()
    if row is None:
        return NativeLimits()
    try:
        return NativeLimits.model_validate_json(row[0])
    except (ValidationError, ValueError):
        # Only throughput depends on this row: an unreadable value falls back to the defaults.
        return NativeLimits()


def native_saved(store):
    if store is None:
        return NativeLimits()
    with store.connection() as db:
        return _read_native(db)


def native_save(store, edit):
    """Replace only the fields the client sent (a PUT body is a NativeLimits), atomically.

    The caller holds the claim lock, so the applied batch limit always matches the saved row."""
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = _read_native(db).model_dump()
        current.update({field: getattr(edit, field) for field in edit.model_fields_set})
        value = NativeLimits.model_validate(current)
        db.execute(
            "INSERT INTO settings(id,value) VALUES (?,?) "
            "ON CONFLICT(id) DO UPDATE SET value=excluded.value",
            (NATIVE_SETTING, json.dumps(value.model_dump(), sort_keys=True)),
        )
    return value


def native_status(store, queue=None):
    value = native_saved(store)
    return {
        **value.model_dump(),
        "defaults": dict(NATIVE_DEFAULTS),
        "maximum": {"uploads": NATIVE_UPLOADS_MAX, "batches": NATIVE_WORKERS},
        "active_batches": queue.native_batch_limit() if queue is not None else None,
    }
