# The seventeen hidden post-MVP tasks: current-code audit

10 November 2026. Source baseline: `708a27bef24430a7ce4e1a7e379f98f131f6dcd6`
on `codex/post-mvp-work`. Read against current `SPEC.md`, `TODO.md`, the decision index and
[the post-MVP plan](../work-plans/post-mvp-2026-11-10.md). This audit changes documentation,
not application behavior. An unchecked umbrella task is not evidence that its implementation
is missing. Historical branch names and dated line numbers are not activation instructions.

Status update, 11 November: item 9 was subsequently implemented and independently verified
on the isolated post-MVP timing-hints branch. Its dated original finding is retained below;
the [later receipt](../verification-2026-11-10-timing-hints.md) is authoritative for that fix.
The other engineering/acceptance gaps below remain open. Reviewing these seventeen areas
does not mean seventeen new product flows were implemented, or that the installed MVP changed.

The important distinction is between delivered behavior, remaining engineering, performance
evidence, actual device/provider acceptance, and decisions belonging to CBS or researchers.
None of the seventeen subjects should simply disappear, but several requests to implement
them from scratch are obsolete. The next steps below are bounded offline work; later live
acceptance is identified separately and has not been performed here.

## 1. Native early per-file retrieval

**Implemented; retain a narrow real-application acceptance gate.**
`native_batch.run_batch()` observes supported publication markers, checks stable exact output
bytes and saves completed members while the allocation is still running. It keeps a pending
member for terminal ownership and does not clean early (ADR 0155). Existing-output read retries
are also delivered (ADR 0162), independently of any new allocation. Tests in
`test_native_batch_incremental.py` cover running saves, unstable output, terminal-only cleanup,
Stop/shutdown, edited early siblings and preserved raw bytes. The download-recovery suite
covers one failed member beside eleven saved results.

**Next:** retain these regressions and prepare a short acceptance record for the exact served
application/model: first saved result before terminal state, edited sibling preserved, final
member saved, cleanup after terminal confirmation. The user's successful same-allocation rc6
recovery is evidence for that recovery scenario; it does not establish early publication on
every application/model. Do not implement another generic downloader.

## 2. App-close and UCloud handoff

**Implemented protocol; one real Windows overnight batch accepted on 12 November.**
`test_native_batch_polling.py::test_queue_shutdown_and_library_relaunch_explicitly_retrieve_the_same_allocation`
uses the actual queue shutdown, a reopened synthetic library and explicit Retry. It asserts
the original allocation is reused. The same file separates local shutdown from remote Stop
and refuses replay of uncertain submission. `test_native_batch_incremental.py` additionally
preserves early results through shutdown.

The user confirmed Retry after the overnight close/reopen. Local evidence retains the same
allocation and all 12 completed member results, alongside the 12 earlier saved results.
This is a scoped served-Windows handoff acceptance, not a claim for every device, transfer
failure or early-result phase. No agent submitted a new provider job for the test.

**Next:** retain the synthetic regressions and separately scope remaining device/failure
phases. General automatic startup recovery is a distinct user-proposed follow-up (T141),
not part of the current explicit-Reconnect implementation.

## 3. Media preparation and original inspection speed

**Still relevant performance work; the old two-worker baseline is obsolete.**
`audio_inputs.freeze()` obtains source hashes, first-stream/timeline evidence, full decoded
PCM/frame evidence and a second source check. `media.prepare_media()` separately prepares
browser derivatives. `original_inspection.py` caches the exact generation/epoch-bound source
observation. Its tests cover cache reuse, bounded hashing, replacements, cancellation, decoder
drain and I/O outside database/snapshot locks. These invariants constrain an optimization;
they do not prove the current path is fast enough.

**Next:** add an opt-in, capped synthetic measurement of hash/probe/decode/media stages and
warm reuse, with small WAV/compressed/video fixtures, CPU/disk/elapsed measurements and no
private paths in the report. Compare receipts before changing work. The older
[inspection plan](../work-plans/original-inspection-performance.md) still describes two
inspection/two media workers and proposes controls already shipped in ADR 0146. Earlier
inspection, shared reads, safe remux and a separately versioned lighter native policy remain
proposals; none follows from increasing thread counts.

