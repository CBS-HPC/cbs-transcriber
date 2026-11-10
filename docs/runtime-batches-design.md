# T85 — Durable CBS Runtime batches

Design prepared 27 October 2026 on `feature/runtime-batches-and-review-layout`.

**Status: implementation in progress under ADR 0065; T85 is open until integration passes.**
Native Transcriber batching exists separately. The user requested shared selected files, compatible late
admission, separate incompatible jobs, independent allocations and one card per allocation.
ADR 0065 accepts two initial allocation slots and the numeric grace/heartbeat policies below.
Stage-major model reuse remains an optimization requiring memory/performance measurements;
the first controller executes one full pipeline per file sequentially inside each allocation.
The current code uses synthetic libraries and mocked UCloud; no real-library migration or
live allocation acceptance is implied by this design document.

## 1. Outcome and constraints

Several selected compatible recordings share one CBS Runtime Terminal allocation. Each file
retains its own durable enhancement job, frozen source, checkpoints and independent result.
Later compatible submissions join that allocation while admission is open. Different models,
connections, runtime identities or processing settings receive separate allocations. Independent
allocations can run concurrently within an explicit library capacity limit.

One Activity card represents each allocation and expands to per-file outcomes. The local
enhancement/passage-timing lane stays independent. Native `native_batch_runs`, frozen API
singleton jobs and frozen SSH jobs retain their existing recovery identities.

Reuse SQLite, the library lease, UCloud transport and pinned runtime. Keep existing 16 kHz WAV
preparation and frozen `da`/`en` language. Remote compressed-media preparation is T87; language
detection is separate. No external queue, remote HTTP listener or credential-bearing bundle.

Proposed supported members: WhisperX transcription, whole-result alignment/speakers, frozen
post-ASR follow-ups and bounded passage re-transcription. Passage repairs and same-result timing
stay local. Preserve source-enhancement lane conflicts and provider-specific ASR duplicates.
Clarify V95 alongside V13: WhisperX ASR uses `kind=enhance` internally, but different explicitly
selected ASR profiles may still queue for one recording. Do not accidentally block model
comparisons by applying the whole-result enhancement conflict rule to all batch members.

## 2. Why the current runner cannot just gain threads

The current code has these coupled assumptions:

| Existing code | Current ownership / consequence |
| --- | --- |
| `enhancement_api.Run` | `job.params.ucloud_run`, remote folder and allocation belong to one job. |
| `Run.submit()` | One `cbs-enhancement-<job-id>` folder, one Terminal specification and one submission journal. |
| `ucloud_supervisor.sh` | Waits for one manifest, executes one directory, writes `finished` and exits. |
| `enhancement_remote.run()` | Locks one directory and invokes a fresh worker for that directory. |
| `Run.collect()` / `settle()` | Stops the allocation and trashes its folder for that one job. `collect()` currently runs before the caller's `Store.add_result()` commit. The batch path must not inherit this ordering. |
| `enhance_window()` | Performs preparation, remote execution, collection and merging synchronously for one job. |
| `JobQueue` / `Store.claim_job()` | One `enhance-remote` worker claims ordinary enhancement jobs individually; cancellation finds `active_id`. |
| `enhancement_worker.execute()` | ASR, alignment and speakers load within a job. Only bounded local `--serve-passages` caches an alignment model. |
| `Store.add_result()` | Existing useful receipt: result/version, running-job completion and frozen enhancement follow-up commit atomically and idempotently by producing job ID. |
| Native batch schema 9 / `native_batch.py` | Useful patterns for independent allocation journals, merged receipts, batch actions and Activity. Its immutable selected-file directory is not a late-admission runtime protocol. |

Add a distinct allocation owner. Member completion never calls singleton `stop`/`settle`/cleanup.
Select the new path only by `runtime_batch_id`; old frozen jobs retain the singleton path.

## 3. Exact compatibility and deterministic admission

Build a versioned canonical compatibility object after normalizing all defaults. Serialize
with UTF-8, sorted keys, compact separators and finite numeric values; SHA-256 indexes the
object. Compare the complete canonical object as well as its hash before attaching.

The object contains:

1. Protocol version `cbs-runtime-batch-v1`; hashes of the exact supervisor, remote controller
   and worker sources, pinned model-revision map, and hash-locked runtime recipe/runtime ID.
   The present runtime ID hashes requirements only; that alone is insufficient to identify a
   changed batch protocol or worker implementation.
2. Local research project ID. Never combine different research projects, including Standard
   and Protected projects on the same UCloud drive.
3. Exact frozen provider profile ID and complete normalized `ucloud_api.settings` snapshot.
   This conservatively separates different saved profiles even when their endpoints happen
   to agree. It fixes server, UCloud project, storage root, connection/template overrides and
   cleanup policy. Profile display names and credentials are not identity fields.
