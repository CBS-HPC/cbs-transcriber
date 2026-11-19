# 0184 — Ask before a full GPU when no native 1g MIG is free

Status: **proposed design, 19 November 2026; user decisions recorded the same day; not
implemented, not independently reviewed, no live evidence collected.** Once accepted it
amends ADR 0124 and V76 for native UCloud Transcriber only. CBS Runtime (ADR 0063/V112) and
the funding-based CPU rule (ADR 0177/V220) are unchanged. SPEC V233/T154 are reserved for it.

## Context

Fresh native requests freeze `native_allocation` = exact `gpu-nvidia-b200-1-mig.1g /
gpu-nvidia-b200 / ucloud` (ADR 0124, user decision 13 November). A batch whose MIG job sits in
UCloud's queue just waits; Activity shows the raw state (`UCloud batch · IN_QUEUE`, members
"Waiting for UCloud allocation"). On 19 November the user decided: when no MIG is free,
Activity asks **per batch** whether to use a full GPU, stating its higher credit use (about 7×
per hour); nothing more expensive happens without that click.

What the pinned client and code offer (read only, no network):

- `ucloud_workflow.availability.check_machine_availability` reads `GET /api/jobs/retrieveProducts`
  and normalises each product's `support.queueStatus` to AVAILABLE/BUSY/FULL/UNKNOWN; a
  requested product that is absent comes back `found=False, UNKNOWN`. Malformed or duplicate
  entries raise. There is no queue position or free-instance count. Its `submit_immediate_job`
  submits and then terminates after a startup timeout; that policy is not used here.
- The checked upstream scheduler (ADR 0177 evaluation, commit `2c23d295`) simulates one and two
  copies per product: AVAILABLE = both fit, BUSY = one fits, FULL = none. It is a snapshot, not
  a reservation, and may ignore project limits and already queued jobs (to be confirmed).
- `catalog.py` statically lists the MIG at 1/7 GPU-hours/hour and `gpu-nvidia-b200-1-gpu` at
  1 GPU-hour/hour (the 7× ratio). `runtime_compute.select` already verifies live MIG metadata
  (fraction 1/7, `B200-mig.1g.23gb`, Docker, funded wallet, AVAILABLE/BUSY; read 26 October).
  `supports_utilization_report` and `utilization.py` summarise a finished job's CSV report;
  they cannot observe queueing and are not used for detection.
- `native_batch.run_batch` polls the remote job every 5 s and, once running,
  `out/transcribe.log` every 15 s. Uploads, folder ownership and the POST are journaled on the
  batch row (ADR 0045/0100); reconciliation checks the name `CBS Transcriber batch <id>`.
- `native_capture` requires every descriptor's submitted product to equal `native_allocation`
  and all attempts to share it; generic writes cannot change it. `native_startup` (ADR 0174)
  hashes frozen params plus journal identity into its reconnect generation.
- `ucloud_funding.observe` (ADR 0178) is an unwired, bounded wallet observer for category
  `gpu-nvidia-b200`; the current native preflight only checks a positive `maxUsable`.

## User decisions (19 November)

1. **No queue-time threshold.** Ask as soon as two UCloud capacity reads agree that the 1g MIG
   is full while a full B200 is free. The user notes that a full GPU also transcribes much
   faster (user statement; not measured by this app). Any failed or unknown read means no
   question.
2. **Live read-only evidence** listed below may be collected with the saved key once the
   implementation is ready. No jobs, uploads or spending; cancelling a queued job and a paid
   full-GPU test each still need separate approval.
3. **Retry after a switch returns to the 1g MIG** when one is available; if the MIG is full
   again, the same question applies. Consent never carries over to a later submission.
4. **Hidden in MVP v2.**
5. Adopted proposals: single-recording requests get the same question; withhold the question
   if live data does not confirm the ~7× relation; after "Keep waiting" the question stays
   collapsed for that wait; no up-front overnight choice now.

## Decision (proposed)

### Scope

One question per native allocation owner and wait: a shared native batch or a singleton native
job (a batch of one). Only owners whose admission froze the exact MIG and whose next submission
would use it. Never: legacy jobs without `native_allocation`, frozen full-GPU or template-bound
jobs, CBS Runtime, repairs, enhancements, restored/archived owners.

