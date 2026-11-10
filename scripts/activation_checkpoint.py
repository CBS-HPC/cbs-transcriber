"""Operator checkpoints, with read-only source connections and no activation commands.

Never imports Store/the app or reads credentials. A stage is a caller's label, not
proof of quiescence. Original bodies and recovery files are inventoried, not copied.
Keep the source library and managed project folders intact. Destinations are private.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import struct
import time
from contextlib import closing, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

FORMAT = "cbs-activation-checkpoint-v1"


def safe(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Use explicit absolute paths.")
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Checkpoint paths cannot contain links or reparse points.")
    return path.resolve()


def readonly(path):
    db = sqlite3.connect(safe(path).as_uri() + "?mode=ro", uri=True, timeout=5)
    db.execute("PRAGMA query_only=ON")
    return db


@contextmanager
def existing_lease(library):
    """Use the serving app's lock byte; never create, initialize or write the file."""
    path = safe(library / "server.lock")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("The existing server lease must be a regular file.")
    with path.open("r+b") as stream:
        if os.fstat(stream.fileno()).st_size < 1:
            raise ValueError("The existing server lease file is uninitialized.")
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def framed(value):
    if value is None:
        tag, body = b"N", b""
    elif type(value) is int:
        tag, body = b"I", str(value).encode()
    elif type(value) is float:
        tag, body = b"F", struct.pack(">d", value)
    elif isinstance(value, str):
        tag, body = b"T", value.encode("utf-8")
    elif isinstance(value, bytes):
        tag, body = b"B", value
    else:
        raise ValueError("Unsupported SQLite value type.")
    return tag + struct.pack(">Q", len(body)) + body


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def fingerprint(db):
    if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValueError("The snapshot failed its integrity check.")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("The snapshot failed its foreign-key check.")
    schema = hashlib.sha256()
    for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ):
        schema.update(b"R" + b"".join(framed(value) for value in row))
    tables = {}
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        columns = db.execute("PRAGMA table_info(" + quote(name) + ")").fetchall()
        names = [row[1] for row in columns]
        primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        ordered = ",".join(quote(column) for column in (primary or names))
        digest, count = hashlib.sha256(), 0
        for row in db.execute("SELECT * FROM " + quote(name) + " ORDER BY " + ordered):
            digest.update(b"R" + b"".join(framed(value) for value in row))
            count += 1
        tables[name] = {"columns": names, "rows": count, "sha256": digest.hexdigest()}
    return {
        "schema": db.execute("PRAGMA user_version").fetchone()[0],
        "schema_sha256": schema.hexdigest(),
        "tables": tables,
    }


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def file_receipt(path):
    path = safe(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("Expected a regular checkpoint input.")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as source:
        if identity(os.fstat(source.fileno())) != identity(before):
            raise ValueError("A checkpoint input changed identity.")
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > before.st_size:
                raise ValueError("A checkpoint input grew while reading.")
            digest.update(chunk)
        if identity(os.fstat(source.fileno())) != identity(before):
            raise ValueError("A checkpoint input changed while reading.")
    if identity(safe(path).stat()) != identity(before) or count != before.st_size:
        raise ValueError("A checkpoint input changed while reading.")
    return {"bytes": count, "sha256": digest.hexdigest()}


def originals(db, library, roots):
    inventory = {}
    for recording, project, filename, storage in db.execute(
        "SELECT r.id,r.project_id,r.filename,p.storage_dir FROM recordings r "
        "JOIN projects p ON p.id=r.project_id ORDER BY r.id"
    ):
        extension = Path(filename).suffix.lower()
        if not re.fullmatch(r"[a-f0-9]{32}", recording) or not re.fullmatch(
            r"(?:\.[a-z0-9]{1,10})?", extension
        ):
            raise ValueError("Invalid managed original identity.")
        base = Path(storage) if storage else library
        path = safe(base / "recordings" / recording / ("original" + extension))
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("An original is outside the explicitly approved media roots.")
        item = {"project_id": project, "path": str(path)}
        try:
            item.update(file_receipt(path), availability="present")
        except FileNotFoundError:
            item["availability"] = "missing"
        inventory[recording] = item
    return inventory


def recovery_inventory(library):
    """Hash local-deletion receipts without parsing/following any stored target path."""
    base, entries = safe(library / "local-deletions"), {}
    if not base.exists():
        return entries
    pending = [base]
    while pending:
        directory = pending.pop()
        for path in sorted(safe(directory).iterdir()):
            path = safe(path)
            if len(entries) >= 4096:
                raise ValueError("Recovery metadata exceeds the checkpoint entry limit.")
            relative = path.relative_to(base).as_posix()
            if path.is_dir():
                entries[relative] = {"kind": "directory"}
                pending.append(path)
            else:
                entries[relative] = {"kind": "file", **file_receipt(path)}
    return entries


def write_json(path, value):
    path = safe(path)
    if path.exists():
        raise FileExistsError(path)
    pending = path.with_name(path.name + ".pending")
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(pending, path)  # Publish create-if-absent; retain the pending file on failure.
    pending.unlink()


def capture(library, output, roots, stage, timeout=120):
    library = safe(library)
    with existing_lease(library) if stage == "offline" else nullcontext():
        return _capture(library, output, roots, stage, timeout)


def _capture(library, output, roots, stage, timeout):
    library, output = safe(library), safe(output)
    roots = [safe(root) for root in roots]
    if not roots or not all(root.is_dir() for root in roots):
        raise ValueError("Supply existing explicitly approved original roots.")
    if any(output.is_relative_to(root) for root in [library, *roots]):
        raise ValueError("The checkpoint destination must be outside all source roots.")
    source_path = safe(library / "library.sqlite3")
    if not source_path.is_file():
        raise ValueError("The source database must already exist.")
    output.mkdir()  # Exclusive: a failed checkpoint stays partial and is never overwritten.
    snapshot = output / "library.sqlite3"
    snapshot.touch(exist_ok=False)
    deadline = time.monotonic() + timeout

    def progress(status, remaining, pages):
        if time.monotonic() > deadline:
            raise ValueError("The SQLite backup exceeded its time budget.")

    with closing(readonly(source_path)) as source, closing(sqlite3.connect(snapshot)) as target:
        source.backup(target, pages=128, progress=progress, sleep=0.1)
        if target.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise ValueError("The copied database could not be made self-contained.")
    with closing(readonly(snapshot)) as db:
        database = fingerprint(db)
        active = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')"
        ).fetchone()[0]
        if active:
            raise ValueError(
                "Active local jobs observed; retain the partial copy and defer activation."
            )
        inventory = originals(db, library, roots)
    receipt = {
        "format": FORMAT,
        "created": datetime.now(UTC).isoformat(),
        "stage_label": stage,
        "source_library": str(library),
        "approved_original_roots": list(map(str, roots)),
        "database": {**database, **file_receipt(snapshot)},
        "originals": inventory,
        "missing_original_ids": [
            key for key, value in inventory.items() if value["availability"] == "missing"
        ],
        "local_recovery_files": recovery_inventory(library),
        "active_local_jobs_observed": active,
        "offline_server_lease_held": stage == "offline",
        "source_quiescence": "not_checked",
        "remote_obligations": "not_interpreted",
        "credentials": "not_read",
        "original_bodies_copied": False,
        "local_recovery_bodies_copied": False,
        "activation_authority": False,
        "restore_ready": False,
    }
    write_json(output / "checkpoint.json", receipt)
    return receipt


