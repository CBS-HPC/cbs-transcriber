"""Transport-only observation of a caller-owned, quiescent portable staging tree.

This neither opens the database nor approves a backup for restoration. Filesystem witnesses
detect observed drift; they are not a defence against a hostile concurrent namespace owner.
"""

import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LIMITS = {
    "commit_bytes": 4 * 1024,
    "manifest_bytes": 16 * 1024 * 1024,
    "depth": 12,
    "string_bytes": 512,
    "objects": 100_000,
    "files": 100_000,
    "gaps": 10_000,
    "database_bytes": 8 * 1024**3,
    "object_bytes": 2 * 1024**4,
    "unique_bytes": 4 * 1024**4,
    "path_bytes": 240,
    "component_bytes": 80,
}
CHUNK_BYTES = 1024 * 1024
ROLES = {
    "original",
    "import_source",
    "provider_response",
    "processing_input",
    "processing_output",
    "processing_checkpoint",
    "processing_evidence",
}
MANIFEST_FIELDS = {
    "format",
    "backup_id",
    "source_library_id",
    "created_at",
    "scope",
    "inventory_policy",
    "app_version",
    "schema_version",
    "database",
    "objects",
    "files",
    "legacy_gaps",
    "exclusions",
}
NOT_CHECKED = (
    "database_format_and_integrity",
    "schema_compatibility_and_table_claims",
    "owner_and_project_membership",
    "role_resolver_and_capture_completeness",
    "legacy_gap_semantics",
    "credential_exclusion_and_confidential_contents",
    "restore_quarantine_and_activation",
    "destination_space_and_path_compatibility",
)


def _invalid(part):
    raise ValueError(f"The portable package {part} is invalid or changed.")


def _guard(guard):
    if guard is not None:
        guard()


def _record(value, fields, part):
    if type(value) is not dict or set(value) != fields:
        _invalid(part)


def _integer(value, maximum, *, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid("numeric declaration")
    return value


def _hex(value, length):
    if not isinstance(value, str) or not re.fullmatch(rf"[a-f0-9]{{{length}}}", value):
        _invalid("identity or hash")
    return value


def _text(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > LIMITS["string_bytes"]
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        _invalid("text declaration")
    return value


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            _invalid("duplicate JSON key")
        value[key] = item
    return value


def _no_float(_):
    _invalid("JSON number")


def _canonical(data):
    # Preflight structural depth before the decoder allocates nested containers.
    depth, quoted, escaped = 0, False, False
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > LIMITS["depth"]:
                _invalid("JSON depth")
        elif byte in (93, 125):
            depth -= 1
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_float,
        )
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                if len(item.encode("utf-8")) > LIMITS["string_bytes"]:
                    _invalid("JSON string limit")
            elif isinstance(item, dict):
                pending.extend(item)
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _invalid("canonical JSON")
    if encoded != data:
        _invalid("canonical JSON")
    return value


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _same_path(before, after):
    if _signature(before) != _signature(after) or before.st_ctime_ns != after.st_ctime_ns:
        _invalid("filesystem witness")


def _node(path, root, *, directory=False):
    try:
        info = path.lstat()
        linked = stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        contained = path.resolve(strict=True).is_relative_to(root)
    except (OSError, RuntimeError):
        _invalid("filesystem entry")
    if (
        linked
        or not contained
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
    ):
        _invalid("filesystem entry")
    return info


def _read(path, root, *, maximum, size=None, expected_hash=None, collect=False, guard=None):
    _guard(guard)
    before = _node(path, root)
    maximum = min(maximum, size) if size is not None else maximum
    if before.st_size > maximum or (size is not None and before.st_size != size):
        _invalid("file byte count")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError:
        _invalid("file access")
    try:
        try:
            stream = os.fdopen(descriptor, "rb", closefd=False)
        except OSError:
            _invalid("file access")
        chunks, count, digest = [], 0, hashlib.sha256()
        with stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _signature(opened) != _signature(before)
            ):
                _invalid("opened file")
            while True:
                _guard(guard)
                try:
                    chunk = stream.read(min(CHUNK_BYTES, maximum - count + 1))
                except OSError:
                    _invalid("file access")
                if not chunk:
                    break
                count += len(chunk)
                if count > maximum or (size is not None and count > size):
                    _invalid("file byte count")
                digest.update(chunk)
                if collect:
                    chunks.append(chunk)
            after = os.fstat(stream.fileno())
            if _signature(opened) != _signature(after) or opened.st_ctime_ns != after.st_ctime_ns:
                _invalid("opened file witness")
    finally:
        os.close(descriptor)
    final = _node(path, root)
    _same_path(before, final)
    if count != before.st_size or (size is not None and count != size):
        _invalid("file byte count")
    if expected_hash is not None and digest.hexdigest() != expected_hash:
        _invalid("file checksum")
    return b"".join(chunks) if collect else None, before


