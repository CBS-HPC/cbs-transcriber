# 0093 — Pinned Working versions and explicit review evidence

Date: 29 October 2026

## Decision

A researcher explicitly designates one immutable saved **version** as the recording's
Working transcript. `active_transcript_id` continues to select the result being reviewed;
viewing another result, editing either result, restoring an earlier version and completing
a producer do not move the Working designation. There is no implicit first-result or
migration assignment. Earlier reviewed choices do not establish earlier research approval.

Schema 14 adds `recording_result_roles` and append-only `result_role_events`. Migration
backs up a schema-13 library under its existing exclusive lease before writes, then creates
empty role tables in one transaction. Existing research rows and payloads stay exact.
The pointer names a same-recording immutable version of a live saved result. Each role
event records exact preceding/successor version IDs and payload hashes, the role revision,
time, action and unverified local actor category. The source version retains its original
producer meaning. Content versions are not added merely to move a label.

Preview binds the requested immutable version/hash, preceding designation, recording
revision and separate role revision. Apply recomputes under one writer transaction.
Marking a historical version changes neither the current result head nor the review view.
Trashing its owning result clears the designation in the same transaction, even when the
Working version is older than that result's head or another result is being reviewed.
Restoring the result does not restore its designation. Ordinary producer idempotency still
returns the original result, including Trash, without modifying history or role state.

Project downloads and default research reports use the exact designated version payload,
verified against its hash and ownership in the same read snapshot. Unassigned recordings
are explicitly skipped; project exports can produce a manifest-only ZIP. There is no
fallback to the newest result. Explicit reviewed-result/all-result downloads and explicit
saved-version reports retain their named scope. Metadata-only role history validates a
bounded complete event chain and emits scoped aliases, never transcript text or raw IDs.

## Explicit conflict decisions

The existing enhancement common-base and immutable creation-version proof remains the
authority for passage correspondence. A bounded preview displays base/current/incoming
wording, timing and speaker assignment. Researchers choose each group explicitly; omitted
groups preserve their complete current dictionaries. Apply binds all decisions, exact
parents, the recording revision and complete proposed payload hash, and recomputes within
the writer transaction. Client-supplied merged passages are never accepted.

Incoming wording uses the literal free-text edit, invalidating old word mapping. Incoming
timing requires the exact current literal wording and a valid current mapping. Missing,
split or joined correspondence remains unresolved. Unknown speaker labels cannot replace
known voices; incoming label/name collisions cannot rename reviewed voices. Keeping current
values is an immutable `result_conflict_review` observation even when no payload changes.
All earlier versions remain restorable. Reports reconstruct the complete successor from
its exact predecessor and cited parents before exposing decision aliases.

## Producer observations

The first public producer integration covers new local full-recording alignment/speaker
enhancements through the existing admission path. A dedicated policy freezes this new
behavior; jobs without it retain legacy recovery. Exact request and prepared-input bindings
are sealed before inference; the actual worker checks its input binding and records only
allowlisted environment facts. Valid completed stage files are durably sealed and observed
after the worker is drained. A dedicated job writer inserts an absent checkpoint or accepts
an identical retry, never replacing earlier observations or unrelated job state.

`Store.add_result(producer_artifacts=...)` validates exact persisted producer ownership and
observations after producer idempotency, then binds them to the newly allocated sequence-one
version ID and exact payload hash before INSERT. Source dictionaries cannot assert capture.
Edits, restoration and explicit merges inherit that original bound observation unchanged.
Historical reports validate the original creation row and stored producer checkpoint, with
no current file, runtime, model-cache or credential lookup. File availability remains
`not_checked`; unobserved request/response/environment roles remain `not_captured`.

The existing internal local full-ASR seam supports the same contract without creating a new
public provider or retrospectively marking legacy jobs. Remote producer capture uses its own
accepted-task boundary; local observations never claim remote execution or provider internals.
Fresh API transcription jobs use the separate `provider_artifacts` contract in ADR 0097;
fresh native UCloud Transcriber jobs use `native_artifacts` (ADR 0100).

## Per-unit same-result local timing

New public local passage-timing and opt-in edited-passage jobs freeze `local-timing-v1` only
at their explicit fresh enqueue boundary. Each actual bounded unit (at most 60 seconds) gets
its own immutable source-projection/unit fingerprint, exact credential-free request and prepared
audio binding before inference. The actual worker verifies the request and decoded input.
Validated alignment/environment output is sealed after drain. The separate normalized assembly
hash records the mapping of unit outputs back to frozen passage IDs, including nullable words
and one application of the unit's offset. It is a producer observation, not a claim that hashes
alone replay or prove the transformation algorithm.