def load_checkpoint(folder):
    folder = safe(folder)
    raw = (folder / "checkpoint.json").read_bytes()
    receipt = json.loads(raw)
    if receipt.get("format") != FORMAT:
        raise ValueError("Unrecognized checkpoint format.")
    if file_receipt(folder / "library.sqlite3")["sha256"] != receipt["database"]["sha256"]:
        raise ValueError("The checkpoint database bytes changed.")
    with closing(readonly(folder / "library.sqlite3")) as db:
        observed = fingerprint(db)
    if any(observed[key] != receipt["database"][key] for key in observed):
        raise ValueError("The checkpoint database evidence changed.")
    receipt["observed_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
    return receipt


def compare(before, after, output):
    before, after = load_checkpoint(before), load_checkpoint(after)
    output = safe(output)
    for receipt in (before, after):
        for root in (receipt["source_library"], *receipt["approved_original_roots"]):
            if output.is_relative_to(Path(root)):
                raise ValueError("The comparison destination must be outside all source roots.")
    changed = [
        key
        for key in (
            "source_library",
            "approved_original_roots",
            "originals",
            "local_recovery_files",
        )
        if before[key] != after[key]
    ]
    changed += [
        "database." + key
        for key in ("schema", "schema_sha256", "tables")
        if before["database"][key] != after["database"][key]
    ]
    result = {
        "preservation_match": not changed,
        "changed_scopes": changed,
        "changed_tables": sorted(
            key
            for key in before["database"]["tables"].keys() | after["database"]["tables"].keys()
            if before["database"]["tables"].get(key) != after["database"]["tables"].get(key)
        ),
        "changed_original_ids": sorted(
            key
            for key in before["originals"].keys() | after["originals"].keys()
            if before["originals"].get(key) != after["originals"].get(key)
        ),
        "before_receipt_sha256": before["observed_receipt_sha256"],
        "after_receipt_sha256": after["observed_receipt_sha256"],
        "before_database_sha256": before["database"]["sha256"],
        "after_database_sha256": after["database"]["sha256"],
        "activation_authority": False,
        "restore_ready": False,
    }
    write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("capture")
    take.add_argument("--library", required=True, type=Path)
    take.add_argument("--output", required=True, type=Path)
    take.add_argument("--original-root", required=True, action="append", type=Path)
    take.add_argument("--stage", required=True, choices=("online", "offline", "post"))
    check = commands.add_parser("compare")
    for name in ("before", "after", "output"):
        check.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = capture(args.library, args.output, args.original_root, args.stage)
            print(
                json.dumps(
                    {
                        "checkpoint_created": True,
                        "originals": len(result["originals"]),
                        "missing_originals": len(result["missing_original_ids"]),
                        "activation_authority": False,
                    }
                )
            )
        else:
            result = compare(args.before, args.after, args.output)
            print(json.dumps(result))
            return 0 if result["preservation_match"] else 2
    except (OSError, ValueError, sqlite3.Error) as error:
        print(
            json.dumps(
                {
                    "checkpoint_failed": type(error).__name__,
                    "detail": str(error)
                    if isinstance(error, ValueError)
                    else "Inspect the partial checkpoint; no activation was attempted.",
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
