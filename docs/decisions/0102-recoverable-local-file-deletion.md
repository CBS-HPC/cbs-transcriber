# 0102 — Recoverable local file deletion

Accepted 30 October 2026 after a confirmed Delete partly removed managed files before a
Windows read-only directory prevented final removal. Extends ADR0092/0101 and V166.

Local deletion now has a small filesystem journal, not a new database schema. Under the
serving Store lease and queue → snapshot → SQLite writer boundary, freeze the exact project,
recording and storage identities and all managed recording/export paths plus project.json.
Durably publish a bounded immutable plan before moving anything. Each existing target moves
by exclusive same-volume rename into a unique sibling wrapper with a plan-bound marker and
payload. Never recursively unlink a recording while its database row is still live; never
copy across volumes, replace a destination, or widen disposal to the whole project folder.

Only after all staging succeeds are the original SQLite rows deleted atomically. Release
queue/snapshot/writer locks before recursive cleanup. A local action reservation is acquired
before those locks and refuses competing actions rather than blocking claims during cleanup.
The engine uses nonblocking per-operation ownership, without holding its registry lock over
I/O or snapshot acquisition. Async admission checks use a cached flag and never wait on the
claim lock. Test/no-worker apps may acquire a transient lease; worker threads borrow the
already-held serving lease without re-entering its lifespan RLock.

Recovery is determined by fresh database facts, never a manifest phase:

- Exact live target ownership: restore staged files only; restore no old database metadata.
- All deletion targets absent, including an empty project's row: clean the staged payloads
  only; never recreate rows or restore originals for a committed deletion.
- Mixed/changed ownership, unavailable database, occupied restore destination, wrong marker,
  or unexplained missing payload: retain evidence and require recovery.

A COMMIT exception is an uncertain outcome: close that transaction before checking its
actual state. Never blindly roll files back. Cleanup receipts explain absent payloads on
retry but do not establish commit. A final completion receipt permits interrupted journal
metadata removal only, with no authority over any data path. Plans bind the exact library
root; copied/restored journals cannot operate on foreign storage.

Startup settles journals under the lease before profile backfill or worker admission.
Unresolved live-row recovery blocks mutations and worker startup while preserving a readable
Activity card and exact-scope Retry. A successful recovery releases admission. Committed
cleanup failures permit ordinary work but keep an independent card after project/job rows
are gone; Clear/Remove cannot hide it. Capture, backup, relocation and library switching
refuse pending local-file obligations. Operational sidecars/wrappers are excluded from
portable media inventories and never acquire control authority through restore.

Every cleanup boundary revalidates exact marker, payload identity and regular-tree ownership.
Known non-name-surrogate Cloud Files placeholders are accepted; unknown reparses, links,
junctions and mounts are refused. Windows read-only attributes may be cleared only on owned
targets; real sharing/ACL failures remain pending. macOS/Linux use native exclusive rename
and fail closed without it. Real Mac/Linux acceptance remains distinct from mocked checks.

This protocol provides tested process-crash recovery. Power-loss behaviour, storage-provider
durability and OneDrive synchronisation are not claimed as filesystem transactions.
