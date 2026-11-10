"""Verified local library backups (ADR 0061), the first step towards storage on UCloud.

A backup is a folder with a consistent SQLite snapshot of the running library, made with the
backup API, and a manifest. The manifest records the schema, row counts, the integrity checks
and every original recording with its size and SHA-256. Originals never change, so their
hashes are reused from the previous backup when size and modification time match. Keys and
tokens live in the OS credential store and are never part of the library; the server lock is
not copied.
"""

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

FORMAT = "cbs-library-backup-v1"
EXCLUDED = (
    "server.lock (the running server's lease)",
    "API keys and tokens (kept in the OS credential store, never in the library)",
    "media prepared for playback (recreated from the originals)",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(path):
    """Read legacy inventory metadata shapes; this grants no package/restore authority."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    tables = data.get("tables")
    if (
        not isinstance(data.get("created"), str)
        or not isinstance(tables, dict)
        or type(tables.get("recordings")) is not int
        or tables["recordings"] < 0
        or not isinstance(data.get("originals"), list)
        or not isinstance(data.get("missing_originals"), list)
    ):
        return None
    try:
        data["created"].encode("utf-8")
    except UnicodeEncodeError:
        return None
    return data


def previous_hashes(root):
    """Hashes from the newest earlier backup, keyed by recording, size and mtime."""
    known = {}
    for manifest in sorted(root.glob("library-*/manifest.json"), reverse=True):
        data = _manifest(manifest)
        if data is None:
            continue
        for item in data["originals"]:
            if not isinstance(item, dict):
                continue
            digest = item.get("sha256")
            if (
                not isinstance(item.get("recording_id"), str)
                or type(item.get("size")) is not int
                or item["size"] < 0
                or type(item.get("mtime_ns")) is not int
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                continue
            key = (item.get("recording_id"), item.get("size"), item.get("mtime_ns"))
            known.setdefault(key, digest)
        break
    return known


def verify(snapshot):
    """Read-only checks of a snapshot: integrity, foreign keys, schema and row counts."""
    # closing(): a `with connection` block only commits; an open handle would keep Windows
    # from removing a failed copy.
    with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = db.execute("PRAGMA foreign_key_check").fetchall()
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        recordings = db.execute(
            "SELECT id,project_id,filename FROM recordings ORDER BY created,id"
        ).fetchall()
    if integrity != "ok" or foreign:
        raise ValueError("The backup copy failed its integrity check; it was not kept.")
    return schema, counts, recordings


def create_backup(store, destination=None):
    """Write library-<time>/ with library.sqlite3 and manifest.json; return a summary."""
    local_files = store.root / "local-deletions"
    if (
        local_files.is_symlink()
        or local_files.is_junction()
        or (local_files.exists() and next(local_files.iterdir(), None) is not None)
    ):
        raise ValueError(
            "Finish local file recovery or cleanup in Activity before backing up this library."
        )
    root = Path(destination).expanduser() if destination else store.root / "backups"
    if not root.is_absolute():
        raise ValueError("Choose an absolute folder for the backup.")
    root.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC)
    folder = root / f"library-{created:%Y%m%dT%H%M%S%fZ}"
    folder.mkdir()
    snapshot = folder / "library.sqlite3"
    try:
        target = sqlite3.connect(snapshot)
        try:
            with store.connection() as db:
                db.backup(target)
        finally:
            target.close()
        schema, counts, recordings = verify(snapshot)
        known = previous_hashes(root)
        originals, missing = [], []
        for recording_id, project_id, filename in recordings:
            name = "original" + Path(filename).suffix.lower()
            path = store.directory(recording_id, project_id) / name
            item = {"recording_id": recording_id, "project_id": project_id, "file": name}
            try:
                stat = path.stat()
            except OSError:
                missing.append(item)
                continue
            key = (recording_id, stat.st_size, stat.st_mtime_ns)
            item |= {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": known.get(key) or sha256(path),
            }
            originals.append(item)
        manifest = {
            "format": FORMAT,
            "created": created.isoformat(),
            "schema": schema,
            "integrity": "ok",
            "tables": counts,
            "database_bytes": snapshot.stat().st_size,
            "originals": originals,
            "missing_originals": missing,
            "excluded": list(EXCLUDED),
        }
        pending = folder / "manifest.pending"
        pending.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        pending.replace(folder / "manifest.json")
    except BaseException:
        # A failed backup leaves nothing that could be mistaken for a good one; the original
        # error is what the researcher sees, even if removal also fails.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {
        "folder": str(folder),
        "created": manifest["created"],
        "schema": schema,
        "projects": counts.get("projects", 0),
        "recordings": counts.get("recordings", 0),
        "originals": len(originals),
        "original_bytes": sum(item["size"] for item in originals),
        "missing_originals": len(missing),
        "database_bytes": manifest["database_bytes"],
    }


def backups(store):
    """The library's own backups, newest first (read-only)."""
    found = []
    for manifest in sorted((store.root / "backups").glob("library-*/manifest.json"), reverse=True):
        data = _manifest(manifest)
        if data is not None:
            found.append(
                {
                    "folder": str(manifest.parent),
                    "created": data["created"],
                    "recordings": data["tables"].get("recordings", 0),
                    "originals": len(data["originals"]),
                    "missing_originals": len(data["missing_originals"]),
                }
            )
    return found[:20]