## 4. Parallel local work

**Controls delivered; shared resource coordination and representative measurements remain.**
`local_concurrency.py` starts maximum media/inspection/API/admission lanes of 8/8/8/4 and gates
new claims. Automatic limits are CPUs minus two within 2–8 for media, half the CPUs within
2–4 for inspection, six API requests and two admissions. Existing work is never killed by
lowering a setting. Native, local inference, cloud enhancement and Runtime retain their own
lanes. `test_local_concurrency.py` checks limits, owner-only Cancel, live changes, drain,
FFmpeg reap and idle poll coalescing.

**Next:** benchmark the exact combined defaults with small mixed media/inspection/API fixtures,
then compare shared FFmpeg/CPU budgeting without changing defaults first. The
[4-core evaluation](../evaluations/local-concurrency-2026-11-05.md) found contention and
improved it; it explicitly does not establish quiet-machine, Mac, video-heavy, UI saturation
or real-provider behavior. Another settings control would duplicate delivered work.

## 5. Large exports

**Export lifecycle delivered; multi-GB/device acceptance remains.**
`export_tasks.py` owns independent server-lifetime preparation, frozen read selections, one
active task, three retained ready downloads, cancellation, descriptor ownership and shutdown
drain (ADR 0158). `exports.py` streams explicitly selected originals using bounded reads and
fresh identities outside shared locks. Tests in `test_export_tasks.py`,
`test_export_tasks_integration.py` and `test_export_originals.py` cover these contracts, source
races and early disconnect cleanup. Background preparation is not absent merely because
large-file acceptance is open.

**Next:** retain a small synthetic cancellation/disconnect/resource regression; define a later
Windows/Mac multi-GB download matrix with staging-space budget, held/linked originals and
interruption points. Do not allocate gigabytes to make this documentation audit look complete.
Restart-durable ZIP preparation would be a separate semantic change.

## 6. Backup lifecycle

**Capture and isolated restore exist; Activity-owned operations remain missing.**
`portable_capture.capture_library()` builds complete packages under a serving-lease/admission
gate and refuses active work. `portable_routes.py` calls it synchronously from the capture
route; it has no durable Activity task, progress/cancel endpoint or capture lost-reply
operation identity. `test_portable_capture.py` checks copied originals, publication refusal,
lease/gate safety and deduplication. `portable_restore.py` and its tests separately implement
isolated restore, target fencing and interrupted promotion/registration recovery. Legacy
`backup.py` is database plus originals inventory, not a complete media-body backup.

**Next:** design one durable capture operation's states, cancellation boundary, publication
receipt, browser retry identity and shutdown behavior before adding destinations or schedules.
Use tiny synthetic packages for each interruption. Restore receipts and verification-only
checks must not be described as successful real-library restore. Dropbox comparison can be
desk research; UCloud backup work remains parked.

## 7. Library and local file recovery

**Core implementation delivered; platform/storage boundaries remain.**
`deletion_files.py` journals/stages exact local paths before logical Delete and distinguishes
live-row rollback from absent-row cleanup. `library_deletion.py` has read-only preview,
reservation/lease fences, exact ownership and restart recovery. Their tests cover failed
cleanup Retry, uncertain boundaries, links/reparse refusal, read-only files, destination
recreation and crash-boundary simulations. `test_portable_restore.py` covers a separate
restore lifecycle. These are not blank features requiring wholesale reimplementation.

**Next:** keep deterministic tiny-file boundary cases and document a narrowly scoped actual
Windows Cloud Files/held-file test plus Mac/local-volume checks. Synthetic permission/reparse
conditions and injected crashes do not establish OneDrive, network-volume, process-kill or
power-loss durability. Do not use real libraries as fault-injection fixtures.

