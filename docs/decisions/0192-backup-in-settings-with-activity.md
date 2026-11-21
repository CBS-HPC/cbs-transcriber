# 0192 — Backup in Settings with Activity

Accepted on 21 November 2026 for post-MVP-v2 work, implemented on `claude/backup-activity`
from Codex's backup-Activity proposal and night seam note of 20 November (private handoff).
It changes job semantics for the existing complete local capture (ADR 0096), so it extends
ADR 0096, ADR 0158 (server-lifetime background tasks) and ADR 0190 (reciprocal admission
with project deletion) without replacing their guarantees. UCloud backup transfer, restore
and retention (ADRs 0096, 0109, 0134, 0139) are unchanged.

## Context

Library → Backups made a complete backup inside one synchronous HTTP request. Closing the
dialog discarded the reply while the server kept copying; a lost reply could leave a
`.portable-<id>.partial` folder with no explanation; there was no progress or Stop. The
TODO item "Backup Settings and Activity" and the weekend Settings design asked for a
Settings → Backup entry reusing the verified flow, with progress and failures in Activity.

## Decision

### One server-owned task

`backup_tasks.BackupTasks` is installed by the app factory and tied to the serving Store and
its lease, like the export and project-deletion owners. One backup runs at a time per
library. It reuses `portable_capture.capture_library` (complete v2 capture, ADR 0096), never
the older database-plus-manifest `backup.py` path.

- **Admission** (`POST /api/library/backup-tasks`, 202, strict body `{operation_id,
  destination}`) refuses early with the existing capture messages (queued/running jobs,
  active workers, runtime preparation, local file recovery), freezes the resolved
  destination and a host-generated capture id, then — before replying — enters the dedicated
  capture guard (project-deletion reservation plus HF/export inhibitors, B513) and takes a
  **nonblocking capture-gate reservation**. Later HTTP mutations wait from that moment;
  earlier ones still drain before the capture starts. A durable note is written before 202.
  An exact replay returns the same task (also after a restart); a changed body is refused.
  A second backup, or the older synchronous route while a task runs, is refused with 409.
- **Worker**: the capture waits for earlier changes (phase *waiting*), then runs *snapshot*,
  *inventory*, *copy*, *verify* and *publishing*. The worker borrows the serving lease; a
  no-worker test app takes its own. All admission (gate, deletion, HF/export) is released
  exactly once when the capture exits, before the destination is inspected or cleaned.
- **Editing during a backup** is unchanged from ADR 0096: reads continue; edits, enqueue,
  switching and other mutations wait until the files are copied. Relaxing that would need a
  separate immutable-source design.

### Capture hooks (absent hooks keep the earlier behavior)

`capture_library` accepts an optional `Observer`, a host `identifier`, a gate `Reservation`
and a `cancelled` predicate. The observer receives phase changes, the staging folder once
created (the task records its `(st_dev, st_ino)` identity at once), logical totals after
inventory and per-chunk/per-file counts. Totals count every file actually read and hashed,
including deduplicated duplicates, so progress never reaches 100% early; before inventory
it is unknown. A SQLite progress handler interrupts only the private snapshot's compaction.
The contained database check already kills and drains its child before a stop propagates.

### Stop and the publication boundary

Stop (`POST …/{id}/stop`) is a signal: it sets the task's flag and wakes the gate.
Its exact path is exempt from the capture gate and the local-recovery gate; host, origin and
request-header checks still apply. Stop wins until the **publication boundary**, latched
under the task lock immediately before the independent creator reference
(`portable-captures/<id>.json`) is written. The latch is written to the note first; if that
write fails, publication is refused. After the latch Stop returns 409 `backup_publishing`
and the backup finishes or reports an uncertain publication; it is never erased.

### Outcomes

- **Ready (verified)** only when capture returned and the intended final folder matches its
  creator reference: commit marker and manifest digests, object names and sizes, the pending
  folder gone. The full transport and contained database checks ran on the sealed bytes
  before the renames; this is the existing "checked when made, not a test restore" meaning.
