# 0144 — Permanent deletion of an inactive library

Proposed 5 November 2026. Extends ADR 0128 (list visibility stays a separate, reversible
action) and reuses the staging patterns of ADR 0102. Numbers 0141–0143 are used on unmerged
branches.

## Decision

The Library dialog offers **Delete permanently…** for a listed or discovered library that is
neither the open library nor open in another server. It removes the whole library folder,
including its database and original recordings, and project folders outside it that this
library exclusively owns.

**Preview (read-only).** The preview validates the normalized root (no link, junction or
non-cloud reparse point on it or its parents; not a drive or mount point), detects another
server with a non-creating lock probe of an existing `server.lock`, and walks the folder
without following links. It never opens the target database in place: even `mode=ro` can
create `-wal/-shm` or write WAL read marks. It copies `library.sqlite3` (and a non-empty WAL
or hot journal) to a private temporary folder with unchanged before/after signatures, then
reads the copy, so the target stays byte-for-byte unchanged (`immutable=1` was rejected
because it ignores WAL-only rows). The preview lists the root and its size, project and
recording counts, external project folders to delete and to keep with reasons, entries the
app never creates (deleted with the folder), and blockers. A fingerprint binds all of it plus
root/child/database identities and the set of other known libraries.
It also binds a deterministic nested inventory of relative names, entry kinds, device/file
identities, sizes and platform-defined mtime/ctime for every confirmed tree. A same-size
file replacement, rename or ordinary rewrite invalidates the earlier confirmation. A known
peer project folder overlapping the target in either direction blocks deletion, including
when the target library is nested within that project's storage folder.

**Blockers** refuse the whole deletion: queued or running jobs; UCloud single jobs, native
batches, CBS Runtime owners, runtime preparations and input preparations that the existing
helpers (`remote_jobs`, `native_batch`, `runtime_store`, `runtime_preparation`,
`input_admission`) report as unresolved, read through a Store-like seam on the private copy;
restored owners stay inert; unreadable job or owner evidence; unfinished local deletion
journals; unsettled backup transfers; restores started from this library that are unfinished;
a newer, damaged or incomplete database; links, mounts or special files inside the folder;
another library inside or around the folder, or another library's project files inside it;
the app's own settings folder inside it; another unfinished deletion.

**External project folders** are deleted only when exclusive: not inside or around any other
known library root (registry current/recent, defaults, redirected and hidden libraries, and
the open library), not overlapping a project folder any of them references (their databases
are read with `mode=ro`), not shared by two of this library's projects, not a link or mount,
containing only `recordings`, `exports`, `project.json` and `project.pending` with recording-ID
children, and with a `project.json` (if present) naming the same project ID. Shared,
unverified, unreadable-peer and missing folders are **kept** and listed; deletion of the
library may still proceed. This deliberately follows the requested design rather than the
earlier work plan's "shared or unverified blocks": keeping a folder never loses data.

**Confirmation.** The user types the library folder name. The server rechecks everything
while holding the target's normal lease and refuses a changed fingerprint.

**Execution.** Under one deletion lock (a thread lock plus an OS lock in the record folder)
and the target lease: publish `plan.json` in `<app folder>/library-deletions/<sha256 of the
normalized root>/` (the reservation) before any change; stage the database files first, then
every other root child (except `server.lock`) by exclusive same-volume renames into a marked
`<parent>/.cbs-deleting-<id>/library/`, and each exclusive external folder into
`<its parent>/.cbs-deleting-<id>-<n>/payload`; verify the root holds only `server.lock`;
publish `committed.json`. After releasing the lease (Windows cannot rename or remove a
leased root), remove the lease file and empty root, remove the registry entry, verify markers
and walk each payload for links before recursive removal, then remove the record. A staging
error rolls back in the same request.

**Fail closed on reopen.** `Store` checks the reservation (for the path and its parents)
before creating a directory or database and again after taking its lease;
`libraries.check/select` refuse it and discovery omits it.

**Recovery** runs in the launcher before any library opens and on **Retry** in the Library
dialog (also in the start-up chooser). Before commit it moves staged entries back only into
unoccupied names and removes the record; after commit it finishes removal. Occupied names,
changed identities or markers, unexpected files and unavailable parents (for example an
unplugged drive) keep the reservation and show it as needing attention.

## Limits

- The fence is honoured only by this and later app versions running as the same OS user
  (records live in that user's app folder). Older versions, other OS users and unrelated
  SQLite clients can open or recreate the folder during an unfinished deletion.
- Libraries that are not registered, discovered or open (for example on another account, an
  unregistered copy or an unfinished restore target) are not checked for shared project
  folders; `project.json` and the layout check reduce, but cannot exclude, that risk.
- Windows has no directory fsync; crash recovery covers process crashes, not power loss.
  Cloud Files/OneDrive and network-volume rename and durability behavior is not proven.
- The nested inventory is an observation of file identities and metadata, not a content hash
  or an exclusive lock on every media file. Unrelated writers that restore metadata or write
  after the last observation remain outside the app's exclusion guarantee.
- Case-insensitive volumes on macOS are compared case-sensitively.
- Backups outside the library folder, UCloud copies and UCloud Trash are never deleted.
- Real-machine Windows/macOS acceptance has not been performed; never test on a real library.
