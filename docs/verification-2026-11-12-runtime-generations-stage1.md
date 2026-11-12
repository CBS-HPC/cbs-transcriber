# Runtime generations: disabled Stage 1 — 12 November 2026

Local `codex/mvp-v2-runtime-generations`, based on reviewed observation checkpoint `857a727`.
Independent source review: **Go within this disabled scope only**. No integration, preparation,
model download, live service, lock changes, main update or app reload was performed.

## Implemented boundary

The new, unintegrated `runtime_generations.py` describes bounded registry slot 11,
CPU/CUDA-separated base targets, exact operation compute descriptors, frozen-selection data,
pure canonical paths, read-only snapshots and candidate compare-and-set bookkeeping. Slot
collisions, duplicate/unsupported/null metadata and oversized input refuse. Slots 9/10 remain
unchanged. An absent policy retains the exact legacy path; removing required fields from a
present policy cannot downgrade it to legacy.

Candidate reservation defaults off. No promotion, active-selection capture, release,
preparation or execution function exists. A validated selection/product or derived path is
data, not proof of funding, eligibility, created-folder ownership or readiness. Explicit
CPU/no-active data does not authorize using a legacy CPU environment. Stage 1 refuses a
nonnull active registry pointer until actual durable-owner promotion is implemented.

The caller owns the writer transaction. `in_transaction` alone cannot prove BEGIN IMMEDIATE;
this precondition remains explicit. Actual SQLite contention, idempotent retries, competing
writers, stale WAL snapshots and rollback were tested for non-overwrite and atomicity.

## Evidence

The builder retained an initial API-absence red gate. Its frozen focused gate then passed
**29/29**, with all 141 captured product/test/configuration inputs unchanged and a complete
collection/result bijection. The independent adversarial gate passed **17/17**, with all 133
captured inputs unchanged. These are **46 distinct cases across two separate gates on the
same module hash**, with zero failures/skips. Ruff, format and CRLF-aware whitespace pass.

| Artifact | SHA-256 |
| --- | --- |
| Module | `c2593eadafd929dad9c20db73e10d76591a0f99df69c36aa16ea3019963d2598` |
| Builder XML | `60480766d5903e7f8e9663fc4da42ecab7063e3169a112d362f10919b1cea7fc` |
| Independent XML | `894251b9e8008a38f3401a6e2ae0e7ab0fc17afa1c0df06043f293bf635eb999` |

Only the new module and two test files change product/test source. Existing application,
capture pins, schemas, worker recipes and environments are unchanged. Private hash maps,
collection diagnostics and original red evidence are retained outside Git.

## Remaining work

This is not a resolution of the historical retained preparation lock. Later stages in
[ADR 0176](decisions/0176-isolated-runtime-generation-recovery.md) must bind the actual durable
preparation owner, exclusive folder creation/lost replies, verified atomic promotion, fresh
admission and all delayed/followup/repair/retry carry, HF/model plans, CPU recipes, provenance
and schema-19 restore. Promoted environments must remain immutable. Complete offline gates
and separately scoped live acceptance are required before enabling any generation path.
