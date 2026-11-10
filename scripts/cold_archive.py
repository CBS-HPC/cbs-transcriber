"""Cold file archives for an explicitly stopped app; no restore or activation command.

Uses only stdlib and the reviewed sibling activation_checkpoint helper. All source
roots must be caller-owned and quiescent; an app lease excludes cooperating servers,
not unrelated writers. Partial copies are retained and never resumed/overwritten.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
from contextlib import closing
from pathlib import Path

import activation_checkpoint as checkpoint

FORMAT = "cbs-cold-archive-v1"
EA_POLICY = "windows-ea-size-zero-v1"
MAX_ENTRIES = 100_000
MAX_MANIFEST = 64 * 1024 * 1024
UNSUPPORTED_ATTRIBUTES = 0x4000 | 0x1000 | 0x40000 | 0x400000


class ExtendedAttributesUnsupported(ValueError):
    """A safe CLI reason without exposing an EA name, value or private filename."""


def regular(path):
    path = checkpoint.safe(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Only regular files can be archived.")
    # 0x40000 can mean internal EA metadata outside directory enumeration. Keep
    # ambiguous cases refused; the flag alone is neither recall nor EA-size proof.
    if getattr(info, "st_file_attributes", 0) & UNSUPPORTED_ATTRIBUTES:
        raise ValueError(
            "Encrypted/offline/recall or extended attributes require a separate backup procedure."
        )
    no_extended_attributes(path)
    streams(path)
    return info


def no_extended_attributes(path):
    """Fresh availability enumeration, then EA byte count with metadata-only rights.

    EAs can exist without any st_file_attributes marker. Ordinary Python copying
    loses them, so never accept an EA-bearing node merely because its flags clear.
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    if not path.name or any(character in str(path) for character in "*?"):
        raise ValueError("EA qualification requires an exact ordinary path.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first = kernel.FindFirstFileW
    first.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.WIN32_FIND_DATAW)]
    first.restype = wintypes.HANDLE
    close_find = kernel.FindClose
    close_find.argtypes = [wintypes.HANDLE]
    close_find.restype = wintypes.BOOL
    found = wintypes.WIN32_FIND_DATAW()
    search = first(str(path), ctypes.byref(found))
    if search == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot qualify availability metadata.")
    close_find(search)
    if found.dwFileAttributes & (UNSUPPORTED_ATTRIBUTES | 0x400):
        raise ValueError("Encrypted/offline/recall/reparse enumeration requires a separate backup.")

    class Status(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    query = ctypes.WinDLL("ntdll").NtQueryInformationFile
    query.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Status),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    query.restype = wintypes.LONG
    # FILE_READ_ATTRIBUTES only; OPEN_EXISTING plus BACKUP_SEMANTICS,
    # OPEN_REPARSE_POINT and OPEN_NO_RECALL. Never request READ_DATA/WRITE/READ_EA.
    handle = create(str(path), 0x80, 7, None, 3, 0x02300000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "Cannot open EA metadata read-only.")
    try:
        status, size = Status(), wintypes.ULONG()
        result = query(handle, ctypes.byref(status), ctypes.byref(size), ctypes.sizeof(size), 7)
        if result != 0 or status.information != ctypes.sizeof(size):
            raise OSError(result & 0xFFFFFFFF, "Cannot verify extended-attribute size metadata.")
        if size.value:
            raise ExtendedAttributesUnsupported(
                "Extended attributes are not copied and verified by this procedure."
            )
    finally:
        close(handle)