4. Exact separately frozen `runtime_product`, `runtime_compute_policy`, replicas = 1 and
   concrete CUDA device policy. New batches require the existing verified `mig-v1` identity;
   there is no inherited native-product fallback for new batch jobs.
5. Exact Terminal application name/version and `batchScript` contract. Resolve metadata
   outside the queue lock before freezing the plan; do not silently select a newer app at
   submission or retry.
6. Operation: `transcription`, `enhancement`, or `passage_retranscription`. ASR and
   source-preserving enhancement do not share a batch just because both align words.
7. All normalized remote execution options, including `align`, `diarize`, `transcribe`,
   `retranscribe`, `whisper_model`, concrete `language`, `min_score`, speaker limits and
   `timeout_minutes`. Execution is `ucloud`, device is `cuda`, and downloads are false.
   Exclude only the local `runtime_python` and the preparation-consent flag.
8. Frozen allocation limits: hard wall-time budget, idle grace, owner-absence grace,
   maximum members, GPU concurrency = 1 and model-cache policy version.

Source recording IDs, source result/version/hash, selected passage IDs, original revision,
audio bounds and input hashes are member identities, not compatibility fields. They must
differ between files without preventing batching. They never change on retry.

Preparation permission belongs to the exact requested runtime/model set. It is not inherited
by later jobs or Automatic routing. Identical work can join an already prepared batch without
preparation permission; different model sets cannot. Finish preparation before private upload.

For each preflighted request, under the queue lock and `BEGIN IMMEDIATE`:

- Recheck media state, current profile identity/approval, source revision and duplicate active
  requests. Freeze source versions using the existing transaction-safe history rules.
- Group the request by compatibility object. Choose the oldest matching batch by
  `(created,id)` whose admission is open, which has no stop/seal/uncertainty, has sufficient
  member capacity and has not passed the late-admission cutoff. Queued/starting batches can
  accept reservations, but no audio uploads until runtime readiness.
- Insert all ordinary jobs, members and first attempts for that request atomically. Increase
  each affected batch's membership revision and assign consecutive member ordinals. Return
  the durable batch/member IDs; never report that a file reached UCloud until its remote
  acceptance receipt exists.
- Create another queued batch for each incompatible group or full/closed batch. Capacity
  limits allocation submission, not local queueing. A request of at most 100 combinations
  remains valid even when its batches must wait.

Check the encoded membership/seal byte budget in this transaction as well as the 100-member
limit. A new member which would overflow control-file limits joins another batch; a processing
retry which would overflow them is refused with a new-run instruction. Never create a durable
reservation which cannot be represented in the bounded protocol.

Admission and sealing use the same SQLite transaction discipline. If sealing wins, the
request creates/joins another batch. If attachment wins, sealing includes the new member and
cannot close underneath it. No remote request occurs while holding the queue/SQLite locks.

## 4. Proposed schema 12

Under the library lease, back up schema 11 with SQLite's backup API and transactionally add
these tables/indexes. Preserve existing rows/frozen parameters; failures retain schema 11 and
older app versions refuse schema 12. Fresh libraries use the same migrations. Develop on
synthetic libraries only.

### `runtime_batch_runs` — allocation owner

| Column | Contract |
| --- | --- |
| `id TEXT PRIMARY KEY` | Generated UUID; the stable remote allocation/folder identity. |
| `project_id TEXT NOT NULL` | Research project ID, deliberately without cascading FK. |
| `compatibility_hash TEXT NOT NULL`, `identity TEXT NOT NULL` | Indexed hash and immutable canonical compatibility JSON. |
| `state TEXT NOT NULL` | `queued`, `running`, `completed`, `failed`, `cancelled`, `interrupted`; canonical local Activity state. |
| `admission TEXT NOT NULL` | `open`, `draining`, `sealed`, `closed`; monotonic except interrupted open batches may resume only after remote verification. |
| `membership_revision INTEGER NOT NULL DEFAULT 0` | Increments for attach and new attempts; identifies the published member list. |
| `seal_revision INTEGER`, `seal_digest TEXT` | Final immutable task set; null before seal. |
| `coordinator_epoch INTEGER NOT NULL DEFAULT 0` | Incremented on claim; fences late local coordinator checkpoints. |
| `journal TEXT NOT NULL DEFAULT '{}'` | Merged submission, ownership, readiness, remote state, control and cleanup receipts. |
| `message TEXT`, `progress REAL`, `created TEXT`, `updated TEXT` | Safe canonical presentation fields. |

Index `(compatibility_hash,admission,state,created,id)`. Batch `project_id` and journal survive
recording/project deletion; deletion still refuses unresolved obligations.

