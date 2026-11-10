# T87b1 — Durable original inspection

28 October 2026. ADR 0082 / V138. Verified development-branch prerequisite.
The separate running app remains on `c3c94e6`, schema 12. This work uses temporary synthetic
libraries only and has not migrated, restarted or changed that app or its jobs.

## Scope and limits

Schema 13 adds a nullable original-inspection cache. Existing observations remain NULL until
an explicit internal request. The internal inspector reserves its own job kind and atomically
maintains cache/job ownership through generation, job ID and epoch. No import, ordinary worker,
public request or startup automatically schedules inspection in this slice. It submits no
provider request and creates no native or Runtime allocation.

Manual inspection requires the caller's lease on the same Store until I/O drains. The worker
does not re-enter the lifespan thread's RLock. Actual hashing/decoding runs outside queue,
snapshot and database locks. A completed original-byte observation is retained before decoding,
including when cancellation arrives at that boundary. Failed/cancelled retries preserve that
identity and reject a replacement original. An incomplete first hash remains unobserved.

Cancellation of a running inspector retains busy ownership until its I/O unwinds. Explicit
recovery fences the earlier epoch. Full decoded source receipts are required; scoped container
observations cannot become whole-original readiness. Ready reads use saved metadata and do not
claim current media availability or decoder compatibility. Raw cache data is excluded from
recording responses and project snapshots; reports/exports keep their existing allowlists.

The HTTP source-inspection bottleneck is **not fixed by this prerequisite**. T87b still needs
durable producer acceptance, background preflight, same-ID materialization and one native
selection barrier, then playback-readiness separation. Bounded repair and remote preparation
remain separate pending work. The activation hold in ADR 0075 remains.

## Verification record

- Independent design review approved the inert contract and identified generic lifecycle,
  projection and lease boundaries before implementation.
- Thirteen new API/queue cases first failed: generic Retry/Cancel/direct processing could
  bypass the proposed inspector boundary. After guards, those cases and the existing queued
  media cancellation check passed: **14 passed**.
- A strengthened early-batch-dispatch canary and the affected store/backup/migration cases
  passed together: **21 passed in 38.12 seconds**. Evidence:
  `test-results/inspection-integration-guards.xml` (ignored).
- The old schema migration fixture's fixed current-version assertion failed at 13 versus 12.
  It now checks `SCHEMA_VERSION`, retaining exact schema-11 backup and row-preservation checks.
- Owner and independent probes reproduced excessive nesting reaching JSON parsing, epoch
  exhaustion persisting an invalid cache, and completed-hash cancellation losing its anchor.
  These are recorded as B202–B204. Further red probes reproduced decoder guard failures
  leaving a live process, NULL-cache resets discarding an existing inspector, and bool/float
  aliases accepted as exact parameters (B205–B207). The final corrections are green.

Final affected checks:

| Scope | Result |
| --- | --- |
| Inspection, existing physical audio inputs and completed-source recovery | 88 passed, one existing Windows symlink-permission skip; 158.24 s |
| Two added transaction-abort cases | 2 passed; 7.98 s |
| Existing original-input API/integration cases | 10 passed; 84.71 s |
| Independent ownership/shape/anchor/drain/migration/privacy probes | 11 passed; 30.79 s |

All **43 new permanent inspection cases** were verified (41 in the combined run, two added
transaction cases separately). They cover concurrent ensure/claim uniqueness, stable anchors,
incomplete hashing, changed originals, cancellation and stale recovery, full versus scoped
receipts, malformed/deep/oversized cache/job metadata, exact numeric types, source growth/read
bounds, worker-thread lease handling, available DB/snapshot locks during decode, migration
backup/lease/rollback, atomic ensure/claim failure and projection privacy. The root's 13 new
API/queue guards are separate; its 21-case integration run above also includes existing cases.

```text
uv run --no-sync pytest tests/test_original_inspection.py tests/test_audio_inputs.py tests/test_original_input_recovery.py -q -o addopts=''
uv run --no-sync pytest tests/test_original_input_integration.py -q -o addopts=''
uv run --no-sync pytest tests/test_inspection_queue_guards.py tests/test_runtime_store.py::test_schema11_migration_preserves_rows_backup_and_reopen_once tests/test_backup.py tests/test_store.py tests/test_jobs.py::test_cancel_queued_job_is_durable -q -o addopts=''
```

The independent final review is **GO for this inert slice**, with ignored JUnit evidence in
`test-results/2026-10-28-inspection-independent-review.xml`. Ruff and format checks passed on
the affected modules/tests. No browser layout changed, and no new browser-acceptance claim
is made. The reviewed source and affected tests are ready for a local feature-branch checkpoint;
the rest of T87b remains the activation gate.

These focused totals overlap; they are not a new whole-project test total. No paid/provider
call, model download, private recording, live cleanup, dependency synchronization, main merge
or push is part of this stage.
