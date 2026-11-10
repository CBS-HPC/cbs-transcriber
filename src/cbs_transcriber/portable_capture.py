"""Complete local portable captures from a caller-owned, idle serving Store.

Untrusted database inspection belongs to portable_semantics' disposable child. Only the
Store's own backup-API snapshot is opened here. Publication never replaces an existing path.
"""

import hashlib
import json
import os
import sqlite3
import stat
import threading
import uuid
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from . import portable_inventory as inventory
from . import portable_transport as transport

_mutation_slot = ContextVar("portable_mutation_slot", default=None)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def durable(path, raw):
    """Create only; never replace a previous receipt or a user file."""
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


class CaptureGate:
    """HTTP mutations share admission; a capture waits for and excludes all of them."""

    def __init__(self):
        self.condition = threading.Condition()
        self.mutations = 0
        self.captures = 0
        self.active = False
        self.promoting = None

    def enter_mutation(self, allowed=None):
        with self.condition:
            self.condition.wait_for(
                lambda: not self.active and not self.captures and self.promoting is None
            )
            # Recovery may have become blocked while this request waited for admission.
            if allowed is not None and not allowed():
                return None
            self.mutations += 1
            return object()

    @contextmanager
    def mutation(self, slot):
        token = _mutation_slot.set((self, slot))
        try:
            yield
        finally:
            _mutation_slot.reset(token)

    @contextmanager
    def promote_mutation(self):
        """Drain earlier writes before staging, excluding this request's own slot."""
        current = _mutation_slot.get()
        own = current is not None and current[0] is self
        with self.condition:
            # Waiting with a second promoter's mutation slot would deadlock the first.
            if self.promoting is not None:
                raise ValueError("Wait for the current local file operation to finish, then retry.")
            self.promoting = current[1] if own else object()
            self.condition.notify_all()
            try:
                self.condition.wait_for(lambda: not self.active and self.mutations == int(own))
            except BaseException:
                self.promoting = None
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            with self.condition:
                self.promoting = None
                self.condition.notify_all()

    def leave_mutation(self):
        with self.condition:
            self.mutations -= 1
            self.condition.notify_all()

    @contextmanager
    def capture(self):
        with self.condition:
            self.captures += 1
            try:
                self.condition.wait_for(
                    lambda: not self.active and self.promoting is None and self.mutations == 0
                )
                self.active = True
            finally:
                self.captures -= 1
        try:
            yield
        finally:
            with self.condition:
                self.active = False
                self.condition.notify_all()


