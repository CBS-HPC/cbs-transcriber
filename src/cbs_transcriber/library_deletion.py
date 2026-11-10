"""Permanent deletion of an inactive library and its exclusively owned files (ADR 0144).

Preview copies the target database into a private temporary folder and reads the copy, so
it never creates or changes anything inside the target, including ``server.lock``.
Execution holds the target's normal server lease, publishes a reservation outside the
target (next to the library registry), stages exact entries by same-volume renames into
marked sibling folders and commits before anything is removed. Recovery moves staged
entries back before commit and finishes removal after it; anything it cannot verify stays
reserved and visible with Retry. Project folders outside the library are deleted only when
this library exclusively owns them; shared, unverified or missing ones are kept and listed.
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import uuid
from contextlib import closing, contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

from .deletion_files import _redirect, _rename, _retry_readonly, _staged_io_path, _sync_directory
from .storage import app_folder
from .store import SCHEMA_VERSION, containment_path

SCHEMA = 1
DATABASE = "library.sqlite3"
WAL, SHM, HOT_JOURNAL = DATABASE + "-wal", DATABASE + "-shm", DATABASE + "-journal"
LOCK = "server.lock"
# The lease file and SQLite's shared-memory index change when anything merely opens the
# library; they are staged and removed but never part of the confirmed fingerprint.
VOLATILE = frozenset({LOCK, SHM, WAL})
PREFIX = ".cbs-deleting-"
MAX_ENTRIES = 500_000
MAX_CHILDREN = 10_000
MAX_JOURNALS = 64
MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_TRANSFER_BYTES = 16 * 1024 * 1024
# Everything the app itself creates directly inside a library folder.
APP_ENTRIES = frozenset(
    {
        DATABASE,
        WAL,
        SHM,
        HOT_JOURNAL,
        LOCK,
        "recordings",
        "exports",
        "projects",
        "backups",
        "local-deletions",
        "runtime-batches",
        "runtime-preparations",
        "portable-captures",
        "portable-transfers",
        "portable-downloads",
        "portable-retention-events",
        "portable-retention.json",
        "portable-restore-index",
        "portable-cloud-settings.json",
        "portable-cloud-settings.lock",
        "preservation",
        "ucloud-enhancement-known-hosts",
    }
)
# A managed project folder outside the library contains only these entries.
PROJECT_ENTRIES = frozenset({"recordings", "exports", "project.json", "project.pending"})
SETTLED_TRANSFERS = frozenset({"complete", "trashed", "abandoned"})
_HEX = re.compile(r"[0-9a-f]+\Z")
_PROJECT_JSON = re.compile(
    rb'\A\{\s*"format_version"\s*:\s*1\s*,\s*"project"\s*:\s*\{\s*"id"\s*:\s*"([0-9a-f]{32})"'
)
_thread_lock = threading.Lock()
_errors = {}

OPEN = (
    "This library is open in another CBS Transcriber window. Close that window, then check again."
)
PENDING = (
    "An earlier library deletion was interrupted. Choose Retry on it at the top of the "
    "library list first."
)
BUSY = "Another library deletion is in progress. Wait for it to finish, then try again."
RESERVED = (
    "This library folder has an unfinished permanent deletion. Open another library and "
    "use Retry under Library, or restart CBS Transcriber to recover it."
)
# Most blockers are settled inside the library itself.
RESOLVE = "Open this library, resolve them in Activity, then come back here."
MISSING = "This folder no longer exists, so there is nothing to delete there."
LINKED = "This folder is reached through a link to another location, so it is kept."
UNCHECKED = "This folder could not be checked, so it is kept."
TOO_MANY = "The library folder has too many files to check safely, so it cannot be deleted here."
CHANGED = (
    "A file waiting to be put back has changed, so it was left untouched. Ask for help "
    "before continuing."
)
FINISHED = "This deletion is already finished."
CLEANUP = (
    "This library was deleted, but some of its files could not be removed yet, for example "
    "because another program was using them. Close other programs, then choose Retry."
)
INTERRUPTED = (
    "Deleting this library was interrupted before it finished, and nothing was removed for "
    "good. Choose Retry to put the library's files back."
)


class Conflict(ValueError):
    """The library or its evidence changed or blocks deletion; nothing was deleted."""


class _Unsafe(ValueError):
    def __init__(self, path):
        super().__init__(str(path))
        self.path = path


class _TooMany(ValueError):
    pass


def _checkpoint(stage):
    """Crash-boundary hook for synthetic recovery tests; production does nothing."""


def journal_root():
    """Reservations live outside every library, next to the library registry."""
    return app_folder() / "library-deletions"


def _normal(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _key(path):
    return hashlib.sha256(_normal(path).encode("utf-8")).hexdigest()


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _recorded(path):
    """Fail closed: only a definite absence means no reservation (lexists hides errors)."""
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def reserved(path):
    """True when an unfinished deletion reserves this folder or one of its parents."""
    resolved = Path(path).expanduser().resolve()
    folder = journal_root()
    return any(_recorded(folder / _key(item)) for item in (resolved, *resolved.parents))


def require_unreserved(path, error=RuntimeError):
    """Fail closed before a launcher or Store opens or creates a reserved library."""
    if reserved(path):
        raise error(RESERVED)


def _canon(path):
    return Path(os.path.normcase(os.fspath(containment_path(Path(path)))))


def _inside(path, parent):
    return path == parent or path.is_relative_to(parent)


def _overlap(first, second):
    return _inside(first, second) or _inside(second, first)


def _identity(path, kind=None):
    info = path.lstat()
    if _redirect(info):
        raise _Unsafe(path)
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        raise _Unsafe(path)
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        raise _Unsafe(path)
    return [info.st_dev, info.st_ino]


def _walk(top, budget):
    """Count files and bind their names/identities/metadata without following links.

    This observes ordinary changes; it does not hash content or exclude an unrelated
    writer that restores metadata, or one that writes after the final observation.
    """
    top = _staged_io_path(top)
    witness = hashlib.sha256()

    def observe(path, info):
        witness.update(
            _canonical(
                [
                    path.relative_to(top).as_posix(),
                    stat.S_IFMT(info.st_mode),
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                ]
            )
            + b"\n"
        )

    info = top.lstat()
    if _redirect(info):
        raise _Unsafe(top)
    observe(top, info)
    if stat.S_ISREG(info.st_mode):
        return 1, info.st_size, witness.hexdigest()
    if not stat.S_ISDIR(info.st_mode):
        raise _Unsafe(top)
    files = size = 0
    device, pending = info.st_dev, [top]
    while pending:
        with os.scandir(pending.pop()) as entries:
            bounded = []
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise _TooMany()
                bounded.append(entry)
            # Stable order makes equal observed trees yield the same confirmation.
            for entry in sorted(bounded, key=lambda item: item.name):
                child = Path(entry.path)
                # Windows DirEntry.stat reports st_dev=0; lstat observes the real volume.
                info = child.lstat()
                if info.st_dev != device or _redirect(info):
                    raise _Unsafe(child)
                observe(child, info)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(child)
                elif stat.S_ISREG(info.st_mode):
                    files += 1
                    size += info.st_size
                else:
                    raise _Unsafe(child)
    return files, size, witness.hexdigest()


def _display(path, base):
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        value = value[4:]
    try:
        return str(Path(value).relative_to(base))
    except ValueError:
        return value


def _signature(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if _redirect(info) or not stat.S_ISREG(info.st_mode):
        raise _Unsafe(path)
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


@contextmanager
def _snapshot(root):
    """Copy the database (and WAL/hot journal) out of the target; never open it in place.

    Opening a WAL database even with mode=ro can create -wal/-shm files or write read marks
    into the shared-memory index. Reading a private copy leaves the target byte-for-byte
    unchanged. Unchanged signatures before and after the copy bind one consistent state.
    """
    names = (DATABASE, WAL, HOT_JOURNAL)
    before = {name: _signature(root / name) for name in names}
    if before[DATABASE] is None:
        raise FileNotFoundError(DATABASE)
    with tempfile.TemporaryDirectory(
        prefix="cbs-library-delete-", ignore_cleanup_errors=True
    ) as folder:
        for name, signature in before.items():
            if signature is not None:
                with (root / name).open("rb") as source, (Path(folder) / name).open("xb") as copy:
                    shutil.copyfileobj(source, copy, 4 * 1024 * 1024)
        if {name: _signature(root / name) for name in names} != before:
            raise Conflict("The library changed while it was being read. Try again.")
        signature = {
            name: value if name == DATABASE else value[2:]
            for name, value in before.items()
            # An empty WAL is what a read-only open leaves; it holds no content.
            if value is not None and not (name == WAL and value[2] == 0)
        }
        yield Path(folder) / DATABASE, signature


class _Copy:
    """The Store-like connection seam of the shared obligation helpers, on the private copy."""

    def __init__(self, path):
        self.db_path = path

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
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


def _job_obligations(source):
    from . import remote_jobs, restored_owners

    with source.connection() as db:
        exclusion = restored_owners.sql_exclusion(db, "job", "jobs.id")
        active = db.execute(
            "SELECT count(*) FROM jobs WHERE state IN ('queued','running') AND " + exclusion
        ).fetchone()[0]
        remote = malformed = 0
        for row in db.execute(
            "SELECT id,state,params FROM jobs WHERE " + exclusion + " AND CASE "
            "WHEN NOT json_valid(params) THEN 1 WHEN json_type(params)<>'object' THEN 1 "
            "WHEN json_type(params,'$.ucloud_run') IS NOT NULL THEN 1 ELSE 0 END=1"
        ):
            try:
                params = json.loads(row["params"])
            except (TypeError, ValueError, RecursionError):
                params = None
            if not isinstance(params, dict) or not isinstance(params.get("ucloud_run"), dict):
                malformed += 1
            elif not (params.get("native_batch_id") or params.get("runtime_batch_id")):
                # Shared allocations are resolved only through their owner journals.
                remote += remote_jobs.unresolved({"params": params, "state": row["state"]})
    return active, remote, malformed


def _native_obligations(source):
    from . import native_batch, restored_owners

    count = malformed = 0
    with source.connection() as db:
        exclusion = restored_owners.sql_exclusion(db, "native_batch", "native_batch_runs.id")
        for row in db.execute("SELECT state,params FROM native_batch_runs WHERE " + exclusion):
            try:
                params = json.loads(row["params"])
            except (TypeError, ValueError, RecursionError):
                params = None
            if not isinstance(params, dict) or not isinstance(params.get("ucloud_run", {}), dict):
                malformed += 1
            else:
                count += native_batch.unresolved({"state": row["state"], "params": params})
    return count, malformed


def _obligations(copy, tables):
    """Unsettled local or remote work blocks deletion; unreadable evidence blocks too."""
    from . import input_admission, runtime_preparation, runtime_store

    source, reasons = _Copy(copy), []

    def check(label, function):
        try:
            return function()
        except Exception:
            reasons.append(f"Some saved {label} could not be checked. {RESOLVE}")
            return None

    found = check("jobs", lambda: _job_obligations(source))
    if found:
        active, remote, malformed = found
        if active:
            reasons.append(
                f"{active} job(s) are waiting or still running, for example a transcription. "
                "Open this library and let them finish or cancel them in Activity, then come "
                "back here."
            )
        if remote:
            reasons.append(
                f"{remote} UCloud job(s) are still running or still have files on UCloud to "
                f"clean up. {RESOLVE}"
            )
        if malformed:
            reasons.append(f"{malformed} saved job record(s) could not be read. {RESOLVE}")
    if "native_batch_runs" in tables:
        found = check("UCloud jobs", lambda: _native_obligations(source))
        if found and found[0]:
            reasons.append(
                f"{found[0]} shared UCloud job(s) are still running or still have files on "
                f"UCloud to clean up. {RESOLVE}"
            )
        if found and found[1]:
            reasons.append(f"{found[1]} saved UCloud job record(s) could not be read. {RESOLVE}")
    if "runtime_batch_runs" in tables:
        found = check("UCloud jobs", lambda: runtime_store.obligations(source))
        if found:
            reasons.append(
                f"{len(found)} UCloud (CBS Runtime) job(s) are still running or still have "
                f"files on UCloud to clean up. {RESOLVE}"
            )
    found = check("UCloud preparations", lambda: runtime_preparation.obligations(source))
    if found:
        reasons.append(f"{len(found)} UCloud preparation(s) are unfinished. {RESOLVE}")
    found = check("file preparations", lambda: input_admission.obligations(source))
    if found:
        reasons.append(f"{len(found)} file(s) are still being prepared for processing. {RESOLVE}")
    return reasons


def _read_database(copy):
    """Counts, projects and blocking obligations from the private copy."""
    blockers = []
    result = {"schema": None, "projects": [], "recordings": 0, "blockers": blockers}
    with closing(sqlite3.connect(copy, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("PRAGMA user_version").fetchone()[0]
        result["schema"] = version
        if version > SCHEMA_VERSION:
            blockers.append(
                "This library was saved by a newer version of CBS Transcriber. Update the app, "
                "then check again."
            )
            return result
        if db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            blockers.append(
                "The library's database is damaged, so its contents cannot be checked safely. "
                "Ask for help before deleting it."
            )
            return result
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"projects", "recordings", "jobs"} <= tables:
            blockers.append(
                "The library's database is incomplete, so its contents cannot be checked "
                "safely. Ask for help before deleting it."
            )
            return result
        columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
        storage = "p.storage_dir" if "storage_dir" in columns else "NULL"
        for row in db.execute(
            f"SELECT p.id,p.name,{storage} AS storage_dir,(SELECT count(*) FROM recordings r "
            "WHERE r.project_id=p.id) AS recordings FROM projects p ORDER BY p.created,p.id"
        ):
            result["projects"].append(dict(row))
        result["recordings"] = db.execute("SELECT count(*) FROM recordings").fetchone()[0]
    blockers.extend(_obligations(copy, tables))
    return result


def _project_dirs(root):
    """Project folders another library references, read through a read-only connection."""
    database = Path(root) / DATABASE
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
        if "storage_dir" not in columns:
            return []
        return [
            row[0]
            for row in db.execute("SELECT storage_dir FROM projects WHERE storage_dir IS NOT NULL")
        ]


def _read_bounded(path, bound):
    info = path.lstat()
    if _redirect(info) or not stat.S_ISREG(info.st_mode):
        raise _Unsafe(path)
    with path.open("rb") as stream:
        data = stream.read(bound + 1)
    if len(data) > bound:
        raise ValueError("A record is too large to verify.")
    return data


def _journal_blockers(root):
    """Local deletion, backup transfer and restore records that are not settled."""
    reasons = []
    try:
        folder = root / "local-deletions"
        if folder.is_dir() and any(folder.iterdir()):
            reasons.append(
                "Deleting a project or recording in this library did not finish. Open this "
                "library and choose Retry on it in Activity, then come back here."
            )
    except OSError:
        reasons.append(f"This library's file deletion records could not be read. {RESOLVE}")
    unsettled = malformed = 0
    folder = root / "portable-transfers"
    try:
        names = sorted(os.listdir(folder)) if folder.is_dir() else []
        for name in names[: MAX_CHILDREN + 1]:
            if not name.endswith(".json"):
                continue
            try:
                record = json.loads(_read_bounded(folder / name, MAX_TRANSFER_BYTES))
                if not isinstance(record, dict) or record.get("format") != (
                    "cbs-portable-transfer-v1"
                ):
                    raise ValueError
            except (OSError, ValueError, RecursionError):
                malformed += 1
                continue
            unsettled += record.get("state") not in SETTLED_TRANSFERS
        if len(names) > MAX_CHILDREN:
            malformed += 1
    except OSError:
        malformed += 1
    if unsettled:
        reasons.append(
            f"{unsettled} backup upload(s) to UCloud from this library did not finish. Open "
            "this library and finish or resolve them under Library → Backups, then come back "
            "here."
        )
    if malformed:
        reasons.append(
            "Some UCloud backup records in this library could not be read. Open this library "
            "and check Library → Backups first."
        )
    unfinished = malformed = 0
    folder = root / "portable-restore-index"
    try:
        names = sorted(os.listdir(folder)) if folder.is_dir() else []
        for name in names[: MAX_CHILDREN + 1]:
            if not name.endswith(".json") or name.startswith("."):
                continue
            try:
                binding = json.loads(_read_bounded(folder / name, 32768))
                identifier = binding["operation_id"]
                if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
                    raise ValueError
                target = binding["target"]
                if not isinstance(target, str) or "\x00" in target:
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                malformed += 1
                continue
            target = Path(target).expanduser()
            if not target.is_absolute():
                continue  # Refused before any restore could start.
            journal = target.parent / (".restore-" + identifier + ".json")
            try:
                if os.path.lexists(target / "restore-pending"):
                    unfinished += 1
                elif os.path.lexists(journal):
                    state = json.loads(_read_bounded(journal, 64 * 1024)).get("state")
                    unfinished += state != "complete"
            except (OSError, ValueError, AttributeError, RecursionError):
                malformed += 1
        if len(names) > MAX_CHILDREN:
            malformed += 1
    except OSError:
        malformed += 1
    if unfinished:
        reasons.append(
            f"{unfinished} restore(s) of a backup started from this library did not finish. "
            "Open this library and finish them under Library → Backups, then come back here."
        )
    if malformed:
        reasons.append(
            "Some restore records in this library could not be read. Open this library and "
            "check Library → Backups first."
        )
    return reasons


def _external(folder, project_id):
    """Why an external project folder must be kept, or None when its layout is owned."""
    try:
        info = folder.lstat()
    except FileNotFoundError:
        return MISSING
    for item in (folder, *folder.parents):
        try:
            if _redirect(item.lstat()):
                return LINKED
        except FileNotFoundError:
            return MISSING
    if not stat.S_ISDIR(info.st_mode):
        return "The saved location is not a folder, so it is kept."
    if _canon(folder) != Path(_normal(folder)):
        return LINKED
    if folder.parent == folder or info.st_dev != folder.parent.lstat().st_dev:
        return "This is a whole drive or a mounted volume, so it is kept."
    names = set(os.listdir(folder))
    unknown = sorted(names - PROJECT_ENTRIES)
    if unknown:
        return (
            "It also contains files not created by CBS Transcriber ("
            + ", ".join(unknown[:5])
            + "), so it is kept. Move those files elsewhere and check again to delete it."
        )
    for name in ("recordings", "exports"):
        if name not in names:
            continue
        if not stat.S_ISDIR((folder / name).lstat().st_mode):
            return f"Its {name} item is not a folder, so it is kept."
        others = [
            child
            for child in os.listdir(folder / name)
            if not (len(child) == 32 and _HEX.fullmatch(child))
        ]
        if others:
            return (
                f"Its {name} folder also contains files not created by CBS Transcriber ("
                + ", ".join(others[:5])
                + "), so it is kept."
            )
    if "project.json" in names:
        snapshot = folder / "project.json"
        if _redirect(snapshot.lstat()) or not stat.S_ISREG(snapshot.lstat().st_mode):
            return "Its project.json file is not a normal file, so the folder is kept."
        with snapshot.open("rb") as stream:
            found = _PROJECT_JSON.match(stream.read(4096))
        if found is None or found.group(1).decode("ascii") != project_id:
            return "Its project.json file belongs to another project, so the folder is kept."
    return None


def _choose(path, current):
    from . import libraries

    if not isinstance(path, str) or not path or len(path) > 4000 or "\x00" in path:
        raise ValueError("Choose a library from the current list.")
    chosen = Path(path)
    if not chosen.is_absolute():
        raise ValueError("Choose a library from the current list.")
    root = chosen.resolve()
    if current is not None and _normal(root) == _normal(Path(current).resolve()):
        raise ValueError("The open library cannot be deleted. Open another library first.")
    if not any(
        _normal(root) == _normal(item) for item in libraries.candidates(include_hidden=True)
    ):
        raise ValueError("This library is no longer in the current list. Refresh the list.")
    if root.parent == root:
        raise ValueError("A drive cannot be deleted as a library.")
    for item in (root, *root.parents):
        if _redirect(item.lstat()):
            raise ValueError("Libraries reached through a filesystem link cannot be deleted here.")
    if _canon(root) != Path(_normal(root)):
        raise ValueError("Libraries reached through a filesystem link cannot be deleted here.")
    return root


def _other_libraries(root, current):
    from . import libraries

    seen, roots = {_normal(root)}, []
    for item in [*libraries.candidates(include_hidden=True), *([current] if current else [])]:
        item = Path(item).resolve()
        if _normal(item) not in seen:
            seen.add(_normal(item))
            roots.append(item)
    return roots


def _inspect(path, current, *, held=False):
    """Every fact the confirmation shows and binds; never changes the target."""
    from . import libraries

    root = _choose(path, current)
    blockers, evidence = [], {}
    facts = {
        "path": str(root),
        "name": root.name,
        "projects": 0,
        "recordings": 0,
        "files": 0,
        "bytes": 0,
        "delete": [],
        "keep": [],
        "other_items": [],
        "blockers": blockers,
        "notes": [
            "Not affected: the library you have open now, other libraries, backups saved "
            "outside this library folder, and copies on UCloud."
        ],
    }
    if not held and _pending_keys():
        blockers.append(PENDING)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("This library folder is no longer a folder. Refresh the list.")
    evidence["root"] = [info.st_dev, info.st_ino]
    if info.st_dev != root.parent.lstat().st_dev:
        blockers.append(
            "The library folder is a whole drive or a mounted volume, so it cannot be deleted here."
        )
    canon = _canon(root)
    if _inside(_canon(app_folder()), canon):
        blockers.append(
            "This folder also holds CBS Transcriber's own settings, so it cannot be deleted here."
        )
    if not held and libraries.in_use(root):
        blockers.append(OPEN)
        return _seal(facts, evidence)

    # Every direct child is staged and removed; names the app never creates are listed.
    try:
        names = sorted(os.listdir(root))
    except OSError as error:
        raise Conflict("The library folder could not be read.") from error
    if len(names) > MAX_CHILDREN:
        blockers.append(TOO_MANY)
        return _seal(facts, evidence)
    children, budget = [], [MAX_ENTRIES]
    files = size = 0
    for name in names:
        child = root / name
        try:
            kind = "directory" if stat.S_ISDIR(child.lstat().st_mode) else "file"
            counted = _walk(child, budget)
            identity = _identity(child, kind)
        except _Unsafe as error:
            blockers.append(
                "The library folder contains a link to another location or a special file "
                f"({_display(error.path, root)}). Remove that item in File Explorer or "
                "Finder, then check again."
            )
            continue
        except _TooMany:
            blockers.append(TOO_MANY)
            break
        except OSError as error:
            raise Conflict("The library folder changed or could not be read. Try again.") from error
        if name != LOCK:
            children.append({"name": name, "kind": kind, "identity": identity})
        if name not in VOLATILE:
            files, size = files + counted[0], size + counted[1]
            evidence.setdefault("inventories", {})[str(child)] = counted[2]
        if name not in APP_ENTRIES:
            facts["other_items"].append(name)
    order = {DATABASE: 0, WAL: 1, HOT_JOURNAL: 2, SHM: 3}
    children.sort(key=lambda item: (order.get(item["name"], 4), item["name"]))
    evidence["children"] = children
    evidence["names"] = [item["name"] for item in children if item["name"] not in VOLATILE]
    evidence["totals"] = [files, size]
    if os.path.lexists(root / "restore-pending"):
        blockers.append(
            "A restore of a backup into this folder did not finish. Finish it under "
            "Library → Backups first."
        )
    facts["delete"].append({"path": str(root), "kind": "library", "files": files, "bytes": size})

    # The database is read from a private copy only.
    database = {"projects": [], "blockers": []}
    try:
        with _snapshot(root) as (copy, signature):
            evidence["database"] = signature
            database = _read_database(copy)
    except FileNotFoundError:
        blockers.append(
            "The library's database file is missing, so its contents cannot be checked safely."
        )
    except _Unsafe:
        blockers.append(
            "The library's database is not a normal file, so its contents cannot be checked safely."
        )
    except (OSError, sqlite3.Error):
        blockers.append(
            "The library's database could not be read. Close other programs that may be "
            "using it, then check again."
        )
    blockers.extend(database["blockers"])
    facts["projects"] = len(database["projects"])
    facts["recordings"] = database.get("recordings", 0)
    blockers.extend(_journal_blockers(root))

    # Ownership: other known libraries' roots and project folders.
    others, unverified, other_dirs = _other_libraries(root, current), [], []
    for other in others:
        other_canon = _canon(other)
        if _overlap(other_canon, canon):
            blockers.append(
                f"Another library ({other}) is inside this folder or contains it, so this "
                "folder cannot be deleted here."
            )
        try:
            folders = _project_dirs(other)
        except (OSError, sqlite3.Error, ValueError):
            unverified.append(str(other))
            continue
        for value in folders:
            if isinstance(value, str) and value and "\x00" not in value:
                other_dirs.append((other, _canon(value)))
                if _overlap(_canon(value), canon):
                    blockers.append(
                        f"The library at {other} keeps project files in or around this folder. "
                        "Move that project's storage elsewhere first."
                    )
    evidence["others"] = sorted(str(item) for item in others)
    evidence["unverified"] = unverified

    externals = {}
    for project in database["projects"]:
        storage = project["storage_dir"]
        if storage is None:
            continue
        if not isinstance(storage, str) or "\x00" in storage or not Path(storage).is_absolute():
            facts["keep"].append(
                {
                    "path": str(storage)[:500],
                    "project": project["name"],
                    "reason": "The saved location is not a usable folder path, so it is kept.",
                }
            )
            continue
        project_canon = _canon(storage)
        if _inside(project_canon, canon):
            continue  # Deleted with the library folder.
        externals.setdefault(project_canon, []).append(project)
    budget = [MAX_ENTRIES]
    for project_canon, projects in sorted(externals.items(), key=lambda item: str(item[0])):
        project = projects[0]
        folder = Path(os.path.abspath(project["storage_dir"]))
        reason = None
        if len(projects) > 1 or any(
            other != project_canon and _overlap(other, project_canon) for other in externals
        ):
            reason = "Several projects in this library use this folder, so it is kept."
        elif _overlap(project_canon, canon):
            reason = "This folder contains the library folder, so only the library is deleted."
        elif unverified:
            reason = (
                f"Another library ({unverified[0]}) could not be checked, so this folder is "
                "kept in case it is shared."
            )
        elif any(_overlap(project_canon, _canon(other)) for other in others):
            reason = "This folder overlaps another library's folder, so it is kept."
        elif any(_overlap(project_canon, value) for _, value in other_dirs):
            owner = next(item for item, value in other_dirs if _overlap(project_canon, value))
            reason = (
                f"Another library ({owner}) also uses this folder, so it is kept for that library."
            )
        elif _overlap(project_canon, _canon(app_folder())) and not _inside(
            project_canon, _canon(app_folder())
        ):
            reason = "This folder contains CBS Transcriber's own settings, so it is kept."
        if reason is None:
            try:
                reason = _external(folder, project["id"])
            except (OSError, ValueError):
                reason = UNCHECKED
        counted = None
        if reason is None:
            try:
                counted = _walk(folder, budget)
                identity = _identity(folder, "directory")
            except _Unsafe:
                reason = "It contains a link to another location or a special file, so it is kept."
            except _TooMany:
                reason = "It has too many files to check safely, so it is kept."
            except OSError:
                reason = UNCHECKED
        if reason is not None:
            facts["keep"].append(
                {"path": str(folder), "project": project["name"], "reason": reason}
            )
            continue
        facts["delete"].append(
            {
                "path": str(folder),
                "kind": "project",
                "project": project["name"],
                "recordings": project["recordings"],
                "files": counted[0],
                "bytes": counted[1],
            }
        )
        evidence.setdefault("externals", []).append(
            {"path": str(folder), "identity": identity, "project_id": project["id"]}
        )
        evidence.setdefault("inventories", {})[str(folder)] = counted[2]
    facts["files"] = sum(item["files"] for item in facts["delete"])
    facts["bytes"] = sum(item["bytes"] for item in facts["delete"])
    return _seal(facts, evidence)


def _seal(facts, evidence):
    facts["fingerprint"] = _digest(
        {
            "facts": facts,
            "evidence": {key: value for key, value in evidence.items() if key != "children"},
            "children": [
                [item["name"], item["kind"], item["identity"]]
                for item in evidence.get("children", [])
                if item["name"] not in VOLATILE
            ],
        }
    )
    facts["can_delete"] = not facts["blockers"]
    return facts, evidence


def preview(path, *, current):
    """A read-only description of what Delete permanently removes, keeps and blocks on."""
    facts, _ = _inspect(path, current)
    return facts


# ---------------------------------------------------------------------------------------
# Durable operation records


def _hex(value, length):
    if not isinstance(value, str) or len(value) != length or not _HEX.fullmatch(value):
        raise ValueError("Invalid library deletion identity.")
    return value


def _absolute(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4000 or "\x00" in value:
        raise ValueError("Invalid library deletion path.")
    path = Path(value)
    if not path.is_absolute() or os.path.abspath(value) != value:
        raise ValueError("Library deletion paths must be absolute and normalized.")
    return path


def _identity_value(value):
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(item) is not int or not 0 <= item < 2**128 for item in value)
    ):
        raise ValueError("Invalid library deletion file identity.")
    return value


def _validate(plan, key):
    fields = {
        "schema",
        "operation_id",
        "key",
        "root",
        "name",
        "created",
        "fingerprint",
        "root_identity",
        "wrapper",
        "children",
        "externals",
        "summary",
    }
    if not isinstance(plan, dict) or set(plan) != fields or plan["schema"] != SCHEMA:
        raise ValueError("Unknown library deletion record.")
    operation = _hex(plan["operation_id"], 32)
    root = _absolute(plan["root"])
    if plan["key"] != key or _key(root) != key or plan["name"] != root.name:
        raise ValueError("The library deletion record belongs to another folder.")
    _hex(plan["fingerprint"], 64)
    _identity_value(plan["root_identity"])
    if not isinstance(plan["created"], str) or len(plan["created"]) > 64:
        raise ValueError("Invalid library deletion timestamp.")
    if plan["wrapper"] != str(root.parent / (PREFIX + operation)):
        raise ValueError("The library deletion staging folder changed.")
    names = set()
    if not isinstance(plan["children"], list) or len(plan["children"]) > MAX_CHILDREN:
        raise ValueError("Invalid library deletion inventory.")
    for child in plan["children"]:
        if (
            not isinstance(child, dict)
            or set(child) != {"name", "kind", "identity"}
            or not isinstance(child["name"], str)
            or child["name"] in {"", ".", "..", LOCK}
            or any(character in child["name"] for character in "/\\:\x00")
            or child["name"] in names
            or child["kind"] not in {"file", "directory"}
        ):
            raise ValueError("Invalid library deletion inventory.")
        names.add(child["name"])
        _identity_value(child["identity"])
    if not isinstance(plan["externals"], list) or len(plan["externals"]) > 10_000:
        raise ValueError("Invalid library deletion project scope.")
    for index, item in enumerate(plan["externals"]):
        if not isinstance(item, dict) or set(item) != {"path", "wrapper", "identity", "project_id"}:
            raise ValueError("Invalid library deletion project scope.")
        folder = _absolute(item["path"])
        if item["wrapper"] != str(folder.parent / f"{PREFIX}{operation}-{index}"):
            raise ValueError("The library deletion staging folder changed.")
        _identity_value(item["identity"])
        _hex(item["project_id"], 32)
    if not isinstance(plan["summary"], dict) or len(_canonical(plan["summary"])) > 1024 * 1024:
        raise ValueError("Invalid library deletion summary.")
    if len(_canonical(plan)) > MAX_PLAN_BYTES:
        raise ValueError("The library deletion record is too large.")
    return plan


def _read_json(path, bound):
    data = _read_bounded(path, bound)
    try:
        value = json.loads(data)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("Malformed library deletion record.") from error
    if _canonical(value) != data:
        raise ValueError("The library deletion record is not canonical.")
    return value


def _publish_file(path, value):
    """Flush a complete file, then rename it into place (never a torn final record)."""
    pending = path.with_name(path.name + ".pending")
    with pending.open("xb") as stream:
        stream.write(_canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(pending, path)
    _sync_directory(path.parent)


def _unlink(path):
    try:
        path.unlink()
    except PermissionError as error:
        _retry_readonly(os.unlink, path, error)


def _binding(plan, slot):
    return {
        "schema": SCHEMA,
        "operation_id": plan["operation_id"],
        "plan_sha256": _digest(plan),
        "slot": slot,
    }


def _completion(plan):
    return {"schema": SCHEMA, "operation_id": plan["operation_id"], "plan_sha256": _digest(plan)}


def _make_wrapper(wrapper, binding):
    wrapper.mkdir()  # Exclusive: an existing folder is never adopted.
    _sync_directory(wrapper.parent)
    with (wrapper / "marker.json").open("xb") as stream:
        stream.write(_canonical(binding))
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(wrapper)


def _staged(wrapper, binding, payload):
    """True when the wrapper holds staged data under a verified marker.

    A wrapper without staged data can be removed whatever its marker says: rmdir of the
    empty payload folder and unlinking a marker cannot discard any library data.
    """
    if not os.path.lexists(wrapper):
        return False
    _identity(wrapper, "directory")
    names = set(os.listdir(wrapper))
    if names - {"marker.json", payload}:
        raise ValueError("A library deletion staging folder contains unexpected items.")
    holds = payload in names
    if holds and payload == "library":
        _identity(wrapper / payload, "directory")
        with os.scandir(_staged_io_path(wrapper / payload)) as entries:
            holds = any(True for _ in entries)
    if holds and _read_json(wrapper / "marker.json", 4096) != binding:
        raise ValueError("A library deletion staging marker changed.")
    return holds


def _remove_wrapper(wrapper, payload):
    if not os.path.lexists(wrapper):
        return
    if payload == "library" and os.path.lexists(wrapper / payload):
        (wrapper / payload).rmdir()  # Only an empty staging folder can be removed here.
    if os.path.lexists(wrapper / "payload") and payload == "payload":
        raise ValueError("A staged project folder still occupies its staging folder.")
    if os.path.lexists(wrapper / "marker.json"):
        _identity(wrapper / "marker.json", "file")
        _unlink(wrapper / "marker.json")
    wrapper.rmdir()
    _sync_directory(wrapper.parent)


@contextmanager
def _file_lock(path, message):
    if os.path.lexists(path):
        _identity(path, "file")
    with path.open("a+b") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Conflict(message) from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive():
    """One library deletion or recovery at a time, across threads and app processes."""
    if not _thread_lock.acquire(blocking=False):
        raise Conflict(BUSY)
    try:
        folder = journal_root()
        folder.mkdir(parents=True, exist_ok=True)
        _identity(folder, "directory")
        with _file_lock(folder / ".lock", BUSY):
            yield folder
    finally:
        _thread_lock.release()


def _lease(root):
    """The target's normal server lease (same lock as Store); refuses an open library."""
    path = root / LOCK
    return _file_lock(path, OPEN)


