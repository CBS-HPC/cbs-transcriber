# Main merge and schema15 activation — 30 October 2026

The user explicitly approved merging the verified integration, backing up the real library,
upgrading schema13 to15, restarting, and pushing to the existing private GitHub repository.
The running `main` checkout fast-forwarded from `cbaa573` to `b578212`; the original development
checkout's unrelated changes and all other worktrees were preserved.

## Backup and idle transition

The app reported no active activities, and the complete SQLite jobs query confirmed zero
queued/running jobs immediately before stopping the verified backend process. A fresh online
backup was followed by the authoritative offline backup under the library's
`backups/library-20261030T103017417140Z`:

- schema13; integrity and foreign-key checks passed;
- 4 projects, 39 recordings and all 39 originals found;
- original media inventory: 642,479,542 bytes, with no missing originals;
- SQLite snapshot: 97,677,312 bytes.

This backup contains SQLite and a hashed-originals manifest, not duplicate media bodies.
The old library copy remains intact. No old backup, recording or UCloud folder was removed.
Private activation scripts, baseline hashes and receipts remain in ignored `test-results/`.

## Preservation

The migration added `recording_result_roles`, `result_role_events` and `restored_owners`.
Exact typed fingerprints of every old column/row across all 19 preexisting tables matched
the authoritative offline snapshot, both after migration and after application startup.
Integrity/FK checks passed. Every original was freshly hashed after migration and startup:
all 39 SHA-256 values and byte sizes matched the manifest.

These checks preserve reviewed transcripts, versions, notes, jobs, provider settings and
existing source evidence. No transcript is automatically designated Working.

## Running app

The app serves the merged checkout's source using the existing Python environment and the
same library/default project-parent locations. No environment synchronization was required.
A background-launch command was rejected by command review; the authorized app was instead
started directly in a managed command session, without changing the launch scope.

`http://127.0.0.1:8765/api/health` passes. Served JavaScript bytes match main and have
`Cache-Control: no-store`. The app browser panel was requested. A read-only-impact deletion
preview for test6 returns zero active jobs, shared recordings, remote folders and legacy
blockers; the project was not deleted. No transcription, private upload or new UCloud job
was started as an acceptance test.

Feature verification remains [253 distinct targeted unit/API passes, one skip and 14 browser
passes](verification-2026-10-30-delete-release.md), plus Claude's separate integration evidence.
Real-provider, Mac, NVivo, backup transfer and institutional acceptance remain open.
