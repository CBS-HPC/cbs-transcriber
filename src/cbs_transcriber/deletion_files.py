"""Recoverable filesystem staging for an already-authorized logical deletion.

The caller owns the Store lease and excludes new work while preparing/staging. This
module never deletes database rows: fresh rows decide whether to restore staged files
or finish cleanup, including after an uncertain SQLite commit.
"""

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .store import containment_path

MAX_RECORDINGS = 2000
MAX_JOURNALS = 256
MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_TREE_ENTRIES = 200000
_lock = threading.RLock()
_active = {}
# Microsoft MS-FSCC 2.1.2.1: exact Cloud Files tags identify sync placeholders,
# not name-surrogate redirects. Every other reparse tag remains unsupported.
_CLOUD_TAGS = {0x9000001A | (index << 12) for index in range(16)}


@contextmanager
def _guard(store):
    if not store._lease_depth:
        raise RuntimeError("Local deletion recovery requires the same Store's library lease.")
    yield


@contextmanager
def _action(store, operation_id):
    """Nonblocking operation ownership never waits while a caller holds snapshot_lock."""
    key, owner = (str(store.root), _identifier(operation_id)), threading.get_ident()
    with _lock:
        previous, depth = _active.get(key, (owner, 0))
        if previous != owner:
            raise ValueError("This local deletion is already being settled.")
        _active[key] = (owner, depth + 1)
    try:
        yield
    finally:
        with _lock:
            if depth:
                _active[key] = (owner, depth)
            else:
                _active.pop(key, None)


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _identifier(value):
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Invalid local deletion identity.")
    return value