def _pending_keys():
    folder = journal_root()
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    return sorted(name for name in names if len(name) == 64 and _HEX.fullmatch(name))


def _plan(root, facts, evidence):
    operation = uuid.uuid4().hex
    plan = {
        "schema": SCHEMA,
        "operation_id": operation,
        "key": _key(root),
        "root": str(root),
        "name": root.name,
        "created": datetime.now(UTC).isoformat(),
        "fingerprint": facts["fingerprint"],
        "root_identity": evidence["root"],
        "wrapper": str(root.parent / (PREFIX + operation)),
        "children": evidence["children"],
        "externals": [
            {
                "path": item["path"],
                "wrapper": str(Path(item["path"]).parent / f"{PREFIX}{operation}-{index}"),
                "identity": item["identity"],
                "project_id": item["project_id"],
            }
            for index, item in enumerate(evidence.get("externals", []))
        ],
        "summary": {
            "projects": facts["projects"],
            "recordings": facts["recordings"],
            "files": facts["files"],
            "bytes": facts["bytes"],
            "kept": [item["path"] for item in facts["keep"]][:200],
        },
    }
    _validate(plan, plan["key"])
    for wrapper in (plan["wrapper"], *(item["wrapper"] for item in plan["externals"])):
        if os.path.lexists(wrapper):
            raise Conflict("A deletion staging folder already exists next to this library.")
    return plan


