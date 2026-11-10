# Verification — runtime resources, native recovery and review layout

Verified 27 October 2026 on `feature/runtime-batches-and-review-layout`.

## Automated evidence

- Full unit/API invocation: **854 passed**. Subsequently added four malformed-parent cleanup
  cases and twelve independently reviewed precondition cases. Those cases pass, bringing
  distinct unit/API coverage to **870**.
- Chromium: **111 distinct cases passed** across the broad run, remaining/failed cases and
  focused reruns. The initial broad run stopped after five outdated fixture/expectation
  failures; it was not a single clean 111-case invocation. Model-group counts/labels and
  read-only MIG mocks were updated, then all remaining and failed cases were exercised.
- The realignment fixture lacked playback media, racing its confirmation with a missing-media
  toast. It now generates synthetic audio and mocks runtime detection alongside inference.
  The synthetic Uvicorn server has a bounded media-request drain; the realignment case passes
  three consecutive executions with shutdown assertions retained.
- Ruff lint/format, JavaScript syntax, Prettier 3.6.2 and Git whitespace checks pass.

All **981 currently collected distinct cases** have a passing result. Raw JUnit reports and
the coverage reconciliation are ignored local `test-results/current-*.xml` and
`test-results/current-verification-coverage.json`; they are not product data or committed
research evidence. Temporary libraries and mocked providers cover persistence, native
four-file same-allocation recovery, cleanup safety, Activity retention, model classification,
MIG specification/readiness/legacy jobs, editing, playback, exports and research history.

The independent review found that refused cleanup manufactured pending obligations. Its
initial API regression result was **8 failed, 4 passed**, then all twelve passed after the fix.
Malformed folder metadata also reproduced false absence confirmation before being rejected.

## What these checks do not establish

- No real UCloud allocation, paid transcription, private recording upload, model-weight
  download or live cleanup was performed for this verification.
- Read-only metadata identified existing native outputs and a funded single MIG product.
  Actual recovery of that allocation, the three older pending cleanup receipts, and first
  MIG performance acceptance remain pending. Alternate slash-qualified model output naming
  was not established; matching has not been loosened speculatively.
- The running backend/library has not been restarted, migrated or changed by these tests.
  Activation requires its own idle check, backup and preservation verification. No merge to
  main is implied by these results.
- Dynamic CBS Runtime batches, late file admission and concurrent allocation ownership remain
  T85. The implementation brief is a proposal, not delivered schema-12/runtime code.
- True automatic alignment language, remote compressed-media preparation, real Mac/NVivo
  acceptance and the later research/storage roadmap remain separate work.
