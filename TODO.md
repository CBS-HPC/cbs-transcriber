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

## Colleague MVP — 11 November 2026

Readiness was brought forward to **10 November**. Follow the
[current finish/freeze/acceptance plan](docs/work-plans/mvp-ready-2026-11-10.md); finish the core
editor/export reliability changes, load one labelled tested build and reserve tomorrow for
acceptance and blocking fixes. **MVP 0.1.0 was activated and verified**, including exact served
asset bytes and unchanged database/originals. That development instance has since been stopped
normally for the user's fresh uv-tool installation. The new installation completed with copy
mode and serves MVP 0.1.0 from its installed tool environment; functional acceptance remains
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
  following inside the open passage editor; manual Checked/Needs review marks with progress;
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

- [ ] **CBS Runtime preparation and batches (T85e/T85f/T86) — parked by user.** Do not resume
  CBS Runtime work or start its UCloud jobs until the user requests it. Resolve the live `runtime-busy`
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
- [ ] **Splitting large batches across MIG allocations.** Evaluate when and how one large batch
  should use multiple 1g MIG allocations. Measure throughput, startup overhead, memory, queue
  time and cost; design allocation limits and per-file recovery. Automatic splitting does not
  currently exist, and a policy should follow measured evidence and a separate decision.
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
- [ ] **MVP UCloud handoff acceptance.** Verify that an accepted native job continues after app
  shutdown and that relaunch plus explicit Reconnect/Retry retrieves results from the same saved
  allocation, without another submission. Keep automatic status reconnection within a session;
  do not add general automatic startup recovery until its separate design is accepted.
  The synthetic actual-queue shutdown/reopen/Retry check passes; a real handoff remains unverified.
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
- [ ] **Library module review.** Review library creation, selection, switching and deletion;
  paths, backup/restore and interrupted-operation recovery; accessibility and navigation between
  Library and Settings. Record actual defects and distinguish implementation, mocked tests and
  real Windows/Mac/UCloud acceptance. The simplified UCloud backup flow uses a uniquely
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

- [ ] **Real backup and restore acceptance (T91/T91c).** The working library stays on the
  computer. Library → Backups implements capture, UCloud connection/upload/resume, isolated
  restore to a new library and manual retention controls with synthetic API/browser checks.
  UCloud locations require explicit whole-library approval; changing the destination requires
  renewed approval and preserves the original identity of any frozen transfer.
  The local capture parent is selectable; a same-device warning is shown and a best-effort
  Windows check can identify the same filesystem volume. Hardware independence is not established.
  Validate a real UCloud transfer, download, independent receipt, restore and retention workflow,
  including separately hosted transfer endpoints, which remain blocked until explicitly trusted,
  plus real-library Windows and Mac restore. The user waived a new backup for this disposable test
  library; that does not waive preservation requirements for other libraries.
