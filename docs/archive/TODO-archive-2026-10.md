# Historical worklist archive — through 3 November 2026

**Historical snapshot, not the current worklist.** Statuses, checkboxes, branches, activation claims and future-tense plans below reflect their dates and may be contradictory or superseded. Use [TODO.md](../../TODO.md) for current work; consult this snapshot only for historical context and evidence.

---

# CBS Transcriber implementation

## Current worklist and weekend execution

[Remaining application audit plan](../audits/application-audit-plan-2026-10-30.md) ·
[Weekend plan: 31 October–1 November](../work-plans/weekend-2026-10-31.md).
These plans do not start an automation or a live provider job.

**Activation — 2 November 2026:** The verified weekend work was fast-forwarded to
`main` at `9695c68`, pushed to the existing private repository and started on the
current test library. The user confirmed that a new recoverable backup was not needed
for this test library; none was made for this activation. The health check passed,
the served JavaScript matched `main`, SQLite `quick_check` passed at schema 15 and
no jobs were active immediately after startup. External acceptance remains open.

**User testing — 2 November 2026:** The user reports grouped original inspection and media
preparation working for files added together, including the requested cancellation check;
**Clear finished** and access to items needing attention also appear to work. A native
Whisper large-v3-turbo batch completed three files in one UCloud allocation and reported its
run folder moved to UCloud Trash. The user reported successful native batch tests with two
models and clarified that the backend still failing is CBS Runtime. Recent live metadata shows
successful MIG batches for large-v3 and Parakeet. Treat these as reported live observations,
not confirmation
that every cleanup-retry path was exercised. A five-file native Parakeet batch succeeded
remotely but initially failed import. After the filename fix was activated, retry recovered
all five results from the original allocation and reported its owned folder moved to UCloud
Trash. A separate three-file CBS Runtime Røst batch failed and remains under investigation.

**Native MIG follow-up:** One real native Transcriber 1.12 `whisper/large-v3-turbo` job
processed two short public Danish clips on the funded B200 1g MIG in one allocation. Both
outputs were saved and validated with timed words and speaker labels; logs report CUDA, but
GPU utilization and VRAM were not measured. The user reports two native models worked; recent
live metadata identifies completed large-v3 and Parakeet MIG batches. Other models and larger
workloads remain unverified. CBS Runtime's separate failure remains under investigation.
Fresh native Transcriber requests default to 1g MIG with an explicit full-GPU profile choice
under Advanced settings.
The reviewed implementation is activated on the idle test library: integrity and existing
frozen job-parameter checks passed. New-request acceptance across all native models remains
to be tested. No fallback is allowed. Consider automatic selection by batch size only after
further measurement and a separate policy decision.

| State | Work |
| --- | --- |
| Verified in integration | Speaker colors in the recording details panel match the corresponding transcript badges; wider speaker labels remain responsive. Focused and compatibility browser checks and independent review passed. |
| Planned for later | Per-recording language choice on each project recording card near Description / notes and in the individual recording toolbar immediately before Transcribe. Define inheritance from the model, fixed-language constraints, durable frozen request/provenance and grouping: native files with incompatible language settings need separate allocations. Do not imply that a blank native language guarantees automatic detection. |
| Live recovery passed | Native UCloud Parakeet returned five validly named outputs but the batch lookup and artifact validator rejected the language-free `_parakeet.json` suffix. The corrected lookup recovered all five results from the same allocation and reported its owned folder moved to UCloud Trash; exact frozen-model matching is also covered by synthetic tests. |
| Fixed; user-reported live test passed | Deleting a project could return 404 after an already-cleaned terminal shared batch retained a reference to a recording deleted earlier. The fix keeps the batch snapshot and unresolved-work guards, with 57 synthetic deletion tests and independent review. The user reports that deleting a recording and then its disposable project now works. |
| Runtime diagnostics verified offline | A CBS Runtime allocation exited before its first input was published; its original bootstrap reason was not retained. The reviewed fix preserves curated early failure evidence, terminal closure and already-downloaded result recovery. Sixteen focused and 23 existing synthetic tests passed; one initial thread-join timeout did not reproduce on isolation or the full rerun. Live Runtime acceptance remains pending. |
| Live preparation failed; recovery open | The user confirmed **Check UCloud again** starts standalone CBS Runtime preparation after refreshing. The operation failed with `runtime-busy`; its run folder and the shared runtime's preparation lock remain retained, without a completed stamp. The recorded lock owner is terminal, but safe removal is not established. Isolated-generation recovery remains unfinished; passive checks remain read-only. |
| Settings layout verified offline | Runtime status, preparation consent and Check are in the UCloud tab; Local Processing holds local settings. Responsive layout and stale-control handling passed focused browser checks; activation remains pending. |
| Integration checks passed | Reviewed loopback/Origin and legacy-backup inventory guards, Settings and Library dialog controls, Processing and capacity reply fences, and activation-checkpoint/cold-archive tooling are integrated. The browser gate passed 370; cold-archive and checkpoint tooling passed 89 focused synthetic checks and independent review. Repository-wide Ruff check and format check passed. Actual archive verification and recovery rehearsal are still required; a checkpoint alone does not copy media. |
| Unit gate passed | The complete integrated non-browser suite passed 4047 tests with 22 skipped and 370 deselected. The later cold-archive guard change is independently reviewed with 89 focused synthetic tests; its actual use remains gated below. |
| Backup limitation | Windows cold-archive qualification refuses NTFS extended attributes, including those invisible to ordinary stat flags, because the current copy mechanism does not preserve them. The separate user-EA tree prototype fixed directory-substitution and checkpoint-writer admission gaps, validates an explicit metadata contract, and passed 88 synthetic checks with scoped independent review of ordered flushing/publication. Its synthetic operator interface passed 43 checks; a metadata-only observer passed 45 checks but explicitly does not establish real backup eligibility. A separate full-schema rehearsal restored a presealed portable package to a new disconnected target and checked path binding, history and job quarantine; it does not establish generic cold restoration or EA fidelity in that target. Power-loss durability, actual EA types and kernel EAs remain unqualified, and the prototype has no real-use authority. |
| Backup options documented | [Windows EA-preserving backup routes](../testing/windows-backup-ea-options.md) distinguish file-copy limitations from a possible CBS IT-reviewed volume image. No image, source-attribute change or actual backup was performed. |
| Synthetic preservation verified | Fresh admission, original inspection, mock API output and real local portable capture/restore were composed on a tiny temporary library. The focused test and independent repeat passed; a deliberate source-body corruption was detected. This does not validate live UCloud, inference, other platforms or all restore transforms. |
| Research methods example | A disposable example now generates real reports from synthetic Store history and Working versions, explicitly marking mocked enhancement and uncaptured evidence. Seven integrated tests and an independent review passed; this is not journal, model or NVivo acceptance. |
| Accessibility follow-up | All static dialogs in the main interface derive their accessible names from their visible headings. The scoped Chromium name and keyboard checks cover all 20 dialogs; actual screen-reader acceptance remains open. |
| Browser gate passed | The complete integrated browser suite passed 370 tests (4006 deselected) on the production source at 2179c38; subsequent integrated changes are tests and documentation only. Real UCloud, Mac and NVivo acceptance remain separate. |
| Activity scale observation | An in-process synthetic poll with 2,000 ordinary upload jobs returned the expected scoped members without changing the database; five warm request/response samples had a 1.217 s median and a 2.07 MB response. This is not a browser, native/Runtime, concurrent-load or live-library measurement. |
| Integrated and checked | T98 Windows staged cleanup beyond MAX_PATH, with canonical receipts/ownership retained; integrated slice 90 passed/1 skipped across T98/T107/T105/T106. |
| Integrated and checked | T107 atomic marked restore reservation, including collision refusal and post-publication resume (ADR 0107); integrated slice 90 passed/1 skipped across T98/T107/T105/T106. |
| Implemented and reviewed | T106 offline source-installation smoke helper; bounded bundled-FFmpeg check and synthetic Windows core evidence. Actual clean Windows/Mac acceptance remains T94. |
| Integrated and checked | T105 native B200/MIG offline qualification planner; canonical model/import-override checks in the 90-pass integrated slice. Actual MIG execution is still pending. |
| Integrated and checked | T110 cancellable/bounded FFmpeg discovery and version probes (ADR 0108). A cold-decoder I/O assumption and failed-entry cleanup in its test were corrected; integrated affected slice passed 75. |
| Reviewed fixes integrated and checked | A11 retention target/connection races, destination-bound backup approval and stale Settings/preview replies (ADR 0109). Integrated backend suite 45 passed, shared backup/playback browser suite 48 passed. Existing unbound approvals require explicit reapproval of the exact destination. |
| Reviewed fixes integrated and checked | A05 playback handoff, short viewport and comparison keyboard scroll; latest seek/speed/play controls retained (ADR 0110). Shared backup/playback browser suite 48 passed using synthetic data. |
| Reviewed audit fixes integrated | A03 bounded word association/confidence and generation-bound parser receipts preserve schema-1 history while fresh schema-2 imports retain speaker 0 and reject ambiguous CSV (ADR 0111); integrated focused slice 37 passed. A04 delayed editor reply, notes and draft fixes and A06 literal newline/search fixes passed 27 combined browser checks. |
| Reviewed export fixes integrated and checked | A12 export modal navigation, snapshot metadata, Unicode filename limits and controlled timestamp overflow; integrated import/export slice 53 passed. Actual NVivo Windows/Mac acceptance remains open. |
| Reviewed audit fixes integrated and checked | A02 uncertain upload publication retains originals, metadata drafts and collection scope (ADR 0112). A07 provider save/version/policy and Settings-open fences (ADR 0113); integrated targeted backend 22 passed and browser 19 passed. |
| Reviewed audit fixes integrated and checked | A13 per-word valid partial timing evaluation (ADR 0114), current Runtime admission evidence, and literal codebook/evidence display; integrated synthetic backend 95 passed/3 deselected and browser 10 passed. |
| Reviewed startup fix integrated and checked | A01 closes schema probes and drains failed partial/later startup under the lease, preserving errors and allowing only fully drained deferred retry (ADR 0115); 14 integrated startup cases passed. |
| Reviewed enhancement fixes integrated and checked | A09 single-passage repair retains full speaker names and Runtime provenance keeps frozen pins (ADR 0116); 15 integrated cases passed. Live quality/resource acceptance remains open. |
| Reviewed report integrity fixes integrated and checked | A10 bounded metadata, canonical snapshots and captured source-ancestry/cycle validation (ADR 0117); integrated focused slice 45 passed. Corrupt synthetic histories reproduced gaps, no ordinary writer exploit identified. |
| Integration gates | Full integrated unit and browser gates subsequently passed: 4047 tests, 22 skipped and 370 deselected; and 370 browser tests, 4006 deselected. Later changes before activation were documentation or separately verified tooling. |
| Reviewed lifecycle and credential fixes integrated and checked | A01/A02 library identity and project-move destination/descendant verification (ADR 0118); A07 persistent credential deletion failure handling (ADR 0113). Integrated slice 51 passed/1 skipped. Real Cloud Files/Mac and crash-durable moves remain unverified. |
| Composite preservation check | Reviewed two-project synthetic scenario is integrated and passed, with immutable preexisting result/notes history checks and mutation-tested assertions. It does not establish live cloud or portable-restore acceptance. |
| Follow-up | T108/A01–A14 and T109 weekend audit evidence is recorded in the linked plans; complete the explicitly listed external acceptance and remaining gaps. |
| Weekend stretch review completed | [T84 and enhancement batching review](../proposals/t84-enhancement-batching-review-2026-10-30.md): shared Runtime batching exists, but a strict one-allocation bulk promise requires an admission barrier; broader Automatic language remains gated. T43h native capture is already present; live acceptance remains. |
| Synthetic anonymisation design completed | [Design and disposable demonstration](../proposals/anonymisation-local-prototype-2026-10-30.md), independently reviewed. Production authority, key/pseudonym policy, export safety and detector acceptance remain open. |
| Synthetic NVivo validation pack integrated | [Windows/Mac protocol and fixture builder](../nvivo-refi-validation-pack.md), independently reviewed. Anonymisation/NVivo integrated examples: 23 passed. All 15 manual cases remain NOT_RUN; actual NVivo/REFI-QDA support and acceptance remain open. |
| Design review | Settings → Backup: simplify access to existing local/UCloud backup, separate verified status, restore and manual retention; see the weekend plan. Actual transfers and institutional acceptance remain open. |
| Planning options after reliability gates | Anonymisation design/synthetic demonstration, NVivo acceptance pack, research-methods example, Settings usability/accessibility, CBS onboarding skill draft and improvements to existing Danish/English evaluation material. See the weekend plan for boundaries; production anonymisation and key-retention decisions remain open. |

The activation above applies to the test library and the stated revision. Verify the actual
source and idle state before any later update or restart.

## Current open tasks — 30 October 2026, after activation

Merged and activated on **main**, schema15, at `http://127.0.0.1:8765/`. The backed-up
migration and startup preserve every old row across 19 tables and all 39 original hashes.
Working versions/conflict restoration, producer capture, resident Runtime cohorts,
remote preparation, older cloud repairs, complete-backup UI and Delete release are
implemented. Entries below this section are dated implementation history; old unchecked
duplicates do not mean that this implementation still needs to be written.
[Activation evidence](../verification-2026-10-30-backend-activation.md).

**Historical offline work (30 October):** `codex/transcription-workflow-audit`, integrating
`codex/recoverable-file-deletion` and based on the running main
revision. Recoverable filesystem staging, active-card × Stop, original-inspection grouping,
source-install shortcuts, new Processing defaults, native Parakeet selection and strict
pre-allocation setup Retry are implemented with focused checks. They are not yet activated
in the user's running app. Four independent workflow audits have reproduced defects; fixes
are being integrated and checked before a fresh complete default-suite run. Do not interrupt
the user's active UCloud work to activate these changes.
[Audit findings and verification](../audits/transcription-batch-2026-10-30.md).

| Priority | Task | Still open |
| --- | --- | --- |
| High | T85e / T86 / T105 — UCloud batch acceptance | Real multi-file Røst/CBS Runtime completion, one-card/one-allocation behaviour, late additions, Stop/reconnect/cleanup and measured model reuse, MIG memory, latency, throughput and cost. Compare native Transcriber on B200 MIG and full B200. |
| High | T80 / T80b — legacy cleanup | Inspect/recover or explicitly dispose remaining legacy single-job obligations. Safe batch settlement is implemented; legacy single-job journals still require independent resolution. |
| High | T98 — filesystem deletion safety | Staging/recovery, HTTP mutation drain and cancellation-capacity fixes are integrated; reviewed Windows long-path cleanup correction and final verification remain before idle activation. |
| High | T104 — transcription/batch and Activity audits | Four parallel reviews. Fix durable ordinary-request grouping, exact file counts, recent-history membership/obligations, malformed admission isolation, frozen preparation consent, native model identity and upload diagnostics; verify integrated controls and record limits. |
| High | T110 — FFmpeg cancellation | Cold locator/version subprocesses bypass managed cancellation and can prevent worker shutdown; verified synthetic repro, isolated bounded-probe fix in progress. |
| High | T107 — restore publication | Marker-bearing target reservation and collision-safe resume are verified in an isolated branch; integrate reviewed fixes and rerun affected gates. |
| High | Publication/licensing | Resolve the October30 audit findings: choose the app licence, CBS font/logo redistribution, supplied archive/script rights, notices/model terms and publication/history policy. Private pushes do not publish the repository. |
| High | T92 / T94 — installation/deployment | Real clean Windows x64, Apple Silicon/macOS14+ and Intel Mac installs; signed installers, Mac signing/notarization, updates/uninstall and CBS onboarding skill/plugin. Optional shortcuts pass 58 focused checks; the offline smoke helper has 13 tests and a developer-Windows core pass. Real shortcut/device qualification and generated-path limits remain. Guides and wheel-based dependencies are implemented. |
| Next | T84 — language handling | General Automatic language for enhancement/follow-ups/repairs and mixed-language handling. Explicit experimental Runtime Auto already exists. |
| Next | T89e / T19b — model evaluation | Real Danish/English interviews, Edda/Røst/Whisper baselines, timing/speaker accuracy, code-switching, resource results and CBS licence acceptance. Profiles and evaluation tools exist. |
| Next | T87f / T90e — preparation/repair acceptance | Real compressed-original codec/timebase/resource tests and large MIG passage-repair quality, interruption and recovery. Implementation is complete offline. |
| Next | T91 / T91c — backups | Working library stays local; validate recoverable UCloud backup/upload/download/retention and Windows/Mac restore. CBS IT/DPO decisions on storage, encryption, key/receipt custody, retention, holds and withdrawal remain. Capture/restore/cloud routes and UI exist. |
| Next | T43 — research acceptance | Validate Working versions, conflict/restoration choices and methods reports with researchers; expand historical environment evidence. Basic version roles and local/API/native/Runtime producer capture are implemented. |
| In progress | Final deep project audit | Transcription/batch/Activity correctness, data safety and lifecycle/concurrency are under independent review now. Complete integration verification and the remaining repository/architecture/documentation pass; earlier focused checks do not replace it. |
| Open | Library module review | Review library selection and switching, creation and deletion, storage paths, backup and restore, interrupted-operation recovery, accessibility and Settings/Library navigation. Distinguish implemented behavior, synthetic verification and real UCloud/Windows/macOS acceptance; record findings and regressions. |
| Open | Backup discoverability and progress | Implement the proposed Settings → Backup entry and verified local/cloud status and Activity progress using the existing Library backup flow; real UCloud transfer, restore and retention acceptance remain separate. |
| Open | Anonymisation | Turn the synthetic proposal into a reviewed in-app workflow: evaluate suitable detectors, allow human span review and pseudonym decisions, produce separate derived results and test exports for identifying leaks. Resolve key/retention policy before real research use. |
| Open | Existing-result enhancement batching | Design and implement a bulk action for timing and/or speakers on existing results, with accurate compatible-allocation grouping, per-recording outcomes, cancellation, recovery and cleanup. |
| Open | Project and recording language | Add a project default with clear inheritance and per-recording overrides in the project cards and recording toolbar. Preserve existing projects and frozen jobs; respect backend-supported languages and fixed-language models, and group incompatible native requests separately. [Plan](../work-plans/project-language-default.md). |
| Open | NVivo and REFI-QDA interchange | Validate real NVivo imports on Windows and Mac; design and verify REFI-QDA interchange against actual tools and representative research material. |
| Open | Live and clean-machine acceptance | Verify real CBS Runtime batches and model reuse, large cloud repairs, clean Windows/Mac installations, and Danish model quality, timing, speaker labels and licence suitability. Offline tests do not establish these results. |

