# 0179 — Existing sole-result Working designation on schema 19

Design accepted 12 November 2026 after independent review for the user's additional backend
work. **Implemented 17 November**: builder `1b4d566` (57 cases) and independent tester Go
`f484571` (114 adversarial cases, 171 combined). Not loaded; real-library activation follows
the authorized activation process. This extends [ADR 0141](0141-existing-sole-working.md),
whose exact schema-18 activation hold was intentional, not an unnoticed stale constant.

## Decision

Support only reviewed runtime/database pairs 18/18 and 19/19; unknown or mismatched schemas
still refuse. Schema 19 adds the independent `passage_reviews` table/index. Marks are neither
Working consent nor a veto; preserve their exact rows, revisions, timestamps and stale entries.
Use genuine schema-19 libraries in tests rather than relabelling schema-18 fixtures.

All ADR 0141 eligibility remains: one active/live sole result counting Trash alternatives,
one intact creation version with no parents, unedited original wording/timing/words, exact
mirrors and hashes, no prior role/event or unsettled authority, no restored/preservation
ambiguity and verified owned snapshot paths. Keep payload/recording revisions, versions,
jobs, existing choices and APIs unchanged. Do not add automatic job-finish re-evaluation.

## Native recovery dependency

The Working startup pass precedes T141 native queue admission. A failed native job without
Working authority may now resume afterward. Add a **negative-only** bounded fence inside
the same writer transaction; never invoke native admission or reorder workers.

Block failed native transcription and failed rows whose malformed/missing settings cannot
prove nonnative identity. Check job-to-batch and owner-manifest-to-job links, including
completed local members of failed/unsettled owners and claimed recording IDs with missing
jobs. Missing/conflicting ownership never means settled. Opaque, malformed, oversized or
incomplete owner inventories refuse the remaining pass, rather than guessing candidates safe.
Bound-plus-one exhaustion is not a complete scan.

Reuse blocked metadata only while `PRAGMA data_version` is unchanged between page writers;
foreign commits require a fresh bounded scan. Keep existing role/mirror/source/Trash checks
under BEGIN IMMEDIATE, without per-candidate full job rescans. Preserve savepoints, existing
soft budgets, post-commit snapshot recovery and idempotency. If opaque evidence appears after
an earlier page committed, stop remaining pages and report that partial progress; earlier
commits cannot be described as globally rolled back.

## Required proof

Retain genuine schema-18 eligibility/corruption/path/budget regressions. Add real schema-19
positive/negative cases with every review status and stale marks; failed singleton and both
directions of batch membership; missing/conflicting/opaque owner evidence; late foreign
commit/cache invalidation; late role/edit/Trash changes; and post-commit snapshot lost replies.

Prove real contained capture/restore with populated reviews and the existing
`working_auto_first` system event, exact creation reference and no invented user consent.
Restored/archived owners stay inert. Existing portable roles/review validators appear to
support this without schema/source-pin changes, but tests must establish it.

Builder ownership: `working_existing.py` and scoped legacy fixture/acceptance corrections.
Independent tester: `test_working_existing_schema19.py`. Native startup, Store, role writers,
startup ordering, portable sources and migrations remain untouched unless a new demonstrated
need is independently reviewed. No real-library activation follows from this design.