## 8. Multiple tabs and library switching

**Server-instance fencing delivered; browser/launcher acceptance remains.**
ADR 0136 binds browser requests and direct resource/download links to the current server
instance. `test_library_instance_fence.py` checks reads, mutations and resources;
`test_browser_library_instance.py::test_stale_tab_preserves_draft_and_fences_downloads`
checks retained drafts and stale downloads. Switch confirmation and export/capture inhibitors
have their own tests. Existing marking does not authenticate unmarked clients; omitted Origin
and request markers remain the documented local-client limit.

**Next:** use two small synthetic browser contexts to retain the current stale-response/draft
proof, then document real launcher/two-tab and Firefox/Safari/direct-download acceptance.
No cross-tab editing or automatic multi-device synchronization is implied.

## 9. Old alignment-fit hints

**Fixed on the isolated branch; not installed. Original 10 November finding follows.**

The fresh-only, host-sealed versioned remedy is complete under ADR 0169. Its full child gate
has 5,574 passes and 26 explicit skips, with independent review. It invalidates all old
alignment-fit word hints after actual timing replacement/adoption, including omitted words,
while preserving exact legacy history and lost-reply behavior. No historical backfill or
automatic alignment was added; cloud historical transformation remains `not_checked`.
`review_hints.current_hints()` binds cached hints to unchanged passage text.
`_current_alignment_hints()` suppresses word spans marked `manual_timing` or
`alignment_hints_stale`, but an unmarked legacy realignment can leave text unchanged and
therefore keep older source alignment-fit hints. The existing suppression is useful and
should not be removed. Text identity alone cannot prove a hint describes current timing.

**Original audit's next step, now completed locally:** review and implement the fresh versioned Apply policy and whole-passage
invalidation described in the proposal below. Test old frozen jobs, exact lost-reply
successors and partially suppressed spans before changing writers or captured validators.
A historical backfill would invent authority; a silent marker rewrite is not a quick fix.

The follow-up [backend trace and versioned remedy](../proposals/legacy-timing-hints-2026-11-10.md)
reproduced this for fresh unmarked local realignment, and separately for a partial replacement
omitting an old hinted word even when returned words are stale-marked. The task is not
redundant or limited to old libraries. A possible cloud writer/reconstruction difference is
source-traced investigation, not a second confirmed cloud failure. No production fix was
made during this audit.

## 10. Automatic language

**Partial primitives delivered; general support remains closed.**
`EnhancementOptions` and the public/remote admission boundaries distinguish supported
full-ASR Auto from existing-text enhancement/retranscription. Tests in
`test_auto_language_boundary.py` and `test_auto_language_admission.py` explicitly refuse
unsupported public Auto work before runtime/parent ASR. Configured-language receipts and
project/recording language freezing are delivered. A configured choice is not detection,
and one full-recording decision is not code-switching support.

**Next:** retain the refusal matrix and develop bounded synthetic source/decision/outcome
binding tests for the internal existing-text primitive. Resolve speech/text compatibility,
mixed language and translated-text behavior before public Auto alignment/follow-ups. Follow
[the batching/language review](../proposals/t84-enhancement-batching-review-2026-10-30.md);
do not resume parked Runtime implementation to complete this audit.

## 11. Batch enhancement of saved results

**Distinct missing product flow, with substantial reusable machinery.**
Existing exact-source enhancement freezing, independent result creation, follow-ups and
`runtime_store.py` compatibility/owner machinery are implemented. Current bulk transcription
with optional follow-ups is not a bulk action for selected saved results. Best-effort shared
Runtime admission is not a guarantee of one allocation for a complete accepted selection;
the proposal documents sealing/capacity/late-admission counterexamples.

**Next:** settle the allocation promise in a tiny offline slow-last-member/sealed-owner test,
then design one fixed-language, same-project bounded selection with exact result/revision,
frozen sources, per-file failures and explicit Apply/Stop/cleanup scope. Do not loop current
single-file HTTP endpoints or relabel native batch ASR as this feature. Runtime work remains
parked unless separately resumed.