**Proposals, not implemented:** anonymisation (reviewed spans, stable pseudonyms and separate
results; execution/re-identification policy still to decide), plus T38/T88 AI coding,
codebooks, summaries, topics, aspect sentiment/stance, human review and REFI-QDA/NVivo
interoperability. Actual NVivo Windows/Mac imports still need validation.

**Explicitly deferred:** T93a automatic second alignment pass, T89d Hviske5.3/6 engines,
Custom/UCloud vLLM UI and provider-capacity policy. Do not start them as incidental fine-tuning.

**Far-future storage roadmap:** Active projects hosted on UCloud, multi-device synchronization,
offline reconciliation and synchronization conflicts are outside the current project scope.
UCloud backup and restore remains in scope; it does not imply a synchronized working library.

### T105 — Native Transcriber on MIG acceptance

[Concrete acceptance protocol](../testing/native-transcriber-mig.md). The isolated adapter
and authorized live run remain to be implemented/executed; this is not a mocked pass.

- [ ] Check the exact native Transcriber Batch application/version and funded B200 MIG product
  for declared compatibility. Do not infer support from the CBS Runtime's separate MIG setup.
- [ ] With explicit live-test approval, run public/synthetic audio through native Transcriber
  on MIG, first one short file then a two-file shared allocation; verify actual model, device
  memory, transcript/timing/speaker outputs, completion, Stop/recovery and owned-folder cleanup.
- [ ] Compare the same input/model with full B200 for failure behavior, throughput and cost;
  record which model/settings fit. A catalog or mock success does not establish real execution.
- [ ] Decide whether native compute policy can change only after this evidence. Existing native
  profiles/defaults and frozen jobs stay unchanged. No live test is started by this TODO update.

## Claude takeover — 30 October 2026

- [x] Located Claude's clean integration at `3e93282` and preserved its unfinished
  `claude/delete-overrules` implementation, continued on `codex/delete-release-and-settlement`.
  The integrated work is now active on `main`.
- [x] T97: Delete's explicit remote-folder impact confirmation, batch-owned cleanup
  journals surviving local deletion, Activity Retry/reconciliation, safe automatic settlement
  and surviving-member recovery verified with synthetic API/unit/browser checks.
  [ADR 0101](../decisions/0101-delete-release-and-automatic-settlement.md),
  [verification](../verification-2026-10-30-delete-release.md).
- [x] User-authorized merge and schema13→15 activation with verified online/offline backups,
  exact preservation checks and restart. The old library copy is retained.

## Integration branch — 29 October 2026 (evening)

`claude/integration` combines Codex's branches (Delete cancels jobs, Working versions, timing
and Runtime capture, remote preparation, schema 14/15, portable restore) with five follow-ups:
backup screen tests/fixes, the Windows path-containment race, API producer evidence, Activity
removal/grouping and preparation consent. [Handoff](../handoff-2026-10-29-integration.md).

- [x] Full unit suite on the final merge: 3305 passed, 20 skipped, 1 load-only failure
  (`test_runtime_model_reuse_evaluation` hit a Windows rename permission error while browser
  tests ran in parallel; it passes alone). Ruff and Prettier clean.
- [x] Browser suite: 191 passed before the consent merge; the 4 failures were tests predating
  Working-version defaults and now pass. Final browser suite: 197 passed, 0 failed (83599ed).
- [x] Authorized real-library activation completed 30 October (backup, idle stop,
  schema13→15, preservation check and restart). A live multi-file Røst batch remains open.

## Runtime preparation consent — 29 October 2026

- [x] Standing "Prepare UCloud models automatically when needed" (now in Settings → UCloud)
  (originally off; new/unset preferences default on under ADR0103, pending current activation)
  and one up-front 409 prepare_runtime per explicit UCloud CBS Runtime request,
  from a bounded read-only probe before acceptance; consent source frozen in version-2 choices.
  Unknown readiness keeps the Activity fallback. [ADR 0098](../decisions/0098-standing-and-up-front-runtime-preparation-consent.md).
- [x] B284: one Activity Prepare continues every stopped same-choice file of a non-native
  request (the live 12-file Røst case), skipping continued siblings.
- [ ] Live UCloud acceptance of the probe remains; activation completed 30 October. The
  user's already stopped intents can now use the new request-wide Prepare.

## Research history integration — 29 October 2026

- [x] Explicit immutable **Working transcript** designation, independent from the review view;
  no automatic promotion. Project export defaults to that exact version and lists unassigned
  recordings as skipped.
- [x] Fingerprint-bound conflict/restoration choices, guarded History previews and exact
  source/parent references, with atomic versions and preserved research wording/voices.
- [x] Actual local enhancement and per-unit timing request/audio/stage capture; atomic local
  timing Apply and verified lost-reply recovery preserve newer edits and timing.
- [x] Fresh CBS Runtime full-ASR request/stage/control observations now cross the actual
  publication/collection and result-creation boundaries, with immutable attempt/version binding
  and metadata-only historical reports. [Runtime hook checks](../verification-2026-10-29-runtime-artifact-hooks.md).
- [x] Fresh API transcription (OpenAI/custom/UCloud vLLM) request descriptors and normalized
  outputs bind to their creation version, with metadata-only reports, portable file roles and
  the contained semantic kernel (ADR 0097). Synthetic/mocked checks only.
- [x] Fresh native UCloud Transcriber jobs (single and shared batch members) record a
  credential-free submission projection before POST and the exact downloaded output, bound
  at creation with journal checks, metadata-only reports, portable roles and the contained
  kernel (ADR 0100). Mocked UCloud only; live allocation acceptance remains separate.
- [x] Combined schema14/15 integration on `claude/integration` (fresh and real 13→15 migrated
  structures match the portable catalogs); absent observations stay explicit.
- [x] Authorized real-library schema13→15 activation completed 30 October with preservation checks.
  [Research verification](../verification-2026-10-29-research-versions.md).

## Complete backups in the Library dialog — 29 October 2026

- [x] ADR 0096 routes are wired into the app and the Library dialog's complete-backup panel:
  capture, receipt save/load, isolated restore into a new registered library, explicit playback
  preparation, UCloud storage settings/upload/folder reconciliation and manual Trash retention.
  Verified with synthetic API tests (`tests/test_portable_routes.py`: settings validation,
  capture in a running app, queued/running/active refusal, admission gate both ways, same-origin
  rejection before admission, restore/resume, mocked upload/operations/retention) and a
  Chromium panel test (`tests/test_browser_portable.py`). Fixed: idle Runtime capacity
  observations intermittently refused captures; refusals left empty partial folders; an
  unapproved upload froze an unresumable journal; the admission gate ran outside host/origin
  checks; the database-only inventory controls had lost their script.
- [ ] Real UCloud upload/download/retention acceptance (no live transfer has been run) and real
  Windows/macOS restore of a real library. Real-library activation completed 30 October.
- [ ] CBS IT/DPO approval of backup storage, encryption/key and receipt custody, retention and
  withdrawal/hold processes. Saved settings and technical defaults do not establish it.
## Activity removal and grouped cards — 29 October 2026

- [x] **T96:** × Remove from Activity hides one finished card (V162), refusing active
  work and UCloud/selection obligations; requests and multi-file uploads render as one
  grouped card; Activity card notes no longer overlap. Synthetic API/browser checks pass;
  one confirmation for every request sibling is separate server work.

## Current implementation — automatic cancellation on Delete

- [x] **T95, isolated implementation:** cancel a recording/project’s own jobs from Delete, wait for workers before
  removing files, confirm effects on other recordings sharing a UCloud allocation, and keep
  unresolved remote evidence. Finish-only requests, exclusive Stop/Retry controls and inline
  progress pass synthetic API/browser checks. Working-role/restored-owner integration passes;
  schema15 activation completed 30 October. [ADR 0092](../decisions/0092-delete-cancels-owned-jobs.md),
  [verification](../verification-2026-10-29-delete-cancellation.md).

## Runtime continuation — 29 October 2026

- [x] T85j: production finite resident cohorts (maximum eight tasks/two-second collection),
  one real ASR/alignment/speaker model slot, immutable identity/load receipts, exact frozen
  request bytes and separate intermediate-stage/final-task acknowledgement (ADR 0094).
- [x] T85k: synthetic late-cohort, shared Stop, partial-output, child-loss/restart and
  constructor-count checks; legacy captured plans retain per-file dispatch. Real model
  throughput/memory/cost remains the separate T85e acceptance task.
- [x] T80b: reviewed five-file stopped Runtime disposition completed through the released,
  freshly fingerprint-checked API after direct approval; local rows/member evidence retained.
  Five older singleton obligations remain protected, pending metadata preview/recovery or
  separate explicit disposition. New singleton preview reads metadata only; ordinary Cleanup
  refuses unsaved private artifacts and retains public setup-only cleanup.

Final scoped counts and limitations are recorded in the Runtime continuation evidence.

## Source transport and older repairs — 29 October 2026

- [x] **T87f:** format-aware compressed-original transport for fresh whole CBS Runtime jobs,
  pinned Linux decoding, bounded canonical PCM preparation, immutable source/request/decoder
  receipts and exact language-evidence binding. Unsupported/video/larger sources retain
  original-derived local WAV preparation. Local full original inspection still runs first.
- [x] **T90f:** older/uninspected selected repairs accept metadata only, then anchor bytes and
  prove absolute selected-window PCM continuity in the worker. Scoped receipts keep whole-file
  frames/PCM unknown; only committed <=60 s clips reach the prepared MIG repair route.
- [x] Independent source-authority review, mocked full result-creation checks, actual synthetic
  timestamp-gap checks and a tiny pinned Linux decoder round trip. Live MIG resource, cost,
  speech/timing-quality and broader codec acceptance remain separate.

[Verification and limits](../verification-2026-10-29-remote-preparation.md).

## Integration checkpoint — 29 October 2026

The verified checkpoint has been merged to **main** and activated on **schema 13** at
`http://127.0.0.1:8765/`, after a fresh verified backup and idle checks. Every preexisting row
across all 19 tables remained exact after startup; only three Danish catalog profiles were
added. [Activation evidence](../verification-2026-10-29-backend-activation.md). The four
native outputs below remain the only live transcription recovery performed in this work.

- **T87b source/admission:** code complete and independently reviewed. Two source inspectors
  and two admission workers pass actual short-file/blocked-worker concurrency tests. Final
  scoped set: **138 passed, 1 Windows symlink-permission skip**; the separate metadata HTTP
  conflict/policy regression passed. Original source and browser playback are independent.
  Consent continuation, native cohort barriers, cancellation/recovery, immutable receipts,
  corrupt-row quarantine and atomic import publication are covered.
- **T90c selected repairs:** bounded public integration and independent review complete.
  **196 focused tests**, **11 API cases**, the additional no-repeat recovery case and the
  historical-cleanup browser case pass; independent final API/Apply/routing set: **22 passed**.
  [Evidence](../verification-2026-10-29-selected-repairs.md). New large cloud repairs require
  prepared/approved MIG setup and an already-ready original receipt. Legacy/uninspected
  recordings retain local/Stop fallback; broader source-proof support remains later work.
- **Integration verified:** the first broader run stopped at **318 passed, 12 failed,
  1 skipped**. One API test and eleven native cleanup cases used obsolete immediate-preparation
  fixtures without the required lease. The API migration also exposed a real HTTP400 versus
 409 regression; typed409/403 handling is now fixed and tested. Test migrations retain the
  old policy, no-resubmit, reviewed-result and cleanup assertions and use real synthetic source
  inspection/admission. A fresh complete default run finished with **2,958 passed, 3 failed,
  17 skipped**; all three outdated expectation failures pass corrected focused reruns. Five
  manual-start lease warnings were reproduced and fixed in tests; all 15 affected module cases
  pass with thread warnings as errors. The complete browser run finished **158 passed, 15 failed**;
  the final 20 affected/adjacent browser cases pass after fixture corrections and the import
  guard fix. Exact deduplicated coverage is **2,961 unit/API passed, 17 skipped; 175 browser
  passed**, with no outstanding failures. These counts combine broad and affected reruns,
  not one new all-green invocation. Global Ruff, Python formatting, JS syntax and Prettier pass.
  [Exact evidence and limits](../verification-2026-10-29-release-gate.md).
- Producer artifact hooks, working/alternative roles and conflict handling remain T43 work.
  Helper/proposal completion does not mean these are implemented. The final project audit
  follows current implementation; live UCloud, Mac, NVivo and licence acceptance stay distinct.
- **Migration rehearsal passed:** an isolated SQLite backup of the actual schema-12 library
  migrated to schema 13 with exact preservation across all 19 tables, a verified premigration
  backup and idempotent reopen. The subsequently authorized real-library activation is verified
  above. [Release evidence](../verification-2026-10-29-release-gate.md).

Focused totals overlap. The test runs made no provider allocation, private upload or trained-model
download. The authorized main merge/activation followed them; no environment synchronization
was needed. Automatic second-pass alignment and Hviske 5.3/6 remain explicitly deferred.

## Pending work — current overview, 29 October 2026

**T94 — guides complete; clean installation acceptance pending:** README and AGENTS now
provide exact HTTPS clone and source-ZIP instructions for the planned public
`CBS-HPC/cbs-transcriber` repository, Windows x64, Apple Silicon/macOS 14+ and Intel Mac.
Both routes explain native-Mac limits, local unsynced paths, first/later start, safe updates
and common setup errors. Git is needed for clone/update operations; the source-ZIP route
uses the released UCloud client wheel. Anonymous clean-machine installs still require
publication and actual platform acceptance; repository visibility and the running environment
are unchanged. [Initial guide evidence](../verification-2026-10-29-public-install-guide.md).

**T94a — release-wheel source verified offline:** the official ucloud-workflow 0.1.9 wheel
replaces its Git source, with an exact uv-generated SHA-256 lock. All 151 other packages and
platform markers are unchanged. Three Git-free offline package plans and **243 mocked
integration/platform cases** pass using the actual verified wheel. No shared environment
was synchronized; real clean-machine acceptance remains pending.
[Source evidence](../verification-2026-10-29-ucloud-release-wheel.md).

**T87e is complete:** integration coverage, the isolated migration rehearsal, main merge and
backed-up idle schema-13 activation are verified. The requested final project audit follows
implementation. Real provider/Mac/NVivo acceptance stays distinct from these checks.

| Status | Task | Remaining scope |
| --- | --- | --- |
| Partly implemented | T43/T43h — research versions/provenance | Wire durable producer artifacts into actual result creation; working/alternative roles, explicit promotion and fuller conflict/restoration decisions. Existing immutable history, safe merges and the artifact helper are delivered. |
| Partly implemented | T85/T85e — Runtime efficiency | Production resident-model reuse and measured throughput; extend real batch/recovery/late-admission/Stop/cleanup acceptance. Pooling and the synthetic reuse experiment are delivered. |
| Implemented offline | T87d/T87f — remote preparation | Compressed-original transport/pinned remote decoding and immutable receipts are delivered. Live resource/cost/codec acceptance remains. |
| Implemented offline | T90d/T90f — older passage repairs | Bounded source/absolute selected-timeline proof and clip-only cloud repairs are delivered; live MIG/timing acceptance remains T90e. |
| Partly implemented | T84 — processing language/defaults | General Automatic-language and repair integration. Native/Runtime catalog separation and explicit experimental Runtime Auto stages are delivered. |
| Partly implemented | T80 — older remote cleanup | Resolve other retained cleanup/disposition obligations. The four-file native output recovery and owned-folder cleanup are complete. |
| Partly implemented | T91 — backup/restore | Complete capture, isolated restore, UCloud upload/recovery and manual Trash retention are wired into the Library dialog (ADR 0096) with synthetic API/browser checks. Real UCloud transfer acceptance, real-library activation and CBS IT/DPO decisions remain. |
| Planned | T38/T88 — AI analysis | Shared AI engine, automatic summaries/topic discovery/coding, hierarchical codebooks, topic sentiment/stance and researcher review. Manual evidence/review prototype exists. |
| Planned | T88a–c — NVivo interchange | REFI-QDA .qdc/.qdpx mappings and actual NVivo validation of codes, exact passages and memos. |
| Planned | Deployment | Installers/shortcuts/updates, colleague onboarding and shared CBS ChatGPT Work installation/use skill/plugin; pilot ownership/support and cost allocation. |
| Planned | OpenAI passage re-transcription | Bounded clips with explicit per-request cost confirmation. |
| External acceptance | T89e, T86, T90e and deployment | Real Danish/English/interview quality, licence clearance, MIG/clip resources, clean Windows and actual Apple Silicon/Intel Mac installation, NVivo Windows/Mac imports, CBS IT/DPO decisions. |
| Deliberately deferred | T93a, T89d and later provider UI | Automatic second-pass alignment, Hviske 5.3/6 separate engines, Custom/vLLM UI/release and provider capacity policy; PDF/ELAN only for a concrete workflow. |

