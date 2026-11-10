"""Which library the launcher opens, and the libraries this computer knows (ADR 0051).

A small registry in <home>/CBS Transcriber/libraries.json remembers the last opened library and
recent ones (paths only). Discovery also finds libraries at the earlier hidden defaults,
including folders a packaged app (for example Codex) had redirected. Inspection is read-only:
it never migrates, locks or changes another library (SQLite may add its usual -wal/-shm files).
"""

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .storage import app_folder, legacy_library_root, synced

DATABASE = "library.sqlite3"
RECENT = 10
HIDDEN_LIMIT = 100


def visibility_path():
    """Visibility is separate from the existing recent/current registry."""
    return app_folder() / "hidden-libraries.json"


def _visibility(*, strict=False):
    try:
        _identity(visibility_path())
        with visibility_path().open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("Hidden library settings are too large.")
        value = json.loads(raw)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        if strict:
            raise ValueError(
                "Hidden library settings could not be read; no libraries were changed."
            ) from exc
        return []
    if (
        not isinstance(value, list)
        or len(value) > HIDDEN_LIMIT
        or any(
            not isinstance(item, dict)
            or set(item) != {"path", "token"}
            or not isinstance(item["path"], str)
            or not 0 < len(item["path"]) <= 2000
            or "\x00" in item["path"]
            or not Path(item["path"]).is_absolute()
            or not isinstance(item["token"], str)
            or len(item["token"]) != 64
            or any(ch not in "0123456789abcdef" for ch in item["token"])
            for item in value
        )
    ):
        if strict:
            raise ValueError("Hidden library settings are invalid; no libraries were changed.")
        return []
    return value


def _visibility_token(path):
    """Bind a list action to the actual directory and database, not just a path."""
    path = Path(path)
    directory = _identity(path, directory=True)
    database = _identity(path / DATABASE)
    value = json.dumps([str(path), directory, database], separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hidden(path, entries):
    try:
        token = _visibility_token(path)
        return any(item == {"path": str(path), "token": token} for item in entries)
    except (OSError, ValueError):
        return False


def set_hidden(path, token, *, hidden, current=None):
    """Change discovery visibility only; keep the library, files and work intact."""
    from .portable_restore import operation_lock

    if not isinstance(path, str) or not isinstance(token, str):
        raise ValueError("Choose a library from the current list.")
    target = Path(path)
    if not target.is_absolute():
        raise ValueError("Choose a library from the current list.")
    target = target.resolve()
    if current is not None and target == Path(current).resolve():
        raise ValueError("The open library cannot be removed from the list.")
    if target not in candidates(include_hidden=True):
        raise ValueError("This library is no longer in the current list.")
    if hidden and in_use(target):
        raise ValueError("This library is open in another CBS Transcriber window.")
    location = visibility_path()
    location.parent.mkdir(parents=True, exist_ok=True)
    with operation_lock(target / "server.lock") if hidden else nullcontext():
        _set_visibility_locked(target, token, hidden, location, operation_lock)


def _set_visibility_locked(target, token, hidden, location, operation_lock):
    with operation_lock(location.with_suffix(".lock")):
        if location.exists() or location.is_symlink():
            _identity(location)
        entries = _visibility(strict=True)
        if not is_library(target) or _visibility_token(target) != token:
            raise ValueError("The library changed. Refresh the list and try again.")
        entries = [item for item in entries if item["path"] != str(target)]
        if hidden:
            if len(entries) >= HIDDEN_LIMIT:
                raise ValueError("Too many libraries are hidden. Show one again first.")
            entries.append({"path": str(target), "token": token})
        encoded = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
        if len(encoded) > 128 * 1024:
            raise ValueError("Hidden library settings are full. Show a library again first.")
        pending = location.with_name(location.name + "." + uuid.uuid4().hex + ".pending")
        created = False
        try:
            with pending.open("xb") as stream:
                created = True
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(location)
        finally:
            if created:
                pending.unlink(missing_ok=True)


def _show_if_hidden(root):
    root = Path(root).resolve()
    entries = _visibility()
    if _hidden(root, entries):
        set_hidden(str(root), _visibility_token(root), hidden=False)


@dataclass(frozen=True)
class SelectedLibrary:
    """An in-process handoff, never a persisted library or content identity."""

    path: Path
    directory_identity: tuple[int, int] | None
    database_identity: tuple[int, int] | None

    def __str__(self):
        return str(self.path)


def _identity(path, *, directory=False):
    info = path.lstat()
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or path.is_symlink()
        or path.is_junction()
    ):
        raise ValueError("The selected library path changed. Choose the library again.")
    return info.st_dev, info.st_ino