def _entries(path, root, *, maximum, guard):
    before = _node(path, root, directory=True)
    names = []
    try:
        entries = os.scandir(path)
    except OSError:
        _invalid("directory access")
    with entries:
        while True:
            try:
                entry = next(entries)
            except StopIteration:
                break
            except OSError:
                _invalid("directory access")
            _guard(guard)
            names.append(entry.name)
            if len(names) > maximum:
                _invalid("directory count")
    _same_path(before, _node(path, root, directory=True))
    return set(names), before


def _portable_path(value):
    _text(value)
    parts = value.split("/")
    if (
        len(value.encode("utf-8")) > LIMITS["path_bytes"]
        or "\\" in value
        or ":" in value
        or any(
            part in ("", ".", "..")
            or len(part.encode("utf-8")) > LIMITS["component_bytes"]
            or part.endswith((".", " "))
            or any(char in '<>"|?*' for char in part)
            or re.fullmatch(
                r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]",
                part.split(".", 1)[0].rstrip(" ."),
                re.IGNORECASE,
            )
            for part in parts
        )
    ):
        _invalid("portable path declaration")
    return parts


def _manifest(value, marker):
    _record(value, MANIFEST_FIELDS, "manifest fields")
    if (
        value["format"] != "cbs-library-backup-v2"
        or _hex(value["backup_id"], 32) != marker["backup_id"]
        or value["scope"] != "whole_library"
        or value["inventory_policy"] != "portable-files-v1"
        or value["exclusions"] != "portable-exclusions-v1"
    ):
        _invalid("manifest policy")
    _hex(value["source_library_id"], 32)
    _integer(value["schema_version"], 2**31 - 1, minimum=1)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]*", _text(value["app_version"])):
        _invalid("declared app version")
    date = _text(value["created_at"])
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z", date):
        _invalid("declared capture time")
    try:
        datetime.fromisoformat(date[:-1] + "+00:00")
    except ValueError:
        _invalid("declared capture time")
    objects = value["objects"]
    if type(objects) is not list or not 0 < len(objects) <= LIMITS["objects"]:
        _invalid("object declarations")
    sizes = {}
    for item in objects:
        _record(item, {"sha256", "size"}, "object fields")
        key = _hex(item["sha256"], 64)
        if key in sizes:
            _invalid("duplicate object")
        sizes[key] = _integer(item["size"], LIMITS["object_bytes"])
    if list(sizes) != sorted(sizes) or sum(sizes.values()) > LIMITS["unique_bytes"]:
        _invalid("object order or total")
    database = value["database"]
    _record(database, {"object", "size", "schema_fingerprint", "tables"}, "database fields")
    key = _hex(database["object"], 64)
    size = _integer(database["size"], LIMITS["database_bytes"], minimum=1)
    if key not in sizes or sizes[key] != size:
        _invalid("database reference")
    _hex(database["schema_fingerprint"], 64)
    tables = database["tables"]
    if type(tables) is not dict or not 0 < len(tables) <= LIMITS["objects"]:
        _invalid("declared tables")
    for name, table in tables.items():
        _text(name)
        _record(table, {"rows", "sha256"}, "declared table fields")
        _integer(table["rows"], 2**63 - 1)
        _hex(table["sha256"], 64)
    files = value["files"]
    if type(files) is not list or len(files) > LIMITS["files"]:
        _invalid("file declarations")
    used, destinations, ordering, mapping_bytes = {key}, set(), [], size
    for item in files:
        _record(
            item, {"project_id", "recording_id", "role", "relative_path", "object"}, "file fields"
        )
        project, recording, role = item["project_id"], item["recording_id"], item["role"]
        parts = _portable_path(item["relative_path"])
        if not isinstance(role, str) or role not in ROLES:
            _invalid("file role declaration")
        if project is None and recording is None:
            if (
                role != "processing_evidence"
                or len(parts) < 3
                or parts[0] not in {"runtime-batches", "preservation"}
            ):
                _invalid("library evidence declaration")
            _hex(parts[1], 32)
            destination = item["relative_path"]
        else:
            _hex(project, 32)
            _hex(recording, 32)
            destination = f"projects/{project}/recordings/{recording}/" + item["relative_path"]
        collision = unicodedata.normalize(
            "NFC", unicodedata.normalize("NFC", destination).casefold()
        )
        if collision in destinations:
            _invalid("colliding file declarations")
        destinations.add(collision)
        ordering.append((project or "", recording or "", role, item["relative_path"]))
        key = _hex(item["object"], 64)
        if key not in sizes:
            _invalid("file object reference")
        used.add(key)
        mapping_bytes += sizes[key]
    if ordering != sorted(ordering) or used != set(sizes):
        _invalid("file order or closed object graph")
    gaps = value["legacy_gaps"]
    if type(gaps) is not list or len(gaps) > LIMITS["gaps"]:
        _invalid("gap declarations")
    for gap in gaps:
        _record(gap, {"recording_id", "job_id", "code"}, "gap fields")
        _hex(gap["recording_id"], 32)
        if gap["job_id"] is not None:
            _hex(gap["job_id"], 32)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", _text(gap["code"])):
            _invalid("opaque gap token")
    return sizes, mapping_bytes


