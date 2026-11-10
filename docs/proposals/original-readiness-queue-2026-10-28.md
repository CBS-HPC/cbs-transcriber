# T87b — Original readiness and durable waiting selections

28 October 2026. **Implementation proposal; no persistence or queue change is delivered by
this document.** Extends [ADR 0075](../decisions/0075-common-original-transcription-input.md)
and the [common-original brief](common-original-input-2026-10-27.md). The implementation
owner must record the schema, queue-semantic and native-grouping decisions in a separately
numbered ADR before implementation. No ADR number is reserved here.

## Recommendation

Add one nullable original-inspection JSON column to recordings. Reuse existing jobs for
local inspection and for each selected recording/provider's waiting request. Keep waiting
requests `queued`; distinguish their non-executable phase with a strict, versioned params
document. Atomically turn a waiting request into its fully frozen executable job, retaining
its job ID. Only that transition may create native/runtime membership.

**Native grouping is a mandatory selection barrier.** All compatible native files selected
in one request wait for their original-inspection/preflight outcomes, then the eligible
members materialize together into one immutable native allocation (or one singleton).
Never split that selection into readiness cohorts. API and CBS Runtime requests may admit
ready members independently; runtime late admission keeps its existing ownership/CAS rules.

This supplies durable accounting without a new workflow service, dependency graph, request
table or replacement queue. A recording cache alone is insufficient: after browser closure
or restart it cannot reconstruct which providers, options, follow-ups and preparation consent
the researcher selected. New top-level waiting/materializing job states are possible, but
would require updating every active-work query; the existing queued/running states already
cover Activity, busy/deletion, project moves and library switching.

Whole-recording ASR and its requested whole-result follow-ups are the first slice. Explicit
whole-result enhancement may use the same admission mechanism only with its exact reviewed
source snapshot frozen at acceptance. The bounded repairs, passage re-transcription and
incidental edit realignment deferred by ADR 0075 stay on their existing paths.

## Current integration constraints

The reviewed working tree is implementing Stage 1 concurrently; function names are the
anchors, not line numbers.

| Existing seam | Required T87b change |
| --- | --- |
| `app.transcribe`, `plan_whisperx`, `features.prepare_followups` | Replace the all-recordings playback gate and synchronous whole-selection inspection with durable per-combination acceptance, then source-dependent preflight. |
| `audio_inputs.freeze/_hash` | Keep full hash/decode out of HTTP. Inspection uses cancellation through hash chunks and FFmpeg; the current `_hash` uses uninterruptible `file_digest` and must become cancellable for background inspection. |
| `Store._insert_job` | Keep its existing meaning. It invokes `runtime_store.attach` before INSERT and must never receive an unready request that happens to contain `batch_plan`. |
| `Store.add_transcription_jobs` | Preserve the fully materialized API and its all-or-none transaction. Extract/reuse native grouping inside the new materialization transaction; do not call this method separately for each ready member. |
| `Store.claim_job`, `JobQueue.run/process` | Exclude non-executable intents from ordinary claims. An admission worker must finish by queueing/failing/cancelling, not through the generic successful-process finisher that marks jobs completed. |
| `runtime_store.attach/_source` | Admit only a fully frozen original-v1 input; remove playback dependence for that explicit path. Keep old source serialization and legacy playback gating. Per-file source evidence stays outside allocation compatibility. |
| `native_batch` admission/run/retry and native singleton | Check the new frozen source/effective duration instead of playback for original-v1 only. Retain exact legacy guards for jobs without the policy. |
| `copy_recording` | Publish closed original, recording metadata and its inspection/playback jobs without exposing an incomplete upload as inspectable. |
| `JobQueue.process(media)` | Do not overwrite an existing `recordings.duration`; fill a missing container observation only. Original inspection never writes it. |
| `Store.add_result` | Preserve result/version, producer completion and exact frozen child insertion as one transaction, including idempotency and Trash behavior. |

The current latency violation is concrete: `app.transcribe()` calls `audio_inputs.freeze()`
for the entire selection before returning jobs; whole-result enhancement does the same.
V8 is not satisfied by putting that call outside `queue.lock`: it still blocks HTTP enqueue.
Likewise, moving only source decoding while retaining slow runtime/network preflight in the
acceptance handler does not establish bounded request latency.

`conflicting_job` currently treats every media job as a transcription conflict. A browser
job is no conflict for a validated original-v1 producer. Inspection jobs also must not be
mistaken for a provider duplicate. Waiting intents themselves **are** active requests for
duplicate detection; their frozen provider identity must be accessible without inference
params being complete.

