"""Small SQLite persistence boundary; each operation owns its connection."""

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import threading
import uuid
from contextlib import closing, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from . import annotations
from .history import (
    decode_payload,
    edit_metadata,
    encode_payload,
    encode_serialized_payload,
    processing_metadata,
    serialized_payload_matches,
    text_hash,
    validate_import_artifact_evidence,
    validate_revision,
)
from .transcripts import (
    MAX_TRANSCRIPT_BYTES,
    default_speaker_names,
    hints_document,
    parse_transcript,
    pop_hints,
)


def now():
    return datetime.now(UTC).isoformat()


def new_id():
    return uuid.uuid4().hex


def containment_path(path):
    """Resolve links for a containment comparison only; never use the result for I/O.

    Windows realpath() looks up a missing path twice. If another thread creates one of its
    missing ancestors in between, the Win32 error changes and realpath() keeps the
    extended-length prefix, so a contained path compares as outside its folder. Both
    spellings name the same location, so one plain spelling is compared on every call.
    """
    value = os.path.realpath(path)
    if os.name == "nt" and not os.fspath(path).startswith("\\\\?\\"):
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[len("\\\\?\\UNC\\") :]
        elif value.startswith("\\\\?\\"):
            value = value[len("\\\\?\\") :]
    return Path(value)


SCHEMA_VERSION = 19
MAX_COLLECTION_NAME = 120
MAX_COLLECTION_NOTES = 20000


