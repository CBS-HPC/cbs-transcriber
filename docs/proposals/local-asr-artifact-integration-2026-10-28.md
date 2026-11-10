# First producer-artifact integration: local full-recording WhisperX ASR

28 October 2026; implementation status amended 29 October. The existing public full-ASR path
uses CBS Runtime, so implementation activates the first producer contract on existing public
local whole-recording enhancements through an explicit `local-enhancement-v1` policy. The
internal local full-ASR seam supports this original `local-full-asr-v1` proposal without a new
public provider/API. [ADR 0093](../decisions/0093-working-versions-and-review-evidence.md)
records exact creation binding and scope. No historical backfill, extra inference or real-library
activation is implied. The detailed full-ASR contract below remains its operation-specific design.

## Existing seams and limits

- `enhancement.enhance_window` writes the credential-free `request.json` before worker launch.
  Local full ASR calls it from `run_transcription`, then appends one result through
  `Store.add_result`. That is the smallest complete producer slice.
- `enhancement_worker.write_json` preserves unchanged bytes and atomically replaces changed
  files, but currently does not flush/fsync them. An atomic replacement alone is insufficient
  for a durable artifact checkpoint. Preserve its Windows unchanged-byte behavior when adding
  a durable write/seal boundary.
- Existing ASR language evidence binds the actual prepared WAV and request hashes. Reuse its
  validation rather than introducing an independent inference-input identity.
- API chunks have no persisted exact request; their response files are normalized SDK dumps.
  Native batch preserves original downloaded JSON bytes, but fsync is currently a later
  cleanup precondition. The singleton native path reserializes JSON. None should receive
  inferred earlier capture receipts. Remote and chunked capture remain later integrations.

## Immutable origin and append-only checkpoint transport

Freeze `producer_artifact_policy: "local-full-asr-v1"` only on new local full-ASR jobs. Do not
add it to completed jobs, legacy cached stages, passage repair jobs or remote snapshots.

The job's `params.producer_artifact_capture` is a strict versioned internal document:

```text
format: "cbs-local-asr-capture-v1"
origin:
  generation: newly allocated 32-hex identifier
  recording_id, job_id: exact owning rows
  request_checkpoint: helper checkpoint with only the captured request role
  audio_binding: {input_sha256, request_sha256, audio_frames}
checkpoints:
  transcription: one cbs-producer-artifacts-v1 checkpoint, once completed
  alignment: one checkpoint only when requested and completed
  diarization: one checkpoint only when requested and completed
```

`origin` is written once and must remain byte-for-byte equivalent across retries. Its audio
binding uses the existing prepared-input/language validation. The request checkpoint's
producer/request hashes and `audio_binding.request_sha256` must agree. Each checkpoint's
producer anchor is the same recording/job/request hash plus its explicit stage. Each stage
contains only its own output file's observation; request is the same captured request receipt.
A normalized provider dump
never acquires the `raw_response` role. Uncaptured raw response and environment remain
`not_captured`. Do not collect the coordinator's environment as the worker's environment.

Checkpoint publication may only insert an absent stage or repeat an exactly identical stage.
It cannot replace an earlier observation. This document is transport in mutable `params`;
its immutable origin/checkpoints are enforced by the dedicated writer, not by assuming all
of `jobs.params` is immutable. No hash covers a later mutable job document.

## Capture and retry boundary

1. Resolve the concrete device and exact frozen options using existing logic. Durably finish
   the exact request and prepared input, then capture request bytes and persist `origin`
   before launching inference. New origin creation refuses existing stage output files.
2. The worker must consume that exact request and prepared input. Validate its actual input
   binding against `origin`; do not merely compare options parsed from another live document.
   The existing ASR language binding supplies this proof for the transcription stage.
3. After a newly completed stage is closed, validate the stage as today, durably seal its
   exact file and parent directory, and capture it. Publish its checkpoint before marking
   that stage captured. Capture occurs at this producer boundary, never in a report.
