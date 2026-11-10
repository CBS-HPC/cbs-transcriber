# Portable backup v2: local packaging and safe restoration brief

27 October 2026. **Proposed implementation contract, not accepted behavior or delivered code.**
This continues the [T91 proposal](ucloud-backup-restore-2026-10-27.md). It covers self-contained
local packages and restoration into a new folder first. Upload, remote deletion, scheduling,
encryption deployment and institutional approval are outside this slice. No private database,
credential file, network service or actual restore was used to prepare this document. Existing
code, specifications, ADRs, TODO entries and deletions remain unchanged.

## 1. Boundary and existing behavior

[backup.py](../../src/cbs_transcriber/backup.py) currently writes a backup-API SQLite snapshot
and a `cbs-library-backup-v1` inventory of original media; it does not copy the originals or
provide restore. [Its tests](../../tests/test_backup.py) establish that behavior, including
hash reuse based on size/mtime. Keep v1 generation and inspection compatible. Do not show a v1
folder as a complete v2 package, or silently follow its source-machine paths to “restore” it.

The v2 module should own four narrow operations: `prepare(store, destination, cancel)`,
`inspect(package, trusted_receipt)`, `restore_stage(package, target, trusted_receipt, cancel)`
and `activate(staged_restore)`. `inspect` and `restore_stage` cannot construct `Store` on an
untrusted database or register/open a library. `activate` is introduced only after operational
quarantine exists. Reuse streaming hashing and snapshot verification where their contracts fit;
do not overload current-view exports or create a general archive/extraction framework.

First supported scope is the whole library. All historical, trashed and private review data in
the database is included. Project-only export, redaction, incremental backups, cross-backup
deduplication, repair of a damaged library and replacement/merge into an existing library are
separate features. A package restores the captured state, not changes made after capture.

## 2. Directory and bounded manifest contract

Use a generated directory, not ZIP/TAR. No executable scripts, links or permissions are
restored. Duplicate bytes are shared only inside this package:

```text
portable-<backup_id>/
    objects/<sha256>       # regular files; names are exactly 64 lowercase hex characters
    manifest.json         # canonical UTF-8 JSON, no BOM
    COMMITTED.json        # written last after verification
```

Write into a separate `portable-<backup_id>.partial` directory. Canonical JSON means sorted
object keys, compact separators, literal Unicode, finite numbers and no duplicate keys; input
validation rejects noncanonical bytes rather than interpreting another spelling as identical.
Arrays retain their defined order. Hash exact bytes, not a decoded/re-encoded approximation.
`COMMITTED.json` has exactly `format="cbs-library-backup-commit-v2"`, `backup_id`,
`manifest_size`, and `manifest_sha256`. It never hashes itself. A separate trusted local
receipt records both the exact commit hash and manifest hash for authenticity/inventory binding.
Keeping that receipt only beside the package provides corruption detection, not independent
protection against replacement of the entire package.

These are proposed implementation limits, not measured capacity or already accepted product
defaults. Put them in one format-policy constant and test boundaries; a manifest cannot raise
them. A future larger format needs explicit compatibility handling, not silent unbounded input.

| Limit | Initial v2 contract |
| --- | --- |
| Commit / manifest bytes | 4 KiB / 16 MiB, checked before JSON parsing |
| JSON nesting / one metadata string | 12 levels / 512 UTF-8 bytes |
| Object records / file mappings / declared legacy gaps | 100,000 / 100,000 / 10,000 |
| Database object / one other object / unique payload total | 8 GiB / 2 TiB / 4 TiB |
| Relative file tail / one path component | 240 UTF-8 bytes / 80 UTF-8 bytes; also pass destination-platform limits |
| Hash / app-generated ID | 64 / 32 lowercase hexadecimal characters |
| Integers | Exact nonnegative integers within the relevant bound; booleans are never integers |

Reject unknown fields and roles for v2. No filename, project title, source absolute path,
arbitrary worker error or credential is needed in the outer manifest. Display names remain in
the confidential database. The required top-level manifest fields are:

| Field | Type and invariant |
| --- | --- |
| `format` | Exact string `cbs-library-backup-v2` |
| `backup_id`, `source_library_id` | Generated IDs; the latter identifies the source instance, not a server path |
| `created_at` | UTC RFC3339 timestamp ending in `Z` |
| `scope`, `inventory_policy` | Exact strings `whole_library`, `portable-files-v1` |
| `app_version`, `schema_version` | Bounded version string and supported positive schema integer |
| `database` | Exactly `{object, size, schema_fingerprint, tables}`; object names one object record, size agrees, tables maps each expected table to `{rows, sha256}` |
| `objects` | Sorted array of unique `{sha256, size}` records, including exactly the database and objects referenced by file mappings |
| `files` | Sorted by `(project_id or "", recording_id or "", role, relative_path)`; each entry is exactly `{project_id, recording_id, role, relative_path, object}`; paired null owner IDs are permitted only for validated library evidence |
| `legacy_gaps` | Bounded `{recording_id, job_id, code}` records, nullable job ID; codes are an explicit enum, never guessed worker prose |
| `exclusions` | Exact policy identifier `portable-exclusions-v1`, not a caller-supplied pattern list |

The object graph must close: every reference exists, its size agrees, every declared object is
used, and no undeclared file exists under `objects/`. Compute both unique stored bytes and
expanded restored bytes; deduplication cannot conceal a larger destination-space requirement.
There is exactly one database mapping, outside recording file mappings. Unknown schema tables
or extra artifact formats block this version instead of being silently omitted.
Every recording in the snapshot has exactly one original mapping; every nonnull owner exists
and has the declared project membership. Extra files cannot create projects or recordings.

Roles are `original`, `import_source`, `provider_response`, `processing_input`,
`processing_output`, `processing_checkpoint` and `processing_evidence`. A maintained inventory
resolver validates each role against known managed filename rules and database ownership;
roles cannot authorize arbitrary filesystem reads. Examples are `original.wav`,
`sources/<source-id>.json`, `ucloud-<job-id>.json`,
`chunks/<job-id>/response-0000.json` and known `enhancements/<job-id>/...` artifacts.
Library-scoped `processing_evidence` additionally covers the exact managed
`runtime-batches/<batch-id>/...` control/discard evidence and `preservation/<restore-id>/...`
restoration records. A batch ID must match the database; a restoration directory must have
its validated source-receipt binding. No arbitrary global path is an inventory candidate.
T43 evidence must use its accepted artifact inventory when implemented. Do not predeclare
unknown evidence files complete merely because their suffix is `.json`.

## 3. Capture and exact preservation

Use the serving Store's lease and a capture gate that excludes new mutations and active local
workers/transfers/snapshot writers. Do not acquire another server lease for the live library.
Keep the queue/SQLite locks short: set the gate and establish the snapshot/inventory, then do
bounded file copying under that gate with cancellation/progress. Finally release the gate on
every outcome. Read-only browsing can continue. No network operation belongs in this path.

Take the SQLite backup-API snapshot. Compact a separate copy to avoid carrying unused deleted
pages, and compare exact logical values/schema before accepting the compacted object. Never
upload/include the uncompacted snapshot as a second hidden payload. A streaming table digest
uses approved column order, primary-key order and a versioned length-prefixed encoding with
distinct null/integer/IEEE-754-real/UTF-8-text/blob tags. JSON stored in TEXT stays the exact
string; it is never parsed and rewritten to calculate a table digest. Schema fingerprints use
an approved structural inventory, including indexes, foreign keys and exact known trigger/view
definitions. Do not interpolate names supplied by an untrusted database into verification SQL.

