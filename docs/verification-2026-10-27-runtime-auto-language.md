# Explicit CBS Runtime Automatic language — offline verification

27 October 2026. T84d / ADR 0071 / V125. This stage enables an explicit per-model
full-ASR opt-in in source code. The running backend and real library remain untouched.
General Automatic defaults, alignment-only and bounded repairs remain gated.

## Delivered behavior

- UCloud Advanced settings offers saved Runtime model language controls. Blank keeps the
  existing Processing fallback; Automatic is experimental. Each save changes one raw row
  with its exact version, no model fanout, credentials, endpoint checks or policy changes.
  Ordinary connection saves preserve the saved Runtime language. Other newly backfilled
  models do not inherit an Auto opt-in. Legacy aliases display truthfully without rewriting.
- Requested timing prepares the selected ASR model, coarse recognition, Danish and English
  aligners; speakers remain separately requested/gated. No `snapshot('auto')`, inference or
  automatic preparation occurs in settings/readiness. Existing confirmation remains required.
- Remote Auto admission accepts only bounded explicit full ASR. It runs pure validators from
  the exact bounded, hash-verified frozen source bytes, refusing old incompatible helpers and
  ignoring poisoned bytecode caches. Accepted request hashes are checked before classifying
  outputs. Decisions bind current and accepted input/request receipts plus the frozen ASR pin.
- Requested alignment outcomes and usable numeric intervals, and requested speaker turns,
  are semantically checked before completion/carry. New admission refuses undeclared caches.
  Verified partial ASR and independent stages survive bad/missing alignment; empty ASR needs
  decision proof. Retry retains exact checkpoints instead of repeating completed ASR.
- Host/coordinator tests cover different per-file da/en decisions in one requested-auto owner,
  cached settlement, exact carry, reviewed-active-result preservation, Stop final harvest and
  cleanup. Cached recovery needs no inference readiness, private upload or new task/allocation;
  public membership/heartbeat controls can still be written.
- Literal Auto is refused for new OpenAI saves and official-host transcription transport,
  including Custom at exactly `api.openai.com`. Genuine custom hosts retain their declared
  behavior. Native new specifications check direct and inherited Auto before private upload;
  explicit da/en override works and owned-job recovery retains its original submission.

## Review findings

Two verified stale-form defects were fixed with red/green regressions:

- **B163:** public provider display and token came from separate reads. A controlled concurrent
  denial produced stale approvals with a fresh token. One raw-row read now owns both values.
  Evidence: ignored `test-results/runtime-language-profile-snapshot-red.*` and the green
  provider regression suite.
- **B164:** refreshing a conflicted language row, or receiving a success response after a
  later denial, updated the shared row token while generic approval drafts stayed old.
  Both browser reproductions returned Save 200 before the fix, including after successful
  row retries. The form now retains its original snapshot/token, advancing only after its
  own verified language-only mutation. Both cases then refuse generic Save with 409 while
  retaining drafts and the saved denial. Evidence: `runtime-language-cas-two-red.xml` and
  `runtime-language-cas-green.xml` (three passing cases, including normal save).

Pre-build/implementation review also hardened frozen-source execution, request downgrade
tampering, old-helper compatibility, independent stage retention and malformed numeric turn
bounds. Huge integer turn tests prove bounded comparisons refuse them without broad catches.
These are protocol checks, not language-quality measurements.

Initial fixture/assertion repairs did not relax production: use an actual catalog Runtime model,
claim the mocked native job before processing, supply the mocked prepared speaker-cache receipt,
and distinguish read-only preparation planning from a forbidden Protected enqueue/upload.

## Evidence

Final combined backend run: **611 passed, one Windows symlink-permission skip in 469.83 s**.
Evidence is ignored `test-results/runtime-auto-final.xml` and `.txt`. The 19-file selection
covers preparation, fixed/Auto workers, host pipeline/admission, pooled remote/coordinator/API,
profile/native transports and existing provider/catalog/version regressions. An initial command
named a nonexistent test file and collected nothing; the corrected selection is the run above.
Focused runs overlap it; do not sum them into an inflated total.

Final affected Chromium run: **15 passed in 176.10 s** (12 new Runtime language cases plus
three existing provider/native/Runtime flows). Evidence: `runtime-language-final-affected.xml`.
The earlier ten-case run, three existing cases and focused two/three-case reruns overlap it.
`runtime-language-settings.png` was inspected: Advanced rows, selected Auto and per-row saved
status are readable; native models remain separate. Cases cover scoped/reloaded saves, key and
enablement drafts, current Runtime ordinary Save, stale/old-backend replies, late navigation,
legacy values and both Protected-denial races with a later row retry.

- Preparation/worker/API: 256 passed, 121.92 s; `auto-language-preparation-verified.*`.
- Host/coordinator: 14 new cases plus 59 affected and 11 boundary cases passed;
  `runtime-auto-batches.xml`, `runtime-auto-affected.xml`, `runtime-auto-boundary.xml`.
- Provider/native: 151 passed, 113.29 s, including 42 new profile/native cases;
  `runtime-language-provider-regression.*`. Root language API: 10 passed, 10.26 s;
  `runtime-language-api.xml`.
- Remote controller: initial combined regression 95 passed, one Windows symlink-permission
  skip, 115.73 s. Final Auto coverage is 54 distinct passing cases across full/focused runs;
  the final combined run above includes all current cases.
- Independent read-only backend review: 172 distinct scoped cases passed across preparation/
  coordinator, remote and provider/API runs. The reviewer then passed the three focused CAS
  browser cases in 46.12 s and gave final **GO**: 175 distinct independently checked cases.
  These overlap the main suites and are not additional application coverage totals.

Whole-repository Ruff lint/format, Prettier 3.6.2 on changed frontend files, Node syntax,
Python source compilation and Git whitespace checks pass. No dependencies/lockfile changed.

## Limits

Temporary libraries, generated audio/text, mocked transfers and Chromium only. No real language
accuracy, ASR inference, weight download, paid call, private transcript inspection, live UCloud
allocation/upload, real-library migration, running-backend restart, merge or push. The prototype
policy's 0.8 threshold remains uncalibrated and its internal VAD source map unknown. One reported
recording language does not establish mixed-language alignment. Live UCloud, Mac and NVivo
acceptance, licensed policy evaluation and the final project audit remain separate.