def _stage(plan):
    """Same-volume exclusive renames of every verified entry into marked sibling folders."""
    root, wrapper = Path(plan["root"]), Path(plan["wrapper"])
    _make_wrapper(wrapper, _binding(plan, "library"))
    payload = wrapper / "library"
    payload.mkdir()
    for index, child in enumerate(plan["children"]):
        source = root / child["name"]
        if _identity(source, child["kind"]) != child["identity"]:
            raise Conflict(f"{child['name']} changed during deletion.")
        _rename(source, payload / child["name"])
        if index == 0:
            _checkpoint("staging")
    _sync_directory(root)
    _sync_directory(payload)
    for index, item in enumerate(plan["externals"]):
        folder, external = Path(item["path"]), Path(item["wrapper"])
        if _identity(folder, "directory") != item["identity"]:
            raise Conflict("A project folder changed during deletion.")
        _make_wrapper(external, _binding(plan, index))
        _rename(folder, external / "payload")
        _sync_directory(folder.parent)
        _sync_directory(external)


def _verify_staged(plan):
    root, payload = Path(plan["root"]), Path(plan["wrapper"]) / "library"
    remaining = sorted(set(os.listdir(root)) - {LOCK})
    if remaining:
        raise Conflict(
            "New items appeared in the library folder during deletion: " + ", ".join(remaining[:5])
        )
    for child in plan["children"]:
        if _identity(payload / child["name"], child["kind"]) != child["identity"]:
            raise Conflict("A staged library item changed during deletion.")
    for item in plan["externals"]:
        if _identity(Path(item["wrapper"]) / "payload", "directory") != item["identity"]:
            raise Conflict("A staged project folder changed during deletion.")