class Store:
    def __init__(self, root):
        from .library_deletion import require_unreserved

        self.root = Path(root).resolve()
        # Before creating a directory or database: never recreate a library being deleted.
        require_unreserved(self.root)
        if (self.root / "restore-pending").exists():
            raise RuntimeError(
                "This portable restore is incomplete. Resume its restore operation "
                "before opening the library."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "library.sqlite3"
        self.snapshot_lock = threading.RLock()
        self.snapshot_errors = {}
        self._lease_lock = threading.RLock()
        self._lease_depth = 0
        # The launcher sets a friendlier local default; libraries/tests keep <library>/projects.
        self.default_parent = self.root / "projects"
        version = 0
        if self.db_path.exists():
            # Inspect read-only before WAL or CREATE can change an unsupported library.
            with closing(sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True)) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    "This library uses a newer CBS Transcriber schema. Use a compatible app."
                )
        self.initial_version = version
        # An already current library needs no constructor writes. Read-only Store users may
        # inspect it while the serving Store holds the lease; migrations may never do so.
        if version == SCHEMA_VERSION:
            return
        with self.library_lock():
            self._initialize(version, not self.db_path.exists())

    def _initialize(self, version, new_database=False):
        if version >= 5:
            if version == 5:
                self._migrate_history()
            if version < 7:
                self._migrate_annotations()
            if version < 8:
                self._migrate_result_trash()
            if version < 9:
                self._migrate_native_batches()
            if version < 10:
                self._migrate_result_hints()
            if version < 11:
                self._migrate_collections()
            if version < 12 and SCHEMA_VERSION >= 12:
                self._migrate_runtime_batches()
            if version < 13 and SCHEMA_VERSION >= 13:
                self._migrate_original_inspection()
            if version < 14 and SCHEMA_VERSION >= 14:
                self._migrate_result_roles()
            if version < 15 and SCHEMA_VERSION >= 15:
                self._migrate_restored_owners()
            if version < 16 and SCHEMA_VERSION >= 16:
                self._migrate_runtime_preparations()
            if version < 17 and SCHEMA_VERSION >= 17:
                self._migrate_language_preferences()
            if version < 18 and SCHEMA_VERSION >= 18:
                self._migrate_automatic_roles()
            if version < 19 and SCHEMA_VERSION >= 19:
                self._migrate_passage_reviews()
            with self.connection() as db:
                db.execute("PRAGMA journal_mode=WAL")
            return
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id)
                    ON DELETE CASCADE, name TEXT NOT NULL, filename TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'audio', status TEXT NOT NULL DEFAULT 'preparing',
                    duration REAL, segments TEXT NOT NULL DEFAULT '[]',
                    speakers TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 0,
                    created TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id)
                    ON DELETE CASCADE, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    params TEXT NOT NULL DEFAULT '{}', chunks TEXT NOT NULL DEFAULT '[]',
                    created TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            """)
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            self._migrate_results(new_database)
        if version < 3:
            self._migrate_projects()
        if version < 4:
            self._migrate_protected()
        if version < 5:
            self._migrate_notes()
        self._migrate_history()
        self._migrate_annotations()
        self._migrate_result_trash()
        self._migrate_native_batches()
        self._migrate_result_hints()
        self._migrate_collections()
        if SCHEMA_VERSION >= 12:
            self._migrate_runtime_batches()
        if SCHEMA_VERSION >= 13:
            self._migrate_original_inspection()
        if SCHEMA_VERSION >= 14:
            self._migrate_result_roles()
        if SCHEMA_VERSION >= 15:
            self._migrate_restored_owners()
        if SCHEMA_VERSION >= 16:
            self._migrate_runtime_preparations()
        if SCHEMA_VERSION >= 17:
            self._migrate_language_preferences()
        if SCHEMA_VERSION >= 18:
            self._migrate_automatic_roles()
        if SCHEMA_VERSION >= 19:
            self._migrate_passage_reviews()

    def _migrate_passage_reviews(self):
        from . import passage_review

        passage_review.migrate(self)

    def _migrate_automatic_roles(self):
        from . import result_roles

        result_roles.migrate_automatic(self)

    def _migrate_language_preferences(self):
        from . import language_preferences

        language_preferences.migrate(self)

    def _migrate_runtime_preparations(self):
        from . import runtime_preparation

        runtime_preparation.migrate(self)

    def _migrate_restored_owners(self):
        from . import restored_owners

        restored_owners.migrate(self)

    def _migrate_result_roles(self):
        from . import result_roles

        result_roles.migrate(self)

    def _migrate_original_inspection(self):
        """Schema 13 (ADR 0082): nullable observations; never open original media."""
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 13:
                return
            if self.initial_version == 12:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-12-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(recordings)")}
            if "original_input" not in columns:
                db.execute("ALTER TABLE recordings ADD COLUMN original_input TEXT")
            db.execute("PRAGMA user_version=13")

    def _migrate_runtime_batches(self):
        from . import runtime_store

        runtime_store.migrate(self)

    def _migrate_collections(self):
        """Schema 11 (ADR 0060): named groups of recordings inside a project; metadata only."""
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 11:
                return
            if self.initial_version == 10:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-10-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            # Additive and idempotent: only what is missing is created.
            db.execute(
                "CREATE TABLE IF NOT EXISTS collections (id TEXT PRIMARY KEY, project_id TEXT "
                "NOT NULL REFERENCES projects(id) ON DELETE CASCADE, name TEXT NOT NULL, "
                "notes TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0, "
                "created TEXT NOT NULL)"
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(recordings)")}
            if "collection_id" not in columns:
                # Deleting a collection never deletes its recordings; they become unsorted.
                db.execute(
                    "ALTER TABLE recordings ADD COLUMN collection_id TEXT "
                    "REFERENCES collections(id) ON DELETE SET NULL"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS recordings_collection ON recordings(collection_id)"
            )
            db.execute("PRAGMA user_version=11")

    def collections(self, project_id):
        self.one("SELECT id FROM projects WHERE id=?", (project_id,))
        return self.all(
            "SELECT c.*, (SELECT COUNT(*) FROM recordings r WHERE r.collection_id=c.id) "
            "AS recordings FROM collections c WHERE c.project_id=? "
            "ORDER BY c.position, c.created",
            (project_id,),
        )

    def _collection_name(self, db, project_id, name, identifier=None):
        name = (name or "").strip()
        if not 0 < len(name) <= MAX_COLLECTION_NAME or any(ord(c) < 32 for c in name):
            raise ValueError(
                f"Name the collection in 1–{MAX_COLLECTION_NAME} characters, on one line."
            )
        existing = db.execute(
            "SELECT name FROM collections WHERE project_id=? AND id!=?",
            (project_id, identifier or ""),
        )
        if any(row["name"].casefold() == name.casefold() for row in existing):
            raise ValueError("This project already has a collection with that name.")
        return name

    def create_collection(self, project_id, name):
        identifier = new_id()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
                raise KeyError(project_id)
            name = self._collection_name(db, project_id, name)
            position = db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM collections WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            db.execute(
                "INSERT INTO collections(id,project_id,name,position,created) VALUES (?,?,?,?,?)",
                (identifier, project_id, name, position, now()),
            )
        return self.collection(identifier)

    def collection(self, identifier):
        return self.one(
            "SELECT c.*, (SELECT COUNT(*) FROM recordings r WHERE r.collection_id=c.id) "
            "AS recordings FROM collections c WHERE c.id=?",
            (identifier,),
        )

    def update_collection(self, identifier, name=None, notes=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM collections WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            if name is not None:
                name = self._collection_name(db, row["project_id"], name, identifier)
                db.execute("UPDATE collections SET name=? WHERE id=?", (name, identifier))
            if notes is not None:
                if len(notes) > MAX_COLLECTION_NOTES:
                    raise ValueError("Collection notes are limited to 20,000 characters.")
                db.execute("UPDATE collections SET notes=? WHERE id=?", (notes, identifier))
        return self.collection(identifier)

    def delete_collection(self, identifier):
        """Remove the group only: its recordings stay in the project, unsorted."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM collections WHERE id=?", (identifier,)).fetchone():
                raise KeyError(identifier)
            db.execute(
                "UPDATE recordings SET collection_id=NULL WHERE collection_id=?", (identifier,)
            )
            db.execute("DELETE FROM collections WHERE id=?", (identifier,))

    def move_recordings(self, project_id, recording_ids, collection_id):
        """Put recordings into one collection of their project, or none (None)."""
        recording_ids = list(dict.fromkeys(recording_ids))
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                collection_id is not None
                and not db.execute(
                    "SELECT id FROM collections WHERE id=? AND project_id=?",
                    (collection_id, project_id),
                ).fetchone()
            ):
                raise ValueError("That collection does not belong to this project.")
            marks = ",".join("?" * len(recording_ids))
            found = db.execute(
                f"SELECT COUNT(*) FROM recordings WHERE project_id=? AND id IN ({marks})",
                (project_id, *recording_ids),
            ).fetchone()[0]
            if found != len(recording_ids):
                raise ValueError("A selected recording does not belong to this project.")
            db.execute(
                f"UPDATE recordings SET collection_id=? WHERE id IN ({marks})",
                (collection_id, *recording_ids),
            )
        return len(recording_ids)

    def _migrate_result_hints(self):
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 10:
                return
            if self.initial_version == 9:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-9-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            # Model review hints live beside results, outside reviewed text and its versions.
            db.execute(
                "CREATE TABLE result_hints (result_id TEXT PRIMARY KEY REFERENCES "
                "transcript_results(id) ON DELETE CASCADE, hints TEXT NOT NULL, "
                "created TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=10")

    def result_hints(self, recording_id, result_id):
        rows = self.all(
            "SELECT h.hints FROM result_hints h JOIN transcript_results r ON r.id=h.result_id "
            "WHERE h.result_id=? AND r.recording_id=?",
            (result_id, recording_id),
        )
        return json.loads(rows[0]["hints"]) if rows else None

    def save_result_hints(self, result_id, document):
        self.execute(
            "INSERT OR IGNORE INTO result_hints(result_id,hints,created) VALUES (?,?,?)",
            (result_id, json.dumps(document, ensure_ascii=False), now()),
        )

    def _migrate_native_batches(self):
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 9:
                return
            if self.initial_version == 8:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-8-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            # Independent of recording cascades: an unresolved allocation must retain its journal.
            db.execute(
                "CREATE TABLE native_batch_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
                "state TEXT NOT NULL DEFAULT 'queued', params TEXT NOT NULL, "
                "members TEXT NOT NULL, "
                "message TEXT NOT NULL DEFAULT '', progress REAL NOT NULL DEFAULT 0, "
                "created TEXT NOT NULL, updated TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=9")

    def _migrate_result_trash(self):
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 8:
                return
            if self.initial_version == 7:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-7-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE result_trash ("
                "result_id TEXT PRIMARY KEY NOT NULL "
                "REFERENCES transcript_results(id) ON DELETE CASCADE,"
                "trashed_at TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=8")

    def _migrate_annotations(self):
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 7:
                return
            if self.initial_version == 6:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-6-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE codebook_versions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id)
                ON DELETE CASCADE, sequence INTEGER NOT NULL CHECK(sequence>0),
                predecessor_id TEXT REFERENCES codebook_versions(id), payload TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL, action TEXT NOT NULL,
                actor_kind TEXT NOT NULL, reason TEXT, UNIQUE(project_id,sequence)
            )""")
            db.execute("""CREATE TABLE annotation_runs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id)
                ON DELETE CASCADE, recording_id TEXT NOT NULL REFERENCES recordings(id)
                ON DELETE CASCADE, sequence INTEGER NOT NULL CHECK(sequence>0),
                result_id TEXT NOT NULL REFERENCES transcript_results(id),
                source_version_id TEXT NOT NULL REFERENCES result_versions(id),
                source_payload_sha256 TEXT NOT NULL,
                codebook_version_id TEXT NOT NULL REFERENCES codebook_versions(id),
                codebook_payload_sha256 TEXT NOT NULL, kind TEXT NOT NULL,
                passage_ids TEXT NOT NULL, producer TEXT NOT NULL, created TEXT NOT NULL,
                request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                UNIQUE(recording_id,sequence), UNIQUE(recording_id,request_id)
            )""")
            db.execute("""CREATE TABLE annotation_items (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES annotation_runs(id)
                ON DELETE CASCADE, sequence INTEGER NOT NULL CHECK(sequence>0),
                origin TEXT NOT NULL, suggestion TEXT, suggestion_sha256 TEXT,
                created TEXT NOT NULL, request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                UNIQUE(run_id,sequence), UNIQUE(run_id,request_id)
            )""")
            db.execute("""CREATE TABLE annotation_review_events (
                id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES annotation_items(id)
                ON DELETE CASCADE, revision INTEGER NOT NULL CHECK(revision>0),
                predecessor_id TEXT REFERENCES annotation_review_events(id),
                payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                decision TEXT NOT NULL CHECK(decision IN ('accepted','rejected')),
                action TEXT NOT NULL, actor_kind TEXT NOT NULL, reason TEXT,
                observed_at TEXT NOT NULL, request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                UNIQUE(item_id,revision), UNIQUE(item_id,request_id)
            )""")
            db.execute("PRAGMA user_version=7")

    def _migrate_history(self):
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 6:
                return
            if self.initial_version == 5:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-5-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            self._validate_mirrors(db)
            db.execute("""CREATE TABLE result_versions (
                id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES transcript_results(id)
                ON DELETE CASCADE, sequence INTEGER NOT NULL CHECK(sequence>0),
                predecessor_id TEXT REFERENCES result_versions(id),
                source_version_id TEXT REFERENCES result_versions(id),
                payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL,
                action TEXT NOT NULL, context TEXT NOT NULL, actor_kind TEXT NOT NULL,
                reason TEXT, UNIQUE(result_id,sequence)
            )""")
            db.execute("""CREATE TABLE note_versions (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id)
                ON DELETE CASCADE, recording_id TEXT REFERENCES recordings(id) ON DELETE CASCADE,
                scope TEXT NOT NULL CHECK(scope IN ('project','recording')),
                notes_revision INTEGER NOT NULL CHECK(notes_revision>=0),
                text TEXT NOT NULL, text_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL,
                actor_kind TEXT NOT NULL, action TEXT NOT NULL, reason TEXT,
                CHECK((scope='project' AND recording_id IS NULL) OR
                      (scope='recording' AND recording_id IS NOT NULL))
            )""")
            db.execute(
                "CREATE UNIQUE INDEX project_note_revision ON note_versions"
                "(project_id,notes_revision) WHERE scope='project'"
            )
            db.execute(
                "CREATE UNIQUE INDEX recording_note_revision ON note_versions"
                "(recording_id,notes_revision) WHERE scope='recording'"
            )
            for row in db.execute("SELECT * FROM transcript_results").fetchall():
                self._append_version(
                    db,
                    row,
                    "legacy_observed",
                    {
                        "history_coverage": "before_observation_unknown",
                        "legacy_created": row["created"],
                        "legacy_updated": row["updated"],
                        "processing": processing_metadata(json.loads(row["source"])),
                        "processing_observation": "legacy_unverified",
                        "artifact_evidence": "not_captured",
                    },
                    "migration",
                )
            for kind in ("projects", "recordings"):
                for row in db.execute(f"SELECT * FROM {kind}").fetchall():
                    self._append_note_version(db, kind, row, "legacy_observed", "migration")
            db.execute("PRAGMA user_version=6")

    @staticmethod
    def _validate_mirrors(db):
        for row in db.execute("SELECT * FROM recordings").fetchall():
            if not row["active_transcript_id"]:
                consistent = json.loads(row["segments"]) == [] and json.loads(row["speakers"]) == {}
            else:
                active = db.execute(
                    "SELECT * FROM transcript_results WHERE id=? AND recording_id=?",
                    (row["active_transcript_id"], row["id"]),
                ).fetchone()
                consistent = (
                    active is not None
                    and encode_payload(
                        json.loads(active["segments"]), json.loads(active["speakers"])
                    )[0]
                    == encode_payload(json.loads(row["segments"]), json.loads(row["speakers"]))[0]
                )
            if not consistent:
                raise RuntimeError(
                    "Library transcript projections disagree. Preserve the backup and resolve "
                    "the active result before upgrading; no transcript was chosen automatically."
                )

    @staticmethod
    def _head_version(db, result):
        head = db.execute(
            "SELECT * FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT 1",
            (result["id"],),
        ).fetchone()
        if not serialized_payload_matches(
            result["segments"],
            result["speakers"],
            head["payload"] if head is not None else None,
            head["payload_sha256"] if head is not None else None,
        ):
            raise RuntimeError(
                "The result and its saved research version disagree. Preserve the library."
            )
        return head

    @staticmethod
    def _append_version(
        db,
        result,
        action,
        context,
        actor_kind,
        reason=None,
        source_version_id=None,
        producer_artifacts=None,
        timing_artifacts=None,
        runtime_artifacts=None,
        provider_artifacts=None,
        native_artifacts=None,
    ):
        head = db.execute(
            "SELECT * FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT 1",
            (result["id"],),
        ).fetchone()
        if head is not None:
            source_version_id = head["source_version_id"]
        if source_version_id:
            source = db.execute(
                "SELECT r.recording_id FROM result_versions v "
                "JOIN transcript_results r ON r.id=v.result_id WHERE v.id=?",
                (source_version_id,),
            ).fetchone()
            if source is None or source[0] != result["recording_id"]:
                raise ValueError("The source version must belong to this recording.")
        payload, digest = encode_serialized_payload(result["segments"], result["speakers"])
        if (
            head is not None
            and head["payload"] == payload
            and action not in {"result_trashed", "result_restored", "result_conflict_review"}
            and timing_artifacts is None
        ):
            context = {**context, "requested_action": action}
            action = "save_no_change"
        identifier = new_id()
        if native_artifacts is not None:
            from . import native_capture

            if head is not None:
                raise ValueError("Native observations bind only the original creation version.")
            context = {
                **context,
                "native_artifacts": native_capture.bind(
                    native_artifacts,
                    creation={
                        "result_id": result["id"],
                        "version_id": identifier,
                        "payload_sha256": digest,
                    },
                ),
            }
        if provider_artifacts is not None:
            from . import provider_capture

            if head is not None:
                raise ValueError("Provider observations bind only the original creation version.")
            context = {
                **context,
                "provider_artifacts": provider_capture.bind(
                    provider_artifacts,
                    creation={
                        "result_id": result["id"],
                        "version_id": identifier,
                        "payload_sha256": digest,
                    },
                ),
            }
        if runtime_artifacts is not None:
            from . import runtime_capture

            if head is not None:
                raise ValueError("Runtime observations bind only the original creation version.")
            context = {
                **context,
                "runtime_artifacts": runtime_capture.bind(
                    runtime_artifacts,
                    creation={
                        "result_id": result["id"],
                        "version_id": identifier,
                        "payload_sha256": digest,
                    },
                ),
            }
        if timing_artifacts is not None:
            from . import timing_capture

            if head is None or action != "timing_realign":
                raise ValueError("Timing observations bind only their timing successor version.")
            context = {
                **context,
                "timing_artifacts": timing_capture.bind(
                    timing_artifacts,
                    creation={
                        "result_id": result["id"],
                        "version_id": identifier,
                        "payload_sha256": digest,
                    },
                ),
            }
        if producer_artifacts is not None:
            from . import local_asr_capture

            if head is not None:
                raise ValueError("Producer observations bind only the original creation version.")
            context = {
                **context,
                "producer_artifacts": local_asr_capture.bind(
                    producer_artifacts,
                    creation={
                        "result_id": result["id"],
                        "version_id": identifier,
                        "payload_sha256": digest,
                    },
                ),
            }
        db.execute(
            "INSERT INTO result_versions(id,result_id,sequence,predecessor_id,source_version_id,"
            "payload,payload_sha256,observed_at,action,context,actor_kind,reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                result["id"],
                head["sequence"] + 1 if head else 1,
                head["id"] if head else None,
                source_version_id,
                payload,
                digest,
                now(),
                action,
                json.dumps(context, ensure_ascii=False, sort_keys=True, allow_nan=False),
                actor_kind,
                reason,
            ),
        )
        return {"id": identifier, "payload_sha256": digest}

    @staticmethod
    def _append_note_version(db, kind, row, action, actor_kind, reason=None, previous_text=None):
        if previous_text is not None and row["notes"] == previous_text:
            action = "save_no_change"
        db.execute(
            "INSERT INTO note_versions(id,project_id,recording_id,scope,notes_revision,text,"
            "text_sha256,observed_at,actor_kind,action,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(),
                row["id"] if kind == "projects" else row["project_id"],
                None if kind == "projects" else row["id"],
                "project" if kind == "projects" else "recording",
                row["notes_revision"],
                row["notes"],
                text_hash(row["notes"]),
                now(),
                actor_kind,
                action,
                reason,
            ),
        )

    def _migrate_notes(self):
        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                return
            if self.initial_version == 4:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-4-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            for table in ("projects", "recordings"):
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if "notes" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
                if "notes_revision" not in columns:
                    db.execute(
                        f"ALTER TABLE {table} ADD COLUMN notes_revision INTEGER NOT NULL DEFAULT 0"
                    )
            db.execute("PRAGMA user_version=5")

    def _migrate_protected(self):
        from .providers import protected_approvals

        with self.library_lock(), self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= 4:
                return
            if self.initial_version == 3:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-3-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE projects SET classification='protected' "
                "WHERE classification IN ('sensitive','restricted','confidential')"
            )
            for row in db.execute("SELECT id,value FROM providers").fetchall():
                value = json.loads(row["value"])
                value["approved_for"] = protected_approvals(value.get("approved_for", []))
                db.execute(
                    "UPDATE providers SET value=? WHERE id=?",
                    (json.dumps(value, ensure_ascii=False), row["id"]),
                )
            db.execute("PRAGMA user_version=4")

    def _migrate_projects(self):
        with self.library_lock(), self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version >= 3:
                return
            # Result migration already backed up a schema-1 library in this startup.
            if self.initial_version == 2:
                (self.root / "backups").mkdir(exist_ok=True)
                backup = sqlite3.connect(self.root / "backups" / f"schema-2-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "ALTER TABLE projects ADD COLUMN classification TEXT NOT NULL DEFAULT 'standard'"
            )
            db.execute("ALTER TABLE projects ADD COLUMN storage_dir TEXT")
            db.execute("PRAGMA user_version=3")

    def _migrate_results(self, new_database=False):
        from .providers import Settings

        with self.library_lock(), self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version >= 2:
                return
            if version:
                backups = self.root / "backups"
                backups.mkdir(exist_ok=True)
                backup = sqlite3.connect(backups / f"schema-{version}-{new_id()}.sqlite3")
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE providers (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, value TEXT NOT NULL, created TEXT NOT NULL
            )""")
            db.execute("""CREATE TABLE transcript_results (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id)
                ON DELETE CASCADE, label TEXT NOT NULL, source TEXT NOT NULL,
                segments TEXT NOT NULL, speakers TEXT NOT NULL,
                job_id TEXT UNIQUE, created TEXT NOT NULL, updated TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX results_recording ON transcript_results(recording_id)")
            db.execute("ALTER TABLE recordings ADD COLUMN active_transcript_id TEXT")
            db.execute(
                "ALTER TABLE recordings ADD COLUMN provider_id TEXT REFERENCES providers(id) "
                "ON DELETE SET NULL"
            )
            previous = db.execute("SELECT value FROM settings WHERE id=1").fetchone()
            if previous:
                settings = Settings.model_validate_json(previous[0])
            elif new_database:
                from .catalog import provider_catalog

                settings = Settings(model=provider_catalog()["openai"]["default"])
            else:
                settings = Settings()
            payload = json.loads(settings.model_dump_json())
            if new_database and not previous:
                payload["_initial_model_defaults"] = True
            provider_id = new_id()
            db.execute(
                "INSERT INTO providers VALUES (?,?,?,?)",
                (
                    provider_id,
                    f"OpenAI · {settings.model}"
                    if settings.base_url.startswith("https://api.openai.com/")
                    else settings.model,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO settings(id,value) VALUES (2,?)",
                (json.dumps({"default_provider_id": provider_id}),),
            )
            for row in db.execute("SELECT * FROM recordings WHERE segments!='[]'").fetchall():
                identifier = new_id()
                db.execute(
                    "INSERT INTO transcript_results VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        row["id"],
                        "Existing transcript",
                        '{"kind":"legacy"}',
                        row["segments"],
                        row["speakers"],
                        None,
                        row["created"],
                        now(),
                    ),
                )
                db.execute(
                    "UPDATE recordings SET active_transcript_id=? WHERE id=?",
                    (identifier, row["id"]),
                )
            db.execute("PRAGMA user_version=2")

    @contextmanager
    def library_lock(self):
        """An OS lock prevents two servers from claiming the same paid-work queue."""
        with self._lease_lock:
            if self._lease_depth:
                self._lease_depth += 1
                try:
                    yield
                finally:
                    self._lease_depth -= 1
                return
            with self._library_lock():
                self._lease_depth = 1
                try:
                    yield
                finally:
                    self._lease_depth = 0

    @contextmanager
    def _library_lock(self):
        with (self.root / "server.lock").open("a+b") as lock:
            try:
                # Reading a locked byte itself fails on Windows. Inspect size instead.
                if os.fstat(lock.fileno()).st_size == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError(
                    "This library is already open in another CBS Transcriber server."
                ) from exc
            try:
                from .library_deletion import require_unreserved

                # Again under the lease: a deletion may have reserved it since construction.
                require_unreserved(self.root)
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                raise RuntimeError(
                    "This library uses a newer CBS Transcriber schema. Use a compatible app."
                )
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    def all(self, sql, params=()):
        with self.connection() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        if not rows:
            raise KeyError("The requested item no longer exists.")
        return rows[0]

    def execute(self, sql, params=()):
        with self.connection() as db:
            return db.execute(sql, params).rowcount

    def create_project(
        self, name, classification="standard", storage_parent=None, default_language=None
    ):
        from .language_preferences import validate
        from .providers import CLASSIFICATIONS

        if classification not in CLASSIFICATIONS:
            raise ValueError("Choose a valid project classification.")
        validate(default_language)
        if SCHEMA_VERSION < 17 and default_language is not None:
            raise ValueError("This library schema does not support a project language.")
        identifier = new_id()
        parent = Path(storage_parent).expanduser() if storage_parent else self.default_parent
        if not parent.is_absolute():
            raise ValueError("Choose an absolute project storage folder.")
        folder = parent.resolve() / identifier[:8]
        try:
            folder.mkdir(parents=True)
        except OSError as exc:
            raise ValueError(
                "Cannot create the project folder. Check its location and permissions."
            ) from exc
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if SCHEMA_VERSION >= 17:
                db.execute(
                    "INSERT INTO projects(id,name,created,classification,storage_dir,"
                    "default_language) "
                    "VALUES (?,?,?,?,?,?)",
                    (identifier, name, now(), classification, str(folder), default_language),
                )
            elif default_language is None:
                db.execute(
                    "INSERT INTO projects(id,name,created,classification,storage_dir) "
                    "VALUES (?,?,?,?,?)",
                    (identifier, name, now(), classification, str(folder)),
                )
            row = db.execute("SELECT * FROM projects WHERE id=?", (identifier,)).fetchone()
            self._append_note_version(db, "projects", row, "notes_created", "local_user")
        self.snapshot_project(identifier)
        return self.one("SELECT * FROM projects WHERE id=?", (identifier,))

    def project_folder(self, identifier):
        project = self.one("SELECT storage_dir FROM projects WHERE id=?", (identifier,))
        return Path(project["storage_dir"]) if project["storage_dir"] else self.root

    def save_notes(
        self, kind, identifier, text, revision, *, action="notes_save", context=None, reason=None
    ):
        if kind not in {"projects", "recordings"}:
            raise ValueError("Choose project or recording notes.")
        if not isinstance(text, str) or len(text) > 20_000:
            raise ValueError("Notes must be plain text of at most 20,000 characters.")
        validate_revision(revision)
        edit_metadata(action, context, reason, notes=True)
        with self.snapshot_lock:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(f"SELECT * FROM {kind} WHERE id=?", (identifier,)).fetchone()
                if previous is None:
                    raise KeyError(identifier)
                column = "project_id" if kind == "projects" else "recording_id"
                scope = "project" if kind == "projects" else "recording"
                head = db.execute(
                    f"SELECT * FROM note_versions WHERE {column}=? AND scope=? "
                    "ORDER BY notes_revision DESC LIMIT 1",
                    (identifier, scope),
                ).fetchone()
                if (
                    head is None
                    or head["notes_revision"] != previous["notes_revision"]
                    or head["text"] != previous["notes"]
                    or head["text_sha256"] != text_hash(previous["notes"])
                ):
                    raise RuntimeError(
                        "The notes and their saved version disagree. Preserve the library."
                    )
                updated = db.execute(
                    f"UPDATE {kind} SET notes=?,notes_revision=notes_revision+1 "
                    "WHERE id=? AND notes_revision=?",
                    (text, identifier, revision),
                ).rowcount
                current = db.execute(f"SELECT * FROM {kind} WHERE id=?", (identifier,)).fetchone()
                if current is None:
                    raise KeyError(identifier)
                if not updated:
                    raise ValueError("These notes changed in another window. Reload before saving.")
                project_id = identifier if kind == "projects" else current["project_id"]
                self._append_note_version(
                    db, kind, current, action, "local_user", reason, previous["notes"]
                )
            self.snapshot_project(project_id)
            if kind == "recordings":
                return self.recording(identifier)
            return {
                **dict(current),
                "storage_path": str(self.project_folder(identifier)),
                "snapshot_error": self.snapshot_errors.get(identifier),
            }

    def snapshot_project(self, identifier):
        """Portable, readable outputs in the chosen folder; SQLite stays authoritative."""
        from .transcripts import export_nvivo

        with self.snapshot_lock:
            with self.connection() as db:
                db.execute("BEGIN")
                project = db.execute("SELECT * FROM projects WHERE id=?", (identifier,)).fetchone()
                if project is None:
                    raise KeyError(identifier)
                project = dict(project)
                if not project["storage_dir"]:
                    return  # Legacy layouts retain their original paths.
                recordings = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM recordings WHERE project_id=? ORDER BY created",
                        (identifier,),
                    )
                ]
                results = {}
                active_mirrors = {}
                active_ids = {row["id"]: row["active_transcript_id"] for row in recordings}
                for row in db.execute(
                    "SELECT t.* FROM transcript_results t JOIN recordings r ON r.id=t.recording_id "
                    "WHERE r.project_id=? AND t.id NOT IN (SELECT result_id FROM result_trash) "
                    "ORDER BY t.created,t.id",
                    (identifier,),
                ):
                    result = dict(row)
                    is_active = active_ids[result["recording_id"]] == result["id"]
                    raw_mirrors = (result["segments"], result["speakers"]) if is_active else None
                    for key in ("segments", "speakers", "source"):
                        result[key] = json.loads(result[key])
                    if is_active:
                        active_mirrors[result["recording_id"]] = (
                            raw_mirrors,
                            (result["segments"], result["speakers"]),
                        )
                    results.setdefault(result["recording_id"], []).append(result)
                for recording in recordings:
                    recording.pop("original_input", None)
                    from . import result_roles

                    recording["result_roles"] = result_roles.read(db, self, recording["id"])
                    mirror = active_mirrors.get(recording["id"])
                    for index, key in enumerate(("segments", "speakers")):
                        if mirror is not None and recording[key] == mirror[0][index]:
                            # Both views remain read-only during snapshot serialization.
                            recording[key] = mirror[1][index]
                        else:
                            recording[key] = json.loads(recording[key])
                    recording["transcripts"] = results.get(recording["id"], [])
                collections = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,name,notes,position,created FROM collections "
                        "WHERE project_id=? ORDER BY position,created",
                        (identifier,),
                    )
                ]
            folder = Path(project["storage_dir"])
            try:
                for recording in recordings:
                    for result in recording["transcripts"]:
                        output = folder / "exports" / recording["id"]
                        output.mkdir(parents=True, exist_ok=True)
                        target = output / f"{result['id']}.txt"
                        pending = target.with_suffix(".pending")
                        pending.write_text(
                            export_nvivo(result["segments"], result["speakers"]), encoding="utf-8"
                        )
                        pending.replace(target)
                pending = folder / "project.pending"
                pending.write_text(
                    json.dumps(
                        {
                            "format_version": 1,
                            "project": project,
                            "collections": collections,
                            "recordings": recordings,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                pending.replace(folder / "project.json")
                self.snapshot_errors.pop(identifier, None)
            except OSError:
                self.snapshot_errors[identifier] = (
                    "Saved in the library database, but project folder outputs could not be "
                    "updated. Check free space and folder access, then save again."
                )

    def recording(self, identifier):
        from . import result_roles

        with self.connection() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM recordings WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            row = dict(row)
            row.pop("original_input", None)
            row["transcripts"] = self._results(db, identifier)
            row["result_roles"] = result_roles.read(db, self, identifier)
            row["active_version"] = None
            active = None
            if row["active_transcript_id"]:
                active = db.execute(
                    "SELECT * FROM transcript_results WHERE id=? AND recording_id=? "
                    "AND id NOT IN (SELECT result_id FROM result_trash)",
                    (row["active_transcript_id"], identifier),
                ).fetchone()
                if active is None:
                    raise RuntimeError(
                        "The recording and active result disagree. Preserve the library."
                    )
                head = db.execute(
                    "SELECT id,payload_sha256 FROM result_versions "
                    "WHERE result_id=? ORDER BY sequence DESC LIMIT 1",
                    (active["id"],),
                ).fetchone()
                if head is None:
                    raise RuntimeError(
                        "The active result has no saved version. Preserve the library."
                    )
                row["active_version"] = dict(head)
                active = dict(active)
        raw_segments = row["segments"]
        row["segments"] = json.loads(raw_segments)
        row["speakers"] = json.loads(row["speakers"])
        if active:
            # The owned active row was read in this snapshot. Replay derivation only reads
            # its passages, so identical mirror bytes need one decode within this call.
            active["segments"] = (
                row["segments"]
                if active["segments"] == raw_segments
                else json.loads(active["segments"])
            )
            active["source"] = json.loads(active["source"])
        row["replay_windows"] = self._legacy_replay_windows(active) if active else {}
        row["snapshot_error"] = self.snapshot_errors.get(row["project_id"])
        row["speaker_defaults"] = default_speaker_names(row["segments"], row["speakers"])
        return row

    def results(self, identifier, *, trashed=False):
        with self.connection() as db:
            return self._results(db, identifier, trashed=trashed)

    @staticmethod
    def _results(db, identifier, *, trashed=False):
        membership = "IN" if trashed else "NOT IN"
        # A derived result names the exact source version it was built from. Report when that
        # source result has newer versions, so later edits there are not silently assumed here.
        rows = db.execute(
            "SELECT t.id,t.label,t.source,t.created,t.updated,"
            "(SELECT s.sequence FROM result_versions f JOIN result_versions s "
            "ON s.id=f.source_version_id WHERE f.result_id=t.id AND f.sequence=1) "
            "AS source_version_sequence,"
            "(SELECT MAX(h.sequence) FROM result_versions f JOIN result_versions s "
            "ON s.id=f.source_version_id JOIN result_versions h ON h.result_id=s.result_id "
            "WHERE f.result_id=t.id AND f.sequence=1) AS source_head_sequence"
            + (
                ",(SELECT trashed_at FROM result_trash WHERE result_id=t.id) AS trashed_at"
                if trashed
                else ""
            )
            + " FROM transcript_results t WHERE t.recording_id=? "
            + f"AND t.id {membership} (SELECT result_id FROM result_trash) "
            "ORDER BY t.created,t.id",
            (identifier,),
        ).fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            row["source"] = json.loads(row["source"])
            row["source_changed"] = (
                row["source_version_sequence"] is not None
                and row["source_head_sequence"] > row["source_version_sequence"]
            )
        return rows

    def result(self, recording_id, identifier, *, include_trashed=False):
        row = self.one(
            "SELECT * FROM transcript_results WHERE id=? AND recording_id=?"
            + ("" if include_trashed else " AND id NOT IN (SELECT result_id FROM result_trash)"),
            (identifier, recording_id),
        )
        for key in ("segments", "speakers", "source"):
            row[key] = json.loads(row[key])
        row["replay_windows"] = self._legacy_replay_windows(row)
        row["speaker_defaults"] = default_speaker_names(row["segments"], row["speakers"])
        return row

    def _legacy_replay_windows(self, result):
        """Expose frozen passage context for old partial fragments; never create timings."""
        if result["source"].get("kind") != "enhancement" or not any(
            s["start"] is None or s["end"] is None for s in result["segments"]
        ):
            return {}
        rows = self.all(
            "SELECT params FROM jobs WHERE id=? AND recording_id=? AND kind='enhance'",
            (result["job_id"], result["recording_id"]),
        )
        if not rows:
            return {}
        params = json.loads(rows[0]["params"])
        frozen = params.get("source_snapshot", {})
        if frozen.get("id") != result["source"].get("source_result_id"):
            return {}
        original_segments = frozen.get("segments")
        if not isinstance(original_segments, list):
            return {}
        selected = params.get("segment_ids")
        if params.get("segment_id") is not None:
            selected = [params["segment_id"]]
        if selected is not None and not isinstance(selected, list):
            return {}
        windows, cursor = {}, 0
        segments = result["segments"]
        for original in original_segments:
            if cursor >= len(segments):
                return {}
            if selected is not None and original["id"] not in selected:
                # Scoped repairs preserve these passages. Their independent edit baseline
                # need not equal the reviewed wording supplied to this alignment job.
                current = segments[cursor]
                if current["id"] != original["id"] or current.get("original_text") != original.get(
                    "original_text"
                ):
                    return {}
                cursor += 1
                continue
            text, matched, fragments = original["text"], "", []
            if not text:
                return {}  # Empty text cannot identify an exact fragment boundary.
            while matched != text:
                if cursor >= len(segments):
                    return {}
                fragment = segments[cursor]
                baseline = fragment.get("original_text")
                if (
                    not isinstance(baseline, str)
                    or not baseline
                    or not text.startswith(matched + baseline)
                ):
                    return {}
                matched += baseline
                fragments.append(fragment)
                cursor += 1
            start, end = original.get("start"), original.get("end")
            if (
                not all(
                    isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
                    for v in (start, end)
                )
                or not 0 <= start < end
            ):
                continue
            for fragment in fragments:
                if fragment["start"] is None or fragment["end"] is None:
                    windows[fragment["id"]] = {
                        "start": start,
                        "end": end,
                        "source_result_id": frozen["id"],
                        "source_segment_id": original["id"],
                    }
        return windows if cursor == len(segments) else {}

    def create_recording(self, project_id, filename, identifier=None):
        identifier = identifier or new_id()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._create_recording(db, project_id, filename, identifier)
        return self.recording(identifier)

    def _create_recording(self, db, project_id, filename, identifier):
        if not db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
            raise KeyError(project_id)
        db.execute(
            "INSERT INTO recordings(id,project_id,name,filename,created) VALUES (?,?,?,?,?)",
            (identifier, project_id, Path(filename).stem, filename, now()),
        )
        row = db.execute("SELECT * FROM recordings WHERE id=?", (identifier,)).fetchone()
        self._append_note_version(db, "recordings", row, "notes_created", "local_user")

    def publish_media_recording(
        self, project_id, filename, identifier, collection_id=None, *, params=None
    ):
        """Publish only a closed managed original, with its initial metadata and playback job.

        Optional params carry only the upload batch that groups Activity cards (V162).
        """
        if params is not None and (
            set(params) != {"upload_batch"}
            or not isinstance(params["upload_batch"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", params["upload_batch"])
        ):
            raise ValueError("The upload batch identifier is invalid.")
        job_id = new_id()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                collection_id is not None
                and not db.execute(
                    "SELECT 1 FROM collections WHERE id=? AND project_id=?",
                    (collection_id, project_id),
                ).fetchone()
            ):
                raise KeyError(collection_id)
            self._create_recording(db, project_id, filename, identifier)
            if collection_id is not None:
                db.execute(
                    "UPDATE recordings SET collection_id=? WHERE id=?", (collection_id, identifier)
                )
            self._insert_job(db, identifier, "media", params or {}, job_id)
        return job_id

    def save_transcript(
        self,
        identifier,
        segments,
        speakers,
        revision,
        *,
        action="manual_save",
        context=None,
        reason=None,
        result_id=None,
        working_choice=None,
    ):
        context = edit_metadata(action, context, reason)
        validate_revision(revision)
        pop_hints(segments)
        if not isinstance(segments, list) or not isinstance(speakers, dict):
            raise ValueError("A result snapshot requires passages and speaker labels.")
        # Validate while serializing the mirrors, rather than encoding and discarding an
        # additional whole snapshot. History still encodes the exact stored successor below.
        text, names = (
            json.dumps(segments, ensure_ascii=False, sort_keys=True, allow_nan=False),
            json.dumps(speakers, ensure_ascii=False, sort_keys=True, allow_nan=False),
        )
        # Match history's UTF-8 refusal before opening the writer, including lone surrogates.
        text.encode("utf-8")
        names.encode("utf-8")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM recordings WHERE id=?", (identifier,)).fetchone()
            if current is None:
                raise KeyError(identifier)
            active = current["active_transcript_id"]
            if result_id is not None and active != result_id:
                raise ValueError("This result changed in another window. Reload before saving.")
            head = None
            if active:
                result = db.execute(
                    "SELECT * FROM transcript_results WHERE id=? AND recording_id=?",
                    (active, identifier),
                ).fetchone()
                if result is None:
                    raise RuntimeError("The active result is missing from this recording.")
                head = self._head_version(db, result)
                mirrors_match = (
                    current["segments"] == result["segments"]
                    and current["speakers"] == result["speakers"]
                )
                if not mirrors_match and (
                    encode_payload(
                        json.loads(current["segments"]), json.loads(current["speakers"])
                    )[0]
                    != head["payload"]
                ):
                    raise RuntimeError(
                        "The recording and active result disagree. Preserve the library."
                    )
            from . import working_manual

            first = (
                not active
                and not db.execute(
                    "SELECT 1 FROM transcript_results WHERE recording_id=? LIMIT 1", (identifier,)
                ).fetchone()
            )
            working = working_manual.prepare(
                db, self, identifier, head, working_choice, first=first
            )
            count = db.execute(
                "UPDATE recordings SET segments=?,speakers=?,revision=revision+1 WHERE id=? "
                "AND revision=?",
                (text, names, identifier, revision),
            ).rowcount
            if not count:
                raise ValueError("This transcript changed in another window. Reload before saving.")
            if not active:
                active = new_id()
                db.execute(
                    "INSERT INTO transcript_results VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        active,
                        identifier,
                        "Existing transcript",
                        '{"kind":"manual"}',
                        text,
                        names,
                        None,
                        now(),
                        now(),
                    ),
                )
                db.execute(
                    "UPDATE recordings SET active_transcript_id=? WHERE id=?", (active, identifier)
                )
            else:
                db.execute(
                    "UPDATE transcript_results SET segments=?,speakers=?,updated=? WHERE id=?",
                    (text, names, now(), active),
                )
            if head:
                previous_context = json.loads(head["context"])
                context = {
                    **{
                        key: value
                        for key, value in previous_context.items()
                        if key
                        in {
                            "processing",
                            "processing_observation",
                            "source_result_id",
                            "source_payload_sha256",
                            "source_version_unknown",
                            "producer_job_id",
                            "processing_kind",
                            "segment_ids",
                            "artifact_evidence",
                            "producer_artifacts",
                            "timing_artifacts",
                            "runtime_artifacts",
                            "provider_artifacts",
                            "native_artifacts",
                            "audio_input",
                            "audio_preparations",
                            "history_coverage",
                        }
                    },
                    **context,
                }
            version = self._append_version(
                db,
                db.execute("SELECT * FROM transcript_results WHERE id=?", (active,)).fetchone(),
                action,
                context,
                "local_user",
                reason,
            )
            working_manual.apply(db, self, identifier, version, working)
        recording = self.recording(identifier)
        self.snapshot_project(recording["project_id"])
        recording["snapshot_error"] = self.snapshot_errors.get(recording["project_id"])
        return recording

    def apply_local_timing(self, recording_id, result_id, job_id, revision, *, working_choice=None):
        from . import local_timing

        return local_timing.apply(
            self, recording_id, result_id, job_id, revision, working_choice=working_choice
        )

    def apply_repair_timing(
        self, recording_id, result_id, job_id, revision, fingerprint, *, working_choice=None
    ):
        """One guarded timing version and its retry receipt, using the shared edit writer."""
        from . import restored_owners

        restored_owners.require_job(self, job_id)
        from . import repair_cloud
        from .result_actions import append_edit

        validate_revision(revision)
        already, stale, warning = False, False, None
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            restored_owners.require_owned(db, "job", job_id)
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["recording_id"] != recording_id or row["kind"] != "enhance":
                raise KeyError(job_id)
            job = dict(row)
            params = json.loads(row["params"])
            job["params"] = params
            from . import result_roles, timing_working

            authority = result_roles.job_authority(db, job_id, recording_id, "timing")
            if authority is not None:
                timing_working.validate_authorized_params(authority, "timing", params)
            if (
                job["state"] not in {"completed", "failed", "cancelled"}
                or params.get("realign") is not True
                or params.get("repair_cloud", {}).get("protocol") != repair_cloud.PROTOCOL
                or params.get("ucloud_run", {}).get("remote_state") not in repair_cloud.api.TERMINAL
                or params.get("ucloud_run", {}).get("repair", {}).get("published") is not True
                or params.get("source_snapshot", {}).get("id") != result_id
                or repair_cloud.application_fingerprint(job) != fingerprint
            ):
                raise ValueError("The verified repair timing changed. Reload before applying.")
            current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
            result = db.execute(
                "SELECT * FROM transcript_results WHERE id=? AND recording_id=?",
                (result_id, recording_id),
            ).fetchone()
            if (
                current is None
                or result is None
                or current["active_transcript_id"] != result_id
                or db.execute(
                    "SELECT 1 FROM result_trash WHERE result_id=?", (result_id,)
                ).fetchone()
            ):
                raise ValueError("Open the live result this repair belongs to.")
            head = self._head_version(db, result)
            if (
                encode_payload(json.loads(current["segments"]), json.loads(current["speakers"]))[0]
                != head["payload"]
            ):
                raise ValueError("The recording and active result disagree. Preserve the library.")
            receipt = params.get("realign_receipt")
            if params.get("realign_applied") == "applied":
                if working_choice is not None and (
                    not isinstance(receipt, dict) or receipt.get("working_choice") != working_choice
                ):
                    raise ValueError("This timing was already applied; choose Working separately.")
                if not isinstance(receipt, dict) or receipt.get("fingerprint") != fingerprint:
                    raise ValueError("The applied repair receipt is invalid. Preserve its history.")
                if (
                    authority is not None
                    and receipt.get("working_follow_warning") is not None
                    and receipt["working_follow_warning"] != timing_working.FOLLOW_WARNING
                ):
                    raise ValueError("The applied timing Working warning is invalid.")
                warning = receipt.get("working_follow_warning") if authority is not None else None
                version = db.execute(
                    "SELECT * FROM result_versions WHERE id=? AND result_id=?",
                    (receipt["version"]["id"], result_id),
                ).fetchone()
                if (
                    version is None
                    or version["payload_sha256"] != receipt["version"]["payload_sha256"]
                    or json.loads(version["context"]).get("job_id") != job_id
                    or version["action"] not in {"timing_realign", "save_no_change"}
                ):
                    raise ValueError("The applied repair version failed its integrity check.")
                decode_payload(version["payload"], version["payload_sha256"])
                from . import timing_working

                if authority is not None or working_choice is not None:
                    timing_working.validate_choice_receipt(
                        db, recording_id, receipt, version["predecessor_id"], version
                    )
                retry = (
                    revision == receipt["base_revision"]
                    and current["revision"] == receipt["applied_revision"]
                    and head["id"] == version["id"]
                )
                if revision != current["revision"] and not retry:
                    raise ValueError("This transcript changed. Reload before applying timing.")
                already = True
            elif params.get("realign_applied"):
                raise ValueError("These repair passages changed. Start a new timing request.")
            else:
                if revision != current["revision"]:
                    raise ValueError("This transcript changed. Reload before applying timing.")
                segments, speakers = (
                    json.loads(current["segments"]),
                    json.loads(current["speakers"]),
                )
                frozen = {s["id"]: s for s in params["source_snapshot"]["segments"]}
                outputs = params["realign_output"]["passages"]
                applied, skipped = [], []
                for segment in segments:
                    identifier = segment["id"]
                    if identifier not in outputs:
                        continue
                    if identifier not in frozen or any(
                        segment.get(k) != frozen[identifier].get(k)
                        for k in ("text", "start", "end", "words", "word_timing", "words_current")
                    ):
                        skipped.append(identifier)
                        continue
                    timing = outputs[identifier]
                    segment.update(
                        words=json.loads(json.dumps(timing["words"])),
                        word_timing=timing["word_timing"],
                        words_current=True,
                    )
                    if "start" in timing and (segment["start"] is None or segment["end"] is None):
                        segment.update(start=timing["start"], end=timing["end"])
                    applied.append(identifier)
                skipped.extend(i for i in outputs if i not in {s["id"] for s in segments})
                if applied:
                    version = append_edit(
                        self,
                        db,
                        current,
                        result_id,
                        head,
                        segments,
                        speakers,
                        action="timing_realign",
                        context={"job_id": job_id},
                        reason=None,
                        **(
                            {"working_choice": working_choice} if working_choice is not None else {}
                        ),
                    )
                    if working_choice is None:
                        from . import timing_working

                        followed = timing_working.follow(
                            self, db, recording_id, job_id, params, head, version
                        )
                        if authority is not None and not followed:
                            warning = timing_working.FOLLOW_WARNING
                    params["realign_applied"] = "applied"
                    params["realign_receipt"] = {
                        "version": version,
                        "fingerprint": fingerprint,
                        "base_revision": revision,
                        "applied_revision": revision + 1,
                        "applied_ids": applied,
                        "skipped_ids": skipped,
                        **({"working_follow_warning": warning} if warning else {}),
                        **(
                            {"working_choice": working_choice} if working_choice is not None else {}
                        ),
                    }
                else:
                    params["realign_applied"], stale = "stale", True
                db.execute(
                    "UPDATE jobs SET params=?,updated=? WHERE id=?",
                    (json.dumps(params), now(), job_id),
                )
        if stale:
            raise ValueError(
                "The selected passages changed; their newer text and timing were preserved."
            )
        if not already:
            self.snapshot_project(current["project_id"])
        saved = self.recording(recording_id)
        saved["snapshot_error"] = self.snapshot_errors.get(current["project_id"])
        if warning:
            saved["working_follow_warning"] = warning
        return saved

    def _import_source_path(self, recording_id, evidence):
        project = self.one("SELECT project_id FROM recordings WHERE id=?", (recording_id,))
        base = self.project_folder(project["project_id"])
        directory = self.directory(recording_id, project["project_id"])
        sources = directory / "sources"
        target = sources / (evidence["source_id"] + "." + evidence["format"])
        # Check managed children before resolving: an in-project link still changes ownership.
        for path in (base / "recordings", directory, sources, target):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Imported transcript files must not use filesystem links.")
            if path.exists() and (not path.is_file() if path == target else not path.is_dir()):
                raise ValueError("The imported transcript storage is not a regular managed path.")
        if not containment_path(target).is_relative_to(containment_path(directory)):
            raise ValueError("Imported transcripts must stay inside their recording folder.")
        return target

    def _verify_import_artifact(self, recording_id, evidence, payload):
        target = self._import_source_path(recording_id, evidence)
        before = target.lstat()
        descriptor = os.open(
            target,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != evidence["bytes"]
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError("The imported transcript source failed its integrity check.")
            data = stream.read(MAX_TRANSCRIPT_BYTES + 1)
            after = os.fstat(stream.fileno())
        self._import_source_path(recording_id, evidence)
        final = target.lstat()

        def identity(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
            )

        # Windows lstat/fstat may use different ctime meanings; compare like observations.
        if (
            identity(before) != identity(opened)
            or identity(opened) != identity(after)
            or identity(after) != identity(final)
            or before.st_ctime_ns != final.st_ctime_ns
            or opened.st_ctime_ns != after.st_ctime_ns
            or len(data) != evidence["bytes"]
            or hashlib.sha256(data).hexdigest() != evidence["sha256"]
        ):
            raise ValueError("The imported transcript source failed its integrity check.")
        parsed = parse_transcript(
            data, "source." + evidence["format"], generation=evidence["schema"]
        )
        pop_hints(parsed)
        if encode_payload(parsed, {})[0] != payload:
            raise ValueError("The imported transcript does not match its preserved source.")

    def import_transcript(self, recording_id, data, filename, revision):
        """Preserve exact bounded input bytes before publishing their parsed result."""
        validate_revision(revision)
        if not isinstance(data, bytes) or not data or not isinstance(filename, str):
            raise ValueError("Supply a nonempty JSON or CSV transcript.")
        parsed = parse_transcript(data, filename, generation=2)
        evidence = validate_import_artifact_evidence(
            {
                "schema": 2,
                "role": "transcript_import",
                "source_id": new_id(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "format": Path(filename).suffix.lower().lstrip("."),
            }
        )
        with self.snapshot_lock:
            current = self.recording(recording_id)
            if current["revision"] != revision:
                raise ValueError("This transcript changed in another window. Reload before saving.")
            try:
                target = self._import_source_path(recording_id, evidence)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._import_source_path(recording_id, evidence)
                with target.open("xb") as output:
                    if output.write(data) != len(data):
                        raise OSError("Incomplete transcript source write")
                    output.flush()
                    os.fsync(output.fileno())
                return self.add_result(
                    recording_id,
                    parsed,
                    {},
                    filename,
                    {"kind": "import"},
                    activate=True,
                    revision=revision,
                    import_artifact_evidence=evidence,
                    auto_first=True,
                )
            except OSError as exc:
                raise ValueError(
                    "The transcript source could not be preserved. Check storage access and space."
                ) from exc

    def add_result(
        self,
        recording_id,
        segments,
        speakers,
        label,
        source,
        *,
        job_id=None,
        activate=False,
        revision=None,
        runtime_receipt=None,
        import_artifact_evidence=None,
        producer_artifacts=None,
        runtime_artifacts=None,
        provider_artifacts=None,
        native_artifacts=None,
        auto_first=False,
    ):
        from . import restored_owners

        restored_owners.require_job(self, job_id) if job_id is not None else None
        from . import runtime_store

        if not isinstance(source, dict):
            raise ValueError("Result source metadata must be a JSON object.")
        if "producer_artifacts" in source:
            raise ValueError("Source metadata cannot assert producer artifact capture.")
        if "runtime_artifacts" in source:
            raise ValueError("Source metadata cannot assert Runtime artifact capture.")
        if "provider_artifacts" in source:
            raise ValueError("Source metadata cannot assert provider artifact capture.")
        if "native_artifacts" in source:
            raise ValueError("Source metadata cannot assert native artifact capture.")
        evidence = None
        if import_artifact_evidence is not None:
            if source.get("kind") != "import" or job_id is not None:
                raise ValueError("Only a new transcript import can capture its source artifact.")
            evidence = validate_import_artifact_evidence(import_artifact_evidence)
            if "artifact_evidence" in source and source["artifact_evidence"] != evidence:
                raise ValueError("The imported transcript source evidence disagrees.")
            source = {**source, "artifact_evidence": evidence}
        elif "artifact_evidence" in source:
            raise ValueError("Source metadata cannot assert imported artifact capture.")
        # Review hints never enter the reviewed text payload or its version history.
        hints = hints_document(pop_hints(segments), segments, "creation")
        identifier = new_id()
        text, names = (
            json.dumps(segments, ensure_ascii=False),
            json.dumps(speakers, ensure_ascii=False),
        )
        payload, _ = encode_payload(segments, speakers)
        guard = self.snapshot_lock if evidence is not None else nullcontext()
        with guard, self.connection() as db:
            if evidence is not None:
                try:
                    self._verify_import_artifact(recording_id, evidence, payload)
                except OSError as exc:
                    raise ValueError(
                        "The imported transcript source could not be verified. Preserve its files."
                    ) from exc
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
            if current is None:
                raise KeyError(recording_id)
            parent = (
                db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if job_id
                else None
            )
            if parent is not None and parent["recording_id"] != recording_id:
                raise ValueError("The producing job belongs to another recording.")
            if parent is not None:
                restored_owners.require_owned(db, "job", job_id)
                parent_params = json.loads(parent["params"])
                if not isinstance(parent_params, dict):
                    raise ValueError("The producing job parameters are invalid.")
                for key, kind in (
                    ("native_batch_id", "native_batch"),
                    ("runtime_batch_id", "runtime_batch"),
                ):
                    if parent_params.get(key):
                        restored_owners.require_owned(db, kind, parent_params[key])
            if job_id:
                existing = db.execute(
                    "SELECT id,recording_id FROM transcript_results WHERE job_id=?", (job_id,)
                ).fetchone()
                if existing:
                    if existing["recording_id"] != recording_id:
                        raise ValueError("The producing job belongs to another recording.")
                    runtime_store.record_result(
                        db, self, parent, existing[0], runtime_receipt, existing=True
                    )
                    return self.result(recording_id, existing[0], include_trashed=True)
            if activate and revision != current["revision"]:
                raise ValueError("This transcript changed in another window. Reload before saving.")
            first_result = (
                db.execute(
                    "SELECT 1 FROM transcript_results WHERE recording_id=? LIMIT 1", (recording_id,)
                ).fetchone()
                is None
            )
            context, source_version_id = self._result_origin(db, recording_id, source, parent)
            from . import local_asr_capture

            captured = local_asr_capture.for_creation(parent, recording_id, producer_artifacts)
            from . import runtime_capture

            runtime_captured = runtime_capture.for_creation(
                db, parent, recording_id, runtime_artifacts, runtime_receipt
            )
            from . import provider_capture

            provider_captured = provider_capture.for_creation(
                parent, recording_id, provider_artifacts
            )
            from . import native_capture

            # Reads the batch journal in this same creation transaction.
            native_captured = native_capture.for_creation(
                db, parent, recording_id, native_artifacts
            )
            if evidence is not None:
                context["artifact_evidence"] = evidence
            db.execute(
                "INSERT INTO transcript_results VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    recording_id,
                    label,
                    json.dumps(source, ensure_ascii=False),
                    text,
                    names,
                    job_id,
                    now(),
                    now(),
                ),
            )
            if hints:
                db.execute(
                    "INSERT INTO result_hints(result_id,hints,created) VALUES (?,?,?)",
                    (identifier, json.dumps(hints, ensure_ascii=False), now()),
                )
            if activate or not current["active_transcript_id"]:
                db.execute(
                    "UPDATE recordings SET segments=?,speakers=?,active_transcript_id=?,"
                    "revision=revision+1 WHERE id=?",
                    (text, names, identifier, recording_id),
                )
            version = self._append_version(
                db,
                db.execute("SELECT * FROM transcript_results WHERE id=?", (identifier,)).fetchone(),
                "provider_result"
                if source.get("kind") == "provider"
                else "enhancement_result"
                if parent is not None
                and parent["kind"] == "enhance"
                or source.get("kind") == "enhancement"
                else "imported_result"
                if source.get("kind") == "import"
                else "provider_result",
                context,
                "worker" if job_id else "local_user",
                source_version_id=source_version_id,
                producer_artifacts=captured,
                runtime_artifacts=runtime_captured,
                provider_artifacts=provider_captured,
                native_artifacts=native_captured,
            )
            from . import result_roles

            params = json.loads(parent["params"]) if parent is not None else {}
            acceptance = params.get("input_acceptance")
            authority = (
                result_roles.job_authority(db, job_id, recording_id, "result")
                if parent is not None
                else None
            )
            expected = None
            if authority is not None:
                frozen_source = params.get("source_snapshot")
                if authority["admission_sha256"] is not None:
                    if (
                        not isinstance(acceptance, dict)
                        or acceptance.get("accepted_sha256") != authority["admission_sha256"]
                        or not result_roles.frozen_roles_equal(
                            acceptance.get("working_role"), authority["role"]
                        )
                    ):
                        raise ValueError("The Working authority differs from its admission.")
                elif params.get(
                    "working_role_policy"
                ) != result_roles.WORKING_POLICY or not result_roles.frozen_roles_equal(
                    params.get("working_role"), authority["role"]
                ):
                    raise ValueError("The Working authority differs from its processing job.")
                if (
                    source_version_id != authority["source_version_id"]
                    or context.get("source_payload_sha256") != authority["source_payload_sha256"]
                    or (
                        result_roles.source_snapshot_hash(frozen_source)
                        if frozen_source is not None
                        else None
                    )
                    != authority["source_snapshot_sha256"]
                ):
                    raise ValueError("The Working authority differs from the frozen source.")
                expected = authority["role"]
            if auto_first and parent is None:
                expected = result_roles.read(db, self, recording_id)
            if expected is not None:
                result_roles.validate_frozen(expected)
                previous = expected["working"]
                source_head = (
                    db.execute(
                        "SELECT v.id FROM result_versions v WHERE v.result_id=? "
                        "AND NOT EXISTS (SELECT 1 FROM result_trash t "
                        "WHERE t.result_id=v.result_id) "
                        "ORDER BY v.sequence DESC LIMIT 1",
                        (context["source_result_id"],),
                    ).fetchone()
                    if source_version_id is not None and context.get("source_result_id") is not None
                    else None
                )
                if first_result and previous is None:
                    result_roles.move_automatic(
                        db, self, recording_id, version["id"], expected, "working_auto_first"
                    )
                elif source_head is not None and source_head["id"] == source_version_id:
                    result_roles.move_automatic(
                        db, self, recording_id, version["id"], expected, "working_auto_successor"
                    )
            runtime_store.record_result(db, self, parent, identifier, runtime_receipt)
            if job_id:
                # The result is the job's receipt: a stop arriving after this commit must not
                # turn a saved result into a "cancelled" job or cancel its follow-up.
                db.execute(
                    "UPDATE jobs SET state='completed',progress=1,message='Complete',updated=? "
                    "WHERE id=? AND state='running'",
                    (now(), job_id),
                )
                followup = (
                    json.loads(parent["params"]).get("followup")
                    if parent and parent["kind"] == "transcribe"
                    else None
                )
                if followup:
                    working_fields = {
                        "working_role",
                        "working_role_policy",
                        "timing_working_policy",
                    }
                    legacy_working_fields = expected is None and bool(
                        working_fields & followup["params"].keys()
                    )
                    if expected is not None and (working_fields & followup["params"].keys()):
                        raise ValueError("Followup parameters cannot assert a Working designation.")
                    params = {
                        **followup["params"],
                        "parent_transcription_id": job_id,
                        "source_snapshot": {
                            "id": identifier,
                            "segments": json.loads(text),
                            "speakers": json.loads(names),
                            "source_version_id": version["id"],
                            "source_payload_sha256": version["payload_sha256"],
                        },
                    }
                    working_role_verified = False
                    if expected is not None:
                        role = result_roles.read(db, self, recording_id)
                        if role == expected or (
                            role["working"] and role["working"]["version_id"] == version["id"]
                        ):
                            params["working_role"] = role
                            params["working_role_policy"] = result_roles.WORKING_POLICY
                            working_role_verified = True
                    if working_role_verified:
                        self._insert_job(
                            db,
                            recording_id,
                            "enhance",
                            params,
                            followup["id"],
                            working_role_verified=True,
                        )
                    elif legacy_working_fields:
                        self._insert_job(
                            db,
                            recording_id,
                            "enhance",
                            params,
                            followup["id"],
                            legacy_working_fields=True,
                        )
                    else:
                        self._insert_job(db, recording_id, "enhance", params, followup["id"])
        self.snapshot_project(current["project_id"])
        # A user may trash the newly committed result while snapshots are written.
        # The producer still receives its committed receipt, without restoring visibility.
        return self.result(recording_id, identifier, include_trashed=True)

    @staticmethod
    def _result_origin(db, recording_id, source, parent):
        context = {
            "processing": processing_metadata(source),
            "processing_observation": "captured_at_creation",
            "artifact_evidence": "not_captured",
        }
        params = json.loads(parent["params"]) if parent is not None else {}
        if parent is not None:
            context.update(producer_job_id=parent["id"], processing_kind=parent["kind"])
        if "audio_input" in params:
            from .audio_inputs import validate, validate_evidence

            context["audio_input"] = validate(params["audio_input"])
            if context["audio_input"]["recording_id"] != recording_id:
                raise ValueError("The frozen audio input belongs to another recording.")
            receipts = params.get("audio_preparations", [])
            if not isinstance(receipts, list) or len(receipts) > 200:
                raise ValueError("The frozen audio preparation evidence is invalid.")
            if receipts:
                context["audio_preparations"] = [
                    validate_evidence(receipt, context["audio_input"]) for receipt in receipts
                ]
            if "remote_audio_preparation" in params:
                from .remote_audio_input import for_job, validate_preparation

                context["remote_audio_preparation"] = validate_preparation(
                    params["remote_audio_preparation"], for_job(params)
                )
        frozen = params.get("source_snapshot")
        if frozen is not None and not isinstance(frozen, dict):
            raise ValueError("The frozen processing source is invalid.")
        if (
            frozen is None
            and source.get("kind") != "enhancement"
            and not any(key in source for key in ("source_result_id", "source_version_id"))
        ):
            return context, None
        frozen = frozen or {}
        source_id = frozen.get("id", source.get("source_result_id"))
        if source_id is not None:
            original = db.execute(
                "SELECT id FROM transcript_results WHERE id=? AND recording_id=?",
                (source_id, recording_id),
            ).fetchone()
            if original is None:
                raise ValueError("The processing source must belong to this recording.")
            context["source_result_id"] = source_id
        if source.get("source_result_id") not in (None, source_id):
            raise ValueError("The result source differs from its frozen processing input.")
        digest, payload = None, None
        if "segments" in frozen and "speakers" in frozen:
            payload, digest = encode_payload(frozen["segments"], frozen["speakers"])
            if frozen.get("source_payload_sha256") not in (None, digest):
                raise ValueError("The frozen source hash does not match its exact content.")
            context["source_payload_sha256"] = digest
        version_id = frozen.get("source_version_id")
        if source.get("source_version_id") not in (None, version_id):
            raise ValueError("The result source version differs from its frozen input.")
        if version_id is not None:
            version = db.execute(
                "SELECT v.*,r.recording_id FROM result_versions v "
                "JOIN transcript_results r ON r.id=v.result_id WHERE v.id=?",
                (version_id,),
            ).fetchone()
            if (
                version is None
                or version["recording_id"] != recording_id
                or version["result_id"] != source_id
                or version["payload"] != payload
                or version["payload_sha256"] != digest
            ):
                raise ValueError("The source version must match the exact frozen recording input.")
        else:
            context["source_version_unknown"] = True
        selected = params.get("segment_ids", source.get("segment_ids"))
        single = params.get("segment_id", source.get("segment_id"))
        if single is not None:
            selected = [single]
        if isinstance(selected, list) and all(isinstance(item, str) for item in selected):
            context["segment_ids"] = selected
        return context, version_id

    def freeze_result(self, recording_id, identifier, revision):
        """Read revision and result from one SQLite snapshot before background enhancement."""
        validate_revision(revision)
        with self.connection() as db:
            db.execute("BEGIN")
            return self._freeze_result(db, recording_id, identifier, revision)

    def _freeze_result(self, db, recording_id, identifier, revision):
        validate_revision(revision)
        current = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
        row = db.execute(
            "SELECT * FROM transcript_results WHERE id=? AND recording_id=? "
            "AND id NOT IN (SELECT result_id FROM result_trash)",
            (identifier, recording_id),
        ).fetchone()
        if current is None or row is None:
            raise KeyError(identifier)
        if current["revision"] != revision:
            raise ValueError(
                "This transcript changed in another window. Reload before enhancement."
            )
        head = self._head_version(db, row)
        if (
            current["active_transcript_id"] == identifier
            and encode_payload(json.loads(current["segments"]), json.loads(current["speakers"]))[0]
            != head["payload"]
        ):
            raise RuntimeError("The recording and active result disagree. Preserve the library.")
        return {
            "id": row["id"],
            "segments": json.loads(row["segments"]),
            "speakers": json.loads(row["speakers"]),
            "source_version_id": head["id"],
            "source_payload_sha256": head["payload_sha256"],
        }

    @staticmethod
    def _history_page(limit, before):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Choose a history page size between 1 and 100.")
        if before is not None and (type(before) is not int or not 0 < before < 2**63):
            raise ValueError("Use a positive history page cursor.")

    def result_versions(self, recording_id, result_id, limit=50, before_sequence=None):
        self._history_page(limit, before_sequence)
        with self.connection() as db:
            db.execute("BEGIN")
            if not db.execute(
                "SELECT id FROM transcript_results WHERE id=? AND recording_id=?",
                (result_id, recording_id),
            ).fetchone():
                raise KeyError(result_id)
            rows = db.execute(
                "SELECT id,result_id,sequence,predecessor_id,source_version_id,payload_sha256,"
                "observed_at,action,context,actor_kind,reason FROM result_versions "
                "WHERE result_id=? "
                "AND (? IS NULL OR sequence<?) ORDER BY sequence DESC LIMIT ?",
                (result_id, before_sequence, before_sequence, limit),
            ).fetchall()
        return [{**dict(row), "context": json.loads(row["context"])} for row in rows]

    def result_version(self, recording_id, result_id, version_id):
        row = self.one(
            "SELECT v.* FROM result_versions v JOIN transcript_results r ON r.id=v.result_id "
            "WHERE v.id=? AND v.result_id=? AND r.recording_id=?",
            (version_id, result_id, recording_id),
        )
        row["payload"] = decode_payload(row["payload"], row["payload_sha256"])
        row["context"] = json.loads(row["context"])
        row["payload"]["speaker_defaults"] = default_speaker_names(
            row["payload"]["segments"], row["payload"]["speakers"]
        )
        return row

    def note_versions(self, kind, identifier, limit=50, before_revision=None):
        if kind not in {"projects", "recordings"}:
            raise ValueError("Choose project or recording notes.")
        self._history_page(limit, before_revision)
        with self.connection() as db:
            db.execute("BEGIN")
            if not db.execute(f"SELECT id FROM {kind} WHERE id=?", (identifier,)).fetchone():
                raise KeyError(identifier)
            column = "project_id" if kind == "projects" else "recording_id"
            scope = "project" if kind == "projects" else "recording"
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,project_id,recording_id,scope,notes_revision,text_sha256,"
                    "observed_at,"
                    f"actor_kind,action,reason FROM note_versions WHERE {column}=? AND scope=? "
                    "AND (? IS NULL OR notes_revision<?) ORDER BY notes_revision DESC LIMIT ?",
                    (identifier, scope, before_revision, before_revision, limit),
                ).fetchall()
            ]

    def activate_result(self, recording_id, identifier, revision):
        validate_revision(revision)
        with self.snapshot_lock:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT * FROM recordings WHERE id=?", (recording_id,)
                ).fetchone()
                result = db.execute(
                    "SELECT * FROM transcript_results WHERE id=? AND recording_id=? "
                    "AND id NOT IN (SELECT result_id FROM result_trash)",
                    (identifier, recording_id),
                ).fetchone()
                if current is None or result is None:
                    raise KeyError(identifier)
                if current["revision"] != revision:
                    raise ValueError(
                        "This transcript changed in another window. Reload before saving."
                    )
                self._head_version(db, result)
                db.execute(
                    "UPDATE recordings SET segments=?,speakers=?,active_transcript_id=?,"
                    "revision=revision+1 WHERE id=?",
                    (result["segments"], result["speakers"], identifier, recording_id),
                )
            self.snapshot_project(current["project_id"])
            return self.recording(recording_id)

    def set_result_trashed(self, recording_id, identifier, revision, *, trashed):
        """Change result visibility without removing frozen work or research evidence."""
        validate_revision(revision)
        with self.snapshot_lock:
            with self.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT * FROM recordings WHERE id=?", (recording_id,)
                ).fetchone()
                result = db.execute(
                    "SELECT * FROM transcript_results WHERE id=? AND recording_id=?",
                    (identifier, recording_id),
                ).fetchone()
                if current is None or result is None:
                    raise KeyError(identifier)
                if current["revision"] != revision:
                    raise ValueError(
                        "This transcript changed in another window. Reload before saving."
                    )
                marker = db.execute(
                    "SELECT 1 FROM result_trash WHERE result_id=?", (identifier,)
                ).fetchone()
                changed = bool(marker) != trashed
                if changed:
                    head = self._head_version(db, result)
                    if (
                        current["active_transcript_id"] == identifier
                        and encode_payload(
                            json.loads(current["segments"]), json.loads(current["speakers"])
                        )[0]
                        != head["payload"]
                    ):
                        raise RuntimeError(
                            "The recording and active result disagree. Preserve the library."
                        )
                    if trashed:
                        from . import result_roles

                        result_roles.clear_trashed(self, db, recording_id, identifier)
                        db.execute("INSERT INTO result_trash VALUES (?,?)", (identifier, now()))
                    else:
                        db.execute("DELETE FROM result_trash WHERE result_id=?", (identifier,))
                    replace_active = (
                        trashed and current["active_transcript_id"] == identifier
                    ) or (not trashed and current["active_transcript_id"] is None)
                    if replace_active:
                        selected = (
                            db.execute(
                                "SELECT * FROM transcript_results WHERE recording_id=? "
                                "AND id NOT IN (SELECT result_id FROM result_trash) "
                                "ORDER BY created DESC,id DESC LIMIT 1",
                                (recording_id,),
                            ).fetchone()
                            if trashed
                            else result
                        )
                        if selected is not None:
                            self._head_version(db, selected)
                        db.execute(
                            "UPDATE recordings SET active_transcript_id=?,segments=?,speakers=?,"
                            "revision=revision+1 WHERE id=?",
                            (
                                selected["id"] if selected else None,
                                selected["segments"] if selected else "[]",
                                selected["speakers"] if selected else "{}",
                                recording_id,
                            ),
                        )
                    else:
                        db.execute(
                            "UPDATE recordings SET revision=revision+1 WHERE id=?", (recording_id,)
                        )
                    self._append_version(
                        db,
                        result,
                        "result_trashed" if trashed else "result_restored",
                        json.loads(head["context"]),
                        "local_user",
                    )
            if changed:
                self.snapshot_project(current["project_id"])
            return self.recording(recording_id)

    @staticmethod
    def _codebook(db, project_id, version_id):
        row = db.execute(
            "SELECT * FROM codebook_versions WHERE id=? AND project_id=?",
            (version_id, project_id),
        ).fetchone()
        if row is None:
            raise KeyError(version_id)
        payload = annotations.verified_document(
            row["payload"], row["payload_sha256"], annotations.CODEBOOK_FORMAT
        )
        annotations.codebook_payload(payload)
        return {**dict(row), "payload": payload}

    def save_codebook(self, project_id, payload, revision, *, reason=None):
        validate_revision(revision)
        edit_metadata("manual_save", None, reason)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
                raise KeyError(project_id)
            head = db.execute(
                "SELECT * FROM codebook_versions WHERE project_id=? ORDER BY sequence DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if revision != (head["sequence"] if head else 0):
                raise ValueError("The codebook changed in another window. Reload before saving.")
            previous = self._codebook(db, project_id, head["id"])["payload"] if head else None
            clean = annotations.codebook_payload(payload, previous)
            encoded, digest = annotations.canonical(clean)
            identifier = new_id()
            db.execute(
                "INSERT INTO codebook_versions(id,project_id,sequence,predecessor_id,payload,"
                "payload_sha256,observed_at,action,actor_kind,reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project_id,
                    revision + 1,
                    head["id"] if head else None,
                    encoded,
                    digest,
                    now(),
                    "save_no_change" if head and head["payload"] == encoded else "codebook_save",
                    "local_user",
                    reason,
                ),
            )
            return self._codebook(db, project_id, identifier)

    def codebook_versions(self, project_id, limit=50, before_sequence=None):
        self._history_page(limit, before_sequence)
        with self.connection() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone():
                raise KeyError(project_id)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,project_id,sequence,predecessor_id,payload_sha256,observed_at,"
                    "action,"
                    "actor_kind,json_extract(payload,'$.approved') AS approved,"
                    "json_array_length(payload,'$.topics') AS topic_count FROM codebook_versions "
                    "WHERE project_id=? AND (? IS NULL OR sequence<?) "
                    "ORDER BY sequence DESC LIMIT ?",
                    (project_id, before_sequence, before_sequence, limit),
                ).fetchall()
            ]

    def codebook_version(self, project_id, version_id):
        with self.connection() as db:
            db.execute("BEGIN")
            return self._codebook(db, project_id, version_id)

    @staticmethod
    def _annotation_context(db, recording_id, result_id, source_version_id, codebook_version_id):
        meta = db.execute(
            "SELECT r.project_id,length(CAST(v.payload AS BLOB)) AS size FROM recordings r "
            "JOIN transcript_results t ON t.recording_id=r.id "
            "JOIN result_versions v ON v.result_id=t.id "
            "WHERE r.id=? AND t.id=? AND v.id=?",
            (recording_id, result_id, source_version_id),
        ).fetchone()
        if meta is None:
            raise KeyError(source_version_id)
        if meta["size"] > annotations.MAX_SOURCE_BYTES:
            raise ValueError("This frozen source exceeds the 16 MiB annotation prototype limit.")
        version = db.execute(
            "SELECT * FROM result_versions WHERE id=?", (source_version_id,)
        ).fetchone()
        source = decode_payload(version["payload"], version["payload_sha256"])
        codebook = Store._codebook(db, meta["project_id"], codebook_version_id)
        if not codebook["payload"]["approved"]:
            raise ValueError("Explicitly approve this codebook before annotating a transcript.")
        return meta["project_id"], dict(version), source, codebook

    @staticmethod
    def _run(db, recording_id, run_id):
        row = db.execute(
            "SELECT * FROM annotation_runs WHERE id=? AND recording_id=?", (run_id, recording_id)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        project_id, version, source, codebook = Store._annotation_context(
            db, recording_id, row["result_id"], row["source_version_id"], row["codebook_version_id"]
        )
        if (
            row["project_id"] != project_id
            or row["source_payload_sha256"] != version["payload_sha256"]
            or row["codebook_payload_sha256"] != codebook["payload_sha256"]
        ):
            raise ValueError("The annotation run's frozen evidence identities do not match.")
        passage_ids = json.loads(row["passage_ids"])
        annotations.selected_passages(source, passage_ids)
        return dict(row), source, codebook["payload"], passage_ids

    @staticmethod
    def _run_view(db, row, *, exact=True):
        head = db.execute(
            "SELECT id FROM result_versions WHERE result_id=? ORDER BY sequence DESC LIMIT 1",
            (row["result_id"],),
        ).fetchone()
        codebook_head = db.execute(
            "SELECT id FROM codebook_versions WHERE project_id=? ORDER BY sequence DESC LIMIT 1",
            (row["project_id"],),
        ).fetchone()
        counts = {"unreviewed": 0, "accepted": 0, "rejected": 0}
        for item in db.execute(
            "SELECT (SELECT decision FROM annotation_review_events e WHERE e.item_id=i.id "
            "ORDER BY revision DESC LIMIT 1) AS decision FROM annotation_items i WHERE run_id=?",
            (row["id"],),
        ).fetchall():
            counts[item["decision"] or "unreviewed"] += 1
        result = {
            key: row[key]
            for key in (
                "id",
                "project_id",
                "recording_id",
                "sequence",
                "result_id",
                "source_version_id",
                "source_payload_sha256",
                "codebook_version_id",
                "codebook_payload_sha256",
                "kind",
                "created",
            )
        }
        result.update(
            passage_ids=json.loads(row["passage_ids"]),
            source_stale=head is None or head["id"] != row["source_version_id"],
            codebook_stale=codebook_head is None
            or codebook_head["id"] != row["codebook_version_id"],
            item_count=sum(counts.values()),
            **{f"{key}_count": value for key, value in counts.items()},
        )
        if exact and row["kind"] == "imported_suggestions":
            result["producer"] = {"declared": json.loads(row["producer"]), "verified": False}
        return result

    @staticmethod
    def _receipt(db, table, scope_column, scope_id, request_id, digest):
        row = db.execute(
            f"SELECT * FROM {table} WHERE {scope_column}=? AND request_id=?", (scope_id, request_id)
        ).fetchone()
        if row is not None and row["request_sha256"] != digest:
            raise ValueError("This request identifier was already used with different values.")
        return row

    @staticmethod
    def _insert_annotation_run(
        db,
        recording_id,
        result_id,
        source_version_id,
        codebook_version_id,
        passage_ids,
        request_id,
        request_digest,
        *,
        kind="manual",
        producer=None,
        expected_hashes=None,
    ):
        if db.execute("SELECT 1 FROM result_trash WHERE result_id=?", (result_id,)).fetchone():
            raise ValueError("Restore this result from Trash before starting topic coding.")
        project_id, version, source, codebook = Store._annotation_context(
            db, recording_id, result_id, source_version_id, codebook_version_id
        )
        if expected_hashes and expected_hashes != (
            version["payload_sha256"],
            codebook["payload_sha256"],
        ):
            raise ValueError(
                "The imported source/codebook hashes do not match their frozen versions."
            )
        selected = annotations.selected_passages(source, passage_ids)
        sequence = db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM annotation_runs WHERE recording_id=?",
            (recording_id,),
        ).fetchone()[0]
        identifier = new_id()
        db.execute(
            "INSERT INTO annotation_runs(id,project_id,recording_id,sequence,result_id,"
            "source_version_id,"
            "source_payload_sha256,codebook_version_id,codebook_payload_sha256,kind,passage_ids,producer,"
            "created,request_id,request_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                project_id,
                recording_id,
                sequence,
                result_id,
                source_version_id,
                version["payload_sha256"],
                codebook_version_id,
                codebook["payload_sha256"],
                kind,
                json.dumps(selected),
                json.dumps(producer or {}, ensure_ascii=False),
                now(),
                request_id,
                request_digest,
            ),
        )
        return identifier, source, codebook["payload"], selected

    def create_annotation_run(
        self,
        recording_id,
        result_id,
        source_version_id,
        codebook_version_id,
        *,
        passage_ids=None,
        request_id,
    ):
        annotations.request_identity(request_id)
        digest = annotations.canonical(
            {
                "kind": "manual",
                "result_id": result_id,
                "source_version_id": source_version_id,
                "codebook_version_id": codebook_version_id,
                "passage_ids": passage_ids,
            }
        )[1]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._receipt(
                db, "annotation_runs", "recording_id", recording_id, request_id, digest
            )
            if previous:
                self._run(db, recording_id, previous["id"])
                return self._run_view(db, previous)
            identifier, _, _, _ = self._insert_annotation_run(
                db,
                recording_id,
                result_id,
                source_version_id,
                codebook_version_id,
                passage_ids,
                request_id,
                digest,
            )
            row = db.execute("SELECT * FROM annotation_runs WHERE id=?", (identifier,)).fetchone()
            return self._run_view(db, row)

    def import_annotation_run(self, recording_id, bundle, *, request_id):
        annotations.request_identity(request_id)
        bundle = annotations.import_bundle(bundle)
        digest = annotations.canonical({"kind": "imported_suggestions", "bundle": bundle})[1]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._receipt(
                db, "annotation_runs", "recording_id", recording_id, request_id, digest
            )
            if previous:
                self._run(db, recording_id, previous["id"])
                return self._run_view(db, previous)
            identifier, source, codebook, selected = self._insert_annotation_run(
                db,
                recording_id,
                bundle["result_id"],
                bundle["source_version_id"],
                bundle["codebook_version_id"],
                bundle.get("passage_ids"),
                request_id,
                digest,
                kind="imported_suggestions",
                producer=bundle.get("producer"),
                expected_hashes=(
                    bundle["source_payload_sha256"],
                    bundle["codebook_payload_sha256"],
                ),
            )
            for index, suggestion in enumerate(bundle["suggestions"]):
                try:
                    clean = annotations.annotation_payload(
                        suggestion["annotation"], source, codebook, selected
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Suggestion {index + 1} has invalid topic/evidence fields."
                    ) from exc
                encoded, item_digest = annotations.canonical(clean)
                db.execute(
                    "INSERT INTO annotation_items(id,run_id,sequence,origin,suggestion,"
                    "suggestion_sha256,"
                    "created,request_id,request_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        new_id(),
                        identifier,
                        index + 1,
                        "imported",
                        encoded,
                        item_digest,
                        now(),
                        suggestion["id"],
                        item_digest,
                    ),
                )
            row = db.execute("SELECT * FROM annotation_runs WHERE id=?", (identifier,)).fetchone()
            return self._run_view(db, row)

    def annotation_runs(self, recording_id, limit=50, before_sequence=None):
        self._history_page(limit, before_sequence)
        with self.connection() as db:
            db.execute("BEGIN")
            if not db.execute("SELECT id FROM recordings WHERE id=?", (recording_id,)).fetchone():
                raise KeyError(recording_id)
            rows = db.execute(
                "SELECT * FROM annotation_runs WHERE recording_id=? AND (? IS NULL OR sequence<?) "
                "ORDER BY sequence DESC LIMIT ?",
                (recording_id, before_sequence, before_sequence, limit),
            ).fetchall()
            return [self._run_view(db, row, exact=False) for row in rows]

    def annotation_run(self, recording_id, run_id):
        with self.connection() as db:
            db.execute("BEGIN")
            row, _, _, _ = self._run(db, recording_id, run_id)
            return self._run_view(db, row)

    @staticmethod
    def _annotation_item(db, run_id, item_id, source, codebook, passage_ids, *, exact=True):
        item = db.execute(
            "SELECT * FROM annotation_items WHERE id=? AND run_id=?", (item_id, run_id)
        ).fetchone()
        if item is None:
            raise KeyError(item_id)
        suggestion = None
        if item["suggestion"] is not None:
            suggestion = annotations.verified_document(
                item["suggestion"], item["suggestion_sha256"], annotations.ANNOTATION_FORMAT
            )
            annotations.annotation_payload(suggestion, source, codebook, passage_ids)
        head = db.execute(
            "SELECT * FROM annotation_review_events WHERE item_id=? ORDER BY revision DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        current = None
        if head is not None:
            current = annotations.verified_document(
                head["payload"], head["payload_sha256"], annotations.ANNOTATION_FORMAT
            )
            annotations.annotation_payload(current, source, codebook, passage_ids)
        payload = current or suggestion
        if payload is None:
            raise ValueError("A manual annotation is missing its initial review event.")
        result = {key: item[key] for key in ("id", "run_id", "sequence", "origin", "created")}
        result.update(
            revision=head["revision"] if head else 0,
            review_state=head["decision"] if head else "unreviewed",
            topic_id=payload["topic_id"],
            polarity=payload["polarity"],
            attribution=payload["attribution"],
            evidence_count=len(payload["evidence"]),
        )
        if exact:
            passages = annotations.source_passages(source)
            source_speakers = {}
            for span in payload["evidence"]:
                speaker = passages[span["passage_id"]].get("speaker")
                source_speakers[span["passage_id"]] = {
                    "speaker_id": speaker,
                    "speaker_name": source["speakers"].get(speaker, speaker),
                }
            result.update(
                suggestion=suggestion,
                current=current,
                source_speakers=source_speakers,
                current_review={
                    key: head[key]
                    for key in (
                        "id",
                        "revision",
                        "predecessor_id",
                        "payload_sha256",
                        "decision",
                        "action",
                        "actor_kind",
                        "reason",
                        "observed_at",
                    )
                }
                if head
                else None,
            )
        return result

    @staticmethod
    def _append_annotation_review(
        db, item_id, payload, decision, action, request_id, digest, reason
    ):
        head = db.execute(
            "SELECT id,revision FROM annotation_review_events WHERE item_id=? "
            "ORDER BY revision DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        encoded, payload_digest = annotations.canonical(payload)
        db.execute(
            "INSERT INTO annotation_review_events(id,item_id,revision,predecessor_id,payload,"
            "payload_sha256,decision,action,actor_kind,reason,observed_at,request_id,"
            "request_sha256) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(),
                item_id,
                head["revision"] + 1 if head else 1,
                head["id"] if head else None,
                encoded,
                payload_digest,
                decision,
                action,
                "local_user",
                reason,
                now(),
                request_id,
                digest,
            ),
        )

    def add_manual_annotation(self, recording_id, run_id, payload, *, request_id, reason=None):
        annotations.request_identity(request_id)
        edit_metadata("manual_save", None, reason)
        digest = annotations.canonical({"payload": payload, "reason": reason})[1]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            _, source, codebook, selected = self._run(db, recording_id, run_id)
            previous = self._receipt(db, "annotation_items", "run_id", run_id, request_id, digest)
            if previous:
                return self._annotation_item(db, run_id, previous["id"], source, codebook, selected)
            clean = annotations.annotation_payload(payload, source, codebook, selected)
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM annotation_items WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            if sequence > 1000:
                raise ValueError("A prototype run is limited to 1,000 annotations.")
            identifier = new_id()
            db.execute(
                "INSERT INTO annotation_items(id,run_id,sequence,origin,suggestion,"
                "suggestion_sha256,"
                "created,request_id,request_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                (identifier, run_id, sequence, "manual", None, None, now(), request_id, digest),
            )
            self._append_annotation_review(
                db, identifier, clean, "accepted", "manual_create", request_id, digest, reason
            )
            return self._annotation_item(db, run_id, identifier, source, codebook, selected)

    def review_annotation(
        self,
        recording_id,
        run_id,
        annotation_id,
        revision,
        action,
        *,
        payload=None,
        request_id,
        reason=None,
    ):
        validate_revision(revision)
        annotations.request_identity(request_id)
        edit_metadata("manual_save", None, reason)
        if not isinstance(action, str) or action not in {"accept", "change", "reject"}:
            raise ValueError("Choose accept, change or reject for the annotation review.")
        if action != "change" and payload is not None:
            raise ValueError("Only Change accepts a replacement annotation.")
        digest = annotations.canonical(
            {
                "revision": revision,
                "action": action,
                "payload": payload,
                "reason": reason,
            }
        )[1]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            _, source, codebook, selected = self._run(db, recording_id, run_id)
            current = self._annotation_item(db, run_id, annotation_id, source, codebook, selected)
            previous = self._receipt(
                db, "annotation_review_events", "item_id", annotation_id, request_id, digest
            )
            if previous:
                return current
            if current["revision"] != revision:
                raise ValueError(
                    "This annotation changed in another window. Reload before reviewing."
                )
            clean = annotations.annotation_payload(
                payload if action == "change" else current["current"] or current["suggestion"],
                source,
                codebook,
                selected,
            )
            self._append_annotation_review(
                db,
                annotation_id,
                clean,
                "rejected" if action == "reject" else "accepted",
                action,
                request_id,
                digest,
                reason,
            )
            return self._annotation_item(db, run_id, annotation_id, source, codebook, selected)

    def annotations(
        self,
        recording_id,
        run_id,
        *,
        topic_id=None,
        review_state=None,
        polarity=None,
        limit=100,
        before_sequence=None,
    ):
        self._history_page(limit, before_sequence)
        if review_state is not None and (
            not isinstance(review_state, str)
            or review_state not in {"unreviewed", "accepted", "rejected"}
        ):
            raise ValueError("Choose a supported annotation review state.")
        if polarity is not None and (
            not isinstance(polarity, str) or polarity not in annotations.POLARITIES
        ):
            raise ValueError("Choose a supported topic evaluation.")
        with self.connection() as db:
            db.execute("BEGIN")
            _, source, codebook, selected = self._run(db, recording_id, run_id)
            if topic_id is not None and topic_id not in {t["id"] for t in codebook["topics"]}:
                raise ValueError("Choose a topic from the frozen codebook.")
            rows = db.execute(
                "SELECT id FROM annotation_items WHERE run_id=? AND (? IS NULL OR sequence<?) "
                "ORDER BY sequence DESC",
                (run_id, before_sequence, before_sequence),
            ).fetchall()
            result = []
            for row in rows:
                item = self._annotation_item(
                    db, run_id, row["id"], source, codebook, selected, exact=False
                )
                if (
                    (topic_id is None or topic_id == item["topic_id"])
                    and (review_state is None or review_state == item["review_state"])
                    and (polarity is None or polarity == item["polarity"])
                ):
                    result.append(item)
                    if len(result) == limit:
                        break
            return result

    def annotation_item(self, recording_id, run_id, item_id):
        with self.connection() as db:
            db.execute("BEGIN")
            _, source, codebook, selected = self._run(db, recording_id, run_id)
            return self._annotation_item(db, run_id, item_id, source, codebook, selected)

    def annotation_review_events(
        self, recording_id, run_id, item_id, limit=50, before_revision=None
    ):
        self._history_page(limit, before_revision)
        with self.connection() as db:
            db.execute("BEGIN")
            _, source, codebook, selected = self._run(db, recording_id, run_id)
            self._annotation_item(db, run_id, item_id, source, codebook, selected, exact=False)
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,item_id,revision,predecessor_id,payload_sha256,decision,action,"
                    "actor_kind,observed_at FROM annotation_review_events WHERE item_id=? "
                    "AND (? IS NULL OR revision<?) "
                    "ORDER BY revision DESC LIMIT ?",
                    (item_id, before_revision, before_revision, limit),
                ).fetchall()
            ]

    def annotation_review_event(self, recording_id, run_id, item_id, event_id):
        with self.connection() as db:
            db.execute("BEGIN")
            _, source, codebook, selected = self._run(db, recording_id, run_id)
            self._annotation_item(db, run_id, item_id, source, codebook, selected, exact=False)
            row = db.execute(
                "SELECT * FROM annotation_review_events WHERE id=? AND item_id=?",
                (event_id, item_id),
            ).fetchone()
            if row is None:
                raise KeyError(event_id)
            payload = annotations.verified_document(
                row["payload"], row["payload_sha256"], annotations.ANNOTATION_FORMAT
            )
            annotations.annotation_payload(payload, source, codebook, selected)
            return {
                **{
                    key: row[key]
                    for key in row.keys()
                    if key not in {"payload", "request_id", "request_sha256"}
                },
                "payload": payload,
            }

    def export_annotation_run(self, recording_id, run_id):
        with self.connection() as db:
            db.execute("BEGIN")
            row, source, codebook, selected = self._run(db, recording_id, run_id)
            run = self._run_view(db, row, exact=False)
            items = []
            for item in db.execute(
                "SELECT id FROM annotation_items WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall():
                view = self._annotation_item(db, run_id, item["id"], source, codebook, selected)
                item_export = {
                    key: view[key]
                    for key in ("id", "sequence", "origin", "revision", "review_state")
                }
                if view["current"] is not None:
                    item_export["annotation"] = view["current"]
                    item_export["review_event_id"] = view["current_review"]["id"]
                    item_export["payload_sha256"] = view["current_review"]["payload_sha256"]
                items.append(item_export)
            return {
                "format": annotations.EXPORT_FORMAT,
                "prototype": True,
                "scope": "Current reviewed evidence and codebook definitions only; "
                "uncoded does not mean absent.",
                "producer_status": "Declared imported producers are unverified; "
                "no model was executed by this prototype.",
                "excludes": [
                    "unreviewed_proposals",
                    "previous_reviews",
                    "transcript_originals",
                    "notes",
                    "review_reasons",
                    "settings",
                    "producer_declarations",
                ],
                "run": run,
                "codebook": codebook,
                "items": items,
            }

    def directory(self, identifier, project_id=None):
        # IDs are generated internally; never resolve arbitrary client filesystem paths.
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise KeyError("Unknown recording.")
        if project_id is None:
            rows = self.all("SELECT project_id FROM recordings WHERE id=?", (identifier,))
            project_id = rows[0]["project_id"] if rows else None
        base = self.project_folder(project_id) if project_id else self.root
        directory = base / "recordings" / identifier
        # Sibling member threads may create base/recordings while this path is resolved.
        if not containment_path(directory).is_relative_to(containment_path(base)):
            raise ValueError("The recording folder must stay inside its project storage.")
        return directory

    def add_job(
        self, recording_id, kind, params=None, *, language_guard=None, capture_timing_role=False
    ):
        identifier = new_id()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            frozen = params or {}
            if capture_timing_role:
                from . import local_timing, timing_working

                current = db.execute(
                    "SELECT * FROM recordings WHERE id=?", (recording_id,)
                ).fetchone()
                source = frozen.get("source_snapshot")
                if (
                    kind != "enhance"
                    or frozen.get("realign") is not True
                    or current is None
                    or not isinstance(source, dict)
                    or current["active_transcript_id"] != source.get("id")
                    or local_timing._digest(
                        self._freeze_result(db, recording_id, source["id"], current["revision"])
                    )
                    != local_timing._digest(source)
                ):
                    raise ValueError("The timing source changed before it could be queued.")
                frozen = timing_working.freeze(self, db, recording_id, frozen)
            self._insert_job(
                db,
                recording_id,
                kind,
                frozen,
                identifier,
                language_guard=language_guard,
                timing_role_verified=capture_timing_role,
            )
        return self.job(identifier)

    def _insert_job(
        self,
        db,
        recording_id,
        kind,
        params,
        identifier,
        *,
        language_guard=None,
        timing_role_verified=False,
        working_role_verified=False,
        legacy_working_fields=False,
    ):
        from . import restored_owners

        restored_owners.require_owned(db, "job", identifier)
        for key, owner_kind in (
            ("native_batch_id", "native_batch"),
            ("runtime_batch_id", "runtime_batch"),
        ):
            if params.get(key):
                restored_owners.require_owned(db, owner_kind, params[key])
        from . import runtime_store

        if kind == "source-inspect":
            raise ValueError("Source inspection jobs require the internal inspection lifecycle.")
        if "input_intent" in params or "input_acceptance" in params:
            raise ValueError("Input preparation requires its durable admission lifecycle.")
        if timing_role_verified and (working_role_verified or legacy_working_fields):
            raise ValueError("A job cannot have conflicting Working authorities.")
        if working_role_verified and legacy_working_fields:
            raise ValueError("A job cannot have conflicting Working authorities.")
        if "timing_working_policy" in params and not (
            timing_role_verified or legacy_working_fields
        ):
            raise ValueError("Timing Working evidence requires the internal acceptance lifecycle.")
        if ({"working_role", "working_role_policy"} & params.keys()) and not (
            working_role_verified or legacy_working_fields
        ):
            raise ValueError("Working evidence requires the internal acceptance lifecycle.")
        if working_role_verified:
            from . import timing_working

            if (
                params.get("working_role_policy") != timing_working.ROLE_POLICY
                or "working_role" not in params
            ):
                raise ValueError("The frozen Working evidence is incomplete.")
        params = json.loads(json.dumps(params, ensure_ascii=False, allow_nan=False))
        from . import native_allocation

        if native_allocation.validate(params) is not None and (
            kind != "transcribe" or params.get("settings", {}).get("provider_type") != "ucloud"
        ):
            raise ValueError("A frozen native allocation belongs to native transcription only.")
        recording = db.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if recording is None:
            raise KeyError(recording_id)
        if language_guard is not None:
            from . import language_preferences

            language_preferences.require_current(db, recording, language_guard)
            preference = language_preferences.selected(language_guard)
            if (
                kind != "enhance"
                or params.get("language_preference") != preference
                or preference is not None
                and (
                    type(params.get("enhancement")) is not dict
                    or params["enhancement"].get("language") != preference["language"]
                )
            ):
                raise ValueError("The frozen processing language disagrees with the recording.")
        runtime_store.attach(db, recording, identifier, kind, params)
        db.execute(
            "INSERT INTO jobs(id,recording_id,kind,params,created,updated) VALUES (?,?,?,?,?,?)",
            (identifier, recording_id, kind, json.dumps(params or {}), now(), now()),
        )
        if timing_role_verified or working_role_verified:
            from . import result_roles

            policy = "timing" if timing_role_verified else "result"
            expected = (
                params["timing_working_policy"]["expected_role"]
                if timing_role_verified
                else params["working_role"]
            )
            result_roles.authorize_job(
                db, self, identifier, recording_id, policy, expected, params.get("source_snapshot")
            )

    def enqueue_enhancement(
        self,
        recording_id,
        result_id,
        revision,
        params=None,
        *,
        selected_ids=None,
        language_guard=None,
    ):
        """Freeze reviewed lineage and attach its job under the same write transaction."""
        identifier = new_id()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            source = self._freeze_result(db, recording_id, result_id, revision)
            if selected_ids is not None:
                if not isinstance(params, dict) or not isinstance(params.get("enhancement"), dict):
                    raise ValueError("Selected snapshots require explicit passage timing options.")
                options = params["enhancement"]
                if (
                    params.get("realign") is not True
                    or params.get("passage_timing") is not True
                    or any(
                        options.get(key) is not False
                        for key in ("diarize", "transcribe", "retranscribe")
                    )
                    or options.get("align") is not True
                    or params.get("transcription")
                    or params.get("retranscription")
                    or params.get("runtime_batch_id") is not None
                    or params.get("native_batch_id") is not None
                    or not isinstance(params.get("ucloud_api", {}), dict)
                    or params.get("ucloud_api", {}).get("batch_plan") is not None
                ):
                    raise ValueError("Selected snapshots are limited to singleton passage timing.")
                if (
                    not isinstance(selected_ids, list)
                    or not 0 < len(selected_ids) <= 1000
                    or any(not isinstance(i, str) or not 0 < len(i) <= 100 for i in selected_ids)
                    or len(set(selected_ids)) != len(selected_ids)
                ):
                    raise ValueError("Choose each selected timing passage exactly once.")
                selected = [s for s in source["segments"] if s["id"] in set(selected_ids)]
                if [s["id"] for s in selected] != selected_ids:
                    raise ValueError("The selected passages changed or are out of saved order.")
                requested = params.get("segment_ids") or [params.get("segment_id")]
                if requested != selected_ids:
                    raise ValueError("The timing selector disagrees with its selected snapshot.")
                source = {**source, "segments": selected}
            from . import runtime_capture, timing_capture

            frozen_params = timing_capture.freeze({**(params or {}), "source_snapshot": source})
            frozen_params = runtime_capture.freeze(frozen_params)
            if {"working_role", "working_role_policy"} & frozen_params.keys():
                raise ValueError("Working evidence is created only when work is accepted.")
            if frozen_params.get("realign") is True:
                from . import timing_working

                frozen_params = timing_working.freeze(self, db, recording_id, frozen_params)
            else:
                from . import result_roles, timing_working

                frozen_params["working_role"] = result_roles.read(db, self, recording_id)
                frozen_params["working_role_policy"] = timing_working.ROLE_POLICY
            self._insert_job(
                db,
                recording_id,
                "enhance",
                frozen_params,
                identifier,
                language_guard=language_guard,
                timing_role_verified=frozen_params.get("realign") is True,
                working_role_verified=frozen_params.get("realign") is not True,
            )
        return self.job(identifier)

    def add_transcription_jobs(self, items):
        """Freeze one mixed API/native/runtime request in one transaction."""
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise ValueError("Select between one and 100 recording/provider combinations.")
        identifiers = []
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            jobs = []
            for item in items:
                recording = db.execute(
                    "SELECT id,project_id,filename FROM recordings WHERE id=?",
                    (item["recording_id"],),
                ).fetchone()
                if recording is None:
                    raise KeyError(item["recording_id"])
                # Round-trip also detaches the caller's mutable dictionaries.
                params = json.loads(json.dumps(item["params"]))
                identifier = new_id()
                identifiers.append(identifier)
                kind = item.get("kind", "transcribe")
                if kind not in {"transcribe", "enhance"}:
                    raise ValueError(
                        "A transcription request can enqueue transcription/enhancement only."
                    )
                jobs.append((identifier, recording["id"], kind, params))
            self._group_native_jobs(db, jobs)
            for identifier, recording_id, kind, params in jobs:
                self._insert_job(db, recording_id, kind, params, identifier)
        return [self.job(identifier) for identifier in identifiers]

    @staticmethod
    def _group_native_jobs(db, jobs):
        """Share exact native grouping between fresh insertion and same-ID materialization."""
        groups = {}
        from . import native_allocation

        for identifier, recording_id, kind, params in jobs:
            settings = params.get("settings", {})
            if kind != "transcribe" or settings.get("provider_type") != "ucloud":
                continue
            recording = db.execute(
                "SELECT id,project_id,filename FROM recordings WHERE id=?", (recording_id,)
            ).fetchone()
            if recording is None:
                raise KeyError(recording_id)
            key = (
                recording["project_id"],
                params.get("provider_id"),
                json.dumps(settings, sort_keys=True, separators=(",", ":")),
                json.dumps(
                    native_allocation.validate(params), sort_keys=True, separators=(",", ":")
                ),
            )
            groups.setdefault(key, []).append((identifier, dict(recording), params))
        for (project_id, _, _, _), members in groups.items():
            if len(members) < 2:
                continue
            if len({r["id"] for _, r, _ in members}) != len(members):
                raise ValueError("A native batch cannot contain the same recording twice.")
            batch_id, source, manifest = new_id(), members[0][2], []
            from . import restored_owners

            restored_owners.require_owned(db, "native_batch", batch_id)
            for identifier, recording, params in members:
                suffix = Path(recording["filename"]).suffix.lower()
                if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
                    raise ValueError("The recording needs a supported filename extension.")
                params["native_batch_id"] = batch_id
                manifest.append(
                    {
                        "job_id": identifier,
                        "recording_id": recording["id"],
                        "filename": identifier + suffix,
                    }
                )
            db.execute(
                "INSERT INTO native_batch_runs(id,project_id,params,members,created,updated) "
                "VALUES (?,?,?,?,?,?)",
                (
                    batch_id,
                    project_id,
                    json.dumps(
                        {
                            "settings": source["settings"],
                            "provider_id": source.get("provider_id"),
                            "provider_name": source["provider_name"],
                            "ucloud_run": {},
                            **(
                                {"native_allocation": source["native_allocation"]}
                                if "native_allocation" in source
                                else {}
                            ),
                        }
                    ),
                    json.dumps(manifest),
                    now(),
                    now(),
                ),
            )

    @staticmethod
    def _native_batch(row):
        if row is None:
            raise KeyError("Unknown UCloud batch.")
        result = dict(row)
        result["params"] = json.loads(result["params"])
        result["members"] = json.loads(result["members"])
        return result

    def native_batch(self, identifier):
        with self.connection() as db:
            return self._native_batch(
                db.execute("SELECT * FROM native_batch_runs WHERE id=?", (identifier,)).fetchone()
            )

    def update_native_batch(self, identifier, *, run_update=None, **fields):
        """Merge journal receipts instead of replacing a concurrent cancellation receipt."""
        from . import restored_owners

        restored_owners.require_owner(self, "native_batch", identifier)
        if fields.keys() - {"state", "message", "progress"}:
            raise ValueError("Unknown UCloud batch field.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            batch = self._native_batch(
                db.execute("SELECT * FROM native_batch_runs WHERE id=?", (identifier,)).fetchone()
            )
            if run_update is not None:
                batch["params"]["ucloud_run"].update(run_update)
                fields["params"] = json.dumps(batch["params"])
            fields["updated"] = now()
            db.execute(
                "UPDATE native_batch_runs SET "
                + ",".join(f"{key}=?" for key in fields)
                + " WHERE id=?",
                (*fields.values(), identifier),
            )
        return self.native_batch(identifier)

    @staticmethod
    def _claim_native_batch(db, batch_id):
        from . import native_batch, restored_owners

        restored_owners.require_owned(db, "native_batch", batch_id)
        batch = Store._native_batch(
            db.execute("SELECT * FROM native_batch_runs WHERE id=?", (batch_id,)).fetchone()
        )
        if batch["state"] != "queued":
            return False
        db.execute(
            "UPDATE native_batch_runs SET state='running',message='Starting UCloud batch…', "
            "updated=? WHERE id=?",
            (now(), batch_id),
        )
        deleted = native_batch._deleted(db, batch)
        for member in batch["members"]:
            if member["job_id"] in deleted:
                continue
            updated = db.execute(
                "UPDATE jobs SET state='running',message='Starting UCloud batch…',updated=? "
                "WHERE id=? AND state='queued'",
                (now(), member["job_id"]),
            ).rowcount
            if not updated:
                state = db.execute(
                    "SELECT state FROM jobs WHERE id=?", (member["job_id"],)
                ).fetchone()
                if state is None or state[0] != "completed":
                    error = ValueError("The UCloud batch member state changed. Retry the batch.")
                    error.batch_id = batch_id
                    raise error
        return True

    def claim_native_batch(self, identifier):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._claim_native_batch(db, identifier)

    def claim_job(self, kind):
        """Claim all members of a native run in the same transaction as its coordinator."""
        try:
            return self._claim_job(kind)
        except ValueError as exc:
            batch_id = getattr(exc, "batch_id", None)
            if not batch_id:
                raise
            self.finish_native_batch(batch_id, "failed", str(exc))
            return None

    def _claim_job(self, kind):
        from . import restored_owners

        if kind == "source-inspect":
            raise ValueError("Source inspection jobs require the internal inspection lifecycle.")
        identifier = None
        lane = ""
        if kind in {"enhance", "enhance-remote"}:
            # Frozen UCloud enhancements (API or legacy SSH) run in their own worker lane.
            remote = (
                "COALESCE(json_extract("
                "CASE WHEN json_valid(j.params) THEN j.params ELSE 'null' END,"
                "'$.enhancement.execution'),'')='ucloud'"
            )
            lane = f" AND {remote}" if kind == "enhance-remote" else f" AND NOT {remote}"
            kind = "enhance"
        elif kind in {"transcribe-api", "transcribe-native"}:
            # Native UCloud Transcriber keeps its own fixed lane, so more API workers never
            # mean more concurrent UCloud allocations (ADR 0146). "transcribe" claims both.
            native = (
                "COALESCE(json_extract("
                "CASE WHEN json_valid(j.params) THEN j.params ELSE 'null' END,"
                "'$.settings.provider_type'),'')='ucloud'"
            )
            lane = f" AND {native}" if kind == "transcribe-native" else f" AND NOT {native}"
            kind = "transcribe"
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT j.id,j.params FROM jobs j WHERE j.state='queued' AND j.kind=? AND "
                "json_type(CASE WHEN json_valid(j.params) "
                "THEN j.params ELSE 'null' END)='object' AND "
                "CASE WHEN json_valid(j.params) THEN json_type(j.params,'$.input_intent') "
                "ELSE 'invalid' END IS NULL AND "
                "json_extract(CASE WHEN json_valid(j.params) THEN j.params ELSE 'null' END,"
                "'$.runtime_batch_id') IS NULL AND NOT EXISTS "
                "(SELECT 1 FROM runtime_batch_members rm WHERE rm.job_id=j.id) AND "
                "(json_extract(CASE WHEN json_valid(j.params) THEN j.params ELSE 'null' END,"
                "'$.native_batch_id') IS NULL OR EXISTS "
                "(SELECT 1 FROM native_batch_runs b WHERE "
                "b.id=json_extract(CASE WHEN json_valid(j.params) "
                "THEN j.params ELSE 'null' END,'$.native_batch_id') AND b.state='queued'))"
                + lane
                + " AND "
                + restored_owners.sql_exclusion(db, "job", "j.id")
                + " AND (json_extract(CASE WHEN json_valid(j.params) THEN j.params "
                + "ELSE 'null' END,'$.native_batch_id') IS NULL OR "
                + "NOT EXISTS (SELECT 1 FROM native_batch_runs restored_native WHERE "
                + "restored_native.id=json_extract(CASE WHEN json_valid(j.params) "
                + "THEN j.params ELSE 'null' END,'$.native_batch_id') AND NOT "
                + restored_owners.sql_exclusion(db, "native_batch", "restored_native.id")
                + "))"
                + " ORDER BY j.created,j.id LIMIT 1",
                (kind,),
            ).fetchone()
            if row is not None:
                identifier = row["id"]
                batch_id = json.loads(row["params"]).get("native_batch_id")
                if batch_id:
                    self._claim_native_batch(db, batch_id)
                else:
                    db.execute(
                        "UPDATE jobs SET state='running',message='Starting…',updated=? WHERE id=?",
                        (now(), identifier),
                    )
        return self.job(identifier) if identifier else None

    def finish_native_batch(self, identifier, state, message):
        from . import restored_owners

        restored_owners.require_owner(self, "native_batch", identifier)
        if state not in {"completed", "failed", "cancelled", "interrupted", "queued"}:
            raise ValueError("Unknown UCloud batch state.")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            batch = self._native_batch(
                db.execute("SELECT * FROM native_batch_runs WHERE id=?", (identifier,)).fetchone()
            )
            for member in batch["members"]:
                # Successful imports and their follow-ups are never reverted by a sibling failure.
                db.execute(
                    "UPDATE jobs SET state=?,message=?,updated=? WHERE id=? AND state!='completed'",
                    (state, message, now(), member["job_id"]),
                )
            db.execute(
                "UPDATE native_batch_runs SET state=?,message=?,progress=?,updated=? WHERE id=?",
                (
                    state,
                    message,
                    1 if state == "completed" else batch["progress"],
                    now(),
                    identifier,
                ),
            )

    def job(self, identifier):
        row = self.one("SELECT * FROM jobs WHERE id=?", (identifier,))
        row["params"] = json.loads(row["params"])
        row["chunks"] = json.loads(row["chunks"])
        return row

    def request_job_cancellation(self, identifier):
        """Close an idle claim before external Stop; never downgrade a committed result."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            from . import restored_owners

            restored_owners.require_owned(db, "job", identifier)
            row = db.execute("SELECT kind,params FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            params = json.loads(row["params"])
            if (
                row["kind"] == "source-inspect"
                or not isinstance(params, dict)
                or "input_intent" in params
            ):
                raise ValueError("Preparation cancellation requires its fenced lifecycle.")
            db.execute(
                "UPDATE jobs SET state=CASE WHEN state='running' THEN state ELSE 'cancelled' END,"
                "message=CASE WHEN state='running' THEN ? ELSE ? END,updated=? "
                "WHERE id=? AND state!='completed'",
                ("Cancelling; waiting for active work to stop.", "Cancelled.", now(), identifier),
            )
        return self.job(identifier)

    def mark_job_termination_requested(self, identifier, remote_id):
        """Merge only an acknowledged Stop for the exact legacy allocation, not stale params."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            from . import restored_owners

            restored_owners.require_owned(db, "job", identifier)
            row = db.execute("SELECT kind,params FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            params = json.loads(row["params"])
            if (
                row["kind"] == "source-inspect"
                or not isinstance(params, dict)
                or "input_intent" in params
                or params.get("ucloud_run", {}).get("job_id") != remote_id
            ):
                raise ValueError("The owned allocation changed during cancellation.")
            params["ucloud_run"]["termination_requested"] = True
            db.execute(
                "UPDATE jobs SET params=?,updated=? WHERE id=?",
                (json.dumps(params), now(), identifier),
            )

    def update_job(self, identifier, **fields):
        from . import restored_owners

        restored_owners.require_job(self, identifier)
        allowed = {"state", "progress", "message", "params", "chunks"}
        if fields.keys() - allowed:
            raise ValueError("Unknown job field.")
        fields["updated"] = now()
        fields = {k: json.dumps(v) if k in {"chunks", "params"} else v for k, v in fields.items()}
        safe = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT kind,CASE WHEN json_type({safe})='object' "
                f"THEN json_type({safe},'$.input_intent') ELSE 'invalid' END AS intent "
                "FROM jobs WHERE id=?",
                (identifier,),
            ).fetchone()
            if row is not None and row["kind"] == "source-inspect":
                raise ValueError(
                    "Source inspection jobs require the internal inspection lifecycle."
                )
            if (
                row is not None
                and row["intent"] is not None
                or ("params" in fields and "input_intent" in json.loads(fields["params"]))
            ):
                raise ValueError("Input preparation requires its durable admission lifecycle.")
            if "params" in fields:
                proposed = json.loads(fields["params"])
                if not isinstance(proposed, dict):
                    raise ValueError("Job parameters must be a JSON object.")
                from . import local_asr_capture

                previous = db.execute(
                    "SELECT params,recording_id FROM jobs WHERE id=?", (identifier,)
                ).fetchone()
                if previous is not None:
                    local_asr_capture.assert_params_preserved(json.loads(previous[0]), proposed)
                    from . import provider_capture

                    provider_capture.assert_params_preserved(json.loads(previous[0]), proposed)
                    from . import native_capture

                    native_capture.assert_params_preserved(json.loads(previous[0]), proposed)
                    from . import local_timing

                    receipt = db.execute(
                        "SELECT policy FROM working_job_authorities WHERE job_id=?", (identifier,)
                    ).fetchone()
                    authority, policy = None, None
                    if receipt is not None:
                        from . import result_roles

                        policy = receipt["policy"]
                        authority = result_roles.job_authority(
                            db, identifier, previous["recording_id"], policy
                        )
                    local_timing.assert_params_preserved(
                        json.loads(previous[0]), proposed, authority=authority, policy=policy
                    )
                safe = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
                receipt = db.execute(
                    f"SELECT json_type({safe},'$.input_acceptance') AS kind,"
                    f"length(CAST(json_extract({safe},'$.input_acceptance') AS BLOB)) AS size "
                    "FROM jobs WHERE id=?",
                    (identifier,),
                ).fetchone()
                if receipt is not None and receipt["kind"] is not None:
                    if receipt["kind"] != "object" or receipt["size"] > 65536:
                        raise ValueError("The frozen input acceptance receipt is invalid.")
                    original = db.execute(
                        f"SELECT json_extract({safe},'$.input_acceptance') FROM jobs WHERE id=?",
                        (identifier,),
                    ).fetchone()[0]
                    if json.dumps(
                        json.loads(original), sort_keys=True, allow_nan=False
                    ) != json.dumps(
                        proposed.get("input_acceptance"), sort_keys=True, allow_nan=False
                    ):
                        raise ValueError("The frozen input acceptance receipt cannot be changed.")
                elif "input_acceptance" in proposed:
                    raise ValueError("Input acceptance is created only by durable materialization.")
            db.execute(
                "UPDATE jobs SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                (*fields.values(), identifier),
            )

    def record_audio_preparation(self, identifier, evidence):
        """Merge local input evidence without replacing a concurrent remote journal receipt."""
        from . import restored_owners

        restored_owners.require_job(self, identifier)
        from .audio_inputs import validate_evidence

        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT params FROM jobs WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise KeyError(identifier)
            params = json.loads(row["params"])
            receipt = validate_evidence(evidence, params.get("audio_input"))
            receipts = params.get("audio_preparations", [])
            if not isinstance(receipts, list) or len(receipts) > 200:
                raise ValueError("The frozen audio preparation evidence is invalid.")
            receipts = [validate_evidence(item, params["audio_input"]) for item in receipts]
            if receipt in receipts:
                return
            if ("transport" in receipt) != ("audio_transport" in params) or (
                "transport" in receipt and receipt["transport"] != params["audio_transport"]
            ):
                raise ValueError("The audio preparation differs from this job's frozen transport.")
            slot = receipt.get("window")
            if any(item.get("window") == slot for item in receipts):
                raise ValueError(
                    "This job already has different prepared audio for this scope. "
                    "Preserve its checkpoints and start a new job."
                )
            if len(receipts) == 200:
                raise ValueError("Too many audio preparation windows for one job.")
            # One passage-timing request can retain several separately frozen windows.
            params["audio_preparations"] = [*receipts, receipt]
            db.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), identifier))

    def recover_jobs(self):
        from . import runtime_store

        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._recover_jobs(db)
            runtime_store.recover(db)

    @staticmethod
    def _recover_jobs(db):
        from . import restored_owners

        db.execute(
            "UPDATE jobs SET state='interrupted',message=?,updated=? WHERE state='running' "
            "AND json_type(CASE WHEN json_valid(params) THEN params ELSE 'null' END)='object' "
            "AND kind!='source-inspect' AND CASE WHEN json_valid(params) "
            "THEN json_type(params,'$.input_intent') ELSE 'invalid' END IS NULL AND "
            + restored_owners.sql_exclusion(db, "job", "jobs.id"),
            (
                "The app stopped during this job. Retry explicitly to continue completed chunks.",
                now(),
            ),
        )
        db.execute(
            "UPDATE native_batch_runs SET state='interrupted',message=?,updated=? "
            "WHERE state='running' AND "
            + restored_owners.sql_exclusion(db, "native_batch", "native_batch_runs.id"),
            ("The app stopped. Retry explicitly to reconnect to the same UCloud batch.", now()),
        )