Journal fields include: expected unique run name, exact owned folder, folder-creation receipt,
script hashes/upload receipts, preparation permission/completion, submission intent/attempt time,
remote job ID/state, reconciliation receipt, readiness generation/hash, absolute observed start
and hard deadline, stop intent/acknowledgement, last remote status revision, last published
membership revision, close intent/acknowledgement, cleanup state and safe diagnostics. Never
replace this JSON from a stale in-memory copy: merge allowlisted receipts under `BEGIN IMMEDIATE`
with the expected coordinator epoch. Endpoint Stop is a monotonic intent which the coordinator
cannot overwrite. Immutable identity fields have no general update method.

### `runtime_batch_members` — logical per-file ownership

| Column | Contract |
| --- | --- |
| `job_id TEXT PRIMARY KEY` | One ordinary durable enhancement job; at most one runtime batch per producing job. |
| `batch_id TEXT NOT NULL REFERENCES runtime_batch_runs(id)` | Restrictive FK; no cascade. |
| `recording_id TEXT NOT NULL`, `ordinal INTEGER NOT NULL` | Frozen owner and stable order; `UNIQUE(batch_id,ordinal)`. No cascading recording/job FK. |
| `source_identity TEXT NOT NULL` | Exact source version/hash or transcription-original identity and selected bounds. No notes or credentials. |
| `current_attempt INTEGER NOT NULL DEFAULT 1` | Latest explicit processing attempt. |
| `state TEXT NOT NULL` | `queued`, `preparing`, `uploading`, `accepted`, `running`, `collecting`, `completed`, `failed`, `cancelled`, `interrupted`. |
| `result_id TEXT`, `output_receipt TEXT NOT NULL DEFAULT '{}'` | Durable result ID and validated local raw/stage hashes; may reference a trashed result without restoring it. |
| `created TEXT`, `updated TEXT` | Member lifecycle timestamps. |

Jobs retain frozen source/options/routing and gain `runtime_batch_id`. Verify the pointer,
member owner and batch identity on every action. Allocation journals exist only on the batch;
retained member metadata can display `Removed recording` after deletion.

### `runtime_batch_attempts` — explicit dispatch and replay receipts

| Column | Contract |
| --- | --- |
| `(job_id,attempt) PRIMARY KEY` | Attempt counter under a member; increment only for an explicit processing retry. |
| `task_id TEXT NOT NULL UNIQUE` | Generated UUID, unique remote mailbox identity; never reuse an old task for changed input. |
| `state TEXT NOT NULL` | `reserved`, `preparing`, `publishing`, `accepted`, `running`, `complete`, `failed`, `cancelled`, `interrupted`. |
| `input_receipt TEXT NOT NULL DEFAULT '{}'` | Exact request/audio hashes, sizes, manifest digest and publish intent. |
| `remote_receipt TEXT NOT NULL DEFAULT '{}'` | Acceptance/status generation, terminal outcome and stage/output digest map. |
| `error_code TEXT`, `created TEXT`, `updated TEXT` | Curated error and times; never an upstream body. |

Use restrictive member FK and a partial unique index preventing more than one nonterminal
attempt per member. Prior attempt input identities/terminal receipts are immutable. This third
table is necessary to distinguish a lost acceptance reply from a deliberate rerun of a known
failed stage; a mutable `uploaded: true` flag cannot express that distinction.

`Store.add_result()` must update member completion, result ID and output receipt in the same
transaction as the result/version/job receipt. Verify its batch/task identity there. A retry
finding an existing producing-job result repairs missing receipt projection and returns that
same result, including Trash visibility, without appending/restoring anything.

## 5. File protocol and remote state machine

New batches use one exact folder `cbs-runtime-batch-<batch-id>` and a Terminal job named
`CBS Runtime batch <batch-id>`. Never adopt a pre-existing folder without the creation receipt.
Keep the shared runtime folder separate; cleanup cannot target it.

```text
cbs-runtime-batch-<id>/
  supervisor.sh, enhancement_remote.py, enhancement_worker.py
  batch.json                 immutable identity/protocol/source hashes; no private text
  membership.json            bounded member/attempt ledger + revision
  heartbeat.json             owner liveness only; never permission to upload/process
  seal.json                  final membership revision, attempt ledger and digest
  stop.json                  monotonic whole-allocation stop intent
  doctor.json, cache.json     bounded readiness before private upload
  batch-status.json          bounded summary: generation, admission, revision, counts, stage
  tasks/<task-id>/
    request.json, audio.wav
    inputs.json              exact hashes/sizes + job/member/task/batch binding
    ready.json               small commit descriptor, published last, hashes inputs.json
    accepted.json             immutable acknowledgement of that exact input digest
    progress.json, outcome.json
    transcription.json / retranscription.json / alignment.json / diarization.json
    windows.json, recognition-* / alignment-* checkpoints as applicable
```

