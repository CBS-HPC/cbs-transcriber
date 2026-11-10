# 0094 — Finite resident Runtime cohorts and retained legacy inputs

Status: accepted for offline implementation; live GPU/resource acceptance remains separate.

The pooled Runtime controller previously started a new inference process for each file.
Cached remote model files did not retain loaded model objects. New frozen plans now select
`resident_models=single-slot-v1`: after at most two seconds of collection, up to eight
accepted tasks form an immutable cohort. Later acceptances wait for the next cohort.
Existing frozen plans without the policy retain their captured controller and worker bytes.

One credential-free child processes the cohort in transcription, alignment and speaker
passes. Stage selection is an internal call argument; request bytes and options never change.
An intermediate pass never announces full task completion. Final acknowledgement is separate
from the controller's immutable, independently verified outcome. Active task status follows
the actual cohort stage owner rather than the cohort's first file.

The child holds exactly one engine object. Its identity includes loader, pinned model,
device/compute type, WhisperX version and, for converted Danish models, the verified conversion
receipt. Immutable load receipts also bind the batch/cohort identity and captured worker hash.
Identity changes evict the prior object before construction, explicitly unload CTranslate2
weights without retaining a CPU copy, collect Python objects and release the CUDA allocator's
unused cache. An unload failure poisons the slot and closes the cohort before another load.
The [CTranslate2 memory API](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html)
defines that unload behavior. Failure clears the slot; cohort completion clears it and
exits the process. There is no idle resident process or unlimited admission. The whole cohort
uses the smallest member timeout; this conservative bound can stop a large cohort early.

The child inherits the allocation and shared runtime-use locks. Controller reconstruction is
permitted only after child closure is established by the allocation lock. An accepted task
whose outcome is uncertain is interrupted, never automatically replayed. Stop and timeout reap
the whole cohort, preserve already acknowledged task completion, and retain all valid partial
stage files in interrupted outcomes. A new explicit attempt may carry verified checkpoints.
Shutdown does not stop the remote allocation or imply cleanup consent.

Exact source loading compiles captured bytes and does not consult timestamp-valid bytecode
caches. Source/preparation protocols remain bound to the captured source inventory. A compressed
original-input variant must verify its own frozen transport/preparation receipts before stage
processing; a resident cache grants no new upload or source authority.

The retained singleton cleanup audit found that a terminal allocation receipt alone previously
allowed ordinary Cleanup to Trash unsaved private inputs/outputs. Cleanup now requires fresh
exact allocation ownership plus a bounded metadata-only inventory. Unsaved private or unknown
files block ordinary cleanup; an empty/public setup-only folder remains eligible. The preview
reports only fixed stage names/counts and explicit unchecked output integrity. It grants no
disposition consent. Failure settlement retains private outputs, and cached-file settlement
invokes the existing output validator before cleanup unless a producer result already exists.
Recovering or abandoning older singleton outputs remains an explicit
operation; no new inference, retry, deletion or broad task request supplies that authority.

Synthetic adapters exercise the production loaders and real file controller. Their measured
constructor counts and exact stage bytes establish scheduling/cache behavior only. Cold/hot
model load time, peak GPU/host memory, real throughput, first-result latency, Linux lock behavior
and paid allocation cost still require a separately authorized public-fixture run.