Older dated sections below retain their evidence and implementation history. Their repeated
items are not additional independent tasks; this overview and SPEC task rows describe current scope.

## Active continuation — 28 October 2026

- [x] Investigate low word-timing coverage in the saved Runtime results. The inspected
  interview has 8,184/11,986 timed words (68.3%), while 945/1,175 passages are incomplete
  (80.4%). Retained raw intervals and receipt hashes account for the legacy 0.3 score filter;
  no additional normalization loss was found. This does not validate accuracy against audio.
  [Investigation](../evaluations/runtime-timing-coverage-2026-10-28.md).
- [x] Show timed-word coverage separately from complete/partial/untimed passages, with
  accurate repair scope. Five focused browser cases and the screenshot rerun pass.
- [x] **T93:** implement and verify offline the user's choice to retain valid timing
  estimates for fresh WhisperX requests and keep uncertainty hints. Missing policy keeps
  legacy behavior and bytes; historical results stay unchanged. [ADR 0089](../decisions/0089-valid-timing-estimates-and-coverage.md).
- [x] **T89c:** implement Danish profile/UI/report integration for Edda v0.2 turbo,
  Edda v0.2-large and Røst v3. The pinned conversion helper, isolated companions, fixed-Danish
  controls and pure conversion receipts pass independent review and focused tests.
  [Evidence](../verification-2026-10-28-danish-runtime.md). **T89e** real model inference,
  comparative accuracy and CBS licence acceptance remain separate opt-in work.
- [x] **T87b:** background original inspection/admission and independent playback readiness
  are implemented and reviewed offline. Metadata-only acceptance, one native selected-set
  barrier and phase/epoch-bound actions are covered. T87e release verification remains.
- [x] **T90c:** prepared-only MIG selected-clip repair transport, per-unit recovery and public
  queue/routing/Apply integration are implemented and reviewed offline. Older source proof and
  real MIG acceptance remain T90d/e.
- [ ] **T43h:** producer durability and exact creation-transaction binding now cover local ASR,
  per-unit timing, CBS Runtime and API transcription (ADR 0093/0097); Working designation and
  conflict decisions are implemented. Native UCloud Transcriber capture remains.
- [x] Recover the four native outputs through the same successful allocation, with no repeated
  ASR. Four new results were saved; all 15 earlier results, 27 versions, four notes versions and
  reviewed active heads were preserved. Owned-folder Trash was independently verified.
  [Recovery evidence](../verification-2026-10-28-native-recovery.md).
  Two current Runtime allocations have successful saved-result evidence; actual resident-model
  reuse is still absent. [Offline reuse limits](../evaluations/runtime-model-reuse-2026-10-28.md).
- [ ] **Deferred by user:** automatic second-pass alignment is later fine tuning. Do not add
  default extra inference now. Hviske 5.3 and 6 remain planned separate-engine work, also deferred.

The navigation label is **Settings**. Verified development changes have not been deployed to
the running app. Tests use temporary libraries and synthetic inputs; no trained weights,
new paid/provider jobs, backend restart, real-library migration, merge or push are included.

## Development checkpoint — 28 October 2026

These verified stages are on `feature/runtime-batches-and-review-layout`; they have not
been activated in the app. The serving checkout remains `c3c94e6` with schema 12. Preserve
its environment, library and active jobs. Focused test totals below overlap other evidence;
they are not a new whole-suite total.

- [x] **T85g — Batch progress.** One Runtime activity reports saved/failed/cancelled/waiting
  counts and confirmed active stages. The bar measures results saved locally; no invented
  recognition percentage or ETA. Shared actions and retained obligations are unchanged.
  [Evidence](../verification-2026-10-28-runtime-progress.md): 18 API and 13 Chromium cases.
- [x] **T43g — Focused version-history review.** Preserve newer independent notes when a delayed
  restore reply arrives; retain three known captured Whisper revision pins in methods reports.
  [Audit](../audits/version-history-2026-10-28.md): two reproduced defects, 22 focused cases
  after fixes. Working/alternative roles and fuller historical environment recovery remain open.
- [x] **T92 — Windows/Mac source-install audit.** Fix malformed registry shapes and an advisory
  bookmark-write failure preventing browser opening; 37 affected cases pass. Windows x64,
  Intel Mac core and Apple Silicon/macOS 14 dependency plans pass. Real Mac/clean-machine,
  shortcuts and installer acceptance remain open. The current Git dependency requirement is
  now explicit. A checksum-verified release-wheel candidate removes Git in isolated plans;
  adoption is deferred to avoid mutating the environment shared with the running app.
  [Audit](../audits/installation-2026-10-28.md).
- [x] **T89b — Offline Danish/English evaluation framework and licence addendum.** Pinned strata,
  safe supplied-output scoring, missing-evidence reporting and incremental primary-source
  licence evidence are available. 26 new cases pass; independent review GO. Only five public
  Danish read-speech clips currently have references. No pinned ASR weights are cached here;
  actual inference, interview/English/mixed-language baselines and CBS clearance remain open.
  [Guide](../danish-evaluation-suite-2026-10-28.md).
- [x] **T90b — Alignment-only selected-clip bundle prerequisite.** Immutable canonical PCM/text
  inventories, exact historical/source/receipt binding and offset-once output mapping pass
  125 combined source/bundle cases and independent review. No public or UCloud routing is
  enabled. Queue admission, upload, per-unit remote recovery/Stop/cleanup, stale application
  and Automatic routing are the next slices. [Evidence](../verification-2026-10-28-repair-bundles.md).
- [x] **T87c — Source/preparation report projection.** Metadata-only validation, aliases,
  separate context budget and explicit local/remote evidence gaps pass focused report checks.
  [Evidence](../verification-2026-10-28-original-inputs.md).
- [x] **T87b1 — Durable original-inspection prerequisite.** Internal schema-13 cache, fenced
  local inspection, retained byte identities and cancellation/recovery are verified: 43 new
  inspection cases, affected API/media/recovery checks and 11 independent probes pass. Generic
  actions cannot bypass its state checks. No automatic lane or public admission is enabled;
  the running app remains schema 12. [Evidence](../verification-2026-10-28-original-inspection.md).
- [x] **T87a/T87b — Preparation admission implementation complete offline.** Whole-recording
  direct-original source/receipts now use durable background inspection/admission, retaining
  accepted selections and one compatible native allocation independently of playback readiness.
  Legacy jobs, durations and bounded repair behavior are preserved. T87e broad verification
  and activation remain. [Evidence](../verification-2026-10-29-input-admission.md).
- [ ] **T87d — Live preparation acceptance for many files.** T87f implements format-aware
  compressed-original transport, pinned decoding and immutable bounded receipts inside existing
  Runtime ownership. Local full inspection remains; no GPU-time or cost improvement is claimed.
  Measure real transfer/decode/resource/codec/timebase behavior on an explicitly approved MIG
  before performance claims or further pipelining. Browser playback and OpenAI/local routes
  remain separate. [Assessment](../evaluations/batch-remote-preparation-2026-10-28.md).
- [x] **Start-button diagnostic closed as unreproduced.** The user confirms it works again;
  six isolated checks of the serving frontend passed. No fix or cause is claimed.
  [Diagnostic limits](../verification-2026-10-28-start-click-diagnostic.md).

Native output/cleanup recovery, successful live Runtime acceptance, T43 remaining redesign,
T89 real evaluation, T90 routing, T91 backup/restore and institutional review remain pending
as listed below. The final deep audit still follows implementation; focused audits do not
replace it. No model downloads, paid/provider jobs, app restart, real-library migration,
merge or push were part of this checkpoint.

## Consolidated current work — updated 27 October 2026

This section is the current priority list. Older sections below retain the implementation
record and supporting detail; repeated items there are the same work, not additional tasks.
The verified baseline is commit `e2a6d4e` (24 October): 793 unit/API and 105 Chromium tests
reported passing. The current branch is `feature/runtime-batches-and-review-layout`.
The fixes below have dedicated unit/API/Chromium regressions. **870 distinct unit/API and 111
Chromium cases pass**, across the broad runs and focused reruns; Ruff/Prettier/syntax checks
pass. See [verification and limits](../verification-2026-10-27.md). Saved interview data and
the running backend have not been changed for these tests.
Separately, the user-authorized backend restart on 27 October activated verified commit
`c3c94e6` and schema 12 after online/offline backups. Existing research data was preserved;
active processing is left undisturbed. [Activation evidence](../verification-2026-10-27-backend-activation.md).

### P0 — Recover failed work and make outcomes reliable

- [ ] **T85f — Live Runtime interruption and useful diagnostics.** The post-restart five-file
  run did share one MIG allocation. It entered ASR and later followed the explicit cancellation
  path before a complete output; no inference exception is recorded. Establish why cancellation
  was requested without attributing an unlogged action. Correct the verified local overwrite of
  stopped/interrupted reasons with generic processing, and keep successful end-to-end acceptance
  separate. [Evidence](../verification-2026-10-27-live-runtime-stop.md). Clarify stage versus
  whole-file/batch progress and distinguish waiting files from the active GPU task.
  **Diagnostic correction verified offline (V129/B171):** retain the current collected reason
  through child-future settlement; stopped/interrupted no longer becomes generic processing.
  Four new seam cases and affected runtime tests pass, with one unchanged startup-deadline
  rerun and one Windows permission skip documented. The serving checkout is still c3c94e6;
  this does not establish the original cancellation cause or successful live completion.
- [x] **T80a — Native UCloud Batch output import.** Investigate the reported four-file failure.
  The local Activity API records remote SUCCESS but four failed output imports. Establish the
  per-file transfer/path/format cause, add a reproducing test, and recover the existing allocation's
  outputs without repeating successful ASR. Preserve results, raw responses and shared journals.
  **Implementation done:** read-only inspection confirmed UUID_model_language filenames;
  strict matching and same-allocation recovery pass synthetic four-file regression tests.
  **Live recovery complete:** the existing successful allocation supplied all four validated
  outputs. Raw hashes match; four results/creation versions were appended, reviewed selections
  and earlier versions retained, and only the owned folder moved to UCloud Trash. No new
  allocation/upload/inference. [Preservation evidence](../verification-2026-10-28-native-recovery.md).
- [ ] **T80 — Remote cleanup recovery.** Resolve the three older completed runs with pending
  cleanup. Establish whether files actually remain before claiming removal. Persist useful safe
  failure stages/reasons, including local validation failures; test shared cleanup and recovery.
  Move only verified owned folders to UCloud Trash after durable local save. Emptying Trash or
  removing unrelated failed-run folders remains a separate, explicitly authorized action.
  **Implementation done:** persist safe cleanup stage/code/status/time, including prerequisite
  failures; shared missing/corrupt response checks prevent Trash. Actual older receipts remain open.
- [x] **T82 — Playback visibility.** Keep the active word/passage inside the visible transcript
  above the bottom player, including Audio/Video, comparison, small windows and resize. Respect
  Follow playback and retain manual scrolling; cover the reported overlap in a browser test.

### P1 — Compact review and understandable Activity

- [x] **T81 — Header and result layout.** Remove the entire breadcrumb strip on project and
  recording pages (Workspace / project and project / recording); move content upward. Place
  Review result below the recording title on the left, followed by Compare with and Versions
  on the same row (revised 27 October). Align Versions and Find in transcript with the
  transcript panel's right edge, matching the Versions button and dropdown heights;
  centre Transcribe between feature controls and search when they share a row;
  remove the redundant saved-result summary and tighten the space above transcription controls;
  wrap naturally on small screens. Preserve navigation/actions.
- [x] **T81 — Single-result comparison.** Disable Compare with whenever fewer than two live
  saved results exist, including after refresh, result switching, Trash/Restore and new results.
- [x] **T83 — One Activity card per native batch.** A four-file native batch appears as one activity,
  with shared state/progress/remote link/Stop/Retry/cleanup controls and expandable per-file
  outcomes. Preserve per-recording jobs for editor status and idempotency. Apply the same
  presentation to CBS Runtime batches when T85 is implemented.
- [x] **T83 — Clear finished and cleanup obligations.** Make the distinction visible and avoid
  duplicate pending-cleanup cards. Keep unresolved ownership/recovery accessible after clearing;
  never erase journals or invent successful cleanup to hide an item. A persistent **Needs
  attention** section retains these actions. Remote terminal jobs no longer show a needless Stop.
  Clear finished now folds older unresolved cards into one closed, counted summary; expand it
  for cleanup/recovery. Four focused browser cases cover clearing, reload, actions and unchanged
  journals. The real four-file startup failure was traced to a stale backend reading the newer
  template, before allocation; [diagnosis and five renderer checks](../verification-2026-10-27-supervisor-startup.md).
  Current browser assets now refuse CBS Runtime enqueue on an older backend, with an explicit
  restart/no-job message; OpenAI/native UCloud remain usable. Four mocked browser cases pass.
  The user's later restart request superseded the earlier keep-running choice. The verified
  batch backend is now active; live outcome/cleanup acceptance remains separate.
- [x] **T84 — Model grouping.** Under the UCloud connection, distinguish native UCloud Transcriber
  models from CBS Runtime / WhisperX models in Available models and transcription selectors.
  Retain model IDs, existing choices, results and frozen execution identities.
  Catalog schema 2 has separate native/runtime groups; legacy schema-1 overrides normalize only
  in memory. Labels use (Transcriber)/(CBS Runtime). ADR 0062.
- [ ] **T84 — Processing defaults.** Do not require a visible language selection by default;
  plan automatic language handling and keep any manual override explicit/advanced. Check the
  alignment language requirements before changing defaults. With no detected local GPU, lock
  Processing device to the applicable CPU behavior and disable the GPU multiplier. Preserve
  saved preferences for later hardware availability and distinguish unknown from absent GPU.
  **UI done:** manual language is Advanced; known-no-GPU controls lock without changing stored
  preferences, and unknown status stays distinct. True automatic language handling remains open.
  Design review: [automatic language handling](../proposals/automatic-language-review-2026-10-27.md)
  covers frozen requests, detected-language checkpoints, retries and mixed-language limits;
  this is a proposal, not an implemented default change.
  **T84a verified offline:** ADR 0067 adds configured-language evidence to new ASR stage outputs,
  bound to prepared audio/request/model/window identities and propagated safely to research
  metadata. Legacy stages and saved/frozen defaults stay unchanged. Automatic detection itself
  remains open until worker, alignment, checkpoint and default-compatibility paths are ready.
  [Evidence and limits](../verification-2026-10-27-language-evidence.md): 172 affected cases
  pass, one Windows symlink case is skipped; synthetic audio/stub ASR only. Hash-bound decoded
  caches and actual mapped-buffer checks fix a verified same-length stale-audio defect.
  **T84b verified offline:** standalone full-ASR Automatic language decisions (ADR 0069), with
  conservative outcomes, effective-language alignment and completed-ASR checkpoint reuse.
  General settings/defaults, remote admission and bounded repairs remain gated; the detector's
  internal VAD window is unknown and its prototype support threshold is not calibrated.
  [Worker evidence and limits](../verification-2026-10-27-automatic-asr-worker.md): 176 distinct
  unit/API cases pass across focused runs; independent review GO. Auto app integration,
  durable partial results and bounded-language handling remain open.
  **T84c verified offline:** validate and save full-ASR partial/empty Auto results and exact typed
  alignment outcomes; preserve language provenance in versions/reports and recover completed
  stages without inference (ADR 0070). Public/profile/remote admission and general defaults
  remain closed until their preparation/carry/operation contracts are ready.
  [Evidence and limits](../verification-2026-10-27-auto-partial-results.md): 358 unit/API
  cases and three Chromium cases verified; independent review GO. No running-app activation.

  **T84d verified offline:** explicit per-saved-model Automatic opt-in for CBS Runtime full
  transcription (ADR 0071), with both-aligner preparation, strict remote acceptance/carry and
  valid partial/empty checkpoint recovery. Other models, blank Processing fallback, general
  defaults and existing-text/bounded repairs keep their behavior. OpenAI/native never receive
  literal auto. Separate form versions prevent language refresh/retry from overwriting a
  concurrent Protected denial. [Evidence and limits](../verification-2026-10-27-runtime-auto-language.md):
  611 unit/API cases pass, one Windows symlink-permission skip; 15 affected Chromium cases pass;
  independent review GO. No live inference, backend restart or real-library activation.

  **T84e verified offline:** gated local bounded-input language checkpoint and alignment worker
  (ADR 0072/V126), with exact sampled scope, no ASR and durable retry. This standalone developer
  seam does not expose public repairs: original-source mapping, text compatibility, host
  carry/provenance and general defaults remain gated. [Evidence and limits](../verification-2026-10-27-clip-language-worker.md):
  334 distinct affected unit/API cases pass across two final runs; independent review GO.
  New clip semantic fixtures capture volatile progress events after diagnosed Windows locking;
  real data/checkpoint writes and dedicated writer-lock tests remain. Production writer unchanged.

### P2 — Efficient CBS Runtime execution on UCloud

- [x] **T85 — Core shared runtime batches (offline).** Send several selected compatible recordings to one CBS
  Runtime allocation with per-file input/output receipts and results. Reuse the loaded model
  where safe; bound parallel execution by GPU memory. This is separate from T59's already built
  native Transcriber batches.
  **Core implemented and verified:** [runtime batches implementation brief](../runtime-batches-design.md) covers
  durable allocation/member receipts, exact compatibility, late admission/sealing, partial results,
  cancellation/retry/cleanup, model reuse, bounded concurrent allocations and six tested slices.
  ADR 0065 accepts the initial capacity, grace and ownership rules. Schema-12 persistence,
  the remote controller, preparation locks, coordinator integration and Activity pass synthetic
  and browser verification. See [evidence and limits](../verification-2026-10-27-runtime-batches.md).
  User-authorized activation now serves the verified backend with schema 12; preserved rows
  passed integrity and identity checks. Live batch behavior remains acceptance work below.
  Each MIG executes one full pipeline at a time; resident model reuse remains a measured
  optimization, not a claim of the first batching implementation.
