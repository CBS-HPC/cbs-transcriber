# 0175 — Bounded observation recovery for Runtime preparation

Accepted 12 November 2026 after independent design review; user authorized Runtime stability work. This slice strengthens the
existing durable preparation/Activity owner. It does not recover the historical retained lock
or activate the disabled isolated-generation prototype. Independent design review and
synthetic red/green reproductions precede production changes.

## Observed gaps

The standalone preparation `Run.poll()` immediately fails its owner when a transient remote
status/download observation raises, despite retaining the same remote job ID. Other Runtime
coordinators already have bounded transient handling. After UCloud SUCCESS, absent or partial
supervisor JSON and temporarily unavailable verification files also have no settling interval.
These code-trace findings are not proof of the historical preparation-lock failure's cause.

## Decision

Retry only read-only observations of the exact frozen remote allocation and its exact owned
artifacts. Bound attempts, backoff and elapsed terminal-settling time; retain one owner/epoch,
submission journal and Activity identity. Reset a consecutive transient counter only after a
successful valid observation. Exhaustion remains a truthful visible recoverable failure, with
no new preparation POST, folder creation, installation, cache rewrite or lock mutation.

Distinguish known transient connection/429/5xx observations from authentication, absence of
the exact allocation, wrong identity/generation/mounts, malformed authoritative evidence,
wrong requirements/pins, explicit failure and a verified negative doctor/CUDA/cache result.
Only definite missing/not-yet-published observations within a bounded SUCCESS settling window
may wait. Never turn invalid proof into readiness, or use a base stamp to claim all models.

Use a preparation-scoped typed observation/validation seam while preserving legacy read APIs.
The current generic adapters conflate 401/403 with retryable errors, missing/partial/nonobject
JSON with None, and absent stamps with wrong-kind/size/digest stamps; catching Transient alone
cannot enforce this decision. Classify transport status, exact identity, complete-invalid proof
and bounded incomplete/not-yet-visible evidence before choosing a retry. Keep refusal types
(including preparation preflight refusals) distinct from recoverable reads; never parse human
exception messages to recover authority. Reuse existing bounded transport primitives where
their contract is adequate, without silently changing unrelated legacy callers.

Keep Stop, shutdown and epoch fencing live during both backoff and terminal settling. Stop
must return through the existing `_observe_stop` remote-control/observation path; merely
calling a generic guard that raises Interrupted can skip actual Stop handling. Observed
terminal state may release capacity independently, but does not prove preparation success or
grant cleanup permission. Late replies cannot overwrite a newer owner/Stop or publish Ready.
Positive completion must re-read the current owner, epoch and durable Stop intent inside its
writer transaction, including after a writer-lock wait. An observation obtained before Stop
cannot commit Ready afterward; return through owned Stop observation instead. Cover both an
I/O barrier and a held-writer barrier, not only a pre-call guard.

The same durable Activity task reports checking, preparation, observation retry, verification
and terminal outcomes. No duplicate manager/queue is introduced. App reopen retains the
owner, remote ID, Stop and recovery evidence; no new automatic preparation follows merely
from launch. Broader automatic Runtime startup recovery is not implied by native ADR 0174.

## Gate and limits

Reproduce transient remote-state/read failures and SUCCESS-before-artifact visibility in
disposable fixtures, then verify bounded same-ID recovery, reset/exhaustion/deadline, actual
Stop during each wait, shutdown/epoch changes, permanent refusal and no false Ready. Test
partial JSON separately from complete wrong-generation/pin evidence. Assert zero new remote
submissions or shared-runtime mutations and preserve existing preflight/lock/HF-plan cases.

The historical lock still requires a separately reviewed immutable-generation recovery
protocol, without deleting or stealing that lock. Physical UCloud mounts/specification
normalization, Linux locks, CUDA/MIG memory and actual model preparation/reuse remain live
acceptance work, outside these mocked checks.