### Ask before the first POST where possible

Re-examined at the user's request: asking **before** the MIG POST is safer than switching a
queued job. With no allocation yet there is nothing to cancel, no start/terminate race, no
superseded remote job, no never-started proof and no second run name to reconcile; consent
leads to exactly one POST, whose only hazard is the existing uncertain-submission rule.
Its cost is a short hold: while the question waits, no MIG job holds a queue place, and an
unattended batch must not stall. So both paths exist:

- **Before POST (preferred).** Capacity reads start while the last uploads finish. If a read
  shows the MIG AVAILABLE/BUSY, or any read fails or is unknown, the batch is submitted to the
  MIG at once, as today. If two reads agree (below), the batch holds before its POST and asks.
  An answer of "full GPU" submits once on the full GPU; "1g MIG", or no answer within
  **5 minutes** (`HOLD_MINUTES`, proposed), submits on the MIG, and the question then counts
  as declined for that wait.
- **After POST (fallback).** For a batch that queues anyway (capacity changed after its POST,
  a read failed before it, or it was submitted after an unanswered hold), the same question
  appears in the queue once two reads agree; the switch uses cancel-and-prove (below).

MVP never holds: with the feature hidden the batch is submitted to the MIG immediately.

### Detecting "no MIG free"

All of:

1. **Owner state.** The owning worker is at the submission boundary (every input uploaded and
   verified, no `submission_attempted`, no remote ID) or in the monitor phase with the latest
   `retrieve_job` reporting `IN_QUEUE` and a complete `updates` list without RUNNING or later
   states. No uncertain submission; no Stop/cancel/termination/release/deleted-member flags;
   members, settings and project unchanged; cleanup not pending.
2. **Capacity.** Two consecutive reads of `GET /api/jobs/retrieveProducts`, at least 30 s apart
   (`READ_SPACING`, calibrated by L4), in the frozen project context and credential scope, both
   report the exact MIG **FULL** with no other MIG status between them, and the latest reports
   the exact full B200 **AVAILABLE or BUSY** (one fits). There is no queue-time threshold.
   At most one read per 30 s per owner; reads continue while the owner stays eligible.
3. **Identity and funding.** MIG passes the `runtime_compute.select` checks. The full product is
   unique `gpu-nvidia-b200-1-gpu` in the MIG's category/provider, COMPUTE, `gpu == 1`,
   supported by the frozen Transcriber application's backend (as discovery `_products`), and
   that category has a positive current wallet in the batch's project.
4. **Price relation.** Live metadata reproduces the documented relation (MIG fraction 1/7; full
   product one whole GPU; any price/unit fields consistent). Otherwise the question is withheld
   (user decision 5).

Any failed, malformed, UNKNOWN or missing read means **no question**; the batch proceeds or
keeps waiting on the MIG. The question is withdrawn as soon as any condition stops holding.
Each continuous eligible period gets one random `offer_id`; refreshed reads keep it while
conditions hold. Reads are bounded (1 MiB response, short deadline) and run outside
queue/snapshot/writer locks. A durable `queued_first_seen_at` is kept only for display.

### What Activity shows

Neutral queue text replaces the raw state for every native owner, also in MVP:
`Waiting for a 1g MIG on UCloud · queued 3 min`. The question appears on the batch card (not
on each member), with counts filled in (singleton: "This recording is uploaded…").

Before POST:

> **No 1g MIG is free on UCloud**
>
> This batch of 12 files is uploaded and ready. UCloud reports that no 1g MIG is free and that
> a full B200 GPU is available now.
>
> A full GPU transcribes much faster but uses about 7 times as many GPU credits per hour as a
> 1g MIG: 1 GPU-hour per hour instead of 1/7. Credits come from the project's GPU allocation.
> UCloud does not reserve the free GPU, so the full-GPU request can also wait.
>
> If you do not choose within 5 minutes, the batch joins the 1g MIG queue.
>
> `[Join the 1g MIG queue]` `[Use a full GPU (about 7× credits)]`
>
> Checked 14:05. Nothing more expensive happens unless you choose a full GPU.