- [ ] **Backup Settings and Activity.** Implement the proposed Settings → Backup entry and
  durable, separately verified local/cloud status and progress using the existing Library flow;
  retain explicit destination approval, restore isolation and manual retention confirmation.
  The Restore flow checks earlier or external receipts against local or UCloud package bytes;
  this is a transport check, not a restore test. Older database-only copies remain available
  only as advanced recovery when present; creation is removed from the Library UI. Capture still has no Activity
  entry, percentage progress or cancellation, and a lost response can leave a partial copy.
  Automatic schedules are not implemented. See the [Settings design](docs/work-plans/weekend-2026-10-31.md#backup-controls-in-settings--design-review).
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
  Review filtering and coordination with future re-transcription tags remain open.
- [ ] **Passage editor and word-timing redesign.** Review moving the global edit-mode and
  realignment controls into each passage's editor, and redesign how word timing is displayed
  and edited. Assess usability, keyboard access, draft preservation, timing accuracy, undo/history,
  partial or missing timing, and how edits interact with explicit alignment jobs; preserve the
  user's deferral of automatic second-pass alignment.
  The saved-text playback preview and compact ordinal/word timing dropdown with Edit word are
  implemented and verified; this does not complete the broader editor redesign.
  The user rejected the duplicated preview in Text mode. Its removal, Text Replay and
  double-click opening Text are implemented, browser-verified and loaded in rc3. A single
  editable highlighting area remains later work. **Current update:** conservative preservation
  is implemented, verified offline and loaded in rc3 (ADR 0156):
  unchanged words keep exact times, replacement words inherit the original interval, inserted
  words stay untimed. Automatic timing estimates and the broader redesign remain post-MVP;
  any future estimate must be labelled and distinct from aligned/verified timing.
- [ ] **Legacy alignment-hint provenance.** Same-result realignment of an unmarked passage can
  retain older alignment-fit hints. Design an explicitly versioned remedy without changing
  frozen jobs or lost-reply recovery; explain individually suppressed manual-timing hints in
  the editor. Marked maps are protected under ADR 0137.
- [ ] **Track and roll back manual edits.** Make changes such as text edits, merges, splits and
  deletions easy to inspect and roll back. Existing result-version history provides a starting
  point; Undo merge now restores proven unmodified merges while keeping unrelated current edits
  (ADR 0135); Undo split is also implemented and verified (ADR 0147). Individual deleted-passage
  restoration passed lifecycle/API/browser/portable checks and is loaded in rc3. Broader passage-level undo
  remains open; preserve other
  edits, timing, speakers, concurrency safety and an auditable history.
- [ ] **Review passage re-transcription.** Discuss the value of the current function and whether
  it needs redesign, including provider and model choice, context around the passage, result
  comparison, user review and how it fits the overall editing workflow.
- [ ] **Spelling integration and acceptance — post-MVP, parked by user.** The local DA/en-GB preview above is implemented
  and independently tested; integrate only after user approval. Danish suggestions remain
  disabled after measured timeouts. Broader languages, representative quality/platform checks,
  dictionary updates and institutional licence review remain open. Preserve recording language,
  private vocabulary, draft/timing guards and saved opt-out. The
  [original design](docs/proposals/offline-spelling-2026-11-05.md) retains the wider future plan.
- [ ] **Passage re-transcription source and transfer.** The current passage action uses the
  browser-playback audio and can upload a whole prepared WAV to UCloud even for one passage.
  Plan original-source bounded clips, preserving timeline/word ownership, source evidence,
  explicit provider policy and review behavior; assess privacy, costs and recovery before rollout.
- [ ] **Anonymisation.** No production in-app detector, model or review workflow exists. Turn
  the reviewed design and synthetic demo into a workflow with evaluated detectors, researcher
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
  a choice when editing an older version. The implementation is merged and the app has restarted
  with the existing library on schema 18. HTTP readiness was verified; no user-visible acceptance
  or Working-version mutation was performed. Focused synthetic migration, manual edits, timing
  choices, Undo, browser prompts, producer/authority and isolated restore checks passed. Broader
  corruption, report and concurrency verification and real-provider acceptance remain open.
  Refreshed merge/conflict previews now use their own fresh Working authority while keeping
  unsaved editor drafts separate; synthetic stale-refresh and closed-preview checks cover B376.
  See ADR 0133 for the scope of the synthetic evidence.
- [ ] **Existing sole-result Working designation.** Automatically designate eligible existing
  recordings with exactly one untouched version. ADR 0141 is implemented on the isolated
  `claude/working-existing` branch with synthetic checks: untouched-passage evidence, restored
  library refusal, job/authority fences, owned-folder snapshot refresh with derived staleness
  recovery and bounded startup. Not merged or active: obtain independent review, then back up
  and accept on a real library. Job-finish re-evaluation was not adopted (next start covers it).
  [Earlier review](docs/proposals/existing-sole-working-review-2026-11-04.md).
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

- [ ] **Installation and deployment (T92/T94/T106).** The primary guide now uses a versioned
  GitHub wheel plus lock-derived runtime constraints through uv tool install, without Git.
  Installed-package/source shortcuts and the locked source fallback exist. A fresh isolated
  Windows environment resolved and installed all 135 packages and passed all four installed-
  app smoke checks; a native COM launcher was verified in a disposable folder without an
  actual Desktop/Start menu install or click. Validate clean Windows x64, Apple Silicon
  macOS 14+ and Intel Mac installations, updates, removal and shortcuts on actual systems;
  signed installers, Mac signing/notarization and managed-device permissions remain open.
  Public v0.1.0 assets and anonymous installation are available; the user's uv-tool startup on
  this PC is verified. Old development history remains private in the separate repository.
- [ ] **Publication and licensing.** Resolve the app licence, CBS fonts/logos, redistributed
  scripts/archives, attribution, model terms and history/publication policy before release.
  The user approves a clean public MVP repository/release; the existing private history retains
  the removed reference bundle. The actual CBS font's embedded terms permit CBS/collaborator
  distribution; retain that separate notice and other third-party attributions. No broad app
  open-source licence has been selected. [Audit](docs/audits/licensing-2026-10-30.md).
- [ ] **CBS pilot and onboarding.** Agree eligible users, support and operating owners, allowed
  processing, data lifecycle, representative acceptance, onboarding and cost/credit allocation;
  review installation/launch/troubleshooting guidance and the proposed CBS Work skill/plugin.
  [Service proposal](docs/cbs-deployment-proposal.md).

## Planned later or explicitly deferred

- [ ] **Optional post-MVP idea: branded shortcut icons.** Consider automatically assigning a
  CBS Transcriber icon to Windows/macOS launchers. Implementation is undecided; this is an
  investigation candidate, not a committed feature or MVP requirement.

- [ ] **Post-MVP connected-provider selectors.** Offer providers in project/recording
  transcription selectors only after successful configuration and a connection check in
  Settings. Fresh presets or missing credentials alone must not appear ready to use. Bind
  readiness to the saved connection; recheck after relevant settings/credential changes.
  Keep Settings available for setup and preserve saved results, active jobs and recovery
  controls when a provider is unconfigured or temporarily unavailable. Verify fresh installs,
  successful setup and later connection changes with mocked checks; no test transcription.

- [ ] **AI-assisted analysis and NVivo roadmap (T38/T88b/T88c).** The manual codebook/evidence
  prototype exists; shared UCloud/OpenAI analysis, researcher-reviewed coding/summaries,
  topic discovery, aspect sentiment/stance and expanded NVivo exports remain planned.
  [Roadmap](docs/ai-analysis-nvivo-roadmap.md).
- [ ] **Automatic second-pass word alignment (T93a).** Explicitly deferred as later fine-tuning;
  do not add a default extra inference pass now.
- [ ] **Hviske 5.3/6 engines (T89d).** Planned separate architecture support is explicitly held.
- [ ] **Custom/UCloud vLLM UI and provider speech release (T29/T29b).** Deferred; include real
  speech profile/allocation, sizing and provider release before downstream adoption. Existing
  vLLM work remains distinct from native Transcriber.
- [ ] **Provider-wide capacity policies.** Deferred design for UCloud Transcriber/vLLM and other
  provider limits; do not change frozen jobs or imply that current lanes are a global limit.
- [ ] **OpenAI passage re-transcription.** Bounded clips and explicit per-request cost confirmation
  remain planned. PDF and ELAN/EAF export are candidates only when a concrete workflow needs them.
- [ ] **Future cloud-hosted projects and multi-device sync.** Outside the current project scope;
  UCloud backup/restore does not imply synchronized working libraries.

## Completed evidence

Bulk recording-language selection and explicit Deselect all are integrated in the current
integration branch; activation and real-user acceptance remain separate.

Fresh model-selection defaults and Danish labels are implemented; activation and real model
acceptance remain separate.
Follow playback and uncertainty controls start enabled; manual scrolling no longer turns off
Follow playback. Both controls remain manually switchable.

The earlier integrated unit and browser gates, synthetic preservation checks, user-reported
Activity/deletion tests, native MIG evidence and the scope of their limitations are recorded in
the [historical worklist](docs/archive/TODO-archive-2026-10.md), linked audits and verification documents.
Treat their commit IDs and deployment claims as dated evidence, not proof of the currently
running version. Update this worklist when implementation or acceptance actually changes.

## Project settings save

- Staged name, classification, language and storage changes use explicit Save/Cancel;
  synthetic tests cover conflicts, move confirmation and uncertain replies. Live acceptance
  remains to be checked after the app update.