4. On restart, reuse a captured stage only if its immutable origin and checkpoint still
   verify against its files and frozen source. A changed request, input or output fails
   closed and preserves artifacts. A file without the new stage checkpoint remains an
   artifact with an uncaptured provenance gap; do not invent its earlier environment or
   silently adopt it as new evidence. Existing stage-content/input validation may still
   recover useful transcript text without claiming producer-time artifact capture. Preserve
   existing legacy recovery behavior for jobs without the policy marker.
5. A fully checkpointed job can finalize without inference, downloads or runtime inspection.
   Partial checkpoints and the original request-only checkpoint remain useful if a later
   stage fails. Inference must not repeat merely to reconstruct evidence. Missing observations
   remain gaps rather than synthesized facts; receipt completeness must not become a reason
   to lose an otherwise verified transcript.

Use the active producing job and existing storage/producer guards to prevent relocation while
capturing. Keep file hashing outside queue and SQLite writer locks. If a snapshot guard is
needed during reads, release it before acquiring the queue lock; never invert queue → snapshot
→ SQLite order. The final Store callback does no file I/O. Cancellation and drain semantics
must continue to prevent a storage move while the producer still owns files.

## Exact creation transaction and historical inheritance

Use an explicit internal `producer_artifacts` keyword at `Store.add_result`, parallel to the
import receipt boundary. A source dictionary cannot assert capture. Before its existing
`BEGIN IMMEDIATE`, all file observations are complete. Inside that transaction a fixed
trusted validator, not a user callback, must:

- Require an existing same-recording local full-ASR producing job and its exact policy.
- Read the persisted origin/checkpoints from the current parent row, compare the complete
  supplied observations, validate the origin's frozen request/audio scope, and reject any
  claimed capture without its exact receipt or any ownership drift. Preserve
  `_result_origin`'s source-version proof. Uncaptured requested stages remain explicit gaps;
  they cannot be presented as captured stages or trigger invented evidence.
- Reuse existing producer idempotency first: an existing result, including Trash, is returned
  without altering its original context or filling a legacy gap.
- In `_append_version`, after allocating the new version ID and computing its exact payload
  hash but before INSERT, call `bind_creation` for each checkpoint using the result ID, that
  sequence-1 version ID and hash. Include the immutable origin request-only observation and
  each completed captured stage; do not synthesize absent stages. Commit this context in the
  same transaction as result, active projection, job completion, follow-up and existing
  runtime-result receipts.

There is no post-commit version/context patch and no reinterpretation of `source_version_id`.
Carry `producer_artifacts` unchanged through `Store.save_transcript` and
`result_actions.INHERITED_CONTEXT`; it remains bound to the original creation, even after
edits, restores, merges and Trash. It is never rebound to a reviewed successor's payload.
Reports later compare the exact stored checkpoint and creation row in one bounded snapshot,
using original creation ownership/hash rather than trusting references inside the receipt.
They perform no current file/runtime lookup and retain availability `not_checked`.

## Independent negative-test checklist

- Refuse origin after existing output, request/audio drift, changed stage receipts and a
  second incompatible origin. Retry of identical origin/checkpoint performs no rewrite.
- Failed flush/fsync/close/checkpoint publication leaves no captured stage; preserve files
  and existing checkpoints. Kill between output and receipt retains an explicit gap. Recovery
  can save content that independently passes its frozen-input validation without inventing
  earlier artifact capture or repeating inference merely to fill the gap.
- Refuse another recording/job, remote or repair policy, supplied source claim, missing parent,
  stale frozen input and checkpoint injection. Test a direct Store call as well as the worker.
- Wrong creation/version/hash and historical source/context mismatch fail closed. A writer
  exception rolls back result/version/job/follow-up together, preserving independent notes.
- Existing producer/Trash idempotency never backfills, restores or reruns inference. Reopening
  after successful checkpoint publication can finalize without importing inference packages.
- Edits, restoration and explicit merges retain exact producer receipts and original ancestry.
  Tests must show report validation uses trusted sequence-1 rows and no filesystem/runtime.
- Cancel while capture/finalization runs preserves current drain/storage protections and
  ordering; no queue lock is held during large file reads. No paid/live calls are needed.