In the queue the first paragraph reads "This batch of 12 files is waiting for a 1g MIG (queued
3 min)…", the hold sentence is replaced by "If you switch, the waiting MIG request is cancelled
first and the uploaded files are reused.", and the first button is `[Keep waiting for a 1g MIG]`.

The MIG button has initial focus and is the visually primary choice; the cost is in the other
button's label. One click is the consent; no second dialog. Declining (either button, or an
expired hold) collapses the question to one line, `Use a full GPU… (about 7× credits)`, for
the rest of that wait, which ends when the MIG job leaves the queue or the batch is stopped or
retried. It is never prompted again within that wait (user decision 5).

Progress and outcomes (never a raw state):

- `Requesting a full GPU…` (before POST) or `Switching to a full GPU · cancelling the waiting
  MIG request…` then `The MIG request was cancelled before it started · requesting a full GPU…`
- `Waiting for a full GPU on UCloud · queued 2 min` / `Processing on UCloud · full GPU`
- `No answer · joined the 1g MIG queue. You can still switch.`
- `A 1g MIG became free first. This batch continues on the MIG; no full GPU was requested.`
- `A full GPU is no longer free. This batch keeps waiting for a 1g MIG; no full GPU was
  requested.`
- `UCloud has not confirmed that the MIG request stopped. No full GPU was requested.`
  `[Check again]` `[Stop UCloud batch (12 files)]`
- `The MIG request started before it could be cancelled and was stopped. No full GPU was
  requested. Retry recovers the files it finished.`
- `Could not confirm the project's GPU funding. No full GPU was requested.`

### Consent: per batch, single-use and recorded

`POST /api/native-batches/{id}/full-gpu` (singleton: `/api/jobs/{id}/full-gpu`), same-origin,
body `{offer_id}`. One writer transaction holding the allocation control slot verifies the
current `offer_id`, phase, remote ID (if any), member set, no Stop flags and the feature switch,
then appends one immutable entry to the params list `native_allocation_consents` (at most 8),
identical on the owner row and every member: policy `native-full-gpu-consent-v1`; phase
(`before_post`/`queued`); `from` (MIG) and exact `to` product; the capture attempt number it
authorises; `superseded_remote_id` (queued phase only); member job IDs; `offer_id`;
`decided_at`; wording version `native-full-gpu-ask-v2`; displayed ratio `7`; bounded
observation (status reads and times). Mutable progress lives in
`ucloud_run.allocation_switch.stage`. Returns 202 (pending in the UI); 409 for a stale offer;
403 `feature_disabled` in MVP. Duplicate clicks are idempotent.

Consent authorises exactly **one** full-GPU submission, the attempt it names. It is consumed
when that attempt's `submission_attempted` is journaled, and lapses unused on restart, Stop,
Retry, member change or a stale re-check. Settings, Retry, reconnect, routing and earlier
consents never create or widen it. Result sources record the consent and the product actually
used (ADR 0123 provenance).

### Run names

The admission MIG's first attempt keeps `CBS Transcriber batch <id>` (singleton
`CBS Transcriber <job-id>`). Every full-GPU attempt, and any attempt after a superseded remote
job, is named `CBS Transcriber batch <id> attempt <n> full-gpu` or `... attempt <n> mig`, with
`n` the capture attempt number, so exact-name reconciliation (ADR 0021) can never match a
superseded job. Reconcile accepts only the current attempt's name and refuses superseded IDs.

### Before POST: one submission

At the hold, the batch has `uploaded` receipts and descriptors not yet written. Consent →
fresh re-check (MIG still FULL, full GPU AVAILABLE/BUSY, funding) → members write their ADR 0100
descriptor for this attempt with the consented product → `submission_attempted` → POST once →
`job_id`. A failed re-check lapses the consent and submits on the MIG. A lost reply is an
uncertain submission under the existing rules. Nothing is cancelled.

### In the queue: cancel and prove, never two allocations

The owning worker executes, network outside locks, each step a CAS on stage, remote ID, members
and Stop flags. Stages: `consented → terminate_requested → mig_terminal → superseded →
submitted`, or `lapsed`/`withdrawn`/`mig_started`/`unproven` with a reason.