@dataclass(frozen=True, slots=True)
class _Capture:
    """Same-call observations, never a public receipt, cache or filesystem ownership token."""

    root: Path
    manifest_bytes: bytes
    backup_id: str
    commit_sha256: str
    manifest_sha256: str
    object_names: tuple[str, ...]
    root_witness: os.stat_result
    objects_witness: os.stat_result
    witnesses: tuple[tuple[str, os.stat_result], ...]
    counts: tuple[int, int, int, int, int]


def _recheck(capture, guard=None):
    root, objects = capture.root, capture.root / "objects"
    _guard(guard)
    if _entries(root, root, maximum=3, guard=guard)[0] != {
        "COMMITTED.json",
        "manifest.json",
        "objects",
    } or _entries(objects, root, maximum=LIMITS["objects"], guard=guard)[0] != set(
        capture.object_names
    ):
        _invalid("final inventory")
    _same_path(capture.root_witness, _node(root, root, directory=True))
    _same_path(capture.objects_witness, _node(objects, root, directory=True))
    for relative, before in capture.witnesses:
        _guard(guard)
        _same_path(before, _node(root / relative, root))


def _observation(capture):
    return {
        "level": "transport_integrity_only",
        "reference": "matched_supplied_commit",
        "backup_id": capture.backup_id,
        "commit_sha256": capture.commit_sha256,
        "manifest_sha256": capture.manifest_sha256,
        "object_count": capture.counts[0],
        "file_mapping_count": capture.counts[1],
        "legacy_gap_count": capture.counts[2],
        "unique_object_bytes": capture.counts[3],
        "declared_mapping_bytes": capture.counts[4],
        "restore_ready": False,
        "not_checked": list(NOT_CHECKED),
    }


def _capture_transport(package_root, *, expected_commit_sha256, guard=None):
    _hex(expected_commit_sha256, 64)
    if guard is not None and not callable(guard):
        _invalid("guard")
    _guard(guard)
    package = Path(package_root)
    if not package.is_absolute():
        _invalid("root")
    try:
        original = package.lstat()
        if stat.S_ISLNK(original.st_mode) or getattr(original, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            _invalid("root")
        root = package.resolve(strict=True)
    except (OSError, RuntimeError):
        _invalid("root")
    names, root_before = _entries(root, root, maximum=3, guard=guard)
    if names != {"COMMITTED.json", "manifest.json", "objects"}:
        _invalid("root inventory")
    data, commit_before = _read(
        root / "COMMITTED.json",
        root,
        maximum=LIMITS["commit_bytes"],
        expected_hash=expected_commit_sha256,
        collect=True,
        guard=guard,
    )
    marker = _canonical(data)
    _record(marker, {"format", "backup_id", "manifest_size", "manifest_sha256"}, "commit fields")
    if marker["format"] != "cbs-library-backup-commit-v2":
        _invalid("commit format")
    _hex(marker["backup_id"], 32)
    size = _integer(marker["manifest_size"], LIMITS["manifest_bytes"], minimum=1)
    _hex(marker["manifest_sha256"], 64)
    data, manifest_before = _read(
        root / "manifest.json",
        root,
        maximum=LIMITS["manifest_bytes"],
        size=size,
        expected_hash=marker["manifest_sha256"],
        collect=True,
        guard=guard,
    )
    manifest = _canonical(data)
    sizes, mapping_bytes = _manifest(manifest, marker)
    objects = root / "objects"
    names, objects_before = _entries(objects, root, maximum=LIMITS["objects"], guard=guard)
    if names != set(sizes):
        _invalid("object inventory")
    witnesses = [
        ("COMMITTED.json", commit_before),
        ("manifest.json", manifest_before),
    ]
    for key, size in sizes.items():
        _, before = _read(
            objects / key,
            root,
            maximum=LIMITS["object_bytes"],
            size=size,
            expected_hash=key,
            guard=guard,
        )
        witnesses.append(("objects/" + key, before))
    capture = _Capture(
        root,
        data,
        marker["backup_id"],
        expected_commit_sha256,
        marker["manifest_sha256"],
        tuple(sizes),
        root_before,
        objects_before,
        tuple(witnesses),
        (
            len(sizes),
            len(manifest["files"]),
            len(manifest["legacy_gaps"]),
            sum(sizes.values()),
            mapping_bytes,
        ),
    )
    _recheck(capture, guard)
    return capture


def inspect_transport(package_root, *, expected_commit_sha256, guard=None):
    """Observe exact stored transport bytes; never returns manifest bodies or restore approval.

    The caller owns a quiescent staging tree and supplies its independent commit reference.
    Any guard exception propagates, with read handles closed and no package writes performed.
    A later consumer must revalidate at use; this observation does not lock future file bytes.
    """
    return _observation(
        _capture_transport(package_root, expected_commit_sha256=expected_commit_sha256, guard=guard)
    )
