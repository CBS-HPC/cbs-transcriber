# 0184 — Ask before a full GPU when no native 1g MIG is free

Status: **proposed design, 19 November 2026; not implemented, not independently reviewed, no
live evidence collected.** Once accepted it amends ADR 0124 and V76 for native UCloud
Transcriber only. CBS Runtime (ADR 0063/V112) and the funding-based CPU rule (ADR 0177/V220)
are unchanged. SPEC V233/T154 are reserved for it.

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

## Decision (proposed)

### Scope

One offer per native allocation owner: a shared native batch, and (open question 2) a singleton
native job treated as a batch of one. Only owners whose admission froze the exact MIG and that
have no switch yet. Never: legacy jobs without `native_allocation`, frozen full-GPU or
template-bound jobs, CBS Runtime, repairs, enhancements, restored/archived owners.

### Detecting "no MIG free"

Both a sustained queue **and** provider capacity evidence are required; either alone is
insufficient (a snapshot can clear in seconds; a long queue can be image/scheduling delay,
project limits or maintenance, which a full GPU would not fix). All of:

1. **Owner state.** Monitor phase of the owning worker; recorded remote ID; no uncertain
   submission; no Stop/cancel/termination/release/deleted-member flags; members, settings and
   project unchanged; cleanup not pending.
2. **Queue duration.** The latest `retrieve_job` reports `IN_QUEUE` and its complete `updates`
   list contains no RUNNING or later state. The app has seen this remote ID `IN_QUEUE` for at
   least **10 minutes** (`QUEUE_MINUTES`), measured from a durable UTC `queued_first_seen_at`
   checkpointed at the first observation, with at least one fresh observation in the current
   lifecycle. This lower bound never exceeds the true wait. Server update timestamps are
   recorded as evidence only, until their shape is confirmed.
3. **Capacity.** From minute 9, at most one products read per 60 s per owner, in the frozen
   project context with the frozen credential scope: the exact MIG reports **FULL** in two
   consecutive reads at least 60 s apart, and the latest read reports the exact full B200
   **AVAILABLE or BUSY** (one fits).
4. **Identity and funding.** MIG passes the `runtime_compute.select` checks. The full product is
   unique `gpu-nvidia-b200-1-gpu` in the MIG's category/provider, COMPUTE, `gpu == 1`,
   supported by the frozen Transcriber application's backend (as discovery `_products`), and
   that category has a positive current wallet in the batch's project.
5. **Price relation.** Live metadata reproduces the documented relation (MIG fraction 1/7; full
   product one whole GPU; any price/unit fields consistent). Otherwise no offer (open question 3).

Any failed, malformed, UNKNOWN or missing read means **no offer**; the batch keeps waiting.
The offer is withdrawn as soon as any condition stops holding. A MIG read of AVAILABLE/BUSY
while still queued shows "UCloud reports a free 1g MIG; still waiting". Each continuous
eligible period gets one random `offer_id`; refreshed reads keep it while conditions hold.
Reads are bounded (1 MiB response, short deadline) and run outside queue/snapshot/writer locks.

### What Activity shows

Neutral queue text replaces the raw state for every native owner (also in MVP):
`Waiting for a 1g MIG on UCloud · queued 23 min`. When an offer exists, the batch card (not each
member) shows, with N files and minutes filled in (singleton: "This recording has waited…"):

> **No 1g MIG is free on UCloud**
>
> This batch of 12 files has waited 23 minutes for a 1g MIG. UCloud reports that no 1g MIG is
> free and that a full B200 GPU is available now.
>
> A full GPU uses about 7 times as many GPU credits per hour as a 1g MIG: 1 GPU-hour per hour
> instead of 1/7. It may finish sooner, but that has not been measured, so expect the same
> files to cost more. Credits come from the project's GPU allocation.
>
> If you switch, the waiting MIG request is cancelled first and the uploaded files are reused.
> UCloud does not reserve the free GPU, so the full-GPU request can also wait.
>
> `[Keep waiting for a 1g MIG]` `[Use a full GPU (about 7× credits)]`
>
> Checked 14:05. Nothing changes unless you choose a full GPU.