1. **Fresh re-check.** Re-read the job (still `IN_QUEUE`, no RUNNING update) and products (MIG
   still FULL, full GPU AVAILABLE/BUSY) and funding. Otherwise `lapsed`; no remote write.
2. **Journal intent** `terminate_requested` with the exact MIG remote ID, then POST terminate
   for that ID only. The switch uses its own flag, not the Stop flags, so Stop stays distinct.
3. **Terminal observation.** Up to six bounded, cancellable reads over about two minutes (the
   `runtime_capacity.drain` pattern), then the normal cadence with `not confirmed` shown.
   Terminate errors, CANCELING or silence never lead to a POST.
4. **Never-started proof.** Terminal state; complete `updates` without RUNNING; `startedAt`
   null or absent (if that field is confirmed); a complete bounded listing of `out/` that is
   empty (no `transcribe.log`, no output); every owned input (`input/<job-id><suffix>`; a
   singleton's `recording<suffix>`) is present with its recorded size. Failure is
   `mig_started` (existing download/Retry recovery of that remote ID applies) or `unproven`
   (no POST, explicit Check again/Stop).
5. **Supersede** in one writer transaction: append `{remote_id, product, terminal_state,
   update_states, out_empty, observed_at, submission_sha256}` to `ucloud_run.superseded_attempts`
   and reset only current-attempt fields (`job_id`, `remote_state`, `submission_attempted`,
   reconciliation, `artifact_submission_sha256`, log/output-error/startup-reconnect/last-failure
   receipts). Keep `owned_folder`, `root_created`, `folders_created`, `uploaded`,
   `directory_parameter` and `output_*`.
6. **Submit once** through a dedicated switch branch, never the generic no-ID branch: the
   consented product, frozen hours/settings and the attempt name above. Members write their
   next ADR 0100 descriptor bound to the consent, then `submission_attempted`, POST, `job_id`.
   Monitoring restarts its deadline; import, capture and cleanup are as today.

The switch keeps the owner's single allocation slot and never counts as an extra allocation
under the decided, unimplemented "UCloud Transcriber batches at once" limit. A residual race
remains: UCloud has no "terminate only if still queued", so a MIG job starting between step 1
and the terminate is stopped; step 4 detects it and no full GPU is requested. Asking before
POST avoids this race whenever the MIG is already full at submission.

### Stop, restart, reconnect, Delete, Retry

- **Stop wins** at every stage; consent and every step recheck it in the same transaction.
  During a hold Stop means no POST at all. In the queue before step 2 Stop stops the MIG as
  today; during steps 3–5 no POST follows; after a full-GPU `job_id` Stop terminates that job.
- **Restart** before a consented POST lapses the consent (`app_restart`), metadata only.
  A hold or step 5 interrupted by restart leaves no allocation; the batch is interrupted and
  Retry re-decides. **Steps 2–4**: `terminate_requested` is a negative for ADR 0174 automatic
  reconnect; Activity shows `Switching to a full GPU was interrupted.` `[Continue switch]`
  `[Stop]`. Continue re-observes: IN_QUEUE → terminate the same ID again; RUNNING →
  `mig_started`, no terminate, continue on the MIG; terminal → step 4, then the same Retry rule.
  **After `job_id`**: ADR 0174 may resume retrieval; its generation includes the consent.
- **Retry** (user decision 3) never creates, reuses or widens consent. If a remote job is
  recorded, Retry reconnects to it as today, including a full-GPU job. If a new submission is
  needed (no remote job, or a certain non-creation), Retry returns to the 1g MIG: it runs the
  before-POST capacity check, submits on the MIG when the reads show it free or are failed or
  unknown, and asks again only when two reads agree that the MIG is full.
- **Reconnect-only owners** (ADR 0174) may display a queued question (read-only). Accepting it
  is an explicit user action: the consent writer binds it to that lifecycle and re-issues the
  pinned authority in the same transaction. The executor is a separate branch gated by the
  consent; the reconnect no-ID fallback keeps refusing.
- **Delete** (ADR 0092/0101) contends for the same allocation control slot; a deleted member
  before the consented POST voids the consent (member set changed).
- **Cleanup** (ADR 0045) never runs during a hold or switch. A superseded MIG used the same
  owned folder, so only the final attempt's success can trigger cleanup; the MIG's platform
  job directory is never touched.

### Uploads already done

Both paths reuse `cbs-batch-<id>/input` and its upload receipts; nothing is re-uploaded. The
question needs every input uploaded and verified, so it follows the decided earlier-upload
change (upload each file once its inspection passes) without overlapping it. A non-empty
`out/` or a missing/resized input blocks a queued switch with its reason, preserving everything.

### Funding

MIG and full GPU are expected to share the `gpu-nvidia-b200` wallet (to confirm, L2). Slice 1
uses the existing positive current `maxUsable` check for the full product's category in the
batch's project; a failed or unknown read means no question. Once ADR 0178's observer is wired:
`positive_current` allows the question; `no_usable_balance`, `absent` and `unknown` do not.
Positive is never affordability (`not_checked`), so the question states credits per hour, not
a total. Congestion never authorises CPU (ADR 0177); the consented full GPU is the only
congestion remedy, and only for the clicked batch and attempt.

### Release profile

New feature key `native_full_gpu` ("Using a full GPU when no 1g MIG is free"), hidden in MVP v2
(user decision 4): no capacity hold, no question panel (neutral queue text stays) and the
consent route refuses with 403 `feature_disabled`. Owners consented under the full profile keep
Stop, Continue switch, Retry, reconciliation and cleanup open in MVP (ADR 0145); in MVP Retry
returns to the MIG without asking. Tests pin `full`; MVP tests set the profile.

### Contract changes required at implementation

- `native_capture`: a pure, pinned validator for `native_allocation_consents`; descriptors and
  `Store.add_result` accept the full product only for the attempt a valid consent names, every
  other attempt still equal to `native_allocation`. `assert_params_preserved` covers the new
  key. Repin the portable checker.
- `native_startup`: switch flags/stages as negatives; generation includes the consents.
- `remote_jobs`: per-attempt run names; refuse superseded IDs.
- `native_batch`/`ucloud_native`: capacity reads, the before-POST hold, the queued executor and
  the Activity projection (`status_fields` gains a bounded offer object, never raw product or
  wallet bodies); Retry's return to the MIG.
- `release_profile`, `app.js` Activity panel. On acceptance update V76, ADR 0124's status
  and CONTEXT.

## Synthetic tests before activation

Mocked SDK/HTTP clients and temporary libraries only; no live calls.

- Detection: two agreeing FULL reads ask at once (no queue time); a single FULL, reads under
  30 s apart, FULL then BUSY flapping and RUNNING in history never ask; MIG/full UNKNOWN,
  missing, duplicate, malformed or foreign-provider; identity mismatches (category, gpu,
  gpuModel, fraction, backend support); unfunded, failed or unknown wallet; price mismatch;
  each owner-state exclusion; legacy, template, full-GPU and Runtime owners never asked; read
  failures, timeouts and oversized responses withdraw the question.
- Before POST: a free or unknown MIG read submits on the MIG without delay; hold answered
  "full GPU" makes exactly one full-GPU POST and no terminate call; "1g MIG" and hold expiry
  submit on the MIG and collapse the question for that wait; Stop during the hold makes no
  POST; restart during the hold submits nothing; MVP never holds.
- Consent: stale/missing `offer_id` 409; 403 in MVP; same-origin; atomic owner+member append;
  idempotent double click; single use (consumed at `submission_attempted`); concurrent Stop or
  Delete wins; Retry/reconnect/settings never create consent; byte-identical params for every
  non-consented job.
- Queued executor: re-check lapses (MIG started, full GPU now FULL, funding unknown) without
  remote writes; spy ordering journal→terminate→observe→supersede→descriptor→
  `submission_attempted`→POST; terminate error/timeout, CANCELING and silence never POST;
  RUNNING update, `startedAt`, log or output present, input missing/resized → no POST; exactly
  one POST across every crash point; lost POST reply → uncertain and current-name-only
  reconciliation; superseded ID refused; attempt names unique.
- Retry: reconnects to a recorded full-GPU job; after supersede or a certain non-creation it
  returns to the MIG when reads show it free or unknown and asks only on two agreeing FULL reads.
- Crash/restart at each stage boundary with real queue reopen; ADR 0174 refuses steps 2–4;
  Continue switch from IN_QUEUE/RUNNING/terminal.
- Stop at every stage; Delete of one member and of the project; cleanup only after the final
  success; restored owners inert.
- Evidence: multi-attempt capture validates; `add_result` rejects the full product without a
  consent naming that attempt; provenance shows both; repinned portable checker; existing
  frozen fixtures unchanged.
- Activity: exact wording for both phases with counts and minutes; MIG button focused; collapse
  after decline or expiry; 202 pending; late-response fences; neutral queue text in MVP and full.
- Concurrency: a switch never changes the allocation count; two batches each need their own
  click, and the second lapses if the full GPU is no longer free.

## Live evidence before activation (read-only; permission granted 19 November)

The user permits collecting these observations with the saved key once the implementation is
ready: the user's project, the app's own credential scope, bounded reads, no jobs, uploads or
spending. Record bounded scalars, never tokens or transcript text.

- **L1 Products**: `GET /api/jobs/retrieveProducts` several times, including while a batch is
  queued. For both exact products record `support.queueStatus`, product/category identity,
  `productType`, `gpu`, `gpuModel`, `fraction`, `price`, the category's accounting unit and
  frequency, `freeToUse`, backend/Docker support and maintenance fields.
- **L2 Wallets**: `GET /api/accounting/v2/browseWallets` (complete pagination,
  `includeChildren=false`): confirm both products bill one `gpu-nvidia-b200` wallet and its units.
- **L3 History**: `GET /api/jobs/browse` and `GET /api/jobs/retrieve?includeUpdates=true` for
  the user's past native batches: the `updates[]` shape (state, timestamp, status),
  `startedAt`/`createdAt` presence, IN_QUEUE→RUNNING durations, and the terminal state and
  updates of any job stopped while queued.
- **L4 Natural queue**: while one of the user's own batches is queued anyway, read the job and
  products every 30 s: does FULL accompany queueing, how often does it flap, is the full GPU
  AVAILABLE/BUSY at the same time, does the status count already queued jobs, and is `out/`
  empty (listing only)? Calibrates `READ_SPACING` and the hold.
- **L5 Application**: Transcriber Batch 1.12 metadata (`/api/hpc/apps/byNameAndVersion`):
  backend support for the full product and any product-dependent parameters.

Still **not authorised**; each needs a separate explicit approval: terminating a deliberately
queued MIG job to observe its terminal state and wallet usage (if L3 has no example), and one
public-clip full-GPU batch for output comparability, speed and actual credit use.

## Public sources to confirm (no credentials)

- UCloud `monitoring.go` at `2c23d295` (queueStatus simulation: project limits, queued jobs).
- `provider-integration/shared/pkg/orchestrators/jobs.go`: JobState values, JobStatus
  `startedAt`, JobUpdate fields.
- The core orchestrator's terminate handling for a queued job (which terminal state results)
  and where the K8s provider starts charging (RUNNING only?).
- Accounting units and prices: `shared/pkg/accounting/products.go` and
  `k8s/shared/products.go` at `2c23d295`, for MIG versus full B200.
- [Machine types](https://docs.cloud.sdu.dk/guide/submitting.html#machine-type) and
  [Transcriber](https://docs.cloud.sdu.dk/Apps/transcriber.html): charging per actual running
  time and full-GPU use of Transcriber Batch.

## Remaining proposals to confirm

- The 5-minute before-POST hold before an unanswered batch joins the MIG queue.
- The 30-second spacing between the two agreeing capacity reads (calibrated by L4).

## Not covered

No implementation, review, live read, allocation, cleanup or price measurement. Capacity
signals are not reservations; this app has not measured full-GPU speed or whether its text
matches the MIG's. CPU fallback, parallel MIGs and CBS Runtime are separate decisions.