def _close(folder):
    """Remove the reservation last; every interrupted step stays recoverable."""
    for name in ("plan.json", "plan.json.pending", "committed.json", "committed.json.pending"):
        if os.path.lexists(folder / name):
            _identity(folder / name, "file")
            _unlink(folder / name)
    folder.rmdir()
    _sync_directory(folder.parent)
    _errors.pop(folder.name, None)


def _rollback(plan, folder, *, held=False):
    """Before commit: move every staged entry back into its unoccupied original name."""
    root = Path(plan["root"])
    # A missing parent may be an unplugged drive holding staged data: retry later.
    for folder_path in (root, *(Path(item["path"]) for item in plan["externals"])):
        if not os.path.isdir(folder_path.parent):
            raise ValueError(
                f"The folder {folder_path.parent} is not available, for example because a "
                "drive is disconnected. Reconnect it, then choose Retry."
            )
    if not os.path.lexists(root):
        root.mkdir()  # The original folder name itself is unoccupied.
    elif _identity(root, "directory") != plan["root_identity"] and set(os.listdir(root)) - {
        LOCK,
        *(child["name"] for child in plan["children"]),
    }:
        # An empty folder (for example one an earlier recovery attempt recreated) or one
        # holding only entries already moved back may receive the rest; never merge into
        # an unrelated folder.
        raise ValueError(
            "A different folder now exists at the library's location, so the files could not "
            "be put back. Ask for help before continuing."
        )
    with nullcontext() if held else _lease(root):
        wrapper = Path(plan["wrapper"])
        if _staged(wrapper, _binding(plan, "library"), "library"):
            payload = wrapper / "library"
            for child in reversed(plan["children"]):
                staged = payload / child["name"]
                if not os.path.lexists(staged):
                    if not os.path.lexists(root / child["name"]):
                        raise ValueError(
                            f"“{child['name']}” could not be found, so it could not be put "
                            "back. Ask for help before continuing."
                        )
                    continue
                if _identity(staged, child["kind"]) != child["identity"]:
                    raise ValueError(CHANGED)
                if os.path.lexists(root / child["name"]):
                    raise ValueError(
                        f"Something new named “{child['name']}” now exists in the library "
                        "folder, so the original could not be put back. Move that new item "
                        "elsewhere, then choose Retry."
                    )
                _rename(staged, root / child["name"])
            _sync_directory(root)
        _remove_wrapper(wrapper, "library")
        for index, item in reversed(list(enumerate(plan["externals"]))):
            external, original = Path(item["wrapper"]), Path(item["path"])
            if _staged(external, _binding(plan, index), "payload"):
                if _identity(external / "payload", "directory") != item["identity"]:
                    raise ValueError(CHANGED)
                if os.path.lexists(original):
                    raise ValueError(
                        f"Something new now exists at {original}, so the project folder could "
                        "not be put back. Move that new item elsewhere, then choose Retry."
                    )
                _rename(external / "payload", original)
                _sync_directory(original.parent)
            _remove_wrapper(external, "payload")
    _close(folder)
    return {
        "state": "restored",
        "message": "The interrupted deletion was undone: the library's files are back and "
        "nothing was deleted.",
    }