def streams(path, *, directory=False):
    """Refuse NTFS alternate data streams: ordinary Python copies do not preserve them."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    first = api.FindFirstStreamW
    first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD]
    first.restype = wintypes.HANDLE
    following = api.FindNextStreamW
    following.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    following.restype = wintypes.BOOL
    close = api.FindClose
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    data = StreamData()
    handle = first(str(path), 0, ctypes.byref(data), 0)
    if handle == ctypes.c_void_p(-1).value:
        if directory and ctypes.get_last_error() == 38:  # A directory without streams.
            return
        raise OSError(ctypes.get_last_error(), "Cannot enumerate file streams.")
    try:
        while True:
            if data.name != "::$DATA":
                raise ValueError("Named file streams require a separate backup procedure.")
            if not following(handle, ctypes.byref(data)):
                if ctypes.get_last_error() != 38:  # ERROR_HANDLE_EOF
                    raise OSError(ctypes.get_last_error(), "Cannot enumerate file streams.")
                break
    finally:
        close(handle)


def directory_info(path):
    info = checkpoint.safe(path).stat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("An archive directory changed type.")
    if getattr(info, "st_file_attributes", 0) & UNSUPPORTED_ATTRIBUTES:
        raise ValueError(
            "Encrypted/offline/recall or extended attributes on directories "
            "require a separate procedure."
        )
    no_extended_attributes(path)
    streams(path, directory=True)
    return info


def inventory(root, *, skip_lock=False, bodies=True):
    root = checkpoint.safe(root)
    entries, pending = {}, [root]
    while pending:
        directory = pending.pop()
        info = directory_info(directory)
        relative = directory.relative_to(root).as_posix()
        # SQLite may create/remove transient WAL bookkeeping while opening mode=ro.
        # Directory times are not restored; identity and exact final child names matter.
        entries[relative] = {"kind": "directory", "identity": (info.st_dev, info.st_ino)}
        for path in sorted(directory.iterdir()):
            if skip_lock and path == root / "server.lock":
                continue
            path = checkpoint.safe(path)
            if path.is_dir():
                pending.append(path)
            else:
                info = regular(path)
                entries[path.relative_to(root).as_posix()] = {
                    "kind": "file",
                    "identity": checkpoint.identity(info),
                    "mtime_ns": info.st_mtime_ns,
                    **(checkpoint.file_receipt(path) if bodies else {}),
                }
            if len(entries) + len(pending) > MAX_ENTRIES:
                raise ValueError("Archive inventory exceeds its entry budget.")
    return entries


def contents(entries):
    return {
        name: {key: value for key, value in item.items() if key != "identity"}
        for name, item in entries.items()
    }


def copy_file(source, target, expected):
    before = regular(source)
    if checkpoint.identity(before) != expected["identity"]:
        raise ValueError("A source file changed before copy.")
    digest, count = hashlib.sha256(), 0
    with source.open("rb") as reader, target.open("xb") as writer:
        if checkpoint.identity(os.fstat(reader.fileno())) != expected["identity"]:
            raise ValueError("A source file changed identity before copy.")
        while chunk := reader.read(1024 * 1024):
            count += len(chunk)
            if count > expected["bytes"]:
                raise ValueError("A source file grew while copying.")
            writer.write(chunk)
            digest.update(chunk)
        writer.flush()
        os.fsync(writer.fileno())
        if checkpoint.identity(os.fstat(reader.fileno())) != expected["identity"]:
            raise ValueError("A source file changed while copying.")
    if count != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        raise ValueError("A source file's bytes changed while copying.")
    if checkpoint.identity(regular(source)) != expected["identity"]:
        raise ValueError("A source path changed while copying.")
    shutil.copystat(source, target, follow_symlinks=False)


def copy_root(source, target, expected):
    target.mkdir()
    for name, item in expected.items():
        if name == ".":
            continue
        destination = checkpoint.safe(target / name)
        if item["kind"] == "directory":
            destination.mkdir()
        else:
            copy_file(source / name, destination, item)
    if contents(inventory(target)) != contents(expected):
        raise ValueError("The copied file inventory does not match its source.")


def roots_from_snapshot(snapshot, library, supplied):
    with closing(checkpoint.readonly(snapshot)) as db:
        requested = {
            checkpoint.safe(Path(row[0]))
            for row in db.execute("SELECT storage_dir FROM projects WHERE storage_dir IS NOT NULL")
            if row[0]
        }
    requested.discard(library)
    if set(supplied) != requested:
        raise ValueError("Supply exactly every current external project storage root.")
    # A containing root copies every nested root as well; keep all bindings separately.
    all_roots = sorted({library, *supplied}, key=lambda path: (len(path.parts), str(path)))
    return [
        root
        for root in all_roots
        if not any(root.is_relative_to(p) for p in all_roots if p != root)
    ]


def bind_originals(originals, roots, inventories):
    """Join known byte/absence evidence lexically; never follow a stored source path."""
    for item in originals.values():
        path = Path(item["path"])
        matches = [
            entries.get(path.relative_to(root).as_posix())
            for root, entries in zip(roots, inventories, strict=True)
            if path.is_absolute() and path.is_relative_to(root)
        ]
        if len(matches) != 1:
            raise ValueError("An original does not map to exactly one archived root.")
        actual = matches[0]
        if item["availability"] == "missing" and actual is None:
            continue
        if (
            item["availability"] != "present"
            or actual is None
            or actual["kind"] != "file"
            or any(actual[key] != item[key] for key in ("bytes", "sha256"))
        ):
            raise ValueError("Checkpoint original evidence does not match archived bytes/absence.")


def database_files(library):
    """Raw SQLite authority excludes SHM bookkeeping, which is anchored later as a file."""
    result = {}
    for name in ("library.sqlite3", "library.sqlite3-wal"):
        path = library / name
        try:
            info = regular(path)
        except FileNotFoundError:
            result[name] = None
            continue
        result[name] = {
            "kind": "file",
            "identity": checkpoint.identity(info),
            "mtime_ns": info.st_mtime_ns,
            **checkpoint.file_receipt(path),
        }
        if checkpoint.identity(regular(path)) != result[name]["identity"]:
            raise ValueError("Database byte anchors changed while being observed.")
    return result


def capture(library, project_roots, output):
    if os.name != "nt":
        raise ValueError("Qualified cold archives currently require Windows metadata APIs.")
    library, output = checkpoint.safe(library), checkpoint.safe(output)
    supplied = [checkpoint.safe(root) for root in project_roots]
    if len(set(supplied)) != len(supplied):
        raise ValueError("Duplicate project roots are not accepted.")
    if any(library.is_relative_to(root) for root in supplied):
        raise ValueError("External project roots must not contain the library.")
    for root in [library, *supplied]:
        if output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("Archive output and source roots must be disjoint.")
    if output.exists():
        raise FileExistsError("Archive output must be a new directory.")
    directory_info(library)
    regular(library / "server.lock")  # Metadata-only refusal before opening the lease byte.
    with checkpoint.existing_lease(library):
        recovery = checkpoint.safe(library / "local-deletions")
        if recovery.exists() and any(recovery.iterdir()):
            raise ValueError("Settle local deletion recovery before this ordinary cold archive.")
        count = 0
        for root in [library, *supplied]:
            count += len(inventory(root, skip_lock=root == library, bodies=False))
            if count > MAX_ENTRIES:
                raise ValueError("Combined metadata preflight exceeds its entry budget.")
        output.mkdir()
        # Same existing lease remains held through snapshot, copy and final source recheck.
        observed = checkpoint._capture(
            library, output / "checkpoint", [library, *supplied], "offline", 120
        )
        roots = roots_from_snapshot(output / "checkpoint/library.sqlite3", library, supplied)
        anchored = database_files(library)
        with closing(checkpoint.readonly(library / "library.sqlite3")) as db:
            db.execute("BEGIN")
            actual = checkpoint.fingerprint(db)
            if any(actual[key] != observed["database"][key] for key in actual):
                raise ValueError("Checkpoint database differs from the cold source database.")
        if database_files(library) != anchored:
            raise ValueError("Database byte anchors changed around the logical observation.")
        baselines, count = [], 0
        for root in roots:
            entries = inventory(root, skip_lock=root == library)
            count += len(entries)
            if count > MAX_ENTRIES:
                raise ValueError("Combined archive inventory exceeds its entry budget.")
            baselines.append(entries)
        library_entries = baselines[roots.index(library)]
        if any(library_entries.get(name) != item for name, item in anchored.items()):
            raise ValueError("Database byte anchors differ from the cold file baseline.")
        bind_originals(observed["originals"], roots, baselines)
        for index, (root, expected) in enumerate(zip(roots, baselines, strict=True)):
            copy_root(root, output / f"root-{index:03d}", expected)
        for root, expected in zip(roots, baselines, strict=True):
            if inventory(root, skip_lock=root == library) != expected:
                raise ValueError("A source root changed during the cold archive.")
        for index, expected in enumerate(baselines):
            if contents(inventory(output / f"root-{index:03d}")) != contents(expected):
                raise ValueError("A copied root changed before publication.")
        receipt = {
            "format": FORMAT,
            "metadata_policy": EA_POLICY,
            "library": str(library),
            "project_roots": list(map(str, supplied)),
            "roots": [
                {"source": str(root), "copy": f"root-{index:03d}", "entries": contents(entries)}
                for index, (root, entries) in enumerate(zip(roots, baselines, strict=True))
            ],
            "checkpoint_files": {
                name: checkpoint.file_receipt(output / "checkpoint" / name)
                for name in ("library.sqlite3", "checkpoint.json")
            },
            "missing_original_ids": observed["missing_original_ids"],
            "server_lock_excluded": True,
            "offline_server_lease_held": True,
            "source_before_after_match": True,
            "copied_bytes_verified": True,
            "source_quiescence": "caller_required_not_proven",
            "restore_ready": False,
            "activation_authority": False,
        }
        raw = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        if len(raw) > MAX_MANIFEST:
            raise ValueError("Archive manifest exceeds its byte budget.")
        checkpoint.write_json(output / "ARCHIVE.json", receipt)
        return checkpoint.file_receipt(output / "ARCHIVE.json")["sha256"]


def verify(archive, expected_manifest_sha256):
    if os.name != "nt":
        raise ValueError("Qualified cold archives currently require Windows metadata APIs.")
    archive = checkpoint.safe(archive)
    directory_info(archive)
    if not isinstance(expected_manifest_sha256, str) or not checkpoint.re.fullmatch(
        "[a-f0-9]{64}", expected_manifest_sha256
    ):
        raise ValueError("Supply the independently retained archive manifest SHA256.")
    manifest = checkpoint.safe(archive / "ARCHIVE.json")
    if regular(manifest).st_size > MAX_MANIFEST:
        raise ValueError("Archive manifest exceeds its byte budget.")
    raw = manifest.read_bytes()
    if len(raw) > MAX_MANIFEST or hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
        raise ValueError("The archive manifest does not match the trusted reference.")
    receipt = json.loads(raw)
    if receipt["format"] != FORMAT or not 1 <= len(receipt["roots"]) <= MAX_ENTRIES:
        raise ValueError("Unrecognized archive manifest.")
    if receipt.get("metadata_policy") != EA_POLICY:
        raise ValueError("The source EA qualification policy was not captured; do not retrofit it.")
    expected_top = {"ARCHIVE.json", "checkpoint"}
    total = 0
    for index, root in enumerate(receipt["roots"]):
        name = f"root-{index:03d}"
        if root["copy"] != name:
            raise ValueError("Unexpected copied root identity.")
        expected_top.add(name)
        actual = contents(inventory(archive / name))
        total += len(actual)
        if total > MAX_ENTRIES or actual != root["entries"]:
            raise ValueError("Archived names, types, timestamps or file bytes changed.")
    if {p.name for p in archive.iterdir()} != expected_top:
        raise ValueError("Unexpected archive entries.")
    folder = checkpoint.safe(archive / "checkpoint")
    directory_info(folder)
    if {p.name for p in folder.iterdir()} != {"library.sqlite3", "checkpoint.json"}:
        raise ValueError("Unexpected checkpoint entries.")
    for name in ("library.sqlite3", "checkpoint.json"):
        regular(folder / name)
        if checkpoint.file_receipt(folder / name) != receipt["checkpoint_files"][name]:
            raise ValueError("Checkpoint bytes changed.")
    observed = checkpoint.load_checkpoint(folder)
    bind_originals(
        observed["originals"],
        [Path(root["source"]) for root in receipt["roots"]],
        [root["entries"] for root in receipt["roots"]],
    )
    if receipt["missing_original_ids"] != observed["missing_original_ids"]:
        raise ValueError("Archive and checkpoint missing-original identities differ.")
    # Absolute source names in a trusted manifest are metadata, never file-read authority.
    return {"archive_verified": True, "restore_ready": False, "activation_authority": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    take = commands.add_parser("capture")
    take.add_argument("--library", required=True, type=Path)
    take.add_argument("--project-root", action="append", default=[], type=Path)
    take.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--archive", required=True, type=Path)
    check.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = {"manifest_sha256": capture(args.library, args.project_root, args.output)}
        else:
            result = verify(args.archive, args.expected_manifest_sha256)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError, checkpoint.sqlite3.Error) as error:
        print(json.dumps({"archive_failed": type(error).__name__, "keep_partial": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