"Keep waiting" has initial focus and is the visually primary choice; the cost is in the other
button's label. One click is the consent; no second dialog. "Keep waiting" records the declined
`offer_id` and collapses the panel to one line (`Use a full GPU… (about 7× credits)`) while the
same offer holds; it never prompts again for that offer (open question 6).

Progress and outcomes (never a raw state):

- `Switching to a full GPU · cancelling the waiting MIG request…`
- `The MIG request was cancelled before it started · requesting a full GPU…`
- `Waiting for a full GPU on UCloud · queued 2 min` / `Processing on UCloud · full GPU`
- `A 1g MIG became free first. This batch continues on the MIG; no full GPU was requested.`
- `A full GPU is no longer free. This batch keeps waiting for a 1g MIG; no full GPU was
  requested.`
- `UCloud has not confirmed that the MIG request stopped. No full GPU was requested.`
  `[Check again]` `[Stop UCloud batch (12 files)]`
- `The MIG request started before it could be cancelled and was stopped. No full GPU was
  requested. Retry recovers the files it finished.`
- `Could not confirm the project's GPU funding. No full GPU was requested.`

### Consent: frozen per batch and recorded

`POST /api/native-batches/{id}/full-gpu` (singleton: `/api/jobs/{id}/full-gpu`), same-origin,
body `{offer_id}`. One writer transaction holding the allocation control slot verifies the
current `offer_id`, the MIG remote ID, the member set, no Stop flags and the feature switch,
then writes an immutable params key `native_allocation_switch`, identical on the owner row and
every member: policy `native-full-gpu-switch-v1`; `from` (MIG) and exact `to` product;
`superseded_remote_id`; member job IDs; `offer_id`; `decided_at`; wording version
`native-full-gpu-ask-v1`; displayed ratio `7`; bounded observation (first-seen time, status
reads and times). Progress lives separately in the mutable journal
`ucloud_run.allocation_switch.stage`. Returns 202 (pending in the UI); 409 for a stale offer;
403 `feature_disabled` in MVP. Duplicate clicks are idempotent. Settings, Retry, reconnect,
routing and earlier consents never create or widen it. Result sources record the switch and
the product actually used (ADR 0123 provenance).

### Switching safely: never two allocations

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
   consented product, frozen hours/settings and a distinct run name
   `CBS Transcriber batch <id> full GPU`. Members write their next ADR 0100 descriptor bound to
   the switch, then `submission_attempted`, POST, `job_id`. A lost reply is an uncertain
   submission under ADR 0021: reconcile accepts only the full-GPU name and refuses any
   superseded remote ID. Monitoring restarts its deadline; import, capture and cleanup are as
   today. A singleton uses `cbs-<job-id>`, its `recording<suffix>` input and the run name
   `CBS Transcriber <job-id> full GPU`.

The switch keeps the owner's single allocation slot and never counts as an extra allocation
under the decided, unimplemented "UCloud Transcriber batches at once" limit. A residual race
remains: UCloud has no "terminate only if still queued", so a MIG job starting between step 1
and the terminate is stopped; step 4 detects it and no full GPU is requested.

### Stop, restart, reconnect, Delete, Retry

- **Stop wins** at every stage; consent and every step recheck it in the same transaction.
  Before step 2 Stop withdraws the consent and stops the MIG as today; during steps 3–5 no
  POST follows; after step 6 Stop terminates the full-GPU job (existing behaviour).
- **Restart before step 2**: startup marks the consent `lapsed` (`app_restart`), metadata
  only; the offer may recur. **Steps 2–5**: `terminate_requested` is a negative for ADR 0174
  automatic reconnect; Activity shows `Switching to a full GPU was interrupted.`
  `[Continue switch]` `[Stop]`. Continue re-observes: IN_QUEUE → terminate the same ID again;
  RUNNING → `mig_started`, no terminate, continue on the MIG; terminal → step 4.
  **After step 5, before the POST**: no allocation exists; `[Retry on a full GPU (about 7×
  credits)]` uses the frozen consent; never automatic. **After `job_id`**: ADR 0174 may resume
  retrieval; its generation includes the switch record.