def _remove_tree(path):
    """Recursive removal of a verified, link-free staged payload (after commit only)."""
    shutil.rmtree(_staged_io_path(path), onexc=_retry_readonly)


def _finish(plan, folder):
    """After commit: remove the lease file, the empty folder and every marked payload."""
    from . import libraries

    root, notes = Path(plan["root"]), []
    libraries.forget(root)
    if os.path.lexists(root):
        info = root.lstat()
        if (
            _redirect(info)
            or not stat.S_ISDIR(info.st_mode)
            or [info.st_dev, info.st_ino] != plan["root_identity"]
        ):
            notes.append("A different folder now exists at its location; it was not touched.")
        else:
            names = set(os.listdir(root))
            if names <= {LOCK}:
                if LOCK in names:
                    lock = root / LOCK
                    _identity(lock, "file")
                    if lock.lstat().st_size > 64:
                        raise ValueError("The library lease file changed.")
                    _unlink(lock)
                root.rmdir()
                _sync_directory(root.parent)
            else:
                notes.append(
                    "New files appeared in the library folder during deletion; they were not "
                    "touched."
                )
    _checkpoint("finishing")
    slots = [(Path(plan["wrapper"]), "library", "library")] + [
        (Path(item["wrapper"]), index, "payload") for index, item in enumerate(plan["externals"])
    ]
    for wrapper, slot, payload in slots:
        if not os.path.lexists(wrapper):
            continue
        _identity(wrapper, "directory")
        names = set(os.listdir(wrapper))
        if names - {"marker.json", payload}:
            raise ValueError("A library deletion staging folder contains unexpected items.")
        if payload in names:
            if _read_json(wrapper / "marker.json", 4096) != _binding(plan, slot):
                raise ValueError("A library deletion staging marker changed.")
            _walk(wrapper / payload, [MAX_ENTRIES * 2])
            _remove_tree(wrapper / payload)
        _remove_wrapper(wrapper, payload)
    _close(folder)
    summary, kept = plan["summary"], len(plan["summary"].get("kept", []))
    done = (
        f"The library “{plan['name']}” was permanently deleted, with "
        f"{summary.get('recordings', 0)} recording(s)."
    )
    if kept:
        done += f" {kept} project folder(s) outside it were kept, as shown before deleting."
    return {"state": "complete", "message": " ".join([done, *notes])}