def select(path, *, create=False, schema):
    """Bind a checked chooser/switch target across the old server's shutdown."""
    folder = check(path, create=create, schema=schema)
    directory = _identity(folder, directory=True) if folder.exists() else None
    database = _identity(folder / DATABASE) if is_library(folder) else None
    if database is None and not create:
        raise ValueError("The selected library disappeared. Choose the library again.")
    return SelectedLibrary(folder, directory, database)


def recheck(selected, *, schema):
    """Refuse changed/newly occupied targets before constructing Store can write there."""
    current = select(str(selected.path), create=selected.database_identity is None, schema=schema)
    if current != selected:
        raise ValueError("The selected library path changed. Choose the library again.")
    return selected.path


def registry_path():
    return app_folder() / "libraries.json"


def load():
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"current": None, "recent": []}
    if not isinstance(data, dict):
        return {"current": None, "recent": []}
    current = data.get("current") if isinstance(data.get("current"), str) else None
    entries = data.get("recent", [])
    recent = (
        [item for item in entries if isinstance(item, str)] if isinstance(entries, list) else []
    )
    return {"current": current, "recent": recent[:RECENT]}


def remember(root):
    """Record the library that just opened as current and most recent (atomic write)."""
    root = str(Path(root).resolve())
    _show_if_hidden(root)
    data = load()
    recent = [root, *(item for item in data["recent"] if item != root)][:RECENT]
    target = registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix(".pending")
    pending.write_text(
        json.dumps({"version": 1, "current": root, "recent": recent}, indent=2), encoding="utf-8"
    )
    pending.replace(target)


def is_library(folder):
    try:
        return (Path(folder) / DATABASE).is_file() and not (
            Path(folder) / "restore-pending"
        ).exists()
    except OSError:
        return False


def forget(root):
    """Drop a permanently deleted library from the remembered list (only after commit)."""
    root = str(Path(root).resolve())
    data = load()
    recent = [item for item in data["recent"] if os.path.normcase(item) != os.path.normcase(root)]
    current = data["current"]
    if current is not None and os.path.normcase(current) == os.path.normcase(root):
        current = None
    if recent == data["recent"] and current == data["current"]:
        return
    target = registry_path()
    pending = target.with_suffix(".pending")
    pending.write_text(
        json.dumps({"version": 1, "current": current, "recent": recent}, indent=2),
        encoding="utf-8",
    )
    pending.replace(target)


def _reserved(path):
    """A library folder with an unfinished permanent deletion is never offered (ADR 0144)."""
    from .library_deletion import reserved

    return reserved(path)


def register(root):
    """Offer a newly verified restored library without switching the currently open one."""
    root = str(Path(root).resolve())
    data = load()
    recent = [root, *(item for item in data["recent"] if item != root)][:RECENT]
    target = registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix(".pending")
    pending.write_text(
        json.dumps({"version": 1, "current": data["current"], "recent": recent}, indent=2),
        encoding="utf-8",
    )
    pending.replace(target)


def default_new():
    return app_folder() / "Library"


def redirected_roots():
    """Earlier defaults that packaged apps redirected (Windows MSIX, macOS containers)."""
    patterns = []
    local = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and local:
        patterns.append((Path(local) / "Packages", "*/LocalCache/Local/CBS Transcriber"))
    containers = Path.home() / "Library" / "Containers"
    patterns.append((containers, "*/Data/Library/Application Support/CBS Transcriber"))
    found = []
    for base, pattern in patterns:
        try:
            found.extend(path for path in base.glob(pattern) if is_library(path))
        except OSError:
            continue
    return sorted(found)


def candidates(*, include_hidden=False):
    """Existing libraries, most relevant first: current, recent, defaults, redirected copies."""
    data = load()
    hidden = _visibility()
    ordered = [
        *(Path(item) for item in ([data["current"]] if data["current"] else [])),
        *(Path(item) for item in data["recent"]),
        default_new(),
        legacy_library_root(),
        *redirected_roots(),
        *([Path(item["path"]) for item in hidden] if include_hidden else []),
    ]
    seen, found = set(), []
    for path in ordered:
        try:
            key = str(path.resolve()).lower() if os.name == "nt" else str(path.resolve())
        except OSError:
            continue
        if (
            key not in seen
            and is_library(path)
            and (include_hidden or not _hidden(path.resolve(), hidden))
            and not _reserved(path)
        ):
            seen.add(key)
            found.append(path.resolve())
    return found


