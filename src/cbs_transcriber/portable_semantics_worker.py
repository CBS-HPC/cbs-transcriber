"""Captured stdlib-only schema-13–19 research checks, only in a disposable interpreter.

The pinned schema-12 opener supplies isolation, schema-first hardening and row accounting.
Its source is captured and hash checked before the narrow catalog/semantic adapters below
are installed. No Store, current history module, credentials or inference code is imported.
"""

import base64
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import sys
import types
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

PROTOCOL = "cbs-portable-semantics-v1"
POLICY = "cbs-portable-research-consistency-v1"
BASE_SHA256 = "d58bbdec746fa3e6967a05093c2fb77f62a3dd75d8bf0b59935c9631f4d7dfee"
CODEC_SHA256 = "e7e0200ca86757590f1ce95cb31c86657062bdb6a946fd6899bed31877efb862"
PAYLOAD_SHA256 = "7fc37c490bcf1fe0268299f383b9a4951cf1ae351631fd6c5b17ce4cd5465698"
ARTIFACTS_SHA256 = "8bccd9ecb0b4b971af7d20c1b33b349c09ff1729e43f7d99ef7b98e40fe810eb"
CAPTURE_SHA256 = "e383f827430b3af7d19d91561dc675c04b25a87c709573cb9281cd2b4e02d9fe"
INVENTORY_SHA256 = "fe60f40479739e10ef232d59d81bc897e497d30314eeaccc70f574f392516b73"
TRANSCRIPTS_SHA256 = "dcfb95696694c42667d8d1f68618866cb8931a346044e2f572d062297d85845b"
TIMING_SHA256 = "928f6c22a74d5a1287ddab381ef7b1b874a1df69053bb2f5de21d1e05baa0d2c"
RUNTIME_SHA256 = "3ffdee5c209f5ea8188cd2a23a1793334c255f359ded8f96a9ec270f422b16e8"
NATIVE_SHA256 = "480368001624167d89fc8d1577430305e4a4b89b5e5db911ee956885da8d89e6"
PROVIDER_SHA256 = "c67c2469f32db5e2187d43945c02b29a3d1e80940ffaee1141fcaac1b85915e9"
CATALOGS = {
    19: (
        "ae97a3870b69ad21d64ab00e438f2341b031e51f57d2c21b9028af33ca43ed34",
        "76d5d16850e3b262569a600a57a3f2ddbe3315565270d78feee4b5cb97ee63a7",
    ),
    18: (
        "16f38667b49fa43f1912c65e44f77b55bc3b7a9dff5ee056b35e6abd4c716265",
        "d382278a375fd6f72cd14cd8ebfeabf031ba42193a80bca4fe32cf5028b566bc",
    ),
    17: (
        "02ae3a3889767924b0fec5baed8e06298b081c020f34f365a0e28193f303b124",
        "9bc459e4855437b19d7a077cf062ab21d3cdb261b22489d4db324c34919695f7",
    ),
    16: (
        "d406ac0585c53c80c06f0ce527209828ec99217d792c8f7cff0fe9f0b590b1f1",
        "40ea25b0b78cae9d1fad9b071bbeca8d0c5a5d4c88a5ef7a7ae0c935cd54b4bc",
    ),
    13: (
        "b3e3867967d8066ee9ba20f0fea5fb3c68c2ab74e2cf3a0d1c7db2ec246f68a7",
        "2b69fbab37a1fe859ae2befa450b268deecbb22d000dcd8c01aa228bf8db3f27",
    ),
    14: (
        "2d2138ac5d21cc65de2ff2a2700adf33395b10f98db7f5fff515135618e84ea2",
        "11a29b05944ca9db94415b80305b065ad67d35b5595d94aea400c6828d72b2d8",
    ),
    15: (
        "5c16cd5812b57d27dc517dfcaf8f374100a268db94b6d6405cc207f7628850f6",
        "2eab2adde15d71b1c0d48b277fadc79132d879f97c08ce3f8b0bc965f6a510c6",
    ),
}
REQUEST_BYTES = 20 * 1024**2
SEMANTIC_BYTES = 256 * 1024**2
MAPPING_LIMIT = 20_000
OWNER_LIMIT = 200_000
FIELDS = {
    "protocol",
    "path",
    "expected_sha256",
    "expected_size",
    "manifest",
    "deadline_ns",
    "source_witness",
    "base_source_b64",
    "codec_source_b64",
    "payload_source_b64",
    "catalog_b64",
    "artifacts_source_b64",
    "capture_source_b64",
    "restore",
    "target_catalog_b64",
    "inventory_source_b64",
    "preservation_records",
    "transcripts_source_b64",
    "timing_source_b64",
    "objects_root",
    "runtime_source_b64",
    "provider_source_b64",
    "native_source_b64",
}


