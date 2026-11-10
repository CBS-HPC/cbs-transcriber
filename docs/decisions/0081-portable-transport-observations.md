# 0081 — Portable package transport observations

Accepted 28 October 2026 for T91a/S1a, the first internal portion of the portable-v2 brief.
This accepts a sealed-byte transport boundary, not full S1, a backup creator, Restore, remote
upload, supported database schemas, retention policy or institutional approval. Existing
`backup.py`/ADR 0061/v1 behavior remains unchanged.

## Trust and scope

`portable_transport.inspect_transport(package_root, *, expected_commit_sha256, guard=None)`
requires a caller-supplied trusted SHA-256. Never discover a neighbouring receipt or make the
package's own checksum the trust anchor. Custody of the supplied hash remains the caller's
assertion; the result means matched supplied bytes, not an authenticated or safe backup.

The tree must be caller-owned, quiescent staging storage. Read fixed root/`objects` layouts
only. No writes, SQLite/Store opening, registration, schema migration, restoration, credential
lookup, model/runtime access or network is permitted. A guard may cancel between bounded reads;
cancellation produces no successful observation or partial package mutation.

## Bounded grammar and bytes

Hash exact `COMMITTED.json` bytes before following its reference. Its exact fields are format,
backup ID, manifest size and SHA-256. Bind the exact manifest bytes/size next. Both JSON
documents require canonical UTF-8 without BOM, sorted keys, compact separators, literal Unicode,
finite values and no duplicates. Check string-aware nesting before parsing and retain strict
primitive types, including booleans never standing in for integers.

Use the portable-v2 proposal's exact manifest/nested keysets and fixed format/scope/policy
identifiers. Validate declarations structurally: IDs/hashes/timestamp/version syntax, table
claim shapes, known role names, ID pairing, safe relative path declarations and collision-free
declared destinations. Legacy gap codes remain bounded opaque tokens; no semantic enum or
role-specific inventory resolver is adopted here. Never open a `files[].relative_path`.

Limits are 4 KiB commit, 16 MiB manifest, depth 12, 512 UTF-8 bytes per metadata string,
100,000 objects/files each, 10,000 gaps, 8 GiB database object, 2 TiB other object, 4 TiB unique
payload, and 240/80 UTF-8 bytes per relative tail/component. A manifest cannot raise them.
These are stored-input limits, not a measured heap/performance guarantee.

Require sorted unique object declarations and a closed graph: database/file references exist,
sizes agree, every declaration is used, and no physical object or unexpected root entry exists
outside that inventory. Stream each object with an exact size/SHA check; no v1 mtime/hash reuse.
Count unique object bytes separately from `declared_mapping_bytes` (database once plus each
file reference). Neither is a destination-space estimate: restore may retain source/working
databases and migration copies.

Portable declaration checks follow [Microsoft's filename rules](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file),
including reserved device forms and characters. Case/NFC collision checks detect aliases
without normalizing the stored bytes. These checks do not prove actual Windows/Mac restore
acceptance or database ownership.

## File observations

Reject managed symlinks/junctions/reparse entries, nonregular files and regular hardlink aliases.
Check bounded reads after stat as well as before; bind path/descriptor identity and compare
before/after metadata. Following B168, ctime comparisons stay within the same stat API.
Rescan fixed inventories and recheck observed identities before success. Close all handles.
Keep raw descriptor ownership through `fdopen(closefd=False)` and one outer finalizer on every
outcome. A failed owning stream constructor may already have closed/reused its descriptor;
blind exception cleanup can close unrelated I/O. Curate directory iterator-advance I/O errors
independently of caller guards; guard exceptions propagate unchanged, with handles closed
(B196–B198, including a controlled factory-close/reuse fault model).

This is a point-in-time observation under the quiescent-tree precondition. These standard-library
checks do not establish resistance to hostile concurrent Windows namespace replacement or
guarantee files remain available afterward. Later ingestion/restore must hold its own operation
ownership and revalidate; this result never grants copying, activation or cleanup authority.

## Honest result

Return only a dedicated `transport_integrity_only` observation with backup/hash/count fields,
`unique_object_bytes`, `declared_mapping_bytes`, and literal `restore_ready: false`. Explicit
`not_checked` fields retain database format/integrity, schema/table claims, owner/project
membership, role/full-capture completeness, legacy-gap semantics, credential exclusion/private
contents and quarantine/activation. Do not return manifest bodies, filenames, private paths,
source library IDs, or a generic valid/ready/verified-backup boolean.

A deliberately non-SQLite database object may match transported bytes and still has every
database/restore check pending. Full S1/PR02 and T91 remain incomplete until their separate
schema, inventory, capture, quarantine, restore and institutional contracts are implemented.

Verify canonical/type/count/byte bounds, graph/content drift, deduplication accounting, portable
declarations, static links/hardlinks/junctions, guarded reads/cancellation, no writes and v1
refusal with tiny synthetic packages. No real library or private backup is used for this slice.