Remote paths use generated UUIDs and known relative filenames only. Never filenames, source
text, shell fragments, arbitrary paths or tokens from a browser request. Existing audio,
passage, stage-size and duration limits still apply. Bound `batch.json`, membership and status
at 64 KiB; a batch has at most 100 members. Stage outputs retain the existing 32 MiB limit.
Validate unique keys, finite numbers, protocol/version, exact IDs and expected file sets.
Reject `..`, absolute paths, symlinks, unexpected nested paths and unknown options/stages.

UCloud file replacement is not assumed to be a transactional filesystem rename. A reader
ignores a missing/partial descriptor and polls again; the complete parsed descriptor and
all listed hashes/sizes must agree before any acceptance/inference. Publish private input
files first and `ready.json` last. Persist publish intent and exact bytes locally before any
upload. Repeating identical bytes to the same task is safe; a different digest is an ownership
conflict. Keep the existing byte-preserving local JSON writer (ADR 0034).

Only the batch coordinator publishes membership, seal and stop files; browser endpoints write
SQLite intent and wake it. It serializes remote writes. The manager cannot hand ownership to
another coordinator until every old coordinator I/O future has settled. Epoch checks fence
late SQLite writes. The remote controller ignores older status/control revisions and refuses
conflicting identity/digests; no concurrent writable manifest merge is required.

Membership includes all task IDs, member/attempt/ordinal, disposition and current-attempt
mapping. Local failure before publish intent gets `not_dispatched` plus a safe code, published
without private data. After publish intent, prove remote work settled before tombstoning it.
This prevents endless seal waits and treating a lost upload reply as an unsubmitted task.

The supervisor prepares/validates the frozen runtime, writes doctor/cache/readiness, then
executes a long-lived batch controller in `enhancement_remote.py` under one allocation lock.
The controller owns a sequential worker child and publishes atomic local status files. Hold
the remote flock through the child as the current wrapper does, so controller uncertainty
cannot launch duplicate inference. Preserve existing singleton CLI modes for old jobs.

Controller states:

```text
starting -> preparing (only explicit consent) -> ready/accepting
ready/accepting -> running -> ready/accepting
ready/accepting or running -> draining -> sealed -> finished
any active state -> stopping -> stopped
any active state -> failed
```

Local `draining` rejects new members, publishes the final membership and seal, and allows all
already admitted work to finish. The seal covers all attempts, their terminal local
dispositions and the current-attempt mapping, not only the latest inbox contents. The remote
controller acknowledges the seal only after checking its digest and complete task set. It
must not infer that a temporarily empty inbox
means the batch has no locally reserved or partly uploaded work. It exits successfully only
after every sealed task has a terminal outcome and every known output receipt is published.
Aggregate outcome follows the current member attempts/results; an earlier failed attempt
followed by an explicitly retried success does not make that member permanently failed.
An unresolved current member failure produces a partial outcome, not fabricated all-success.

Allocation readiness is batch-level, but before **each** private upload the coordinator
requires the correct running remote ID, generation/protocol, ready doctor/cache, exact pinned
models, CUDA and the first-device MIG VRAM floor. Recheck that member's current approval and
frozen endpoint/profile identity immediately before transfer. Journal cleanup pending before
the first private byte. A later missing model is a readiness failure; do not download weights
or rebuild a runtime underneath a running allocation.

Per-task lifecycle:

1. Prepare the member request/audio locally with the existing exact source/bounds and timebase.
2. Persist hashes and publish intent, then upload to its exact task folder and commit `ready`.
3. Remote controller verifies membership, task/compatibility/source binding, hashes and sizes;
   writes `accepted` **before** inference. A replay of the same task returns its receipt.
4. Run pending stages sequentially and checkpoint each stage. A completed task writes an
   outcome with exact output hashes/lengths and safe codes. It never edits another task.
5. Coordinator downloads only the exact expected task files, validates generation/digests and
   merges through existing source-preserving code. Write local files atomically, verify hashes
   and flush before recording them as durable.
6. Commit `add_result` plus member receipt atomically. Signal per-file completion immediately;
   do not wait for all siblings, seal, remote exit or cleanup. Keep the reviewed result active.

Reconnect may validate/finalize existing stage files; presence alone is no receipt. Reject
wrong generation/input, ambiguity, oversize and hash mismatch. An accepted task lacking an
outcome is poll-only while its inherited worker lock is held. Once the child is confirmed gone,
mark interrupted and require explicit retry. Results commit once; computation can repeat an
incomplete stage on explicit retry after a crash before its checkpoint. Complete stages reuse.

## 6. Admission lifetime, capacity and model memory

**Proposed policies, to adopt or revise during implementation:**