def _parents(path):
    """Refuse links including existing ancestors; resolve does not establish ownership."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Choose an absolute portable backup folder.")
    for parent in (*reversed(path.parents), path):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError("Portable storage must not use filesystem links.")
    return path.resolve()


def _local_volume(path):
    """Observe a local Windows volume without asserting physical independence."""
    if (
        os.name != "nt"
        or not path.is_absolute()
        or path.drive.startswith("\\\\")
        or ".." in path.parts
    ):
        return None
    import ctypes

    # Unknown, network, optical and unavailable drives are not useful evidence.
    if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) not in (2, 3):
        return None
    existing = None
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            return None
        if not stat.S_ISDIR(info.st_mode):
            return None
        existing = info
    return existing.st_dev if existing and existing.st_dev else None


def backup_volume_hint(root, destination):
    """Advisory only: unknown also covers different or unverifiable volumes."""
    try:
        library = _local_volume(Path(root))
        target_path = Path(destination).expanduser() if destination else Path(root) / "backups"
        target = _local_volume(target_path)
    except (OSError, RuntimeError, ValueError):
        return "unknown"
    return "same_volume" if library is not None and library == target else "unknown"


def _windows_path(path, *, directory=False):
    """Conservative native Win32 limits; no registry change or long-path assumption."""
    if os.name == "nt":
        units = len(str(path).encode("utf-16-le")) // 2
        if units > (247 if directory else 259):
            raise ValueError(
                "Choose a shorter local folder for this portable operation. "
                "Its generated Windows staging or destination path would be too long."
            )


def _walk(base, guard):
    """Bounded traversal of one database-owned managed root, with no link following."""
    base = _parents(base)
    transport._node(base, base, directory=True)
    pending, count = [base], 0
    while pending:
        current = pending.pop()
        names, _ = transport._entries(current, base, maximum=transport.LIMITS["files"], guard=guard)
        for name in sorted(names):
            transport._guard(guard)
            path = current / name
            count += 1
            if count > transport.LIMITS["files"]:
                raise ValueError("The portable file inventory exceeds its supported limit.")
            if path.is_dir():
                transport._node(path, base, directory=True)
                pending.append(path)
            else:
                transport._node(path, base)
                yield path, path.relative_to(base).as_posix()


def _copy_object(source, base, objects, guard):
    transport._guard(guard)
    before = transport._node(source, base)
    if before.st_size > transport.LIMITS["object_bytes"]:
        raise ValueError("A portable object exceeds the supported byte limit.")
    pending = objects / ("." + uuid.uuid4().hex + ".pending")
    digest, count = hashlib.sha256(), 0
    descriptor = os.open(
        source,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as incoming, pending.open("xb") as output:
            opened = os.fstat(descriptor)
            if transport._signature(opened) != transport._signature(before) or opened.st_nlink != 1:
                raise ValueError("A portable source changed while opening it.")
            while chunk := incoming.read(transport.CHUNK_BYTES):
                transport._guard(guard)
                count += len(chunk)
                if count > before.st_size:
                    raise ValueError("A portable source changed while copying it.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
            after = os.fstat(descriptor)
            if (
                transport._signature(opened) != transport._signature(after)
                or opened.st_ctime_ns != after.st_ctime_ns
            ):
                raise ValueError("A portable source changed while copying it.")
    finally:
        os.close(descriptor)
    final = transport._node(source, base)
    transport._same_path(before, final)
    if count != before.st_size:
        raise ValueError("A portable source changed while copying it.")
    key = digest.hexdigest()
    target = objects / key
    if target.exists():
        transport._read(
            target, objects.parent, maximum=count, size=count, expected_hash=key, guard=guard
        )
        pending.unlink()
    else:
        # Hard-link only our new flushed object, never any source recording. This provides
        # create-if-absent publication on both Windows and POSIX; no replace-on-rename race.
        os.link(pending, target, follow_symlinks=False)
        pending.unlink()
    return key, count, (source, base, before)


def _identity(root):
    path = root / "portable-library-id"
    if not path.exists():
        try:
            durable(path, uuid.uuid4().hex.encode("ascii"))
        except FileExistsError:
            pass
    raw, _ = transport._read(path, root, maximum=32, size=32, collect=True)
    return transport._hex(raw.decode("ascii"), 32)


def _recording_files(db, root, guard):
    jobs, attempts = {}, {}
    for identifier, recording_id in db.execute("SELECT id,recording_id FROM jobs ORDER BY id"):
        jobs.setdefault(recording_id, set()).add(identifier)
    for job_id, recording_id in db.execute("SELECT job_id,recording_id FROM runtime_batch_members"):
        jobs.setdefault(recording_id, set()).add(job_id)
    for job_id, attempt in db.execute(
        "SELECT a.job_id,a.attempt FROM runtime_batch_attempts a "
        "JOIN runtime_batch_members m ON m.job_id=a.job_id ORDER BY a.job_id,a.attempt"
    ):
        attempts.setdefault(job_id, set()).add(attempt)
    for recording in db.execute(
        "SELECT r.id,r.project_id,r.filename,p.storage_dir FROM recordings r "
        "JOIN projects p ON p.id=r.project_id ORDER BY r.project_id,r.id"
    ):
        identifier, project, filename, storage = recording
        transport._hex(identifier, 32)
        transport._hex(project, 32)
        base = _parents(Path(storage) if storage else root)
        directory = base / "recordings" / identifier
        original = "original" + Path(filename).suffix.lower()
        transport._portable_path(original)
        originals = 0
        for path, relative in _walk(directory, guard):
            transport._portable_path(relative)
            role = inventory.recording_role(
                relative, original=original, job_ids=jobs.get(identifier, set()), attempts=attempts
            )
            if role is not None:
                originals += role == "original"
                yield (
                    path,
                    directory,
                    {
                        "project_id": project,
                        "recording_id": identifier,
                        "role": role,
                        "relative_path": relative,
                    },
                )
        if originals != 1:
            raise ValueError("A complete portable backup requires every original recording.")


def _candidates(db, root, guard):
    from . import portable_preservation

    candidates = list(_recording_files(db, root, guard))
    runtime_ids = {row[0] for row in db.execute("SELECT id FROM runtime_batch_runs")}
    restore_ids = portable_preservation.local(root, guard)
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='restored_owners'"
    ).fetchone():
        recorded = {row[0] for row in db.execute("SELECT DISTINCT restore_id FROM restored_owners")}
        if not recorded <= restore_ids:
            raise ValueError("The library is missing restoration evidence for archived owners.")
    for namespace in ("runtime-batches", "preservation"):
        base = root / namespace
        if base.exists():
            for path, tail in _walk(base, guard):
                relative = namespace + "/" + tail
                role = inventory.library_role(
                    relative, runtime_ids=runtime_ids, restore_ids=restore_ids
                )
                if role is None:
                    continue
                candidates.append(
                    (
                        path,
                        base,
                        {
                            "project_id": None,
                            "recording_id": None,
                            "role": role,
                            "relative_path": relative,
                        },
                    )
                )
    return candidates


def capture_library(store, queue, destination=None, *, gate, guard=None):
    """Seal the whole idle library, including original/evidence bodies, into a new folder.

    The serving lease must remain held through this call. HTTP middleware uses this same
    gate for every mutation. Workers cannot exist at the snapshot boundary, and the exclusive
    request gate prevents new enqueue/control/import/edit operations while bytes are copied.
    """
    from . import deletion_files, portable_semantics, restored_owners

    if not store._lease_depth:
        raise RuntimeError("Portable capture requires the serving library lease.")
    parent = _parents(Path(destination).expanduser() if destination else store.root / "backups")
    parent.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    pending = parent / (".portable-" + identifier + ".partial")
    _windows_path(pending / "objects" / ("a" * 64))
    objects = pending / "objects"
    witnesses, mappings, sizes = [], [], {}
    with gate.capture(), queue.pause_admission():
        with queue.lock, store.snapshot_lock:
            if deletion_files.journal_status(store):
                raise ValueError(
                    "Finish local file recovery or cleanup in Activity "
                    "before making a complete backup."
                )
            if any(worker.active_id or worker.active_batch_id for worker in queue.workers):
                raise ValueError(
                    "Finish or stop active processing before making a complete backup."
                )
            with store.connection() as db:
                operational = restored_owners.sql_exclusion(db, "job", "jobs.id")
                from . import runtime_preparation

                if any(
                    v["state"] in {"queued", "running"}
                    for v in runtime_preparation.obligations(store)
                ):
                    raise ValueError(
                        "Stop active UCloud runtime preparation before making a complete backup."
                    )
                if db.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('queued','running') AND "
                    + operational
                    + " LIMIT 1"
                ).fetchone():
                    raise ValueError(
                        "Finish or cancel queued and running jobs before making a complete backup."
                    )
                # Only an admitted capture reserves its partial folder; a refusal leaves none.
                pending.mkdir()
                objects.mkdir()
                snapshot = pending / ".snapshot.sqlite3"
                with closing(sqlite3.connect(snapshot)) as copied:
                    db.backup(copied)
            source_id = _identity(store.root)
        # The serving mutation gate remains exclusive, but neither the queue lock nor a live
        # DB transaction is held during media I/O. Ordinary reads may continue.
        with closing(sqlite3.connect(snapshot)) as db:
            before = portable_semantics.database_claims(db)
            compacted = pending / ".compacted.sqlite3"
            db.execute("VACUUM INTO ?", (str(compacted),))
        with closing(sqlite3.connect(compacted)) as db:
            claims = portable_semantics.database_claims(db)
            if before != claims:
                raise ValueError("Portable compaction changed stored research values.")
            candidates = _candidates(db, store.root, guard)
            # Snapshot inventory authority and exact bytes are retained separately. A filename
            # is never accepted as proof of a producer receipt.
            gaps = inventory.capture_artifact_requirements(db, candidates, guard=guard)
        for path, base, item in candidates:
            key, count, witness = _copy_object(path, base, objects, guard)
            witnesses.append(witness)
            sizes[key] = count
            mappings.append({**item, "object": key})
        key, count, _ = _copy_object(compacted, pending, objects, guard)
        sizes[key] = count
        for path, base, witness in witnesses:
            transport._same_path(witness, transport._node(path, base))
        with closing(sqlite3.connect(snapshot)) as db:
            if candidates != _candidates(db, store.root, guard):
                raise ValueError("The managed file inventory changed during portable capture.")
        snapshot.unlink()
        compacted.unlink()
        manifest = {
            "format": "cbs-library-backup-v2",
            "backup_id": identifier,
            "source_library_id": source_id,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "scope": "whole_library",
            "inventory_policy": inventory.POLICY,
            "app_version": "0.1.0",
            "schema_version": claims["schema_version"],
            "database": {
                "object": key,
                "size": count,
                "schema_fingerprint": claims["schema_fingerprint"],
                "tables": claims["tables"],
            },
            "objects": [{"sha256": name, "size": size} for name, size in sorted(sizes.items())],
            "files": sorted(
                mappings,
                key=lambda item: (
                    item["project_id"] or "",
                    item["recording_id"] or "",
                    item["role"],
                    item["relative_path"],
                ),
            ),
            "legacy_gaps": gaps,
            "exclusions": "portable-exclusions-v1",
        }
        raw = canonical(manifest)
        durable(pending / "manifest.json", raw)
        marker = canonical(
            {
                "format": "cbs-library-backup-commit-v2",
                "backup_id": identifier,
                "manifest_size": len(raw),
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        durable(pending / "COMMITTED.json", marker)
        reference = hashlib.sha256(marker).hexdigest()
        transport.inspect_transport(pending, expected_commit_sha256=reference, guard=guard)
        portable_semantics.inspect_database(
            objects / key, expected_sha256=key, expected_size=count, manifest=manifest, guard=guard
        )
        target = parent / ("portable-" + identifier)
        result = {
            "folder": str(target),
            "backup_id": identifier,
            "created_at": manifest["created_at"],
            "commit_sha256": reference,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "schema": claims["schema_version"],
            "originals": sum(item["role"] == "original" for item in mappings),
            "files": len(mappings),
            "unique_bytes": sum(sizes.values()),
            "legacy_gaps": len(gaps),
        }
        receipts = _parents(store.root / "portable-captures")
        receipts.mkdir(exist_ok=True)
        # A lost HTTP/UI reply must not lose the independently stored local reference.
        durable(receipts / (identifier + ".json"), canonical(result))
        target.mkdir()  # Exclusive reservation; never os.replace an existing directory.
        (pending / "objects").rename(target / "objects")
        (pending / "manifest.json").rename(target / "manifest.json")
        (pending / "COMMITTED.json").rename(target / "COMMITTED.json")
        pending.rmdir()
    return result


def captures(store):
    """Local creator references survive lost replies; actual bodies are checked on use."""
    root = store.root / "portable-captures"
    if not root.exists():
        return []
    names, _ = transport._entries(root, store.root, maximum=10000, guard=None)
    found = []
    for name in names:
        if not name.endswith(".json"):
            raise ValueError("An unexpected portable creator receipt is present.")
        transport._hex(name[:-5], 32)
        raw, _ = transport._read(root / name, store.root, maximum=64 * 1024, collect=True)
        receipt = json.loads(raw)
        if type(receipt) is not dict or receipt.get("backup_id") != name[:-5]:
            raise ValueError("A portable creator receipt is invalid.")
        transport._hex(receipt.get("commit_sha256"), 64)
        found.append(receipt)
    return sorted(found, key=lambda item: (item["created_at"], item["backup_id"]), reverse=True)