class Refused(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def fail(code="semantics"):
    raise Refused(code)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            fail("protocol")
        value[key] = item
    return value


def bad_number(_):
    fail("protocol")


def source(value, maximum, pin):
    if type(value) is not str or len(value) > ((maximum + 2) // 3) * 4:
        fail("input")
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        fail("input")
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != pin:
        fail("input")
    return raw


def module(name, raw):
    value = types.ModuleType(name)
    exec(compile(raw, "<captured-" + name + ">", "exec"), value.__dict__)
    return value


def row_codec(request, raw_catalog, version):
    codec = module("portable_rows", source(request["codec_source_b64"], 16 * 1024, CODEC_SHA256))

    class Resource:
        def joinpath(self, name):
            if name != "catalogs/portable_schema_12.json":
                fail("input")
            return self

        def open(self, mode):
            if mode != "rb":
                fail("input")
            return io.BytesIO(raw_catalog)

    codec.files = lambda name: Resource() if name == "cbs_transcriber" else None
    codec.CATALOG_SHA256, codec.SCHEMA_FINGERPRINT = CATALOGS[version]
    codec._catalog()
    return codec


def bounded_import_parser(transcripts):
    normalize, words = transcripts.normalize_segments, transcripts._word_data

    def limited_words(value):
        if type(value) is str:
            depth, quoted, escaped, tokens = 0, None, False, 0
            for char in value:
                if quoted is not None:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quoted:
                        quoted = None
                elif char in "\"'":
                    quoted, tokens = char, tokens + 1
                elif char in "[{(":
                    depth, tokens = depth + 1, tokens + 1
                    if depth > 24:
                        fail("resource")
                elif char in "]})":
                    depth -= 1
                elif char in ",:":
                    tokens += 1
                if tokens > 50_000:
                    fail("resource")
        result = words(value)
        if len(result) > 25_000:
            fail("resource")
        return result

    def limited_normalize(rows, top_words=None):
        if type(rows) is not list or len(rows) > 10_000:
            fail("resource")
        top_words = limited_words(top_words)
        prepared, total, indexed_words = [], 0, None
        for row in rows:
            if type(row) is not dict:
                fail()
            current = limited_words(row.get("words"))
            total += len(current)
            start, end = transcripts._interval(row)
            if not current and top_words and start is not None and end is not None:
                if indexed_words is None:
                    indexed_words = transcripts._index_words(top_words)
                total += len(transcripts._interval_words(indexed_words, start, end))
            if total > 25_000:
                fail("resource")
            prepared.append({**row, "words": current})
        return normalize(prepared, top_words)

    transcripts.normalize_segments = limited_normalize


def integer(value, *, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        fail()


def identifier(value):
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        fail()


def _parent(path):
    if not path.is_absolute() or ".." in path.parts:
        fail("input")
    for directory in (*reversed(path.parents), path):
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
        ):
            fail("input")
    return path.resolve(strict=True)


def _file_hash(path, base, request):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
    ):
        fail("changed_input")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            base._guard(request)
            count += len(block)
            if count > 8 * 1024**3:
                fail("resource")
            digest.update(block)
    after = path.lstat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or count != info.st_size:
        fail("changed_input")
    return digest.hexdigest(), count


def _table(db, name, definition, codec, guard, transform=None):
    columns = definition["columns"]
    keys = sorted(
        (column for column in columns if column["pk_position"]),
        key=lambda column: column["pk_position"],
    )
    select = ",".join('"' + column["name"] + '"' for column in columns)
    order = ",".join(
        '"'
        + column["name"]
        + '"'
        + (" COLLATE BINARY" if column["declared_type"] == "TEXT" else "")
        for column in keys
    )
    cursor = db.execute(f'SELECT {select} FROM "{name}" ORDER BY {order}')
    codec.LIMITS.update(rows=1_000_000, total_bytes=8 * 1024**3)
    try:
        rows = (transform(tuple(row)) if transform is not None else tuple(row) for row in cursor)
        result = codec.digest_table(name, rows, guard=guard)
    finally:
        cursor.close()
    return {key: result[key] for key in ("rows", "sha256")}


def prepare_working(
    request,
    base,
    codec,
    structure,
    payload,
    capture,
    inventory,
    transcripts,
    timing,
    runtime,
    target_structure,
    provider,
    native,
):
    """Authored local overlay in this same disposable heap-limited child, never a live Store."""
    restore = request["restore"]
    if type(restore) is not dict or set(restore) != {
        "working_path",
        "target_root",
        "restore_id",
        "source_backup_id",
    }:
        fail("input")
    for key in ("restore_id", "source_backup_id"):
        identifier(restore[key])
    if restore["source_backup_id"] != request["manifest"]["backup_id"]:
        fail("input")
    if any(
        type(restore[key]) is not str or len(restore[key]) > 4096 or "\0" in restore[key]
        for key in ("working_path", "target_root")
    ):
        fail("input")
    working, final_root = Path(restore["working_path"]), Path(restore["target_root"])
    owned = _parent(working.parent)
    if not final_root.is_absolute() or ".." in final_root.parts:
        fail("input")
    _parent(final_root.parent)
    source_path = Path(request["path"]).resolve(strict=True)
    if (
        working == source_path
        or not source_path.is_relative_to(owned)
        or working.parent.resolve() != owned
        or final_root.exists()
    ):
        fail("input")
    if working.exists():
        _file_hash(working, base, request)
    pending = owned / (".portable-working-" + uuid.uuid4().hex + ".pending")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    source_db = target_db = None

    def guard():
        base._guard(request)

    try:
        source_db = sqlite3.connect(
            source_path.as_uri() + "?mode=ro&immutable=1",
            uri=True,
            timeout=0,
            isolation_level=None,
            cached_statements=0,
        )
        state = base._hardening(source_db, request, structure)
        base._schema(source_db, structure, state)
        source_db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32 * 1024**2)
        semantic = Semantics(
            source_db,
            request,
            base,
            payload,
            structure,
            capture,
            inventory,
            transcripts,
            timing,
            runtime,
            provider,
            native,
        )
        target_db = sqlite3.connect(pending, timeout=0, isolation_level=None, cached_statements=0)
        for setting, enabled in (
            ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
            ("SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False),
            ("SQLITE_DBCONFIG_ENABLE_TRIGGER", False),
            ("SQLITE_DBCONFIG_ENABLE_VIEW", False),
        ):
            flag = getattr(sqlite3, setting)
            target_db.setconfig(flag, enabled)
            if target_db.getconfig(flag) is not enabled:
                fail("unsupported_runtime")
        source_db.backup(target_db, pages=64, progress=lambda *_: guard())
        target_db.execute("PRAGMA journal_mode=DELETE")
        target_db.execute("PRAGMA foreign_keys=ON")
        target_db.execute("BEGIN IMMEDIATE")
        if request["manifest"]["schema_version"] == 13:
            for name in ("recording_result_roles", "result_role_events"):
                sql = next(
                    item["sql"]
                    for item in target_structure["objects"]
                    if item["type"] == "table" and item["name"] == name
                )
                target_db.execute(sql)
        if 14 <= request["manifest"]["schema_version"] < 18:
            target_db.execute(
                "ALTER TABLE result_role_events RENAME TO result_role_events_before_auto"
            )
            sql = next(
                item["sql"]
                for item in target_structure["objects"]
                if item["type"] == "table" and item["name"] == "result_role_events"
            )
            target_db.execute(sql)
            target_db.execute(
                "INSERT INTO result_role_events SELECT * FROM result_role_events_before_auto"
            )
            target_db.execute("DROP TABLE result_role_events_before_auto")
        if request["manifest"]["schema_version"] < 18:
            sql = next(
                item["sql"]
                for item in target_structure["objects"]
                if item["type"] == "table" and item["name"] == "working_job_authorities"
            )
            target_db.execute(sql)
        if request["manifest"]["schema_version"] < 19:
            sql = next(
                item["sql"]
                for item in target_structure["objects"]
                if item["type"] == "table" and item["name"] == "passage_reviews"
            )
            target_db.execute(sql)
        if request["manifest"]["schema_version"] < 15:
            sql = next(
                item["sql"]
                for item in target_structure["objects"]
                if item["type"] == "table" and item["name"] == "restored_owners"
            )
            target_db.execute(sql)
        if request["manifest"]["schema_version"] < 16:
            sql = next(
                item["sql"]
                for item in target_structure["objects"]
                if item["type"] == "table" and item["name"] == "runtime_preparations"
            )
            target_db.execute(sql)
        if request["manifest"]["schema_version"] < 17:
            target_db.execute("ALTER TABLE projects ADD COLUMN default_language TEXT")
            target_db.execute(
                "ALTER TABLE projects ADD COLUMN language_revision INTEGER NOT NULL DEFAULT 0"
            )
            target_db.execute("ALTER TABLE recordings ADD COLUMN language_override TEXT")
            target_db.execute(
                "ALTER TABLE recordings ADD COLUMN language_revision INTEGER NOT NULL DEFAULT 0"
            )
        safe_provider = canonical(
            {
                "provider_type": "custom",
                "base_url": "https://unconfigured.invalid/v1",
                "model": "unconfigured",
                "language": "",
                "capability": "segments",
                "enabled": False,
                "approved_for": [],
                "max_upload_mb": 20,
                "concurrent_chunks": 1,
                "ucloud": None,
                "ucloud_transcriber": None,
            }
        ).decode("utf-8")
        first = semantic.one("SELECT id FROM providers ORDER BY id COLLATE BINARY LIMIT 1")
        default_id = (
            first[0]
            if first
            else hashlib.sha256(
                ("portable-unconfigured-provider:" + restore["restore_id"]).encode()
            ).hexdigest()[:32]
        )
        default_settings = canonical({"default_provider_id": default_id}).decode("utf-8")
        expected = {}
        transforms = {
            "projects": lambda row: tuple(
                str(final_root / "projects" / row[0]) if column["name"] == "storage_dir" else value
                for column, value in zip(
                    structure["tables"]["projects"]["columns"], row, strict=True
                )
            ),
            "recordings": lambda row: tuple(
                "preparing"
                if column["name"] == "status"
                else (None if column["name"] == "original_input" else value)
                for column, value in zip(
                    structure["tables"]["recordings"]["columns"], row, strict=True
                )
            ),
            "providers": lambda row: (row[0], row[1], safe_provider, row[3]),
        }
        if request["manifest"]["schema_version"] >= 16:
            transforms["runtime_preparations"] = lambda row: tuple(
                1 if column["name"] == "archived" else value
                for column, value in zip(
                    structure["tables"]["runtime_preparations"]["columns"], row, strict=True
                )
            )
        for name, transform in transforms.items():
            expected[name] = _table(
                source_db, name, structure["tables"][name], codec, guard, transform
            )
        codec.LIMITS.update(rows=1_000_000, total_bytes=8 * 1024**3)
        value = codec.digest_table("settings", [(2, default_settings)], guard=guard)
        expected["settings"] = {key: value[key] for key in ("rows", "sha256")}
        if first is None:
            row = (default_id, "Unconfigured", safe_provider, request["manifest"]["created_at"])
            value = codec.digest_table("providers", [row], guard=guard)
            expected["providers"] = {key: value[key] for key in ("rows", "sha256")}
            target_db.execute("INSERT INTO providers VALUES (?,?,?,?)", row)
        else:
            target_db.execute("UPDATE providers SET value=?", (safe_provider,))
        for (project_id,) in semantic.rows("SELECT id FROM projects"):
            identifier(project_id)
            target_db.execute(
                "UPDATE projects SET storage_dir=? WHERE id=?",
                (str(final_root / "projects" / project_id), project_id),
            )
        # Current source-inspection caches describe the old machine and are operational,
        # including malformed caches. Immutable source DB, old jobs and receipts stay exact.
        target_db.execute("UPDATE recordings SET status='preparing',original_input=NULL")
        target_db.execute("DELETE FROM settings")
        target_db.execute("INSERT INTO settings VALUES (?,?)", (2, default_settings))
        target_db.execute("UPDATE runtime_preparations SET archived=1")
        owners = set()
        owner_rows = sum(
            request["manifest"]["database"]["tables"][name]["rows"]
            for name in ("jobs", "native_batch_runs", "runtime_batch_runs", "runtime_batch_members")
        )
        if owner_rows > OWNER_LIMIT:
            fail("resource")
        ancestors = {}
        if request["manifest"]["schema_version"] >= 15:
            if request["manifest"]["database"]["tables"]["restored_owners"]["rows"] > OWNER_LIMIT:
                fail("resource")
            for row in semantic.rows(
                "SELECT kind,owner_id,restore_id,source_backup_id FROM restored_owners"
            ):
                ancestors[(row[0], row[1])] = row
        for table, kind in (
            ("jobs", "job"),
            ("native_batch_runs", "native_batch"),
            ("runtime_batch_runs", "runtime_batch"),
        ):
            for (owner_id,) in semantic.rows(f'SELECT id FROM "{table}"'):
                identifier(owner_id)
                owners.add((kind, owner_id))
        for (batch_id,) in semantic.rows("SELECT id FROM native_batch_runs"):
            members = semantic.json(semantic.text("native_batch_runs", "members", "id", batch_id))
            if type(members) is not list or len(members) > 100:
                fail()
            for member in members:
                if type(member) is not dict or "job_id" not in member:
                    fail()
                identifier(member["job_id"])
                owners.add(("job", member["job_id"]))
                if len(owners) > OWNER_LIMIT:
                    fail("resource")
        for (job_id,) in semantic.rows("SELECT job_id FROM runtime_batch_members"):
            identifier(job_id)
            owners.add(("job", job_id))
        for kind, owner_id in sorted(owners):
            ancestors.setdefault(
                (kind, owner_id),
                (kind, owner_id, restore["restore_id"], restore["source_backup_id"]),
            )
            if len(ancestors) > OWNER_LIMIT:
                fail("resource")
            target_db.execute(
                "INSERT OR IGNORE INTO restored_owners VALUES (?,?,?,?)",
                (kind, owner_id, restore["restore_id"], restore["source_backup_id"]),
            )
        target_db.execute("PRAGMA user_version=19")
        target_db.execute("COMMIT")
        # Compare complete typed rows under the SOURCE codec: schema salt stays identical,
        # so every untouched cell is proved exact and each permitted cell is reconstructed.
        for name, definition in structure["tables"].items():
            if name == "restored_owners":
                continue
            actual = _table(target_db, name, definition, codec, guard)
            wanted = expected.get(name, request["manifest"]["database"]["tables"][name])
            if actual != wanted:
                fail()
        target_codec = row_codec(
            request, source(request["target_catalog_b64"], 128 * 1024, CATALOGS[19][0]), 19
        )
        if request["manifest"]["schema_version"] < 19:
            empty = target_codec.digest_table("passage_reviews", [], guard=guard)
            expected["passage_reviews"] = {key: empty[key] for key in ("rows", "sha256")}
            if (
                _table(
                    target_db,
                    "passage_reviews",
                    target_structure["tables"]["passage_reviews"],
                    target_codec,
                    guard,
                )
                != expected["passage_reviews"]
            ):
                fail()
        if request["manifest"]["schema_version"] < 16:
            empty = target_codec.digest_table("runtime_preparations", [], guard=guard)
            expected["runtime_preparations"] = {key: empty[key] for key in ("rows", "sha256")}
        else:
            expected["runtime_preparations"] = _table(
                source_db,
                "runtime_preparations",
                structure["tables"]["runtime_preparations"],
                target_codec,
                guard,
                transforms["runtime_preparations"],
            )
        if (
            request["manifest"]["schema_version"] < 18
            and target_db.execute("SELECT 1 FROM working_job_authorities LIMIT 1").fetchone()
        ):
            fail()
        if request["manifest"]["schema_version"] < 17:
            for table, column in (
                ("projects", "default_language"),
                ("recordings", "language_override"),
            ):
                if target_db.execute(
                    f"SELECT 1 FROM {table} WHERE {column} IS NOT NULL "
                    "OR language_revision != 0 LIMIT 1"
                ).fetchone():
                    fail()
        if (
            _table(
                target_db,
                "runtime_preparations",
                target_structure["tables"]["runtime_preparations"],
                target_codec,
                guard,
            )
            != expected["runtime_preparations"]
        ):
            fail()
        claim = target_codec.digest_table(
            "restored_owners", (ancestors[key] for key in sorted(ancestors)), guard=guard
        )
        expected["restored_owners"] = {key: claim[key] for key in ("rows", "sha256")}
        if (
            _table(
                target_db,
                "restored_owners",
                target_structure["tables"]["restored_owners"],
                target_codec,
                guard,
            )
            != expected["restored_owners"]
        ):
            fail()
        for kind, owner_id in owners:
            if (
                target_db.execute(
                    "SELECT 1 FROM restored_owners WHERE kind=? AND owner_id=?", (kind, owner_id)
                ).fetchone()
                is None
            ):
                fail()
        quarantine_counts = dict.fromkeys(("job", "native_batch", "runtime_batch"), 0)
        for kind, owner_id, restore_id, backup_id in target_db.execute(
            "SELECT kind,owner_id,restore_id,source_backup_id FROM restored_owners"
        ):
            if kind not in quarantine_counts:
                fail()
            for value in (owner_id, restore_id, backup_id):
                identifier(value)
            quarantine_counts[kind] += 1
        quarantine_counts["runtime_preparation"] = target_db.execute(
            "SELECT count(*) FROM runtime_preparations WHERE archived=1"
        ).fetchone()[0]
        target_db.execute("VACUUM")
        base.SCHEMA_FINGERPRINT = CATALOGS[19][1]
        target_state = base._hardening(target_db, request, target_structure)
        base._schema(target_db, target_structure, target_state)
        target_db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 32 * 1024**2)
        target_state["phase"] = "checks"
        if (
            base._one(target_db, "PRAGMA integrity_check(1)") != ("ok",)
            or base._one(target_db, "PRAGMA foreign_key_check") is not None
        ):
            fail("integrity")
        target_db.close()
        target_db = None
        source_db.close()
        source_db = None
        with pending.open("r+b") as stream:
            os.fsync(stream.fileno())
        digest, size = _file_hash(pending, base, request)
        if working.exists():
            if _file_hash(working, base, request) != (digest, size):
                fail("changed_input")
        else:
            os.link(pending, working, follow_symlinks=False)
        return {
            "working_database_sha256": digest,
            "working_database_bytes": size,
            "schema_version": 19,
            "quarantine_policy": "cbs-restored-owner-quarantine-v1",
            "quarantine_counts": quarantine_counts,
            "overlay_tables": expected,
        }
    finally:
        if target_db is not None:
            target_db.close()
        if source_db is not None:
            source_db.close()
        pending.unlink(missing_ok=True)