def _settle(folder):
    names = set(os.listdir(folder))
    if not names:
        folder.rmdir()
        return {"state": "complete", "message": FINISHED}
    if names <= {"plan.json.pending"}:
        _close(folder)  # The reservation was never published; nothing was changed.
        return {
            "state": "restored",
            "message": "The interrupted deletion had not changed anything. Nothing was deleted.",
        }
    if names <= {"committed.json"}:
        receipt = _read_json(folder / "committed.json", 4096)
        if not isinstance(receipt, dict) or set(receipt) != {
            "schema",
            "operation_id",
            "plan_sha256",
        }:
            raise ValueError("Malformed library deletion receipt.")
        _close(folder)  # The plan is removed only after every removal finished.
        return {"state": "complete", "message": FINISHED}
    allowed = {"plan.json", "committed.json", "committed.json.pending"}
    if "plan.json" not in names or names - allowed:
        raise ValueError("Unexpected files remain in the library deletion record.")
    plan = _validate(_read_json(folder / "plan.json", MAX_PLAN_BYTES), folder.name)
    if "committed.json" in names:
        if _read_json(folder / "committed.json", 4096) != _completion(plan):
            raise ValueError("The library deletion receipt changed.")
        return _finish(plan, folder)
    return _rollback(plan, folder)


