# 0031 — Immutable research versions beside current results

Accepted: 21 October 2026. Implements the first delivery in research-provenance-design.md.
This branch is tested only with synthetic libraries; do not migrate the running main library.

Schema 6 adds result_versions and note_versions beside existing tables. Preserve result IDs,
recording/notes revisions, saved settings and current projections. Back up schema 5 through
SQLite's backup API before a single migration transaction. Fail without partial changes if
backup, projection-consistency checking or baseline insertion fails. Refuse newer schemas.
Existing states become legacy_observed baselines; earlier history remains unknown.

Every accepted transcript/speaker/notes save appends its exact immutable state in the same
transaction as the existing optimistic projection update. A stale or failed save changes neither.
No-change saves are explicit observations. Selection, playback, search and report reads append
nothing. Explicit object deletion cascades to its retained history under existing deletion guards.

Use the cbs-result-snapshot-v1 envelope with UTF-8 compact sorted-key JSON, ensure_ascii=False
and allow_nan=False, without Unicode/text/list/number normalization. SHA-256 identifies those
exact payload bytes. Actor categories describe local user (identity unverified), worker or
migration; do not read an OS identity. Optional reasons are bounded local metadata.

freeze_result reads the exact current version/hash in the same snapshot as the reviewed text.
New enhancements and atomic batch children retain that source version even after later edits.
Result creation/version/child insertion remain one idempotent transaction. Older queued snapshots
retain their own hash and an explicit unknown version; never attach today's head retroactively.
Cross-recording source references are rejected. Preserve source lineage across review successors.

History APIs are scoped and paginated; metadata lists exclude historical text. An exact version
read/export uses its preserved state. A metadata-first project ZIP reads selected versions and
lineage in one SQLite snapshot, then renders outside the transaction. Explicit selected-text
opt-in includes current-view text only, never discarded originals. Use report aliases and a field
allowlist; exclude private names, notes/reasons, paths, endpoints, account/job IDs and credentials.
Report unknown provenance honestly; no automatic inference, universal journal compliance or
tamper-proof/authorship claim. Full artifact/environment capture and restoration UI remain later
slices. Integrity hashes alone do not anonymize research material.

Verify migration preservation/rollback/idempotency, edit/notes atomicity, frozen lineage,
scope/pagination, coherent reports, default redaction and exact historical rendering before merge.