class Semantics:
    def __init__(
        self,
        db,
        request,
        base,
        payload,
        structure,
        capture,
        inventory,
        transcripts,
        timing,
        runtime,
        provider,
        native,
    ):
        self.db, self.request, self.base, self.payload = db, request, base, payload
        self.capture = capture
        self.inventory = inventory
        self.transcripts, self.timing = transcripts, timing
        self.runtime = runtime
        self.provider, self.native = provider, native
        self.capture_results = {}
        self.absent_runtime = set()
        self.import_hashes = {}
        self.bytes = 0
        self.audio = {}
        if len(request["manifest"]["files"]) > MAPPING_LIMIT:
            fail("resource")
        self.mapped = {
            (item["recording_id"], item["relative_path"]): item["object"]
            for item in request["manifest"]["files"]
        }
        self.sizes = {item["sha256"]: item["size"] for item in request["manifest"]["objects"]}
        self.restorations = {}
        records = request["preservation_records"]
        if type(records) is not list or len(records) > MAPPING_LIMIT // 4:
            fail("resource")
        for item in records:
            if type(item) is not dict or set(item) != {
                "restore_id",
                "source_backup_id",
                "source_database_sha256",
                "commit_sha256",
            }:
                fail("input")
            identifier(item["restore_id"])
            identifier(item["source_backup_id"])
            restore_id = item["restore_id"]
            prefix = "preservation/" + restore_id + "/"
            if (
                restore_id in self.restorations
                or self.mapped.get((None, prefix + "source.sqlite3"))
                != item["source_database_sha256"]
                or self.mapped.get((None, prefix + "COMMITTED.json")) != item["commit_sha256"]
            ):
                fail("mapping")
            self.restorations[restore_id] = item
        self.counts = dict(
            result_versions=0,
            results=0,
            recordings=0,
            note_versions=0,
            file_mappings=0,
            role_events=0,
        )
        tables = set(structure["tables"])

        def authorize(action, first, second, database, origin):
            if origin is not None or database not in (None, "main"):
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and first in tables:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and second in {
                "length",
                "typeof",
                "json_type",
                "json_valid",
            }:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        db.set_authorizer(authorize)

    def guard(self):
        self.base._guard(self.request)

    def rows(self, sql, parameters=()):
        cursor = self.db.execute(sql, parameters)
        try:
            for row in cursor:
                self.guard()
                yield row
        finally:
            cursor.close()

    def one(self, sql, parameters=()):
        return self.base._one(self.db, sql, parameters)

    def text(self, table, column, key, value, *, maximum=None):
        # Table/column/key names are authored literals, never incoming SQL identifiers.
        maximum = self.payload.MAX_BYTES if maximum is None else maximum
        keys, parameters = ((key,), (value,)) if type(key) is str else (key, value)
        where = " AND ".join('"' + name + '"=?' for name in keys)
        size = self.one(
            f'SELECT length(CAST("{column}" AS BLOB)) FROM "{table}" WHERE {where}', parameters
        )
        if size is None or type(size[0]) is not int:
            fail()
        if size[0] > maximum or self.bytes + size[0] > SEMANTIC_BYTES:
            fail("resource")
        raw = self.one(f'SELECT "{column}" FROM "{table}" WHERE {where}', parameters)[0]
        if type(raw) is not str:
            fail()
        self.bytes += size[0]
        return raw

    def json(self, raw):
        try:
            encoded = raw.encode("utf-8")
            if len(encoded) > self.payload.MAX_BYTES:
                fail("resource")
            self.payload._preflight(encoded, self.guard)
            value = json.loads(
                encoded,
                object_pairs_hook=self.payload._pairs,
                parse_float=self.payload._float,
                parse_constant=self.payload._constant,
            )
            # Validate Unicode and the full numeric domain even for opaque metadata.
            self.hash_value(value)
            return value
        except ValueError as error:
            fail("resource" if "resource budget" in str(error) else "semantics")

    def hash_value(self, value):
        digest, size = hashlib.sha256(), 0
        for part in self.payload._encoder(value):
            self.guard()
            for offset in range(0, len(part), self.payload.UTF8_CHARS):
                raw = self.payload._piece(part, offset)
                size += len(raw)
                if size > self.payload.MAX_BYTES:
                    fail("resource")
                digest.update(raw)
        return digest.hexdigest()

    def projection(self, table, key):
        segments = self.json(self.text(table, "segments", "id", key))
        speakers = self.json(self.text(table, "speakers", "id", key))
        if type(segments) is not list or type(speakers) is not dict:
            fail()
        digest = self.hash_value(
            {"format": self.payload.FORMAT, "segments": segments, "speakers": speakers}
        )
        return digest, not segments and not speakers

    def version_reference(self, version_id, recording_id, expected_hash=None):
        identifier(version_id)
        row = self.one(
            "SELECT t.recording_id,v.payload_sha256 FROM result_versions v "
            "JOIN transcript_results t ON t.id=v.result_id WHERE v.id=?",
            (version_id,),
        )
        if (
            row is None
            or row[0] != recording_id
            or (expected_hash is not None and row[1] != expected_hash)
        ):
            fail()
        return row[1]

    def artifact(self, value, recording_id, result_id):
        """A PRESENT import receipt is a promise, never an optional/legacy gap."""
        if value == "not_captured":
            return
        if (
            type(value) is not dict
            or set(value) != {"schema", "role", "source_id", "sha256", "bytes", "format"}
            or type(value["schema"]) is not int
            or value["schema"] not in (1, 2)
            or value["role"] != "transcript_import"
            or type(value["source_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", value["source_id"]) is None
            or type(value["sha256"]) is not str
            or re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) is None
            or type(value["bytes"]) is not int
            or not 1 <= value["bytes"] <= 32 * 1024**2
            or value["format"] not in {"json", "csv"}
        ):
            fail()
        name = "sources/" + value["source_id"] + "." + value["format"]
        self.mapping_receipt(recording_id, name, value["sha256"], value["bytes"])
        source = self.json(self.text("transcript_results", "source", "id", result_id))
        if (
            type(source) is not dict
            or source.get("kind") != "import"
            or (canonical(source.get("artifact_evidence")) != canonical(value))
        ):
            fail()
        creation = self.one(
            "SELECT id,payload_sha256 FROM result_versions WHERE result_id=? AND sequence=1",
            (result_id,),
        )
        if creation is None:
            fail()
        original_context = self.json(self.text("result_versions", "context", "id", creation[0]))
        if canonical(original_context.get("artifact_evidence")) != canonical(value):
            fail()
        cache_key = value["schema"], value["sha256"], value["format"], value["bytes"]
        if cache_key not in self.import_hashes:
            if value["bytes"] > self.payload.MAX_BYTES:
                fail("resource")
            path = Path(self.request["objects_root"]) / value["sha256"]
            before = self.base._witness(path)
            if before["size"] != value["bytes"] or self.bytes + before["size"] > SEMANTIC_BYTES:
                fail("resource")
            with path.open("rb") as stream:
                raw = stream.read(value["bytes"] + 1)
            self.bytes += len(raw)
            if (
                len(raw) != value["bytes"]
                or hashlib.sha256(raw).hexdigest() != value["sha256"]
                or self.base._witness(path) != before
            ):
                fail("mapping")
            if value["format"] == "json":
                # Only resource preflight: retain the frozen import parser's accepted spelling,
                # including ignored extension fields. Compare its exact normalized payload.
                try:
                    self.payload._preflight(raw, self.guard)
                except ValueError as error:
                    fail("resource" if "resource budget" in str(error) else "semantics")
            else:
                csv.field_size_limit(200_000)
                count = 0
                try:
                    for row in csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")):
                        self.guard()
                        count += 1
                        if (
                            count > 10_001
                            or len(row) > 64
                            or any(len(field) > 200_000 for field in row)
                        ):
                            fail("resource")
                except csv.Error:
                    fail("resource")
            parsed = self.transcripts.parse_transcript(
                raw, "source." + value["format"], generation=value["schema"]
            )
            self.transcripts.pop_hints(parsed)
            self.import_hashes[cache_key] = self.hash_value(
                {"format": self.payload.FORMAT, "segments": parsed, "speakers": {}}
            )
        if self.import_hashes[cache_key] != creation[1]:
            fail()

    def mapping_receipt(self, recording_id, name, digest, count):
        actual = self.mapped.get((recording_id, name))
        if actual != digest or (count is not None and self.sizes.get(actual) != count):
            fail("mapping")

    def producer(self, context, recording_id, result_id, version_id, payload_hash, sequence):
        if "artifact_evidence" in context:
            self.artifact(context["artifact_evidence"], recording_id, result_id)
        if "producer_artifacts" not in context:
            return
        original = self.one(
            "SELECT id,payload_sha256 FROM result_versions WHERE result_id=? AND sequence=1",
            (result_id,),
        )
        if original is None:
            fail()
        if sequence != 1:
            creation_context = self.json(self.text("result_versions", "context", "id", original[0]))
            if canonical(creation_context.get("producer_artifacts")) != canonical(
                context["producer_artifacts"]
            ):
                fail()
        job_id = self.one("SELECT job_id FROM transcript_results WHERE id=?", (result_id,))[0]
        if job_id is None:
            fail()
        creation = {
            "result_id": result_id,
            "version_id": original[0],
            "payload_sha256": original[1],
        }
        self.processing_inventory(context["producer_artifacts"], recording_id, job_id, creation)
        if self.one("SELECT 1 FROM jobs WHERE id=?", (job_id,)) is not None:
            params = self.json(self.text("jobs", "params", "id", job_id))
            if type(params) is not dict or "producer_artifact_capture" not in params:
                fail()
            try:
                unbound = self.capture.unbound(
                    context["producer_artifacts"],
                    recording_id=recording_id,
                    job_id=job_id,
                    creation=creation,
                )
                if canonical(unbound) != canonical(params["producer_artifact_capture"]):
                    fail()
            except (ValueError, TypeError, KeyError):
                fail()

    def processing_inventory(self, receipt, recording_id, job_id, creation):
        try:
            inventory = self.capture.inventory(
                receipt, recording_id=recording_id, job_id=job_id, creation=creation
            )
        except (ValueError, TypeError, KeyError):
            fail()
        self.required_files(inventory, recording_id, job_id)

    def required_files(self, inventory, recording_id, job_id):
        for expected in inventory:
            self.mapping_receipt(
                recording_id,
                "enhancements/" + job_id + "/" + expected["filename"],
                expected["sha256"],
                expected["bytes"],
            )
            if "audio_frames" in expected:
                previous = self.audio.setdefault(expected["sha256"], expected["audio_frames"])
                if previous != expected["audio_frames"]:
                    fail()

    def timing_receipt(self, context, recording_id, result_id):
        if "timing_artifacts" not in context:
            return
        evidence = context["timing_artifacts"]
        if type(evidence) is not dict or type(evidence.get("creation")) is not dict:
            fail()
        reference = evidence["creation"]
        if (
            set(reference) != {"result_id", "version_id", "payload_sha256"}
            or reference["result_id"] != result_id
        ):
            fail()
        self.version_reference(reference["version_id"], recording_id, reference["payload_sha256"])
        application = self.one(
            "SELECT result_id,predecessor_id,action FROM result_versions WHERE id=?",
            (reference["version_id"],),
        )
        if (
            application is None
            or application[0] != result_id
            or application[2] not in {"timing_realign", "save_no_change"}
        ):
            fail()
        original = self.json(self.text("result_versions", "context", "id", reference["version_id"]))
        if canonical(original.get("timing_artifacts")) != canonical(evidence):
            fail()
        job_id = original.get("job_id")
        identifier(job_id)
        owner = self.one("SELECT recording_id,kind FROM jobs WHERE id=?", (job_id,))
        if owner is None or owner[0] != recording_id:
            fail()
        params = self.json(self.text("jobs", "params", "id", job_id))
        job = {"id": job_id, "recording_id": recording_id, "kind": owner[1], "params": params}
        try:
            references = self.timing.historical_references(evidence, job)
            required = self.timing.inventory(evidence, job, creation=reference)
        except (ValueError, TypeError, KeyError, AttributeError):
            fail()
        for parent in references:
            if parent["result_id"] != result_id:
                fail()
            self.version_reference(parent["version_id"], recording_id, parent["payload_sha256"])
            if self.one(
                "SELECT result_id FROM result_versions WHERE id=?", (parent["version_id"],)
            ) != (result_id,):
                fail()
        if application[1] != references[-1]["version_id"]:
            fail()
        if (
            params["realign_apply_receipt"].get("input_sha256")
            != hashlib.sha256(
                canonical({"source": params["source_snapshot"], "output": params["realign_output"]})
            ).hexdigest()
        ):
            fail()
        source = self.json(
            self.text("result_versions", "payload", "id", evidence["source"]["version_id"])
        )
        frozen = params["source_snapshot"]
        if self.hash_value(frozen["speakers"]) != self.hash_value(source["speakers"]):
            fail()
        originals = {}
        for passage in source["segments"]:
            if (
                type(passage) is not dict
                or type(passage.get("id")) is not str
                or passage["id"] in originals
            ):
                fail()
            originals[passage["id"]] = passage
        for passage in frozen["segments"]:
            if self.hash_value(originals.get(passage["id"])) != self.hash_value(passage):
                fail()
        self.required_files(required, recording_id, job_id)

    def capture_job(self, kernel, job_id, recording_id):
        """The exact owning job row, decoded once; never a receipt-supplied path."""
        owner = self.one("SELECT recording_id,kind FROM jobs WHERE id=?", (job_id,))
        if owner is None:
            return None
        params = self.json(self.text("jobs", "params", "id", job_id))
        if owner[0] != recording_id or type(params) is not dict:
            fail()
        job = {"id": job_id, "recording_id": recording_id, "kind": owner[1], "params": params}
        try:
            if not kernel.enabled(job):
                fail()
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            fail()
        return job

    def capture_files(self, required, recording_id, folder):
        for expected in required:
            self.mapping_receipt(
                recording_id, folder + expected["filename"], expected["sha256"], expected["bytes"]
            )

    def kernels(self, job_id):
        """API parts live beneath chunks/<job>; native leaves are named by the job itself."""
        return (
            (self.provider, "provider_artifacts", "chunks/" + job_id + "/"),
            (self.native, "native_artifacts", ""),
        )

    def capture_receipt(self, context, recording_id, result_id):
        """A PRESENT API/native observation binds its creation, job capture and files."""
        if "provider_artifacts" not in context and "native_artifacts" not in context:
            return
        original = self.one(
            "SELECT id,payload_sha256 FROM result_versions WHERE result_id=? AND sequence=1",
            (result_id,),
        )
        job_id = self.one("SELECT job_id FROM transcript_results WHERE id=?", (result_id,))
        if original is None or job_id is None or job_id[0] is None:
            fail()
        job_id = job_id[0]
        identifier(job_id)
        for kernel, key, folder in self.kernels(job_id):
            if key not in context:
                continue
            value = context[key]
            fingerprint = self.hash_value(value)
            if (key, result_id) in self.capture_results:
                # Later versions inherit the exact creation proof already checked below.
                if self.capture_results[key, result_id] != fingerprint:
                    fail()
                continue
            first = self.json(self.text("result_versions", "context", "id", original[0]))
            if type(first) is not dict or self.hash_value(first.get(key)) != fingerprint:
                fail()
            creation = {
                "result_id": result_id,
                "version_id": original[0],
                "payload_sha256": original[1],
            }
            job = self.capture_job(kernel, job_id, recording_id)
            try:
                transport = kernel.unbound(
                    value, recording_id=recording_id, job_id=job_id, creation=creation
                )
                required = kernel.inventory(
                    value, recording_id=recording_id, job_id=job_id, creation=creation
                )
                if job is not None:
                    if canonical(job["params"].get(kernel.KEYS[1])) != canonical(transport):
                        fail()
                    kernel.validate(transport, job)
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                fail()
            self.capture_files(required, recording_id, folder)
            self.capture_results[key, result_id] = fingerprint

    def job_receipts(self):
        safe = "CASE WHEN json_valid(params) THEN params ELSE 'null' END"
        for job_id, recording_id in self.rows(
            f"SELECT id,recording_id FROM jobs WHERE json_type({safe},"
            f"'$.provider_artifact_policy') IS NOT NULL OR json_type({safe},"
            f"'$.provider_artifact_capture') IS NOT NULL OR json_type({safe},"
            f"'$.native_artifact_policy') IS NOT NULL OR json_type({safe},"
            "'$.native_artifact_capture') IS NOT NULL"
        ):
            identifier(job_id)
            params = self.json(self.text("jobs", "params", "id", job_id))
            if type(params) is not dict:
                fail()
            for kernel, _, folder in self.kernels(job_id):
                if not set(kernel.KEYS) & params.keys():
                    continue
                job = self.capture_job(kernel, job_id, recording_id)
                capture = job["params"].get(kernel.KEYS[1])
                if capture is None:
                    continue  # A frozen policy without a recorded request promises no files.
                try:
                    kernel.validate(capture, job)
                    required = kernel.inventory(capture, recording_id=recording_id, job_id=job_id)
                except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                    fail()
                self.capture_files(required, recording_id, folder)
        if (
            self.one(
                "SELECT 1 FROM jobs WHERE json_type(CASE WHEN json_valid(params) "
                "THEN params ELSE 'null' END,'$.runtime_artifact_capture') IS NOT NULL LIMIT 1"
            )
            is not None
        ):
            # Runtime observations belong to actual attempt rows, never a job's assertion.
            fail()
        for job_id, recording_id in self.rows(
            "SELECT id,recording_id FROM jobs WHERE json_type("
            "CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.producer_artifact_capture') IS NOT NULL"
        ):
            params = self.json(self.text("jobs", "params", "id", job_id))
            if type(params) is not dict:
                fail()
            self.processing_inventory(
                params["producer_artifact_capture"], recording_id, job_id, None
            )
        for job_id, recording_id, kind in self.rows(
            "SELECT id,recording_id,kind FROM jobs WHERE json_type("
            "CASE WHEN json_valid(params) THEN params ELSE 'null' END,"
            "'$.timing_artifact_capture') IS NOT NULL"
        ):
            params = self.json(self.text("jobs", "params", "id", job_id))
            job = {"id": job_id, "recording_id": recording_id, "kind": kind, "params": params}
            try:
                required = self.timing.inventory(
                    params["timing_artifact_capture"], job, creation=None
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                fail()
            self.required_files(required, recording_id, job_id)

    def runtime_data(self, job_id, attempt):
        integer(attempt, minimum=1)
        if attempt >= 2**31:
            fail()
        identifier(job_id)
        row = self.one(
            "SELECT m.recording_id,m.batch_id,m.current_attempt,m.result_id,"
            "a.task_id,b.compatibility_hash,b.project_id FROM runtime_batch_members m "
            "JOIN runtime_batch_attempts a ON a.job_id=m.job_id "
            "JOIN runtime_batch_runs b ON b.id=m.batch_id WHERE m.job_id=? AND a.attempt=?",
            (job_id, attempt),
        )
        if row is None:
            fail()
        recording_id, batch_id, current, result_id, task_id, identity_hash, project_id = row
        integer(current, minimum=1)
        if current >= 2**31 or attempt > current:
            fail()
        binding = {
            "batch_id": batch_id,
            "task_id": task_id,
            "attempt": attempt,
            "identity_hash": identity_hash,
        }
        try:
            self.runtime._binding(binding)
        except (ValueError, TypeError, KeyError):
            fail()
        identity = self.json(self.text("runtime_batch_runs", "identity", "id", batch_id))
        if (
            type(identity) is not dict
            or identity.get("operation") != "transcription"
            or self.hash_value(identity) != identity_hash
        ):
            fail()
        recording = self.one(
            "SELECT project_id,filename FROM recordings WHERE id=?", (recording_id,)
        )
        job = self.one("SELECT recording_id,kind FROM jobs WHERE id=?", (job_id,))
        if recording is not None and recording[0] != project_id:
            fail()
        if recording is not None and job is None:
            fail()  # A live recording cannot use the narrow deleted-owner exception.
        if job is not None:
            if job != (recording_id, "enhance"):
                fail()
            params = self.json(self.text("jobs", "params", "id", job_id))
            if (
                type(params) is not dict
                or not self.runtime.eligible(params)
                or params.get("runtime_artifact_policy") != self.runtime.POLICY
            ):
                fail()
        inputs = self.json(
            self.text(
                "runtime_batch_attempts", "input_receipt", ("job_id", "attempt"), (job_id, attempt)
            )
        )
        remote = self.json(
            self.text(
                "runtime_batch_attempts", "remote_receipt", ("job_id", "attempt"), (job_id, attempt)
            )
        )
        if type(inputs) is not dict or type(remote) is not dict or "artifact_origin" not in inputs:
            fail()
        output = self.json(self.text("runtime_batch_members", "output_receipt", "job_id", job_id))
        if type(output) is not dict:
            fail()
        return {
            "recording_id": recording_id,
            "binding": binding,
            "current": current,
            "result_id": result_id,
            "inputs": inputs,
            "remote": remote,
            "output": output,
            "recording": recording,
            "absent": recording is None and job is None,
        }

    def runtime_files(self, value, data, job_id, creation=None):
        try:
            self.runtime.validate_document(
                value,
                recording_id=data["recording_id"],
                job_id=job_id,
                binding=data["binding"],
                inputs=data["inputs"],
                remote=data["remote"],
                output_receipt=data["output"] if creation is not None else None,
                creation=creation,
            )
            required = self.runtime.inventory(
                value, recording_id=data["recording_id"], job_id=job_id, creation=creation
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            fail()
        if data["absent"]:
            self.absent_runtime.add((data["recording_id"], job_id))
            return
        if data["recording"] is None:
            fail()
        for expected in required:
            scope = expected.get("scope")
            if scope == "managed_original":
                suffix = PurePosixPath(data["recording"][1]).suffix.lower()
                if expected.get("transport_name") != "audio.source" + suffix:
                    fail()
                self.mapping_receipt(
                    data["recording_id"], "original" + suffix, expected["sha256"], expected["bytes"]
                )
                continue
            if scope not in {"producer_directory", "producer_attempt"}:
                fail()
            prefix = ""
            if scope == "producer_attempt":
                if expected.get("attempt") != data["binding"]["attempt"]:
                    fail()
                if expected["attempt"] < data["current"]:
                    prefix = "attempts/" + str(expected["attempt"]) + "/"
            name = "enhancements/" + job_id + "/" + prefix + expected["filename"]
            self.mapping_receipt(data["recording_id"], name, expected["sha256"], expected["bytes"])

    def runtime_receipt(self, context, recording_id, result_id):
        if "runtime_artifacts" not in context:
            return
        value = context["runtime_artifacts"]
        if type(value) is not dict or type(value.get("origin")) is not dict:
            fail()
        binding = value["origin"].get("binding")
        try:
            self.runtime._binding(binding)
        except (ValueError, TypeError, KeyError):
            fail()
        original = self.one(
            "SELECT id,payload_sha256 FROM result_versions WHERE result_id=? AND sequence=1",
            (result_id,),
        )
        if original is None:
            fail()
        first_context = self.json(self.text("result_versions", "context", "id", original[0]))
        if canonical(first_context.get("runtime_artifacts")) != canonical(value):
            fail()
        job_id = self.one("SELECT job_id FROM transcript_results WHERE id=?", (result_id,))[0]
        data = self.runtime_data(job_id, binding["attempt"])
        if data["recording_id"] != recording_id or data["result_id"] != result_id:
            fail()
        self.runtime_files(
            value,
            data,
            job_id,
            {"result_id": result_id, "version_id": original[0], "payload_sha256": original[1]},
        )

    def runtime_attempts(self):
        source = "CASE WHEN json_valid(a.input_receipt) THEN a.input_receipt ELSE 'null' END"
        remote = "CASE WHEN json_valid(a.remote_receipt) THEN a.remote_receipt ELSE 'null' END"
        for job_id, attempt in self.rows(
            "SELECT a.job_id,a.attempt FROM runtime_batch_attempts a "
            f"WHERE json_type({source},'$.artifact_origin') IS NOT NULL "
            f"OR json_type({remote},'$.artifact_files') IS NOT NULL "
            f"OR json_type({remote},'$.artifact_checkpoints') IS NOT NULL"
        ):
            data = self.runtime_data(job_id, attempt)
            try:
                value = self.runtime.document(data["binding"], data["inputs"], data["remote"])
            except (ValueError, TypeError, KeyError, AttributeError):
                fail()
            if value is None:
                fail()
            self.runtime_files(value, data, job_id)

    def histories(self):
        previous_result, previous_id, sequence, source_id = None, None, 0, None
        for version_id, result_id, current, predecessor, source, recording_id in self.rows(
            "SELECT v.id,v.result_id,v.sequence,v.predecessor_id,"
            "v.source_version_id,t.recording_id "
            "FROM result_versions v JOIN transcript_results t ON t.id=v.result_id "
            "ORDER BY v.result_id COLLATE BINARY,v.sequence"
        ):
            identifier(version_id)
            identifier(result_id)
            integer(current, minimum=1)
            if result_id != previous_result:
                previous_id, sequence, source_id = None, 0, source
            if current != sequence + 1 or predecessor != previous_id or source != source_id:
                fail()
            if source is not None:
                self.version_reference(source, recording_id)
            raw = self.text("result_versions", "payload", "id", version_id)
            expected = self.text("result_versions", "payload_sha256", "id", version_id, maximum=64)
            try:
                self.payload.inspect_result_payload(
                    raw.encode("utf-8"), expected_sha256=expected, guard=self.guard
                )
            except ValueError as error:
                fail("resource" if "resource budget" in str(error) else "semantics")
            context = self.json(self.text("result_versions", "context", "id", version_id))
            if type(context) is not dict:
                fail()
            self.producer(context, recording_id, result_id, version_id, expected, current)
            self.timing_receipt(context, recording_id, result_id)
            self.runtime_receipt(context, recording_id, result_id)
            self.capture_receipt(context, recording_id, result_id)
            previous_result, previous_id, sequence = result_id, version_id, current
            self.counts["result_versions"] += 1
        for result_id, recording_id, job_id in self.rows(
            "SELECT id,recording_id,job_id FROM transcript_results ORDER BY id COLLATE BINARY"
        ):
            identifier(result_id)
            head = self.one(
                "SELECT payload_sha256 FROM result_versions WHERE result_id=? "
                "ORDER BY sequence DESC LIMIT 1",
                (result_id,),
            )
            if head is None or self.projection("transcript_results", result_id)[0] != head[0]:
                fail()
            source = self.json(self.text("transcript_results", "source", "id", result_id))
            if type(source) is not dict:
                fail()
            if "provider_artifacts" in source or "native_artifacts" in source:
                fail()
            if "artifact_evidence" in source:
                self.artifact(source["artifact_evidence"], recording_id, result_id)
            if job_id is not None:
                identifier(job_id)
                job = self.one("SELECT recording_id FROM jobs WHERE id=?", (job_id,))
                # Producer journals without a live job are retained evidence, not live authority.
                if job is not None and job[0] != recording_id:
                    fail()
            self.counts["results"] += 1

    def mirrors(self):
        for recording_id, active, revision, notes_revision, collection_id, project_id in self.rows(
            "SELECT id,active_transcript_id,revision,notes_revision,collection_id,project_id "
            "FROM recordings ORDER BY id COLLATE BINARY"
        ):
            identifier(recording_id)
            identifier(project_id)
            integer(revision)
            integer(notes_revision)
            digest, empty = self.projection("recordings", recording_id)
            if active is None:
                if not empty:
                    fail()
            else:
                identifier(active)
                row = self.one(
                    "SELECT t.recording_id,v.payload_sha256 FROM transcript_results t "
                    "JOIN result_versions v ON v.result_id=t.id WHERE t.id=? "
                    "AND NOT EXISTS(SELECT 1 FROM result_trash x WHERE x.result_id=t.id) "
                    "ORDER BY v.sequence DESC LIMIT 1",
                    (active,),
                )
                if row is None or row != (recording_id, digest):
                    fail()
            if collection_id is not None and self.one(
                "SELECT project_id FROM collections WHERE id=?", (collection_id,)
            ) != (project_id,):
                fail()
            self.counts["recordings"] += 1

    def notes(self):
        for table, scope in (("projects", "project"), ("recordings", "recording")):
            selection = "id,notes_revision" + (",project_id" if table == "recordings" else ",id")
            for owner_id, revision, project_id in self.rows(f'SELECT {selection} FROM "{table}"'):
                identifier(owner_id)
                integer(revision)
                current = self.text(table, "notes", "id", owner_id)
                previous, head_text, head_revision = None, None, None
                column = "recording_id" if table == "recordings" else "project_id"
                for version_id, number, stored_project, recording_id in self.rows(
                    f"SELECT id,notes_revision,project_id,recording_id FROM note_versions "
                    f"WHERE scope=? AND {column}=? ORDER BY notes_revision",
                    (scope, owner_id),
                ):
                    integer(number)
                    if (
                        stored_project != project_id
                        or (
                            recording_id != owner_id
                            if scope == "recording"
                            else recording_id is not None
                        )
                        or (previous is not None and number != previous + 1)
                    ):
                        fail()
                    raw = self.text("note_versions", "text", "id", version_id)
                    expected = self.text(
                        "note_versions", "text_sha256", "id", version_id, maximum=64
                    )
                    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected:
                        fail()
                    previous, head_text, head_revision = number, raw, number
                    self.counts["note_versions"] += 1
                if head_revision != revision or head_text != current:
                    fail()

    def document(self, table, key, column, digest_column, format_name):
        raw = self.text(table, column, "id", key)
        expected = self.text(table, digest_column, "id", key, maximum=64)
        value = self.json(raw)
        if (
            type(value) is not dict
            or value.get("format") != format_name
            or hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected
            or self.hash_value(value) != expected
        ):
            fail()

    def annotations(self):
        previous_owner, previous_id, sequence = None, None, 0
        for key, project_id, number, predecessor in self.rows(
            "SELECT id,project_id,sequence,predecessor_id FROM codebook_versions "
            "ORDER BY project_id COLLATE BINARY,sequence"
        ):
            integer(number, minimum=1)
            if project_id != previous_owner:
                previous_id, sequence = None, 0
            if number != sequence + 1 or predecessor != previous_id:
                fail()
            self.document(
                "codebook_versions", key, "payload", "payload_sha256", "cbs-topic-codebook-v1"
            )
            previous_owner, previous_id, sequence = project_id, key, number
        previous_owner, sequence = None, 0
        for (
            key,
            project,
            recording,
            number,
            result,
            version,
            source_hash,
            codebook,
            code_hash,
        ) in self.rows(
            "SELECT id,project_id,recording_id,sequence,result_id,source_version_id,"
            "source_payload_sha256,codebook_version_id,codebook_payload_sha256 "
            "FROM annotation_runs "
            "ORDER BY recording_id COLLATE BINARY,sequence"
        ):
            integer(number, minimum=1)
            if recording != previous_owner:
                sequence = 0
            if number != sequence + 1:
                fail()
            if self.one("SELECT project_id FROM recordings WHERE id=?", (recording,)) != (project,):
                fail()
            if self.one("SELECT result_id FROM result_versions WHERE id=?", (version,)) != (
                result,
            ):
                fail()
            self.version_reference(version, recording, source_hash)
            if self.one(
                "SELECT project_id,payload_sha256 FROM codebook_versions WHERE id=?", (codebook,)
            ) != (project, code_hash):
                fail()
            for column, expected_type in (("passage_ids", list), ("producer", dict)):
                if (
                    type(self.json(self.text("annotation_runs", column, "id", key)))
                    is not expected_type
                ):
                    fail()
            previous_owner, sequence = recording, number
        previous_owner, sequence = None, 0
        for key, run_id, number, present, digest in self.rows(
            "SELECT id,run_id,sequence,suggestion IS NOT NULL,suggestion_sha256 "
            "FROM annotation_items ORDER BY run_id COLLATE BINARY,sequence"
        ):
            integer(number, minimum=1)
            if run_id != previous_owner:
                sequence = 0
            if number != sequence + 1 or bool(present) != (digest is not None):
                fail()
            if present:
                self.document(
                    "annotation_items",
                    key,
                    "suggestion",
                    "suggestion_sha256",
                    "cbs-topic-annotation-v1",
                )
            previous_owner, sequence = run_id, number
        previous_owner, previous_id, sequence = None, None, 0
        for key, item, number, predecessor in self.rows(
            "SELECT id,item_id,revision,predecessor_id FROM annotation_review_events "
            "ORDER BY item_id COLLATE BINARY,revision"
        ):
            integer(number, minimum=1)
            if item != previous_owner:
                previous_id, sequence = None, 0
            if number != sequence + 1 or predecessor != previous_id:
                fail()
            self.document(
                "annotation_review_events",
                key,
                "payload",
                "payload_sha256",
                "cbs-topic-annotation-v1",
            )
            previous_owner, previous_id, sequence = item, key, number

    def mappings(self):
        manifest = self.request["manifest"]
        mappings = manifest["files"]
        if type(mappings) is not list or len(mappings) > MAPPING_LIMIT:
            fail("resource")
        originals = set()

        class Membership:
            def __init__(inner, sql, parameters=()):
                inner.sql, inner.parameters = sql, parameters

            def __contains__(inner, value):
                return self.one(inner.sql, (*inner.parameters, value)) is not None

        class AttemptMap:
            def get(inner, job_id, default):
                class Attempts:
                    def __contains__(index, attempt):
                        return (
                            self.one(
                                "SELECT 1 FROM runtime_batch_attempts a "
                                "JOIN runtime_batch_members m ON m.job_id=a.job_id "
                                "WHERE a.job_id=? "
                                "AND a.attempt=? AND m.recording_id=?",
                                (job_id, attempt, recording),
                            )
                            is not None
                        )

                return Attempts()

        runtime_ids = Membership("SELECT 1 FROM runtime_batch_runs WHERE id=?")
        restore_ids = set(self.restorations)
        for item in mappings:
            self.guard()
            project, recording = item["project_id"], item["recording_id"]
            if project is None and recording is None:
                try:
                    role = self.inventory.library_role(
                        item["relative_path"], runtime_ids=runtime_ids, restore_ids=restore_ids
                    )
                except ValueError:
                    fail("mapping")
            else:
                owner = self.one(
                    "SELECT project_id,filename FROM recordings WHERE id=?", (recording,)
                )
                if owner is None or owner[0] != project:
                    fail("mapping")
                expected = "original" + PurePosixPath(owner[1]).suffix.lower()
                job_ids = Membership(
                    "SELECT 1 FROM (SELECT recording_id,id AS job_id FROM jobs "
                    "UNION ALL SELECT recording_id,job_id FROM runtime_batch_members) "
                    "WHERE recording_id=? AND job_id=? LIMIT 1",
                    (recording,),
                )
                try:
                    role = self.inventory.recording_role(
                        item["relative_path"],
                        original=expected,
                        job_ids=job_ids,
                        attempts=AttemptMap(),
                    )
                except ValueError:
                    fail("mapping")
                if item["role"] == "original":
                    if item["relative_path"] != expected or recording in originals:
                        fail("mapping")
                    originals.add(recording)
            if role is None or role != item["role"]:
                fail("mapping")
            self.counts["file_mappings"] += 1
        for (recording_id,) in self.rows("SELECT id FROM recordings"):
            if recording_id not in originals:
                fail("mapping")
        absent_seen = set()
        for gap in manifest["legacy_gaps"]:
            if gap["code"] == "absent_owner_artifacts_not_retained":
                key = gap["recording_id"], gap["job_id"]
                if key not in self.absent_runtime or key in absent_seen:
                    fail("mapping")
                absent_seen.add(key)
                continue
            owner = self.one("SELECT 1 FROM recordings WHERE id=?", (gap["recording_id"],))
            if owner is None:
                fail("mapping")
            if gap["job_id"] is not None and self.one(
                "SELECT recording_id FROM jobs WHERE id=?", (gap["job_id"],)
            ) != (gap["recording_id"],):
                fail("mapping")
        if absent_seen != self.absent_runtime:
            fail("mapping")

    def roles(self):
        if self.request["manifest"]["schema_version"] < 14:
            return
        seen = 0
        for recording_id, working, revision in self.rows(
            "SELECT recording_id,working_version_id,revision FROM recording_result_roles"
        ):
            integer(revision, minimum=1)
            previous, number = None, 0
            for (
                current,
                before,
                before_hash,
                after,
                after_hash,
                action,
                actor,
                event_id,
            ) in self.rows(
                "SELECT sequence,previous_version_id,previous_payload_sha256,next_version_id,"
                "next_payload_sha256,action,actor_kind,id FROM result_role_events "
                "WHERE recording_id=? "
                "ORDER BY sequence",
                (recording_id,),
            ):
                integer(current, minimum=1)
                if current != number + 1 or before != previous:
                    fail()
                stamp = self.text("result_role_events", "observed_at", "id", event_id, maximum=256)
                try:
                    if "\x00" in stamp or len(stamp) > 64:
                        fail()
                    if datetime.fromisoformat(stamp).tzinfo is None:
                        fail()
                except (TypeError, ValueError):
                    fail()
                for ref, digest in ((before, before_hash), (after, after_hash)):
                    if (ref is None) != (digest is None):
                        fail()
                    if ref is not None:
                        self.version_reference(ref, recording_id, digest)
                automatic = {"working_auto_first", "working_auto_successor", "working_auto_edit"}
                if action == "working_kept":
                    if actor != "local_user" or after != before or after_hash != before_hash:
                        fail()
                elif action in automatic:
                    if (
                        actor != "system"
                        or after is None
                        or after == before
                        or (action == "working_auto_first" and (before is not None or current != 1))
                        or (action == "working_auto_edit" and before is None)
                    ):
                        fail()
                elif action == "working_marked":
                    if actor != "local_user" or after is None or after == before:
                        fail()
                elif action in {"working_cleared", "working_trashed"}:
                    if actor != "local_user" or before is None or after is not None:
                        fail()
                else:
                    fail()
                previous, number = after, current
                seen += 1
            if number != revision or previous != working:
                fail()
            if working is not None:
                self.version_reference(working, recording_id)
                if (
                    self.one(
                        "SELECT 1 FROM result_trash x JOIN result_versions v "
                        "ON v.result_id=x.result_id WHERE v.id=?",
                        (working,),
                    )
                    is not None
                ):
                    fail()
        # Foreign keys do not require an event to have a matching state row.
        if (
            self.one(
                "SELECT 1 FROM result_role_events e WHERE NOT EXISTS "
                "(SELECT 1 FROM recording_result_roles r WHERE r.recording_id=e.recording_id) "
                "LIMIT 1"
            )
            is not None
        ):
            fail()
        self.counts["role_events"] = seen

    def working_authorities(self):
        """Validate sealed job authority without treating it as current execution authority."""
        for (
            job_id,
            recording_id,
            policy,
            source_id,
            source_hash,
            snapshot_hash,
            admission,
        ) in self.rows(
            "SELECT job_id,recording_id,policy,source_version_id,source_payload_sha256,"
            "source_snapshot_sha256,admission_sha256 FROM working_job_authorities"
        ):
            identifier(job_id)
            identifier(recording_id)
            job = self.one("SELECT recording_id,kind FROM jobs WHERE id=?", (job_id,))
            if job is None or job[0] != recording_id or policy not in {"result", "timing"}:
                fail()
            raw = self.text("working_job_authorities", "role_json", "job_id", job_id, maximum=4096)
            role = self.json(raw)
            if type(role) is not dict or set(role) != {"revision", "working"}:
                fail()
            integer(role["revision"])
            if role["revision"] >= 2**63 or canonical(role).decode("utf-8") != raw:
                fail()
            working = role["working"]
            if role["revision"] == 0:
                if working is not None:
                    fail()
            else:
                historical = self.one(
                    "SELECT next_version_id,next_payload_sha256 FROM result_role_events "
                    "WHERE recording_id=? AND sequence=?",
                    (recording_id, role["revision"]),
                )
                if historical is None:
                    fail()
                if working is None:
                    if historical != (None, None):
                        fail()
                elif type(working) is not dict or historical != (
                    working.get("version_id"),
                    working.get("payload_sha256"),
                ):
                    fail()
            if working is not None:
                if (
                    type(working) is not dict
                    or set(working) != {"version_id", "result_id", "sequence", "payload_sha256"}
                    or role["revision"] == 0
                ):
                    fail()
                self.version_reference(
                    working["version_id"], recording_id, working["payload_sha256"]
                )
                integer(working["sequence"], minimum=1)
                target = self.one(
                    "SELECT result_id,sequence FROM result_versions WHERE id=?",
                    (working["version_id"],),
                )
                if target != (working["result_id"], working["sequence"]):
                    fail()
            params = self.json(self.text("jobs", "params", "id", job_id))
            if type(params) is not dict:
                fail()
            intent, acceptance = params.get("input_intent"), params.get("input_acceptance")
            if intent is not None and acceptance is not None:
                fail()
            if admission is not None:
                if type(admission) is not str or re.fullmatch(r"[0-9a-f]{64}", admission) is None:
                    fail()
                if type(intent) is dict:
                    try:
                        accepted = {
                            key: intent[key]
                            for key in ("accepted", "source_snapshot", "baseline", "predecessor")
                        }
                        frozen_role = intent["baseline"]["working_role"]
                        source = intent["source_snapshot"]
                    except (KeyError, TypeError):
                        fail()
                    if (
                        self.hash_value(accepted) != admission
                        or intent.get("accepted_sha256") != admission
                        or type(accepted["accepted"]) is not dict
                        or accepted["accepted"].get("operation")
                        != ("transcription" if source_id is None else "enhancement")
                    ):
                        fail()
                elif type(acceptance) is dict:
                    frozen_role = acceptance.get("working_role")
                    source = params.get("source_snapshot")
                    if acceptance.get("accepted_sha256") != admission:
                        fail()
                else:
                    fail()
                if policy != "result" or job[1] not in {"transcribe", "enhance"}:
                    fail()
            else:
                source = params.get("source_snapshot")
                if policy == "timing":
                    detail = params.get("timing_working_policy")
                    if (
                        type(detail) is not dict
                        or set(detail) != {"format", "expected_role"}
                        or detail["format"] != "cbs-timing-working-v1"
                    ):
                        fail()
                    frozen_role = detail["expected_role"]
                else:
                    if params.get("working_role_policy") != "cbs-working-role-v1":
                        fail()
                    frozen_role = params.get("working_role")
                if intent is not None or acceptance is not None or job[1] != "enhance":
                    fail()
            if type(frozen_role) is not dict or canonical(frozen_role) != canonical(role):
                fail()
            if source_id is None:
                if (
                    policy != "result"
                    or job[1] not in {"transcribe", "enhance"}
                    or (job[1] == "enhance" and params.get("transcription") is not True)
                    or admission is None
                    or source is not None
                    or source_hash is not None
                    or snapshot_hash is not None
                ):
                    fail()
            else:
                if job[1] != "enhance" or type(source) is not dict:
                    fail()
                self.version_reference(source_id, recording_id, source_hash)
                source_result = self.one(
                    "SELECT result_id FROM result_versions WHERE id=?", (source_id,)
                )
                if (
                    source.get("id") != source_result[0]
                    or source.get("source_version_id") != source_id
                    or source.get("source_payload_sha256") != source_hash
                    or type(snapshot_hash) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", snapshot_hash) is None
                    or self.hash_value(source) != snapshot_hash
                ):
                    fail()
                if (
                    type(source.get("segments")) is not list
                    or type(source.get("speakers")) is not dict
                ):
                    fail()
                if policy == "result":
                    if (
                        self.hash_value(
                            {
                                "format": self.payload.FORMAT,
                                "segments": source["segments"],
                                "speakers": source["speakers"],
                            }
                        )
                        != source_hash
                    ):
                        fail()
                else:
                    saved = self.json(self.text("result_versions", "payload", "id", source_id))
                    if (
                        type(saved) is not dict
                        or type(saved.get("segments")) is not list
                        or canonical(source["speakers"]) != canonical(saved.get("speakers"))
                        or len(source["segments"]) > len(saved["segments"])
                    ):
                        fail()
                    remaining = iter(saved["segments"])
                    for item in source["segments"]:
                        self.guard()
                        needle = canonical(item)
                        if not any(needle == canonical(candidate) for candidate in remaining):
                            fail()

    def passage_reviews(self):
        # Bound every field before materializing rows from the immutable source. A retained
        # mark may describe an older or removed passage; its hash is metadata, not approval.
        if (
            self.one(
                "SELECT 1 FROM passage_reviews WHERE "
                "typeof(result_id)!='text' OR length(CAST(result_id AS BLOB))!=32 OR "
                "typeof(segment_id)!='text' OR length(CAST(segment_id AS BLOB))>800 OR "
                "typeof(passage_sha256)!='text' OR length(CAST(passage_sha256 AS BLOB))!=64 OR "
                "typeof(status)!='text' OR length(CAST(status AS BLOB))>12 OR "
                "typeof(revision)!='integer' OR typeof(updated)!='text' OR "
                "length(CAST(updated AS BLOB))>64 LIMIT 1"
            )
            is not None
        ):
            fail()
        previous, per_result, count = None, 0, 0
        for result_id, segment_id, digest, status, revision, stamp in self.rows(
            "SELECT result_id,segment_id,passage_sha256,status,revision,updated "
            "FROM passage_reviews ORDER BY result_id COLLATE BINARY,segment_id COLLATE BINARY"
        ):
            identifier(result_id)
            if result_id != previous:
                per_result = 0
                if self.one("SELECT 1 FROM transcript_results WHERE id=?", (result_id,)) is None:
                    fail()
            per_result += 1
            if per_result > 10000:
                fail("resource")
            if (
                not 1 <= len(segment_id) <= 200
                or "\0" in segment_id
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or status not in {"checked", "needs_review", "not_checked"}
            ):
                fail()
            integer(revision, minimum=1)
            try:
                if "\0" in stamp or datetime.fromisoformat(stamp).tzinfo is None:
                    fail()
            except (TypeError, ValueError):
                fail()
            self.bytes += 8 + sum(
                len(value.encode("utf-8"))
                for value in (result_id, segment_id, digest, status, stamp)
            )
            if self.bytes > SEMANTIC_BYTES:
                fail("resource")
            count += 1
            previous = result_id
        self.counts["passage_reviews"] = count

    def run(self):
        self.histories()
        self.job_receipts()
        self.runtime_attempts()
        self.mirrors()
        self.notes()
        self.annotations()
        self.mappings()
        self.roles()
        if self.request["manifest"]["schema_version"] >= 18:
            self.working_authorities()
        if self.request["manifest"]["schema_version"] >= 19:
            self.passage_reviews()
        if self.request["manifest"]["schema_version"] >= 17:
            for table, column in (
                ("projects", "default_language"),
                ("recordings", "language_override"),
            ):
                for language, revision in self.rows(
                    f"SELECT {column},language_revision FROM {table}"
                ):
                    if (
                        (
                            language is not None
                            and (
                                type(language) is not str
                                or re.fullmatch(r"[a-z]{2,8}", language) is None
                            )
                        )
                        or type(revision) is not int
                        or revision < 0
                    ):
                        fail()
        if self.request["manifest"]["schema_version"] >= 16:
            for row in self.rows(
                "SELECT id,frozen,journal,state,epoch,archived,connection_fingerprint "
                "FROM runtime_preparations"
            ):
                identifier(row[0])
                frozen, journal = self.json(row[1]), self.json(row[2])
                if (
                    type(frozen) is not dict
                    or frozen.get("policy") != "cbs-runtime-preparation-v1"
                    or type(journal) is not dict
                    or row[3]
                    not in {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
                    or type(row[4]) is not int
                    or row[4] < 0
                    or row[5] not in (0, 1)
                ):
                    fail()
                if type(row[6]) is not str or re.fullmatch(r"[a-f0-9]{64}", row[6]) is None:
                    fail()
        if self.request["manifest"]["schema_version"] >= 15:
            for kind, owner_id, restore_id, backup_id in self.rows(
                "SELECT kind,owner_id,restore_id,source_backup_id FROM restored_owners"
            ):
                if kind not in {"job", "native_batch", "runtime_batch"}:
                    fail()
                for value in (owner_id, restore_id, backup_id):
                    identifier(value)
                if (
                    restore_id not in self.restorations
                    or self.restorations[restore_id]["source_backup_id"] != backup_id
                ):
                    fail("mapping")
        return self.counts


def execute(request):
    if type(request) is not dict or set(request) != FIELDS or request["protocol"] != PROTOCOL:
        fail("protocol")
    manifest = request["manifest"]
    if type(manifest) is not dict or type(manifest.get("schema_version")) is not int:
        fail("input")
    version = manifest["schema_version"]
    if version not in CATALOGS:
        fail("input")
    base = module(
        "portable_database_base", source(request["base_source_b64"], 64 * 1024, BASE_SHA256)
    )
    payload = module(
        "portable_payload", source(request["payload_source_b64"], 32 * 1024, PAYLOAD_SHA256)
    )
    receipt_package = types.ModuleType("_portable_receipts")
    receipt_package.__path__ = []
    sys.modules[receipt_package.__name__] = receipt_package
    artifacts_name = receipt_package.__name__ + ".provenance_artifacts"
    artifacts = types.ModuleType(artifacts_name)
    artifacts.__package__ = receipt_package.__name__
    sys.modules[artifacts_name] = artifacts
    exec(
        compile(
            source(request["artifacts_source_b64"], 32 * 1024, ARTIFACTS_SHA256),
            "<captured-provenance-artifacts>",
            "exec",
        ),
        artifacts.__dict__,
    )
    capture_name = receipt_package.__name__ + ".local_asr_capture"
    capture = types.ModuleType(capture_name)
    capture.__package__ = receipt_package.__name__
    sys.modules[capture_name] = capture
    exec(
        compile(
            source(request["capture_source_b64"], 32 * 1024, CAPTURE_SHA256),
            "<captured-local-asr-capture>",
            "exec",
        ),
        capture.__dict__,
    )
    inventory = module(
        "portable_inventory", source(request["inventory_source_b64"], 32 * 1024, INVENTORY_SHA256)
    )
    transcripts_name = receipt_package.__name__ + ".transcripts"
    transcripts = module(
        transcripts_name, source(request["transcripts_source_b64"], 64 * 1024, TRANSCRIPTS_SHA256)
    )
    transcripts.__package__ = receipt_package.__name__
    sys.modules[transcripts_name] = transcripts
    bounded_import_parser(transcripts)
    history = types.ModuleType(receipt_package.__name__ + ".history")

    def encode_payload(segments, speakers):
        if type(segments) is not list or type(speakers) is not dict:
            fail()
        raw = canonical({"format": payload.FORMAT, "segments": segments, "speakers": speakers})
        return raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()

    history.encode_payload = encode_payload
    sys.modules[history.__name__] = history
    timing_name = receipt_package.__name__ + ".timing_capture"
    timing = types.ModuleType(timing_name)
    timing.__package__ = receipt_package.__name__
    sys.modules[timing_name] = timing
    exec(
        compile(
            source(request["timing_source_b64"], 64 * 1024, TIMING_SHA256),
            "<captured-timing-capture>",
            "exec",
        ),
        timing.__dict__,
    )
    runtime_name = receipt_package.__name__ + ".runtime_capture"
    runtime = types.ModuleType(runtime_name)
    runtime.__package__ = receipt_package.__name__
    sys.modules[runtime_name] = runtime
    exec(
        compile(
            source(request["runtime_source_b64"], 32 * 1024, RUNTIME_SHA256),
            "<captured-runtime-capture>",
            "exec",
        ),
        runtime.__dict__,
    )
    # API observations import only the captured artifacts, history shim and transcripts.
    provider_name = receipt_package.__name__ + ".provider_capture"
    provider = types.ModuleType(provider_name)
    provider.__package__ = receipt_package.__name__
    sys.modules[provider_name] = provider
    exec(
        compile(
            source(request["provider_source_b64"], 48 * 1024, PROVIDER_SHA256),
            "<captured-provider-capture>",
            "exec",
        ),
        provider.__dict__,
    )
    native_name = receipt_package.__name__ + ".native_capture"
    native = types.ModuleType(native_name)
    native.__package__ = receipt_package.__name__
    sys.modules[native_name] = native
    exec(
        compile(
            source(request["native_source_b64"], 48 * 1024, NATIVE_SHA256),
            "<captured-native-capture>",
            "exec",
        ),
        native.__dict__,
    )
    if type(request["objects_root"]) is not str or len(request["objects_root"]) > 4096:
        fail("input")
    _parent(Path(request["objects_root"]))
    raw_catalog = source(request["catalog_b64"], 128 * 1024, CATALOGS[version][0])
    structure = json.loads(raw_catalog)["structure"]
    raw_target_catalog = source(request["target_catalog_b64"], 128 * 1024, CATALOGS[19][0])
    target_structure = json.loads(raw_target_catalog)["structure"]
    if (
        hashlib.sha256(b"cbs-portable-schema-v1\0" + canonical(structure)).hexdigest()
        != CATALOGS[version][1]
    ):
        fail("input")

    codec = row_codec(request, raw_catalog, version)
    base.SCHEMA_FINGERPRINT = CATALOGS[version][1]
    base.PROTOCOL = PROTOCOL
    base.ERROR_CODES |= {"semantics", "mapping"}
    base._load_codec = lambda _: (codec, structure)
    validate, row_claims = base._validate_request, base._claims
    counts, required_audio = {}, []

    def validate_request(value, catalog):
        # The base validates numeric/path/row/deadline contracts; only its literal schema
        # scalar is adapted. The observed structure is independently exact-gated as 13/14.
        copied = {**value, "expected_claims": {**value["expected_claims"], "schema_version": 12}}
        validate(copied, catalog)

    def claims(db, value, active_codec, catalog, state):
        tables = row_claims(db, value, active_codec, catalog, state)
        semantic = Semantics(
            db,
            request,
            base,
            payload,
            catalog,
            capture,
            inventory,
            transcripts,
            timing,
            runtime,
            provider,
            native,
        )
        counts.update(semantic.run())
        required_audio.extend(
            [
                {"sha256": digest, "frames": frames}
                for digest, frames in sorted(semantic.audio.items())
            ]
        )
        return tables

    base._validate_request = validate_request
    base._claims = claims
    bounded_rows = base._bounded_rows

    def schema_rows(db, statement, maximum):
        # The original reader's schema-12 object ceiling is 56. Schema 14 has 60
        # exact approved objects; only this authored schema query gets a new bound.
        if statement == (
            "SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name LIMIT 56"
        ):
            statement = (
                "SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name LIMIT "
                + str(maximum + 1)
            )
        return bounded_rows(db, statement, maximum)

    base._bounded_rows = schema_rows
    # All fields below are rechecked by the pinned opener. Manifest paths are observations,
    # never opened; parent binds their exact transport bytes and role resolver separately.
    inner = {
        key: request[key]
        for key in (
            "protocol",
            "path",
            "expected_sha256",
            "expected_size",
            "deadline_ns",
            "source_witness",
            "codec_source_b64",
            "catalog_b64",
        )
    }
    inner["expected_claims"] = {
        "schema_version": version,
        "schema_fingerprint": manifest["database"]["schema_fingerprint"],
        "tables": manifest["database"]["tables"],
    }
    try:
        result = base.execute(inner)
    except base.WorkerError as error:
        fail(error.code)
    result.update(
        schema_version=version, semantic_counts=counts, required_audio=required_audio, policy=POLICY
    )
    if request["restore"] is not None:
        try:
            result["working_copy"] = prepare_working(
                request,
                base,
                codec,
                structure,
                payload,
                capture,
                inventory,
                transcripts,
                timing,
                runtime,
                target_structure,
                provider,
                native,
            )
        except base.WorkerError as error:
            fail(error.code)
    return result


def read_request(raw):
    if len(raw) > REQUEST_BYTES:
        fail("resource")
    depth, tokens, quoted, escaped, span = 0, 0, False, False, 0
    for byte in raw:
        if quoted:
            span += 1
            if span > 256 * 1024:
                fail("resource")
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted, span, tokens = True, 0, tokens + 1
        elif byte in (91, 123):
            depth, tokens = depth + 1, tokens + 1
            if depth > 16:
                fail("resource")
        elif byte in (93, 125):
            depth -= 1
        if tokens > 1_000_000:
            fail("resource")
    value = json.loads(
        raw, object_pairs_hook=pairs, parse_float=bad_number, parse_constant=bad_number
    )
    if canonical(value) != raw:
        fail("protocol")
    return value


def main():
    raw = sys.stdin.buffer.read(REQUEST_BYTES + 1)
    reply = {"protocol": PROTOCOL, "request_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        reply.update(status="ok", result=execute(read_request(raw)))
    except Refused as error:
        reply.update(status="error", code=error.code)
    except MemoryError:
        reply.update(status="error", code="resource")
    except BaseException:
        reply.update(status="error", code="processing")
    encoded = canonical(reply)
    if len(encoded) > 4 * 1024**2:
        encoded = canonical(
            {
                "protocol": PROTOCOL,
                "request_sha256": reply["request_sha256"],
                "status": "error",
                "code": "resource",
            }
        )
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
