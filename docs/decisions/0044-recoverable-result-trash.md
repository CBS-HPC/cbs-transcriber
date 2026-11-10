# 0044 — Recoverable saved-result Trash

Accepted 22 October 2026. Extends 0007 and 0031.

Researchers need to remove individual model transcriptions or enhancement steps without deleting
the recording. The user chose recoverable Trash. Physical result deletion would sever exact
enhancement lineage, topic evidence and completed-job receipts, so visibility and retention must
be separate.

Schema 8 adds `result_trash(result_id, trashed_at)`, with a non-null primary key and a cascading
foreign key to the retained result. A schema-7 SQLite backup precedes the transactional migration.
Existing rows and payloads remain unchanged. Membership changes require the owning recording's
current integer revision, append `result_trashed` / `result_restored` history observations and
update the active projection atomically. Identical desired membership is a no-op at the current
revision. Lifecycle observations retain exact content/context but do not count as manual edits.

Removing the active result selects the newest remaining live result, ordered by creation and ID;
removing the last clears the active ID, passages and speakers. Restore autoactivates only when
there is no current active result. Each actual change advances the recording revision once.
Notes use their independent revisions and remain untouched.
Recording reads capture the projection, live choices and active result in one SQLite snapshot;
concurrent lifecycle changes from another connection cannot turn that view into a false 404.
Project-file copies likewise capture their project, recordings and full live result rows together
before filesystem writes, so a later Trash action cannot invalidate a producer's committed receipt.

Live lists/counts, selections, comparison, exports and new enhancement/topic inputs exclude Trash.
Project ZIP filtering belongs in the result join so recordings without live results still receive
an explicit skipped entry. Historical reads, frozen source validation and existing coding runs
retain access to preserved evidence. A report can traverse a trashed ancestor but cannot select
it as a new root; the manifest marks retained ancestor visibility. Completed-job idempotency
returns its original receipt without recreating or restoring a result. Native cleanup journals
and remote ownership semantics are unchanged.

The review dialog offers Move to Trash and Restore, preserves notes/media state, protects unsaved
passage drafts and guards delayed replies against navigation. Errors remain in the dialog.
Trash is not erasure: original recordings, raw responses, history, jobs and old project-file
copies remain. Permanent purge, institutional retention policy and version-as-new restoration
are separate work.