## Minimal persisted contracts

The names below are proposed private contracts, not public API promises.

### Recording cache

Add nullable `recordings.original_input`. NULL means not inspected under this contract;
it says nothing about old results or old frozen jobs. A version-1 value contains:

- `version`, `generation`, `state` (`pending`, `running`, `ready`, `error`),
  `inspection_job_id`, monotonic `epoch`, and `updated_at`;
- optional import-observed `{sha256, bytes, format}`;
- on ready, the validated strict `audio_inputs` original-v1 receipt and its canonical digest;
- on error, an allowlisted error code and curated message, without decoder output or paths.

`generation` identifies this managed original's logical publication, not an invented
historical content hash. New imports hash while copying; only after close/flush may they
publish that identity. An old recording first receives a generation when fresh work requests
inspection; its first measured hash is explicitly a new observation. No migration opens
media, synthesizes earlier evidence, alters notes/transcript revisions or changes duration.

The cache is for **fresh admission**. A frozen executable job owns its own audio receipt;
subsequent cache errors/reinspection cannot rewrite that job or its runtime source identity.
Validate the cache against the supported policy/recipe/decoder contract before reuse.
Incompatible inspection evidence requires reinspection or an explicit refusal, never
relabelling. Already completed inference stages retain their existing no-inspection/no-model
finalization path.

Once an import-observed or inspected original hash is anchored for a generation, reinspection
must verify that same original. An externally changed file is an immutable-source error,
not a new generation adopted silently at the same recording ID. Decoder reinspection may
publish a new receipt/epoch for the same source hash; old executable jobs retain their own
previous receipt. NULL legacy rows get a newly observed anchor without invented history.

Use the existing strict receipt validator and bounded JSON limit. Reject unsupported versions,
unknown fields, malformed hashes, non-finite numbers and oversized envelopes. Keep credentials
out of both the cache and jobs, as today. API projections expose only the readiness fields
needed by the UI, not arbitrary job params.

### Source-inspection job

Use `kind='source-inspect'`, existing job states, and params binding `version`, recording
generation and inspection epoch. One active inspection per recording/generation is enforced
under `BEGIN IMMEDIATE` by the cache's authoritative job pointer. Repeated requests reuse it.
This does not need a general dependency table or an in-memory-only deduplication set.

Reuse Stage 1's strict whole-original inspection outside locks. Its success proves the
generic first-stream input contract; it does not promise native multitrack support or that
every backend accepts the duration. Backend-specific refusal belongs to that combination's
producer request. Inspection never creates an allocation, invokes inference, prepares a
remote runtime, or uploads private data.

### Waiting producer request

Reserve its final job ID at acceptance, using eventual `kind='transcribe'` or `kind='enhance'`.
Its ordinary state is queued and `params.input_intent` contains:

- strict `version`, `request_id`, request ordinal and total combinations;
- `phase='waiting'`, generation, monotonic admission epoch and `cancel_requested=false`;
- accepted provider ID/name and complete settings; requested feature/options snapshots;
- selected operation and confirmation baseline, including any existing reviewed-source
  result/version/hash or replacement confirmation required by that operation;
- frozen routing rules and exact candidate connection identities if Automatic is requested;
- the explicit preparation/download consent and its exact connection/model scope.

For native combinations, also freeze a native-selection group key derived from request,
project, provider and exact accepted settings, plus its expected member count and canonical
job-ID-set digest. Assign all reserved IDs in the acceptance transaction. This makes the
selected scope checkable without a new request table or mutable native owner before readiness.
Queries must include all group rows, including cancelled/failed rows, and verify that digest.
While the group is unresolved, retain those rows: recording deletion must not cascade away
one terminal member and erase the barrier. An explicit pending-group obligation guard is
needed in addition to the existing queued/running guard. Resolve the group atomically for
every row before releasing that guard. A small dedicated request/group table would be an
alternative if immediate deletion of terminal members is required; it is unnecessary for
this first, conservatively guarded slice.

Use the existing validated Settings/options representations and bound the serialized envelope.
There is no runtime/native owner ID, runtime task, remote journal or capacity reservation.
Read-only connection discovery may be frozen before acceptance; a source-dependent concrete
runtime plan is not executable until materialization. An unknown duration is never replaced
with `recordings.duration` merely to bypass inspection.