## 12. Selected-passage retranscription

**Existing hidden implementation; original-backed clip/transfer redesign remains.**
`enhancement.retranscription_inputs()` selects saved passage bounds;
`run_retranscription()` appends a separate result and preserves the source. The UCloud test
`test_t71_ucloud_retranscription_runs_one_job_for_the_selected_passages` proves one mocked
job and unchanged source wording. However, that path calls `enhance_window()` without scoped
clipping, and the API worker uploads its prepared `audio.wav`. Selected recognition bounds
therefore do not establish selected-only network transfer or original-v1 bounded clips.

**Next:** inspect a tiny fixture's prepared-audio frame count and upload inventory, then
propose an explicitly versioned original-backed clip request with exact offset-once evidence,
per-target checkpoints and unselected-dictionary preservation. Decide usefulness/UX before
exposing it. Existing passage alignment/repair bundles are different operations and do not
prove retranscription already has this contract.

## 13. Research/provenance report performance

**Report and budgets delivered; large-history measurement and researcher needs remain.**
`research_reports._capture()` captures one bounded database snapshot; limits include 100
selections, 1,000 versions and separate 32 MiB payload/source/context budgets. Its tests
verify exact one-snapshot behavior, ownership, corrupted/oversized lineage and budget refusal
before large payload loading. Captured producer evidence is not reconstructed from arbitrary
current runtime files. These are integrity/resource safeguards, not a latency benchmark.

**Next:** create a capped synthetic 10/100/near-limit version benchmark, recording p50/p95,
CPU, peak memory and guard failures; avoid private transcript text and giant fixtures.
Profile before changing budgets/caching. Methods wording, journal expectations and broader
restoration/conflict presentation need researcher input separately.

## 14. Installation and shortcuts

**Versioned distribution and source/tool launchers delivered; device acceptance remains.**
The [0.1.0 receipt](../verification-2026-11-10-mvp-release.md) records the public clean snapshot,
anonymous asset/hash verification, isolated Windows uv-tool package installation, installed
smoke and disposable native COM shortcut verification. The guide's copy mode addresses the
observed Windows hardlink failure. `shortcuts.py`, `test_shortcuts.py` and
`test_tool_shortcuts.py` cover exact launch arguments, collision refusal and no app startup
while creating a launcher. An actual user-installed startup is also recorded.

**Next:** keep a fixture-driven guide/command check, then run the actual clean Windows,
Apple Silicon/macOS14+, Intel Mac, update/uninstall, OS-keychain and shortcut-click matrix.
Do not repeat an environment installation during an audit. Signing/notarization and branded
icons are separate decisions; default icons are not a broken shortcut.

## 15. Accessibility

**Useful automated coverage; whole-app screen-reader acceptance remains.**
`test_browser_dialog_accessibility.py` checks unique dialog title references, accessible
names, Escape/focus behavior, delayed library replies and keyboard conflict handling.
These concrete checks disprove a claim of no accessibility work, but Chromium automation
cannot establish screen-reader announcement or navigation usability on actual platforms.

**Next:** prepare a small task script covering project/library dialogs, review/edit/save,
Activity errors and recovery; record focus/announcement expectations and keyboard-only
outcomes in synthetic browser checks. Actual NVDA/VoiceOver and supported-browser acceptance
is a separate human/device session, not something to mark complete from role selectors.

## 16. Model quality and routing calibration

**Still relevant empirical acceptance; deterministic infrastructure is not quality evidence.**
Pinned model catalogs, configured/Auto language provenance, conservative timing retention,
speaker processing and FLEURS fixtures exist. `test_timing_policy.py`,
`test_language_provenance.py` and worker/provenance suites test retained facts and protocol
semantics, not accuracy. Five short FLEURS clips and successful provider completion cannot
establish Danish/English interview, mixed-language, speaker or word-time quality.

