# 9 November verification — MVP rc1 preparation

The runtime source at `c9ed56c` integrates Claude's `7178ef3` handoff and the reviewed
corrections below. Main retains the full profile. The separate local MVP packaging branch
changes only `catalogs/release.json`; its source ZIP is an internal test candidate.
Real provider, device, installation and NVivo acceptance are not established by these checks.

## Included corrections

- Permanent inactive-library deletion: independently reviewed, with nested ownership overlap
  checks and a fingerprint of the observed directory entries. Normal and extended Windows
  paths agree. The active library and unsettled work remain protected (ADR 0144, B373).
- Eligible existing sole-result Working designation: snapshot recovery checks actual role
  content rather than file modification time (ADR 0141, B374).
- Editing: retain only the narrow, reviewed memo for completed historical payload validation.
  Every use still hashes current bytes and rechecks role authority. The synthetic save pilot
  improved but remains slow; it is not responsiveness acceptance.
- Standalone installation smoke: use a loopback TestClient origin under the real host guard,
  with a subprocess regression that cannot inherit pytest's test-host allowance (B375).
- Refreshed merge/conflict previews: use their own fresh Working/version authority without
  advancing the editor behind the dialog or replacing drafts. Closed previews cannot reuse
  promotion consent or send a late Apply (B376).
- Claude's split-at-cursor, recording-scoped edit lock, Delete Project wording, adjustable
  concurrency and MVP profile changes are included. Unfinished edit-speed work was preserved
  on its original branch rather than imported wholesale.

## Synthetic verification

These are layered runs, not a claim that one monolithic suite passed on the final commit.
Counts overlap and must not be added together.

| Check | Observed result |
| --- | --- |
| Handoff's complete non-browser sweep on `7178ef3` | 4,673 passed, 35 failed, 37 errors, 22 skipped |
| Every failed/error case after fixture repairs | All 72 passed in the focused rerun |
| Assembled deletion, Working, history and profile regression gate | 178 passed |
| Installation-smoke unit/subprocess checks | 14 passed |
| Full-profile browser coverage | All 535 currently collected cases have passing evidence across baseline and repaired runs |
| MVP editing/Working repeat | 31 passed; five explicit realignment-choice cases excluded because MVP hides that control |
| MVP playback, timing, notes and speaker repeat | 31 passed |
| MVP project, comparison, history, merge and walkthrough repeat | 35 passed |
| MVP export unit repeat | 50 passed |
| Standalone smoke using the existing Desktop environment | Passed startup, synthetic media, import/export and playback-range checks; no environment synchronization |
| Static checks | Ruff check/format, JavaScript syntax and Prettier 3.6.2 passed |
| Packaged MVP variant | 63 profile tests and three MVP browser cases passed; the main-only full-default assertion was intentionally excluded here and passed on main |
| Packaged MVP standalone smoke | Passed using the existing Desktop Python environment and a disposable synthetic library |

The browser coverage ledger is `nov9-browser-coverage-final.json` in the maintainer's Temp
folder; it records per-case evidence and no uncovered cases. The core and repaired unit
reports are `nov9-integrated-core.xml` and `nov9-unit-rerun2.xml`. MVP reports use
`browser_mvp_editing_final_nov9`, `browser_MVP_release_nov9`, `nov9-mvp-playback` and
`unit_MVP_exports_nov9`; the two repaired restore cases have a separate passing rerun.
Some earlier reports intentionally retain their original failures alongside corrected reruns.

Most browser failures were fixtures assuming earlier defaults: all models visible, no Working
choice, old settings labels, or media URLs without an instance marker. They were reproduced
and corrected while retaining the behavioral assertions. These do not justify new product
invariants; B375 and B376 record the actual product defects found during verification.

## Activation and repository preservation

The app was stopped before activation. The actual library passed a read-only SQLite integrity
check, had no queued/running recording jobs, and its retained allocation/preparation rows were
terminal. Counts were saved for comparison after startup. The earlier verified backup remains
available; no test recordings or older libraries were restored or deleted for this release.

The Desktop checkout's seven pre-existing tracked deletions and two stashes are preserved.
No dependency or lockfile change is required; the existing Desktop environment passed the
standalone smoke. The live app runs with the MVP override while main's packaged default stays
full. Activation returned HTTP 200 with the expected library and MVP profile. Served JavaScript
bytes matched the Desktop source; every saved table count matched the pre-start baseline,
SQLite integrity remained OK, and no recording job was queued or running at that check.

The three old GitHub branch heads are already ancestors of main. Their exact refs were saved
in a verified local Git bundle before changing the default branch and removing them with SHA
leases. Keep local development worktrees and branches. GitHub remains private and main-only;
the MVP packaging branch stays local, with an RC tag and ZIP rather than a GitHub release.

## Still requiring acceptance

- Clean managed Windows/macOS installation, real listening and long-recording responsiveness.
- Authorized native Transcriber/OpenAI runs on representative recordings and resource limits.
- A real local backup and isolated restore walkthrough, and separately scoped real UCloud transfer.
- Real NVivo Windows/Mac imports. The rebuilt synthetic pack contains 41 files; manual outcomes
  remain NOT_RUN. REFI-QDA coding interchange is still future work.
- Institutional licence/privacy approval and the wider editing performance audit. CBS Runtime
  remains parked; spelling, passage review marks and anonymisation are not implemented here.

Use the [tester guide](mvp-tester-guide-2026-11-11.md) and
[NVivo plan](work-plans/mvp-nvivo-test-plan-2026-11-11.md) to record actual outcomes.
