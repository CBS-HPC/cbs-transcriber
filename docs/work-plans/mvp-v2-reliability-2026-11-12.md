# MVP v2 reliability — 12 November 2026

The user has authorized two further implementation tracks: automatic reconnection to
already-submitted native UCloud jobs on app reopen, and CBS Runtime stability. Verify the
separate setup-dialog update before serving it; isolated backend branches can advance from
the tested `5cab832` preview in parallel. Compose only reviewed/tested changes for a later
preview. Public main, published 0.1.0 and Desktop source remain unchanged.

| Work | Status | Next gate |
| --- | --- | --- |
| Separate setup dialog and missing-key colour | Served static UI at `dbb3581`; independent browser Go, 74 MVP/10 full-profile qualified observations | Save drafts before refresh; no backend restart was needed. |
| Native automatic startup reconnection (T141) | Local `653b0e9`, final 76 focused passes and independent Go; broader 444/one-skip evidence source-qualified; combined 193-case Go | Preserved labelled reload and real automatic provider reopen acceptance remain. |
| CBS Runtime observation stability (T143) | Local `857a727`, 100 focused and 17 independent cases pass, independent Go; combined 193-case Go | Preserved labelled reload; historical lock unchanged. |
| Isolated Runtime generation recovery (T144) | Disabled Stage 1 verified separately at `401769e`, 46 distinct synthetic passes and independent Go | No integration/promotion; actual-owner, admission/HF/compute/capture/restore proofs remain. |
| Funding-based CPU fallback | Rule agreed for native Transcriber and CBS Runtime; funding/units/affordability design in progress | MIG even when queued if GPU funding is sufficient; exact 8-vCPU fallback only for known absent/insufficient GPU funding, with CPU eligibility/model proof. Not implemented. |
| Spelling interface | Proposed only | Replace the separate editing panel with local dictionary underlines and on-demand suggestions; right-click/keyboard is recommended, trigger remains undecided. No integration yet. |

## Native reconnect boundary

Use an exact saved native allocation identity and the existing output validation/import path.
On reopen, reconnect/poll already-submitted work and retrieve available results. Preserve
saved siblings, edits, per-file state, lineage, cleanup obligations and library ownership.
Retain bounded retry/backoff and responsive Stop/shutdown controls. Avoid duplicate recovery
owners and repeated imports across two app lifecycles or a lost local reply.

Stopped/cancelled work, uncertain submissions and missing or ambiguous allocation evidence
stay explicit. Startup must not create a new allocation, repeat ASR, grant preparation consent,
change frozen credentials/settings or infer permission to discard outputs. Native Transcriber
and CBS Runtime have separate owners; this track does not silently recover every job type.
Account for startup marking formerly running rows Interrupted: new recovery must distinguish
those rows from explicit user Stop/Cancel and preserve frozen historical behavior.

## CBS Runtime stability boundary

Trace readiness, preparation, Hugging Face Activity ownership, locks, model reuse and recovery.
Use retained diagnostics and synthetic fixtures first. Separate a proven app defect from a
remote-state or provider acceptance gap. A terminal owner, elapsed time or absent current
process is not sufficient authority to steal a preparation lock. Shared runtime/model folders
are not batch cleanup targets; the disabled isolated-generation prototype remains disabled
until its activation design and compatibility boundaries have passed review.

No real preparation, allocation, model download, private upload or remote cleanup is performed
as part of this offline implementation. Real pinned-model preparation/reuse, CUDA/MIG memory,
batch, Stop/recovery and cleanup acceptance must be explicitly scoped after the candidate
passes. Runtime stability precedes PII-Tracer integration; anonymisation output/review policy
remains undecided, and no anonymisation or spelling frontend redesign is included here.

## Verification and preservation

The native and Runtime observation fixes are now composed on local
`codex/mvp-v2-reliability-candidate`: **193/193 focused tests pass**, all 566 captured inputs
unchanged, independent Go. [Exact composition and limits](../verification-2026-11-12-reliability-composition.md).
This candidate is not loaded; the current preview keeps its earlier Python backend.

- Give implementation owners separate files/worktrees and obtain independent design/code review.
- Keep production and test bytes immutable within each gate; retain original red diagnostics.
- Exercise real startup/shutdown seams with disposable libraries and mocked provider operations,
  including Stop/Cancel, unknown references, lost replies, existing saved results and cleanup.
- Preserve the currently running preview, real library/originals and overnight job evidence.
  Do not synchronize its environment or refresh a tab holding a draft. Loading a later tested
  candidate needs idle/preservation checks and verified served label/assets.
- Update SPEC/TODO/ADRs and distinguish implemented/tested code from the build actually loaded.
  Record usage at the milestone without moving the fixed MVP baseline or summing cumulative
  checkpoints; unmeasured later usage remains pending, with no invented billing estimate.

## Combined compute scheduling, planned after reliability

The user explicitly wants parallel MIG allocation, potential CPU fallback and multi-machine
availability considered together. Design one policy using model memory/quality compatibility,
batch audio duration, expected CPU/GPU completion time, cost/queue limits and per-allocation
recovery/cleanup. Keep the current compute behavior until that policy is reviewed and tested.

The checked availability API exposes per-product queue status, not an exact free-instance
count. Several available product sizes may share the same pool; do not count them as separate
free machines or infer a reservation. Investigate stronger provider capacity evidence where
available; otherwise retain Unknown/queued outcomes and bounded concurrency. CPU fallback
must be benchmarked per supported model and must not silently change approved execution.

The user's later decision fixes the fallback trigger: project GPU funding, not machine
congestion. Apply it to both engines. A successful complete project-wallet observation can
prove absent/exhausted funding; `maxUsable > 0` establishes current eligibility but does not
reserve a whole future run. Full-run insufficiency needs a defined/versioned estimate, not
an invented provider requirement or automatic use of the configured maximum walltime.
Unknown/malformed/unavailable funding never becomes zero. CPU needs its own usable allocation
and exact `cpu-amd-zen5-8-vcpu` compatibility. Preserve all already-frozen compute choices.
See [funding policy (ADR 0177)](../decisions/0177-funding-based-compute-fallback.md) and
[AU and other published measurements](../evaluations/whisperx-cpu-gpu-speed-2026-11-12.md).
