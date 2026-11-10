# 0090 — Compose package and database observations

Accepted 28 October 2026 for internal T91b2, after independent design review. This binds the
transport observation in ADR 0081 to the supplied-database observation in ADR 0088. It does
not complete full S1/PR02 or introduce capture, public package admission, backup creation,
restore, upload, registration or migration. Semantic history/ownership and institutional
decisions remain separate. Existing S1b/codec/catalog pins and v1 behavior remain unchanged.

## One small interface

`portable_package.inspect_package(package_root, *, expected_commit_sha256, guard=None)`
requires an exact native lowercase SHA-256 string before invoking a caller guard. The caller
owns quiescent local staging and the custody of that reference. There is no input for a parsed
manifest, capture, supplied database claims, previous observation or resume cache.

The module obtains transport evidence during this call, derives the database object and
claims from its captured manifest bytes, runs the existing contained reader, and rechecks
transport witnesses afterward. It returns a dedicated `package_database_claims_only`
observation matched to the supplied commit, with safe allowlisted transport/database metadata,
its own remaining unchecked scopes and literal `restore_ready: false`.

Do not return paths, filenames, source-library/project/recording IDs, raw manifests, SQL or
row bodies. Do not forward the earlier observers' stale unchecked categories as if no binding
were checked, or silently erase their remaining semantic/security limits. A matched commit
reference is not authenticated custody or proof that the database owns every file mapping.

## Private immutable capture

Refactor transport observation only enough to retain a private frozen capture: exact bounded
validated manifest bytes, the resolved native root, immutable hashes/counts/inventories and
tuple/stat witnesses. No parsed mutable graph escapes into that capture. Public
`inspect_transport` keeps its interface, behavior, output and non-SQLite scope unchanged.

The composer creates this capture itself from the supplied root/reference. It never accepts
a capture from its caller or treats a caller-created dictionary as authority. Parse only the
captured bytes in memory, then immediately derive the fixed `root/objects/<database-sha>` path,
size, schema version/fingerprint and table claims. No file-mapping path is opened. Pass these
fresh values to unchanged S1b, which snapshots claims before its first caller guard. Do not
reopen the manifest or make another callback between derivation and that reader call.

After the reader has reaped its child, settled I/O and closed pipes, recheck the captured root
and objects inventories, both directory witnesses and every file witness, including marker,
manifest and the database. A failed check/guard produces no partial successful observation.
Existing byte/count/depth/link/path bounds and descriptive privacy-preserving refusals remain.

Hash each non-database object body once during transport inspection. The database retains
its transport hash plus the reader's pre/post hashes. Rechecking metadata does not introduce
a second full pass over potentially large recordings. Tests must measure these read counts
and prove that claims came from the manifest already bound to the supplied commit.
There is no cross-call hash cache. The reader's 120-second cooperative budget applies to its
database phase; it is not a whole-package deadline for the potentially large transport pass
and final inventory/stat scan. Caller guards remain active throughout those phases.

## State the filesystem assumption precisely

The capture contains immutable bytes/receipts; the filesystem is not made immutable. Final
inventory/stat checks detect observed drift but do not establish an atomic snapshot or defeat
an actor restoring both content and metadata. In particular, Windows ctime semantics cannot
be treated as an unforgeable change counter. Quiescence remains mandatory, and any later
copy/restore must hold its own ownership and revalidate at use. Do not claim fresh end-of-call
body hashes for non-database objects that were read earlier in the call.

The contained database reader still supports only its pinned schema-12 family. Its heap,
deadline, native-parser, runtime-security and private-data acceptance limits remain unchanged.
No public hostile-package security admission, schema-13 compatibility, historical payload or
mirror semantics, project/file membership, role/capture completeness, gap semantics, credential
exclusion, quarantine, destination-space/path compatibility or restore authority follows.

## Evidence required

Run the affected existing transport tests because its private implementation changes, then
new synthetic composition cases: exact manifest-to-reader binding, no external capture or
claim input, immutable receipt fields, guard/cancellation identity, late marker/manifest/object
or inventory drift, link/size/unsupported-schema refusals, safe metadata and measured body-read
counts. Include a real contained-child case using the complete pinned synthetic schema.
Confirm S1b/codec/catalog source pins are unchanged. Do not rerun their completed broad suites
unless a new change or failure justifies it. No private package/library or running app is used.