def startup_library(force=False, *, schema):
    """A bound existing/new-default choice, or None when the user should choose."""
    current = load()["current"]
    if (
        not force
        and current
        and is_library(current)
        and not _hidden(Path(current).resolve(), _visibility())
        and not _reserved(current)
    ):
        return select(current, schema=schema)
    found = candidates()
    if force or len(found) > 1 or (not found and candidates(include_hidden=True)):
        return None
    return (
        select(str(found[0]), schema=schema)
        if found
        else select(str(default_new()), create=True, schema=schema)
    )


def describe(path):
    """Read-only facts for choosing: kind, counts, last change; never opens for writing."""
    path = Path(path)
    facts = {"path": str(path), "kind": kind(path), "synced": synced(path)}
    database = path / DATABASE
    try:
        info = database.stat()
        if not stat.S_ISREG(info.st_mode):
            return {**facts, "exists": False}
        facts["exists"] = True
        facts["modified"] = datetime.fromtimestamp(info.st_mtime, UTC).isoformat()
        db = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1)
        try:
            facts["schema"] = db.execute("PRAGMA user_version").fetchone()[0]
            facts["projects"] = db.execute("SELECT count(*) FROM projects").fetchone()[0]
            facts["recordings"] = db.execute("SELECT count(*) FROM recordings").fetchone()[0]
        finally:
            db.close()
    except FileNotFoundError:
        return {**facts, "exists": False}
    except (OSError, sqlite3.Error):
        facts["exists"] = True
        facts["unreadable"] = True
    return facts


def kind(path):
    path = Path(path).resolve()
    if path == default_new().resolve():
        return "Default library"
    if path == legacy_library_root().resolve():
        return "Earlier default (hidden app data)"
    if "Packages" in path.parts and "LocalCache" in path.parts:
        package = path.parts[path.parts.index("Packages") + 1].split("_")[0]
        return f"App data redirected by {package}"
    if "Containers" in path.parts:
        return "App data redirected by a macOS app container"
    return "Library folder"


def in_use(folder):
    """True when another server holds this library's lease; never creates a lock file."""
    lock_path = Path(folder) / "server.lock"
    if not lock_path.is_file():
        return False
    try:
        with lock_path.open("r+b") as lock:
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return True
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        return True
    return False


def check(path, *, create=False, schema):
    """Validate a library to open, or an empty folder for a new one; return its absolute path."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Choose a library folder.")
    folder = Path(path.strip()).expanduser()
    if not folder.is_absolute():
        raise ValueError("Choose an absolute library folder.")
    folder = folder.resolve()
    from .library_deletion import require_unreserved

    # Before any directory or database exists: never recreate a library being deleted.
    require_unreserved(folder, ValueError)
    if is_library(folder):
        facts = describe(folder)
        if not facts.get("exists") or facts.get("unreadable"):
            raise ValueError("This folder's library database cannot be read.")
        if facts["schema"] > schema:
            raise ValueError("This library was saved by a newer CBS Transcriber. Update the app.")
        if in_use(folder):
            raise ValueError("This library is already open in another CBS Transcriber window.")
        return folder
    if not create:
        raise ValueError(
            "No CBS Transcriber library in this folder. Choose an existing library, "
            "or create a new one in an empty folder."
        )
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ValueError("Create a new library in an empty or new folder.")
    if synced(folder):
        raise ValueError(
            "Cloud-synced folders (OneDrive, iCloud, Dropbox) can damage the library database. "
            "Choose a folder on this computer."
        )
    return folder


def known(current=None):
    """Libraries to offer in the chooser, with the open one first and flagged."""
    paths = candidates(include_hidden=True)
    hidden = _visibility()
    if current is not None:
        current = Path(current).resolve()
        paths = [current, *(path for path in paths if path != current)]
    listed = []
    for path in paths:
        try:
            token = _visibility_token(path)
        except (OSError, ValueError):
            token = None
        listed.append(
            {
                **describe(path),
                "current": path == current,
                "hidden": path != current and _hidden(path, hidden),
                "visibility_token": token,
            }
        )
    return listed
