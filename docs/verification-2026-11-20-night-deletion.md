# Project deletion in Activity: night hardening, 20 November 2026

Scope: local post-v2 development, independently reviewed from Claude's `9b8ca96` candidate.
No main merge, push, publication, installed-environment change, preview reload or live UCloud
operation. The running v2 preview is separate from these source checkpoints.

## Changes

- B513 / V254: complete-backup capture and project-deletion admission now reserve one another
  before any Stop or copy, including a task still draining before a file journal exists.
  The reservation releases on exceptions and does not block Keep or a task's own staging.
- B514 / V255: the task decision guard spans the actual Store connection/COMMIT outcome.
  Keep and shutdown wait outside the manager lock. A lost COMMIT reply is classified from
  fresh database facts; unknown facts refuse Keep without claiming the project was removed.
- B515 / V256: Keep is rechecked when the queue is claimed and before special/native Stop
  dispatches. Controls accepted earlier stay stopped; a Keep that wins first leaves the
  frozen input/inspection controls unchanged. Legacy no-predicate cancellation is unchanged.
- Independent R5 cases cover native and Runtime lost Trash replies, the retained exact release
  decision, same-owner Retry and parent-listing absence confirmation, without touching siblings.

No schema change or new restore authority. Existing per-deletion receipt files remain
observational and are not executable after restart. Recording deletion retains its legacy
flow. ADR 0190 and AGENTS describe these boundaries.

## Frozen verification

The initial unchanged Claude candidate passed 366 backend cases with two platform skips and
63 relevant broad browser cases. Those results describe `9b8ca96`, not this hardened source.

The first focused fix gate passed 119 cases with one platform skip before B515 was added.
Final expanded hardening gate: 366 passed, two skipped and ten failed. All failures were in
portable-route capture scenarios whose deep generated Windows fixture paths reached the
existing path-budget refusal (one observed its secondary early-thread-return symptom).

The complete 30-case `test_portable_routes.py` module was rerun with a fresh shorter synthetic
base, retaining production guards and every assertion: 30 passed. The before/after maps of
610 source/test files match across the expanded and corrected runs. Exact XML node identities
reconcile to **378 distinct cases: 376 passed, two skipped, no unresolved failures**. This is
a staged correction, not a claim that two overlapping suites are additive (B516).

Independent diagnostic reruns of the ten new adversarial cases and 38 task cases also passed;
they overlap that inventory and are not added to it. Independent source/backend review: Go.
The private handoff retains source hashes, red/green XML and the independent review receipt.

Final hardened-source relevant browser gate passed **22 cases** across project Activity,
project Delete, Stop/cancellation, local cleanup, remote release and library deletion, with
identical before/after source/test maps. Scoped Ruff and format checks pass for all 13 changed
Python files; whitespace checks pass. Independent source/backend review is Go. The full
browser inventory has 729 cases and is not claimed passed. Real Windows/macOS, Cloud Files/held-file/crash behavior,
real UCloud owners and human H24 acceptance remain open. All exercised libraries/providers
were synthetic/mocked; no download, preparation, paid call or remote cleanup occurred.