| Setting / rule | Suggested value and exact behavior |
| --- | --- |
| Processing → Advanced, active CBS Runtime allocations | Default 2, valid 1–4. Count nonterminal/uncertain new batches and legacy API runtime allocations. Native/SSH retain their lanes. Lowering never stops owners; queued batches wait. This is not a provider-wide spend limit. |
| Local coordination | At most the configured number of allocation coordinators and 2 heavy preparation/transfer operations. Polling holds no transfer semaphore, queue lock or transaction. Continue recovery/cleanup of owned allocations after limits decrease. |
| GPU concurrency | 1 stage per allocation; no VRAM/host-RAM-derived multiplier. Independent allocations provide concurrency. Local enhancement remains sequential. |
| Wall time | Freeze existing `timeout_minutes` (1–720) plus explicit preparation allowance. Persist observed start/deadline; reconnect never resets it. No silent extension/reallocation. |
| Late-admission cutoff | 120 seconds before deadline. It is not a completion estimate. Show remaining time and retain unfinished checkpoints on expiry. Short-budget initial groups remain allowed without late admission. |
| Idle grace | 60 seconds after no reservation, transfer, accepted task, processing or collection remains. Failed terminal tasks do not hold a GPU forever. Attach/idle seal/Stop use the same transaction seam. |
| Owner absence | Heartbeat every 15 seconds; after 300 seconds absent, reject new tasks, drain accepted/validated tasks within their timeout, write orphan-drained status and exit. Unpublished local reservations remain interrupted; never silently reallocate them. |

Use monotonic local interval clocks. Cross-machine wall-clock comparison cannot authorize
admission. Owner-absence drain bounds idle cost without equating app shutdown with user Stop.

### Reusing loaded models without retaining every model

**Proposed reuse strategy:** a batch-only worker mode with one resident model, identified by
stage engine, pinned repository/revision, device/compute type and language. Unload/collect/clear
CUDA cache before identity changes. Do not broaden local `--serve-passages` or retain ASR,
alignment and speakers together by assumption.

The controller freezes a finite ready cohort (at most 100 tasks) when it starts processing.
Run stage-major within that cohort: transcription/re-transcription if requested; coarse window
location where needed; alignment; speakers. Each stage processes eligible members in ordinal
order using the same resident model. Files admitted later enter the next cohort; continuous
new arrivals cannot starve the current cohort's alignment/speakers or result publication.
Stage output names and all existing window/checkpoint/text-preservation semantics stay intact.
Publish a member's outcome as soon as its final required stage finishes.

Refactor existing stage functions to accept the resident model through a small internal seam;
reuse the same functions in singleton execution. A worker command contains only an allowed
stage and a validated task directory/ID; request text is read from that task's bounded file.
On task processing failure, clear the cache. Before continuing siblings after a worker crash
or OOM, reap the child, start a clean child and rerun doctor/readiness. An unhealthy runtime
closes admission and fails/stops the allocation. Never automatically rerun the failed task's
inference or switch models, compute, device or place.

## 7. Ownership, Stop, retry and failure races

### Stop is allocation-wide

`Stop CBS Runtime batch (N files)` and Cancel through any member address the same allocation,
returning its batch/affected job IDs. Even a completed member can address a still-active batch;
the generic completed-job guard must not swallow that action.

In one transaction, close admission and record stop intent before contacting UCloud. Wake the
actual coordinator by batch ID. Stop affects only this batch's recorded remote job ID, never
another compatible allocation or a template. No known ID plus uncertain submission remains
uncertain; cancellation cannot assert that no remote allocation exists.

Pending members become cancelled; accepted/running members settle against their actual
outcomes. Already committed results remain completed. The `add_result` receipt wins over a
later Stop. If Stop was committed before result import, hold the queue/receipt mutation seam
while deciding whether a fully collected validated result may commit: choose **preserve and
commit valid completed output**, then cancel only unfinished members. Never downgrade a
committed result or discard a known successful sibling. No private upload begins after the
stop check; an accepted in-flight transfer may settle, with its receipt retained.

Re-read remote state before termination. A terminal allocation needs no Stop merely because
output import/cleanup failed. A lost terminate reply leaves stop intent and unresolved remote
state visible; retrying Stop retrieves the same ID before any further terminate request.

### Retry is explicit and distinguishes three cases

| Case | Action |
| --- | --- |
| Lost connection, missing acceptance reply, app restart | Reconnect to the same allocation and same task IDs. Inspect exact receipts; publish only identical pending bytes. Never increment attempt or POST another allocation. |
| Outputs/checkpoints exist, local validation/import/snapshot failed | Revalidate exact outputs and finalize without inference/model checks where the complete local stage set suffices. Skip every producing-job result already committed, including trashed results. Cleanup retries never run ASR. |
| Known terminal task processing failure, or interrupted task whose old worker is confirmed gone; allocation still accepting and healthy | An explicit Retry may increment that member's attempt, create a new task ID and freeze an input manifest carrying only verified completed checkpoints from the prior attempt. Recheck approval/readiness. No automatic retry; old attempts remain evidence. |