- [x] **T85 — Add files to running allocations (offline).** Keep an eligible runtime accepting work;
  atomically attach compatible files while it is open. Different model/runtime/connection or
  incompatible settings start another allocation. Define accepting/draining/closing states,
  cancellation, shutdown, retry, partial success, ownership and cleanup before implementation.
- [x] **T85 — Concurrent allocations (offline).** Permit several independent CBS Runtime UCloud jobs,
  with bounded local coordinators and explicit capacity controls; keep the local enhancement
  lane independent. More threads alone must not replace durable shared-run ownership.
  Initial limit 2, editable 1..4. Processing retries retain validated stages; failed local saves
  retry the same completed task. Parent native Stop preserves mixed Runtime owners, with a
  durable notice. Unprovable interrupted publication pauses instead of rewriting remote inputs.
- [x] **T85 — Retained failed-run disposition (offline).** A distinct whole-batch discard action
  showing affected members and missing results before confirmation. Verify originals/frozen
  inputs, preserve all available successful output and require fresh remote terminal evidence;
  records the abandonment decision before moving only the owned folder to Trash. Failed or
  partial private runs remain retained until verified disposition or recovery. Never
  make Stop, Clear finished, Retry or deletion silently discard these files. Proven public-only
  script folders have a separate safe cleanup path; inspect every attempt before classifying them.
  **Verified:** ADR 0066 defines a separate preview/confirmation fingerprint, every-attempt
  local output preservation, complete-unsaved refusal, fresh exact terminal identity and atomic
  recovery fencing before owned-folder Trash. All 58 focused unit/API and four browser cases
  pass; affected coordinator cases pass with the production heartbeat cadence in the fixture.
  [Evidence and limits](../verification-2026-10-27-runtime-discard.md). Changed evidence after
  recorded consent remains blocked for explicit review. No real files were moved or jobs started.
- [ ] **T85 — Model reuse and live acceptance.** Measure safe resident-model reuse and MIG
  throughput before changing the first controller's sequential per-file GPU pipelines. Validate
  selected-many, compatible late joining, separate allocations, restart, Stop and cleanup with
  licensed public media on UCloud after explicit live-run authorization.
- [ ] **T86 / T73b — MIG for WhisperX.** The user's 26 October request supplies the decision
  to prefer an appropriate MIG slice for small enhancements. Discover exact funded products
  read-only, freeze a separate runtime product, validate GPU memory/readiness, retain existing
  frozen full-GPU jobs, and live-test one approved slice. Full-GPU use needs an explicit policy;
  do not silently replace native Transcriber's current fixed-B200 contract.
  **Offline implementation done:** verified `gpu-nvidia-b200-1-mig.1g`, separately frozen product
  and mig-v1 policy, first-device VRAM check before private upload, no full-GPU fallback.
  Mocked selection/specification/legacy/readiness tests pass; live MIG execution is still pending.
- [ ] **T87 — Common original input and independent playback preparation.** Browser playback is currently prepared
  locally; CBS Runtime additionally decodes local playback audio to a 16 kHz WAV for upload.
  **Observed coupling (27 October):** browser playback readiness and
  transcription readiness are currently coupled. OpenAI chunks and CBS Runtime audio depend
  on the prepared playback file; native UCloud Transcriber uploads the original, so its wait
  for browser playback is unnecessary. Separate readiness/dependencies and queued
  selected files without one preparing recording blocking ready files. Preserve actual decode
  validation, duration/timebase checks, policy and immutable inputs.
  **User decision:** every new transcription backend starts from the same immutable original
  recording. Required decoding, compression or splitting starts directly from that original,
  never the browser playback derivative. Freeze original/input identity and conversion evidence;
  preserve old queued/running jobs and checkpoints. Review quality, channel/timebase handling
  and playback synchronization. Implementation is pending; this is now an accepted direction.
  Evaluate uploading a compressed inference source and decoding remotely after readiness and
  checksum checks, preserving a known timebase. Keep browser playback separate. Measure transfer
  time, local CPU, remote CPU/GPU idle cost and format/failure behavior before choosing defaults.
  **Offline evaluation ready:** [media preparation assessment](../remote-media-preparation.md)
  distinguishes the two conversions and the readiness gate. Synthetic ten-minute M4A measured
  11.179 s median browser conversion and 1.452 s extra WAV conversion; WAV 19.2 MB versus
  original 4.94 MB. Remote decoder/timebase validation and live measurements remain pending;
  processing defaults are unchanged.
  **Offline assessment ready:** [compressed-input experiment](../evaluations/remote-media-preparation-2026-10-27.md)
  and a reproducible synthetic script compare exact PCM, sizes, stream selection, container
  timing and truncated input. The existing browser MP3 reproduced current PCM under the pinned
  decoder with about half the bytes in that fixture. Remote performance and speech accuracy
  remain unmeasured. Common original input/readiness separation is accepted; remote decoding,
  transport and conversion details still need implementation and validation.
  [Reviewed implementation brief](../proposals/common-original-input-2026-10-27.md): freeze
  fresh original-v1 inputs and conversion receipts first, then separate durable input readiness
  from browser playback. Preserve legacy durations, follow-ups and checkpoints. Remote compressed
  decoding is a later optimization; the first delivery can retain the existing WAV protocol.

### Follow-up order after the current work — confirmed 27 October

1. **T43 — Version-control redesign, restoration/merging and fuller research provenance.**
   Extend [the design](../version-control-redesign.md) with a migration preserving every
   current result/version, conservative three-way passage merging, explicit conflicts and
   append-only restoration. Capture exact model/runtime/artifact/prompt evidence and make
   reports reproducible. Never silently promote another model's wording over reviewed text.
   **First merge stage implemented:** explicit enhancement-to-source preview and safe timing/
   speaker adoption (ADR 0068), with exact base/creation correspondence, conflict preservation,
   atomic preview guards and verified research-parent references. Working/alternative migration,
   wording suggestions and interactive conflict resolution remain later T43 stages.
   **Import evidence stage implemented:** new JSON/CSV imports bind exact preserved bytes to
   their normalized result before atomic history publication (ADR 0073/V127). Metadata-first
   reports retain source aliases/hash/size through edits, restoration and Trash, without
   backfilling old imports or claiming current file availability, full backups or ASR identity.
   [Verification](../verification-2026-10-27-import-artifacts.md) separates offline evidence
   from real-library/Mac acceptance. Broader working/alternative role choices remain open.
   **Wording adoption implemented offline:** a one-to-one comparison row opens an exact
   preview; explicit Apply copies only literal text, preserving destination bounds/speaker and
   invalidating timing without queuing alignment (ADR 0074/V128). Exact source ancestry is
   verified in reports; earlier text remains recoverable. [Verification](../verification-2026-10-27-wording-adoption.md):
   159 service/related cases, 13 report cases (five overlap), 71 report integration cases and
   19 distinct browser cases across focused runs. This later stage is not in the running c3c94e6
   checkout. Persisted suggestions, role migration and multi-passage conflicts remain open.
2. **T89 — Danish model evaluation and licence review.** Pin candidate revisions/licences;
   evaluate Danish, English and code-switching on licensed public and explicitly approved
   reviewed clips. Report text, timing, speaker, resource/cost coverage and uncertainty.
   CBS licence clearance and real interview acceptance remain external evidence.
   **Research stage complete:** [licence and holdout review](../danish-model-licence-review-2026-10-27.md)
   and [seven pinned evaluation candidates](../../research/model-evaluation-candidates.json).
   Candidates remain unvalidated and pending CBS review; no models were enabled or benchmarked.
   **T89a verified offline:** bind opt-in public evaluation to accepted producer/settings and
   immutable creation versions, preserving exact prediction/export receipts (ADR 0076/V11).
   [Evidence](../verification-2026-10-27-evaluation-producer-receipts.md): 66 distinct passing
   affected cases across broad/final focused checks and independent GO. This corrects
   attribution of measurements; it does not run or clear any model.
3. **T90 — UCloud routing for large passage-repair jobs.** Design an explicit Automatic threshold,
   freeze exact target/version/text hashes and remote compute, and reuse the same-result timing
   merge guards. Preserve local single-passage repair, skip stale targets and test cancellation,
   partial completion, retry/restart and Protected policy. No whole-recording upload by accident.
   Include passage re-transcription in the transfer-scope review: its current implementation
   prepares/uploads the recording WAV and clips the requested bounds inside the remote worker.
   Build explicit bounded clip bundles/time mappings before describing that transfer as clip-only;
   preserve already frozen jobs and never send reviewed wording for re-transcription.
   [Bounded-bundle brief](../proposals/bounded-repair-bundles-2026-10-27.md) records the full
   source-version/selected-projection distinction, sample mappings and required transport proofs.
   It is design only; implementation and public routing remain pending.
   **T90a verified offline:** read-only source/projection inspector (ADR 0077/V132), independent
   of unfinished audio preparation. It proves historical version ownership and exact selected
   input; it does not prove extraction, authorize upload or enable remote repair. [Evidence](../verification-2026-10-28-repair-sources.md):
   112 affected cases, 69 overlapping committed-image cases and independent reviewer GO.
   No remote repair was enabled.
4. **T91 — UCloud backup upload/restore, retention policy and CBS IT/DPO review.** Build on verified
   local snapshots: scoped checksummed upload, manifest verification, restore to a new local
   library, interrupted-transfer recovery and no credentials in backups. Document retention,
   access, encryption, expiry and Trash decisions for CBS IT/DPO; do not imply institutional approval
   or implement destructive retention defaults before those decisions.
   [Portable backup/restore proposal](../proposals/ucloud-backup-restore-2026-10-27.md) records
   package completeness, verified transfers, isolated restoration and pending institutional
   decisions. It is design evidence only; no live backup or restore was performed.
   The [portable format-v2 design](../proposals/portable-backup-format-v2.md) specifies
   complete file manifests, interrupted transfer and isolated restore contract tests;
   implementation and CBS retention approval remain pending.
   **T91a/S1a delivered offline:** internal sealed-byte transport observation
   (ADR 0081/V137), verified with 106 synthetic cases plus two unchanged v1 backup cases;
   four Windows symlink-permission cases skipped, both actual junction cases passed.
   [Evidence](../verification-2026-10-28-portable-transport.md). This is not full S1:
   database/schema, completeness, quarantine, capture, restoration, upload and institutional
   acceptance remain separate pending work. No public restore or v1 behavior changed.
   **T91b0 delivered offline:** pure typed-table evidence and a frozen schema-12 catalog
   (ADR 0083/V139), with 86 unit cases, independent review and isolated wheel/Windows Git
   checkout checks. [Evidence](../verification-2026-10-28-portable-rows.md).
   The separate internal reader is recorded in T91b1 below; public hostile-file admission
   remains closed. Neither codec nor transport observations establish backup/restore eligibility.
   **T91b1 delivered offline:** contained standalone database-claims reader (ADR 0088/V140),
   with 96 new tests, independent actual-child/CHECK probes and wheel execution verified.
   [Evidence](../verification-2026-10-28-portable-database.md). History/mirrors, package
   binding, hostile-file security acceptance, capture, quarantine and public Restore/upload
   remain separate gates; this does not complete full S1/PR02 or T91.
   **T91b2 delivered offline:** sealed transport and contained database observations compose
   from one private capture (ADR 0090/V146), with final inventory/witness checks and no second
   media-body hash pass. 135 distinct passes, four Windows symlink skips, independent review
   and an actual wheel/child check are verified. [Evidence](../verification-2026-10-28-portable-package.md).
   Semantic history/ownership, capture and Restore remain pending; the
   [history-consistency follow-on](../proposals/portable-history-consistency-2026-10-28.md)
   still needs a separately reviewed full semantic acceptance policy.
   **T91b3 delivered offline:** pure supplied-payload canonical envelope/byte observation
   (ADR 0091/V147), with measured lexical ceilings, 103 distinct cases, independent review
   and an isolated wheel/actual committed-codec check. [Evidence](../verification-2026-10-29-portable-payload.md).
   Inner passage meaning, history/heads/mirrors and database/Restore integration remain pending;
   larger valid canonical payloads may be unsupported without being corrupt or modified.

Current reliability, native recovery, dynamic runtime batches (T85), remote media evaluation (T87)
and language handling finish first. This order plans the next work; it does not mark it delivered.

### Existing remaining work — supporting detail

- [ ] **Live acceptance and benchmarks (T19b, T59, T60, T70, T61/T63 calibration).** Verify native
  batches and runtime ASR/alignment/speakers end to end, partial failures, restart/reconnect,
  cancellation and cleanup; compare reviewed Danish/English text, timing, speakers, wall time,
  memory and cost. Public FLEURS alone is insufficient for interview/speaker validation.
- [ ] **Version-control redesign and provenance (T43).** Decide the working-result/alternatives/
  branches-and-merges behavior; add processing-time artifact/environment/model evidence,
  restore/merge workflows and scalable history reports. Existing immutable history and methods
  reports are delivered; journal/researcher acceptance and broader evidence remain.
- [ ] **Danish model evaluation.** Evaluate pinned Edda, Røst, Hviske and relevant Whisper
  baselines on reviewed CBS interviews; verify licensing with CBS and resource/runtime needs
  before making candidates available. Keep the researched candidates separate from installed ones.
- [ ] **Topics and topic-specific sentiment (T38).** Implement/evaluate automatic local/UCloud
  suggestions and topic mapping, with exact text evidence, researcher review, reproducibility
  and Protected-data execution policy. The current manual coding prototype is already delivered.
  Continue under the broader long-term T88 AI analysis/NVivo roadmap below, rather than creating
  a competing analysis workflow.
- [ ] **Large Repair all live acceptance (T90e).** T90f extends prepared clip-only cloud repair
  to older/uninspected recordings using anchored bounded source/absolute timestamp proof.
  Unsupported mappings fail before allocation. Validate real MIG resources and timing quality;
  small/single selections retain local behavior and same-result revision checks remain.
- [ ] **Storage step 2 and retention.** Decide UCloud backup upload/restore/project archives and
  institutional retention/Trash policy with CBS IT/DPO. Verified local backups and collections
  are already built. An optional library move needs its own backup/preservation procedure.
- [ ] **Deployment.** Clean Windows and real Apple Silicon/Intel Mac acceptance, NVivo Windows/
  Mac imports, colleague onboarding, shortcuts/installers/updates and the shared ChatGPT Work
  skill/plugin. Define CBS pilot owners, access, support, approved processing and cost allocation.
- [ ] **Later/deferred.** Custom/vLLM UI and speech-hosting release; provider capacity policies;
  OpenAI passage re-transcription with cost confirmation; PDF/ELAN exports if needed by a
  concrete research workflow. These do not block the priority fixes above.

### Longer term — AI-assisted analysis and NVivo integration (T88)

Requested 26 October 2026. Planned after the current reliability, review and UCloud execution
work; the P0/P1/P2 labels here describe this roadmap's own implementation sequence.
Detailed scope: [AI analysis and NVivo roadmap](../ai-analysis-nvivo-roadmap.md).

**Goal:** transcription → AI-assisted analysis → human review → NVivo export. CBS Transcriber
prepares structured, traceable research material. NVivo retains advanced qualitative research:
thematic interpretation, coding matrices, code co-occurrence, cross-interview comparisons and
research reporting. Do not attempt to replicate NVivo.

- [ ] **T88a / P0 — Interoperability and underlying data model.** Validate actual NVivo imports
  first; establish transcript/source/version identities, hierarchical codebooks, coded evidence,
  summaries/memos and suggestion/review states. Plan REFI-QDA Codebook `.qdc` import/export and
  Project `.qdpx` export. Test code hierarchy, exact coded passages and supported memos on target
  NVivo Windows/Mac versions; document unsupported mappings rather than silently losing content.
- [ ] **T88b / P1 — Shared AI analysis engine.** Integrate UCloud AI Platform and OpenAI API
  through one model-independent analysis interface, separate from ASR and timing enhancement.
  Preserve provider approval/Protected policy and frozen execution identity.
- [ ] **T88b / P1 — Codebooks and coding.** Create, edit and import hierarchical codebooks;
  apply researcher codebooks deductively and suggest new codes inductively. Allow researchers
  to accept, modify or reject AI-generated codes and annotations.
- [ ] **T88b / P1 — Summaries and review.** Generate interview and topic summaries with linked
  evidence; retain original AI suggestions separately from researcher-approved material.
- [ ] **T88c / P2 — Discovery and attitudes.** Discover recurring topics, suggest relevant codes
  and add topic/aspect sentiment and stance analysis. Evaluate Danish/English interview quality.
- [ ] **T88c / P2 — Expanded NVivo exports.** Extend verified `.qdc`/`.qdpx` mappings to the
  supported transcripts, code hierarchies, reviewed coded passages, summaries and memos.
- [ ] **Cross-cutting provenance and acceptance.** Record AI provider/model, exact prompts,
  transcript/codebook versions and human decisions. Every finding must link to exact transcript
  evidence and original audio where timing exists; never invent audio locations. Keep AI and
  approved coding distinguishable. Validate actual NVivo transfers, not just archive/schema shape.

Current status: the existing manual codebook/evidence/review prototype, immutable transcript
history and methods reports are foundations. Shared AI execution, generated summaries,
hierarchical codebooks and REFI-QDA interchange are planned, not delivered. This expands T38
and T43; it does not enable OpenAI Batch API or automatic transcription cleanup.

### Completed baseline and repository status

- [x] Collections, verified local backups, Processing settings, API-only UCloud runtime,
  in-place passage timing, passage re-transcription, saved-result Trash and version history
  are implemented in the 24 October baseline. Real-run issues above remain open.