The intent records an accepted selection whose full source evidence is still pending. Do
not call it an executable frozen job until materialization has committed. It must not acquire
new provider settings, another default provider or newly broadened consent while waiting.

Small Store seams, all metadata-only and transactional, are sufficient:

| Proposed helper | Contract |
| --- | --- |
| `accept_transcription_intents(items, request_id)` | Atomically ensure inspection metadata/jobs and insert every requested combination; return jobs in request order. |
| `claim_input_inspection()` | Claim one queued inspector and advance its epoch; return job plus generation/epoch, or None. |
| `finish_input_inspection(job_id, generation, epoch, *, receipt=None, error=None)` | CAS exactly one validated ready receipt or curated error together with job/dependent-state changes. |
| `claim_input_admission()` | Claim an eligible API/runtime/whole-enhancement row, or an entire eligible native-selection group after its barrier. Return IDs, epochs and exact cache receipts/digests; never claim a ready native subset. |
| `materialize_input_admission(outcomes)` | CAS the claimed unit, queue valid executable jobs and create memberships atomically; commit explicit per-row failures/cancellations. Resolve every native-group member together, including exclusions. Remove `input_intent` from executable params, retaining a separate immutable `input_acceptance` receipt. |
| `cancel_input_intent(job_id, expected_epoch)` | Return cancelled, cancellation requested, or changed scope; never dispatch allocation Stop as a fallback. |
| `retry_input_intent(job_id, expected_epoch)` | Requeue a recoverable non-executable failure with a new epoch; retain accepted options and never operate on a materialized job. |

Existing `add_job`/`add_transcription_jobs` remain executable insertion APIs. Share their
small internal grouping/attachment code inside the final transaction rather than having one
public method open another transaction.

## Acceptance, inspection and materialization

1. Validate request-wide syntax, the 100-combination bound, IDs, explicit confirmations,
   enabled selected profiles, locally known credential presence and policy constraints.
   Acceptance is bounded metadata work: no source hashing/decoding, interpreter/model probe,
   remote storage read, wallet/app discovery or download in HTTP. A malformed or unauthorized
   selection fails before acceptance. Freeze desired options and exact candidate identities;
   deferred preflight must use them, not reload today's defaults. Preserve an immediate
   `409 prepare_runtime` only when its need is already known without slow work. Otherwise a
   deferred need for missing consent becomes an explicit visible refusal/needs-confirmation
   outcome, with no download/allocation; ordinary Retry cannot turn false consent into true.
2. Under `queue.lock` and one short `BEGIN IMMEDIATE`, recheck identities and duplicates,
   ensure each original's cache/inspection job exists, and insert **all** selected combination
   rows. Already known per-file invalid inputs get explicit failed rows; others get waiting
   rows. Return every row in request order. No selected item disappears silently.
3. A dedicated bounded local inspection lane claims its job/epoch in a short transaction,
   releases locks, inspects the managed regular source, then CAS-publishes ready/error against
   generation, job pointer and epoch. Receipt and inspection completion commit together.
   Source failure atomically fails dependent still-waiting intents with the safe source error;
   unrelated ready API/runtime files proceed. Native groups wait for all selected members'
   source outcomes (ready, failed or explicitly cancelled). A stale inspector cannot overwrite
   a newer attempt. Hash cancellation must retain the running/busy row until actual I/O drains;
   returning Cancel while a large `file_digest` continues is not release of the library lease.
4. A separate small admission lane claims an eligible ready API/runtime/whole-enhancement
   row, or all eligible members of one native group after its barrier, marking claimed intent
   jobs running/`phase='materializing'` with epochs. A pending native group does not head-of-line
   block other eligible backends. Keep one materializer initially; at most 100 combinations
   are processed. Claiming before file I/O
   provides a durable busy guard for deletion/moves. Expensive source verification, runtime
   preflight, feature checks and routing occur after releasing locks.
5. Construct full producer params from the **accepted** settings/options and the exact cache
   receipt. Use its effective duration for routing, backend limits, language and follow-ups.
   Preflight each requested child before its parent may run; one file's source/capability error
   fails that combination and does not discard valid siblings. For native, finish all selected
   members' preflight outcomes before the group's one materialization commit. Preserve the existing default
   that missing optional extras cannot silently downgrade a request to ASR only.
