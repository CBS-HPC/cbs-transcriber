"""Internal same-call package/database observations (ADR 0090); no Restore authority.

The caller owns quiescent staging. Immutable receipts do not freeze files: final stat checks
cannot prove atomicity or defeat hostile content/metadata restoration. Synthetic acceptance
does not establish a private-data security baseline or future availability.
"""

from . import portable_database, portable_transport

TRANSPORT_FIELDS = (
    "backup_id",
    "commit_sha256",
    "manifest_sha256",
    "object_count",
    "file_mapping_count",
    "legacy_gap_count",
    "unique_object_bytes",
    "declared_mapping_bytes",
)
DATABASE_FIELDS = (
    "database_sha256",
    "database_bytes",
    "schema_version",
    "schema_fingerprint",
    "table_policy",
)
RUNTIME_FIELDS = (
    "sqlite_version",
    "heap_cap_bytes",
    "heap_sentry",
    "worker_sha256_lf",
    "codec_sha256_lf",
    "catalog_sha256",
)
NOT_CHECKED = (
    "supplied_reference_custody_and_authentication",
    "filesystem_atomicity_and_future_availability",
    "history_payloads_and_active_mirrors",
    "owner_and_project_membership",
    "role_resolver_and_capture_completeness",
    "legacy_gap_semantics",
    "credential_exclusion_and_confidential_contents",
    "owner_authority_and_quarantine",
    "restore_and_activation",
    "destination_space_and_path_compatibility",
    "private_data_security_baseline",
)


def inspect_package(package_root, *, expected_commit_sha256, guard=None):
    """Bind package bytes to their declared database claims, with all semantic gaps explicit.

    No public argument accepts a capture, parsed manifest, claims dictionary or cache. The
    supplied commit reference's custody remains the caller's assertion, not authentication.
    Media bodies are hashed once; only the database receives the reader's extra pre/post hashes.
    """
    if type(expected_commit_sha256) is not str:
        raise ValueError("The portable package commit reference is invalid.")
    portable_transport._hex(expected_commit_sha256, 64)
    capture = portable_transport._capture_transport(
        package_root, expected_commit_sha256=expected_commit_sha256, guard=guard
    )
    # Reparse only the exact already-bound bytes, never a reopened/current manifest. No guard
    # intervenes before the unchanged reader validates and copies these derived claims.
    manifest = portable_transport._canonical(capture.manifest_bytes)
    database = manifest["database"]
    observed = portable_database.inspect_database_object(
        capture.root / "objects" / database["object"],
        expected_sha256=database["object"],
        expected_size=database["size"],
        expected_claims={
            "schema_version": manifest["schema_version"],
            "schema_fingerprint": database["schema_fingerprint"],
            "tables": database["tables"],
        },
        guard=guard,
    )
    portable_transport._recheck(capture, guard)
    transport = portable_transport._observation(capture)
    return {
        "level": "package_database_claims_only",
        "reference": "matched_supplied_commit",
        **{key: transport[key] for key in TRANSPORT_FIELDS},
        "database_claims": {
            **{key: observed[key] for key in DATABASE_FIELDS},
            "tables": {
                name: {
                    key: observed["tables"][name][key]
                    for key in ("rows", "sha256", "encoded_bytes")
                }
                for name in portable_database.TABLES
            },
        },
        "inspection_runtime": {key: observed[key] for key in RUNTIME_FIELDS},
        "restore_ready": False,
        "not_checked": list(NOT_CHECKED),
    }