Retry cannot reopen sealed/owner-closed or stopped/expired allocations for inference; output
collection remains possible. New inference there requires a new producing job and newly frozen
source via the existing new-run action. Say so; never silently migrate members.

Batch Retry operates on eligible unfinished members; successful siblings are skipped. While
the batch is open, an explicit per-file retry is allowed for a known failed task and names that
file. Close/retry CAS decides whether a new attempt was admitted: retry winning prevents seal
until that attempt settles; seal winning refuses inference retry and leaves output recovery
available. A task whose acceptance or worker liveness is uncertain is never eligible for a
new attempt. Copy only validated stage/window checkpoints into the new attempt, recompute
their manifest hashes, and never overwrite the prior task directory to simulate resumption.

### Submission and reconnect

Checkpoint batch-owned submission intent and reset any earlier explicit absence receipt before
POST. Capture the remote ID immediately. A missing ID after attempted POST reserves capacity
and blocks another POST for this batch. Exact-name reconciliation verifies
`CBS Runtime batch <batch-id>` with the frozen server/project, app and product; retain the
existing explicit human-confirmed absence path. Reconnection does not require permission to
upload, but sending/retrying private inputs does. Do not infer allocation absence from one
failed listing or status lookup.

Before an explicit Retry claims an interrupted batch, retrieve its saved remote status and
protocol generation outside locks. Under lock, recheck the same receipts/revision and claim
with a new epoch. A sealed batch stays sealed. An open interrupted batch resumes admission
only if the remote controller confirms it is still accepting and before the same deadline.
Do not trust the stale pre-shutdown local admission state.

### Failures stay local when possible

- Bad source/audio preparation affects that member before publication; no private upload or
  remote inference occurs for it. Other members continue.
- A member manifest/hash/processing/merge failure records that exact member/attempt outcome;
  unrelated members can finish. Output hash/ownership conflicts close admission and require
  inspection; do not guess the intended file.
- Approval revoked before upload fails that pending member. A globally revoked connection
  blocks all further private transfers. Already accepted remote work can finish and be
  downloaded locally; retrieving existing outputs does not upload new content. Stop remains
  available independent of approval.
- Batch doctor/cache/protocol failure stops the owned allocation before private inputs; after
  inputs exist, preserve checkpoints and mark all unfinished members. A single member's curated
  processing failure does not automatically stop a healthy sibling's allocation.
- Transient status/transfer failures preserve the allocation and exact publish receipts. After
  bounded retry exhaustion, mark the local coordinator interrupted/failed, leave ownership and
  capacity reservation, and offer reconnect. A later request cannot steal that batch's task.
- Hard expiry/terminal remote state triggers collection of every available valid outcome before
  failing remaining members. Do not require a still-running GPU just to import completed files.

## 8. Closure, durable preservation and cleanup

Separate three receipts: member result saved, remote allocation finished/stopped, remote files
trashed. None implies the others. `progress=1` on one member cannot close an open batch.

After seal/drain, observe remote terminal state and verify the final outcome: all-success is
completed, mixed is failed/`partial` with exact counts, fully cancelled is cancelled. Preserve
completed members. Interrupted means local coordination was lost, not that UCloud stopped.

Default success cleanup is batch-owned and runs only when:

1. Admission is sealed/closed and every member's current task is terminal; no transfer,
   collection or retry publisher is active.
2. The batch has the exact remote ID, folder creation/ownership receipts and an observed
   successful terminal remote outcome. Terminate acknowledgement alone does not prove that
   a shared worker has stopped writing; confirm terminal state before Trash.
3. Every admitted member has its validated durable producing-job result and exact required
   local raw/stage files, with their expected hashes and successful parse/merge validation.
   Flush all those files. A failed project snapshot does not undo the SQLite result receipt,
   but cleanup must still verify the actual local raw files.
4. The frozen cleanup-after-success policy enables it. The folder is exactly the generated
   batch folder and is never a root, runtime, template, sibling or unknown folder.

Journal pending before private upload and keep it pending through result commit/remote close.
Only then move the exact whole folder to UCloud Trash and confirm parent absence with existing
`trash_owned_folder`. Never trash individual task folders opportunistically while siblings or
retries may need them. Never empty Trash. Already absent plus exact ownership is idempotent
success. Record safe stage/code/status/time diagnostics as in ADR 0064.

Failed/cancelled/partial batches retain their folder by default with `cleanup_state=retained`
and an explicit reason, rather than claiming success cleanup. They remain inspectable in
Needs attention while private-file disposition is unresolved. A cleanup-only action may
finish existing saved-output recovery first. Moving retained partial/failed files requires a
distinct explicit whole-batch discard request after showing the affected members and confirming
all remote work is terminal; record that abandonment decision and verify local originals/frozen
inputs and all available successful outputs before Trash. This action must never appear as an
automatic result of Retry, Clear finished, Stop or deletion. If that discard action is deferred
from the first slice, retain the files and report the limitation; do not weaken cleanup guards.

