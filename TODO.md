# CBS Transcriber worklist

This file is the current list of outstanding work. Implemented means code exists; synthetic
verification does not establish real-provider, real-device, research-quality or institutional
acceptance. Check the actual branch, running app and job state before activation. Dated past
checkpoints are preserved in the [historical archive](docs/archive/TODO-archive-2026-10.md); their unchecked
items and status claims are not current instructions.

Supporting references: [application audit plan](docs/audits/application-audit-plan-2026-10-30.md),
[transcription and Activity audit](docs/audits/transcription-batch-2026-10-30.md),
[weekend plan and Settings backup design](docs/work-plans/weekend-2026-10-31.md),
[SPEC](SPEC.md), and [architecture decisions](docs/decisions/README.md).

Checks only a person can do before public release (real devices, accounts, recordings and
desktop software) are collected in the [human release checklist](docs/testing/human-release-checklist.md),
started 20 November. Add a check there whenever new behavior is verified only synthetically.
The [post-MVP audit and proposed sequence](docs/work-plans/post-mvp-2026-11-10.md) separates
missing implementation, pending acceptance, workflow decisions and explicitly parked work.
The [11 November implementation plan](docs/work-plans/post-mvp-2026-11-11.md) tracks the
verified local morning changes on `codex/post-mvp-morning`; activation remains separate.

## Current post-MVP branch

20-21 November night continuation: local `codex/night-post-v2-20261120` is verified at product
`f96362f` (ADR0190/0191, B513-B518, V254-V258): 448 backend passes/two skips and 108 selected
browser passes on one immutable source, with exact hashes and independent review. See the
[night composition receipt](docs/verification-2026-11-20-night-composition.md). Main, Claude's
original branches, rc2 and the loaded preview are unchanged. Whole-suite and human/provider
acceptance remain open. The user still needs to choose whether too-short timing repair may
replace passage bounds; no widening or new timing-editor save workflow was implemented.


20 November final follow-on tasks (local, separate from verified source): cursor-word timing
prototype `f140a34` has 15 browser passes but no persistence and remains unmerged/OFF by default
(T159). Project deletion in Activity is now implemented on `claude/deletion-activity`
(ADR 0190, T160), independently hardened at `8cad1eb` with376 backend passes/two skips
and22 relevant browser passes. Real H24 remains. The agreed review dropdown passed58 focused
browser cases at `9b56e20`. Both are composed on local `codex/night-post-v2-20261120` for final
gates; main and the running preview are unchanged. Backup in Activity has a reviewed
proposal and three characterization passes, including the necessary reciprocal
deletion/capture admission fence (T161); that plan is not an accepted ADR or task service. Save/Split benchmarking was deferred at the user's request;
no usable timing samples were collected. Exact private artifacts are in the Claude handoff.


20 November offline weekend composition: `codex/weekend-post-v2-20261120` combines
the reviewed timing, spelling/Text uncertainty, disabled Runtime supersession and
unused native capacity helpers at product `4574083`. Focused staged gates and
independent source Go are recorded in the [composition receipt](docs/verification-2026-11-20-weekend-composition.md).
The local rc2 wheel remains separate/frozen at `6ff03df`; no main merge/push,
publication, installation or app reload occurred. Real provider/device acceptance
and the remaining weekend features are open; Runtime switches remain off and
the native helper has no paid-control integration.


20 November Codex editor checkpoint on `codex/weekend-spelling-20261120`: Claude's
recording vocabulary/Activity downloads/double-click redesign was independently checked,
and dotted model uncertainty now appears in the actual Text draft without changing its
native editing state (T157/ADR0188). **165 distinct focused cases pass**, with the
cached-engine harness limitation recorded in the [verification receipt](docs/verification-2026-11-20-editor-spelling.md).
That editor branch is now included in the offline composition above; it remains
unloaded/unreleased. Spelling stays hidden in MVP.
Spelling bug IDs were reconciled to B488–B491; core export/timing keeps B467–B470.

12 November handover: [the continuation plan](docs/work-plans/mvp-v2-continuation-2026-11-12.md)
records tested components and the unstarted Runtime Stage 2, funding-observer (T146) and
schema-19 Working (T147) designs. Editor/review workflow is the next user discussion;
broader cleanup must preserve ongoing/recoverable work and Activity evidence.

17–18 November (Claude): T147 is implemented with independent Go; the Show filter and
default-on Text highlighting are implemented; saving a new UCloud key reconnects
automatically (B452). The standalone funding observer (T146) is implemented with independent
Go (unwired). Runtime Stage 2 (T144) is implemented disabled with independent Go after three
review defects were fixed. All are combined on `claude/v2-integration`, not loaded. User decisions: [editor/review decisions](docs/work-plans/editor-review-decisions-2026-11-12.md).

18 November browser baseline (Claude): the first full browser run on v2 code failed 105
cases. Fixed, test-only unless noted: the notes overlap (B455, CSS); fresh-profile
first-use setup now pre-completed unless a module is marked `first_use` (B457, ~70 cases);
journeys built on retired direct OpenAI moved onto a checked native UCloud model; 20 stale
Settings/runtime/consent cases updated (B458). Tests can no longer reach a real host
(B456). After these fixes the full browser suite passed on `claude/v2-integration`
(668 passed); `test_release_profile` fails on this branch only because it packages the MVP
profile. Settings design findings, decided by the user 19 November (`claude/settings-findings`):
the Protected approval box is hidden for UCloud, which may serve both classifications, and
shows only for a connection saved as not allowed so it can be undone (V227); choosing the
open tab again first asks before discarding unsaved UCloud or Hugging Face entries, while a
clean tab still reloads and retries as before (V228); always saving words_speakers for UCloud
is correct because the Transcriber app always identifies speakers (closed, no change).

19 November (Claude): `claude/v2-integration` is loaded on the user's real library after a
verified backup. Preview feedback fixed on `claude/v2-preview-fixes`: setup returns on each
load while no UCloud key is saved (ADR 0171 amendment), a centred Create a project button on
the empty home page, and project deletion from the home page (B460).
On `claude/funding-wakeup` (not merged) the unwired funding observer's deadline wake-up no
longer relies on a Windows-only socket close: it first shuts each connection down through its
own duplicate descriptor, which also reaches a stalled TLS handshake (ADR 0178 amendment,
B465). Windows-verified (272 funding tests); Linux only by a stdlib probe; macOS pending.

20 November (Claude, `claude/timing-units-fix`, not merged or loaded): local passage word
timing on the MVP v2 preview. Repair all no longer fails when Windows briefly refuses the
progress file (B468); job failures name busy files and processing faults separately from disk
problems (B469); windows too short for their text are refused, skipped when frozen and shown as
"Too short to time", and Activity shows "Aligning words…" (B470, ADR 0189). Synthetic and
offline-replay evidence only; a real WhisperX run on the user's audio is human check H15a.

- [x] **Local passage timing reliability candidate (B468–B470/B481/B486/B487, ADR 0189).**
  Frozen backend gate: 377 passes; final browser gate: 22 passes, plus independent real
  Windows held-reader and cancellation checks. Unicode parity and local rc2 wheel/constraints
  are verified (source `1ae0069`, packaging `6ff03df`); installation-helper/profile gate 155 passes; [verification](docs/verification-2026-11-20-timing-rc2.md). Real-library
  WhisperX acceptance remains H15a; this candidate has not been loaded or published.

The assembled local `codex/mvp-v2-reliability-candidate` now has independent Go on 193 focused
tests, preserving exact reviewed UI/native/Runtime source. [Composition and limits](docs/verification-2026-11-12-reliability-composition.md).
It is not loaded or published. Funding-based CPU fallback remains planned; disabled Runtime
generation Stage 1 is verified separately at `401769e`, with later recovery stages still open.

- [x] **Automatic native startup reconnection (T141), verified locally 12 November.**
  `codex/mvp-v2-native-reconnect` resumes exact submitted singleton/batch allocations with
  durable Stop and no new allocation/upload/followup. Final 76 focused cases and independent
  review pass; earlier 444 passes/one platform skip remain qualified prior-source evidence.
  [Verification](docs/verification-2026-11-12-native-startup-reconnection.md).
  The 23 November Windows full-profile live checks passed H7–H9 on local source `9575be8`.
  Startup rejoined a queued allocation, explicit Retry reused a confirmed upload, and startup
  recovered the remaining download without duplicating results.
  [Live scope and limits](docs/verification-2026-11-23-rc3-backup-and-reconnect.md).
  Fresh singleton generic-curated failures, historical generic failures, uncertain submissions
  and cleanup-only obligations remain manual. Live missing-output Retry remains unverified.

- [ ] **Runtime preparation and lifecycle acceptance.** The local rc3 candidate includes B581,
  which treats a matching suspended legacy job as a possible reader at preparation admission.
  Its scoped synthetic gates pass. The full-profile Windows test app reported base preparation
  needed in a read-only check. Controlled paid preparation and a real small batch await approval.
  Audit the broader effect of resumable states on preparation ownership, capacity, Stop and cleanup
  separately. [Guard verification](docs/verification-2026-11-23-runtime-reader-guard.md).

