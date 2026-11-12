# MVP v2 reliability — 12 November 2026

The user has authorized two further implementation tracks: automatic reconnection to
already-submitted native UCloud jobs on app reopen, and CBS Runtime stability. Finish and
verify the separate setup-dialog update first. Work stays on local branches descending from
the tested preview; public main, published 0.1.0 and Desktop source remain unchanged.

| Work | Status | Next gate |
| --- | --- | --- |
| Separate setup dialog and missing-key colour | Implementation frozen; independent browser gate running | Record exact source/test results, integrate reviewed bytes, preserve user drafts and real library. |
| Native automatic startup reconnection (T141) | Implemented; final 76 focused cases pass with independent Go, broader 444/one-skip receipt source-qualified | Compose and verify before preserved reload; actual automatic provider reopen acceptance remains. |
| CBS Runtime stability | Authorized; read-only diagnosis in progress | Identify the actual failing stage, reproduce actionable defects synthetically, review fixes before activation. |
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
