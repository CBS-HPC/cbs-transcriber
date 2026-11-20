# CBS Runtime preparation: offline investigation and recovery design

10 November 2026. This work resumes investigation and synthetic verification only.
It does not start preparation, allocate compute, upload recordings, download models,
activate isolated generations or change model defaults. The historical failed owner,
its run files and cleanup obligations remain evidence.

## What currently owns each lock

`runtime_preparation.py` owns the durable library-level preparation operation and its
frozen connection/recipe, epoch, submission intent, remote ID and cleanup journal.
`runtime_batches.py` and legacy `enhancement_api.Run` retain independent allocations.
Their generated supervisors share the same versioned runtime folder.

Two distinct remote filesystem objects matter:

| Object | Current authority and purpose | Recovery consequence |
| --- | --- | --- |
| `.prepare-lock` directory, with `owner` containing the supervisor generation | Exclusive `mkdir` serializes preparation/model prefetch. A shell-local flag tracks acquisition. | A stale directory can survive a forced exit. Neither age nor the recorded allocation becoming terminal proves that the directory can be removed. |
| Persistent `.in-use` regular file | Ready supervisors and inherited children hold a shared `flock`; destructive runtime installation requires its exclusive lock. The inode is never replaced. | A preparer must preserve active readers. A terminal allocation observation cannot substitute for acquiring the correct file lock. |

Preparation rechecks the requirements stamp after claiming its directory lock. A ready
environment is reused; destructive installation needs exclusive `.in-use` ownership.
`runtime_batches.legacy_readers()` also refuses rebuilding when this library knows an
uncertain/nonterminal legacy singleton that may not participate in the reader protocol.
That local inventory is a conservative fence, not a global proof that every possible
remote reader has gone away. Ordinary prepared readers do not take `.prepare-lock`.

`Run.submit()` in standalone preparation refuses uncertain submission, retains the
recorded ID and creates its owned run folder separately from the shared runtime.
Retry reconnects an unresolved recorded allocation or creates a visible successor only
through its existing rules. Cleanup targets the owned run folder; it does not own the
runtime/model folder or `.prepare-lock`. A successor currently encounters the same retained
lock. There is no safe lock-reclaim endpoint or automatically recoverable lock-owner receipt.

The documented historical `runtime-busy` observation therefore remains unresolved.
This investigation did not query that remote owner or its files and does not diagnose
why it originally left the lock behind.

## Applied safeguard: release only verified ownership

The existing release path trusted only `HOLDING_LOCK=1` and recursively removed the
current lock path. Synthetic replacement, changed owner bytes and extra-content cases
showed that it could remove another directory or evidence after acquisition. This is
a separate discovered weakness; it is not asserted to be the cause of the historical
contention failure.

Under ADR 0168, a new generated supervisor captures device/inode identities for the
acquired directory and owner marker. Release requires those identities, regular/non-link
kinds, the exact own-generation marker bytes and no additional children, including hidden
ones. It then unlinks only the owner marker and removes the empty directory. Failure or
uncertainty retains the lock. Release never recursively removes its contents. Existing
prepared-reader locking, stamp checks and refusal to steal locks remain in force.

This is defensive checking for cooperating supervisors. The checks and pathname-based
unlink/rmdir are not one atomic filesystem transaction. An external actor can replace a
path between checking and using it; a process/storage crash can leave a partially released
directory. A late extra entry can make rmdir fail after the own marker was removed. Missing
markers and such failures stay unresolved; no age rule, repair loop or terminal allocation
observation grants reclaim authority. Stronger hostile-mutation fencing or crash recovery
requires a separately designed protocol.

New supervisor bytes change the computed recipe/code identity naturally. Already uploaded
scripts retain their original behavior; this is not a backfill or a deployed remote fix.

## Existing isolated-generation prototype

The retained `runtime-prep-contention` worktree contains a substantial earlier prototype,
including `runtime_generations.py`, generation-aware preparation/path helpers, admission
and generic-mutation guards, batch identity and frozen-source tests, and preview UI.
Its `FRESH_RUNTIME_ENABLED`, `GENERATION_WORKER_ENABLED` and
`GENERATION_ADMISSION_ENABLED` constants are false. The preparation endpoint rejects the
incomplete path while worker and admission gates add independent refusal. This branch
does not import or enable that prototype.

The prototype already sketches useful boundaries:

1. A bounded registry keys the physical target by exact server/project/storage, runtime
   recipe and requirements hash, with separate active and candidate generations and a
   revision. The token is an immutable per-generation identifier.