- **Stopped** / **failed** before the boundary: the worker removes only its own staging
  folder, and only when its identity matches, it is a real folder (no link or reparse point)
  and it holds nothing but the expected staging names. Otherwise the card names the exact
  folder as *incomplete, not a backup* and offers **Remove incomplete copy** (same checks).
- **Needs attention** after the boundary: files are kept. **Check again** reads the creator
  reference; if the pending folder still holds remaining parts it moves only those, without
  replacing anything, into the reserved target, then runs the full receipt verification. A
  missing reference or mismatch becomes *failed* with the kept folders named.
- **Interrupted**: the app closed before the boundary. Graceful shutdown signals the task,
  cleans up and joins it under the lease. Failure messages are curated; ENOSPC and
  permission errors get plain wording, unknown exceptions never expose text or paths.

### Notes across a restart

Each task writes `backup-tasks/<task>.json` (format `cbs-backup-task-v1`, at most 32 KiB):
backup id, request and operation digests, destination folder, state, phase, revision,
publication latch, staging identity, kept paths, counts, message and (when ready) the
receipt. No token, transcript text or recording name. Startup (after file recovery, before
workers) reads at most 64 notes and settles running ones from fresh observations only:
before the boundary → *interrupted*, naming the staging folder if it still exists; after
it → *needs attention* (no automatic verification, keeping startup bounded). Nothing
recaptures, resumes or deletes because of a note. Malformed notes become inert
"could not be read" cards. Captures copy neither this folder nor any other task note, so a
restored library carries no task state; library deletion lists `backup-tasks` as an app
entry. At most 32 notes are kept: older finished notes without obligations are pruned.

### Activity card and Settings

Kind `backup`, id `backup:<task>`, `backup_task` summary with `can_stop`, `can_dismiss`,
`can_remove_partial`, `can_check`, kept paths and counts. Stages are listed while running;
the progress bar shows copied bytes only during *copy*. Ready shows **Save receipt** (the
same JSON as Library → Backups). Dismiss is refused while an owned folder remains or a
publication is unresolved; Clear finished keeps such cards in the attention group.

Settings gains a **Backup** tab: what is included, the current library, the last saved
receipt, the backup folder (same default and same-device warning), **Make backup**,
*View Activity* and *Library backups* (receipts; restore and UCloud where the profile offers
them). Library → Backups → Create backup hands off to the same task and, while that dialog
stays open, loads the finished receipt into its existing upload/restore fields.

### Release profile

No feature key: local backups were already offered in MVP. UCloud transfer stays behind
`cloud_backup` and restore behind `restore_ui`, both unchanged and never run live. No
automatic schedules exist.

## Deviations from the proposal

- **Persistence.** A file note folder (as ADR 0190), not a new table: no schema, migration,
  catalog pin or portable restore handling, and copies are inert by construction.
- **Ready check.** Ready re-reads the published marker/manifest and object sizes rather than
  re-hashing every object a second time; the full hash checks ran on the same bytes before
  the renames. Check again and Library → Backups still run the full verification.
- **Startup.** Interrupted publications are not verified automatically at startup (bounded
  startup); the card asks for Check again.
- **Admission order.** The early refusal checks run before the deletion fence; the
  authoritative checks are repeated under the queue/snapshot locks.
- **Control routes.** Dismiss, Remove incomplete copy and Check again also bypass the
  capture gate (they touch only the note and the task's own destination folders); only Stop
  bypasses the local-recovery gate.

## Consequences

- Closing Settings, Library or the tab no longer abandons a backup; a lost reply leaves a
  task card, never an unexplained partial folder.
- HTTP mutations still wait during a backup, including the project-deletion preview; direct
  deletion admission is refused while a backup is reserved (B513 unchanged).
- Real large libraries, slow or removable disks, OneDrive/cloud-synced destinations,
  process crashes on Windows/macOS and screen readers are not verified by synthetic tests;
  see human checklist H21.