def _summary(key, outcome):
    return {"id": key, **outcome}


def retry(identifier):
    """Settle one unfinished deletion: restore before commit, finish after it."""
    _hex(identifier, 64)
    with _exclusive() as root:
        folder = root / identifier
        if not os.path.lexists(folder):
            return _summary(identifier, {"state": "complete", "message": FINISHED})
        try:
            _identity(folder, "directory")
            return _summary(identifier, _settle(folder))
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            _errors[identifier] = _message(error)
            return _summary(identifier, {"state": "attention", "message": _errors[identifier]})


def _message(error):
    if isinstance(error, PermissionError):
        return (
            "A file is in use or read-only. Close other programs that may be using the "
            "library's files (for example a media player or File Explorer), then choose Retry."
        )
    if isinstance(error, _Unsafe):
        return (
            f"A link to another location or a special file was found ({error.path}), so it "
            "was not removed. Ask for help before continuing."
        )
    if isinstance(error, ValueError) and str(error):
        return str(error)[:500]
    return "This step could not be completed yet. Nothing more was removed; choose Retry later."


def recover_all():
    """Startup recovery; never creates the record folder when there is nothing to do."""
    return [retry(key) for key in _pending_keys()]


def pending():
    """Unfinished deletions for the Library dialog; read-only."""
    root, result = journal_root(), []
    for key in _pending_keys()[:MAX_JOURNALS]:
        folder = root / key
        item = {
            "id": key,
            "path": None,
            "name": "Library",
            "state": "attention",
            "message": "The record of this interrupted deletion could not be read. Choose "
            "Retry; if it still fails, ask for help.",
        }
        try:
            names = set(os.listdir(folder))
            if "plan.json" in names:
                plan = _validate(_read_json(folder / "plan.json", MAX_PLAN_BYTES), key)
                item.update(path=plan["root"], name=plan["name"], created=plan["created"])
            committed = "committed.json" in names
            item["state"] = "cleanup_pending" if committed else "restore_pending"
            item["message"] = _errors.get(key) or (CLEANUP if committed else INTERRUPTED)
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            pass
        result.append(item)
    return result


