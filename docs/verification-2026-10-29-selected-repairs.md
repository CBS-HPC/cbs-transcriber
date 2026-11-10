# Selected UCloud passage repairs — 29 October 2026

The development branch now connects selected passage-timing requests to the bounded
API-only UCloud repair protocol. This is mocked integration evidence; no live GPU job,
private upload, model download, backend restart or real-library migration was performed.

## Behavior

- A single/small repair keeps local processing. Large multi-unit requests use the union
  of selected windows and saved processing rules. Cloud eligibility requires an approved,
  prepared connection, frozen MIG compute and an already-ready original-v1 receipt.
  Legacy/uninspected recordings follow the explicit local/Stop fallback. Repair does not
  start a full-file inspection. There is no silent truncation beyond the 200-unit bound.
- Source/revision/selection checks are repeated after routing, outside the long preflight
  lock. Selected enqueue narrows only an explicitly marked timing/realign singleton while
  retaining exact full history-version evidence. ASR, diarization and shared Runtime plans
  cannot enter this selected-only path.
- The worker prepares only bounded selected audio, records preparation receipts and commits
  the bundle before remote dispatch. Each unit retains acceptance/start/output evidence.
  Recovery uses completed clips and the same recorded allocation; uncertain or terminal
  inference is not silently repeated.
- Partial failed/cancelled outcomes remain honest. Valid completed timing can be applied
  through one transaction that appends the timing version and its exact job receipt.
  Lost-response retries are idempotent. Result, revision, text, bounds and current word-map
  checks protect newer edits/timing while valid sibling targets can still apply. Every
  unselected passage is preserved.
- Stop, Retry, reconciliation and cleanup use fenced repair-specific writes. They preserve
  concurrent publication, capacity and Apply receipts. Unknown accepted outputs retain their
  remote obligations. Cleanup moves only the owned folder to Trash after terminal validation.
- The browser supplies the result ID and keeps cancelled/failed partial-ready requests pending
  until application. Active repair tags take priority over cleanup-updated historical jobs.

## Verification

The following sets overlap; they are not a summed whole-project total:

- **196 focused tests passed** across source/bundle/cloud/Apply/API/routing/admission and
  legacy passage/realign behavior.
- Final public API set: **11 passed**. A further actual HTTP Retry→worker regression passed,
  proving the same remote ID, one original allocation and no additional private transfer or
  inference after output completion.
- Browser historical-cleanup ordering case: **1 passed**.
- Independent final API/Apply/routing review: **22 passed**, plus the actual Stop/Apply
  interleaving probe. The earlier closed lifecycle review passed its scoped checks.
- Owned compilation, Ruff checks and formatting passed. Broader integration and final shared
  source formatting are coordinated separately in the main chat.

Verified defects fixed during implementation include lost journal updates, partial jobs
misreported complete, older timing overwriting a newer map, Stop erasing a concurrent Apply
receipt, and Retry entering the local-only checker. See SPEC B215/B216/B222–B224 and
[ADR 0086](decisions/0086-ucloud-selected-repair-execution.md).

## Remaining acceptance

Real Linux/UCloud execution, resource use and timing quality need a separately authorized
public-fixture run. Older/uninspected recordings need a future bounded source-proof route
before they can use this cloud path without a prior original inspection. Frozen legacy jobs
keep their existing behavior. Automatic second-pass alignment remains explicitly deferred.