Cleanup intent and Close/Stop are monotonic, and terminal cleanup permanently excludes admission.
A cleanup worker uses a CAS receipt/epoch and must not run until the coordinator has released
all upload futures. Holding a SQLite lock while performing Trash is not a substitute for that
ownership rule. Retry after Trash only finalizes verified local outputs; missing required local
files cannot trigger a new allocation disguised as recovery.

## 9. Local queue and interface changes

Proposed `runtime_batch.py` owns compatibility, attach/claim, status and shared actions;
`enhancement_api.py` supplies existing transport/readiness/speaker helpers and batch
specifications. Persistence stays in `store.py`; stage/merge meaning stays in enhancement
modules. No generic scheduler framework or native-batch protocol retrofit.

Coordinator interface: `enqueue(preflighted_members)`, `resume(batch_id)`, `stop(batch_id)`,
`cleanup(batch_id, discard=False)` and canonical read projection. Reuse the mocked UCloud
client seam; keep membership/publication mechanics inside the coordinator.

`JobQueue` gets a bounded CBS Runtime batch manager. Ordinary `claim_job('enhance-remote')`
excludes jobs with `runtime_batch_id`, so a member cannot also run through `Run`. Claim the
batch under `BEGIN IMMEDIATE`, increment its epoch and mark its eligible member jobs running.
One coordinator owns that batch until its I/O settles; several coordinators own different
batches. The owner separately claims each late member's queued-to-running transition under
the same transaction rules before preparing it; generic workers never claim those rows.
Keep current workers for legacy singletons and SSH recovery. Capacity counts their
known API allocations before allowing another new POST.

Reserve submission capacity atomically under the shared queue lock/transaction for both new
batch claims and legacy API singleton `Run.submit()`. Persist a `capacity_reserved` receipt;
count it, uncertain submissions and known nonterminal IDs. Merely counting currently observed
IDs races two POSTs and lets legacy submissions exceed the limit. Release a reservation only
on observed terminal state, proven no-POST failure or explicit no-allocation reconciliation.
Stop intent/acknowledgement alone does not release it. Known-ID reconnect/poll/cleanup remains
allowed after limits decrease; do not make recovery wait for permission to create a new GPU.

Extract reusable preparation/merge/finalization seams from `enhance_window`; the coordinator
invokes them asynchronously rather than faking each member's synchronous `run_api` return.
Singleton entrypoints stay valid; complete checkpoints need no inference runtime.

Enqueue integration must cover all producers, not just Transcribe selected:

- `/api/transcriptions`: freeze all groups outside locks; atomically create runtime member jobs
  and native/API jobs. A failed preflight queues none. No per-recording `add_job` loop may leave
  half a requested runtime group committed.
- Explicit/Automatic whole-result enhancements and passage re-transcription: same attach
  function after existing source/revision checks; keep repair/timing jobs out.
- `Store.add_result` post-ASR follow-up: create the existing exact frozen-source child and
  attach it using a transaction-internal no-network helper in the same transaction. Preflight
  must already have frozen its batch plan. It can join a compatible open batch at ASR completion.
  A rollback cannot leave a child, a membership reservation or a provider result alone.
- Retry/cancel/reconcile/cleanup job endpoints delegate through verified runtime membership
  before generic singleton logic. They return explicit shared scope and affected IDs.

Shutdown stops new claims/admission locally and drains local transfers while retaining the
library lease. It does not send user Stop or destroy remote working files. Persist local
interrupted states and leave recorded remote work for reconnect/owner-absence drain. Recovery
changes stale local running claims to interrupted only after acquiring the library lease;
never automatically rerun a batch or resume a POST. Queued never-submitted batches can start
normally. Update library-switch, storage-move and deletion guards to include live batch owners,
uncollected outputs, uncertainty and cleanup obligations independent of member job state.

## 10. Activity and safe presentation

Keep per-recording jobs in `/api/jobs` for editor badges. Add canonical batch fields for type
`cbs_runtime`, ID, state/admission, aggregate progress/counts, updated time, remote link,
deadline/accepting status, safe cleanup/recovery actions and ordered member outcomes. Use the
same card grouping mechanism introduced for native T83, but distinguish the batch kind and
unique run name. Do not hard-code `Batch transcription` for enhancement allocations.

Return no snapshots, text, credentials, paths or arbitrary worker output. Review links require
live results; trashed producer receipts still count as saved. Source-changed warnings are per file.

Calculate progress from the current admitted members and actual stages, with counts such as
`3 saved · 1 aligning · accepting files`. New admission may lower aggregate progress; state the
count instead of implying work was lost. Preserve expanded outcomes across polls. A member
failure cannot make a still-running healthy batch look terminal.