6. Under `queue.lock` and one `BEGIN IMMEDIATE`, CAS every outcome against job ID, phase/epoch,
   original generation/receipt digest, cancellation and confirmation baseline. Recheck current
   Protected approval and exact connection/model identity. For successes, replace intent-only
   params with fully frozen executable params on the **same** job ID, retain a small immutable
   acceptance receipt, attach runtime members/group native jobs, and return jobs to queued.
   Fail/cancel other members explicitly. Owner/member/task creation and producer transition
   must roll back together on any transactional failure. Notify workers only after commit.

Two small local phases avoid a full-file inspection blocking already source-ready runtime
admission, or a long API/legacy enhancement run blocking admission. Reuse the queue's worker,
wakeup, cancellation and lease lifecycle, with a distinct admission completion branch. No
new inference concurrency, timer scheduler or generic task executor is required. Track all
materializing IDs in a claimed cohort; the current single `active_id` cannot route cancellation
to arbitrary siblings on its own.

When the later eager-import optimization is added, create the recording, cache and
source-inspection/playback job rows in one
metadata transaction after the managed original is closed. A failed transaction leaves only
an unreferenced ID-owned upload directory that the existing import rollback may remove; never
publish a recording whose inspection can read a partial upload. New playback jobs carry a
specific initial-inspection-job gate: wait until that initial attempt terminates, then allow
browser conversion whether inspection succeeded or failed. This is the smallest ordering that
puts original inspection before costly browser video preparation. It also delays browser audio
until that first inspection ends; splitting browser audio/video priority is a separate product
choice. Unmarked legacy media jobs keep their current behavior.
Post-commit snapshot/notification/response failure must retain the committed original and
metadata; do not let the import's broad exception cleanup remove a successfully published row.

## Freeze and grouping semantics

Existing [ADR 0023](../decisions/0023-batch-transcription-and-enhancement.md) preflights the
entire request before creating jobs, and [ADR 0047](../decisions/0047-automatic-enhancement-routing.md)
resolves Automatic when queueing. The new ADR must explicitly distinguish durable selection
acceptance from executable materialization. Automatic uses the accepted rules and candidate
identities, resolves once using measured source duration and current readiness, and stores
concrete local/UCloud options plus the reason. It never runs against newly loaded routing rules
or silently enables preparation. Workers never receive `execution='auto'`.

Recommended enable/approval rule: the accepted profile was enabled at selection, as with
today's frozen queued jobs. Later disable alone does not discard accepted work; changed/deleted
endpoint/model identity blocks materialization, and current Protected approval is mandatory
at materialization and every private upload. Runtime's `_check_profile` currently compares
enable/approval fields for fresh attachment; extend it only through a verified accepted-intent
path, analogous in scope to its existing frozen-follow-up exception. Do not loosen ordinary
fresh attachment globally or trust a client-supplied bypass flag.

Freeze the complete requested follow-up in the parent before ASR becomes executable, with the
same audio input and chosen identity. The actual child source result/version/hash still comes
from the parent's atomic result commit. A delayed old parent must continue inserting its old
follow-up unchanged; `_insert_job` must not add policy/readiness/intent defaults.

| Backend | Ready/pending mixed request |
| --- | --- |
| Ordinary API | Materialized ready rows run immediately; pending rows retain exact accepted choices. |
| CBS Runtime | Only materialized ready rows attach. Later rows may join a compatible open owner under its existing revision/deadline/Stop CAS; otherwise they use a later owner. Source identity stays member-specific. |
| Native Transcriber | Wait for every selected compatible member's source/preflight outcome. Materialize all eligible survivors together, grouped by request/project/exact provider/settings, in one commit and one allocation per group. Never append to or split an existing native manifest. |

The user's one-native-allocation requirement takes precedence over starting native ready
subsets early. The barrier applies separately to each compatible native group; it does not
hold API/runtime jobs, another request or a different native project/profile hostage. Failed
or explicitly cancelled selected members are visible exclusions, never silent drops. Zero
eligible members means no allocation; one survivor may use the existing singleton path.
The final group resolution records the selected set and exclusions atomically. After a native
manifest materializes, a failed excluded intent cannot reopen that group or create a second
allocation on ordinary local Retry: a fresh explicitly selected request is required. Before
any owner exists, an explicit retry may rerun the still-local group's failed admission while
retaining choices and never reviving intentional cancellation implicitly.

## Cancellation, retry and restart

- Waiting intent cancellation is local and affects that combination only. It does not stop
  another intent's shared source inspection or an unrelated provider/runtime allocation.
- Cancelling materialization sets durable monotonic cancellation intent. Keep its job running
  until active local I/O has drained, then mark cancelled. Otherwise deletion could remove a
  file still being hashed. Check cancellation between preflight stages and before attachment.