def delete(path, *, fingerprint, confirm_name, current):
    """Permanently delete one confirmed inactive library; recoverable at every step."""
    with _exclusive() as records:
        if _pending_keys():
            raise Conflict(PENDING)
        root = _choose(path, current)
        if not isinstance(confirm_name, str) or confirm_name != root.name:
            raise ValueError(
                "The typed name does not match. Type the library folder name exactly as shown."
            )
        with _lease(root):
            facts, evidence = _inspect(str(root), current, held=True)
            if facts["fingerprint"] != fingerprint:
                raise Conflict(
                    "The library changed since it was checked, so nothing was deleted. Choose "
                    "Delete permanently… again to see its current contents."
                )
            if facts["blockers"]:
                raise Conflict(facts["blockers"][0])
            plan = _plan(root, facts, evidence)
            folder = records / plan["key"]
            folder.mkdir()  # The reservation: launcher and Store refuse this path from now.
            _sync_directory(records)
            try:
                _publish_file(folder / "plan.json", plan)
            except Exception as error:
                # Nothing in the library has changed yet; drop the unpublished reservation.
                _close(folder)
                raise Conflict(
                    "Nothing was deleted: the deletion record could not be saved."
                ) from error
            _checkpoint("published")
            try:
                _stage(plan)
                _verify_staged(plan)
                _checkpoint("staged")
                _publish_file(folder / "committed.json", _completion(plan))
            except Exception as error:
                try:
                    _rollback(plan, folder, held=True)
                except Exception as undo:
                    _errors[plan["key"]] = _message(undo)
                    raise Conflict(
                        "The deletion stopped before it finished and could not be undone "
                        "automatically. Nothing more will be deleted. It is listed at the top of "
                        "the library list; choose Retry there to put the files back."
                    ) from error
                raise Conflict("Nothing was deleted. " + _message(error)) from error
            _checkpoint("committed")
        # The lease is released: Windows can now remove the lease file and empty folder.
        try:
            outcome = _finish(plan, folder)
        except (OSError, ValueError) as error:
            _errors[plan["key"]] = _message(error)
            return {
                "ok": True,
                "state": "cleanup_pending",
                "message": CLEANUP.replace("choose Retry", "choose Retry at the top of the list"),
            }
        return {"ok": True, **outcome}
