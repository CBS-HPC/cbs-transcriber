# Transcription, batch and Activity audit — 30 October 2026

Work in progress on `codex/transcription-workflow-audit`, integrating the deletion branch.
The independent audit baseline is
`4bbbced21642efc8b2d91717d0dda05e55be3900`. Fixes are developed in separate worktrees and
verified before integration. This is offline evidence, not acceptance of a real UCloud run.
The running app and its active jobs have been left untouched.

## Scope and method

Four parallel reviews cover admission/preparation, native and CBS Runtime execution,
Stop/recovery/cleanup, and Activity as a separate module. Read source paths through their
actual queue/Store/API/UI transitions, reproduce suspected failures with synthetic libraries,
and add regressions for confirmed defects. Use mocked remote execution and local browser
fixtures; never start a paid request, upload a private recording or download model weights.

The earlier component checks do not replace this integrated review. Nor does a passing
mocked test establish Linux/MIG execution, actual model reuse or cleanup on a live provider.

## Confirmed findings under repair

| Priority | Finding | Evidence and required behavior |
| --- | --- | --- |
| P2 | One malformed persisted admission blocks healthy later files | Nested Settings/EnhancementOptions validation escapes the per-candidate isolation boundary. Skip that invalid candidate without changing its raw evidence; both admission lanes must continue to healthy rows. |
| P2 | Standing UCloud preparation permission is frozen too early in passage retranscription | The visible enhancement option becomes true after the UCloud request has already frozen false. Freeze the final effective options; keep originally Automatic placement unable to grant preparation. |
| P2 | Generic native model alias can select the wrong model version | An enum value `parakeet` labelled v2 satisfies the canonical v3 alias. Require the declared identity to establish the selected version; retain the verified Transcriber1.12 v3 mapping. |
| P2 | Ordinary batch Activity splits after admission materializes | Two source-preparation jobs become individual producer cards and later separate enhancement cards. Preserve the durable request identity and verified follow-up lineage through these transitions. |
| P2 | Activity mixes raw intent rows with newer phase projections | Same-ID materialization can lose the observed frozen control token or split an atomic child handoff. Read raw rows, membership and intent projection in one snapshot. |
| P2 | Native Activity can fail its entire poll during deletion | Native status re-reads a deleted job after closing the listing snapshot; a real synthetic HTTP Delete reproduces GET `/api/jobs` returning404. Capture native status inputs coherently and retain the surviving cleanup-owner card. |
| P2 | Recent-history truncation loses batch members and retained obligations | An active upload can lose an older failed sibling at the recent-job limit; the 201st retained deleted-native owner disappears. Select recent groups, then project their complete bounded membership and all unresolved obligations. |
| P2 | A mutation already admitted before staged deletion can run after rollback needs recovery | The outer check refuses only later requests. Drain earlier mutators before staging, block new admission through commit/rollback, and recheck recovery after any gate wait. |
| P2 | A later-member readiness failure claims no recording was uploaded | Production upload succeeds for one member, then readiness fails for another and denies the earlier upload. Scope diagnostics to the failed transfer, preserving all prior upload evidence. |
| P2 | No-worker startup re-enters a caller-held Store lease on another thread | A portable route fixture holds the Store lease while TestClient startup tries to take its RLock again. Borrow that exact held lease; acquire a transient startup lease only when absent. |
| P2 | Admission waits can starve the endpoints they must drain | Independent exact-gate reproduction with two default worker tokens blocks an earlier admitted endpoint behind Delete and a later admission waiter. A separate one-thread admission limiter makes the earlier endpoint finish and the gate drain; default endpoint capacity stays unchanged. |
| P2 | Draft group expansion makes polling issue hundreds of repeated reads | The first uncommitted fix takes 801 read statements and 6.34–6.85 seconds for a synthetic 2,000-row history with 200 selected groups. This is a draft regression, not a measurement of the running app. The final helper takes 4 reads and 0.148–0.185 seconds; this measures expansion only, excluding existing job/owner projection. |
| P3 | Multi-model request counts overstate file count | Two recordings × two models render as four files. Count unique recording identities as files and report model/stage requests separately. |

Further allocation transitions, cleanup/recovery races and action scopes remain under review.
Only executable or directly traced evidence is listed as a verified finding.

## Activity contract

- Files imported together retain their verified project/upload grouping for inspection and
  media preparation. No timestamp or similar-name inference.
- Ordinary transcription requests retain project/request identity after preparation, including
  exact automatically chained enhancement jobs. Count recordings separately from requests.
- Native and CBS Runtime allocation identity takes precedence, including late joins. Do not
  claim that different allocations are one batch merely because a user selected files together.
- Preserve source/preflight failures explicitly when other members move into an allocation;
  do not present failed admissions as successfully submitted allocation members.
- Cards expose per-file/per-stage outcomes. Stop, Retry, Prepare and Hide keep their exact
  server-checked controls; grouping never creates processing or disposal permission.
- Truncation must not silently change membership, counters, cancellation scope or Hide checks.
  Unresolved remote/local obligations remain visible after Clear finished.
- Legacy ordinary jobs without a durable request identity remain separate. Actual native/Runtime
  owners still group; no historical membership is guessed from time, names or provider labels.

## Verification in progress

Before audit fixes, the integrated deletion checks pass **107 tests, 1 skipped**. The three
new local-cleanup browser tests pass. A complete default-suite attempt reached 28% without a
reported failure, then was deliberately stopped after the independent review reproduced the
startup lease deadlock above; it is not counted as a complete pass. A fresh integrated run
will follow that correction. Individual implementation checks and their limits are documented
in ADR0102–0105 and the shortcut guide.

Integrated fixes `d25522e`, `48e0702`, `d8e1cc7` and `b6584af` cover native version identity,
admission isolation, truthful readiness errors and final preparation consent. Independent
exact-commit review found no actionable defects. Component checks include 18 model cases,
11 malformed-admission regressions, 4 readiness diagnostics and 7 actual frozen-runner
preparation cases; broader existing checks also pass with short Windows temporary paths.
Deep temporary paths expose Windows path-length failures and remain a chosen-storage/clean-
installation limitation to test, rather than evidence of a cloud submission failure.

Real UCloud batch completion, model reuse/performance, interruption, cleanup and older-output
recovery still require separate live acceptance. Real Windows/Apple Silicon/Intel installation
and backup/restore acceptance also remain open.

The user also requested a **native Transcriber-on-B200-MIG** test (T105). This is independent
of the CBS Runtime's MIG test. Verify declared app/product compatibility, then actual native
single/two-file execution, model/device memory, outputs and cleanup against a full-B200 baseline.
The current native full-B200 policy and frozen jobs remain unchanged until evidence supports
a separate decision; no real allocation is started during this audit.
