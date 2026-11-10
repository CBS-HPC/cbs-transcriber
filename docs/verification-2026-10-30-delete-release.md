# Delete release takeover — 30 October 2026

Base: Claude's clean integrated commit `3e93282`. Its unfinished deletion work was preserved
and completed on `codex/delete-release-and-settlement`; no running-app source or library
was changed. See [ADR 0101](decisions/0101-delete-release-and-automatic-settlement.md).

The missing browser flow now displays exact folder/server/shared-file impact, sends the
confirmed operation fingerprint, handles both cancellation and release continuations, and
retains cleanup-only Retry/reconciliation after project/job deletion. Activity never offers
inference Retry for an owner whose recordings were explicitly released.

Independent review found and fixed three operational gaps:

- Delete's remote release had no logical allocation reservation. A concurrent Retry could
  queue recovery while the local owner still said failed, before Trash. The threaded test
  now requires Retry to return 409 and preserve the release's exact scope.
- Immutable native/Runtime membership outlived partially deleted local jobs. Native Retry,
  transactional claim and import, Runtime launch and a second Delete now skip only members
  named by a deletion receipt whose job row is actually absent. Members/attempts stay intact.
- A locally failed monitor could still own a running remote job. Partial Delete now stops it
  even when the unsaved sibling requires retaining the remote folder.

Additional checks cover confirmed/wrong fingerprints, offline Trash, cleanup Retry after
local rows disappear, Stop awaiting remote termination, uncertain submissions, restored-owner
inertness, same-origin guards, automatic preservation of partial output, refusal of complete
unsaved output, and cleaned native history preceding a pending cleanup scan.

Validation uses synthetic originals and mocked UCloud only; no jobs, uploads, model downloads
or live Trash requests were made. The previous live test6 folder was already marked trashed
in the running app's metadata; current Delete still runs the older guarded implementation.

Final verification:

- Deletion/cancellation/race run: **56 passed, 1 skipped**.
- Native/Runtime/discard/Activity/restoration/portable-semantic integration: **201 passed**.
- Final owner-release checks, including automatic reservation and scan ordering: **23 passed**.
- Browser deletion, cleanup Retry and Activity clear/remove: **14 passed**.
- Ruff check and whole-tree formatting, Prettier 3.6.2 and JavaScript syntax checks pass.

These overlapping targeted runs are not a new claim of a complete repository-wide or
real-cloud run. The earlier exploratory integration run had two new-test failures: a fixture
wrote a receipt to the wrong table, and the native survivor test reproduced the missing-job
bug before the fix was loaded. The fixture and recovery fix pass in the final runs above.

Reproduce with the locked environment and source checkout using `python -m pytest` for
`test_delete_overrules`, `test_delete_release_races`, `test_delete_cancellation`,
`test_native_batch`, `test_runtime_batches`, `test_runtime_discard`,
`test_runtime_discard_activity`, `test_activity_clear`, `test_activity_remove`,
`test_restored_activity`, `test_quarantine_integration` and `test_portable_semantics` under
`tests/`. The browser run selects `test_browser_remote_release`,
`test_browser_delete_cancellation`, `test_browser_activity_clear` and
`test_browser_activity_remove` with `-m browser -o addopts=''`.

Activation remains separate: an idle, backed-up schema-13 library must be upgraded to schema15
with preservation checks, then the app restarted on the integrated main. The old library copy
is retained. Legacy single-job journals still need independent resolution before deletion.
