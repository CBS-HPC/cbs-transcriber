# 0073 — Exact artifact evidence for new transcript imports

Accepted 27 October 2026 for T43e. Extend ADR 0031's research observations with exact
new-import source receipts. This does not accept the working/alternative role proposal,
change restoration/merging semantics, migrate a library, or infer historical evidence.

## Capture before publication

`Store.import_transcript` validates bounded JSON/CSV bytes and parses them through the existing
normalizer. Hold the existing snapshot lock from recording/revision/path lookup through raw
storage, physical verification and result creation. HTTP retains queue/busy/replace guards;
lock order is queue → snapshot → SQLite. Direct receipt-bearing result creation takes the same
snapshot lock, so relocation cannot switch paths between verification and commit.

Save exact original bytes, including BOM, whitespace and line endings, under a fresh opaque
source ID and known `.json`/`.csv` extension in the recording's managed `sources` directory.
Never use the supplied filename as a path. Reject managed recording/source/file symlinks,
Windows junctions, escaping resolved paths and non-regular files. Do not reject legitimate
chosen home ancestors. Exclusive creation refuses collisions instead of overwriting.
Flush/fsync/close before publishing a result. Verify physical bytes/size against the original
upload hash/length, with bounded reads and drift checks.
Compare device/inode/size/mtime across pathname/descriptor observations; compare ctime only
between observations from the same stat API. Windows may expose different ctime meanings
through `lstat` and `fstat`. Exact byte count, SHA-256 and parser proof remain required.

File or SQL failures publish no new result/version/projection receipt. Retain unreferenced
owned files after failed publication; never delete originals or imply a database rollback
also undid durable filesystem writes. Stale revisions still fail atomically. Do not claim
whole-device power-loss guarantees from these application-level checks.

## One explicit receipt, one transaction

`add_result(..., import_artifact_evidence=receipt)` is the explicit internal capture boundary.
A source dictionary cannot self-assert capture. Require an import source with no producing
job; verify its owned file, then reparse the verified bytes in the declared format. After
stripping review hints, its exact normalized payload with empty speaker names must match the
proposed imported result. A hash alone cannot prove a different transcript came from that file.

The pure `history.validate_import_artifact_evidence` accepts exactly:

- `schema`: literal integer 1;
- `role`: `transcript_import`;
- `source_id`: 32 lowercase hexadecimal characters;
- `sha256`: 64 lowercase hexadecimal characters;
- `bytes`: integer 1–32 MiB, excluding booleans;
- `format`: `json` or `csv`.

Copy the validated receipt into both result source and origin version's `artifact_evidence`
inside the result/version transaction. Existing context inheritance preserves it through
manual edits, same-result restoration, Trash and safe enhancement merges. A derived result
retains its own origin; its source-version lineage carries upstream import facts separately.
Keep old absent/`not_captured` observations unchanged. No source-folder scan, association by
filename, backfill, schema addition, runtime identity or original ASR-model inference.

## Historical reports

Within the existing bounded SQLite read snapshot, validate context evidence against the same
result's preserved source evidence and import kind. Read each result source once, preflight
its SQL byte length and use a separate aggregate 32 MiB source budget; preserve the existing
payload budget. Cache only the needed kind/receipt, never project arbitrary private source.
Malformed, one-sided or contradictory claims refuse report generation. Both sides absent or
exactly `not_captured` stay unknown; never promote source-only metadata into older history.

The report exposes a recording-scoped source alias, role, hash, byte count, format and
`availability: not_checked`. Repeated references to one source must agree. Identical bytes
under different source IDs remain distinct imports. Omit raw source IDs, filenames, paths,
transcript bodies and credentials. Report generation reads no retained files and does not
claim present availability, backup completeness, raw HTTP capture, processing environment,
authorship or institutional/journal compliance. Keep media/API/environment gaps explicit.

Verify new capture, physical/SQL failures, collision/link/relocation safety, exact parser
correspondence, inheritance, historical snapshot consistency, privacy and legacy unknowns
with synthetic libraries and mock failure injection. No live library, private transcript,
paid service, inference or UCloud operation is needed.