For every original and required evidence file, resolve the ID-owned source from the snapshot,
copy it into a pending object, hash the copied bytes, verify source identity/size before and
after, flush/close it, and promote to its hash name. Recheck an existing same-hash object before
deduplicating. Do not use v1's size/mtime shortcut as proof, use hardlinks to source files, or
normalize text/newlines. A missing original or a file promised by a captured artifact receipt
fails preparation. Evidence never captured by a legacy producer is a declared gap, not newly
invented evidence. Available imported source files without result attribution remain available
with an explicit attribution gap.

Include all originals, imported sources, raw responses, request/stage/window JSON and available
preservation artifacts selected by the role resolver. Exclude derived playback/chunk audio
that can be recreated from retained originals and exact bounds, exports/project snapshots,
model caches, virtual environments, application source, prior backup trees, partial files,
locks, WAL/SHM and machine library registry. Credential/config files, private keys, session URLs
and tokens are never inventory candidates. Test structured credential exclusions with canaries;
literal interview content is not automatically censored. The database still contains private
notes, review reasons and historical configuration/paths and must be handled accordingly.

Seal only after integrity, foreign keys, active-result mirrors, every immutable payload hash,
table fingerprints and every object hash verify. Write manifest and commit through pending
files, flush and close them, then promote the package using a verified no-overwrite operation.
A failed final UI refresh cannot remove a committed package. Interrupted preparation retains
only a clearly owned partial operation or cleans that exact resolved staging directory after
its I/O has settled; never publish a partial package or remove originals.

## 4. Restore paths and working-copy differences

First implement restore into a **nonexistent** final directory with an existing local unsynced
parent. If the UI accepts an existing empty folder, use a new generated child and show its
actual final path; do not silently replace the chosen folder. Arbitrary existing-library merge
or overwrite is unsupported. Reject source/package descendants, the live library, nonempty
targets, symlink/junction parents, and source/target aliases after resolution. A final target
appearing during restore causes conflict; a precheck is not permission to replace it later.