Preexisting completed unit files without receipts remain explicit gaps. They can still finalize
useful timing through the established validators without importing a runtime or repeating
inference to reconstruct evidence. A dedicated short writer publishes the complete normalized
output and its assembly hash together, preserving other job state.

Local Apply reads the current complete job, exact frozen source version/projection and current
active result from one writer transaction. It retains every target whose current wording,
bounds or word map changed, and every unselected passage dictionary and speaker name. Candidate
words must be a valid current literal mapping. The recording mirror, saved result, immutable
timing successor and applied-job receipt commit together. A failed receipt write rolls them all
back. Exact version/hash/parent/job/source/output references reconstruct the complete successor
on a lost-reply retry, before current-view/revision refusal, without restoring a trashed result
or changing the review selection.

`timing_artifacts` binds this distinct timing successor. It does not replace a result's original
provider job, `producer_artifacts` or `source_version_id`. Later edits/restoration inherit the
original timing observation unchanged; reports validate its original application row and exact
parents and expose scoped aliases. Legacy jobs without capture policy keep explicit missing
artifact evidence. No artifact hashing or audio decoding occurs inside the Apply transaction.

## Runtime full-ASR publication and collection

New public CBS Runtime full-ASR requests freeze `runtime-artifact-v1` at fresh admission.
Ordinary remote enhancements, selected repairs, native Transcriber jobs and existing frozen
jobs do not acquire this policy. Read/recovery validation checks a present marker without
deriving today's policy for a missing legacy marker.

The existing epoch/task writer retains an immutable `artifact_origin` in its input receipt
before private publication. It binds the exact credential-free request bytes, accepted input
manifest, batch/task/attempt identity and compatibility hash. Validated stage/control bytes
already received by the collector are observed outside writer locks, then appended to the
existing remote receipt in the same checkpoint that makes the task terminal. There is no
new remote protocol, inference, allocation or upload. A terminal receipt cannot acquire
retrospective observations.

`runtime_artifacts` is an explicit Store keyword. The fixed validator reads the actual owning
member/current attempt and durable input/output receipts in the creation transaction. It binds
the observations to the exact sequence-one version and payload hash; source dictionaries and
omitted explicit evidence cannot fabricate or silently discard a present observation. Existing
producer/Trash idempotency runs first. Edits and restoration retain the original bound evidence.

The boundary is `validated_local_collection`: downloaded stage JSON is observed output, and
a locally normalized task-outcome document is control output. Neither is labelled original
provider wire-response capture. Worker environment remains `not_captured`. Original/remote
preparation receipts retain their own source, decoder and sample-clock scope; remotely prepared
jobs do not acquire a fictional local WAV observation.

Historical reports verify actual result/job/member/attempt ownership, immutable creation and
receipt references in one bounded snapshot, then emit hashes, sizes and aliases without current
file/runtime access. A present remote preparation receipt is validated explicitly. Only an
exact match to its recorded preparation window/bytes is described as remote decoded PCM.
Fixed inventories distinguish managed originals, producer-directory request/descriptor files
and current/archived attempt outputs; they grant no restore, upload or cleanup authority.

In the coordinator, failed outcomes capture only valid reusable stages and observed
controls; invalid raw stage files remain retained without a validated claim. Separate
`artifact_files`/`artifact_checkpoints` join the same owner/epoch/task CAS that makes the
attempt terminal, while reusable stage `files` keep their existing meaning. Compressed-original
inputs keep their own descriptor and source names. Cached/recovered finalization reuses the
immutable observations and never backfills them from file existence. Per-attempt archives
preserve the recorded output controls alongside stage files.

## Consequences and verification

Researchers can keep editing alternatives and can explicitly choose a historical research
snapshot for publication without restoring it into the editor. Working badges show the
exact version, including when a saved result contains a newer head. Draft and delayed-response
guards prevent a late preview from marking a different historical choice.

Synthetic migration/preservation, ownership/hash drift, stale preview, writer rollback,
Trash/restore/idempotency, exact pinned exports, immutable conflict decisions and actual
browser workflows are verified separately from any real-library activation or inference
acceptance. No real-library migration is authorized by this implementation.
