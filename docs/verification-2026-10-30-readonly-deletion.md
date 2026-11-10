# Windows read-only deletion and dialog recovery — 30 October 2026

After schema15 activation, the user retried Delete for test6 and received a generic error.
The live traceback identified `PermissionError: WinError 5` from `os.rmdir` inside
`shutil.rmtree`: the exact managed recording directory in OneDrive had attribute `0x31`,
including `FILE_ATTRIBUTE_READONLY`. This was a local filesystem failure, not a retained
UCloud allocation or missing main merge.

Two new API regressions failed against the previous code. A real Windows synthetic project
with read-only recording/export folders, a read-only original and read-only `project.json`
now deletes successfully. Retry clears only the Windows read-only attribute on validated
managed targets; it refuses symlinks/junctions and does not bypass sharing or ACL denial.
Remaining filesystem errors return actionable409 instead of a generic500. The database entry
remains until the file phase completes.

Independent browser reproduction found the stuck progress/disabled submit case after a
project projection refresh. Dialog-local identity now finalizes that same dialog independently
of projection replacement and ignores replies belonging to a closed/reopened dialog. Opening
and retrying also reset old progress. Normal success and navigation guards remain covered.

Verification: **63 unit/API passes, one unchanged skip**, and **8 browser passes**. Ruff,
formatting, Prettier3.6.2, JavaScript syntax and whitespace checks passed. Synthetic data only
for tests; no provider requests or model downloads. The code-only update was merged and the
idle backend restarted without a schema change.

The user's failed Delete had already emptied one recording directory before its final rmdir
failed; SQLite rolled back the project removal. The fresh pre-hotfix backup therefore records
39 recordings, 38 available originals and that one known missing original, rather than
claiming it was preserved. All 22 tables and all 38 available originals match that snapshot
after restart. The initial pre-activation backup and old library copy remain retained.

Broader recoverable filesystem staging is an urgent separate task: a later lock during recursive
multi-file removal can still leave partly removed files behind retained database entries.
This fix addresses the demonstrated read-only error and truthful retry/UI handling; it does
not claim filesystem transactions or recovery of files already removed by a confirmed Delete.