def _path(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or "\x00" in value:
        raise ValueError("Invalid local deletion path.")
    path = Path(value)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        raise ValueError("Local deletion paths must be absolute and normalized.")
    return path


def _safe(path):
    """Reject links/reparse points before resolving or using any owned path."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if _redirect(info):
            raise ValueError("Local deletion paths cannot contain filesystem links.")
    if containment_path(path) != path:
        raise ValueError("Local deletion path ownership changed.")


def _redirect(info):
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    )
    return bool(reparse and getattr(info, "st_reparse_tag", None) not in _CLOUD_TAGS)


def _identity(path, kind):
    _safe(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not (stat.S_ISDIR(info.st_mode) if kind == "directory" else stat.S_ISREG(info.st_mode)):
        raise ValueError("Local deletion requires regular managed files and folders.")
    return [info.st_dev, info.st_ino]


def _sync_directory(path):
    # Windows does not support fsync on a directory descriptor. File writes are flushed;
    # these journals promise process-crash recovery, not storage-provider durability.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write_once(path, value):
    data = _canonical(value)
    _safe(path)
    if path.exists():
        if _read(path, len(data)) != data:
            raise ValueError("The local deletion receipt changed.")
        return
    with path.open("xb") as stream:
        if stream.write(data) != len(data):
            raise OSError("Incomplete local deletion receipt.")
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)


def _read(path, bound):
    _identity(path, "file")
    with path.open("rb") as stream:
        data = stream.read(bound + 1)
    if len(data) > bound:
        raise ValueError("The local deletion receipt exceeds its size limit.")
    return data


def _json(path, bound):
    data = _read(path, bound)
    try:
        value = json.loads(data)
        if _canonical(value) != data:
            raise ValueError("The local deletion receipt is not canonical.")
        return value
    except (UnicodeError, RecursionError, TypeError) as error:
        raise ValueError("Malformed local deletion receipt.") from error


def _journal(store, operation_id):
    folder = store.root / "local-deletions" / _identifier(operation_id)
    _safe(folder)
    return folder


def _expected(plan):
    project = plan["projects"][0]
    base = _path(project["storage_dir"]) if project["storage_dir"] else _path(plan["library_root"])
    paths = []
    for recording in plan["recordings"]:
        identifier = recording["id"]
        paths.extend(
            (
                (base / "recordings" / identifier, "directory"),
                (base / "exports" / identifier, "directory"),
            )
        )
    if plan["kind"] == "project" and project["storage_dir"]:
        paths.append((base / "project.json", "file"))
    return paths


def _validate(store, plan):
    fields = {
        "schema",
        "library_root",
        "operation_id",
        "kind",
        "target_id",
        "target_name",
        "created",
        "projects",
        "recordings",
        "entries",
    }
    if (
        not isinstance(plan, dict)
        or set(plan) != fields
        or type(plan["schema"]) is not int
        or plan["schema"] != 1
    ):
        raise ValueError("Unknown local deletion plan.")
    if plan["library_root"] != str(store.root) or plan["kind"] not in {"project", "recording"}:
        raise ValueError("The local deletion plan belongs to another library.")
    _identifier(plan["operation_id"])
    _identifier(plan["target_id"])
    if not isinstance(plan["target_name"], str) or len(plan["target_name"]) > 512:
        raise ValueError("Invalid local deletion caption.")
    if not isinstance(plan["created"], str) or len(plan["created"]) > 64:
        raise ValueError("Invalid local deletion timestamp.")
    if not isinstance(plan["projects"], list) or len(plan["projects"]) != 1:
        raise ValueError("Invalid local deletion project scope.")
    project = plan["projects"][0]
    if not isinstance(project, dict) or set(project) != {"id", "created", "storage_dir"}:
        raise ValueError("Invalid local deletion project identity.")
    _identifier(project["id"])
    if not isinstance(project["created"], str) or len(project["created"]) > 128:
        raise ValueError("Invalid local deletion project creation identity.")
    if project["storage_dir"] is not None and not isinstance(project["storage_dir"], str):
        raise ValueError("Invalid local deletion storage root.")
    if project["storage_dir"]:
        _safe(_path(project["storage_dir"]))
    recordings = plan["recordings"]
    if not isinstance(recordings, list) or len(recordings) > MAX_RECORDINGS:
        raise ValueError("Local deletion has too many recordings.")
    for row in recordings:
        if not isinstance(row, dict) or set(row) != {"id", "project_id", "created"}:
            raise ValueError("Invalid local deletion recording identity.")
        _identifier(row["id"])
        if (
            row["project_id"] != project["id"]
            or not isinstance(row["created"], str)
            or len(row["created"]) > 128
        ):
            raise ValueError("Invalid local deletion recording ownership.")
    identifiers = [row["id"] for row in recordings]
    if identifiers != sorted(set(identifiers)):
        raise ValueError("Duplicate or unordered local deletion recordings.")
    if plan["kind"] == "recording":
        if identifiers != [plan["target_id"]]:
            raise ValueError("The local deletion recording scope changed.")
    elif plan["target_id"] != project["id"]:
        raise ValueError("The local deletion project scope changed.")
    expected = _expected(plan)
    if not isinstance(plan["entries"], list) or len(plan["entries"]) != len(expected):
        raise ValueError("The local deletion path inventory changed.")
    for index, (entry, (original, kind)) in enumerate(zip(plan["entries"], expected, strict=True)):
        wrapper = original.parent / f".cbs-delete-{plan['operation_id']}-{index}"
        if not isinstance(entry, dict) or set(entry) != {
            "original",
            "wrapper",
            "payload",
            "kind",
            "existed",
            "identity",
        }:
            raise ValueError("Invalid local deletion path entry.")
        if (
            any(
                entry[key] != str(path)
                for key, path in (
                    ("original", original),
                    ("wrapper", wrapper),
                    ("payload", wrapper / "payload"),
                )
            )
            or entry["kind"] != kind
            or type(entry["existed"]) is not bool
        ):
            raise ValueError("The local deletion path authority changed.")
        identity = entry["identity"]
        if (not entry["existed"] and identity is not None) or (
            entry["existed"]
            and (
                not isinstance(identity, list)
                or len(identity) != 2
                or any(type(i) is not int or not 0 <= i < 2**128 for i in identity)
            )
        ):
            raise ValueError("Invalid local deletion file identity.")
        for path in (original, wrapper, wrapper / "payload"):
            _safe(path)
    if len(_canonical(plan)) > MAX_PLAN_BYTES:
        raise ValueError("The local deletion plan exceeds its size limit.")
    return plan


def _load(store, supplied):
    operation_id = supplied["operation_id"] if isinstance(supplied, dict) else supplied
    plan = _validate(store, _json(_journal(store, operation_id) / "plan.json", MAX_PLAN_BYTES))
    allowed = {"plan.json", "complete.json"} | {
        f"cleanup-started-{i}.json" for i in range(len(plan["entries"]))
    }
    for path in _journal(store, operation_id).iterdir():
        if path.name not in allowed:
            raise ValueError("Unexpected files remain in the local deletion journal.")
        _identity(path, "file")
    if isinstance(supplied, dict) and plan != supplied:
        raise ValueError("The supplied local deletion plan differs from its durable receipt.")
    return plan


def _facts(store, plan):
    """A read snapshot gives three outcomes; no phase receipt can imply SQL commit."""
    with store.connection() as db:
        db.execute("BEGIN")
        project = plan["projects"][0]
        current_project = db.execute(
            "SELECT id,created,storage_dir FROM projects WHERE id=?", (project["id"],)
        ).fetchone()
        if current_project is not None and dict(current_project) != project:
            return "mixed"
        if (
            project["storage_dir"]
            and db.execute(
                "SELECT 1 FROM projects WHERE storage_dir=? AND id<>? LIMIT 1",
                (project["storage_dir"], project["id"]),
            ).fetchone()
        ):
            return "mixed"
        present = 0
        for frozen in plan["recordings"]:
            current = db.execute(
                "SELECT id,project_id,created FROM recordings WHERE id=?", (frozen["id"],)
            ).fetchone()
            if current is not None:
                if dict(current) != frozen or current_project is None:
                    return "mixed"
                present += 1
        if plan["kind"] == "project":
            members = [
                row[0]
                for row in db.execute(
                    "SELECT id FROM recordings WHERE project_id=? ORDER BY id LIMIT ?",
                    (project["id"], MAX_RECORDINGS + 1),
                )
            ]
            if current_project is None and present == 0 and not members:
                return "absent"
            if current_project is not None and members == [r["id"] for r in plan["recordings"]]:
                return "live"
            return "mixed"
        return "live" if present else "absent"


def _binding(plan, index):
    return {
        "schema": 1,
        "plan_sha256": hashlib.sha256(_canonical(plan)).hexdigest(),
        "index": index,
        "wrapper": plan["entries"][index]["wrapper"],
    }


def _wrapper(plan, index, *, allow_empty=False):
    entry = plan["entries"][index]
    wrapper = Path(entry["wrapper"])
    if _identity(wrapper, "directory") is None:
        return False
    if allow_empty and not any(wrapper.iterdir()):
        return True  # Removing an empty wrapper cannot remove any staged data.
    if _json(wrapper / "marker.json", 4096) != _binding(plan, index):
        raise ValueError("The local deletion staging marker changed.")
    if {p.name for p in wrapper.iterdir()} - {"marker.json", "payload"}:
        raise ValueError("The local deletion staging folder contains unexpected files.")
    return True


def _rename(source, destination):
    """Atomic no-replace renames, including when sync recreates a restore destination."""
    if os.name == "nt":
        source.rename(destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function = libc.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        arguments = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise OSError(errno.ENOTSUP, "Exclusive local deletion renames are unavailable.")
    function.restype = ctypes.c_int
    if function(*arguments) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def prepare(store, operation_id, kind, target_id, recording_ids, paths, project_file=None):
    """Capture DB-owned paths and persist the immutable plan before any rename."""
    with _guard(store), _action(store, operation_id):
        _identifier(operation_id)
        _identifier(target_id)
        if kind not in {"recording", "project"}:
            raise ValueError("Choose a recording or project for local deletion.")
        identifiers = sorted(recording_ids)
        if len(identifiers) > MAX_RECORDINGS or len(set(identifiers)) != len(identifiers):
            raise ValueError("Invalid local deletion recording scope.")
        recordings = [
            store.one(
                "SELECT id,project_id,created FROM recordings WHERE id=?",
                (_identifier(identifier),),
            )
            for identifier in identifiers
        ]
        if kind == "recording" and identifiers != [target_id]:
            raise ValueError("The local deletion recording scope changed.")
        project_id = target_id if kind == "project" else recordings[0]["project_id"]
        project = store.one("SELECT id,created,storage_dir FROM projects WHERE id=?", (project_id,))
        table = "projects" if kind == "project" else "recordings"
        name = store.one(f"SELECT name FROM {table} WHERE id=?", (target_id,))["name"]
        plan = {
            "schema": 1,
            "library_root": str(store.root),
            "operation_id": operation_id,
            "kind": kind,
            "target_id": target_id,
            "target_name": name[:512],
            "created": datetime.now(UTC).isoformat(),
            "projects": [project],
            "recordings": recordings,
            "entries": [],
        }
        expected = _expected(plan)
        supplied = [Path(p) for p in paths]
        if project_file is not None:
            supplied.append(Path(project_file))
        if len(supplied) != len(expected) or set(supplied) != {p for p, _ in expected}:
            raise ValueError("Only exact database-owned local deletion paths may be staged.")
        for index, (original, entry_kind) in enumerate(expected):
            identity = _identity(original, entry_kind)
            wrapper = original.parent / f".cbs-delete-{operation_id}-{index}"
            if wrapper.exists():
                raise ValueError("A local deletion staging folder already exists.")
            plan["entries"].append(
                {
                    "original": str(original),
                    "wrapper": str(wrapper),
                    "payload": str(wrapper / "payload"),
                    "kind": entry_kind,
                    "existed": identity is not None,
                    "identity": identity,
                }
            )
        _validate(store, plan)
        if _facts(store, plan) != "live":
            raise ValueError("Local deletion database ownership changed.")
        folder = _journal(store, operation_id)
        folder.parent.mkdir(exist_ok=True)
        folder.mkdir()
        _sync_directory(folder.parent)
        _write_once(folder / "plan.json", plan)
        return plan


def stage(store, plan):
    """Move each existing target into its marked same-volume sibling wrapper."""
    with _guard(store), _action(store, plan["operation_id"]):
        plan = _load(store, plan)
        if _facts(store, plan) != "live":
            raise ValueError("Local deletion database ownership changed before staging.")
        for index, entry in enumerate(plan["entries"]):
            original, wrapper, payload = (
                Path(entry[k]) for k in ("original", "wrapper", "payload")
            )
            current = _identity(original, entry["kind"])
            if not entry["existed"]:
                if current is not None or wrapper.exists():
                    raise ValueError("An originally absent local deletion path appeared.")
                continue
            marked = _wrapper(plan, index)
            moved = _identity(payload, entry["kind"])
            if moved is not None:
                if not marked or moved != entry["identity"] or current is not None:
                    raise ValueError("Local deletion staging ownership changed.")
                continue
            if current != entry["identity"]:
                raise ValueError("A local deletion source is missing or changed.")
            if not marked:
                wrapper.mkdir()
                _sync_directory(wrapper.parent)
                _write_once(wrapper / "marker.json", _binding(plan, index))
            _rename(original, payload)
            _sync_directory(original.parent)
            _sync_directory(wrapper)


def _retry_readonly(action, filename, error):
    path = Path(filename)
    _safe(path)
    if (
        os.name != "nt"
        or not isinstance(error, PermissionError)
        or action
        not in {
            os.unlink,
            os.rmdir,
        }
    ):
        raise error
    info = path.lstat()
    if not info.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY:
        raise error
    path.chmod(info.st_mode | stat.S_IWRITE)
    action(path)


def _unlink(path):
    try:
        path.unlink()
    except PermissionError as error:
        _retry_readonly(os.unlink, path, error)


def _staged_io_path(path):
    """Use Windows' long I/O spelling without changing canonical path authority."""
    value = os.fspath(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return path
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def _tree(path, kind):
    if _identity(path, kind) is None:
        raise ValueError("A staged local deletion payload is missing.")
    if kind == "file":
        return
    pending, count, device = [_staged_io_path(path)], 0, path.stat().st_dev
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > MAX_TREE_ENTRIES:
                    raise ValueError("The local deletion tree exceeds its entry limit.")
                # Windows DirEntry.stat reports st_dev=0; Path.lstat observes the
                # actual volume identity used by the payload-root mount guard.
                info = Path(entry.path).lstat()
                if info.st_dev != device or _redirect(info):
                    raise ValueError("A staged local deletion tree contains a link or mount.")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif not stat.S_ISREG(info.st_mode):
                    raise ValueError("A staged local deletion tree contains a special file.")


def _cleanup_receipt(store, plan, index):
    return _journal(store, plan["operation_id"]) / f"cleanup-started-{index}.json"


def _remove_wrapper(plan, index):
    wrapper = Path(plan["entries"][index]["wrapper"])
    if _wrapper(plan, index, allow_empty=True):
        if (wrapper / "payload").exists():
            raise ValueError("A local deletion payload still occupies its wrapper.")
        if (wrapper / "marker.json").exists():
            _unlink(wrapper / "marker.json")
        wrapper.rmdir()
        _sync_directory(wrapper.parent)


def _finish(store, plan):
    folder = _journal(store, plan["operation_id"])
    allowed = {"plan.json", "complete.json"} | {
        f"cleanup-started-{i}.json" for i in range(len(plan["entries"]))
    }
    if {p.name for p in folder.iterdir()} - allowed:
        raise ValueError("Unexpected files remain in the local deletion journal.")
    # This receipt authorizes only removal of journal metadata, never a data path.
    # Keep it until the plan is unlinked so every finalization interruption is retryable.
    _write_once(folder / "complete.json", _completion(plan))
    for path in sorted(
        folder.iterdir(), key=lambda p: 2 if p.name == "complete.json" else p.name == "plan.json"
    ):
        _identity(path, "file")
        _unlink(path)
    folder.rmdir()
    _sync_directory(folder.parent)


def _completion(plan):
    return {
        "schema": 1,
        "library_root": plan["library_root"],
        "operation_id": plan["operation_id"],
        "plan_sha256": hashlib.sha256(_canonical(plan)).hexdigest(),
    }


def _summary(plan, state, committed=None):
    return {
        "id": plan["operation_id"],
        "state": state,
        "committed": committed,
        "target_kind": plan["kind"],
        "target_name": plan["target_name"],
        "created": plan["created"],
        "updated": plan["created"],
        "message": {
            "complete": "Local file deletion recovery is complete.",
            "pending": "Deleted from the library; local file cleanup will retry. Close apps "
            "using these files and check folder permissions or sync.",
            "recovery_pending": "Local deletion recovery needs attention. Files and its "
            "journal were retained; no further library rows were deleted.",
        }[state],
    }


def _settle(store, plan):
    """Restore exact live targets or clean committed targets, using fresh SQL evidence."""
    with _guard(store):
        state = "recovery_pending"
        committed = None
        try:
            plan = _load(store, plan)
            with store.snapshot_lock:
                facts = _facts(store, plan)
                if facts == "mixed":
                    return _summary(plan, state)
                committed = facts == "absent"
                state = "pending" if facts == "absent" else "recovery_pending"
                completion = _journal(store, plan["operation_id"]) / "complete.json"
                completed = completion.exists()
                if completed and _json(completion, 4096) != _completion(plan):
                    raise ValueError("The local deletion completion receipt changed.")
                # Validate every entry before changing any filesystem state.
                for index, entry in enumerate(plan["entries"]):
                    current = _identity(Path(entry["original"]), entry["kind"])
                    payload = _identity(Path(entry["payload"]), entry["kind"])
                    receipt = _cleanup_receipt(store, plan, index)
                    cleaning = receipt.exists()
                    if cleaning and _json(receipt, 4096) != _binding(plan, index):
                        raise ValueError("The local deletion cleanup receipt changed.")
                    marked = _wrapper(
                        plan,
                        index,
                        allow_empty=payload is None
                        and (current == entry["identity"] or facts == "absent" and cleaning),
                    )
                    if not entry["existed"]:
                        if current is not None or marked or cleaning:
                            raise ValueError("An originally absent local deletion path appeared.")
                        continue
                    if current is not None and payload is not None:
                        raise ValueError("The local deletion restore destination is occupied.")
                    if current is not None and (facts != "live" or current != entry["identity"]):
                        raise ValueError("The local deletion original changed ownership.")
                    if payload is not None and (not marked or payload != entry["identity"]):
                        raise ValueError("The local deletion payload changed ownership.")
                    if (
                        current is None
                        and payload is None
                        and not (facts == "absent" and (cleaning or completed and not marked))
                    ):
                        raise ValueError("Both local deletion source and payload are missing.")
                if facts == "live":
                    for index, entry in enumerate(plan["entries"]):
                        if Path(entry["payload"]).exists():
                            _rename(Path(entry["payload"]), Path(entry["original"]))
                            _sync_directory(Path(entry["original"]).parent)
                        _remove_wrapper(plan, index)
                    _finish(store, plan)
                    return _summary(plan, "complete", committed)
            # Recursive validation/removal is deliberately outside snapshot/SQLite locks.
            for index, entry in enumerate(plan["entries"]):
                if Path(entry["payload"]).exists():
                    _verify_payload(plan, index)
                    _tree(Path(entry["payload"]), entry["kind"])
                    _verify_payload(plan, index)
            for index, entry in enumerate(plan["entries"]):
                if not entry["existed"]:
                    continue
                # Receipt only explains missing files on a later retry. SQL commit is
                # independently proved above on every call, never by this receipt.
                _write_once(_cleanup_receipt(store, plan, index), _binding(plan, index))
                payload = Path(entry["payload"])
                if payload.exists():
                    _verify_payload(plan, index)
                    if entry["kind"] == "directory":
                        shutil.rmtree(_staged_io_path(payload), onexc=_retry_readonly)
                    else:
                        _unlink(payload)
                _remove_wrapper(plan, index)
            _finish(store, plan)
            return _summary(plan, "complete", committed)
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
            try:
                return _summary(plan, state, committed)
            except (KeyError, TypeError):
                return _unknown("local-deletions")


def _verify_payload(plan, index):
    entry = plan["entries"][index]
    if (
        not _wrapper(plan, index)
        or _identity(Path(entry["payload"]), entry["kind"]) != entry["identity"]
    ):
        raise ValueError("The staged local deletion payload changed ownership.")


def settle(store, plan):
    """Restore live files or finish committed cleanup; concurrent actions never wait."""
    with _guard(store):
        try:
            with _action(store, plan["operation_id"]):
                return _settle(store, plan)
        except (OSError, ValueError, KeyError, TypeError):
            try:
                return _summary(plan, "recovery_pending")
            except (KeyError, TypeError):
                return _unknown("local-deletions")


def _journals(store):
    folder = store.root / "local-deletions"
    _safe(folder)
    if not folder.exists():
        return []
    entries = []
    for entry in folder.iterdir():
        entries.append(entry.name)
        if len(entries) > MAX_JOURNALS:
            raise ValueError("Too many retained local deletion journals.")
    return sorted(entries)


def _unknown(identifier):
    return {
        "id": identifier[:64],
        "state": "recovery_pending",
        "committed": None,
        "target_kind": "unknown",
        "target_name": "Local deletion",
        "created": "",
        "updated": "",
        "message": "A local deletion journal could not be verified. Its files were retained.",
    }


def recover(store):
    """Startup settlement under the caller's existing lease; never logical deletion."""
    with _guard(store):
        try:
            identifiers = _journals(store)
        except (OSError, ValueError):
            return [_unknown("local-deletions")]
        results = []
        for identifier in identifiers:
            results.append(retry(store, identifier))
        return results


def _retry(store, operation_id):
    """Settle one validated durable operation without accepting caller path authority."""
    with _guard(store):
        try:
            folder = _journal(store, operation_id)
            if not folder.exists():
                result = _unknown(operation_id)
                result.update(state="complete", message="Local file deletion recovery is complete.")
                return result
            if not (folder / "plan.json").exists() and {p.name for p in folder.iterdir()} == {
                "complete.json"
            }:
                # The last remaining item is a bounded, root-bound metadata receipt.
                # Do not infer SQL commit or recover any data paths from its contents.
                receipt = _json(folder / "complete.json", 4096)
                if (
                    not isinstance(receipt, dict)
                    or set(receipt) != {"schema", "library_root", "operation_id", "plan_sha256"}
                    or type(receipt["schema"]) is not int
                    or receipt["schema"] != 1
                    or receipt["library_root"] != str(store.root)
                    or receipt["operation_id"] != operation_id
                    or not isinstance(receipt["plan_sha256"], str)
                    or len(receipt["plan_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in receipt["plan_sha256"])
                ):
                    raise ValueError("Malformed final local deletion metadata receipt.")
                _unlink(folder / "complete.json")
            if _identity(folder, "directory") is not None and not any(folder.iterdir()):
                # Crash after the final plan unlink: there is no file/path authority to
                # execute, and rmdir cannot discard any data from an empty directory.
                folder.rmdir()
                _sync_directory(folder.parent)
                result = _unknown(operation_id)
                result.update(state="complete", message="Local file deletion recovery is complete.")
                return result
            return settle(store, _load(store, operation_id))
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
            return _unknown(operation_id)


def retry(store, operation_id):
    """Retry exactly one journal; a concurrent action retains it without waiting."""
    with _guard(store):
        try:
            with _action(store, operation_id):
                return _retry(store, operation_id)
        except (OSError, ValueError, KeyError, TypeError):
            return _unknown(operation_id)


def journal_status(store):
    """Read-only summaries for retained Activity cards and recovery admission gates."""
    with _guard(store):
        try:
            identifiers = _journals(store)
        except (OSError, ValueError):
            return [_unknown("local-deletions")]
        results = []
        for identifier in identifiers:
            try:
                plan = _load(store, identifier)
                facts = _facts(store, plan)
                results.append(
                    _summary(
                        plan,
                        "pending" if facts == "absent" else "recovery_pending",
                        True if facts == "absent" else False if facts == "live" else None,
                    )
                )
            except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
                results.append(_unknown(identifier))
        return results