2. Explicit fresh-preparation preview/confirmation binds the connection and registry
   revision. Reserving one candidate is separate from selecting it as active. A settled
   candidate may be released from the local registry without deleting any remote folder.
3. An owned, generation-specific folder is created exclusively after a durable intent.
   A lost create reply or collision is not adopted, and a later directory read is not
   permission to retry creation or submit an allocation.
4. The specification's mounted folder and supervisor runtime path use the same frozen
   token. Verified successful allocation, matching supervisor/doctor, requirements and
   folder-creation receipts are required before an epoch/revision-fenced promotion.
5. New requests freeze the selected generation at acceptance. Batch compatibility includes
   that selection. Frozen jobs, retries and recovery do not consult a newer active pointer
   or fall back silently to the legacy runtime.

These are inspected design elements, not new acceptance claims. The prototype also
touches application routes, admission, generic job mutation, persistence and frontend
selection; copying preparation helpers alone would bypass its intended fences. It has
not been integrated or revalidated against the current post-MVP branch by this work.

## Recommended next design

Prefer explicit preparation of a fresh, isolated generation over automated reclaim of
the retained legacy directory. It avoids deleting/rebuilding the ambiguous target and
keeps existing readers and frozen requests pinned. It still requires paid preparation
consent, extra storage and a full ownership/readiness protocol; a fresh folder name alone
does not establish safe preparation or make the old lock disposable.

Before activation, review and verify the complete prototype against the current source:

- Keep the legacy lock and all shared runtime/model generations outside ordinary run
  cleanup. Terminal candidate release affects local selection only. Later garbage
  collection, if needed, requires a separate explicit inventory and ownership design.
- Preserve candidate, folder-create, submission and readiness uncertainty across Stop,
  restart, lost replies and failed commits. Do not replay a POST or adopt an existing
  generation folder. Retain independent old run cleanup obligations.
- Freeze generation identity at every fresh request boundary, including delayed original
  inspection, automatic follow-ups, singleton and shared-batch paths. Keep exact existing
  frozen jobs readable and recoverable without retargeting them.
- Validate mounts, `.in-use` creation and stable inode handling on the actual Linux
  project-drive filesystem. Keep known legacy-reader refusal for any legacy rebuild;
  do not infer global reader absence from one library or remote terminal state.
- Permit promotion only from fresh trusted observations of the exact recorded allocation,
  mounted generation and immutable recipe. Keep Stop/epoch/CAS fences and stale-selection
  refusal atomic locally. Do not accept public caller assertions as readiness authority.
- Keep the integration disabled until mocked lifecycle/failure tests, current-schema
  persistence tests and a separately authorized controlled remote acceptance are complete.

## Verification and remaining acceptance

Before the safeguard, the existing locking suite passed 10 tests and skipped its Linux
`flock` concurrency test on Windows. Eight new ownership-change regressions failed against
the old release behavior, while the normal release/idempotence regression passed.

After the safeguard, the focused locking suite passes 17 tests and skips three on Windows:
the actual Linux `flock` concurrency test and the two native-symlink cases. Git Bash can
emulate `ln -s` by copying, and this Windows account cannot create native symlinks; those
cases are explicitly skipped rather than reported as native-link evidence. Ruff check,
Ruff format checking and the scoped CRLF-aware whitespace check pass.

Related verification covers supervisor rendering, standalone preparation, setup retry,
capacity and the remote batch controller: 169 tests passed and one native-symlink test
skipped because this Windows account lacks permission.
The initial wider run also exposed ten consent-test fixtures that had no saved connection
check under the concurrent ADR 0167 change. Those fixtures now explicitly issue/save a
synthetic successful Settings observation, supply the fixed Danish model language and
retain their internal provider shape. All 15 consent tests pass after that test-only update;
production admission was not bypassed or changed. These are layered results, not a fresh
whole-repository run.

Git Bash executes the generated shell control flow on synthetic files; mocked flock is
used for tests that need the Linux-only command. No provider call, real runtime installation
or model operation is part of these tests. Actual Linux shared/exclusive locking and native
symlink behavior remain separate acceptance.

Remaining live acceptance includes real Linux lock inheritance and signals/forced exit,
actual project-drive identity/rename semantics, uncertain create/POST recovery, separately
pinned model preparation/reuse, CUDA/MIG memory, real multi-file completion/late admission,
Stop/reopen/recovery, owned-run cleanup and measured latency/cost. None is established by
the offline safeguard or this design document.
