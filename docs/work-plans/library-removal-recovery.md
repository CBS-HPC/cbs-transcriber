# Permanent library deletion — implementation plan

Status: **implemented on `claude/library-delete` as ADR 0144; pending independent review and
real-machine acceptance.** ADR 0144 records where the implementation deliberately differs
from this plan: the preview takes no lease and writes nothing (it reads a private database
copy and probes `server.lock` without creating it); the lease is taken at execution; shared,
unverified or missing external project folders are kept and listed rather than blocking; and
entries the app never creates inside the library folder are listed and deleted with it.
Reversible list hiding is a separate operation (ADR 0128).
The user requested both choices; a list hide must never be described as a permanent delete.

## Scope and current evidence

Permanent deletion must include the library's raw recordings, including originals in verified
exclusively owned project storage outside the library root. The experimental work on the
`codex/library-removal` branch is not connected to the app: its reservation, staging-authority
and Windows lock-disposition hooks refuse real use. Its synthetic staging and recovery tests
are not proof of a complete deletion protocol. It stages only direct library-root children and
cannot remove external originals or finish removal of the root and its lock file.

The smallest user-facing slice that actually meets the request is a deletion restricted to
libraries for which every requested original and other target has verified exclusive ownership,
every obligation is settled, and the entire operation has recoverable staging and finalization.
If any of those facts cannot be proved, the preview must explain the affected scope and block
deletion. An empty-library-only experiment or an inventory preview is useful development work,
but does not complete the requested feature.

Current blockers:

- **Shared or external files.** The recent/default library registry is incomplete and cannot
  prove that an unlisted library or another user does not reference a project folder. Paths,
  project IDs, `project.json`, an empty Activity panel and user confirmation are not ownership
  evidence. Preview exact recording originals, exports, snapshots and local backup copies;
  distinguish owned targets, shared or uncertain targets, and any separate backup/cloud scope.
  An external project parent must never be recursively deleted just because it is referenced.
- **Obligations.** The prototype counts jobs and some owners but does not validate their journals
  or reconcile pending, uncertain or unsaved output. Inspect native and Runtime allocations and
  preparations, legacy and repair owners, transfer/retention receipts, and local deletion or
  restore journals. Distinguish inert restored owners from operational ones; malformed or unknown
  evidence blocks disposal rather than erasing the only recovery record.
- **Windows and reopen safety.** A locked root cannot be staged by renaming the whole directory;
  the existing experiment cannot safely finalize the lock file or root. A reservation outside the
  target must survive each crash boundary and be checked before directory/database creation and
  under the lease. The experimental reservation and connection barrier live under the current
  user's home, so another OS user opening the same shared library uses a different fence. Older
  app versions and unrelated SQLite clients also bypass it. A supported implementation must
  specify and prove its exclusion boundary; these cases cannot be claimed as protected.
- **Preview effects.** The prototype's `inspect_inactive()` acquires `server.lock` via an
  `a+b` open that can create the file or write its initial byte. Its connection barrier can also
  create app-local coordination files. It is not a strictly read-only preview and must not be
  connected to an app action on that basis. Establish target identity and exclusive access without
  hidden library mutation, or explicitly design and test any necessary coordination writes.
- **Recovery across volumes.** External project files may reside on different volumes. Each
  confirmed target needs a same-volume reservation, durable plan and verified ownership, with
  rollback before commit and resumable cleanup after commit. Missing volumes, occupied restore
  paths, replaced objects or unknown markers remain unresolved; no copy-and-delete fallback.

## Why this needs its own operation

A library database may reference project folders outside the library directory, including
folders shared with other libraries. It can also hold queued jobs, unsaved provider results,
remote allocation and cleanup receipts, and portable transfer or local deletion journals.
Removing the directory can destroy the only record of those obligations. Existing project
deletion stages exact DB-owned paths in the *serving* library; it does not cover removal of
an inactive library and its own database.

On Windows, synthetic experiments on this computer show that the library root cannot be
renamed while its normal `server.lock` lease is held (WinError 5), including with a lock
handle opened with `FILE_SHARE_DELETE`. A database child can be renamed under the lease.
Therefore an implementation cannot treat a whole-folder rename while locked as its safety
boundary. No user library was changed by these experiments.

## Required protocol before enabling Delete permanently

1. **Preview against a held target lease.** Confirm the selected path is a listed non-current
   library; bind normalized root, directory and SQLite-file identities; open the database
   read-only without constructing or migrating a `Store`. Reject links, junctions, mounts,
   unreadable/newer or unsupported schema, a second open server and unsettled work. Inventory
   only exact app-owned root children under bounded limits; list any unowned or unverified
   paths separately and refuse deletion until they are accounted for. Query project storage
   locations and exact managed originals, including those outside the root. Verify exclusive
   ownership independently; a shared or unverified target blocks the requested deletion.
   Detect local deletion/restore/transfer journals and all native, Runtime, repair and legacy
   remote ownership; unverified, active, unsaved or pending cleanup evidence blocks deletion.
2. **Explicit confirmation.** Show the exact root and owned targets, external originals to
   delete, any blocked or retained paths, backup/cloud scope and applicable refusal reasons. Bind
   confirmation to a fresh fingerprint over database state, path/file identities, full staged target list,
   journal outcomes and external-path inventory. Recheck all facts while holding the lease;
   a changed preview needs a new confirmation. A list-hide action never grants this authority.
3. **Durable, recoverable staging.** Publish a bounded immutable operation plan and a target
   reservation outside the target library before changing it. Place a plan-bound marker in the
   target, then stage only the previously verified children by exclusive, same-volume renames
   to marked sibling wrappers. Keep the target's normal lease while moving children; do not
   follow links/reparse points or cross volumes. Never recursively remove a file while the
   database is still live. Treat SQLite, its WAL/SHM files, backups and old per-recording roots
   as explicit, individually bound entries. Unexpected new files refuse further staging.
4. **Guard the gap and reopen.** Before releasing the target lease or moving/removing its
   `server.lock`, make both the launcher and `Store` fail closed on a verified unresolved
   target reservation, including *before* they create a missing directory/database and again
   under the lease. Otherwise a crash or a competing launch can silently create a new empty
   library at the same path while its old database is staged. Old app versions cannot honour
   a new fence; scope and document that limit rather than claim cross-version exclusion.
5. **Commit and recover.** Once every exact entry is staged and the old database can no longer
   be served, durably commit the user's confirmed disposition. Before commit, recovery
   restores staged entries only into unoccupied original names and never recreates DB rows.
   After commit, recovery may settle only the verified marked staging payload. Partial/unknown
   states remain blocked and visible, with an independent retry that survives switching
   libraries or deleting the source. Delete an external original only when its exact ownership and
   confirmed scope are proved; never delete a shared or unverified project, purge UCloud Trash,
   or discard unverified remote output as an implicit consequence of library deletion.
6. **Testing.** Exercise current/other-active roots, stale or replaced identities, explicit
   consent changes, external and shared projects, links/reparse/mounts, oversized trees,
   journal/remote uncertainty, concurrent open/create/restore, process crashes at every
   publication/rename/commit/cleanup boundary, read-only ACL/share violations and resume.
   Run synthetic Windows/macOS/Linux qualification separately; power-loss and Cloud Files
   durability require their own evidence. Never test permanent deletion on a real library.

Until this protocol is implemented and independently reviewed, the app must not present an
active **Delete permanently** control that silently uses `rmtree` or forgets a failed cleanup.
