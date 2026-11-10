# Runtime resident cohorts and retained remote obligations — 29 October 2026

The production path now retains a single real engine object across compatible task stage
passes, within finite cohorts. This implements the missing production mechanism from the
[28 October reference experiment](evaluations/runtime-model-reuse-2026-10-28.md).
[ADR 0094](decisions/0094-finite-resident-runtime-cohorts.md) records the boundaries.

New frozen plans collect at most eight accepted tasks for at most two seconds. Their child
passes through ASR, alignment and speakers, with exact original request bytes. Model changes
release the previous engine before constructing its replacement; CTranslate2 explicitly
unloads weights without moving them to CPU. A failed release closes the cohort. Stage progress
and current task identity remain observations; immutable final acknowledgements and controller
output verification establish task outcomes. Late admissions enter another finite cohort.

The constructor-count harness exercises production loaders with synthetic inference adapters.
For three fixed-Danish tasks it constructs one ASR model, one aligner and one speaker pipeline,
with six cache hits, and produces byte-identical stage files to the per-file baseline. The
comparison does not measure model loading cost, GPU throughput, memory, speech quality, Linux
locking, first-result latency or paid allocation cost. No trained weights were downloaded.

## Verification

The existing source environment was reused through a child-only `PYTHONPATH=<worktree>/src`;
no synchronization or installation modified the serving environment.

- Initial controller/loader regression run: **63 passed, 1 Windows symlink permission skip**.
- Real controller/coordinator mailbox with the resident fake process boundary: **23 passed**.
- Worker language, enhancement and Auto boundary/batch suite: **227 passed, 1 deadline
  failure**. The remaining thread was validating an Auto output during a fixed 15-second
  join under substantial concurrent filesystem/memory pressure. Its isolated rerun passed
  (**1 passed in 23.20 seconds**) with every frozen-request and no-repeat-ASR assertion.
  The test's correctness assertions were retained.
- Legacy retention guard: **4 passed**. The unsaved terminal-run case was first run against
  exact `cbaa573` implementation bytes and failed because ordinary cleanup moved its folder
  to Trash. The corrected code refuses that operation, still permits public setup-only cleanup,
  rejects mismatched allocation ownership and provides a metadata-only, non-mutating preview.
- Later captured-cohort/Auto harness run: **70 passed, 1 skipped, 1 fixture failure**. The
  fake ASR info omitted `duration_after_vad`; the worker correctly treated Auto language as
  uncertain and skipped alignment. The fixture now supplies its measured synthetic VAD
  interval for the resolved-Auto comparison. No worker language rule was weakened.

The final combined controller, production cache, legacy guard, original-transport helper and
real coordinator mailbox run passed **107 tests, 1 Windows symlink permission skip in
214.19 seconds**. This includes fixed and resolved-Auto byte equivalence, actual captured
cohort execution with a failed sibling, active stage ownership, native unload failure fencing,
late cohorts, Stop, partial output retention and controller reconstruction without replay.
Scoped Python formatting and Ruff passed. The later cleanup-settlement follow-up passed
**10 tests in 21.18 seconds**: seven legacy retention cases plus existing explicit Cleanup,
crash-after-local-output recovery and lost-connection reconnect cases. These overlap the four
legacy cases in the 107-test run. Failure settlement retains private outputs; cached-file
settlement invokes the existing output validator before cleanup rather than trusting existence.

## Authorized released-application disposition

The serving application's read-only job metadata identified one cancelled five-file Runtime
owner and five older failed singleton obligations. Two completed Runtime owners, the prior
four-file native batch and earlier completed singleton runs already reported Trash cleanup.

The released Runtime discard preview internally preserved bounded opaque artifacts and
verified originals/frozen inputs. It reported five missing results and zero verified or
unverified output stages. After direct user approval for that exact owner/folder, a fresh
preview matched the reviewed fingerprint and one fingerprint-bound discard request succeeded.
The released endpoint confirmed owned-folder absence after moving it to UCloud Trash.

Post-operation metadata reports cleanup `trashed`, stage `complete`, immutable discard consent
recorded and no unresolved obligation for that owner. All **107 job rows** remain; all **five
member rows** remain and their zero saved-result count is unchanged. No inference, allocation,
private input upload, permanent deletion or Trash purge occurred. Only this one owner changed.
The safe operation receipt is stored outside Git. Private names, paths and transcript/media
bodies are absent from this report.

The five legacy singleton obligations remain intact. Three report remote SUCCESS and two
remote FAILURE, all with no saved result. Fresh exact ownership and bounded metadata-only
inventories are required before deciding recovery or explicit disposition. Ordinary cleanup
does not authorize discarding unsaved private outputs; preview grants no such consent.

## Limits

The serving checkout was not replaced with this development branch. No new schema or real
library migration belongs to the cache. Frozen legacy controller/request/validator bytes
remain authoritative; newly captured source bundles select the resident policy. Live GPU
resource/throughput acceptance remains T85e. The combined compressed-original path must retain
its independent decoder/PCM proof and preparation receipt before inference.