**Next:** prepare an inert approved-reference manifest and scoring rubric for text, timing,
speakers, silence/noise and code-switching; test scoring on tiny hand-written examples.
Real inference/download/provider/cost experiments need their own scope and authorization.
Do not tune routing/uncertainty thresholds from unreviewed outputs or mocked timings.

## 17. Institutional and service decisions

**Publication blocker wording is outdated; institutional decisions remain.**
The 10 November release receipt supersedes the 30 October licence audit's blanket claim
that publication is blocked: a clean public snapshot was authorized and published, old
history retained privately, and CBS font terms/notices reviewed. That does not select a
general app reuse licence or settle model terms, institutional privacy/storage approval,
retention, eligibility, funding or ongoing support. Provider approval in the app is a user
policy record, not CBS certification (`CONTEXT.md`).

**Next:** maintain an owner/decision/evidence register, separating app reuse, bundled assets,
model access/use, data protection/storage, support and costs. Record unanswered decisions
as unanswered. No code test, public repository or this engineering audit can supply CBS
approval or a legal interpretation; current official terms should be checked in the
separately scoped institutional review.

## Consolidation and smallest useful next changes

- Keep early retrieval, same-allocation recovery, handoff, concurrency, exports, deletion,
  switching and installation as delivered capabilities with precise remaining acceptance.
  Do not create duplicate implementation tasks for them.
- Keep missing-only resubmission separate from Retry failed, and saved-result enhancement
  separate from bulk ASR. Their new authority/state semantics need explicit design; see the
  [missing-output and owned-folder proposal](../proposals/native-failed-output-recovery-2026-11-10.md).
- Consolidate media/inspection and local-parallelism performance into one shared resource
  experiment. Keep a lighter source policy and playback remux as separate decisions.
- Keep existing package capture/restore separate from the missing Activity capture lifecycle.
- Good small implementation candidates are deterministic overlap regressions and bounded
  synthetic report/media benchmark tools. The later versioned hint remedy is complete on its
  isolated branch; preserve its legacy and unchecked-history boundaries. None of the open quality/platform/institutional gates justifies
  silently starting provider jobs, processing real libraries or synchronizing environments.

## Focused verification

The existing Python 3.13 environment was reused with `PYTHONPATH` pointed at this worktree's
`src`; no synchronization/install occurred. Eleven selected pytest nodes produced **14 passed
in 111.13 s**. The private JUnit receipt is
`%TEMP%/post-mvp-hidden-tasks-2026-11-10.xml`.

The slice covered early native save without early cleanup; one failed output beside eleven
saved siblings; actual queue shutdown/reopen and explicit same-allocation Retry; automatic
concurrency defaults and owner-only Cancel/drain; bounded original ZIP reads outside shared
locks, source races and cancellation; report snapshot isolation and pre-load payload budget;
public existing-source Auto refusal; and current-instance resource/read/write fencing.
All libraries/media and provider responses were synthetic/mocked. These checks establish
those existing contracts, not real-provider, large-file, device, accessibility, model-quality
or institutional acceptance. Concurrent branch work is being verified separately; this is
not a monolithic exact-release regression run.

One additional deterministic composition regression is now
`test_native_media_overlap.py::test_native_inspection_upload_and_results_do_not_wait_for_active_playback_reader`:
**1 passed in 17.54 s**. A real media lane holds an open original reader behind an Event while
the real original inspector/admission and mocked native allocation upload/save both results;
playback remains preparing until explicitly released. The worker then completes actual
three-second synthetic playback preparation and drains while the serving lease is held.
It asserts one allocation, one result per recording and exact retained original bytes.
This is a correctness interleaving, not a throughput or real-provider benchmark. Its first
run correctly encountered the concurrent connected-provider change's required-check refusal;
the shared fixture was updated to supply an explicit synthetic Settings-owned successful
check, and the rerun passed without a production overlap fix. Ruff lint/format checks pass
for the new test. The private receipt is
`%TEMP%/post-mvp-native-media-overlap-2026-11-10.xml`.