- **Reconnect-only owners** (ADR 0174) may display an offer (read-only). Accepting it is an
  explicit user action: the consent writer binds it to that lifecycle and re-issues the pinned
  authority in the same transaction. The executor is a separate branch gated by the consent;
  the reconnect no-ID fallback keeps refusing.
- **Delete** (ADR 0092/0101) contends for the same allocation control slot; a deleted member
  before step 5 voids the consent (member set changed).
- **Retry** never creates or widens consent. Before step 5 it acts on the MIG remote ID as
  today; after step 5 it submits only the consented full GPU, so labelled.
- **Cleanup** (ADR 0045) never runs during a switch. The superseded MIG used the same owned
  folder, so only the final attempt's success can trigger cleanup; the MIG's platform job
  directory is never touched.

### Uploads already done

The switch reuses `cbs-batch-<id>/input` and its upload receipts; nothing is re-uploaded. An
offer exists only after the POST, so the decided earlier-upload change (upload each file once
its inspection passes) has no overlap. A non-empty `out/` or a missing/resized input blocks the
switch with its reason, preserving everything.

### Funding

MIG and full GPU are expected to share the `gpu-nvidia-b200` wallet (to confirm, L2). Slice 1
uses the existing positive current `maxUsable` check for the full product's category in the
batch's project; a failed or unknown read means no offer. Once ADR 0178's observer is wired:
`positive_current` allows the offer; `no_usable_balance`, `absent` and `unknown` do not.
Positive is never affordability (`not_checked`), so the ask states credits per hour, not a
total. Congestion never authorises CPU (ADR 0177); the consented full GPU is the only
congestion remedy, and only for the clicked batch.

### Release profile

New feature key `native_full_gpu` ("Using a full GPU when no 1g MIG is free"). MVP hides the
offer panel (neutral queue text stays) and refuses the consent route with 403
`feature_disabled`. Already consented owners keep Stop, Continue switch, Retry on a full GPU,
reconciliation and cleanup open in MVP (ADR 0145). Tests pin `full`; MVP tests set the profile.

### Contract changes required at implementation

- `native_capture`: a pure, pinned validator for `native_allocation_switch`; descriptors and
  `Store.add_result` accept the `to` product only for attempts after a valid switch and the
  recorded superseded attempt, earlier attempts still equal to `native_allocation`.
  `assert_params_preserved` covers the new key. Repin the portable checker.
- `native_startup`: switch flags/stages as negatives; generation includes the switch record.
- `remote_jobs`: per-attempt run names; refuse superseded IDs.
- `native_batch`/`ucloud_native`: offer computation, consent executor and Activity projection
  (`status_fields` gains a bounded offer object, never raw product or wallet bodies).
- `release_profile`, `app.js` Activity panel. On acceptance update V76, ADR 0124's status
  and CONTEXT.

## Synthetic tests before activation

Mocked SDK/HTTP clients and temporary libraries only; no live calls.

- Detection: the 10-minute boundary; durable first-seen across restart with a fresh read;
  RUNNING in history; FULL then BUSY flapping; a single FULL; reads under 60 s apart;
  MIG/full UNKNOWN, missing, duplicate, malformed or foreign-provider; identity mismatches
  (category, gpu, gpuModel, fraction, backend support); unfunded, failed or unknown wallet;
  price mismatch; each owner-state exclusion; legacy, template, full-GPU and Runtime owners
  never offered; read failures, timeouts and oversized responses withdraw the offer.
- Consent: stale/missing `offer_id` 409; 403 in MVP; same-origin; atomic owner+member write;
  idempotent double click; concurrent Stop or Delete wins; Retry/reconnect/settings never
  create consent; byte-identical params for every non-consented job.
