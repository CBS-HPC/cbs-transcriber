"""Imported queues are archival evidence, never authority over source allocations.

Schema15 follows schema14's result roles. No foreign keys: archived remote obligations stay
fenced after their local members are deleted.
"""

import re
import sqlite3

SCHEMA_VERSION = 15
POLICY = "cbs-restored-owner-quarantine-v1"
KINDS = {"job", "native_batch", "runtime_batch"}
# SQLite stores this without IF NOT EXISTS, so the catalogued table text is unchanged.
DDL = """CREATE TABLE IF NOT EXISTS restored_owners (
    kind TEXT NOT NULL CHECK(kind IN ('job','native_batch','runtime_batch')),
    owner_id TEXT NOT NULL,
    restore_id TEXT NOT NULL,
    source_backup_id TEXT NOT NULL,
    PRIMARY KEY(kind,owner_id)
)"""


class HistoricalOwner(ValueError):
    """A restored owner can be inspected but cannot perform operational actions."""


def is_restored(db, kind, owner_id):
    if kind not in KINDS:
        raise ValueError("Unknown restored owner kind.")
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restored_owners'"
    ).fetchone():
        if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            raise RuntimeError(
                "The library is missing its imported-owner quarantine. Preserve the library."
            )
        return False
    return (
        db.execute(
            "SELECT 1 FROM restored_owners WHERE kind=? AND owner_id=?", (kind, owner_id)
        ).fetchone()
        is not None
    )


def require_owned(db, kind, owner_id):
    if is_restored(db, kind, owner_id):
        raise HistoricalOwner(
            "This restored job is historical. Create a new run to process this recording; "
            "its source allocation cannot be controlled from this library."
        )


def require_job(store, job_id):
    with store.connection() as db:
        require_owned(db, "job", job_id)
        row = db.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is not None:
            import json

            try:
                params = json.loads(row[0])
            except (ValueError, TypeError):
                params = None
            if isinstance(params, dict):
                for key, kind in (
                    ("native_batch_id", "native_batch"),
                    ("runtime_batch_id", "runtime_batch"),
                ):
                    if params.get(key):
                        require_owned(db, kind, params[key])


def require_owner(store, kind, owner_id):
    with store.connection() as db:
        require_owned(db, kind, owner_id)


def sql_exclusion(db, kind, owner_sql):
    """Static owner expressions only; absent pre-15 tables retain exact old behavior."""
    if kind not in KINDS or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?", owner_sql):
        raise ValueError("Use a known restored-owner scope.")
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restored_owners'"
    ).fetchone():
        if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
            raise RuntimeError("The imported-owner quarantine is missing. Preserve the library.")
        return "1"
    return (
        "NOT EXISTS (SELECT 1 FROM restored_owners restored_guard WHERE "
        f"restored_guard.kind='{kind}' AND restored_guard.owner_id={owner_sql})"
    )


def require_supported_activation():
    from .store import SCHEMA_VERSION as current

    if current < SCHEMA_VERSION:
        raise RuntimeError("Portable activation requires the app's schema15 imported-owner guards.")


def migrate(store):
    """Ordinary libraries receive an empty policy; their source owners remain operational."""
    from .store import new_id

    with store.library_lock(), store.connection() as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if version != 14:
            raise RuntimeError("Imported-owner quarantine must follow the schema14 migration.")
        if store.initial_version == 14:
            (store.root / "backups").mkdir(exist_ok=True)
            backup = sqlite3.connect(store.root / "backups" / f"schema-14-{new_id()}.sqlite3")
            try:
                db.backup(backup)
            finally:
                backup.close()
        db.execute("BEGIN IMMEDIATE")
        db.execute(DDL)
        db.execute("PRAGMA user_version=15")
