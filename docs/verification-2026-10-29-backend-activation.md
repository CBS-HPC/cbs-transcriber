# Main merge and backend activation — 29 October 2026

The user authorized merging the verified checkpoint, a fresh backup, idle activation and
pushing main to the existing GitHub repository. The separate running checkout now uses
`main`; unrelated TODO cleanup and two document deletions in the development checkout
were preserved without staging them.

## Source and backup

The feature checkpoint `f630876`, followed by documentation-only task `6959174`, was
fast-forwarded into local main. The urgent T94 task records public-GitHub installation
guides for README/AGENTS; it does not publish the repository or change visibility.
The [release verification](verification-2026-10-29-release-gate.md) is unchanged in scope:
2,961 distinct unit/API passes, 17 skips and 175 browser passes across broad/affected runs.

Neither old loopback port had a listener, the full database job check found no queued/running
work, and the library lease could be acquired. The schema-12 implementation created a fresh
SQLite backup plus hashed-originals manifest before any migration. Integrity/FK checks passed:

- 2 projects and 18 recordings; all 18 originals found, totaling 468,238,897 bytes;
- database snapshot: 97,484,800 bytes;
- no credentials, server lease or prepared playback included in the backup.

This is a database snapshot and originals inventory, not a copy of media bodies or the
unfinished portable/cloud backup/restore feature. Originals remained in their managed folders.
The backup, exact command, operational logs and typed preservation receipts remain private
under the library's `backups/activation-20261029T095603810646Z` and companion `library-*` folder.

## Migration and preservation

The accepted schema-12 → 13 migration ran before workers started. Exact typed comparison
of every old column/row across 19 tables passed; indexes/FKs and the expected sole nullable
`recordings.original_input` addition were checked. The migration's own schema-12 backup
also matched the authoritative fresh snapshot. Integrity and FK checks passed afterward.

After app startup, every preexisting row in every table remained exact. Preserved counts:
31 transcript results, 43 result versions, 20 note versions, 92 jobs, 24 review hints,
1 native owner, 3 Runtime owners, 13 members and 13 attempts. All 13 existing providers
and all 6 settings rows were unchanged. The sole additive startup change was three catalog
profiles for Edda turbo/large and Røst v3, bringing providers to 16. No weights or inference
were requested. No jobs were added or started, and all original-inspection fields remained NULL.

## Running app

The existing Python 3.13 environment was reused without synchronization. A hidden child
launch uses the verified checkout's `src` through child-only PYTHONPATH, the same library,
and the same existing default project parent. The server binds to `127.0.0.1:8765` and
passes `/api/health`. The listener PID at verification was 11848 (launcher 17672).

The served JavaScript bytes matched the main checkout and carried `Cache-Control: no-store`.
The app browser panel was requested at the current URL. No test transcription, private upload,
new UCloud allocation or paid request was made. Real-provider/Mac/NVivo acceptance remains
separate. Subsequent commits recording this activation change documentation only.

The previous schema-12 backup is retained; older app versions cannot open schema 13 directly.
A future rollback must preserve any newer work and use a controlled idle restore.