- Executor: re-check lapses (MIG started, full GPU now FULL, funding unknown) without remote
  writes; spy ordering journal→terminate→observe→supersede→descriptor→`submission_attempted`→
  POST; terminate error/timeout, CANCELING and silence never POST; RUNNING update,
  `startedAt`, log or output present, input missing/resized → no POST; exactly one POST across
  every crash point; lost POST reply → uncertain and new-name-only reconciliation; superseded
  ID refused.
- Crash/restart at each stage boundary with real queue reopen; ADR 0174 refuses steps 2–5;
  Continue switch from IN_QUEUE/RUNNING/terminal; Retry before and after supersede.
- Stop at every stage; Delete of one member and of the project; cleanup only after the final
  success; restored owners inert.
- Evidence: multi-attempt capture validates; `add_result` rejects the full product without a
  valid switch; provenance shows both; repinned portable checker; existing frozen fixtures
  unchanged.
- Activity: exact wording with counts and minutes; focus on Keep waiting; collapse after
  decline; 202 pending; late-response fences; neutral queue text in MVP and full.
- Concurrency: the allocation count is unchanged by a switch; two batches each need their own
  click, and the second lapses if the full GPU is no longer free.

## Live evidence before activation (read-only, with the user's permission)

Use the user's project, the app's own credential scope, no new allocations, bounded reads;
record bounded scalars, never tokens or transcript text.

- **L1 Products**: `GET /api/jobs/retrieveProducts` several times, including while a batch is
  queued. For both exact products record `support.queueStatus`, product/category identity,
  `productType`, `gpu`, `gpuModel`, `fraction`, `price`, the category's accounting unit and
  frequency, `freeToUse`, backend/Docker support and maintenance fields.
- **L2 Wallets**: `GET /api/accounting/v2/browseWallets` (complete pagination,
  `includeChildren=false`): confirm both products bill one `gpu-nvidia-b200` wallet and its units.
- **L3 History**: `GET /api/jobs/browse` and `GET /api/jobs/retrieve?includeUpdates=true` for
  the user's past native batches: the `updates[]` shape (state, timestamp, status),
  `startedAt`/`createdAt` presence, IN_QUEUE→RUNNING durations when a MIG was free (to
  calibrate 10 minutes), and the terminal state and updates of any job stopped while queued.
- **L4 Natural queue**: while one of the user's own batches is queued anyway, read the job and
  products once a minute: does FULL accompany queueing, is the full GPU AVAILABLE/BUSY at the
  same time, does the status count already queued jobs, and is `out/` empty (listing only)?
- **L5 Application**: Transcriber Batch 1.12 metadata (`/api/hpc/apps/byNameAndVersion`):
  backend support for the full product and any product-dependent parameters.

Not read-only and **not authorised here**; each needs a separate explicit decision: terminating
a deliberately queued MIG job to observe its terminal state and wallet usage (if L3 has no
example), and one public-clip full-GPU batch for output comparability and actual credit use.

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

## Open questions for the user

1. Is 10 minutes of queueing plus two FULL reports a minute apart the right trigger?
2. Should a single-recording native request get the same ask (proposed: yes)?
3. If live metadata does not confirm the 7× relation, should the ask be withheld (proposed)
   or shown with the documented "about 7×"?
4. Hide in MVP (proposed) or include in the next preview?
5. After a switch, should Retry of that batch stay on the full GPU (proposed), or require a
   new click? Should the ask also show the worst case from the time limit (24 h = 24
   GPU-hours instead of about 3.4) once per-runtime charging is confirmed?
6. Should "Keep waiting" stay collapsed for the rest of that wait (proposed), or ask again
   after another interval?
7. For unattended overnight batches, is an up-front per-batch choice at Transcribe time wanted
   later? Not proposed now; it would be a separate consent record.

## Not covered

No implementation, review, live read, allocation, cleanup or price measurement. Capacity
signals are not reservations; whether a full GPU is faster or produces identical text is
unmeasured. CPU fallback, parallel MIGs and CBS Runtime are separate decisions.
