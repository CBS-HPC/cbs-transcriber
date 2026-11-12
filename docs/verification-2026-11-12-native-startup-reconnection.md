# Native startup reconnection — 12 November 2026

Implemented on local `codex/mvp-v2-native-reconnect` from `5cab832`. Independent review: Go.
This code is not loaded in the user's preview or published MVP. Tests use disposable libraries
and mocked providers; no real allocation, upload, preparation or cleanup was performed.

## Verified behavior

At queue startup, bounded metadata admission reconnects only exact known submitted native
singleton/batch allocations. Its dedicated atomic transition preserves frozen requests,
saved/Trash results, review edits and Working versions. One attempt belongs to one queue
lifecycle; worker authority remains outside mutable job parameters and guards every
creation/upload/submission fallback as well as retrieval and finalization.

Stop is durable before worker acknowledgment. Legacy pending-Cancelling evidence is excluded
before generic recovery changes messages. Uncertain/no-ID, restored, archived, disposed,
cleaned, malformed, replacement-consent and inference-followup owners remain explicit.
Typed current transient-failure receipts may recover; old generic failed rows remain manual.
Reconciliation timestamps and completed-sibling producer/recording/remote-ID links are checked
both at admission and after later changes, including retained Trash results.

Recovery does not repeat original inspection, media preparation or inference. Normal frozen
source/output validation, producer idempotency and terminal/durable-save cleanup fences remain.
Explicit Retry retains its separate checks and atomically clears the new negative marker only
when that explicit action is accepted. Other job families keep their existing behavior.

## Evidence and review corrections

Five actual old-source seam failures establish the missing singleton/batch startup recovery,
saved-sibling finalization, durable Stop and legacy Cancelling protection. The original red
diagnostics are retained privately. The independent adversarial suite covers cancellation
flags, crash-before-ack, repeated startup, mutated/no-ID journals, current failure receipts,
saved/Trash siblings and no-submit spies. Its one setup correction explicitly reads a Trash
result with `include_trashed=True`, preserving unchanged payload/version assertions.

The broad frozen checkpoint ran 445 cases: **444 passed and one Windows symlink-privilege
case skipped**. Independent source review then found two metadata consistency gaps: a
nonempty but invalid reconciliation timestamp, and unverified completed-sibling producer
ownership. Both were fixed with eight positive, negative and late-change regressions.

The final focused gate ran **76 distinct cases: 76 passed, no failures/errors/skips**. All
561 captured product, test and configuration inputs remained unchanged. It includes all
21 basic startup and 24 adversarial cases plus 31 directly coupled legacy/native lifecycle,
capture, cancellation, restore and incremental-result cases. Ruff, format and CRLF-aware
whitespace checks pass.

| Retained receipt | SHA-256 |
| --- | --- |
| Final focused XML | `611d6223cf9cce54e729a4bc0cb3f0fee5ce68898ce7561e4a0f6063921eb655` |
| Prior broad XML | `2dda51e9180f8473b9e9baff2dffdb0fd4984941710f22a4d1a83e2281933f05` |
| Final `native_startup.py` | `bf103acc3f0ce4466cfb89e8374201c5d7522e50a9d77d1788701bd6b4f33831` |

Only `native_startup.py` changed between these two product checkpoints; the basic startup
test file also gained the new cases. The earlier 444/one-skip result is qualified prior-source
evidence, not a new complete final-source run. The two receipts span 453 distinct identities;
do not add their repeated tests or call that a fresh 453-case gate.

## Limits and activation

Fresh legacy singleton `cloud_call` still curates some connection/502 failures into generic
errors. Those failed rows stay manual; typed receipts cover batches and reconnect-mode
singletons. Interrupted singleton reopen is covered. This is not automatic recovery of every
historical Retry card, every job family, or cleanup-only obligations.

Real provider shutdown/relaunch and output/cleanup acceptance remain separate. The user's
successful overnight explicit Retry on allocation 12456177 is useful acceptance of the
existing recovery path, not evidence that this new automatic mode has run live. Composition
with the UI/Runtime branches needs its own focused gate before a preserved, idle reload.
