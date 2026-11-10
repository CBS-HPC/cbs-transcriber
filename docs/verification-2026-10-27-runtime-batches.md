# CBS Runtime batching — implementation verification

27 October 2026; branch `feature/runtime-batches-and-review-layout`.

**Core allocation stage verified offline; T85 remains open.** This record supplements the earlier
compact-review/native-batch verification. It does not claim readiness for real-library activation.
The running backend and real library have not been restarted/migrated. No live allocations,
private transfers, model-weight downloads or paid inference were used.

## Implemented seams under verification

- ADR 0065 and SPEC V113–V116: schema-12 owners, members and attempts; compatible admission,
  exact source lineage, fenced recovery, atomic result receipts and shared capacity.
- One remote controller per allocation, immutable acceptance/outcome receipts, bounded tasks,
  exact membership sealing, heartbeat/orphan/deadline handling and one GPU pipeline at a time.
- Coordinator integration with existing enhancement finalizers, independent local preparation
  limits, per-file results, allocation-wide Stop/reconnect/cleanup and canonical Activity.
- Atomic mixed-provider enqueue, explicit enhancement-source freeze, storage/deletion guards,
  Advanced allocation limits and a single expandable Activity card.
- Shared runtime preparation locks and legacy-singleton capacity recovery. Old API/SSH/native
  execution identities are preserved.

## Evidence recorded so far

| Scope | Result | Limits |
|---|---|---|
| `test_runtime_store.py` | 38 passed | synthetic migration/library, cached save, scoped Stop/attach races |
| Store + research-history affected run | 52 passed | overlaps the evolving focused storage suite |
| `test_runtime_batch_remote.py` | 49 passed, 1 skipped | includes verified partial-stage carry; Windows symlink fixture skipped |
| Preparation locking | 10 passed, 1 skipped | Git Bash control flow uses mocked flock; real Linux flock skipped |
| `test_runtime_capacity.py` | 19 passed | mocked exact owned allocation status only |
| New runtime API cases | 21 passed | includes real owner grouping/cancellation helpers, no remote calls |
| Affected baseline API/lane cases | 13 passed | includes one strengthened obligation guard |
| Runtime coordinator mailbox suite | 23 passed | one POST, late join, recovery, Stop, immutable publication, stage reuse |
| Queue seam checks | 6 passed | dispatch, cached receipt, lane survival and cancellable preparation capacity |
| Full unit/API run | 1,010 passed, 3 failed, 2 skipped | two stale schema assertions and a real old-future/new-task retry race; all fixed below |
| Final affected unit/API group | 208 passed, 2 skipped | runtime seams plus legacy API/follow-ups/receipts/backup/collections |
| Guarded native cancellation/receipts | 24 passed | actual API continuation notice; containment and late foreign attachment |
| Full Chromium run | 112 passed, 3 failed | old singleton mocks lacked the new read-only Terminal preflight seam |
| Three browser fixture corrections | 3 passed | preserve explicit legacy protocol; production preflight unchanged |
| Additional native Stop browser case | 1 passed | shared Runtime continues, notice shown, zero indirect Runtime Stop calls |

Do not sum these rows: runs overlap. All 116 current Chromium cases are verified across the
broad run and focused reruns. The corrected backup/collection cases also pass their seven-case
focused run. Ignored raw evidence is in `test-results/runtime-*.xml`/`.txt`; subagents returned
the complete 23-case coordinator, 38-case persistence and 24-case cancellation command results.
An unrelated optional NLTK import dominated one legacy run (1,100 seconds). The pinned-snapshot
unit case now isolates tokenizer lookup; production inference code is unchanged.

Final checks: repository-wide Ruff lint/format, Prettier 3.6.2, JavaScript syntax and Git whitespace.

## Review and remaining work

Independent review's Stop harvest, remote admission, cached-only recovery, safe public-folder
cleanup, explicit retry, seal CAS and long-transfer heartbeat findings have regression coverage.
The broad run also reproduced an old future failing its replacement task; futures now retain
their original task IDs. Accepted or readied inputs are never rewritten after a lost local
publication receipt. Unprovable partial publication pauses for explicit recovery/Stop/new run.

A final ownership review reproduced native parent cancellation affecting another parent's
pooled Runtime work. Containment is now checked in the same SQLite transaction as Stop closes
admission, with a continuation response, durable Activity notice and browser coverage (V117).

Failed/partial private-run discard controls remain pending. Such files stay retained and can
block library switching/deletion; ordinary cleanup guards have not been weakened. The safe
script-only exception checks every attempt and requires absence or fresh terminal proof.
Resident model caching is also pending; the first controller uses sequential full pipelines.

Real schema-12 activation, Linux/container locks, actual MIG performance, private-file cleanup
and production UCloud acceptance remain separate from synthetic/mock verification. Remote
media preparation is not delivered by this first shared controller.

## Unattended continuation

The existing CBS Transcriber chat follow-up was updated and activated every 30 minutes at the
user's request, with current workspace/branch, review gates and stop conditions. It must not
merge, push, restart the running app, migrate the real library or start live jobs. Inspect saved
evidence before continuing so tests and completed stages are not repeated unnecessarily.

An attempted read-only Claude background review was refused by its launcher: workspace trust
has not been accepted. No trust settings were changed. The launcher also listed an existing
interactive Claude session; do not interrupt it or duplicate any editing workload it owns.
