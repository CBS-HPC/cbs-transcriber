"""Restore a sealed package into a new, isolated library; never merge or overwrite.

The parent copies bounded regular bytes. The contained semantic worker alone opens incoming
SQLite and creates its quarantined working copy. Imported remote owners remain historical.
"""

import hashlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import portable_semantics as semantics
from . import portable_transport as transport
from .portable_capture import _parents, _walk, _windows_path, canonical, durable
from .portable_preservation import RECEIPT_BYTES
from .storage import synced

FORMAT = "cbs-portable-restore-v1"


@contextmanager
def operation_lock(path):
    """An OS lock survives crashes safely; age never grants another operation ownership."""
    _parents(path)
    if path.exists():
        transport._node(path, path.parent.resolve())
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
            raise ValueError("This portable operation is already running.") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _journal(path, value):
    raw = canonical(value)
    pending = path.with_suffix(".pending")
    if pending.exists():
        transport._node(pending, path.parent.resolve())
        pending.unlink()
    durable(pending, raw)
    if path.exists():
        transport._node(path, path.parent.resolve())
    pending.replace(path)


def _directories(root, relative):
    transport._portable_path(relative)
    current = root
    transport._node(current, root, directory=True)
    for component in relative.split("/")[:-1]:
        current = current / component
        current.mkdir(exist_ok=True)
        transport._node(current, root, directory=True)
    return root / relative


