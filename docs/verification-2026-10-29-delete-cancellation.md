# Automatic job cancellation on Delete — 29 October 2026

Development is isolated from the serving `main` checkout and real library. No live recording
was deleted, no inference was requested and no remote allocation was started by these tests.
The separately approved disposal of cancelled Runtime job 12435533 used the released main
API and is not evidence for this new implementation.

## Behavior

Recording/project Delete captures exact targets and cancels their jobs. It waits for worker
file ownership, reserved inspection/admission drains and shared allocation controls before
removing managed local files. Shared allocations affecting other recordings require a
concrete confirmation. Unresolved remote output, cleanup and reconciliation keep local data.
Follow-up DELETEs carry short-lived finish-only tokens; edits, new work and explicit Retry
invalidate earlier deletion consent. Stop/Retry controls reserve the same job/allocation
without holding the queue lock during remote I/O.

The UI retains cards/dialogs on HTTP202, disables duplicate Delete and displays progress.
File/project errors stay inside their dialogs. Closing/navigating stops further continuations;
late responses do not navigate away from a newer recording.

## Evidence

- Initial queued-job delete failed against the old active-job refusal, then passed.
- New cancellation lock/journal probes failed against the old lock-held remote control and
  stale journal replacement. The fixed tests verify lock availability and preservation of a
  concurrently written capacity receipt.
- Backend final scoped run: **30 passed, 1 skipped** before the final control-slot additions,
  including reserved controls, native/runtime shared impact, membership changes, Stop errors,
  unresolved remote evidence, worker drain, Retry invalidation, commit failure and size bounds.
  The complete backend stage then passed **33 tests, 1 skipped**. A separate five-case control suite verifies blocked Stop versus Retry/duplicate Stop,
  unrelated-job independence, shared-member exclusion and slot release. Totals overlap.
- Browser coverage: **five distinct cases passed**, combining the corrected two-case rerun,
  two initial passes and the final project deletion test. Cases cover pending worker drain,
  declined shared Stop, inline error visibility, a held reply after navigation and typed-name
  project deletion to the home view. Initial failures were ambiguous card/notes selectors and
  the expected empty-hash URL spelling; assertions were corrected without weakening behavior.
- The existing repair Stop/publication interleaving regression passes. In particular a
  terminal observed remote state does not bypass the protocol's fenced Stop handling.
- Python Ruff/format, JavaScript parse and Prettier 3.6.2 checks are performed on changed files.

Test receipts are local ignored `test-results/delete-*.xml` and `cancel-interleaving.xml`.
All libraries and transport failures are synthetic; no paid API call, trained-model download
or private recording is needed. The skipped test requires Windows permission to create a
symbolic link. Other ownership and bounded-path checks execute on this machine.

## Limits

This adds no durable filesystem transaction: a disk/SQLite commit failure during irreversible
removal can retain a database row after some managed files have been removed, as before.
Completion is never cached until commit succeeds. It does not introduce remote disposal
consent, restore a deleted recording or change transcript-result Trash semantics. Integration
with schema14 Working roles and schema15 restored-owner quarantine requires explicit checks
before the combined release is activated.

The combined role/delete/local-producer/legacy-retention run passed **50 tests, 1 skipped**.
It includes a new proof that marking a Working version invalidates a pending Delete without
changing the transcript revision or deleting the recording. Schema15 integration is pending.
