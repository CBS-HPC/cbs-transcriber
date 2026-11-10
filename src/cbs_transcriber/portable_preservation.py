"""Fixed restoration-control bindings; no SQLite opening or owner/control authority.

An intact archival receipt establishes which retained source bytes it describes. It does not
authenticate the old package, restore its missing bodies or grant its source allocations.
"""

import hashlib
import json

from . import portable_payload as payload
from . import portable_transport as transport

FILES = {"source.sqlite3", "manifest.json", "COMMITTED.json", "restore-receipt.json"}
RECEIPT_BYTES = 64 * 1024


def validate_controls(receipt_bytes, manifest_bytes, commit_bytes, source_sha256, restore_id):
    """Pure bounded observations of supplied control bytes and the observed source digest."""
    transport._hex(source_sha256, 64)
    transport._hex(restore_id, 32)
    if (
        len(receipt_bytes) > RECEIPT_BYTES
        or len(manifest_bytes) > transport.LIMITS["manifest_bytes"]
        or len(commit_bytes) > transport.LIMITS["commit_bytes"]
    ):
        raise ValueError("The preserved restoration controls exceed the supported byte limit.")
    payload._preflight(receipt_bytes, None)
    receipt = json.loads(
        receipt_bytes,
        object_pairs_hook=payload._pairs,
        parse_float=payload._float,
        parse_constant=payload._constant,
    )
    canonical = json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if canonical != receipt_bytes or type(receipt) is not dict:
        raise ValueError("The preserved restoration receipt is invalid.")
    commit = transport._canonical(commit_bytes)
    transport._record(
        commit, {"format", "backup_id", "manifest_size", "manifest_sha256"}, "preservation commit"
    )
    manifest = transport._canonical(manifest_bytes)
    transport._manifest(manifest, commit)
    if (
        commit["format"] != "cbs-library-backup-commit-v2"
        or commit["manifest_size"] != len(manifest_bytes)
        or commit["manifest_sha256"] != hashlib.sha256(manifest_bytes).hexdigest()
        or receipt.get("format") != "cbs-portable-restore-v1"
        or receipt.get("operation_id") != restore_id
        or receipt.get("state") != "prepared"
        or receipt.get("backup_id") != commit["backup_id"]
        or receipt.get("commit_sha256") != hashlib.sha256(commit_bytes).hexdigest()
        or receipt.get("manifest_sha256") != commit["manifest_sha256"]
        or receipt.get("source_database_sha256") != source_sha256
        or manifest["database"]["object"] != source_sha256
        or type(receipt.get("working_copy")) is not dict
        or receipt["working_copy"].get("schema_version") not in {15, 16, 17, 18, 19}
        or receipt["working_copy"].get("quarantine_policy") != "cbs-restored-owner-quarantine-v1"
    ):
        raise ValueError("The preserved restoration controls disagree with their source bytes.")
    return {
        "restore_id": restore_id,
        "source_backup_id": commit["backup_id"],
        "source_database_sha256": source_sha256,
        "commit_sha256": receipt["commit_sha256"],
    }


def derive(package_object_root, outer_manifest, guard=None):
    """Read only fixed hash-named metadata objects selected by frozen outer mappings."""
    groups = {}
    sizes = {item["sha256"]: item["size"] for item in outer_manifest["objects"]}
    for item in outer_manifest["files"]:
        parts = item["relative_path"].split("/")
        if item["project_id"] is None and len(parts) == 3 and parts[0] == "preservation":
            if (
                item["recording_id"] is not None
                or item["role"] != "processing_evidence"
                or parts[2] not in FILES
            ):
                raise ValueError("The preserved restoration mapping is invalid.")
            transport._hex(parts[1], 32)
            group = groups.setdefault(parts[1], {})
            if parts[2] in group:
                raise ValueError("The preserved restoration mapping is duplicated.")
            group[parts[2]] = item["object"]
    output = []
    for identifier, group in sorted(groups.items()):
        if set(group) != FILES:
            raise ValueError("A preserved restoration control file is missing.")
        raw = {}
        for name, maximum in (
            ("restore-receipt.json", RECEIPT_BYTES),
            ("manifest.json", transport.LIMITS["manifest_bytes"]),
            ("COMMITTED.json", transport.LIMITS["commit_bytes"]),
        ):
            digest = group[name]
            raw[name], _ = transport._read(
                package_object_root / digest,
                package_object_root,
                maximum=maximum,
                size=sizes[digest],
                expected_hash=digest,
                collect=True,
                guard=guard,
            )
        output.append(
            validate_controls(
                raw["restore-receipt.json"],
                raw["manifest.json"],
                raw["COMMITTED.json"],
                group["source.sqlite3"],
                identifier,
            )
        )
    return output


def local(root, guard=None):
    """Resolve fixed archival controls in a trusted source library, including an empty one."""
    base = root / "preservation"
    if not base.exists():
        return set()
    names, _ = transport._entries(base, root, maximum=10000, guard=guard)
    identifiers = set()
    for identifier in sorted(names):
        transport._hex(identifier, 32)
        directory = base / identifier
        if transport._entries(directory, root, maximum=4, guard=guard)[0] != FILES:
            raise ValueError("A preserved restoration control file is missing or unexpected.")
        raw = {}
        for name, maximum in (
            ("restore-receipt.json", RECEIPT_BYTES),
            ("manifest.json", transport.LIMITS["manifest_bytes"]),
            ("COMMITTED.json", transport.LIMITS["commit_bytes"]),
        ):
            raw[name], _ = transport._read(
                directory / name, root, maximum=maximum, collect=True, guard=guard
            )
        payload._preflight(raw["restore-receipt.json"], None)
        receipt = json.loads(raw["restore-receipt.json"], object_pairs_hook=payload._pairs)
        source_hash = receipt.get("source_database_sha256")
        transport._hex(source_hash, 64)
        transport._read(
            directory / "source.sqlite3",
            root,
            maximum=transport.LIMITS["database_bytes"],
            expected_hash=source_hash,
            guard=guard,
        )
        validate_controls(
            raw["restore-receipt.json"],
            raw["manifest.json"],
            raw["COMMITTED.json"],
            source_hash,
            identifier,
        )
        identifiers.add(identifier)
    return identifiers
