# 0141 — Designate an untouched sole existing result as Working

Accepted 5 November 2026 on the isolated `claude/working-existing` branch; not active in the
serving app until independently reviewed and merged. Narrowly extends ADR 0133, whose
automatic first designation applies only when a result is created: a recording whose only
result predates it, or came from a legacy unmarked job, may now become Working once. No
schema, catalog or pinned portable source changes.

## Eligibility

`working_existing.designate_existing` decides each recording inside one SQLite
`BEGIN IMMEDIATE` transaction. All of the following must hold, otherwise it is skipped:

- the library is not restored: no `preservation` entry of any kind (an unreadable or linked
  one also refuses) and no `restored_owners` row; schema 18 exactly;
- an active result and no `recording_result_roles` row or role event at all, so any earlier
  designation, explicit clear, Keep or Trash clearing excludes the recording permanently;
- exactly one result, including Trash, and it is the active, live result;
- exactly one version: sequence 1, no predecessor or source version, action
  `provider_result` or `imported_result` (legacy observations, manual first saves and
  derived results are not proven);
- no job for the recording outside `completed`/`failed`/`cancelled` (unknown states count as
  pending), and no ADR 0133 Working authority whose job is not completed (this covers stopped
  intents awaiting Prepare and retryable authorized jobs);
- every passage is untouched: `edited` is false and text, words and word timing equal their
  originals (a creation can already carry edited passages, for example an imported export);
- the saved result and the recording projection, bounded to the transcript limit, encode to
  the exact creation payload, and the shared writer verifies that payload's observation,
  size and hash.

Damaged or unprovable evidence skips only that recording, under a savepoint; it is never
treated as absence. Imports without a job are eligible in ordinary libraries because ADR 0133
designates new imports too; in restored libraries nothing is designated.

## Write, files and recovery

The write is `result_roles.move_automatic` with frozen role `{revision 0, unassigned}` and
`working_auto_first`: the same writer, tail validation, sequence-1 rule and system actor as an
ADR 0133 first result. History, reports, exports, portable checks and Trash therefore treat
both identically. The event table has no reason field; a pass designation is distinguishable
only by its later observation time than the creation version. Versions, projections,
revisions, jobs and authorities are never written.

Project files follow the ordinary Working path, `Store.snapshot_project`, after the database
commit, but only for a proven project folder: absolute and normalized, named after the
project ID, not shared with another project, an existing directory with no link or non-cloud
reparse point in it or its parents, and every existing output target (`project.json`,
`project.pending`, `exports`, per-recording folders and per-result `.txt`/`.pending`) of the
expected regular kind. Moves hold the same snapshot lock. Legacy projects without a storage
folder need no snapshot and receive no warning.

No new persistence marker exists. After B374 (9 November), file timestamps are not publication
evidence: a copied or touched file can still contain stale Working roles. Each start compares
the actual `result_roles` for the snapshot's recordings against the same SQLite read transaction.
A missing snapshot is stale. An existing snapshot must be a bounded (at most 32 MiB), valid
format-1 envelope for this project, without duplicate keys or recording identifiers; a foreign,
malformed or oversized file is retained with the visible unverified-snapshot warning. A valid
but stale snapshot uses the ordinary writer after the existing owned-folder checks. Up-to-date
projects are not rewritten. Refused, failed or budget-deferred projects are retried at the next
start, and an explicit save uses the ordinary snapshot writer as before.

## Bounds and concurrency

The startup call runs after deletion-file recovery and profile backfill, inside the library
lease and before workers. Designation has a 10 s budget and snapshots another 10 s. The pass
uses a 1 s busy timeout instead of the ordinary 15 s, and a progress handler interrupts every
SQLite statement at the deadline, including the job scan; an interrupted page rolls back and
earlier pages stay committed. Candidates are keyset-paged by recording ID through indexed role
and result lookups (128 per page, at most 8 MiB proved per transaction). The jobs table has no
recording index and adding one would change the pinned catalog, so pending work is collected
by one scan per pass, repeated only when another connection committed between pages
(`PRAGMA data_version` at the start of each writer transaction). Remaining candidates continue
at the next start; designated ones leave the candidate query.

Every fence is rechecked in the writer transaction, so a user choice, edit, Trash or new job
that commits first wins, and one that started from the unassigned role is refused afterwards
by the existing role-revision fences. Re-evaluating a recording when its last job finishes
was not adopted: it would need its own bounded scans and snapshot handling in the worker
path, and the next start covers it.

## Synthetic verification, 5 November 2026

A synthetic library of 5,001 eligible recordings in 50 projects (about 50 KB of payload
each), 50,000 completed jobs with about 1.9 KB parameters and an 893 MiB database was
measured on the development PC while other processes were running. One job scan took
0.5–0.8 s. A default startup designated 431 recordings and refreshed 4 project snapshots in
21.9 s, leaving the rest incomplete and 46 projects visibly pending. The remaining 4,570
designations took 95.9 s unbounded (about 21 ms each, one job scan). Writing the remaining
snapshots for the first time took 4,665 s in that run; a later forced refresh of all 50
took 137 s (about 2.7 s per 100-recording project) and a budgeted one 11.6 s. A following
start with nothing left took 0.7 s. JSON proof and ordinary snapshot writing dominate.

The focused synthetic suite (22 cases) covers eligibility and every skip reason including
the edited-creation regression from independent review, restored imports and unreadable
evidence, concurrent user choices and edits, foreign commits between pages, an interleaved
writer, page-atomic interruption, aborts on a held lock or a long statement, snapshot refresh,
recovery after a post-commit crash, unowned, missing, linked and non-regular targets, failed
writes, budget deferral and a 401-recording/4,000-job history scanned once.

## Limits

Off-app copies whose restore
evidence was removed cannot be distinguished from ordinary libraries. Filesystem checks cannot
exclude a non-cooperating process changing folders between check and write. Snapshots above the
bounded inspection limit need an explicit save and remain visibly unverified. A single
candidate's JSON proof, bounded snapshot check and a single project's snapshot write are not
interruptible, so a budget may be exceeded by one of each. A job table too large to scan
within the budget leaves the pass inert. Real-library activation still needs a backup,
independent review and user acceptance.