- [x] Private repository created: https://github.com/CBS-HPC/cbs-transcriber. The tested
  `feature/processing-collections-backups` and existing `main` are pushed. The tested feature
  branch is the repository default; it has not been merged into local main.
- [x] Today's work uses `feature/runtime-batches-and-review-layout`. Two read-only subagent
  reviews covered runtime batching/media preparation and cleanup/Activity. No remote allocations,
  recording uploads, cleanup mutations or running-server restarts were performed for this overview.

## Requested 23 October 2026 — in progress, in this order

- [x] T68: Researcher-set rules for Automatic processing: local minute limits for word timing
  and speaker labels, the local-GPU factor, and what happens when UCloud is unavailable for a
  longer recording (run here anyway, or don't start). Saved per library; named in each reason.
  "Rules for Automatic" in the enhancement dialog (ADR 0047 amendment, V98); tests pass.
- [x] T69: Opt-in automatic realignment after free-text edits. Word timing is added to the same
  result as a new timing-only version (text unchanged, revision-checked, undoable in History),
  run locally on the passage's bounded audio. ADR 0052; "Realign edited passages" in the editor.
- [x] T70: Full WhisperX transcription as models under the UCloud provider (user decision 23 Oct:
  no separate button/provider). "UCloud WhisperX large-v3 / large-v3-turbo (CBS runtime)" run
  faster-whisper + alignment (+ speakers in the same run) on the prepared API-only runtime;
  pinned public weights fetched only during explicit preparation (asked before the first run).
  Keeps model hints; appends a normal provider result. Local engine support exists, no UI.
  ADR 0053, V100; mocked UCloud/browser/worker tests pass.
- [ ] T70 live acceptance: one confirmed preparation (large-v3 download) and a normal run on the
  user's UCloud project; compare against UCloud Transcriber whisper/large-v3 on one interview.
- [x] T73: Investigated UCloud GPU needs (docs/ucloud-mig.md): every WhisperX stage fits one
  1/7 B200 MIG slice (~23 GB; peak ~4.5 GB for large-v3 fp16), billed at 1/7 GPU-hour.
- [ ] T73b (user chose MIG for small enhancements on 26 October; see T86 above): allow-list a MIG product,
  freeze a separate enhancement product, GPU-memory readiness check, let the UCloud lane run
  several one-slice jobs at once; confirm exact MIG product IDs read-only; live test on one MIG.
- [x] T72: Step-by-step install guide for Windows and macOS in README and AGENTS.md:
  install uv -> Python 3.13 via uv -> get the app -> uv sync --locked -> launch/first run.
- [ ] T72 follow-up: try the guide on a clean Windows account and a real Mac (Apple Silicon and
  Intel) and fix any step that differs; decide where colleagues get the app folder (Git/ZIP).
- [x] T71: Re-transcribe selected passages (docs/passage-retranscription.md) with WhisperX
  (local, or UCloud for many/long passages). Decisions taken: WhisperX first (no paid cost);
  OpenAI clips later with a cost confirmation; surrounding text is not sent.
  ADR 0054, V101: passage Re-transcribe and Re-transcribe flagged; tests pass (mocked UCloud).
- [ ] T71 later: OpenAI passage clips with a per-request cost confirmation (not started).

## Requested 23 October 2026 (afternoon)

- [x] Missing word timing becomes an "Add word timing" heading tag like "Review suggested"
  (reason in its tooltip); passage "Delete passage" button reads "Delete".
- [x] Removed the "Listen closely. Work at your own pace." and "YOUR RESEARCH, IN FOCUS" texts.
- [x] "+ Create a project" uses the same font size (14px) as the sidebar's Provider Settings.
- [x] Provider Settings: "Forget saved key" under the key box, right-aligned on the OpenAI and
  UCloud tabs (fixed after the layout script moved it into Advanced, B124); Hugging Face tab has
  "Forget saved token" in the same place. Browser test covers all three tabs.
- [x] UCloud WhisperX live run failed (exit code 1 before any supervisor status; the Transcriber
  app's own program ends without an input). CBS runtime now runs as a Terminal app batchScript
  job (ADR 0055, B123); mocked tests pass. Live acceptance pending.
- [x] Speaker labels for UCloud WhisperX run on UCloud only: with a saved HF token and explicit
  preparation consent the weights are fetched here once and copied; no local speaker run.
- [x] B125: speaker runs now check Hugging Face access to the gated pyannote model before any
  GPU job; the user's token is valid but the model's terms are not yet accepted.
- [x] User accepted the pyannote terms; Check token now reports access allowed.
- [x] B126 / T75: the next speaker run failed while downloading (a locked file on Windows; the
  cache kept 6 of 7 files). Speaker preparation is now part of Hugging Face Check token (token,
  terms, download with retries, file hashes) and runs before any UCloud GPU request (ADR 0057).
  The model is now downloaded and verified on the user's computer.
- [x] B127: the third speaker run (still on the old code) failed copying the model to UCloud;
  the old copy wrote the completion marker first, so UCloud now holds a marked but incomplete
  copy. The runtime copy is now checked file by file and repaired (missing files only, retried)
  before any GPU request (ADR 0057).
- [ ] User: start a new UCloud WhisperX run with speaker labels after the app restart (live
  acceptance of ADR 0055/0057); it will first re-copy the two missing speaker files.
- [x] T76: one "Add word timing" tag for every untimed passage (no "No word timing" tag) and no
  yellow highlighting (ADR 0058). Passages without their own start/end are searched only between
  their timed neighbours (<=60 s), together with adjacent untimed passages.
- [x] T74: "Add word timing" works per passage and in place (ADR 0056): only that passage is
  aligned, other passages can be started meanwhile, each tag shows its own state, timing joins
  the same result as a version (no "Review repair" result). The banner is one tag, "Repair all N
  of M missing word timings" (one job, one version). Passages without their own start/end (e.g.
  untimed fragments from alignment) show a static "No word timing" tag with the reason.
- [x] The edit-mode hint ("Edit a passage freely...") is now the Edit mode menu's tooltip.
- [x] "Re-transcribe flagged" is now a tag beside "Repair all…" above the passages:
  "Re-transcribe all N of M flagged passages" (flagged passages with a known interval of at
  most 60 s, up to 200 per request). Timed words say "Click to play" instead of "Click to seek".
- [x] Independent review of ADR 0056–0058 and Clear finished: 5 findings fixed (B128).
- [x] T77 (ADR 0059): Provider Settings → Processing (language, Automatic device using a GPU
  when found, standing model-download permission, Python runtime, this computer's and UCloud's
  status checked by themselves, Rules for Automatic); startup GPU check; UCloud preparation asked
  once per job instead of a checkbox; Re-transcribe on a passage starts at once; "Add speaker
  labels" tag; no Add word timing / speakers button on the recording page; transcribe row moved
  below the result controls; Export/Research report/Topics moved to the sidebar; Versions… merges
  History and Manage results; File Settings (download original, import, delete) in a recording.
- [ ] Version control redesign still awaits the user's answers (docs/version-control-redesign.md).

### 24 October 2026 (continued)

- [x] T78 Collections (ADR 0060, schema 11) with the proposal's recommended options: one
  collection per recording, one level, notes, exports/report follow the shown collection;
  chips, Move to, uploads into the shown collection, Collection Settings in the sidebar.
- [x] T79 UCloud storage step 1 (ADR 0061): Library → Back up library now (verified snapshot
  plus hashed originals manifest). Next: upload a backup to a chosen UCloud drive behind its own
  approval, and restore into a new library; CBS IT/DPO questions still open.
- [x] B129: follow-ups on an unprepared UCloud runtime now ask once; legacy SSH jobs fixed.
- [x] B130: independent review of collections, backups and processing settings: 9 findings fixed.
- [x] Danish ASR research (docs/danish-asr-models.md): figures confirmed from model cards; newer
  candidates Edda v0.2 (Apache-2.0, Whisper fine-tune) and Hviske v6 (CC BY-NC); all fit a
  MIG slice; licences need CBS review. Proposed order: Edda large/turbo and Røst v3 as pinned
  faster-whisper models first, a Hviske engine only after licence clearance, evaluation on
  reviewed CBS clips with scripts/evaluate_reviewed.py.
- [x] UCloud storage step 2 planned (docs/ucloud-storage-investigation.md); not built: it waits
  for the user's decision and the CBS IT/DPO answers.

### Checkpoint 24 October 2026

- [x] Older browser tests adapted to ADR 0059. Final verification 24 Oct (after collections,
  backups and the B130/B131 fixes): 793 unit/API and 105 Chromium tests pass; Ruff and
  Prettier clean. The app was restarted on the final code; the library was unchanged.
- [x] App started on the new code (the old one had been closed). Library backed up first
  (pre-restart copy plus the app's own schema-10 backup); migrated to schema 11 with every earlier
  row unchanged (fingerprint compare); startup check: WhisperX 3.8.6 ready, no CUDA GPU, saved
  device CPU kept.
- [ ] User: one UCloud WhisperX run with speaker labels (it first re-copies the two missing
  speaker files to UCloud, B127).
- [ ] Follow-up planning is authorized in the order above. Version-control details, evaluated
  model choices and institutional storage/retention decisions stay explicit in their designs;
  they do not block independent offline work. Collections are implemented; MIG is authorized
  for scoped runtime work in T86.
- [x] Activity: **Clear finished** hides finished jobs that need no action (nothing is deleted; V105)
- [ ] Decide: should a large "Repair all" follow the Automatic rule (UCloud above the local
  word-timing limit)? Needs UCloud passage alignment; today repairs stay local (ADR 0047).
- [x] Decided by the user (one tag for all): untimed fragments are searched inside the gap
  between their neighbours (ADR 0058).
- [ ] Clean up the failed run folders on UCloud (no recordings uploaded) after the user approves.
- [x] Collections inside projects implemented with the recommended options (schema 11, T78);
  the earlier proposal is retained at docs/collections-proposal.md.
- [x] Investigated UCloud as secure storage (docs/ucloud-storage-investigation.md): keep the live
  library local (SQLite must not live on synced/network folders); store verified snapshots on
  UCloud. Questions for CBS IT/DPO listed there.
- [x] Local "Back up library" implemented with a verified snapshot and hashed originals manifest
  (T79, ADR 0061). Upload to UCloud and restore/project archives remain separate pending work above.
- [x] Recording page layout: transcribe controls on one left-aligned row under the title; Review
  result + Manage results + History; Compare with; then Add word timing/speakers, Export
  Settings and Topics & sentiment (user-approved proposal).
- [x] Project Settings note uses the small muted note style again.

- [ ] Version control redesign (user request): one working transcript per recording with a
  linear version history; machine timing/speakers merged per passage as versions; model
  re-transcriptions as accept/reject suggestions; other model runs as alternatives; one tabbed
  Versions dialog (History, Alternatives, Trash). Proposal: docs/version-control-redesign.md;
  awaiting the user's review and three questions before an ADR and implementation.
  The user suggested Git-style branches and merges (23 Oct): added to the proposal as a
  three-way passage merge with the job's frozen version as the common base.

## Roadmap: Danish speech models (requested 23 October 2026)

- [ ] Evaluate Danish-specific ASR for the many Danish/English interviews, primarily on UCloud:
  - syvai/hviske-v5.3 (https://huggingface.co/syvai/hviske-v5.3): reported 2B-parameter
    Conformer encoder-decoder, not Whisper, so not a drop-in WhisperX model. It would be a
    separate transcription engine, followed by our WhisperX alignment and speakers.
  - Røst Whisper 1.5B v2 (Whisper-based; may fit WhisperX/faster-whisper after conversion).
  - Reported CoRal v3 CER (from the user's notes, not yet verified): conversation 11.35 /
    11.6 / 27.5 % and read-aloud 3.63 / 4.5 / 10.1 % for Hviske v5.3 / Røst v2 / Whisper
    large-v3.
  - Before adoption: verify licence and terms, pin revisions and hashes, check GPU memory (a
    MIG slice?), runtime dependencies, and timestamp/word output; score both against
    large-v3 with scripts/evaluate_reviewed.py on reviewed Danish clips and a small public
    Danish set. Code-switched Danish/English needs its own check. Add as UCloud catalog models
    like ADR 0053.

## Open threads from 22–23 October (not lost, not started)

- [x] Changes since d6325bc committed as e2a6d4e on feature/processing-collections-backups
  (24 October) and pushed to the private CBS-HPC repository (26 October).
- [ ] Topics & sentiment as a real local/UCloud analysis workflow (today: manual coding only).
  Proposed as its own session on 22 Oct; never started. Reuses the T60 runtime design.
- [ ] Enhancement batching: one UCloud allocation for many recordings' word timing/speakers
  (today one allocation per recording; transcription batches already share one, T59).
- [ ] Live acceptance is in progress: the user has run native and CBS Runtime jobs. Reproduce
  and fix the reported native batch import/cleanup issues, complete runtime acceptance, and
  verify the three older cleanup obligations. Permanent Trash emptying requires separate approval.
- [ ] Sensitive-data audit of UCloud API transfer with CBS IT/DPO (see below).

## UCloud workflow and editing follow-up — 22 October 2026

- [x] T57: Provider Settings shows only OpenAI/UCloud; defer Custom/vLLM from new UI choices,
  retain old configurations/results/frozen jobs, hide built-in Configuration name and move
  Available models outside Advanced. Simplify the native display name to UCloud.
- [x] Hugging Face tab in Provider Settings saves/checks the shared read token (metadata only).
  Enhancement shows token status: word timing needs none; speaker downloads need token + terms.
- [x] T58: Delete a passage from its editor, preserving exact earlier History and all other
  passage text/word intervals. Permit an empty final saved result; guard pending/delayed actions.
  In-page passage/comparison actions no longer ask to discard independent notes drafts.
- [x] T59: Multiple selected recordings with identical native model/connection settings share
  one UCloud Batch job/input directory. Keep per-recording results/retries, durable shared
  ownership, uncertain-submission recovery, cancellation and validated cleanup (ADR 0045).
- [ ] T59 live acceptance: run one real two-file Batch job and confirm per-input output naming.
- [ ] Sensitive-data audit for UCloud API transfer (with CBS IT/DPO): data-class approval and
  agreements for UCloud, TLS-only transfer path vs SSH (SSH adds no transport protection), data at
  rest and retention (Trash keeps run files until emptied; consider a dedicated app drive whose
  Trash the app may empty), project/drive access, token scope/lifetime and audit logging.
- [x] Remote cleanup moves the owned run folder to UCloud Trash and confirms by listing the parent;
  DELETE is unimplemented by UCloud storage and missing paths return 400 (ADR 0045, B112).
  Activity shows the failure reason. Existing pending runs can use Retry remote cleanup.
- [x] Compare with sits on its own row below Review result. Recording rename uses the heading
  pencil like projects. Project classification/storage save on change (folder moves confirm).
- [x] T66: Library and new projects default to one visible folder in the home directory on
  Windows and macOS: `CBS Transcriber/Library` and `CBS Transcriber/Projects` (ADR 0050). Existing
  libraries at the earlier hidden default are used in place, never moved automatically.
- [x] T67: Without `--data-dir` the launcher opens the last library, or asks when several are
  found (including Codex-redirected app data); **Library** in the sidebar switches libraries
  by restarting the server (ADR 0051). Mocked/real-launcher browser test passes.
- [ ] Optional: move the Codex-redirected working library to `<home>/CBS Transcriber/Library`
  with a backup and preservation check, after user approval (no longer needed to open it).
- [x] Independent review of the 22–23 Oct changes: 10 findings fixed with regressions (B122).
  Verification 23 Oct: 727 unit/API and 94 Chromium tests pass; Ruff and Prettier clean. The
  running library was activated to schema 10 with a backup; all prior tables byte-identical.
- [x] T65: UCloud enhancement runs in its own worker lane, so local enhancement and passage
  repairs are not blocked behind long remote polls (ADR 0049, V95).
- [x] T64: Enhancements whose source was edited after queueing say so in Activity, the
  "Enhancement saved separately" note and the derived result's editor note (V94); no rebasing.
- [x] 23 Oct UI requests (all done): storage field narrower; Classification only as wide as its
  options; Add recordings beside the search box; provider/model/features/Transcribe directly
  after "0 selected"; classification saves confirmed with a message (an old browser tab caused
  "cannot change"); pressed/busy feedback; Windows folder window in front; dropdowns verified in
  a normal browser (the Claude preview pane cannot open native dropdown menus).
- [x] Project Settings (sidebar, project pages): one dialog holds Classification, Storage folder
  and Delete Project, so the project page shows only title, notes and recordings (V93).
- [x] 23 Oct layout: Delete Project moved to the sidebar above Library (styled like it);
  Add recordings beside the search box; narrow Classification/Storage fields; classification
  saved toast; pressed/busy feedback; Windows folder chooser in front (top-most owner).
  Explicit --data-dir launches no longer change the remembered library (ADR 0051).
- [x] Storage folder sits on its own row below Classification. Project deletion moved from the
  heading to a quiet "Delete project…" action below the notes; it requires typing the name (V93).
- [x] T60: UCloud word timing/speaker enhancement reuses the single Provider Settings connection,
  using API-only execution/file transfer and no new SSH configuration. Retain old frozen SSH
  recovery. Verify prepared runtime/cache and preserve reviewed text; no implicit model downloads.
  Runtime: an explicit one-time "prepare UCloud runtime" job builds a versioned, locked venv
  (uv-managed standalone Python) plus model cache on the project drive; runs mount it read-only,
  offline, after a version-stamp check. Shared with the planned topic/sentiment worker. Probe
  Transcriber 1.12 Batch `initScript` ordering with one approved live allocation first.
  Implemented with mocked API tests (ADR 0046); SSH remains only for already frozen jobs.
- [x] T61: Automatic processing (default): short recordings run locally (10 min words,
  3 min speakers, x6 with local CUDA); longer ones on UCloud when approved and prepared,
  otherwise locally with a reason shown in Activity (ADR 0047). Passage repairs stay local.
- [ ] T61 calibration: replace the routing thresholds with measured local CPU/GPU and UCloud
  timings from representative interviews (T19).
- [x] T62: Unnamed speakers display/export as Speaker_01, Speaker_02, ... (Transcriber style)
  from the raw label; speaker lists show only the reviewed result's voices (V88).
- [x] T63: Show uncertainty: passages/words with the lowest model confidence (relative, with
  ceilings) are flagged/underlined with reasons; Next to review; edits remove marks (ADR 0048).
- [ ] T63 calibration: compare flags with reviewed corrections (T19) and tune share/ceilings.
- [ ] T60 live acceptance: one approved runtime-preparation job, then a normal run; confirm
  initScript ordering, GPU visibility, network during preparation, drive flock and outputs.
- [x] Clarify local/UCloud HF access: public alignment models normally require no token;
  gated pyannote downloads require terms/read token, cached pipelines can infer offline.

## Recoverable result removal — 22 October 2026

- [x] Add revision-checked per-recording Trash/Restore, retaining exact results, research history,
  frozen jobs, raw sources and originals. Active removal switches to the newest remaining result;
  last removal clears the transcript view. Restore activates only when none is selected.
- [x] Exclude Trash from live selection/counts/comparison/exports and new processing inputs;
  retain historical reads, frozen retries, existing topic evidence and labelled report ancestry.
- [x] Full regression: 613 unit/API tests pass. Fresh 82 lifecycle/history/export cases and 22
  storage/job/replay/API cases pass after the snapshot fixes, including two new cross-connection
  race regressions. Ruff and formatting pass. Schema-7 backup/migration uses synthetic libraries.
- [x] Six Chromium workflows pass: full lifecycle/comparison/last result, original download/media,
  notes/drafts, conflict and committed-view-error recovery, late navigation replies, pending close
  and held result switching. Prettier, JS syntax and diff checks pass; modal visually checked.
- [x] Safely activate schema 8: done 22 October 2026 together with schema 9 (7 -> 9) after an
  idle backup; all 12 existing tables matched their pre-restart fingerprints. Schema 10 (review
  hints) activates at the next idle restart with the same procedure.

## Project name heading — 22 October 2026

- [x] Replace the inline Name field with an accessible pencil button beside the heading, opening
  the existing name dialog. Rename saves independently and retains classification/folder/notes
  drafts. Prevent concurrent settings saves and ignore replies belonging to another project view.
- [x] Four Chromium workflows pass: rename/cancel/failure/reopen, delayed replies, notes independence
  and Protected-project storage moves. Visually checked the heading; Ruff, formatting, Prettier
  and JS syntax pass. Static changes are served by the running main app.

## Project toolbar — 22 October 2026

- [x] Group recording search, Export Settings, Research report and Topics/sentiment on the left
  with consistent spacing and wrapping. Chromium layout checks and visual review pass at
  1920, 1440 and 900 pixels; Prettier passes. Updated CSS is served by the running main app.

## Word timing through passage changes — 22 October 2026

- [x] Retain corrected and partially timed word maps through merge/split, recompute completeness
  for each half and keep replay highlighting. Preserve the free-rewrite and legacy stale-map guard.
- [x] Seven Chromium structure/replay workflows pass, including unbounded parents, both comparison panes, restoration
  and persistence. A parallel read-only review verified five repeated merge/split cycles.
- [x] Broad regression: 603 unit/API cases passed; its one newly added legacy fixture had already
  been corrected after collection. Both corrected regression cases pass in a fresh focused run.
  Ruff/format, Prettier, JS syntax and diff checks pass. See B105 for the fixture correction.
- [x] Fresh focused verification: all 88 passage/transcript/report cases pass. Unbounded passages
  follow actual word windows without filling unknown gaps; fully located halves recover real bounds.
- [x] Activated the final backend on 8766 and revalidated two fully located passages in the affected
  public test result. Idle shutdown, SQLite backup and exact result/revision/history binding;
  one explained History version appended. Every character, word object and interval is unchanged.
  Nine unrelated tables match before/after repair; all 12 table fingerprints match the corrected
  baseline across restart. All 11 providers and credential flags are preserved; no job was started.

## Connection setup and model visibility — 22 October 2026

- [x] Remove visible model dropdowns from the OpenAI and native UCloud Transcriber setup tabs.
  Keep model selection in transcription controls; Advanced settings lists model enable checkboxes.
- [x] Move OpenAI Check model availability and its results into the basic connection area.
- [x] Verified 602 unique unit/API cases across the full regression and focused reruns, including
  20 model-enable/frozen-job cases. Updated the obsolete last-provider fixture for catalog expansion.
  All 49 focused Chromium workflows pass, including all-disabled/re-enable, both simplified tabs,
  shared credentials, Protected filtering, notes lifecycle and existing review/transcription flows.
  Ruff/format, Prettier, JS syntax and diff checks pass. Screenshots visually checked.
- [x] Backed up and restarted idle main on 8766. All 12 table fingerprints, 11 provider
  rows, defaults and credential flags are unchanged; no profiles were added on activation.
  Current HTML/JS/CSS and the enabled-model API are served. No paid request, live allocation
  or recording upload was started during verification.

## Project transcription controls — 22 October 2026

- [x] Remove individual recording-card Transcribe buttons and the separate Batch transcription
  button. Remove the cards' Provider, Model and timing/speaker choices too; retain notes and
  saved-result counts. Transcribe selected handles
  one or several recordings, opening Local/UCloud settings when added features are requested.
- [x] Add editable Description / notes to recording cards, using the existing shared recording
  notes API/revision/history; the same notes remain editable inside recording review. Preserve
  drafts across list filtering/provider changes and guard late replies during navigation.
- [x] Notes/card/selection/comparison/unattended flows verified in the browser suite above.
  Four delayed-note tests cover frozen revisions, external saves, detached inputs, recovery and
  a single confirmed discard without reviving the discarded draft.
- [x] Complete card-control removal verified with 12 focused Chromium cases, including bulk
  and recording-review model/features, comparison and shared notes. Visually checked the
  compact cards; Ruff/format, Prettier and JS syntax pass. Updated assets are served on 8766.

## Model selection and manual passage structure — 22 October 2026

- [x] Split transcription Provider/Model choices in project bulk controls and recording review,
  retaining saved IDs and Protected-provider filtering. Card controls were removed following
  the subsequent clarification; cards show shared Description / notes instead.
- [x] Saving a complete native B200 connection adds all catalog models with shared credentials;
  offline startup fills missing profiles without changing existing settings/choices or frozen jobs.
- [x] Add Merge up, Merge down and Split at cursor in Free text. Different speakers require
  an explicit choice. Complete word timing supplies split boundaries; otherwise use an explicit
  playback position within the source passage. Untimed text can split without invented timing.
- [x] Preserve exact earlier versions and untouched passages; append structural history actions.
- [x] Verification: 582 unit/API tests and 40 focused Chromium workflows pass (35 provider/review,
  two model-selector and three structure workflows). Ruff/format, Prettier, JS syntax and diff checks pass.
  Added regression coverage for delayed merge replies, exact history and Unicode boundaries.
- [x] Backed up and restarted the idle main app on 8766. All 11 non-provider table fingerprints,
  six existing provider rows, defaults and credential flags are unchanged. Startup added only
  five missing native model profiles. Current JS and both structural API routes are served;
  no live transcription, paid call, remote allocation or upload was started.

## Settings simplification — 22 October 2026

- [x] Treat native Transcriber compute as fixed gpu-nvidia-b200-1-gpu, superseding the
  preference/alternative behavior below. No compute chooser or fallback; unavailable/ambiguous
  allocation blocks setup. Enforce new-job identity and reject template mismatch before upload.
  Preserve frozen legacy runs; reconnect old configurations for new jobs. See ADR 0039.
- [x] Remove Saved provider/Add/Delete from OpenAI and native Transcriber. Use model selection
  to load each saved model or prepare a separate configuration, retaining same-endpoint draft
  keys and unknown saved OpenAI models. Keep Custom/vLLM configuration controls.
- [x] Change the untimed fallback label to Replay; retain whole-recording disclosure in its tooltip.
- [x] Targeted native/API/worker checks and 24 Chromium workflows pass, including two extended
  model-switch checks. Visually checked both simplified tabs. Ruff/format, Prettier and JS syntax pass.
- [x] Back up and activate the idle running main app on 8766. All 12 table fingerprints and
  saved-key flags match; the updated frontend is served. No live job or upload was started.
- [x] Final regression: 514 unit/API tests pass (60 opt-in browser cases deselected). The
  separately verified 24 browser workflows include the corrected long-audio cleanup; the
  reference export regression now deliberately exercises concurrent out-of-order completion.

## Native UCloud setup corrections — 22 October 2026

- [x] Keep provider errors inside the settings dialog, focus the alert, preserve drafts and
  ignore stale failed replies. Clear obsolete discovery progress after failure.
- [x] Retain safe HTTP status and connection/timeout categories through released SDK wrappers;
  keep raw upstream bodies/tokens private. Actual live project-listing cause awaits another attempt.
- [x] Keep Replay available for an untimed fragment through explicitly labelled Replay recording
  when no exact passage/source interval exists. Preserve all saved timing/text; see ADR 0037.
- [x] Verification: 505 unit/API tests and 20 focused Chromium workflows pass; Ruff lint/format,
  Prettier and JS syntax pass. Visually checked the inline dialog alert with synthetic errors.
  Backed up/restarted idle main on 8766; all 12 table fingerprints and credential flags match.
- [x] Default native discovery to gpu-nvidia-b200-1-gpu when an exact unique compatible,
  funded allocation exists. Preserve explicit/template selections. Explain absence and require
  an explicit alternative; retain named choices for duplicate provider/category identities.
  User reports read-only discovery now reaches compute selection; no live job acceptance yet.
  All 85 native discovery/worker regressions and Ruff lint/format pass.
- [x] Remove Custom / existing model from native Transcriber; use supported catalog choices only.
  Keep old saved settings/frozen jobs intact; require a preset before saving an unlisted model.
- [x] Expand available UCloud application-group flavors so an interactive default cannot hide
  its Batch sibling. Preserve exact pinned application/version selection and validate group replies.
  Source: SDU-eScience/UCloud core2/pkg/orchestrator/app_catalog_rpc.go, AppsSearch handler.
- [x] Label ambiguous applications Transcriber application/version; show a discovery failure
  without an unusable empty dropdown. No upload, allocation or credential changes during discovery.
- [x] Verification: 478 unit/API tests and 14 focused Chromium workflows pass; Ruff lint/format,
  Prettier and JavaScript syntax pass. Restarted the idle main app on 8766 after a SQLite backup;
  all 12 table fingerprints and five providers' credential flags are unchanged. Served JS matches.
- [x] Correct the application-group route to retrieveGroups, verified against the pinned UCloud
  frontend/RPC contract; identify static discovery stages in errors without raw upstream bodies.
- [x] Prefer the native token's eligible project through an exact unique server metadata record.
  Preserve explicit saved choices; absent/personal/unsupported/forbidden metadata retains named
  choices. Reject malformed/duplicate records and authentication/outage failures. See ADR 0035.
  Ownership is an association, not a claim of token permission scope.
- [x] Verification: full regression run 489 unit/API tests; 42 focused discovery checks include
  two additional pagination regressions; all four native-settings Chromium workflows pass.
  Ruff lint/format pass. Backed up and restarted the idle app; all 12 library table fingerprints
  and provider credential flags match. No refresh is needed for this backend-only fix.
- [ ] Verify the corrected setup and token ownership detection against the user's UCloud catalog.
  No live job is needed for this check; execution acceptance remains separate.
- [x] Capture the actual Transcriber 1.12 Batch schema with a temporary metadata-only probe:
  WEB capability, Batch flavour, camel-case fields and boolean jupyterLab. Preserve the public
  schema subset as a regression fixture; synthetic enum/default/tool fields are labelled separately.
- [x] Recognise that verified unattended contract in both discovery and workers; resolve known
  field aliases, force JupyterLab off and preserve source templates. GUI/unknown WEB variants and
  extra initialization/input files remain excluded. Focused native checks pass; see ADR 0036.
- [x] Remove the temporary probe and restart the normal idle app after backup. All 12 table
  fingerprints and credential flags match. The user can retry Connect without refreshing.
- [x] Batch-contract verification: 501 unit/API tests, 78 focused native checks and four native
  settings Chromium workflows pass; Ruff lint/format pass. Mocked execution covers forced
  noninteractive mode, immutable templates and rejection before upload. Live job acceptance
  remains separate; the capture itself made only read-only metadata requests.

## Sidebar cleanup and main activation — 22 October 2026

- [x] Remove the duplicate plus button beside YOUR PROJECTS; retain Create a project below.
  Three existing Chromium workflows verify creation, restart, policy and enhancement navigation;
  Ruff/format, Prettier and JS syntax pass. Remove its obsolete event binding as well.
- [x] Fast-forward main to 9fe54d4, back up the idle library, sync locked dependencies,
  upgrade schema 5 → 7 and reload the existing app tab at 8766. All six original table
  fingerprints and five providers' credential flags are unchanged: 3 projects, 6 recordings,
  11 results and 18 jobs preserved. Added 11 observed result versions and 9 notes versions.
  Served HTML/JS/CSS match main with no-store headers; live sidebar has one Create a project
  action and the new research/prototype controls. No paid calls or real UCloud jobs started.

## Research branch — 21 October 2026

- [x] T43 first delivery: backed-up additive schema-6 migration; immutable transcript and notes
  versions; exact frozen enhancement lineage and atomic/idempotent follow-ups; scoped paginated
  history, integrity-checked historical reads/exports, read-only History dialog and metadata-first
  research report with explicit selected-text inclusion. See docs/research-history.md / ADR 0031.
- [x] Verification: 429 unit/API tests and 37 Chromium workflows pass (466 total), plus final
  focused report/history checks; Ruff/format, Prettier, JS syntax and copy-mode wheel build pass.
  Independent review regressions cover hash corruption, UCloud identity, comparable timing counts,
  private-content exclusions, byte-budget checks and concurrent report capture. All synthetic.
- [x] T44 Provider Settings: short Basic setup, one Advanced disclosure, capability/Protected
  summaries, offline vLLM defaults and preserved custom settings/credentials. Delayed save/forget
  replies cannot replace newer drafts/model selections. All 47 browser workflows pass; backend
  remains the same verified 429-test suite (476 total). Ruff/format, Prettier and JS checks pass.
- [x] T38b local Topics & sentiment prototype under ADR 0033: approved/versioned codebooks,
  frozen transcript evidence, manual coding, strict imported suggestions, immutable originals,
  append-only reviews, safe retries, pagination and reviewed JSON export. Six Chromium workflows
  pass, including Unicode/repeated quotes, stale sources, draft isolation and post-commit retry.
  Automatic model execution and validated research accuracy remain outside the prototype.
- [x] Final branch verification: 474 unit/API tests plus 53 Chromium workflows pass (527 total).
  Ruff lint/format, Prettier, JS syntax, source/wheel builds and packaged-source comparisons pass.
  Independent review found no remaining blocker. Real Windows reader regression fixes unchanged
  frozen-request replacement; deterministic cancellation tests correct a false-success stub.
  See ADR 0034, SPEC B77–B82 and docs/overnight-handover-2026-10-22.md.
- [x] Restore-as-new-version in History (V92).
- [ ] Full processing-time artifact/environment capture, wider metadata
  history, large-history report scaling and journal/researcher acceptance remain later slices.
  Overnight work left main unchanged; authorized activation completed on 22 October above.

## Enhancement replay and transcription controls — 21 October 2026

- [x] Retain actual sentence replay bounds after partial word alignment; highlight individually
  reliable words in both panes, with incomplete warnings and free-text invalidation/restoration.
- [x] Recover read-only source-passage replay for older split repairs from exact frozen job
  evidence; preserve every stored result, word interval and export. No guessed fragment timing.
- [x] Add a direct Review enhanced result action while keeping reviewed sources selected.
- [x] Notice completed enhancements after an open edit draft closes, preserving drafts and
  source selection even when no further job events arrive.
- [x] Add word timing / speaker labels beside individual and Transcribe selected controls;
  model capabilities checked/locked, missing features use the existing Local/UCloud follow-up.
- [x] Merged playback/control fix a14a5eb into main and restarted the idle app after backup.
  All six tables and credential-presence flags were unchanged across restart. Live checks recover
  four source-passage replay actions and bounded playback on the existing repair. Selecting the
  already-saved Danish sample enhancement exposes 22 timed words and verified playback highlighting;
  its one unlocated word stays untimed. No inference rerun or existing result rewrite was needed.
- [x] Verification: 379 unit/API tests plus 33 Chromium workflows pass (412 total), with
  Ruff/format, Prettier, JavaScript syntax and wheel build passing. Synthetic inference only;
  read-only timing inspection confirms four legacy replay windows recover without stored changes.

## Main activation and overnight handover — 21 October 2026

- [x] Fast-forward main to c660caa, back up the idle library, sync locked dependencies,
  restart on 8766 and reload the user's tab. All six database tables and credential-presence
  flags match the backup; schema stays 5. Served frontend matches main with no-store headers.
  Live read-only settings show five OpenAI and six native Transcriber presets, token-first
  UCloud setup and Advanced settings. No provider settings, transcripts or recordings changed.
- [x] Overnight stages completed: T43 first provenance/history delivery → T44 Provider Settings →
  T38 manual evidence/review prototype, only in feature/overnight-research, preserving running main.
  Bounded plan/acceptance: docs/overnight-plan-2026-10-21.md. No live jobs, paid calls or private data.

## Provider setup — requested 21 October 2026

- [x] T46: Shared editable JSON model catalog for OpenAI and all documented native UCloud
  Transcriber models. Preserve custom saved models and current UI labels; restart to reload.
- [x] T47: Token-first native Transcriber Batch setup using the released UCloud API package
  as a dependency. Discover eligible project/storage/application/compute; named choices only
  where ambiguous, advanced overrides available, no setup allocation or upload.
- [x] T48: Delete only app-owned remote input/output folders by default for new configurations
  after successful download, validation and local save. Preserve failures for recovery, expose
  incomplete cleanup and support cleanup-only retry. Do not purge shared Trash or old jobs.
- [x] T49: Native macOS folder chooser and Intel core dependency split. Locked dry-run install
  plans pass for Intel and Apple Silicon/macOS 14+. Windows keeps standard local inference;
  Intel reports its pinned WhisperX limitation. CI declares Windows plus both Mac architectures.
- [x] Verification: 393 tests passed, including 24 Chromium workflows; Ruff/format, Prettier,
  JS syntax, wheel build and packaged catalog inspection pass. Cleanup review regressions cover
  commit/crash order, disk flush failure, ownership conflicts and cleanup-only idempotency.
  All execution tests use temporary data and mocked services; no live UCloud/paid calls.
- [ ] External acceptance: real native Batch job and permanent-delete capability; real Mac
  clean install, Keychain, playback, chooser and cancellation. CI Mac jobs are declared but not
  executed here. Provider setup was merged and activated on main; external acceptance remains open.
- [ ] (Deferred with UCloud vLLM, T57) T44 clarification: UCloud vLLM basic setup should show only
  UCloud API and HF token; supply defaults for remaining settings under Advanced settings.

## Research roadmap — requested 21 October 2026

- [x] Record the clarified service objective: a CBS-supported transcription service for interview
  research, with usability, research quality, operating cost and support effort as success criteria.
  See docs/cbs-deployment-proposal.md; current runtime remains local and single-user.
- [ ] Define the CBS service pilot with LIBAS/CBS IT: eligible researchers/students, service and
  support owners, approved processing/data lifecycle, representative Danish/English interview
  acceptance, onboarding time and total cost per audio hour/annual service cost. Confirm UCloud
  credit availability and charging responsibility. Select delivery/access model before adding
  shared hosting, SSO or multi-user administration; benchmark against the service being replaced.

- [ ] CBS rollout discussion: versioned installer/launcher, updates and support; shared
  ChatGPT Work plugin with install/launch/use/troubleshooting skills and optional local
  tools. Pilot on managed CBS Windows devices, agree IT installation permissions and workspace
  distribution. A skill guides workflow, not grants local execution rights. Keep recording
  access and provider credentials under each user's explicit permissions.
  Sources: https://learn.chatgpt.com/docs/plugins and https://learn.chatgpt.com/docs/get-started-with-work.
  Include desktop and Start menu shortcuts: one click starts the app and opens its browser,
  or focuses/reopens the existing local instance, without requiring terminal commands.
  Support Windows and macOS installation, launch, storage/keychain and media playback;
  platform-specific shortcuts (Start menu/Desktop; Applications/Dock). Review locked dependency
  compatibility and run real-device acceptance on both platforms before claiming full support.

- [x] T43 design: immutable result/notes versions, frozen lineage, legacy observed baselines,
  privacy-aware methods/evidence report and migration/regression plan.
  See docs/research-provenance-design.md. No schema migration or history implementation yet.
- [x] T43 implementation: Research version control for transcript edits, speaker changes, notes and
  processing steps (schema 6 history, ADR 0031; full action vocabulary in research-history.md).
  Remaining slices are listed under "Research branch" above.
- [ ] Record reproducible processing provenance: audio hashes, app/environment/model revisions,
  provider settings without secrets, chunk offsets, alignment/diarization settings, raw responses,
  manual corrections and quality checks. Flag missing/opaque provenance explicitly.
- [ ] Export a human-reviewable workflow description and machine-readable evidence manifest,
  with versioned exports/checksums and privacy-aware inclusion of research materials. Preserve
  actual outputs where stochastic inference or changing APIs prevent exact reruns.
- [ ] Assess target journals' and institutional methods/data requirements with researchers;
  validate reports against representative Danish/English interviews. No blanket journal-compliance
  claim. Detailed scope and acceptance: docs/research-workflow-roadmap.md.
- [x] T44: Redesign overwhelming Provider Settings: short basic setup, Advanced section,
  OpenAI/UCloud/Hugging Face tabs and visible model lists (T54, T57).

## Playback and feature choices — 21 October 2026

- [x] T40: Offer Add word timing on individual passages flagged as missing timing. Align
  only the chosen passage/audio interval, preserve reviewed wording and existing timings
  elsewhere, and append a separate result. Leave unmatched words untimed; support retry and
  local quick-repair path; UCloud remains available through full-result enhancement. Reuse
  a prepared local worker/model between
  repairs to avoid paying cold startup per sentence, with bounded memory and cancellation
  isolation. Also repair all eligible missing passages from the count warning as one combined
  result, with per-passage checkpoints and explicit review. Verified with synthetic inference.
- [x] T41: Inline project settings in place of the static classification/storage summary;
  original-download link above Review result; remove all four decorative slogans.
- [x] T42: Description / notes for each project and recording, independent Save/revision,
  Unicode/literal text, draft/conflict protection and schema-4 backup/migration to schema 5.
- [x] T45: Change/add a speaker for one passage, preserving all words and timings and every
  other passage; keep whole-result speaker renaming independent.
- [x] Make passage-repair completion/result selection clearer: enhancement preserves the selected source,
  so an untimed reference still has passage highlighting until the enhanced result is selected.
  Offer Review repair beside the passage/count without replacing reviewed text automatically.
  Whole-result completion still uses the existing Review result dropdown.
- [x] Integrated verification: 316 unit/API tests and 22 Chromium workflows passed (338 total).
  Ruff/format, Prettier and JS syntax checks passed. Real cached-model passage acceptance on
  the public Danish fixture timed 12/12 words twice, reusing one subprocess: 129.351 seconds
  cold and 7.360 seconds warm; shutdown released it. Private recordings, UCloud jobs and paid
  APIs were not used. See docs/cpu-alignment-benchmark.md.
- [x] Verify da_dk_0002 live playback: the reference was selected. Selecting Local WhisperX
  enhancement displays Word timing and follows individual words during replay; screenshot
  saved in test-results/da-0002-word-timing.png. No timing data needed repair.
- [x] Benchmark real CPU-only Danish alignment on the public 6.84-second da_dk_0002 fixture:
  7.372-second warm median, 12/12 words timed. First imports 75.392 seconds and model/tokenizer
  setup 31.328 seconds. Accuracy and long-recording throughput not established. See
  docs/cpu-alignment-benchmark.md; no speaker model or private recording processed.
  Running-app acceptance also passed: cached-model queue execution took 187.146 seconds with
  12/12 words timed. Appended the sample result; its reference source, unrelated data and
  credential presence are unchanged. Measure/reduce cold startup separately from inference.
- [x] T39: Include WhisperX 3.8.6 and CPU PyTorch in the standard locked installation, as
  requested. Validate every local entrypoint before queueing/conversion, retain completed
  checkpoints and frozen retry settings, and verify the real default environment without weights.
  Verification: locked installation succeeded; 251 unit/API tests and 15 browser workflows
  passed, including missing-runtime rejection before queue insertion, claim-lock availability,
  stale-revision revalidation and completed-stage reuse. Real public alignment passed separately.
- [x] Activate the standard dependency update on main/8766 after backup and idle checks;
  locked sync succeeded, default CPU readiness is true, and every database table/key-presence
  flag matched the backup before the explicitly requested public-sample benchmark was started.
- [x] Investigate passage-wide word timing gaps against original responses. Zero-duration
  provider words (including a final word at a passage boundary), missing mappings and
  zero-length passages trigger the current conservative fallback. Saved text was not edited.
- [x] T31: Enabling Follow playback immediately brings the currently playing passage into
  view, including replay and comparison, without waiting for the next passage boundary.
  Browser regression passes in both views and the complete 260-test regression suite.
- [x] T32 revised: Keep one clear Add word timing / speakers… action for existing results,
  rather than additional provider checkboxes (latest user preference). Reuse its durable
  follow-up backend for the batch dialog; the original checkbox UI stays unmerged.
- [x] T33: Remove Compare providers… actions/dialog; retain the Compare with result dropdown.
  Transcribe with another selected provider to create a result for comparison.
- [x] T34: Move export controls into Export Settings for recordings and projects; offer a ZIP
  of active reviewed results or all saved results, with an explicit skipped-file report.
  Add researched SRT/WebVTT subtitle and CSV/JSON data exports alongside NVivo/readable text.
- [x] T35: Visibly flag missing word timing in word-timed results, including both comparison
  panes. Explain rewritten passages separately; do not warn for speaker/segment-only models.
- [x] T36: Batch transcription dialog: apply provider and Local/UCloud feature settings once
  to selected files. Start immediately and run unattended (confirmed); no scheduling needed.
  Preserve originals/reviewed results; preflight, checkpoint, retry and cancel independently.
- [x] T37: Check runtime & GPU in enhancement settings: detect CUDA access, names and memory
  in the selected interpreter; distinguish absent PyTorch from no accessible CUDA device.
- [x] T38 design/research: Feasibility and current primary-source methods, Danish/English
  candidates, local/UCloud/API execution, evidence-linked topic/aspect sentiment, human review,
  evaluation and T43 lineage. See docs/sentiment-topic-mapping-design.md.
- [x] T38b manual evidence/review slice: separate codebooks/runs, exact quotations, manual topic
  evaluations and reviewed imported suggestions. See docs/topic-evidence.md; no inference added.
- [ ] T38 later: automatic topic-specific sentiment models, discovery/maps and evaluated
  Danish/English quality. Freeze separate text-execution/Protected approvals before transport.
- [x] DOCX export for collaborative review (deterministic, no new dependency).
- [ ] Later export candidates: assess PDF and ELAN EAF only
  against concrete workflows. Subtitle exports currently keep one cue per passage.
- [x] Verification: 260 tests passed, including 15 Chromium workflows; Ruff lint/format,
  Prettier and JavaScript syntax checks pass. Export formats, skipped-item reports, independent
  results, idempotent follow-ups, retry/cancellation, Protected revocation and GPU detection
  use synthetic inputs/mocked providers. No paid calls, real jobs or model downloads.
- [x] Activate on main at 8766 after confirming no active jobs and saving a SQLite backup.
  All database table fingerprints and saved credential presence remain unchanged (schema 4).
  Served JavaScript matches main with no-store headers. Live read-only checks confirm timing
  gap markers, the clear processing action, no provider checkboxes/duplicate comparison
  buttons, and project Export Settings with all seven formats.
- [x] Design follow-up: reliable word highlighting within partially timed passages is retained
  while uncertain words stay untimed (ADR 0030, ADR 0043).

## Comparison search — 21 October 2026

- [x] T30: Remove the manual occurrence-alignment form. Keep automatic comparison alignment
  active while one shared search highlights matches in both panes, including a query present
  on only one side. Retain all comparison context and single-transcript filtering.
- [x] Verify query changes/clearing, case-insensitive literal punctuation, Unicode offsets,
  search during mode changes, timed-word seeking and unsaved-edit protection. Update the
  running interface without changing saved project/transcript data. See ADR 0022 and B50/V40.
- [x] Update highlights in place, retaining passage/word elements and unsaved drafts (B51).
  Full suite: 228 passed, including twelve Chromium workflows; Ruff, JS syntax and Prettier
  checks pass. Live comparison verified both panes highlight and clear correctly in the long
  interview. Refreshed the user's app tab; the service and saved results remain unchanged.

## Interface refresh — 21 October 2026

- [x] Verify the user's stale open tab against current main, reload it safely, and confirm
  Provider Settings at the sidebar bottom, project renaming in Project settings, and
  continuous Play transcript beside Follow playback. Prevent cached HTML/JS/CSS on updates
  and cover ordinary and conditional requests with a regression test (B49/V39).
- [x] Restart main on 8766 after confirming no active jobs; verify no-store headers on the
  live HTML/JS/CSS and check continuous playback plus Project settings in the user's tab.
  Full suite: 228 passed, including twelve Chromium workflows; Ruff lint/format passed.
  No project/provider settings, saved transcripts or recordings were changed.

## Main activation — 21 October 2026

- [x] Merge the app feature branch into local main, fast-forwarding 9794489 to 039914d.
  The hosting agent's feature/transcription-models branch remains separate and unreleased.
- [x] Confirm no active jobs, stop the old service and save a pre-merge SQLite backup.
  All original database-table fingerprints and saved provider-key presence remain unchanged.
- [x] Sync locked dependencies in the main checkout's local .venv and restart on port 8766
  with the same library. Repair a read-only empty OneDrive metadata folder after uv's partial
  uninstall; no filesystem permission changes or environment rebuild were needed (B48).
- [x] Real-library read-only browser check: provider tabs render, password fields stay empty,
  no JavaScript errors and the served frontend matches main. No uploads, saves or live jobs.
- [x] Fresh full run from main: 227 tests passed, including twelve Chromium workflows;
  Ruff/format and frontend checks pass. Optional live UCloud/model/NVivo acceptance remains
  separate from this merge and from automated tests. No paid calls or real remote jobs.

- [x] T1: Git, Python environment, transcript import/edit/export, SQLite persistence, core tests (18 passing).
- [x] T2: Media conversion, provider settings, transcription, durable queue, integration tests.
- [x] T3: Project UI, imports, synchronized playback, both editing and playback modes (browser verified).
- [x] T4: Documentation, lint, full test suite, browser acceptance, committed working version.
- [x] T5: Apply official CBS logos, palette, and locally served CBS fonts; verify browser layout.
- [x] T6: Danish FLEURS reference suite, offline WER/CER scoring, opt-in provider evaluation.
- [x] T7: Named provider configurations, bulk/file selectors, multiple saved results and comparison.
- [x] Default edit mode to Free text; review/edit/restart browser check passed.
- [x] T8: Provider tabs, all five OpenAI models, safe model visibility checks, diarization format fix.
- [x] T9: Project folder selection, data classification and enforced provider approvals.
- [x] T10: Independent UCloud vLLM provider, secure setup tokens, served model discovery.
- [x] T11: Bounded parallel transcription parts with durable out-of-order checkpoints.
- [x] T12: Prepare two files and transcribe two recordings concurrently; label queued preparation accurately.
- [x] T13: Correct NVivo Windows/Mac transcript exports, format labels and import instructions.
- [x] T14: Visually align matching passages in transcript comparison.
- [x] T15: Standard / Protected projects with explicit provider approval.
- [x] T20: Model-first OpenAI labels with Word timing / Speaker Labels suffixes; preserve custom names.
- [x] T21: Native UCloud Transcriber provider through UCloud API v0.1.9; mocked acceptance passed.

## Next implementation work

- [x] T23: UCloud vLLM layout for several saved models, offline configuration without endpoint
  availability checks, and served model ID equal to the Hugging Face owner/model name.
- [x] T24: Protected-project defaults: OpenAI/Custom off, UCloud on. Preserve explicitly saved
  choices and apply eligibility consistently in selectors, APIs and background workers.
- [x] T25: Continuous Play transcript beside Follow playback; passage Replay remains bounded.
  Project renaming now uses the heading pencil/name dialog, superseding the earlier inline field.
- [x] T26: Highlight search matches in both transcript panes, including comparisons. Replace
  the sidebar local-storage slogan with Provider Settings.
- [x] T27: Enable Choose folder for existing projects and safely relocate managed files.
  Copy before changing storage; preserve results/revisions and block active work. Browser/API verified.

- [x] **T22: robust comparison matching by specific words and phrases.** Replace
  the short greedy passage matcher with distinctive phrase anchors and ordered token alignment.
  Handle larger omissions and different sentence boundaries; highlight matching/different wording,
  show uncertain matches, and retain local phrase-anchor API support. The UI uses automatic
  alignment and a shared highlighted search (T30).
  See [review and acceptance cases](../comparison-alignment-review.md). Eleven focused unit/API
  cases and the comparison browser workflow pass. Matching remains approximate; representative
  reviewed interviews still require manual assessment.
- [x] T16: Curated Danish/English Hugging Face speech catalog, following the hosting agent's
  pinned metadata and candidate/validated distinction. Prioritize Whisper large-v3, turbo,
  Parakeet v3 and Danish Hviske v5.3; retain smaller models for resource tradeoffs.
- [ ] T29: Extend UCloud hosting with speech-specific deployment profiles and tested allocations;
  keep UCloud vLLM independent of UCloud Transcriber. A catalog entry does not deploy a model.
  - [x] T29a: Provider-owned speech task, manual request-bound sizing, isolated audio runtime,
    HF served ID, multipart/auth lifecycle checks and nine unvalidated catalog candidates.
    Committed as ab49949 on ucloud-vllm-agent feature/transcription-models; 152 tests passed,
    one existing platform skip, source/wheel and isolated offline CLI checks passed.
  - [ ] T29b: Real reviewed speech profile/allocation, runtime/capacity/quality acceptance,
    provider release and downstream adoption. Published v0.1.4 remains chat/PII only.
  Provider main and this app's running service remain unchanged. See ADR 0020.
- [x] T17: Optional **local** word alignment and speaker detection for existing transcripts,
  CPU support with optional GPU. Save a new result and retain the original text/timings.
  - [x] T17a: Optional WhisperX runtime, timed windows/short untimed clips, independent results,
    stage checkpoints, cancellation/retry, credentials, Protected-local use and browser acceptance.
  - [x] T17b: Derive reliable coarse windows for long untimed text/long passages, with bounded
    memory, wording preservation and explicit uncertainty. Do not invent proportional timestamps.
- [x] T18: Optional **UCloud** execution of the same enhancement pipeline, with progress,
  cancellation, retry, and explicit provider approval for Protected projects.
- [ ] T19: Representative Danish/English evaluation: transcription WER/CER, word timing,
  speaker attribution, processing time and memory. Public FLEURS alone cannot test diarization.
  - [x] T19a: Offline reviewed-clip scorer, Danish/English/corpus totals, timing and speaker
    coverage, explicit mappings, reported time/memory scopes and multi-method CLI comparison.
  - [ ] T19b: Real reviewed interview baselines with prepared local/UCloud/API runtimes;
    external data/inference acceptance remains pending, with no paid or real runs in automation.

Local enhancement, including long untimed alignment, is implemented with mocked inference
acceptance. UCloud execution also has mocked API/transport/browser acceptance; its prepared
runtime needs live validation. No weights have been downloaded or real CPU/GPU inference performed.
See [speech model research](../speech-model-research.md) for model comparisons and
the catalog/enhancement direction.

## After implementation

- [x] T28: Deep project audit after the implementation TODO work: correctness, data safety,
  provider/queue lifecycle, concurrency, transcript editing/search/comparison, exports,
  architecture, tests and documentation. Record evidence and fix verified issues with regressions.
  Keep live provider/NVivo acceptance limitations separate from automated audit evidence.
  Offline implementation and audit are complete. T19b/T29b remain external acceptance.
  See [audit evidence and limitations](../audit-2026-10-20.md).

## Final audit verification — 20 October 2026

- **227 tests passed, including twelve Chromium workflows.** Ruff lint/format, JS syntax,
  Prettier, source/wheel builds and package-content checks passed. All 23 added audit cases
  use synthetic/mocked data; default SDK transport is tested alongside injected HTTP clients.
- Fixed unresolved remote journal deletion, uncertain submission reconciliation and hidden
  old Activity actions. Exact unique-name reconnection, explicit absence receipts, stop/terminal
  state persistence and subsequent failed-POST protection are covered. No remote jobs were started.
- Fixed malformed top-level words, timestamp field disclosure, unreliable/zero-duration word
  intervals, classification changes during remote enhancement and late editor response races.
- Default provider clients reject redirects so audio/model requests stay at the configured
  endpoint. Actual 307/308 transport behavior was reproduced using mocked SDK-default HTTPX2.
- Visual review corrected recovery action overflow; Activity shows the exact UCloud search name.
  A legacy provider browser assertion now waits for Protected routing and tests general labels
  in an explicitly Standard project. Policy was not relaxed to satisfy the test.
- Main and provider main checkouts remain clean at their original commits; the main service
  on 8766 is healthy and was not restarted. No paid calls, private uploads, large weights,
  real allocations, merges, pushes or releases. Remaining live acceptance is listed below.

## Local enhancement increment — 20 October 2026

- Complete suite: 160 tests passed, including eight Chromium workflows. A further restoration
  edge case was reproduced and corrected; all 16 focused enhancement/browser checks passed after
  that change. Ruff lint/format, JS syntax, frontend formatting and source/wheel builds passed.
- Optional separate WhisperX 3.8.6 runtime; app environment/lock remain lightweight. CPU/CUDA,
  Danish/English, word alignment and speaker detection can be selected independently. Frozen
  source snapshots, stage artifacts and job-ID idempotence preserve edits and reviewed results.
- Subprocess cancellation and secret redaction are exercised with synthetic processes. Worker
  tests use mocked inference packages; browser acceptance covers Protected-local use, new result
  selection, word seeking, credential clearing and restart. No weights, live jobs or private uploads.
- Actual WhisperX model loading/accuracy, gated access, memory and CUDA remain unverified.
  The original short-window limit was extended in T17b below. T19 and the final audit
  remain pending. Main service remains unchanged.

## Long-text alignment verification — 20 October 2026

- 168 tests passed, including nine Chromium workflows. The subsequently extended comparison
  playback regression also passed. Ruff lint/format, JavaScript syntax, frontend formatting,
  source/wheel builds and packaged-worker inspection passed.
- Preliminary local recognition locates distinctive ordered phrases in bounded audio slices;
  final timings come only from bounded WhisperX alignment of the unchanged reviewed wording.
  Repeated/conflicting phrases and large omissions stay untimed. Known passage bounds constrain
  anchors; short timed passages retain their supplied windows.
- Disk-backed audio, per-slice/per-window checkpoints, offsets, source preservation, Unicode,
  partial timing and restoration have synthetic regression coverage. Partial results retain
  every character, label incomplete timing and allow seeking beyond untimed gaps in both panes.
- Browser acceptance exposed a shared playback lookup assumption; fixed timed interval indexing
  while preserving display order. Visual inspection confirms timed and untimed passages remain clear.
- Recognition/alignment models were mocked; real accuracy, memory, gated access and GPU behavior
  remain external acceptance work. No models, paid requests, UCloud jobs or private uploads.

## UCloud enhancement verification — 20 October 2026

- Complete suite: 185 passed, including ten Chromium workflows. Ruff lint/format, JS syntax,
  Prettier, source/wheel builds and packaged standalone-worker inspection passed. Browser UI
  screenshot reviewed; the main service is still healthy on 8766 and was not restarted.
- Separate enhancement execution configuration, with server/project/SSH template/runtime/cache,
  secure server-scoped token, Protected defaults/explicit denial and metadata-only checking.
  Both word alignment and speaker detection use the existing source-preserving pipeline.
- Fourteen focused cloud tests exercise frozen identity, revocation between private uploads,
  unknown allocation, template hardware, stage validation, idempotence, explicit cancellation,
  shutdown/reconnect, cleanup retry, curated transport failures and bounded output. Native failed/
  interrupted remote jobs now also have an owned Stop path. No actual control-plane requests.
- Detached remote wrapper retains an inference-owned lock and stage/window checkpoints. Retry
  reconnects to the recorded allocation; no uncertain allocation is resubmitted. Cached remote
  models only; no package or model installation. Only current alignment wording/bounds are sent;
  speaker-only requests contain no transcript text. No setup credentials enter the remote worker.
- Real UCloud template/SSH/key/host-cache behavior, model loading, permissions, memory and GPU
  remain live acceptance work. No UCloud jobs, private uploads, paid calls or weights were used.

## Reviewed evaluation verification — 20 October 2026

- 204 tests passed, including ten Chromium workflows. Nineteen new synthetic evaluation cases
  cover language/corpus weighting, exact lexical timing association and repeated-word ties,
  missing annotations, manual speaker mapping, invalid intervals, empty predictions, measurement
  units/scopes, saved-result edit validity, streamed hashes and non-overwriting offline CLI reports.
- Reports omit transcript bodies and disclose timing/mapping/measurement coverage. Speaker word
  accuracy is explicitly not DER. Missing measurements stay null and mixed scopes are not
  silently aggregated. Reported values are not independently collected hardware benchmarks.
- Ruff lint/format and source/wheel builds passed. No provider calls, external datasets, private
  recordings or model downloads. Real Danish/English interview rankings and measured resource
  baselines still need reviewed fixtures and live runs; T19b remains unfinished.

## Existing-project storage verification — 20 October 2026

- 144 tests passed, including seven Chromium workflows. Ruff lint/format, JavaScript syntax
  and frontend formatting passed. Source/wheel builds include the nine packaged catalog entries.
- Reproduced the missing storage-change API. Six regression cases now cover successful media/raw
  transcript/result relocation and reopening, failed copy rollback, active job blocking,
  legacy-project isolation, post-commit errors, occupied/nested targets and cleanup warnings.
- Chromium chooses a new parent for an existing project, saves a new name/location, reopens
  settings and verifies the new path. Native Windows folder selection is mocked; no real project
  was moved. Unicode timed-word rendering now has emoji/Danish review/search/export coverage.
- Changes are committed on feature/ucloud-transcriber; main and the running main service remain
  unchanged. No live UCloud allocation, private upload, paid request or model download occurred.

## Provider models and review controls — 20 October 2026

- 138 tests passed including seven Chromium workflows; Ruff lint/format and JavaScript syntax
  passed. Provider defaults and explicit overrides, multiple offline HF models, matching served
  IDs and unchanged legacy job identities are covered. No endpoint discovery is needed to save.
- Nine pinned speech candidates with provenance and license metadata; no claimed deployment or
  measured allocation. Browser checks cover catalog filtering, Hviske's JSON setting, multiple
  saved models, credentials and ordinary speech requests to a synthetic endpoint.
- Continuous playback crosses passage boundaries; search marks exact text in both panes,
  including matches present on only one side. Word seeking still works through search marks.
  Project renaming remains in settings; sidebar slogan removed. Real hosting remains untested.

## Whole-transcript comparison verification — 20 October 2026

- 132 tests passed, including seven Chromium workflows. Ruff lint/format, JavaScript syntax
  and frontend formatting passed. Regression cases cover the four reproduced matching failures,
  phrase anchors, differences, uncertainty, Unicode and cross-recording isolation.
- Comparison uses all words, distinctive phrase anchors and local sequence matching; it no
  longer accepts timing alone or restricts matching to three adjacent passages. Browser checks
  exercise exact phrase search and anchors without changing text, alongside editing and playback.
- Reviewed interview acceptance remains manual. No external inference or private-data reads.

## Native UCloud provider verification — 20 October 2026

- Pinned released UCloud API client v0.1.9 in uv.lock. Independent setup tab, metadata-only
  checking, immutable original upload, preserved template product/resources, durable submission
  checkpoints, remote job links, cancellation and reconnect/retry implemented.
- 125 checks passed in the complete suite. A further cancellation regression was reproduced
  and fixed; 31 focused queue/provider/browser checks then passed, including that new case.
- Chromium covers native setup, draft credential isolation, saving, file selection, synthetic
  transcription, word timing and remote job link. UI uses the provider/model/suffix label.
- UCloud responses and transfers were mocked. No real UCloud job, recording upload, API-key
  lookup or model download occurred. Live template/transfer/output acceptance remains below.

## Comparison and Protected verification — 20 October 2026

- 112 tests passed: 106 unit/integration checks and 6 Chromium acceptance checks.
  Ruff lint/format, JavaScript syntax and frontend Prettier checks passed.
- Comparison matches wording and available passage timing, including split/combined passages
  and omissions. Paired groups align after resize, link scrolling side by side, retain active
  editing, and temporarily suspend alignment during search. Matching changes no saved text/timing.
- The new Protected label replaces all three former protected categories. Schema-3 migration
  saves a backup and preserves transcripts/results/queue snapshots. Partial legacy approvals
  require explicit approval for the combined category.
- Model capability help now distinguishes plain text, word timing and speaker/passage timing.
  A local post-processing pipeline for speaker detection and word alignment is recorded below
  as future work; no model weights or additional inference dependencies were installed.
- The FLEURS provider mock now identifies the recording rather than assuming request order;
  parallel execution retains the deliberate single-word scoring error deterministically.

## NVivo format verification — 20 October 2026

- Reproduced the schema failure against the real export path: INTERVIEW text had no
  deprotected columns. Kept the supplied grouping workflow as explicitly labelled Readable text.
- Added Windows TSV with Timespan/Speaker/Content and Mac F5/speaker paragraphs.
  Native downloads use UTF-8 BOM, preserve selected-result edits and reject untimed passages
  explicitly. Readable export remains available without timing.
- 102 tests passed: 97 unit/integration checks and 5 Chromium acceptance checks. Browser checks
  download all three formats and verify untimed fallback. Ruff, Python formatting, JavaScript
  syntax and Prettier checks passed.
- Documented platform-specific import settings and Mac's colon-space speaker-parsing limitation.
  Actual imports in both desktop NVivo applications remain manual acceptance work.

## UCloud vLLM and concurrency verification — 20 October 2026

- Developed in isolated branch `feature/ucloud-vllm`. During this work the original checkout
  stayed clean on `main`; the running server and current transcription were left unchanged.
- 95 tests passed: 90 unit/integration checks and 5 Chromium acceptance checks.
  Ruff lint/format, JavaScript syntax and frontend Prettier checks passed.
- Separate UCloud vLLM settings, scoped setup credentials, model alias discovery and synthetic
  speech requests verified. Setup tokens never enter inference requests or database settings.
- Two media workers and two recording workers run concurrently. Deterministic blocked-work
  tests verify overlap, unique claims and cancellation of only the requested job.
- Parallel parts finish out of order and retain sparse checkpoints; failure, cancellation,
  explicit retry, timestamp offsets and graceful shutdown are covered.
- Automatic speech hosting/URL creation requires a future hosting-agent ASR workflow.
  Provider-wide UCloud capacity management remains deferred. No UCloud jobs were allocated
  and no paid transcription calls were made.

## Project policy and provider verification — 20 October 2026

- Final verification: 79 tests passed (75 unit/integration, 4 Chromium acceptance); Ruff,
  Python formatting, JavaScript syntax and Prettier checks passed.
- All five OpenAI presets use their supported request formats; legacy diarization jobs normalize
  their format on explicit retry. Custom endpoints keep configurable capabilities.
- OpenAI/Custom/UCloud settings tabs and model-list checks verified in Chromium. Draft keys
  are not saved by checking; errors expose only curated messages and safe protocol fields.
- Sensitive/restricted/confidential classifications require explicit provider approval in the
  dropdowns, selection API, enqueue/retry API, and worker before each upload. Tests cover
  revocation and changed endpoints between chunks and after queueing.
- Project creation supports a chosen parent folder, native Windows folder chooser, readable
  original/current result snapshots and per-result NVivo files. SQLite remains authoritative.
- Schema-2 migration backs up the database and preserves existing paths, text, results, and
  revisions. Existing projects retain their location; storage is selected at creation.
- Four Chromium acceptance tests passed. Native folder selection is mocked in browser tests;
  the Windows chooser itself still requires a manual desktop interaction.
- Live app restarted idle. Saved transcript/result fingerprints are unchanged. All five models
  were listed by the configured account's read-only model endpoint. No audio was uploaded and
  the failed paid transcription was not retried during development.

## Provider/result verification — 20 October 2026

- 59 tests passed: 56 unit/integration tests and 3 Chromium acceptance tests.
- Named provider CRUD/defaults, endpoint credential redaction, frozen queued configurations,
  independent results/imports, migration backups, revisions, cross-recording isolation, and
  result-specific export verified.
- Browser checks cover bulk and per-file selection, delayed preference saving, multi-provider
  runs, read-only comparison, independent editing, highlighting, playback state, and restart.
- Ruff lint/format, JavaScript syntax, and frontend formatting passed.
- User-authorized restart completed; all six existing recordings retained identical transcript
  text, speakers, and revisions. A schema-1 SQLite backup was saved in the managed library.
- The running library has Speaker labels and Word timing configurations sharing its existing
  OpenAI endpoint credential. Development checks made no paid API calls.

## Reference evaluation verification — 20 October 2026

- Five public Danish WAVs (63.96 seconds) pinned to a dataset revision with hashes/license.
- 52 tests passed, including 11 evaluation checks and both Chromium acceptance tests.
- Real clips exercised upload, FFmpeg conversion, queue, playback ranges, persistence,
  and complete NVivo export with mocked transcription responses.
- Scoring verified for Unicode, corpus weighting, empty output, insertions, incomplete
  coverage, duplicate IDs, damaged fixtures, failed jobs, and timeouts.
- Ruff lint/format checks passed. No paid requests were made during this change.
- Actual provider accuracy has not been measured by this suite yet; live evaluation is opt-in.

## CBS branding verification — 20 October 2026

- Official horizontal/vertical logo SVGs and CBS New Bold / CBS Sans fonts served locally.
- Both Chromium acceptance tests pass; existing playback, import, edit, and export flows work.
- Frontend formatting, Ruff, and package build passed; built wheel contains all seven brand assets/notices.
- Updated app visually checked in the user's local browser at its narrow sidebar-panel width.

## Verification — 20 October 2026

- 41 tests passed: 39 unit/integration checks and 2 Chromium acceptance tests.
- Ruff lint and formatting checks passed; JavaScript syntax check passed.
- `uv sync --locked` and wheel/source builds passed; wheel includes static assets and CLI.
- Browser acceptance covers two projects, audio/video import, both editing modes,
  word highlighting, audio/video switching, export, persistence after restart, and
  transcription through a synthetic local HTTP provider. No paid API calls were made.

## Later / external validation

- [ ] Import the new native transcript formats in NVivo Windows and Mac desktop applications.
- [ ] Validate a real UCloud JSON/CSV export; only reference scripts were supplied.
- [ ] Record a first live FLEURS WER/CER baseline using the configured provider.
- [ ] Add representative interview recordings with reviewed ground truth for broader evaluation.
- [ ] Validate native UCloud submission/transfer/output with the user's real project and batch
  template using a small synthetic recording; user plans this for tomorrow.
- [ ] Validate UCloud enhancement live (now API-only, T60): runtime preparation, readiness, real
  speaker/alignment output, restart/reconnect and Trash cleanup. SSH validation is superseded.
- [ ] Provider-wide capacity limits for UCloud vLLM and UCloud Transcriber, when implemented.
- [ ] Validate/release the provider-owned speech extension (T29b), including real sizing,
  runtime and owned allocation lifecycle. The published agent still supports chat/PII only.
- [x] Automatic realignment after free-text edits (T69, opt-in). Assessed 23 Oct: edited passages already
  offer **Add word timing** and **Repair all missing word timing** (one local run, one appended
  result; the note now says so). Fully automatic runs would append a result per edit while review
  continues (V94) or write timing into the reviewed result, which ADR 0016 rules out. Decide with
  researchers whether a timing-only version of the same result is acceptable before building it.
- [ ] Run live local/UCloud WhisperX quality and CPU/GPU benchmarks on representative reviewed
  Danish/English interviews (T19b); offline pipeline implementation and mocked acceptance are complete.
- [ ] Native desktop packaging and broader platform testing.

OpenAI Batch API and automatic summary/cleanup features are outside this version.