- Fence the waiting Cancel action by expected admission phase/epoch or expected scope. If
  cancellation wins the transaction, attachment cannot occur. If materialization has already
  won, return changed canonical scope for confirmation; **never turn a stale one-file waiting
  Cancel into shared-allocation Stop** affecting late-joined outside members. Request-wide
  cancellation likewise covers exactly its recorded pending IDs, then uses existing guarded
  allocation Stop semantics for any materialized work.
  Use an explicit intent action endpoint/token; the current generic `JobQueue.cancel()`
  follows today's owner fields and cannot distinguish a stale waiting-screen click. A
  materialized target returns changed scope from that intent action, not allocation Stop.
- Source inspection has its own visible cancellation. Cancelling one producer need not cancel
  that shared metadata task. A cancelled inspection publishes a safe terminal reason for its
  dependent waiters; an active inspector keeps its running/busy ownership until drained.
  Deletion or switching after cancelling producers may still require cancelling that inspector.
- On restart under the exclusive library lease, only these new versioned local phases may
  automatically return to queued with a new epoch. They performed no provider submission or
  inference. Requeue inspection/materialization before the generic running-to-interrupted
  recovery. Terminal explicit cancellations/failures remain terminal; source retry does not
  silently revive failed producer requests.
  Validate that an intent has no actual allocation/member/publication journal before automatic
  recovery; contradictory ownership fails closed. Shutdown interruption is resumable, while a
  durable explicit cancellation request remains cancellation on recovery.
- The materialization commit is the recovery boundary. Before it, restart resumes only local
  validation of the same intent. After it, recover the ordinary producer/runtime/native job by
  its existing rules. Never reset an actual running producer to queued, issue another POST,
  append another task attempt, or redo accepted ASR merely because input metadata changed.
- Explicit Retry of a failed pre-materialization intent retains its request ID/job ID and
  accepted settings/consent, and reruns only local admission if the reason is recoverable.
  Unsupported/corrupt originals require a new import or supported decoder contract, not
  replacement of an immutable original. Already executable jobs use existing Retry semantics.

If a deferred check discovers that additional preparation consent is required, retain a
visible refusal and request a fresh explicitly confirmed selection. Ordinary Retry cannot
broaden the accepted consent. A resumable consent-amendment workflow would need its own
versioned acceptance receipt and is unnecessary for the first slice.

## Locks, guards and compatibility

Use the existing library lease through shutdown; drain both local lanes before releasing it.
Where both process locks are needed, the order remains `queue.lock` → `snapshot_lock` → short
SQLite transaction. Import already holds snapshot without queue: its atomic metadata helper
must not acquire queue while holding snapshot. Queue notification after publication is an event,
not a queue-lock acquisition. No file hashing, decode, runtime checks or provider I/O inside
queue/write transactions.

Workers claim under queue/database locks and retain running rows while active outside them.
That makes current queued/running guards cover waiting selections, inspection and admission:
project moves, deletion and library switching stay blocked until work is cancelled and drained.
Test the guards rather than assuming them; helper SQL and UI filtering can still omit a new
kind. Collection moves remain project-local metadata and must not rewrite accepted project or
source identity. Snapshot generation occurs after database commits, outside write transactions,
under its existing lock; a snapshot failure cannot roll back a committed materialization by
deleting owners/files. SQLite stays authoritative after restart.

`recordings.status` remains playback state. Original inspection does not set it ready/error.
Browser conversion failure cannot fail validated original work, and source failure does not
automatically prohibit playback. Keep old `recordings.duration` exactly; later media completion
may fill NULL only. Every original-v1 processing path uses its frozen effective duration, while
legacy jobs retain their old duration/source checks. Do not rewrite old jobs, queued parents,
prepared plans, WAVs, journals, histories or report evidence during migration or recovery.

## Smallest implementation order and activation boundary

1. **Inspection mechanics, internal first.** Add the nullable cache, one bounded inspection
   lane, epoch-fenced publication and cancellable hash loop. Test migration, deduplication,
   cancellation/drain and recovery without changing public enqueue yet. This is not enough
   to activate T87a: synchronous freeze remains in HTTP until the next slice is complete.
