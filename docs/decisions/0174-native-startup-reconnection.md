# 0174 — Reconnect known native allocations on app reopen

Accepted 12 November 2026 after independent design review; user authorized implementation.
Implemented and independently verified on the isolated native-reconnect branch; not loaded.
Applies to native Transcriber singleton and batch jobs, not CBS Runtime, repairs,
API-compatible inference or every old Retry card. See the
[verification receipt](../verification-2026-11-12-native-startup-reconnection.md).

## Decision

Opening the app may resume observation and result retrieval for an exact already-submitted
native allocation. It cannot submit another allocation, upload inputs, discover a substitute
job by name, repeat ASR, grant preparation consent or replace reviewed text.

Use dedicated startup admission and atomic compare-and-set, never manual Retry: that path
clears cancellation flags and has separate replacement consent. Keep the serving library
lease and deletion-recovery barrier; start no workers/network for `start_worker=False`.
Admission is metadata-only, bounded and runs before queue workers. Network/backoff/drain
must stay outside queue/snapshot/writer locks.

### Eligibility and negative authority

- Include both a standalone native job and a shared native batch owner with its exact members.
  Require valid frozen native Settings/allocation, a known submitted remote ID, consistent
  submission/folder ownership and live matching recording/project/member identities.
  Reconciled submissions need a bounded valid timezone-aware timestamp. A completed sibling
  needs a unique producer result linked to that recording and exact remote allocation,
  including a retained Trash result; this metadata is rechecked during execution.
- Interrupted work with that proof may resume. A failed job qualifies only with a new strict
  typed transient-monitor/download failure receipt bound to the current owner/remote ID and
  frozen generation. Historical generic failures and stale/unknown receipts stay manual;
  never classify authority from human-readable error text.
- Exclude Stop/Cancel/termination intent, uncertain submissions, missing/changed/ambiguous
  identity, restored/archived/released owners, cleaned folders, malformed params, inconsistent
  batch membership, deterministic/authentication/not-found failures and explicit new-run or
  replacement-confirmation states. Cleanup-only obligations remain independently accessible.
  Do not silently start frozen inference followups from this retrieval-only slice; such owners
  remain manual without stripping their original request fields.
- Preserve existing queued new requests. Any queued known-ID recovery must also retain its
  reconnect-only execution fence; a missing later ID must never turn it into a fresh job.

New singleton Stop acceptance needs a durable negative intent in the same writer transaction,
before the ephemeral cancel event or remote call. A crash before acknowledgment cannot erase
it. Existing legacy pending-Cancelling rows must be conservatively excluded before generic
startup recovery overwrites their message. Text is negative compatibility evidence only,
never positive permission to reconnect. Batch and singleton intent checks remain independent.

### Atomic admission and execution fence

Revalidate owner/member states, frozen-generation digest, result/job/recording/project links,
Stop/restoration/disposition and competing current work in one writer transaction. Queue only
the exact unfinished eligible rows, preserving saved siblings, versions, Trash, review and
Working authority. Result-bearing pending-finalization rows need an explicit idempotent
finalization proof, not another result import or an invented replacement choice.

Persist credential-free versioned attempt identity bound to one JobQueue lifecycle, exact
owner/remote ID, frozen settings/submission and members. Repeated start on that lifecycle
and concurrent admissions must not produce another recovery owner or a retry timer loop.

Pass pinned reconnect-only authority outside mutable job params into **both** native worker
paths. Recheck it before polling/listing/download/import/checkpoints and, especially, before
every existing no-ID creation/upload/submission branch. Missing/replaced journal fields or
changed membership after admission must refuse visibly. Never clear Stop flags or recapture
current settings/provider/model choices. Current credentials may only serve the same frozen
endpoint; missing credentials pause rather than retarget.

Use bounded cancellable status/download backoff. Stop uses the existing owned control path;
shutdown drains accepted reads while retaining the lease and remote work. Exact-ID absence,
invalid outputs and exhausted recovery stay visible with their evidence. New results use the
existing validated raw capture and producer-job idempotency; preserve current edits and named
voices. Existing success cleanup requires durable local receipts and observed terminal state,
is limited to the exact owned folder and never grants broad cleanup or output-discard consent.

## Proof before activation

Synthetic actual queue reopen tests must cover singleton and batch, graceful/crash shutdown,
pending Stop before acknowledgment, every cancellation flag, uncertain/no-ID and ID changes
after admission, all-completed and pending-finalizer gaps, saved/Trash siblings, malformed or
restored evidence, stale typed failures, auth/404 refusal, followups/replacement consent,
competing activity, duplicate lifecycle/CAS, lost replies, approval changes and cleanup fences.
Spy that create-folder, upload and job-submit operations are never called by this mode.
Retain exact legacy frozen/capture receipts and existing explicit recovery paths. Real provider
acceptance is separate; tests start no UCloud jobs or paid calls.