def _copy(source, source_root, target, root, size, digest, temporary, guard):
    transport._guard(guard)
    _parents(target.parent)
    if target.exists():
        # Complete copies can resume; a modified output is not silently accepted.
        transport._read(target, root, maximum=size, size=size, expected_hash=digest, guard=guard)
        return
    temporary.mkdir(exist_ok=True)
    transport._node(temporary, temporary.parent.resolve(), directory=True)
    pending = temporary / (uuid.uuid4().hex + ".pending")
    before = transport._node(source, source_root)
    if before.st_size != size:
        raise ValueError("A portable source object changed before restoration.")
    descriptor = os.open(
        source,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    count, actual = 0, hashlib.sha256()
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as incoming, pending.open("xb") as output:
            opened = os.fstat(descriptor)
            if transport._signature(opened) != transport._signature(before) or opened.st_nlink != 1:
                raise ValueError("A portable source changed while opening it.")
            while chunk := incoming.read(min(transport.CHUNK_BYTES, size - count + 1)):
                transport._guard(guard)
                count += len(chunk)
                if count > size:
                    raise ValueError("A portable source object changed during restoration.")
                output.write(chunk)
                actual.update(chunk)
            output.flush()
            os.fsync(output.fileno())
            after = os.fstat(descriptor)
            if (
                transport._signature(opened) != transport._signature(after)
                or opened.st_ctime_ns != after.st_ctime_ns
            ):
                raise ValueError("A portable source object changed during restoration.")
        if count != size or actual.hexdigest() != digest:
            raise ValueError("A portable source object failed its restore checksum.")
        transport._same_path(before, transport._node(source, source_root))
        _parents(target.parent)
        os.link(pending, target, follow_symlinks=False)
    finally:
        os.close(descriptor)
        pending.unlink(missing_ok=True)


def _mapping(item):
    if item["project_id"] is None:
        return item["relative_path"]
    return (
        "projects/"
        + item["project_id"]
        + "/recordings/"
        + item["recording_id"]
        + "/"
        + item["relative_path"]
    )


def _target(package, target, live_root):
    chosen = Path(target).expanduser()
    if not chosen.is_absolute() or not chosen.name:
        raise ValueError("Choose an absolute new library folder.")
    parent = _parents(chosen.parent)
    if not parent.is_dir() or synced(parent):
        raise ValueError("Choose an existing, local unsynced parent for the restored library.")
    chosen = parent / chosen.name
    _windows_path(chosen, directory=True)
    transport._portable_path(chosen.name)
    if chosen == package or chosen.is_relative_to(package) or package.is_relative_to(chosen):
        raise ValueError("Restore into a new folder outside the source package.")
    if live_root is not None:
        live = Path(live_root).resolve()
        if chosen == live or chosen.is_relative_to(live) or live.is_relative_to(chosen):
            raise ValueError("Restore into a separate new library outside the open library.")
    return chosen


def _verify_output(root, manifest, marker, receipt, guard):
    sizes = {item["sha256"]: item["size"] for item in manifest["objects"]}
    expected = {
        _mapping(item): (sizes[item["object"]], item["object"]) for item in manifest["files"]
    }
    identifier = receipt["operation_id"]
    prefix = "preservation/" + identifier + "/"
    database = manifest["database"]
    expected[prefix + "source.sqlite3"] = (database["size"], database["object"])
    for name, raw in (
        ("manifest.json", canonical(manifest)),
        ("COMMITTED.json", marker),
        ("restore-receipt.json", canonical(receipt)),
    ):
        expected[prefix + name] = (len(raw), hashlib.sha256(raw).hexdigest())
    expected["portable-library-id"] = (32, hashlib.sha256(identifier.encode("ascii")).hexdigest())
    working = receipt["working_copy"]
    expected["library.sqlite3"] = (
        working["working_database_bytes"],
        working["working_database_sha256"],
    )
    found = set()
    for path, relative in _walk(root, guard):
        if relative in {"server.lock", "restore-pending"}:
            continue
        if relative not in expected:
            raise ValueError("An undeclared file appeared in the staged restore.")
        size, digest = expected[relative]
        transport._read(path, root, maximum=size, size=size, expected_hash=digest, guard=guard)
        found.add(relative)
    if found != set(expected):
        raise ValueError("A required staged restore file is missing.")


def _reserve_target(staging, target, identifier, guard):
    """Publish the incomplete marker with the destination, never an unmarked directory."""
    from .deletion_files import _rename, _sync_directory

    transport._hex(identifier, 32)
    if staging != target.parent / (".restore-" + identifier + ".partial"):
        raise ValueError("The restore reservation belongs to another operation.")
    _parents(staging)
    transport._node(staging, target.parent, directory=True)
    reservation = staging / "reservation"
    pending = reservation / "restore-pending"
    _windows_path(reservation, directory=True)
    _windows_path(pending)
    reservation.mkdir(exist_ok=True)
    names, _ = transport._entries(reservation, staging, maximum=1, guard=guard)
    if names - {"restore-pending"}:
        raise ValueError("An unexpected file appeared in the restore reservation.")
    expected = identifier.encode("ascii")
    if pending.exists():
        raw, _ = transport._read(pending, staging, maximum=32, collect=True, guard=guard)
        if not expected.startswith(raw):
            raise ValueError("The restore reservation marker belongs to another operation.")
        if raw != expected:
            # Only this operation's unpublished, quiescent staging may repair its short
            # marker write. An existing final target never enters this branch.
            pending.unlink()
    if not pending.exists():
        durable(pending, expected)
    transport._read(
        pending,
        staging,
        maximum=32,
        size=32,
        expected_hash=hashlib.sha256(expected).hexdigest(),
        guard=guard,
    )
    _sync_directory(reservation)
    _parents(target.parent)
    transport._guard(guard)
    _rename(reservation, target)
    _sync_directory(staging)
    _sync_directory(target.parent)


def _activate(staging, target, journal, manifest, marker, receipt, register, guard):
    """Reserve a new target, retain interrupted moves and revalidate before registration."""
    identifier = receipt["operation_id"]
    library = staging / "library"
    if target.exists():
        try:
            (staging / "reservation").lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError(
                "The unpublished restore reservation still exists; "
                "the existing destination cannot be adopted."
            )
        transport._node(target, target.parent, directory=True)
        pending = target / "restore-pending"
        if pending.exists():
            transport._read(
                pending,
                target,
                maximum=32,
                size=32,
                expected_hash=hashlib.sha256(identifier.encode("ascii")).hexdigest(),
                guard=guard,
            )
        else:
            # A crash after removing the pending marker but before registry/journal save
            # is recoverable only if every complete byte still matches this exact receipt.
            _verify_output(target, manifest, marker, receipt, guard)
    else:
        _reserve_target(staging, target, identifier, guard)
    _journal(journal, {**receipt, "state": "activating"})
    with operation_lock(target / "server.lock"):
        if library.exists():
            for child in sorted(library.iterdir()):
                transport._guard(guard)
                destination = target / child.name
                if destination.exists():
                    raise ValueError(
                        "A conflicting file appeared while activating the restored library."
                    )
                child.rename(destination)
        _verify_output(target, manifest, marker, receipt, guard)
        (target / "restore-pending").unlink(missing_ok=True)
        registered, registration_error = False, None
        try:
            if register is not None:
                register(target)
                registered = True
        except (OSError, ValueError, RuntimeError):
            registration_error = (
                "The complete restored library was retained. Register or open this folder again."
            )
        complete = {
            **receipt,
            "state": "complete",
            "registered": registered,
            "registration_error": registration_error,
        }
        _journal(journal, complete)
    return complete


def restore_library(
    package_root,
    *,
    expected_commit_sha256,
    target,
    operation_id=None,
    live_root=None,
    register=None,
    guard=None,
):
    """Copy, verify and publish a new library; return a receipt and registration status.

    A failed final registration leaves the complete target intact. A partial operation is
    resumable only with the same operation ID, trusted reference and exact destination.
    Caller-owned quiescent package/staging storage is required throughout the operation.
    """
    from . import restored_owners

    restored_owners.require_supported_activation()
    package = _parents(Path(package_root).expanduser())
    target = _target(package, target, live_root)
    identifier = operation_id or uuid.uuid4().hex
    transport._hex(identifier, 32)
    staging = target.parent / (".restore-" + identifier + ".partial")
    journal = target.parent / (".restore-" + identifier + ".json")
    binding = {
        "format": FORMAT,
        "operation_id": identifier,
        "target": str(target),
        "commit_sha256": expected_commit_sha256,
    }
    with operation_lock(target.parent / (".restore-" + identifier + ".lock")):
        previous = None
        if journal.exists():
            raw, _ = transport._read(journal, target.parent, maximum=64 * 1024, collect=True)
            previous = json.loads(raw)
            if any(previous.get(key) != value for key, value in binding.items()):
                raise ValueError("The restore operation belongs to another package or destination.")
        elif staging.exists() or target.exists():
            raise ValueError("The restore destination already exists. Choose a new library folder.")
        else:
            _journal(journal, {**binding, "state": "inspecting"})
        capture = transport._capture_transport(
            package, expected_commit_sha256=expected_commit_sha256, guard=guard
        )
        manifest = transport._canonical(capture.manifest_bytes)
        database = manifest["database"]
        # Check real expanded paths, including the longer staging prefix, before materializing
        # anything. Portable relative-tail validity alone cannot establish Windows acceptance.
        for base in (target, staging / "library"):
            _windows_path(base, directory=True)
            for relative in [
                "library.sqlite3",
                "preservation/" + identifier + "/restore-receipt.json",
                *(_mapping(item) for item in manifest["files"]),
            ]:
                path = base / relative
                _windows_path(path)
                _windows_path(path.parent, directory=True)
        marker, _ = transport._read(
            package / "COMMITTED.json",
            package,
            maximum=transport.LIMITS["commit_bytes"],
            expected_hash=expected_commit_sha256,
            collect=True,
            guard=guard,
        )
        if previous is not None and previous.get("state") in {"prepared", "activating", "complete"}:
            receipt = {
                key: value
                for key, value in previous.items()
                if key not in {"registered", "registration_error"}
            }
            receipt["state"] = "prepared"
            transport._recheck(capture, guard)
            return _activate(staging, target, journal, manifest, marker, receipt, register, guard)
        observation = semantics.inspect_database(
            package / "objects" / database["object"],
            expected_sha256=database["object"],
            expected_size=database["size"],
            manifest=manifest,
            guard=guard,
        )
        audio = observation.get("required_audio", [])
        observation = {key: value for key, value in observation.items() if key != "required_audio"}
        observation["required_audio_count"] = len(audio)
        observation["required_audio_sha256"] = hashlib.sha256(canonical(audio)).hexdigest()
        required = capture.counts[4] + database["size"] * 2 + 16 * 1024**2
        if shutil.disk_usage(target.parent).free < required:
            raise ValueError("There is not enough free space for an isolated verified restore.")
        staging.mkdir(exist_ok=True)
        transport._node(staging, target.parent, directory=True)
        library = staging / "library"
        library.mkdir(exist_ok=True)
        temporary = staging / "copies"
        temporary.mkdir(exist_ok=True)
        preservation = library / "preservation" / identifier
        preservation.mkdir(parents=True, exist_ok=True)
        transport._node(preservation, library, directory=True)
        _journal(journal, {**binding, "state": "copying", "backup_id": manifest["backup_id"]})
        sizes = {item["sha256"]: item["size"] for item in manifest["objects"]}
        for item in manifest["files"]:
            relative = _mapping(item)
            destination = _directories(library, relative)
            digest = item["object"]
            _copy(
                package / "objects" / digest,
                package,
                destination,
                library,
                sizes[digest],
                digest,
                temporary,
                guard,
            )
        source = preservation / "source.sqlite3"
        _copy(
            package / "objects" / database["object"],
            package,
            source,
            library,
            database["size"],
            database["object"],
            temporary,
            guard,
        )
        for name, raw in (
            ("manifest.json", capture.manifest_bytes),
            ("COMMITTED.json", marker),
        ):
            path = preservation / name
            if path.exists():
                transport._read(
                    path,
                    library,
                    maximum=len(raw),
                    size=len(raw),
                    expected_hash=hashlib.sha256(raw).hexdigest(),
                    guard=guard,
                )
            else:
                durable(path, raw)
        prepared = semantics.prepare_restore(
            source,
            library / "library.sqlite3",
            expected_sha256=database["object"],
            expected_size=database["size"],
            manifest=manifest,
            target_root=target,
            restore_id=identifier,
            artifact_object_root=package / "objects",
            guard=guard,
        )
        identity = library / "portable-library-id"
        if identity.exists():
            transport._read(
                identity,
                library,
                maximum=32,
                size=32,
                expected_hash=hashlib.sha256(identifier.encode("ascii")).hexdigest(),
                guard=guard,
            )
        else:
            durable(identity, identifier.encode("ascii"))
        transport._recheck(capture, guard)
        for item in manifest["files"]:
            digest = item["object"]
            transport._read(
                library / _mapping(item),
                library,
                maximum=sizes[digest],
                size=sizes[digest],
                expected_hash=digest,
                guard=guard,
            )
        receipt = {
            **binding,
            "state": "prepared",
            "backup_id": manifest["backup_id"],
            "source_database_sha256": database["object"],
            "manifest_sha256": capture.manifest_sha256,
            "research_observation": observation,
            "working_copy": prepared,
        }
        if len(canonical(receipt)) > RECEIPT_BYTES:
            raise ValueError(
                "The restoration control receipt exceeds its supported metadata budget."
            )
        saved = preservation / "restore-receipt.json"
        if saved.exists():
            transport._read(
                saved,
                library,
                maximum=len(canonical(receipt)),
                size=len(canonical(receipt)),
                expected_hash=hashlib.sha256(canonical(receipt)).hexdigest(),
                guard=guard,
            )
        else:
            durable(saved, canonical(receipt))
        _journal(journal, receipt)
        _verify_output(library, manifest, marker, receipt, guard)
        if target.exists():
            raise ValueError(
                "The restore destination appeared during preparation; the staged copy was retained."
            )
        return _activate(staging, target, journal, manifest, marker, receipt, register, guard)