Query batch owners directly as well as recent job rows: unresolved batches survive the 200-job
limit, all-members-completed, missing/deleted member rows and Clear finished. Expose one Needs
attention card, not duplicate member/cleanup cards. Clear hides only resolved terminal batches
whose canonical updated time precedes the saved clear time; it never deletes any row/receipt.
Canonical actions cannot depend on whichever member happened to sort first.

## 11. Ordered vertical implementation slices and acceptance tests

Each slice includes its SPEC/TODO progress and numbered ADR work when its behavior becomes
accepted. Do not mark T85 complete after the schema, extra threads or card grouping alone.

| Slice | Concrete implementation / reviewable behavior | Required synthetic tests |
| --- | --- | --- |
| 1. Durable owner and exact grouping | Schema 12, compatibility function, atomic runtime enqueue/membership, legacy isolation. No remote batch submission until protocol exists. | Schema-11 backup/migration/idempotency/rollback; exact preservation of old rows; every compatibility field independently splits groups; differing source IDs can group; same project only; duplicate active recording rejected; native and SSH unchanged; all-or-none enqueue; add-result/follow-up rollback. |
| 2. One selected group, one allocation | New Terminal supervisor/controller protocol, readiness, per-task commits/receipts, serial stage execution, independent result import. Initially close admission after the initial group. | N selected files cause exactly one POST/specification and N exact task uploads/results; first-device VRAM/model refusal uploads no private bytes; incorrect manifest/generation/path/hash rejected; partial/duplicate upload and acceptance replay do not duplicate inference; sibling import failure preserves successful results; result+member receipt atomic; no Trash before result/raw durability. |
| 3. Open admission and sealing | Add while queued/starting/running; coordinator-only publication; idle grace, immutable seal and deadline/owner-absence drain. | Deterministic barriers race attach versus seal, upload versus seal, retry versus seal, Stop versus attach; winning reservation included exactly once; closing winner routes new request to another queued batch; partial input publication never executes; orphan drain does not lose accepted outputs or claim unpublished tasks; stale revision cannot reopen/overwrite stop; late deadline cutoff. |
| 4. Recovery, shared Stop and cleanup | Batch endpoint delegation, explicit attempts, reconnect/reconcile, checkpoint recovery, terminal/cleanup guards and retained-failure disposition. | Lost POST reply makes no second POST; exact-name mismatch fails; every fresh allowed POST resets absence receipt; lost ready/acceptance replies reuse exact task; crash before/after result commit, raw flush, terminal observation and Trash response; completed members/Trash never recreated; no sibling termination by member; terminal allocation receives no unnecessary Stop; cleanup-only retries perform zero allocation/inference/upload; failed/partial policy and discard scope. |
| 5. Concurrent allocations and loaded-model reuse | Capacity setting/reservations, bounded coordinator/preparation pools, disjoint claim kinds, stage cohorts and one-model cache. | Two incompatible batches overlap with isolated journals/paths/cancellation; third waits; race new-batch and legacy singleton POST reservations; uncertainty consumes capacity; lower limit preserves owners/reconnect/local repairs; no double claim; different-model ASR jobs on one recording remain valid; fake model factory counts one load per stage/cohort and unloads before a different type; late cohorts cannot starve final stages; OOM never automatically repeats failed inference. |
| 6. Activity and complete user flow | One runtime card with live admission/deadline, expandable exact outcomes, shared actions and retained obligations; all enqueue producers use the batch path. | Browser selected-many gives one card and per-recording results; late compatible file joins same card; different model/settings gives another; completed first member cannot hide running siblings; Stop names N files; expanded outcomes survive polling; source-change/trashed/removed member cases; unresolved owner beyond recent limit and Clear finished; follow-ups and passage re-transcription bind to exact source evidence. |

Retain existing enhancement API/lane, native batch, feature follow-up, job receipt and Activity
tests. Add runtime store/protocol/queue/browser tests with temporary synthetic libraries, fake
clocks, barriers and mocked partial file publication/lost POST responses. Check generated Bash
and the standalone controller on Linux; Windows mocks do not establish flock/container behavior.

Run pytest, Ruff check/format and browser acceptance for the affected slices. No real library,
paid service, private audio, model download or live GPU allocation is involved. Separately
authorized live acceptance should exercise selected-many + late file, two allocations,
reconnect, shared Stop, partial failure and confirmed Trash on licensed public media. Measure
model loads, first-device peak VRAM, idle/allocation/end-to-end time before claiming savings.

Implementation must record the chosen ADR and invariants, amend V95's sequential UCloud-lane
wording while retaining sequential local/GPU-stage guarantees, and leave T85 open until all six
slices work through UI, queue and protocol. Automated and live evidence remain distinct.