2. **One complete durable-enqueue vertical slice.** Accept whole-ASR and whole-result
   enhancement selections as waiting jobs, return their IDs promptly, inspect in background,
   run source-dependent routing/preflight in the admission lane, and atomically materialize
   those same IDs. Include claim exclusion, current policy, exact source/consent snapshots,
   requested follow-ups, native all-selected barrier, intent Cancel/Retry and restart handling
   in this slice. Native grouping cannot be a later follow-up patch. The current playback
   gate may remain as an admission predicate temporarily, but no full decode/hash or slow
   runtime/provider operation may remain in the acceptance handler. This is the smallest
   activation candidate for T87a's V8 hold, covering both synchronous whole-file endpoints.
3. **Independent playback readiness.** Remove media conflicts/status guards for validated
   new original-v1 producers in app/worker/native/runtime paths, preserve legacy guards,
   expose server-owned source readiness and update the UI. Keep `recordings.duration`
   unchanged except filling NULL on media completion. A failed browser derivative must not
   fail original-ready producers. Only after this slice is independent readiness delivered.

Start with lazy inspection ensured by durable selection acceptance. This avoids expanding
the first activation unit into upload publication, video-priority scheduling and eager work
for recordings never selected for processing. An immediate later throughput slice may hash
while importing and enqueue/gate inspection before new browser-video work; its atomic import
publication and post-commit cleanup rules above are mandatory when that optimization lands.
The first implementation should not claim it already prioritizes source inspection at upload.

## Verification contract

Use temporary synthetic libraries, mocked runtime/provider checks and event barriers. Real
media validation remains in the existing licensed/synthetic audio-input suite. No live services,
downloads, private libraries or deployment are required to prove queue semantics.

| Slice | Required failing-then-passing evidence |
| --- | --- |
| 1. Additive metadata and inspection | Backup/rollback/reopen and lease-before-migration-write tests; old rows remain NULL; same-generation concurrent requests create one inspector; stale epoch/hash/path replacement cannot publish ready; notes/transcript versions and duration unchanged. |
| 2. Durable acceptance and claims | HTTP returns all combination IDs while inspection is held; traps prove no source hash/decode/runtime/network call from HTTP; ordinary claims ignore intents; no unready runtime/native members or capacity slots; metadata/intent insert fault rolls back the accepted request. |
| 3. Materialization | Ready API/runtime row runs while another inspection is held; native group does not materialize until its barrier; source-dependent failure keeps other successes; frozen options/current approval/source digest/confirmation are checked; injected attachment failure leaves no orphan owner/task or partially replaced producer params. |
| 4. Follow-up and grouping | Mixed-ready native selection produces exactly one immutable allocation containing eligible survivors, never cohorts; cancelled terminal members cannot be deleted to erase a pending barrier; a closed group cannot reopen into another allocation; runtime late join vs seal/Stop is serialized; extras preflight before ASR; parent/child rollback and late legacy follow-up remain exact. |
| 5. Cancel and restart | Race Cancel against final CAS in both orders, including outsider runtime members; cancel keeps live I/O busy until drained; crashes before/after inspection publication and before/after materialization commit yield one job/member and zero duplicated provider calls; actual producer interruption is never auto-replayed. |
| 6. Playback/guards/UI | Preparing/failed playback does not block original-ready work; pending/error source stays explicit; move/delete/switch block waiting and live local phases; shutdown holds lease. If eager import priority is enabled later, first inspection precedes video conversion, source failure still permits playback, and import/move barriers prove lock order. |
| 7. Compatibility and policy | Old jobs/plans/receipts retain bytes/source identity; cached result finalizes without playback/decoder/model checks; changed routing/default profile never retargets a waiter; disable-only vs endpoint change vs Protected revocation follow the stated rule; report/export allowlists do not grow from the cache column accidentally. |

Adapt existing coverage in `test_original_input_integration.py`, `test_jobs.py`,
`test_preparation_queue.py`, `test_feature_followups.py`, `test_native_batch.py`,
`test_runtime_store.py`, `test_runtime_queue.py`, `test_project_storage.py`, `test_libraries.py`
and API/browser tests; put the new intent/inspection CAS cases in a small dedicated test module.
Keep legacy fixtures explicitly legacy instead of upgrading all fixtures by implicit defaults.
Run each vertical slice's focused tests before broad verification.

This proposal was reviewed and amended against the current original-v1 implementation on
28 October 2026. The native ready-cohort recommendation was removed to preserve the user's
single selected native allocation. This amendment changed only this proposal: no production
code, schema, accepted specification, TODO or ADR, and no media decode, provider call, model
download, private-data read, allocation, app restart or activation.