- [ ] **Isolated Runtime recovery (T144), authorized stability work.** Stage1 on
  `codex/mvp-v2-runtime-generations` from reviewed observation checkpoint `857a727` implements
  only bounded registry/selection/path data and candidate CAS, default disabled; [46 distinct
  synthetic cases and independent review pass](docs/verification-2026-11-12-runtime-generations-stage1.md).
  Promotion, readiness, preparation and execution remain held. Later stages must bind the actual
  durable preparation owner, exclusive folder receipt and all accepted compute/generation
  carry before controlled live acceptance. Keep old locks/environments untouched; CPU/CUDA
  targets stay separate and promoted environments cannot receive new model downloads.
  Stage 2 (disabled owner/folder/readiness-promotion slice) is implemented on
  `codex/mvp-v2-runtime-protocol`: builder `e9dd23d`, independent review `229865a`, defect
  fix `600141f` (B453); combined on `claude/v2-integration` with all switches off.
  - [x] Disabled settled-candidate supersession (`claude/runtime-supersession`, 19 November).
    Independent review found B484/B485; local fixes have nine red/green regressions and
    final 203 generation and 249 legacy/HF/Stage1 passes with three platform skips;
    [verification and limits](docs/verification-2026-11-20-runtime-supersession.md). All
    generation switches remain off; no live preparation or activation is implied.
  - [ ] Strict comparison with UCloud's normalised specification:
    [designed](docs/proposals/runtime-strict-specification-comparison-2026-11-19.md);
    needs authorized real UCloud job replies.
  - [ ] Real create-folder reply shapes and a frozen Terminal script-parameter record
    (both need real UCloud evidence and the user's authorization).
  Admission/carry/execution/repair/capture/restore/CPU and live acceptance remain (ADR 0176).

- [x] **Existing sole-result Working on schema 19 (T147).** `codex/mvp-v2-existing-working`
  accepts only 18/18 and 19/19 pairs, never reads or writes review marks, and skips any
  recording with doubtful native-recovery evidence (ADR 0179). Builder `1b4d566`; independent
  tester Go `f484571` (171 focused cases). Not loaded. Undecodable stored text no longer aborts
  the pass (B454): unreadable failed-job settings skip that recording, unreadable owner links
  or owner rows refuse the pass as `native_evidence_unverifiable` (`claude/v2-followups`).

- [x] **CBS Runtime preparation observation stability (T143), authorized 12 November.** Reproduce and
  fix standalone transient-monitor/terminal-publication and late-Stop completion races on
  `codex/mvp-v2-runtime-reliability`. ADR 0175 requires typed bounded observations, one durable
  Activity/allocation and no new preparation/lock/cache mutation. Independent design review
  passed with the typed-reader/atomic-success conditions; 117 distinct final synthetic passes
  and independent code/evidence Go qualify this local slice. It is not loaded in the preview.
  The historical retained lock is unresolved; separate immutable-generation work and controlled
  UCloud acceptance are needed before claiming the environment is ready.
  [Verification and limits](docs/verification-2026-11-12-runtime-observation.md).
  [Reliability plan](docs/work-plans/mvp-v2-reliability-2026-11-12.md).

The user authorized selected follow-up work on local `codex/post-mvp-work`, descended from
clean public main `708a27b`. The current morning checkpoint is `codex/post-mvp-morning`,
including the reviewed isolated timing-policy work. **Do not merge/push it to main, update Desktop main, publish or
reload the installed MVP.** Frontend changes stay within explicit requests; broader choices
await the colleague's feedback. The installed/published baseline remains MVP 0.1.0.

The [seventeen-item evidence audit](docs/audits/post-mvp-hidden-tasks-2026-11-10.md) is the
current classification of hidden work. Delivered native retrieval/recovery, concurrency
controls, background exports, deletion and server fencing need acceptance/performance checks,
not duplicate implementations. Real-device/provider, model-quality and institutional gates
remain open. Missing-output reruns have a [design only](docs/proposals/native-failed-output-recovery-2026-11-10.md);
no new transcription or remote cleanup was performed. Focused branch verification is recorded
separately from the published release receipt.
The segmented full backend gate now covers 5,526 unique cases: 5,500 passed, 26 skipped;
independent inventory/outcome audit passes. Relevant provider browser regression finished with
25 distinct passes. The isolated `codex/post-mvp-timing-hints` child now passes its complete
5,600-case backend inventory: 5,574 passed, 26 skipped, independent Go. Both remain local;
see [child verification](docs/verification-2026-11-10-timing-hints.md).

## Colleague MVP — 11 November 2026

Readiness was brought forward to **10 November**. Follow the
[current finish/freeze/acceptance plan](docs/work-plans/mvp-ready-2026-11-10.md); finish the core
editor/export reliability changes, load one labelled tested build and reserve tomorrow for
acceptance and blocking fixes. **MVP 0.1.0 was activated and verified**, including exact served
asset bytes and unchanged database/originals. That development instance has since been stopped
normally for the user's fresh uv-tool installation. The new installation completed with copy
mode and was verified serving MVP 0.1.0 from its installed tool environment; functional acceptance remains
pending. See the [release receipt](docs/verification-2026-11-10-mvp-release.md).

10 November user acceptance: double-click/Text controls, manual Checked/Needs review and Next
to review, and background exports after closing Export Settings work in the user's quick test.
Native Retry failed and cleanup passed the user's real rc6 check on 10 November: all twelve
results are saved on the same allocation, the eleven earlier result IDs are unchanged, and
owned run-file cleanup is verified complete in UCloud Trash. No new transcription was submitted.
The [focused Save/Split audit](docs/audits/rc5-save-split-speed-2026-11-10.md) reproduced long-transcript
delays. The independently reviewed rc7 candidate halves 1,800-passage controls-ready medians
to 8.20 seconds for Save and 7.52 seconds for Split; both remain below one second at 100
passages, with slightly slower small-fixture measurements. The user accepted these improvements
for this MVP. The tested package is now loaded; its served label and asset bytes match.
Functional acceptance does not establish speed.

- [x] **Final manual-edit follow-up, verified offline.** Fresh Text and Split policies now
  allow wording/between-word splits independently of timing quality; old history generation
  remains unchanged. The assembled backend gate passes 258 cases, including new-policy Undo
  and repinned contained history. The broader browser run passed 31 cases; eleven corrected
  oracle cases and six re-profiled Text cases pass their reruns. Independent reviews give Go.
  These final policies are included in the published and verified MVP 0.1.0 package.
- [x] **Publish the MVP wheel release.** v0.1.0 is public in a clean-history repository, with
  matching constraints and optional installed-package shortcuts. All six release assets were
  verified by anonymous download. Old development history remains in the private repository.
- [ ] **Fresh installed-app acceptance.** The user installed all packages on this PC after
  switching to `--link-mode copy` for Windows Cloud Files error 396. The earlier development
  server was stopped gracefully after idle checks so the installed app can open the library.
  Installed startup now responds with MVP 0.1.0. Check core review/export controls and the optional shortcut; Mac/clean-device
  acceptance remains separate. The guide now checks existing uv/PATH before installation.

- [ ] **Post-MVP Save/Split speed follow-up.** Further reduce long-transcript Save and Split
  latency. The accepted rc7 improvement still takes roughly 7–8 seconds at 1,800 passages.
  Audit full-recording mutation responses (about 4.94 MB), remaining history validation and
  synchronous project snapshots, and the retained full mutation render. Measure cold existing
  heads after restart, larger multi-recording/history fixtures and representative tail latency;
  separate controls-ready, primary API, render and background-settled timing. Preserve exact
  timing/history/Working/inverse and snapshot/recovery contracts. These further changes are
  explicitly deferred until after MVP.
  **Broadened 20 November (post-v2 weekend):** testing MVP v2 the user still finds manual
  editing slow on long transcripts, not only Save and Split: opening and typing in long
  passages, Save, Merge, Split and Delete on 600–2,000-passage transcripts, and the per-passage
  Undo merge (also reported slow). Measure each action end to end (click to usable controls)
  with synthetic fixtures and fix the largest costs.

Requested source cleanup is complete in main and Desktop: the retired external preparation
bundle is removed, and both historical Markdown archives are under docs/archive with incoming
and internal relative links updated. Test-result/cache removal was audited, not performed.

- [x] **Button caption cleanup:** Delete Recording and other action buttons omit trailing
  ellipses. Forty affected browser checks pass; source and independent caption scans pass.
  Loaded in rc5 after idle and preservation checks.
- [x] **Review-controls layout:** manual progress, Next to review and uncertainty controls now
  share a row when space permits; narrower widths wrap without page overflow. Five affected
  browser checks and visual checks at four widths pass. Loaded in rc5 alongside captions.
- [x] **Passage editor follow-up:** double-click selects the exact mapped word in Text, including
  emoji, repeated words and CRLF. Save requires changed text; Split requires an interior caret;
  Cancel restores only one passage. Seven word-selection, six new controls and six existing
  playback/hints browser checks pass. Loaded in rc5.
- [x] **Native log-budget correction:** mandatory local batch checks no longer consume the
  separate three-second log I/O allowance (ADR 0160). Reproduced the live twelve-file failure;
  73 log/incremental cases and nine independent targeted checks pass. Loaded in rc5;
  real early-result acceptance remains open.

- [x] Final speed audit and focused fixes on the exact activated rc4 package: separate 100/600
  synthetic fixtures passed correctness/source checks. Zero of 48 sampled content actions
  exceeded three seconds; long save median 2.69 s, Undo split 2.49 s. Separate API/render/
  background times and the earlier slower Undo observations remain explicit; n=3 is not p95
  or a guarantee on other hardware. Larger-history performance remains a broader follow-up.
  [final audit and exact metric definitions](docs/audits/mvp-final-editing-speed-2026-11-09.md).
- [x] Implement/review/test the separate local spelling preview from the stable package:
  codex/offline-spelling-rc4, feature commit 3870ac2; preserved follow-up 2c2d61b.
  Danish checking and British English suggestions,
  private vocabulary and bounded worker/cache controls passed scoped backend/browser/privacy
  gates. This branch is unmerged and not pushed; integration awaits later user approval.
  The loaded MVP rc5 contains no spelling feature.
  **Parked until after MVP by the user on 10 November.** The separate preview server/tab is
  closed; its test library and unmerged, unpushed source remain. Do not resume or integrate it
  without a new request. Prioritize the core app and release/installation/documentation review.
  On 19 November the user chose "Hidden in v2"; see Spelling integration and acceptance below.

- [x] **Background export preparation implemented and verified offline:** project/recording ZIPs
  continue in Activity after Export Settings closes. Ready downloads stream without a browser
  ZIP Blob, with Cancel/Discard and exact selection/Working/file safeguards. Twenty-two final
  backend/API, 99 broader (two setup skips) and 16 browser cases pass (ADR 0158). Loaded in rc3;
  real large-media throughput remain; these temporary tasks do not resume through server restart.

- [x] **Post-rc2 usability follow-up implemented and verified offline:** Next to review follows
  manual Not checked/Needs review; ZIP exports optionally include originals with clear disabled
  no-transcript rows. Focused browser, owned-file/streaming and selection regressions pass.
  Loaded as MVP 20b8ee7 on 9 November after the batch completed and preservation checks passed.
- [x] **Native processing-log progress implemented and verified offline:** bounded owned-log
  reads map frozen input UUIDs to curated historical Activity notes, including later overnight
  same-job Retry. Forty-two log cases and independent polling/guard checks pass. Loaded in rc3;
  real-log acceptance remain; observations never imply locally saved completion.
- [x] **Incremental native result retrieval implemented and verified offline:** frozen supported
  application references, fresh processed observations and two stable valid reads permit early
  saves; retained raw uses terminal recovery. Capture/idempotency and terminal-only cleanup
  checks passed, including independent review. Loaded in rc3; real served-image/finalization
  acceptance remain; unknown applications keep terminal retrieval (ADR 0155).
- [x] **Longer native job default activation:** fresh-only defaults are verified and loaded.
  The user's six saved native four-hour defaults were explicitly updated to 24 through the
  versioned local API. All other provider fields/profiles, credentials and frozen jobs were
  preserved; no remote allocation was changed or started. Receipt is recorded with rc3.

- [x] **9 November MVP follow-up implemented and verified offline:** Undo split; spoken-word playback
  following in the Word and timing editor's saved-text preview; manual Checked/Needs review marks with progress;
  bounded parallel UCloud uploads/downloads with accurate Activity stages; compact recording/format
  export selection and named ZIP folders; combined word/timing selector with Edit word; automatic
  same-job UCloud status reconnection after temporary connection errors. The assembled regression
  gate passed 158 tests, with separate browser and independent-review evidence. Packaging and
  activation are recorded in the [rc2 checkpoint](docs/verification-2026-11-09-mvp-rc2.md)
  (ADRs 0147–0152); real-provider and real-user acceptance remain open.

- [ ] Prepare a non-technical colleague's MVP focused on usability, editing and export. Hide
  unfinished features for this release, including passage Re-transcribe, Topics & Sentiments
  and Research Report; keep existing data and recovery accessible. CBS Runtime remains parked.
  The release profile is implemented; main keeps the full profile and the release candidate
  selects MVP. Complete synthetic release checks,
  real-device/provider acceptance under the appropriate separate scope, and an NVivo walkthrough.
  The [9 November verification checkpoint](docs/verification-2026-11-09-mvp-rc1.md) records
  the layered unit/browser gates and the remaining real acceptance limits.
  [MVP plan](docs/work-plans/mvp-2026-11-11.md) · [Tester guide and feedback](docs/mvp-tester-guide-2026-11-11.md) ·
  [NVivo test plan](docs/work-plans/mvp-nvivo-test-plan-2026-11-11.md).
  - [x] Update the colleague walkthrough for released MVP 0.1.0: timing-independent manual
    saves/splits, explicit installed-app/shortcut instructions, feedback form and separate
    optional NVivo, cloud-handoff and maintainer-only backup/deletion acceptance.
  - [x] Release profile mechanism and MVP hide list (ADR 0145): `--profile`/`CBS_TRANSCRIBER_PROFILE`/
    packaged `release.json`, `GET /api/features`, `data-feature` hiding, server refusals and open
    recovery routes. Synthetic unit and MVP browser walkthrough checks pass on `claude/mvp-profile`.
  - [x] Prepare and verify the local MVP rc1 packaging branch with `release.json` = mvp.
    Packaged-profile tests, the MVP walkthrough and isolated installation smoke pass.
    Main stays full. This is the historical rc1 checkpoint; current user distribution is the
    published v0.1.0 wheel plus matching constraints, as described in README.
  - [ ] Complete real-device/provider/backup/NVivo acceptance. A fresh monolithic full-suite
    run on the exact packaged RC remains distinct from the documented layered verification.

## Reliability and existing workflows

- [ ] **CBS Runtime preparation and batches (T85e/T85f/T86) — stability before PII integration.**
  User priority, 11 November. On 20 November the user added Runtime implementation and testing
  to the post-v2 plan: it opens the way to other transcription models and to the AI-assisted
  coding work. Live preparation and paid test jobs still need approval each time.
  **20 November: the user deleted the old Runtime folder on the UCloud project drive**, which
  held the retained `runtime-busy` preparation lock described below. The app's local records
  of that failed preparation (job evidence, cleanup obligations, readiness stamps) are
  unchanged. **Confirmed the same afternoon with the user's OK** (one read-only lookup through
  the app's bounded file API, nothing created or changed): the `cbs-runtime` parent folder,
  this version's runtime folder (`whisperx-3.8.6-cu128-a8f866300a50`), `.prepare-lock`,
  `.in-use` and `stamp.json` are all absent. The only library has no Runtime records, so nothing
  needs reconciling: a clean slate. The controlled live-test plan is
  [runtime-live-test-2026-11-21](docs/work-plans/runtime-live-test-2026-11-21.md); every
  credit-using step needs the user's OK. **21 November, user direction:** once the Runtime is
  stable (live test passed), extend it to also host the smaller LLM expected for AI-assisted
  qualitative coding (see that roadmap item), reusing the Runtime's preparation, MIG allocation,
  owned folders, Activity and cleanup rather than a separate hosting path. The morning branch implements verified exact host/shell stamp
  validation and PREPARE=1 legacy-reader/retained-lock refusal before new job submission
  (ADR 0172), plus the separately reviewed configured-model preparation plan (ADR 0173).
  These fixes do not reclaim the historical lock or establish live Runtime acceptance.
  Live preparation/jobs and generation activation remain held. The branch adds exact acquired
  lock-release guards without reclaim (ADR 0168), with synthetic ownership-change regressions.
  [Investigation and disabled-generation review](docs/proposals/post-mvp-runtime-preparation-2026-11-10.md).
  Resolve the live `runtime-busy`
  preparation failure safely: the retained lock's recorded owner is terminal, but neither safe
  removal nor an isolated replacement generation is established. The isolated-generation work
  is a disabled prototype, not a deployed fix. Preserve the failed job's evidence and remote
  cleanup obligations. Then validate real multi-file Runtime completion, one Activity/allocation,
  per-file results, late admission, Stop, recovery and owned-folder cleanup; measure model reuse,
  MIG memory, throughput, latency and cost. Do not start a paid job or discard remote material
  without the appropriate authorization. Keep its models deselected by default; prioritize
  stability and testing of the native UCloud Transcriber and the rest of the application.
- [ ] **Native Transcriber (T105).** Fixed 1g MIG for new native Transcriber and CBS Runtime
  requests is implemented and tested offline; existing frozen jobs retain their allocation.
  User testing and a public-audio run show
  successful short batches on several models; this does not validate every model, recording
  length or workload. Verify remaining models and larger workloads, timing/speaker outputs,
  Stop/recovery, cleanup and resource use. Do not silently fall back to a full GPU.
- [ ] **Post-MVP smart transcription scheduling — strategy discussion.** Expand the batch-
  splitting review into a scheduling policy: when to keep one shared UCloud job and when to
  split a selection across two or more 1g MIG jobs; whether shorter recordings should run
  first, using duration/estimated work rather than compressed file size; and whether users
  need explicit priority or original-order controls. Compare time to first usable transcript,
  total completion time, model compatibility/reuse, startup and queue overhead, transfer
  bottlenecks, memory and cost. Discuss allocation/budget limits, balancing long recordings
  without starving them, progress and per-file recovery/cleanup across multiple jobs.
  Preserve already-frozen jobs and avoid duplicate transcription during retry/restart.
  Automatic splitting/sorting is not implemented; agree a measured strategy and separate
  architecture decision before implementation. This discussion stays outside MVP.
  On 12 November the user asked to design parallel MIG splitting, CPU fallback and availability
  together. Check 1/2/3/4-mig.1g products (counts of identical 1g devices) and verified scheduler
  status semantics as lower-bound signals, not summed/reserved capacity. Compare same-model
  total queue/startup/ASR/alignment/speaker/transfer time and cost before any fallback.
  [Published CPU/GPU evidence and limits](docs/evaluations/whisperx-cpu-gpu-speed-2026-11-12.md).
  The user then agreed the v2 fallback rule for **both native and CBS Runtime**: usable,
  sufficient GPU hours means 1g MIG even if queued; known absent/insufficient GPU funding
  permits exact `cpu-amd-zen5-8-vcpu` only with CPU funding and model compatibility. Unknown
  funding is not zero. Affordability estimates/units and Runtime CPU preparation require
  reviewed implementation; this rule is documented, not enabled in the preview. See
  [funding policy (T145)](docs/decisions/0177-funding-based-compute-fallback.md).
- [ ] **Old UCloud obligations (T80/T80b).** Inspect and safely recover or explicitly dispose
  remaining legacy single-job and batch cleanup obligations; retain unresolved journals and
  user-visible controls. Emptying UCloud Trash or discarding unpreserved output requires the
  relevant explicit authority. Existing successful native Parakeet output recovery is complete.
  Design cleanup on the failed native batch's Activity card: verify terminal remote state,
  recover/preserve available outputs, then explicitly move only its owned run files to Trash.
  A local connection failure is not permission to delete a still-running job. A broader cleanup
  dashboard and automatic recovery of every historical job are deferred beyond this MVP.
  Review an easy select-all cleanup of `/1018810/CBS-Transcriber/`, preserving `cbs-runtime/`.
  Show folders/files, active and recoverable-job obligations, and exact exclusions before
  confirmation; define treatment of untracked folders and UCloud Trash. This is a review task,
  not authorization to delete the current directory during development.
- [ ] **Missing-output Retry failed workflow.** Recovery first checks the same recorded native
  allocation and preserves completed results. If output is genuinely missing after terminal
  state is verified, design explicit confirmation for a new run containing only those files,
  retain lineage and reconcile cleanup of both allocations. An absent/unavailable read or
  uncertain submission must not silently resubmit ASR. Current recovery does not implement this
  missing-output resubmission step; bounded transient download recovery is a separate fix.
  Bounded same-allocation transient reads and curated failed-member diagnostics are now
  implemented (ADR 0162), with eleven-sibling preservation, partial-buffer, Stop, guard and
  malformed-diagnostic regressions. Same-allocation existing-output recovery and verified
  cleanup passed the user's real rc6 acceptance on 10 November; missing-output resubmission
  remains open.
- [x] **MVP UCloud handoff acceptance — one Windows overnight batch, 12 November.** The user
  closed the app while a 12-file native job queued/processed overnight, reopened the preview
  and confirmed explicit Retry succeeded. Read-only local evidence shows the
  same recorded allocation, all 12 members completed, 12 newly saved results and the prior
  12 retained (24 total); the observed remote state is SUCCESS. This qualifies that served
  Windows batch, not early retrieval while running, every failure phase, or other devices.
- [ ] **Automatic startup reconnect — loading and real acceptance (T141).** On reopening,
  resume monitoring/retrieval for already-submitted native singleton/batch allocations with a
  confirmed saved remote ID. Preserve saved siblings and frozen identities, back off bounded
  transient reads and keep unmet authentication/ownership obligations visible. Explicit Stop/
  Cancel stays authoritative. Do not create replacement jobs, replay uncertain submissions,
  steal preparation locks or discard unresolved output. Historical failures and cleanup-only
  actions are separate policies. Implementation passed its final 76-case gate and independent
  review, then the assembled candidate passed 193 cases. Earlier 444/one-skip evidence remains
  source-qualified; this item now tracks loading/real acceptance. It is not loaded yet.
  Historical generic failures and fresh singleton errors already curated to generic
  errors remain manual; no error-text inference is used to grant automatic recovery.
- [ ] **Activity and transcription audit (T104/T108/T109).** Complete the remaining deep audit
  and integrated verification of batch grouping, admission, per-file progress, Retry/Stop,
  uncertain submission, cancellation, cleanup and restart. The user has confirmed grouped
  import/inspection and preparation, active preparation cancellation, Clear finished, and
  recording/project deletion in their test scenarios. Large-history/concurrent performance and
  untested recovery paths remain open. Routine batch-requested inspections now appear within the
  request or allocation in synthetic checks; validate the display in a real batch after activation.
- [ ] **Task dependency composition audit.** Media preparation and original-backed native
  transcription are intentionally independent; the current twelve-file overlap has a healthy
  local checkpoint and source review found no conflicting-write defect. Complete combined
  resource measurements, delete with two active readers, move after Stop, arriving native
  results during export and shutdown/recovery checks. See the
  [scoped dependency audit](docs/audits/task-dependencies-2026-11-10.md); existing per-component
  tests do not establish every combination or representative throughput.
- [ ] **Manual transcript editing performance.** Audit edit/save, splitting, merging, deletion,
  speakers, keyboard, playback and History on synthetic short and large transcripts; measure
  p50/p95 and preserve versions, Unicode, timing and concurrent-edit safety before optimizing.
  Speaker rename and assignment avoid a full redraw in verified safe cases; cached hints avoid a
  duplicate full-result read; opening merge or a free-text edit avoids a full redraw when no editor
  or draft is open. Measure real use. Project snapshots also reuse decoded active-result data only
  when the exact stored mirror matches; all output files are still regenerated and snapshot failures
  still surface. Continue the wider editing audit. For the implemented Working policy, profile the bounded
  role-history validation, including duplicate checks inside one transaction. A bounded memo now
  reuses only completed canonical payload checks after hashing current bytes; metadata and role
  authority remain freshly checked. The earlier 9 November save pilot identified delays;
  the final rc4 100/600-passage audit resolved its measured subset. Larger-history and
  representative-hardware work remain; see the
  [audit](docs/audits/mvp-final-editing-speed-2026-11-09.md).
  Review the existing current-Working version
  lookup's broad metadata projection and bound it without changing preview or export behavior.
  [Audit and benchmark plan](docs/work-plans/manual-transcript-editing-performance.md).
  The [23 November rc3 candidate check](docs/verification-2026-11-23-rc3-editing.md)
  covers matched 600- and 1,200-passage server and browser pairs. The first 1,200-passage
  round reduced median Save from 6.987 to 3.963 s and Split from 5.375 to 3.839 s until
  controls were ready. Larger histories, measurement on the installed build and representative
  hardware, and the long-history Undo scan remain open.
- [ ] **Preparation and passage-repair acceptance (T87d/T87f/T90e/T90f).** New inference uses
  immutable originals independently of browser playback; remote compressed-input preparation
  and bounded older-recording cloud repairs are implemented offline. Validate real codecs,
  timebases, resource/cost behavior, long recordings, MIG repair timing quality, per-clip
  recovery, cancellation and stale Apply. Do not claim real quality from mocked tests.
- [ ] **Large original-inclusive exports.** Validate real multi-GB browser downloads, temporary
  disk use, cancellation and filesystem links on Windows/macOS. Chunked backend staging/ZIP
  checks and small synthetic browser exports do not establish those resource limits.
- [ ] **Large-batch media preparation.** Local worker/FFmpeg throughput, CPU and memory were
  measured synthetically and an adjustable local limit added (ADR 0146); representative real
  recordings and video remain. Compare optional UCloud preparation for larger batches, including
  upload/download time, privacy approval, remote cleanup, resource charges and recovery, before
  choosing a policy.
- [ ] **Very large library stress test — requested 20 November, after MVP v2.** Check that the
  app does not crash or hang with a very large set of recordings, using a public Hugging Face
  speech dataset (any language) in a throwaway library. Choose the dataset with a verified
  licence, pinned revision and hashes; download it outside the repository, never in routine
  tests. Import its reference transcripts as results so scale costs no transcription; transcribe
  only a small subset. Cover upload, media preparation, lists, Activity, editor, search, export,
  backup/restore and deletion. A synthetic 1,204-recording test exercised lists, search,
  Activity and ZIP export on Windows. The recording-list fix is in the local rc3
  composition. On 1,204 synthetic recordings the verified API projection took 0.41–0.60 s,
  compared with 10.91–12.63 s before the change. Separate browser rendering and human
  check H15b are still open.
  [Measurements](docs/verification-2026-11-23-rc3-scale.md).
- [ ] **Local processing concurrency across task types.** Implemented on
  `claude/local-concurrency` (ADR 0146, T111): Settings → Local Processing → Parallel local
  tasks with automatic media = logical CPUs − 2 in 2..8, inspection = CPUs/2 in 2..4, API 6 and
  admission 2 (user decisions 6 November), live claim
  gating, a separate two-worker native lane, coalesced idle polling and an admission prefilter
  for intents still waiting on inspection. On a shared 4-core laptop, 16 synthetic files had
  playback ready 20–47% sooner, and the claim lock dropped from 31–81% to 22–32% held
  ([evaluation](docs/evaluations/local-concurrency-2026-11-05.md)). Remaining: independent review,
  a quiet-machine and a 6+ core rerun, video-heavy batches (libx264 threads), interactive
  responsiveness while saturated, and whether inspection's 50 ms cancellation poll should relax.
  Local WhisperX timing stays one recording at a time pending memory measurements.
- [ ] **Original inspection latency.** Measure full-file hashing, full audio decode and concurrent
  preparation separately on representative recordings. Evaluate whether native UCloud submission
  can safely use a lighter source-integrity receipt without weakening timeline, retry, recovery or
  provenance guarantees, while keeping full decoded evidence where it is needed. Evaluate doing
  inspection during import and combining compatible reads/decoding with playback preparation;
  avoid simply adding another full decode that competes for the same resources. See the
  [inspection and media performance plan](docs/work-plans/original-inspection-performance.md).
  A synthetic cold-cache check confirms concurrent inspections share one decoder identity
  observation; real end-to-end latency and throughput still need measurement.
- [ ] **Local file and restore boundaries (T98/T107/T110).** Recoverable deletion staging,
  collision-safe restore publication and bounded/cancellable FFmpeg discovery were integrated
  and tested synthetically. Complete remaining crash, platform, filesystem and live acceptance;
  keep unresolved deletion/cleanup visible. Real Cloud Files, Mac and power-loss behavior are
  not established by those tests.
- [x] **Project deletion in Activity — decided 20 November, post-v2.** Testing MVP v2 the
  user deleted a 12-recording project; the Delete dialog could still be closed while the final
  step ran, so the open project stayed visible until a refresh removed it. Today the open
  dialog drives deletion (it re-sends Delete each second while jobs stop; closing it abandons
  the request). User decisions: Delete hands off at once to an Activity item "Deleting project
  …" with stages (stopping jobs, removing from the library, removing files); the server owns
  the deletion, independently of the dialog. Only that project becomes inactive (greyed,
  "Deleting…", not openable or editable); a user inside it lands on the project list; the rest
  of the app stays usable. Activity offers **Keep project** until the point of no return
  (stopped jobs stay stopped). If the app closes before that point the project is kept and
  Activity notes the unfinished deletion; nothing resumes automatically. After it, deletion
  stays permanent and recovery relies on backups ("Recently deleted" and automatic pre-delete
  backups were not chosen). No quick fix for v2: the full version only, in post-v2 work. Needs
  its own ADR (job semantics); apply the same model to recording deletes where it fits.
  **Implemented on `claude/deletion-activity` (ADR 0190, V250–V253, T160, B500–B503):** a
  server-owned task with the three stages and Keep project; the point of no return is the
  SQLite Delete commit, with owner UCloud release receipts now written in that same
  transaction and Trash only afterwards; only that project is inactive (UI, and server 409
  `project_deleting` refusals); early close keeps the project and leaves an
  *interrupted* note via a small receipt folder (no schema change); nothing resumes. Synthetic
  backend scenarios (Codex A1–M1 where feasible), new browser cases and the existing deletion
  suites pass. Recording deletes keep the dialog-driven flow (it would need recording-level
  inactivity in every route and editor). Evening independent review on
  `codex/night-deletion-fixes-20261120` reproduced and fixed existing backup-capture admission
  overlap, premature Keep/COMMIT claims and a Keep-before-Stop reserved-control race
  (B513-B515, V254-V256); R5 lost-Trash-reply tests now cover Runtime and native owners.
  The corrected frozen backend inventory has 376 distinct passes/two skips; the final fixed
  source passed all 22 relevant browser cases. Independent source/backend review is Go; see
  [the night verification receipt](docs/verification-2026-11-20-night-deletion.md). The earlier
  deep-path harness failures are retained (B516); production path guards are unchanged.
  Still unmerged/unloaded at this component checkpoint; whole-browser and real-device/UCloud
  H24 acceptance remain separate. Future backup Activity UI (T161) remains a proposal.
- [ ] **Library module review.** Review library creation, selection, switching and deletion;
  paths, backup/restore and interrupted-operation recovery; accessibility and navigation between
  Library and Settings. Record actual defects and distinguish implementation, mocked tests and
  real Windows/Mac/UCloud acceptance. **Post-MVP branch:** Remove from list and its explanation
  are retired; old hidden entries remain accessible with Open/Delete without changing stored
  preferences. The deletion preview clears the floated note, separates paths/counts and keeps
  the typed-name field full width. Wide/narrow browser checks preserve confirmation, blockers
  and file-ownership safeguards. No real library was deleted by these tests.
  The simplified UCloud backup flow uses a uniquely
  configured project and a location selector; exact destination and recovery fencing remain
  internal (ADR0139). Broader integration and real transfer acceptance remain open.
  Initial findings and simplification priorities: [Library audit](docs/audits/library-module-2026-11-03.md).
  Browser pages now carry a server-instance marker on API and direct media/download requests;
  stale marked tabs are refused. Unmarked local clients and browsers omitting both Origin and
  Fetch Metadata remain unfenced. Validate real launcher/two-tab behavior, Firefox/Safari and
  download handling across a switch before treating multi-tab isolation as complete (ADR 0136).
- [ ] **Permanent library deletion.** Implemented on `claude/library-delete` (ADR 0144,
  V183): read-only private-copy preview, blockers for unsettled local/remote work, exclusive
  external project folders deleted and shared/unverified ones kept, typed confirmation bound
  to a fingerprint rechecked under the target lease, reservation outside the target, Store and
  launcher reopen fence, same-volume staging, startup recovery and Retry in the Library dialog.
  Part of the 11 November MVP: the preview, blockers, results and recovery rows use plain
  language for non-technical testers (what is deleted, what is kept and why, how to resolve).
  - [x] Synthetic tests: read-only preview (including WAL-only rows), current/open refusal,
    blockers, shared/mismatched/missing/linked externals, peer project folders containing the
    target, stale nested-file identities/names/metadata in both library and external trees,
    stale fingerprint, crashes at every
    boundary, in-request rollback, post-commit retry, old/newer schemas, browser dialog flow.
  - [x] Independent review on 9 November: peer-folder overlap and nested-inventory
    confirmation defects fixed (B373), including the follow-up Windows extended-path check.
    The 38 synthetic deletion tests pass; the documented platform and sharing limits remain.
  - [ ] Real Windows and macOS acceptance on synthetic libraries, including Cloud Files /
    OneDrive folders, network volumes, read-only files and files held open by other programs.
  - [ ] Known limits (ADR 0144): older app versions, other OS users and unregistered libraries
    are outside the fence and sharing check; no power-loss durability claim.
  [Earlier protocol and Windows lease evidence](docs/work-plans/library-removal-recovery.md).
- [ ] **Whole-app accessibility acceptance.** Test the application with actual screen readers,
  including navigation, dialog names, focus and recovery from errors. Automated accessible-name
  and keyboard checks do not establish real screen-reader acceptance.

## Backups and data governance

- [ ] **Post-MVP Dropbox backup destination — evaluation.** Review Dropbox for storing
  verified complete library backups and their restore receipts. Compare API transfers with a
  Dropbox-synced backup folder; consider setup, interrupted transfers, storage limits, retention
  and integrity-checked restore. Keep the active working library local; this is backup/restore,
  not active cloud hosting or multi-device synchronization. Implementation is not yet decided.

- [ ] **Real backup and restore acceptance (T91/T91c).** The working library stays on the
  computer. Library → Backups implements capture, UCloud connection/upload/resume, isolated
  restore to a new library and manual retention controls with synthetic API/browser checks.
  UCloud locations require explicit whole-library approval; changing the destination requires
  renewed approval and preserves the original identity of any frozen transfer.
  The local capture parent is selectable; a same-device warning is shown and a best-effort
  Windows check can identify the same filesystem volume. Hardware independence is not established.
  A Windows test-library capture and local restore verified four projects, 12 original recordings,
  12 saved results and versions, and 16 note versions. The same UCloud upload resumed its existing
  operation but refused a transfer origin outside the approved list. The backup and exact operation
  are preserved. Validate approved transfer, download, receipt, restore and retention, plus
  larger Windows libraries and macOS.
  [Verification](docs/verification-2026-11-23-rc3-backup-and-reconnect.md).
- [ ] **Backup Settings and Activity.** Integrated in the local rc3 candidate
  ([ADR 0192](docs/decisions/0192-backup-in-settings-with-activity.md), T161): the Settings → Backup tab and Library → Backups → Create backup hand a local backup to one
  server-owned Activity task with stages, copy counts, Stop until it is being saved, and a
  verified/stopped/failed outcome; its own incomplete copy is removed or named, and a restart
  reports interrupted backups without resuming them. Synthetic backend and Chromium checks
  pass, and Windows local Activity capture passed with 12 public originals. Remaining:
  real-device acceptance (H21: large libraries, removable and cloud-synced folders, crashes,
  macOS) and separately verified UCloud status in Settings (UCloud transfer
  stays in Library → Backups, hidden in MVP, unchanged). Explicit destination approval,
  restore isolation and manual retention confirmation are unchanged. The Restore flow checks
  earlier or external receipts against local or UCloud package bytes; this is a transport
  check, not a restore test. Older database-only copies remain available only as advanced
  recovery when present. Automatic schedules are not implemented. See the
  [Settings design](docs/work-plans/weekend-2026-10-31.md#backup-controls-in-settings--design-review).
- [ ] **Institutional storage and privacy.** Agree UCloud data classification and transfer
  approval, account/drive access, TLS and token scope, storage encryption and key/receipt custody,
  audit logging, retention, holds and withdrawal with CBS IT/DPO. Real transfer is not verified
  by a configured destination. Windows extended-attribute/cold-backup limitations remain:
  [options and evidence](docs/testing/windows-backup-ea-options.md).

## Language, enhancement and research

- [ ] **Project and recording language.** A project default and per-recording overrides for new
  transcription, timing, edit realignment and repairs are implemented with Danish/English first
  and a searchable standard base-language catalog. Validate real providers and model-specific
  support; standalone processing keeps the saved local-language fallback when unset. Preserve old projects and
  frozen requests, respect backend-supported and fixed-language models, group incompatible
  native language choices separately, and carry provenance through backup/restore. A blank
  native profile means use the UCloud application/template default, not guaranteed detection.
  Per-model language controls are hidden in the compact model list; saved values and frozen jobs
  remain unchanged. Machine selectors have been removed under the fixed-MIG policy.
  [Implementation plan](docs/work-plans/project-language-default.md).
- [ ] **Automatic language (T84).** General automatic language for enhancements, follow-ups and
  repairs, including mixed-language behavior, remains incomplete. CBS Runtime has explicit
  experimental full-ASR Auto; do not present a configured language as detected or infer missing
  evidence. Preserve saved explicit choices and frozen jobs.
- [ ] **Existing-result enhancement batching.** Design and implement a bulk action for adding
  word timing and/or speaker labels to existing results, with compatible allocation grouping,
  per-recording progress, cancellation, recovery and cleanup. Shared Runtime batching exists,
  but it does not guarantee one allocation per whole selection. [Review](docs/proposals/t84-enhancement-batching-review-2026-10-30.md).
- [ ] **Re-transcribed passage review tags.** Mark newly re-transcribed passages in the new
  result so users can find, review and re-evaluate changed wording; define how review state
  persists through editing, comparison, subsequent versions and export without implying approval.
- [ ] **Passage review acceptance.** MVP now has Not checked, Checked and
  Needs review marks, a visible checked count, persistence and explicit edit/structure semantics
  (ADR 0148), verified with synthetic backend/browser/restore checks. Validate real-user use;
  Next to review now visits Not checked/Needs review, skips Checked and wraps independently
  of model hints, with focused browser and draft-safety verification.
  The Show filter (All / Not checked / Needs review / Checked / Uncertain / Missing word timing)
  is implemented and browser-verified; Next to review follows it (T148). Real-user acceptance
  and coordination with any future re-transcription tags remain open.
- [x] **Word timing in review and Activity — decided 20 November, post-v2.** Testing MVP v2
  the user found passages with missing or rebuilt timing hard to locate, and a per-passage
  "Add word timing" job vanished from Activity when it finished (finished realignments are
  hidden as routine; the ready timing is added only when the recording is next open with no
  draft or dialog). User decisions: in results meant to have word timing, a passage missing it
  is automatically **Needs review**, so Next to review finds it (untimed-by-design results, such
  as speakers-only, are not flagged); when timing is added or rebuilt for a passage it becomes
  **Needs review** again, even if it was Checked, with the reason shown ("timing added");
  finished word-timing jobs stay in Activity with their outcome (added, ready until the
  recording is opened, not added because the passage changed, or failed) until cleared.
  Automatic marks need a truthful system actor and their own ADR (passage review is manual
  metadata today, ADR 0148); preserve the passage-hash binding, stale-Checked rule and drafts.
  **Implemented 20 November on branch `claude/timing-review` (unmerged; ADR 0191, T162,
  V243/V244, B510–B512):** automatic marks are derived when review progress is read, never
  stored; the researcher's mark on the exact current content always wins. New timing versions
  record the passages they timed (`timing_added`); older timing raises no mark. The reasons show
  beside the status, the summary counts them, and Show/Next to review find them. Finished word
  timing stays in Activity with its outcome; the worker's own message is no longer replaced by
  "Complete"; passages being timed leave the Repair all count at once. Synthetic backend,
  restore and browser checks pass; real-library acceptance is H15c. Merged into
  `claude/post-v2` at f2ea4ea after root review.
- [x] **Review status dropdown shows Uncertain and Timing missing — decided 20 November
  (evening), post-v2, amends ADR 0191 and the review-hint display.** Testing the MVP v2
  preview, the user asked to remove the separate "Review suggested" tag. Instead the passage's
  status dropdown itself shows **Uncertain** (visually highlighted) for passages flagged by the
  model's uncertainty hints, and **Timing missing** for passages that lack word timing. When a
  passage is both uncertain and missing timing, **Uncertain wins**. These are automatic states
  shown in the dropdown, not marks the researcher set; keep ADR 0191's rules: derived, never
  stored as review rows, the researcher's own mark on the exact current content wins, and
  untimed-by-design results are not flagged. Implemented at `9b56e20` on the isolated
  dropdown branch (V257, T163, ADR0191 amendment): timing added retains Needs review and its
  reason; Show/Next and dotted text uncertainty are preserved.58 focused browser cases passed,
  including explicit same-status manual override, stale Checked with complete/missing timing,
  disabled derived options, no implicit writes and native keyboard focus. Cached Spylls host/
  child imports are a test qualification (B517), not an installed-worker claim. Composed locally
  with deletion at `5482338`; final composition gates remain separate and nothing was loaded.
- [ ] **Too-short passage bounds: retiming pilot — decided 21 November.** The user answered
  "Yes, small pilot" to Codex's option 2 (`_handover\codex-2026-11-21\cbs-night-short-window-design-20261120.md`):
  "Add word timing" may correct a too-short passage's saved start/end from its own aligned
  words, only when every word aligns completely and in order, searched within the gap between
  the neighbouring passages (at most 60 s, never across a neighbour, never storing the search
  window as timing). The previous version stays in History and Undo restores it; partial or
  ambiguous output changes nothing and keeps its evidence. Needs its own admitted,
  versioned bounded-retiming policy, ADR, Apply/receipt generation and the six synthetic
  oracles in the design note. A concrete Per og Kim example is still wanted for real-audio
  acceptance.
- [x] **Word-timing editor — decisions 21 November.** Build from Codex's prototype f140a34:
  the ordinary Text editor plus start/end boxes for the word at the cursor. Edited times are
  part of the passage draft: **Save passage saves text and times together**, Cancel discards
  both, one History version, Undo restores it. When the cursor is between two words or on a
  space, **the boxes show nothing**. Still to settle while building: selections spanning words
  and one-character words (conservative defaults, shown to the user).
  **Implemented for local rc3 verification (source `07d9bbd`, ADR 0195, V272, T164,
  B552–B557, B582–B585).** Start (s)/End (s)
  boxes just below the textarea replace the ordinal word dropdown and its saved-text preview.
  Settled defaults: a selection spanning several words shows nothing; a selection within one
  word shows it; a one-character word (one grapheme, such as "i" or an emoji) is selected by
  the caret on either side of it, since it has no inside; a longer word needs the caret inside
  it. New or changed draft words start empty (the tooltip explains that saving may still keep a
  replaced word's time, ADR 0166). Typed times follow their word through spelling fixes and are
  dropped with a notice when it is deleted or split. Save passage uses a new edit-v4 route only
  when times were typed; older routes are unchanged. Shown in MVP. Synthetic backend and
  Chromium/Windows browser checks pass; real IME, screen reader, Mac/Firefox and real-user
  acceptance remain open.
- [ ] **Passage editor and word-timing redesign.** Review moving the global edit-mode and
  realignment controls into each passage's editor, and redesign how word timing is displayed
  and edited. Assess usability, keyboard access, draft preservation, timing accuracy, undo/history,
  partial or missing timing, and how edits interact with explicit alignment jobs; preserve the
  user's deferral of automatic second-pass alignment.
  The saved-text playback preview and compact ordinal/word timing dropdown with Edit word were
  implemented and verified, and are now replaced by the cursor word-time boxes (ADR 0195); this
  does not complete the broader editor redesign.
  The user rejected the duplicated preview in Text mode. Its removal, Text Replay and
  double-click opening Text are implemented, browser-verified and loaded in rc3.
  **Post-MVP replay in the Text editor:** follow/highlight spoken words directly within the
  editable passage while replaying it, without a separate duplicate playback panel. Preserve
  the draft, typing focus, cursor/selection and undo; use available valid word timing without
  inventing times for new words or blocking editing. Review scrolling and responsiveness on
  long passages. **MVP v2 (user decision 12 November):** highlighting is on by default with no
  setting; a differing draft keeps highlighting provably unchanged prefix/suffix words and
  never times new or changed words; Follow playback waits while typing (T149). Browser-verified
  on Chromium/Windows; Mac/Firefox/IME/screen-reader and real-user acceptance remain. This does
  not settle the broader editor design.
  **Released MVP 0.1.0:** the fresh timing-independent manual policy (ADR 0166) preserves proven
  unchanged/replacement intervals and leaves inserted or ambiguous words untimed, without
  blocking wording changes. ADR 0156 retains the older conservative generation for history.
  Automatic timing estimates and the broader redesign remain post-MVP;
  any future estimate must be labelled and distinct from aligned/verified timing.
  **User design direction, 20 November (post-v2):** (1) the word-timing editor should look like
  the ordinary Text editor, plus two boxes for the start and end time: the user puts the
  cursor on a word and the boxes show that word's timing, editable in place (replacing the
  ordinal/word dropdown). (2) The dotted underline of uncertain words should also show while
  the Text editor is open, drawn in its highlighting surface behind the textarea; keep it
  distinct from the spelling redesign's underline (for example dotted vs wavy) so both can
  mark the same word. Preserve draft, caret, native Undo, typing focus and late-reply fences.
- [x] **Realignment alignment-hint provenance — local child only.** ADR 0169 now seals the
  fresh host policy and invalidates whole-passage source alignment-fit hints after actual
  timing replacement/adoption, including omitted words. Exact legacy transforms, raw evidence,
  Working fences and lost replies remain. Structural carry and contained authority checks are
  covered. The complete child gate is **5,574 passed, 26 skipped**, independently reviewed;
  the earlier 393-case evidence gap was closed by a frozen rerun. No historical backfill or
  automatic alignment. Cloud historical transformation and ordinary merge full transformation
  remain `not_checked`. Branch `codex/post-mvp-timing-hints` stays unmerged and uninstalled.
  [Verification and limits](docs/verification-2026-11-10-timing-hints.md).
- [ ] **Track and roll back manual edits.** Make changes such as text edits, merges, splits and
  deletions easy to inspect and roll back. Existing result-version history provides a starting
  point; Undo merge now restores proven unmodified merges while keeping unrelated current edits
  (ADR 0135); Undo split is also implemented and verified (ADR 0147). Individual deleted-passage
  restoration passed lifecycle/API/browser/portable checks and is loaded in rc3. Broader passage-level undo
  remains open; preserve other
  edits, timing, speakers, concurrency safety and an auditable history.
  User direction 12 November: one **Undo last change** whose label names the next step
  (instead of separate undo buttons), undo recorded as history, a Git-like change list with
  word-level diffs and later a branch view of results/versions with the Working marker.
  19 November the user chose every recommended answer of the
  [design proposal](docs/proposals/history-undo-2026-11-18.md). Slice 1 is implemented on
  `claude/undo-last-change` (ADR 0180, T150): toolbar **Undo last change** / **Redo** with live
  labels, exact predecessor restoration of every saved content change, one Redo until the next
  change, reach to the result's start; review marks stay outside Undo. Slice 2 (ADR 0181,
  T151) provides the History change list, word-level "What changed", undone/redone tags and
  "Revert this change" for proven older merges, splits and deletions. Slice 3 (ADR 0182,
  T152) draws results as branches with Working and undo markers. The user's 20 November
  decision exposes all three in MVP v2 and keeps the per-passage controls as well. Code is
  implemented and the rc2 package preserves that profile; broader real-user/history workflow
  acceptance remains separate. The earlier pending-choice/hidden-MVP description is obsolete.
- [ ] **Review passage re-transcription.** Discuss the value of the current function and whether
  it needs redesign, including provider and model choice, context around the passage, result
  comparison, user review and how it fits the overall editing workflow.
  **Parked by the user 12 November:** same-model re-runs add little. If revived, prefer the
  NVIDIA Parakeet UCloud model (`nvidia/parakeet-tdt-0.6b-v3`) only where the catalog confirms
  the passage language, warn on identical model/language, and offer per-passage suggestions
  that start as Needs review, never automatic replacements.
- [ ] **Spelling integration and acceptance — ported to v2, hidden in MVP.** The user decided
  "Hidden in v2" on 19 November: the local DA/en-GB preview above is re-applied to v2 (branch
  claude/spelling-hidden, T155, [ADR 0185](docs/decisions/0185-local-passage-spelling.md))
  behind the `spelling` release-profile feature: on in full, hidden and refused in MVP while
  existing private vocabulary stays readable/removable. Settings rows moved to 12/13 (B464).
  Danish suggestions remain disabled after measured timeouts. Remaining: the user's choice to
  show it, broader languages, representative quality/platform checks, dictionary updates and
  institutional licence review (Spylls ships an MPL-2.0 LICENSE although its metadata says MIT).
  Preserve recording language, private vocabulary, draft/timing guards and saved opt-out. The
  [original design](docs/proposals/offline-spelling-2026-11-05.md) retains the wider future plan.
  On 12 November the user proposed underlines directly in the Text editor with on-demand
  alternatives instead of the separate spelling panel.
  - [x] **Redesign of 20 November** (branch claude/spelling-redesign, T156, still hidden in
    MVP). The user asked for recording vocabulary inheriting project words, automatic
    dictionary downloads in Activity and on-demand suggestions without fixed boxes, then chose
    Ctrl+click (⌘+click on macOS; Alt+Enter from the keyboard), no line under the text box with
    coloured words and a remembered Spelling checkbox, popover actions recording/project/Ignore,
    and a Settings → Spelling tab. Implemented with [ADR 0186](docs/decisions/0186-recording-spelling-vocabulary.md)
    and [ADR 0187](docs/decisions/0187-spelling-activity-downloads-and-on-demand-suggestions.md);
    synthetic unit, browser and MVP checks pass (B488, B491 fixed before commit).
  - [x] **Double-click trigger** (branch claude/spelling-trigger, 20 November): the user asked
    again for suggestions by double-clicking a word. Double-click on a coloured word in the open
    editor now opens the popover beside the browser's own word selection; other words do
    nothing extra, the editor-opening double-click is unchanged, Alt+Enter stays and Ctrl+click
    was removed (ADR 0187 update, B489, B490). Independent review of the redesign found no
    further defect to fix; Chromium browser tests pin the trigger and the distinct
    wavy/dotted styles on one word.
  - [ ] Not yet verified: macOS double-click and Option+Enter, Firefox/Safari double-click
    word selection and Undo after a chosen suggestion, screen-reader announcements, a
    real-library look, real dictionaries with researchers, and downgrading to an earlier v2
    build once recording words exist (ADR 0186 records that limit). Post-v2 work (user,
    20 November): spelling stays hidden in v2 and the v2 → main merge does not wait for this
    branch. A later uncertainty underline in the Text editor should use its own layer that
    leaves pointer events to the textarea; ADR 0187 keeps spelling's wavy line below it.
- [ ] **Passage re-transcription source and transfer.** The current passage action uses the
  browser-playback audio and can upload a whole prepared WAV to UCloud even for one passage.
  Plan original-source bounded clips, preserving timeline/word ownership, source evidence,
  explicit provider policy and review behavior; assess privacy, costs and recovery before rollout.
- [ ] **Anonymisation — parked pending CBS Runtime stability and a later user decision.**
  The app should eventually start the model on demand using the UCloud API key; review,
  redaction/pseudonym and mapping-custody decisions remain open. No adapter/workflow
  implementation is authorized while parked. User direction on
  11 November: use the UCloud hosting project's anonymisation model. Its catalogue identifies
  **PII-Tracer** (`perplexity-ai/PII-Tracer-vLLM`), a PII token classifier with a `/v1/pii`
  adapter; evaluate it on Danish/English transcripts and design the Transcriber integration.
  Historical synthetic hosting checks do not establish interview accuracy. The direct-identifiers-only
  scope question remains unanswered. No production in-app detector, model or review workflow
  exists. Local synthetic examples remain review/evaluation scaffolding. The branch expands explicit
  entity-label linking and export-canary tests; a six-case DA/EN fixture exposes detection gaps,
  not real accuracy. [Offline evaluation](docs/evaluations/anonymisation-offline-2026-11-10.md).
  Turn the reviewed design and synthetic demo into a workflow with evaluated detectors, researcher
  span review, separate derived results, pseudonym decisions and export-leak tests. Resolve
  key custody/retention and scope before using real research data; text-only redaction does not
  anonymise audio or metadata. [Design](docs/proposals/anonymisation-local-prototype-2026-10-30.md) ·
  [model survey](docs/evaluations/anonymisation-models-2026-10-30.md).
- [ ] **Version history and research provenance (T43/T43h).** Working version designations,
  history/conflict workflows and scoped local/API/native/Runtime producer capture exist. Validate
  result restoration, conflict choices and reports with researchers; test large-history report
  scaling and resource limits. Widen environment/artifact coverage where justified without
  inventing historical evidence. Assess actual methods and journal requirements.
  [Research workflow](docs/research-workflow-roadmap.md).
- [ ] **Working-transcript defaults.** User authorized implementation of automatic Working
  designation for the first result and processing successors, rollback to earlier versions, and
  a choice when editing an older version. These flows are included in released MVP 0.1.0;
  its current database schema is 19. Focused synthetic migration, manual edits, timing
  choices, Undo, browser prompts, producer/authority and isolated restore checks passed. Broader
  corruption, report and concurrency verification and real-provider acceptance remain open.
  Refreshed merge/conflict previews now use their own fresh Working authority while keeping
  unsaved editor drafts separate; synthetic stale-refresh and closed-preview checks cover B376.
  See ADR 0133 for the scope of the synthetic evidence.
- [x] **Existing sole-result Working designation — current-schema implementation (T147).**
  ADR 0179 extends the intentionally held ADR 0141 path to reviewed 18/18 and 19/19 pairs,
  retaining untouched-result, restore, native-authority, owned-snapshot and startup fences.
  Builder and independent adversarial evidence cover 171 cases; B454 adds unreadable-evidence
  refusal without aborting the pass. This supersedes the earlier current-schema engineering
  gap, not its historical MVP 0.1.0 limitation. Real-library acceptance remains separate;
  automatic job-finish re-evaluation was not adopted. See [ADR 0179](docs/decisions/0179-existing-working-schema19.md)
  and the [earlier review](docs/proposals/existing-sole-working-review-2026-11-04.md).
- [ ] **NVivo and REFI-QDA (T88a/T88c).** The acceptance pack is synthetic; test actual
  transcript imports on Windows and Mac and implement/validate REFI-QDA codebook/project
  interchange for supported codes, passages and memos. Do not claim compatibility until tested.
  [Acceptance pack](docs/nvivo-refi-validation-pack.md) · [roadmap](docs/ai-analysis-nvivo-roadmap.md).
- [ ] **Danish/English quality and licensing (T19/T19b/T89e).** Run approved representative
  reviewed-interview and FLEURS baselines, with text accuracy, timing, speakers, code-switching,
  wall time, memory and cost; compare Edda, Røst, Whisper and relevant candidates. Profiles,
  fixtures and desk licence evidence exist, but model quality and CBS licence clearance do not.
  [Licence review](docs/danish-model-licence-review-2026-10-27.md).
- [ ] **Calibrate routing and uncertainty (T61/T63).** Measure local CPU/GPU and UCloud
  thresholds on representative interviews; compare uncertainty flags against reviewed corrections
  before changing thresholds, shares or ceilings. Review real UCloud JSON/CSV export behavior.

## Installation, publication and service

- [ ] **Claude MVP usage documentation — assigned to Claude by the user.** Claude will add
  its own scoped token/cost evidence to the [development usage ledger](docs/development-usage.md).
  The Codex MVP baseline is documented, with Standard speed confirmed and cost estimates
  separated from actual billing. Keep future milestones/phases current under AGENTS.md;
  leave Claude and combined AI totals pending until its figures are supplied.

- [ ] **Installation and deployment (T92/T94/T106/T139).** The local preview branch now
  documents a uv-managed Python environment in `<home>/CBS Transcriber/App/.venv`, installed
  from a versioned GitHub wheel and matching runtime constraints without Git. The earlier
  uv-tool route remains documented for existing installations and method-specific removal.
  The new private Windows probe installed all 135 packages and passed module help, four core
  smoke checks, private COM/no-overwrite and stopped-environment repair. Full environment
  removal was blocked by automatic approval review and remains unverified; the environment
  is retained. No manual preview install is required from the user. Mac, actual shortcut
  clicks and the colleague's Access denied issue remain unverified.
  Installed-package/source shortcuts and the locked source fallback exist. A fresh isolated
  Windows environment resolved and installed all 135 packages and passed all four installed-
  app smoke checks; a native COM launcher was verified in a disposable folder without an
  actual Desktop/Start menu install or click. Validate clean Windows x64, Apple Silicon
  macOS 14+ and Intel Mac installations, updates, removal and shortcuts on actual systems;
  signed installers, Mac signing/notarization and managed-device permissions remain open.
  Public v0.1.0 assets and anonymous installation are available; the user's uv-tool startup on
  this PC is verified. Old development history remains private in the separate repository.
- [ ] **Remaining licensing and institutional decisions.** The clean public MVP repository/
  release and its publication/font checks are complete. Choose the app reuse licence and
  resolve remaining institutional/model terms. The existing private history retains
  the removed reference bundle. The actual CBS font's embedded terms permit CBS/collaborator
  distribution; retain that separate notice and other third-party attributions. No broad app
  open-source licence has been selected. [Audit](docs/audits/licensing-2026-10-30.md).
- [ ] **CBS pilot and onboarding.** Agree eligible users, support and operating owners, allowed
  processing, data lifecycle, representative acceptance, onboarding and cost/credit allocation;
  review installation/launch/troubleshooting guidance and the proposed CBS Work skill/plugin.
  [Service proposal](docs/cbs-deployment-proposal.md).

## Planned later or explicitly deferred

- [ ] **Possible later version: UCloud speaker-count options — undecided.** The Transcriber
  app accepts minimum/maximum speaker counts (and speaker merging). Offering them in Settings
  or per request is an idea for a later version only; the user has not decided it.

- [ ] **Native UCloud batches: earlier uploads, upload/batch limits and full-GPU choice —
  decided 19 November.** Done on `claude/native-batch` (T153, ADR 0183; synthetic mocked
  checks only): each file uploads as soon as its own original inspection passes, failed files
  are dropped with their own outcome and only the UCloud submission waits for every file;
  Settings → UCloud has "Files uploaded at once per batch" (1–8, default 4; upload speed
  depends on the network, not CPU cores) and "UCloud Transcriber batches at once" (1–4,
  default 2); Activity's batch card uses plain wording ("Waiting in UCloud's queue for a GPU ·
  12 min", "Transcribing on UCloud · 5 of 12 files reported done · last log 14:05").
  Remaining: a live UCloud batch run, and the full-GPU choice — when no 1g MIG is free,
  Activity asks per batch whether to use a full GPU, stating its higher credit use; nothing
  switches without that click (amends ADR 0124). Detecting "no MIG free" needs UCloud capacity
  or queue evidence before activation.
  Full-GPU ask: [ADR 0184](docs/decisions/0184-native-full-gpu-ask.md) remains unintegrated.
  Supplied-data capacity/never-started helpers are implemented and reviewed synthetically;
  [verification and activation limits](docs/verification-2026-11-20-native-gpu-helpers.md).
  (V233/T154; activation is not implemented or reviewed). User decisions 19 November: ask as soon as two
  capacity reads agree the MIG is full and a full B200 is free (no queue-time threshold),
  preferably before the POST; a queued MIG is cancelled and proven never started first; Retry
  returns to the MIG; hidden in MVP v2. Read-only live evidence is permitted once implemented;
  cancelling a queued job or a paid full-GPU test each need separate approval.

- [x] **Post-MVP branch README simplification and uninstall guide.** The short main guide
  links focused install/review/export/provider/storage/development guides retaining unique detail.
  Install pins, copy mode, shortcuts, uninstall and retained data/credentials remain explicit.
  Local outgoing and incoming README links pass; this is not a new published release.

- [x] **MVP v2 preview readiness — resumed 12 November (T139/T140).** Finish the local-folder
  guide and isolated Windows installation/removal probe, modestly enlarge transcript/editor
  text and run a focused immutable candidate gate under the actual MVP profile. Preserve
  draft, caret, selection, overlay wrapping, playback and existing review/export/recovery.
  The assembled candidate qualifies 56 unique MVP browser passes, eight native recovery
  passes and 23 backend/launcher passes, retaining original failures and scoped remedies.
  Two fixture expectations were corrected and a one-line post-alignment Follow fix keeps
  words visible after a short-window resize. **MVP v2 preview 1** is loaded using the
  existing development environment after preservation checks, without a user reinstall or
  public-main/release change. The user's overnight batch remains in the same library with
  exact stored allocation/parameters; Activity offers explicit Retry to reconnect it.
  Follow-up font feedback is implemented: passage/editor 15 px, passage actions 12 px with
  24 px minimum height, sidebar text 13 px/headings 14 px. An independently reviewed 23-case
  MVP browser gate covers wrapping, action bounds, sidebar, playback, caret/undo and speakers;
  it overlaps earlier coverage and is not added to its distinct total.
  [Verification and limits](docs/verification-2026-11-12-mvp-v2-preview.md).
  [Preview plan and UCloud scheduling findings](docs/work-plans/mvp-v2-preview-2026-11-12.md).

- [x] **Preview feedback refinements — 12 November (T140/T142).** Balance passage text at
  15 px, enlarge passage actions and Recording details/Speakers, move first-use connection
  setup into a separate dialog and highlight genuinely missing UCloud/HF credentials. Keep
  typing/focus/undo, responsive wrapping, existing-key retention, pending-save and late-reply
  fences. Independent review passed: 39 fresh MVP/9 fresh full-profile cases, with explicit
  unaffected-case qualification and original failures retained. Verified bytes are served on
  preview ports 8765/8766 without restarting the app or refreshing a user tab.

- [ ] **Post-MVP explanatory-text review.** Review feature-description and information boxes
  across the app; shorten long text, remove repetition and use plain, action-focused wording.
  Keep essential instructions, consequences and recovery guidance clear; move secondary detail
  to help where useful. Review the proposed copy for non-technical users before applying it.
  Three reviewed boxes (Library, preparation, concurrency) are shortened on the branch;
  broader wording/frontend choices await colleague feedback.

- [x] **Post-MVP branch empty start-page change.** When the current library has no projects, show
  just the CBS Transcriber logo/title in the main area. Use the user's
  [supplied CBS logo](docs/design-assets/cbs-logo-white-on-blue.png) as the artwork reference,
  retaining the requested reversed colours: blue artwork/text on the light background.
  The unchanged official blue vertical SVG supplies that treatment without editing the PNG.
  Promotional headings, waveform, feature labels and welcome panel are removed. Existing
  project creation stays accessible at narrow widths too; synthetic browser checks pass.
  Existing project-overview layout is unchanged; installed MVP remains unchanged.

- [x] **Deactivate direct OpenAI backend — decided 11 November.** **Active in MVP v2** (checked
  23 November): the morning branch is merged into the v2 line and `require_service_available`
  refuses new direct OpenAI requests in every profile; the "activation pending" text below is
  historical. Real-user acceptance follows the human release checklist. Remove OpenAI-hosted
  service setup and models from ordinary user choices, and refuse new direct OpenAI
  transcription requests. Retain the shared OpenAI-compatible client/protocol code, endpoint
  configuration and relevant tests for a possible future UCloud transcription service using
  that protocol. Do not delete saved results/history, credentials or configuration evidence.
  Define existing queued/running work and Retry behavior before activating this change;
  hiding the UI alone is insufficient. Keep native UCloud Transcriber unchanged and do not
  activate the separately deferred Custom/UCloud vLLM UI. Morning implementation has component
  review Go under ADR 0170 and complete assembled synthetic coverage. Activation is still
  pending. Local raw/chunk finalization and accepted-request drain
  remain available, while missing-part requests refuse without new direct service calls.

- [x] **First-launch front-page connection setup — requested 11 November.** **Active in MVP v2**
  (checked 23 November; merged with the morning branch). Real first-time-user acceptance
  remains (human checklist). Let a first-time
  user enter the UCloud API key and an optional Hugging Face access token directly on the
  front page. Reuse the existing Settings validation and secure credential-storage flow;
  keep credentials editable later in Settings. This is first-use onboarding, not a prompt
  on every empty project/library. UCloud-only setup starts no preparation. The later explicit
  HF choice queues access/configured-model preparation in Activity (ADR 0173), without an
  inline download-capable Check call. Implementation/source review are complete on the
  isolated morning branch; complete backend coverage and 67 relevant browser cases pass,
  with no installed update. Activation and real-user acceptance remain open.

- [x] **HF access and configured-model preparation in Activity.** **Active in MVP v2** (checked
  23 November): saving a token returns 202 and starts the check in Activity; the user's preview
  showed "Token valid · terms accepted" on 20 November. Live model download/preparation
  acceptance remains. Explicit nonempty primary
  HF Save should check access and model terms, then prepare configured local/UCloud assets
  with cache reuse, Stop, token/plan fences and truthful partial states. Use the existing
  durable remote owner and a versioned model plan beyond base readiness; do not claim all
  models ready from a base stamp. Parent task is server-lifetime; remote recovery stays
  durable. Implemented/source-reviewed under ADR 0173; complete synthetic regression is
  verified on the branch, with no live preparation/download acceptance. At least
  one enabled CBS Runtime profile is required for cloud preparation; native flags do not
  select those weights. No live downloads/jobs are part of development checks.

- [ ] **Stable install-latest entry point.** Morning branch adds a dependency-free bootstrap
  resolving one approved stable GitHub release's versioned wheel, matching constraints and
  digests, retaining uv copy mode and project isolation. Mocked installer and complete branch
  regression pass. Current MVP commands stay available;
  the stable script URL is not active until separately authorized release publication.

- [ ] **Optional post-MVP idea: branded shortcut icons.** Consider automatically assigning a
  CBS Transcriber icon to Windows/macOS launchers. Implementation is undecided; this is an
  investigation candidate, not a committed feature or MVP requirement.

- [x] **Post-MVP connected-provider selectors.** **Active in MVP v2** (checked 23 November):
  selectors offer only providers whose saved connection passed its check (`connection_ready`).
  Real-provider acceptance remains. Offer providers in project/recording
  transcription selectors only after successful configuration and a connection check in
  Settings. Fresh presets or missing credentials alone must not appear ready to use. Bind
  readiness to the saved connection; recheck after relevant settings/credential changes.
  Keep Settings available for setup and preserve saved results, active jobs and recovery
  controls when a provider is unconfigured or temporarily unavailable. Verify fresh installs,
  successful setup and later connection changes with mocked checks; no test transcription.
  Implemented on the separate branch under ADR 0167; focused backend/browser/restore checks
  and independent review pass. The full backend inventory is verified; the additional
  provider browser regression has 25 verified parent cases; the morning changes now have
  67 distinct qualified browser passes. Real-provider/device acceptance is separate.

- [ ] **Language-focused transcription model catalogue — longer term, added 11 November.**
  Expand the existing catalogue rather than introduce a parallel list. Cover the languages
  relevant to CBS users, keeping Danish/English prominent; agree the wider priority list.
  Record pinned model/runtime, supported task/backend, language evidence, licence/access
  terms, word timing/speaker capabilities, measured quality, memory, throughput and cost on
  the app's 1g MIG setup. Separate candidate/sized/tested models and preserve limits for long
  recordings and batches; parameter count or multilingual branding alone does not prove fit.
  Keep ordinary model choices simple. Representative quality/MIG tests need separate scope;
  no model downloads or provider runs are authorized by this roadmap item.

- [ ] **AI-assisted qualitative coding, topic generation/search and NVivo roadmap
  (T38/T88b/T88c).** Added to the later-release discussion on 12 November: suggest codes for
  transcript passages, generate candidate topics/themes across selected recordings, and
  find supporting passages by topic. Status: exploratory, not started; outside the current
  MVP v2 commitment unless reprioritised. The manual codebook/evidence prototype exists;
  it does not implement model-driven coding, topic generation or topic search. Agree the
  workflow, inference provider and researcher review before implementation; preserve source
  transcripts and distinguish AI suggestions from approved coding. Summaries, aspect
  sentiment/stance and expanded NVivo exports remain separate planned parts of this roadmap.
  [Roadmap](docs/ai-analysis-nvivo-roadmap.md). **21 November:** the user wants the smaller LLM
  for this work hosted on the CBS Runtime once the Runtime is stable (see the CBS Runtime item);
  the 20 November inference-tier context (1g MIG, full B200, UCloud AI platform tiers; a
  model-independent coding engine behind an OpenAI-compatible SDK interface; access control and
  safeguards) still awaits the rest of the user's text before a planning note is written.
- [ ] **Automatic second-pass word alignment (T93a).** Explicitly deferred as later fine-tuning;
  do not add a default extra inference pass now.
- [ ] **Hviske 5.3/6 engines (T89d).** Planned separate architecture support is explicitly held.
- [ ] **Custom/UCloud vLLM UI and provider speech release (T29/T29b).** Deferred; include real
  speech profile/allocation, sizing and provider release before downstream adoption. Existing
  vLLM work remains distinct from native Transcriber.
- [ ] **Provider-wide capacity policies.** Deferred design for UCloud Transcriber/vLLM and other
  provider limits; coordinate native batches with the smart transcription scheduling review
  above. Do not change frozen jobs or imply that current lanes are a global limit.
- [ ] **OpenAI passage re-transcription.** Bounded clips and explicit per-request cost confirmation
  remain planned. PDF and ELAN/EAF export are candidates only when a concrete workflow needs them.
- [ ] **Future cloud-hosted projects and multi-device sync.** Outside the current project scope;
  UCloud backup/restore does not imply synchronized working libraries.

## Completed evidence

Bulk recording-language selection and explicit Deselect all are included in released MVP
0.1.0; representative real-user acceptance remains separate.

Fresh model-selection defaults and Danish labels are included in MVP 0.1.0; actual model
quality/coverage acceptance remains separate.
Follow playback and uncertainty controls start enabled; manual scrolling no longer turns off
Follow playback. Both controls remain manually switchable.

The earlier integrated unit and browser gates, synthetic preservation checks, user-reported
Activity/deletion tests, native MIG evidence and the scope of their limitations are recorded in
the [historical worklist](docs/archive/TODO-archive-2026-10.md), linked audits and verification documents.
Treat their commit IDs and deployment claims as dated evidence, not proof of the currently
running version. Update this worklist when implementation or acceptance actually changes.

## Project settings save

- Staged name, classification, language and storage changes use explicit Save/Cancel;
  synthetic tests cover conflicts, move confirmation and uncertain replies. Included in MVP
  0.1.0; full real-user settings/move acceptance remains open.