The restorer computes destinations. A mapping goes to
`<new-root>/projects/<full-project-id>/recordings/<recording-id>/<validated-relative-path>`.
Project IDs and recording membership must match the snapshot. All mappings must have distinct
destination names after Windows/macOS case/normalization collision checks. A library-scoped
evidence mapping uses only its approved `runtime-batches/` or `preservation/` namespace under
the new root. Refuse a leading `/`, every `\`, drive/UNC prefixes, `..`, `.`, empty components,
alternate data streams, control/NUL characters,
reserved Windows device names, trailing dots/spaces and platform-unsupported lengths. The
manifest cannot provide an absolute storage directory. Originals retain generated physical
names; user display filenames never become destination paths. Reject every link/reparse escape
while opening sources/creating destinations, not just once before a long copy. Copy regular
bytes into new files rather than hardlinking the package or another recording's original.

Restore has a sibling staging directory with a durable operation ID and receipt bound to the
trusted commit/manifest hash and final target. Object copies restart from byte zero after an
interruption unless the complete hash verifies; the first stage does not promise byte-range
resume. An operation cannot resume against a different package or follow paths from its old
machine. Validate actual expanded free-space requirements and cancellation between bounded
chunks. A restore journal contains safe IDs/hashes/status, not credentials or source text.

Keep the verified database object unchanged in the restored preservation area. Build the live
database from a separate working copy, with this generated layout:

```text
<new-root>/library.sqlite3
<new-root>/projects/<project-id>/recordings/<recording-id>/...
<new-root>/runtime-batches/<imported-batch-id>/...          # historical evidence only
<new-root>/preservation/<restore-id>/source.sqlite3
<new-root>/preservation/<restore-id>/manifest.json
<new-root>/preservation/<restore-id>/COMMITTED.json
<new-root>/preservation/<restore-id>/restore-receipt.json
```

The source database and control records are restoration evidence, not a nested copy of the
entire package. Subsequent packaging preserves this evidence through its library-scoped role;
it never recursively includes backup trees. Existing restoration/quarantine records are kept,
and a new restore adds coverage for newly imported IDs without rewriting earlier ancestry.

Only these pre-activation working-copy differences are allowed:

| Allowed working difference | Exact restriction |
| --- | --- |
| Project storage roots | Rebind `projects.storage_dir` to generated paths under this new root; record old/new mapping in a local confidential restore receipt |
| Current machine/provider preferences | Disconnect current providers and backup destination, disable current model/Protected approvals, reset interpreter/SSH paths to this machine's unconfigured defaults; do not rewrite frozen job snapshots or historical processing evidence |
| Restoration quarantine | Add the narrow schema/table described below and mark imported owner IDs; fresh restore/library UUID and source receipt stay local |
| Playback readiness | Mark missing derived playback as needing local preparation; creating new media jobs is a later explicit activation action, not a replay of imported queue rows |
| Approved schema migrations | Apply only on this working copy, with the normal migration backup and exact preservation tests; record both source and resulting schema/hash |

No other table/value drift is accepted. In particular, preserve notes and notes revisions,
collections, result/speaker text, timings, payload hashes, result/version/source IDs, coding
reviews, hints and Trash flags, including absent/deleted rows at capture. Do not activate a
trashed result, invent producer evidence, clear a failed queue receipt or regenerate exports
over research data. A backup predating later consent withdrawal/deletion requires the agreed
retention handling; restoration does not erase those later obligations on the source system.

## 5. Imported jobs are archival; cleanup ownership stays with its source

This is a prerequisite for public restore, not a UI-only badge. Add one small next-schema
table, proposed as:

```sql
CREATE TABLE restored_owners (
    kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    restore_id TEXT NOT NULL,
    source_backup_id TEXT NOT NULL,
    PRIMARY KEY (kind, owner_id)
);
```

`kind` is validated against `job`, `native_batch`, `runtime_batch`. No cascading foreign key:
source-owner evidence must survive missing/deleted member rows. The next schema version makes
older apps refuse the restored library. Existing source libraries receive an empty additive
table through a separately reviewed migration with backup/rollback tests; no original owner
is marked historical. Populate all imported job and batch IDs atomically in the working copy
before a live database can be promoted. Reject incomplete quarantine coverage. The restore
receipt records coverage counts/fingerprints, immutable source database hash and operation ID.

Every claim and mutation boundary must consult the same persisted ownership check. Coverage
includes ordinary media/API/enhancement claims; native/dynamic batch claims and late admission;
Retry/new attempts, Stop/cancel, reconciliation, remote cleanup, startup recovery and producer
callbacks. A copied pending job is not a new request. An imported owner cannot gain authority
because the OS keyring already contains a credential for its endpoint, or because its remote
job is still RUNNING. Do not clear imported journals, rewrite their run IDs, resolve them as
absent, or terminate/trash anything on restore. Original cleanup remains an obligation of the
original source instance, with its original local preservation evidence.

Activity may show archived obligations as read-only historical information. They cannot offer
operational actions or permanently block this copy's library switch, project storage move or
deletion. New operations in the restored library get new IDs and fresh source/approval checks
and are not added to `restored_owners`; their own unresolved cleanup still blocks destructive
actions normally. This must work after all imported member jobs/recordings disappear.

Retain the restoration receipt and source object while validation/activation is incomplete.
No cancellation, Clear finished, project deletion, failed activation or restore retry is
authorization to delete the source package or source remote data. Local staging cleanup owns
only its generated operation directory, checks resolved containment/ownership and waits for
all file handles/futures to settle. After a successful promotion, a finalization error retains
the committed target and offers registration recovery; it must never roll back by deleting it.

## 6. Eventual activation and decisions before exposure

Activation is a distinct local action after a ready staging receipt: revalidate the source
binding, working database, quarantine coverage and exact allowlisted differences under an
operation lock outside the staging tree. Close/flush handles before no-overwrite directory
promotion; moving a tree while holding an open lock/database file must not be assumed to work
on Windows. After promotion, acquire the target library lease for final registration. If another
server already acquired it, leave the complete target intact and report registration recovery;
do not mutate or remove it. Then register the new library in the chooser. The original library
stays open until the user separately chooses the restored
one through the existing idle-switch flow. Do not register partial copies. Local playback can
then be regenerated through newly created media jobs, preserving original research timings;
remote processing stays disconnected until explicitly configured as a new run.

The following decisions must be accepted before exposing a general Restore action. They do
not block synthetic, local-only development of the earlier slices:

- Product: supported schema/version set, proposed limits, trusted-receipt UX, treatment of
  missing legacy evidence, target-folder semantics, operational reset preview and local media
  preparation. There is no “ignore integrity errors” default or silent salvage mode.
- Engineering/research: accepted artifact inventory including T43, table-fingerprint encoding,
  quarantine coverage for every worker/endpoint, portable path rules and Windows/macOS evidence.
- Institutional/private rollout: approved storage/location and full-library scope; encryption
  and key/receipt custody; role/access/support ownership; retention, legal holds, withdrawal,
  expiry and Trash handling. Do not treat current ASR Protected approval as backup permission.
  This local format creates no UCloud upload permission or destructive retention default.

The installed release `ucloud-workflow 0.1.9` / commit
`a521fe7546e2735d0e8d9872c2486e02ad6b6fc8` supplies paginated project/drive discovery,
contained storage-path validation and conflict-rejecting folder creation. Its setup helpers
are read-only with `create=False`; the local packaging/restore stage must not call any of them.
It supplies no portable-library restorer or provider-checksum guarantee. Later upload can use
the sealed object/manifest/commit bytes, explicit settings and the existing direct transfer
seam, with download-back verification; that is a separate reviewed slice. No `.env`, CLI setup,
SSH workflow, compute allocation or token-management command is needed here. Evidence sources
are the [prior capability review](ucloud-backup-restore-2026-10-27.md#4-released-ucloud-capabilities-and-transfer-recovery)
and the release's [setup implementation](https://github.com/CBS-HPC/ucloud-api/blob/a521fe7546e2735d0e8d9872c2486e02ad6b6fc8/src/ucloud_workflow/setup.py).

## 7. Executable contract-test matrix

These are test specifications, not tests already run or files already delivered. Use temporary
synthetic libraries and generated tiny originals; mock all credentials/transports and fail if
any network/model/runtime preparation function is called. Suggested future command:

```text
uv run pytest tests/test_portable_backup.py tests/test_portable_restore.py tests/test_restore_quarantine.py -q
```

Each row names a runnable assertion; table-fingerprint and byte comparisons must cover whole
fixtures rather than mirror the helper's own implementation.

| ID / suggested case | Fixture/action | Required assertion |
| --- | --- | --- |
| PB01 `v1_remains_inventory_only` | Existing v1 folder and tests | Same v1 behavior; v2 inspector refuses to advertise a complete restore |
| PB02 `complete_object_graph` | Two recordings share original bytes; raw/source/evidence files differ | One stored object per hash, separate correct file mappings, exact expanded byte count, database object included |
| PB03 `fingerprints_preserve_exact_values` | Unicode combining marks, emoji, numeric TEXT, blob/null, notes/history/Trash/topic reviews | Compaction keeps all typed cells and immutable payload/hash relationships; no JSON reserialization |
| PB04 `mtime_is_not_proof` | Change original bytes while retaining size/mtime | New digest or changed-source refusal; cached v1 hash is never accepted as byte proof |
| PB05 `required_missing_vs_legacy_gap` | Missing original; missing promised stage; old producer never captured evidence | First two fail complete capture; last is an explicit bounded gap without invented artifacts |
| PB06 `no_credentials_or_unowned_walk` | Canary keys/session URLs/config files plus unrelated sibling directory | No credential/unowned file enters inventory; literal transcript text remains unchanged |
| PB07 `gate_and_publication_failure` | Barrier races edit/delete/job enqueue against capture; injected copy/fsync/commit/UI failure | Capture is coherent or refused; no partial commit, originals unchanged, committed package survives UI failure |
| PV01 `strict_bounds_and_json` | Boundary/+1 sizes/counts, duplicate/unknown keys, NaN, bool-as-size, wrong timestamps/hash/IDs | Deterministic rejection before materialization or unbounded allocation |
| PV02 `object_graph_tampering` | Missing/extra object, wrong bytes/size, changed manifest/commit, bad trusted receipt | Refusal with no target database or registration |
| PR01 `platform_path_matrix` | Traversal, UNC/drive/ADS, device names, case/Unicode collisions, nested links/junctions, replacement race | No read/write escapes owned roots; no truncation or overwritten target |
| PR02 `untrusted_database` | Newer schema, extra trigger/view/table, corrupt payload/mirror/FK, injected SQL dump | Inspect fails read-only; no `Store` constructor, extension execution, migration or mutation |
| PR03 `new_root_exact_overlay` | Projects formerly in different absolute/legacy roots | All restored bytes contained under new root; exact research fingerprints, only listed operational differences |
| PR04 `resume_and_finalization` | Crash at each pending-object, working-copy, receipt, promotion and registration seam | Same binding resumes safely; wrong package refuses; appearing target retained; committed target never deleted |
| Q01 `all_imported_claims_refuse` | Queued/running imported media/API/local/SSH/native/runtime owners, including missing members | No claim, retry, late attach, Stop, reconcile, cleanup or source-journal rewrite; zero remote calls |
| Q02 `same_keyring_is_not_authority` | Destination already has valid source endpoint credentials | Q01 still holds; no automatic connection/approval or recovered source allocation control |
| Q03 `history_not_live_obligation` | Imported uncertain/retained-cleanup owners older than Activity limit | Read-only history survives; source receipts unchanged; copy can switch/move/delete without claiming source cleanup |
| Q04 `new_work_is_independent` | User configures a new provider and creates new job beside imported IDs | New job works with new ID/approval; its actual obligations still guard local deletion; imported IDs remain fenced |
| A01 `quarantine_cannot_be_bypassed` | Missing coverage, interrupted population, copied library opened by old app | Fail closed before worker/API use; old app refuses next schema; migration failure preserves original library |
| A02 `portable_acceptance` | Real Windows/macOS synthetic local round trips | Playback from regenerated media, exact current/history exports and path behavior; no live service evidence inferred |

## 8. Minimum vertical slices and explicit stop points

| Slice | Small reviewable outcome | Exit evidence / boundary |
| --- | --- | --- |
| S1: inspect contract | Pure strict manifest/object/schema inspection, plus generated synthetic package fixture | PV01–02 and PR02; no user-facing Restore, schema migration or live library writes |
| S2: prepare complete local package | Snapshot gate, role inventory, compacted database, copied objects and immutable commit/receipt | PB01–07; existing v1 unchanged; no restore or network |
| S3: stage restore | Safe new-root file materialization and exact working-copy path/reset plan, still unopened | PR01–04; nothing registered or passed to workers; no production schema migration yet |
| S4: ownership quarantine | One separately reviewed additive next-schema table and shared claim/action/obligation guards | Q01–04/A01 plus existing queue/native/runtime/Trash regressions; empty migration preserves ordinary libraries |
| S5: activate local copy | Populate quarantine, verify exact overlay, no-overwrite promotion and recoverable chooser registration | All earlier cases plus A02 on real Windows/macOS; expose Restore only after this slice passes |
| S6: usable local workflow | Explicit new media preparation/reconnection/new runs, historical-owner presentation and recovery UX | Browser restore/late-response/cancel/restart checks, zero imported-owner control, independent new-work proof |

Each slice gets its accepted ADR/SPEC/TODO work when implemented; this brief does not mutate
them. Do not combine filesystem restoration, all queue semantics and remote upload into one
migration/PR. Passing S1–S6 establishes a local portable restore only. T91's UCloud transfer,
retention/Trash and institutional decisions remain separate pending work; no deployment or
approval is implied by a local contract test.
