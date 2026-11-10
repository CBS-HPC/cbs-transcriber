# Working results, alternatives and explicit review decisions

28 October 2026; implementation amended 29 October after the researcher's explicit choice.
The Working label pins an **exact immutable saved version**, not a result's moving head.
Schema 14, public mark/version-preview controls, exact default project exports/reports and
explicit conflict review are implemented on the isolated development branch.
[ADR 0093](../decisions/0093-working-versions-and-review-evidence.md) is authoritative for
the revised role contract. Real-library activation is separate and has not been performed.

## The distinction that must be preserved

`Store.activate_result` changes `active_transcript_id`, the recording projection and its
revision. It appends no result version: ordinary review/comparison selection is a view
operation. Reusing this pointer as the working designation would make every model comparison
silently promote an alternative. The working designation therefore needs separate state.

Recommended terms:

- **Working transcript:** the recording's explicitly designated immutable saved version for
  the main research workflow. Later edits create versions without moving the designation.
- **Alternative:** another retained live saved result, with all its existing edits and history.
- **Review view:** the result currently displayed; it can be the working result or an alternative.
- **Role event:** an append-only observation that designates or clears the working result,
  separate from a content edit and from its original producer ancestry.

The existing result ID, producer job, source version, originals and word maps remain intact.
No copying a model output into an unrelated result merely to change its role.

## Recommended first vertical slice

Add a recording-scoped immutable-version pointer with its own role revision and an append-only
role event table. Migration and first-result creation leave every recording unassigned.
An existing active selection never establishes researcher approval. Existing result/version/job,
note and transcript bytes stay unchanged. Take the ordinary schema backup under the library
lease before any migration write. This supersedes the earlier migration-observed active-result
initialization proposal.

The role service should own these small operations:

| Operation | Contract |
| --- | --- |
| Read roles | Return the exact working designation and live alternatives from one snapshot, with no media/runtime lookup. |
| Preview promotion | Resolve one immutable same-recording target version of a live result, the current exact designation and role revision; return a metadata fingerprint and visible origin. |
| Apply promotion | Recompute under one writer transaction, verify expected recording/role revisions and exact version/hash, then append the role event and switch the working pointer atomically. |
| Clear on Trash | Trashing the working result explicitly clears its designation and records why; it does not promote the newest alternative automatically. |
| Create-result hook | All model/import/enhancement outputs retain their data without assigning or moving a Working designation. |

Role events must retain previous/next result and exact version/hash references, action,
observed time and local actor category. They do not establish a researcher's identity.
Metadata-only reports use scoped aliases after checking ownership/hashes in their bounded
snapshot. Earlier events are not rewritten when a result is edited, restored or moved to Trash.

Ordinary Review result selection must not change roles. Marking a version does not activate
or restore it into the review view. Current draft/navigation guards remain. New producer results
cannot overwrite the Working designation merely
because they finish later. Existing completed-producer idempotency still cannot recreate or
restore a trashed result.

Default project exports and research reports use the exact Working version.
The recording's current-view export and explicit selected-result or selected-version export remain
available for alternatives and historical evidence. Missing working designations are reported
as skipped items, not replaced by a guessed latest alternative.

## Initial role policies agreed for implementation

These are small product policies, not reasons to rewrite the existing version model:

1. **Alternative editing:** preserve existing reviewed alternatives and their edits. Alternatives
   remain editable as their own saved results. The broad 23 October proposal recommends
   read-only alternatives; that restriction is not part of this first slice.
2. **Promotion observation:** prefer one role event rather than an identical content version.
   If the UI promises a new version, append a same-payload role observation deliberately and
   keep its producer `source_version_id` unchanged. Ordinary view selection still creates none.
3. **Explicit assignment only:** neither a never-assigned designation nor one cleared by Trash
   can be filled by a producer. Only a deliberate exact-version action assigns it.
4. **Activation boundary:** role behavior, default exports, new-result policy and Trash must
   switch together. A table alone is an internal prerequisite, not a completed user feature.
   Current-view exports retain their explicit existing selection until working-result export
   scope is implemented and activated atomically.

The Preparation owner currently edits Store/queue/app admission. Role schema and writer hooks
must be integrated serially with that work; a new module can isolate the role transactions,
but must not create an unversioned side table or bypass Store's schema checks.

## Fuller conflict handling

`result_merges` already proves incoming creation-to-base correspondence, reads the reviewed
and incoming heads in one snapshot, and fingerprints the complete proposed successor. It
preserves reviewed wording, originals, named voices and structural changes. `wording_adoptions`
already applies one explicit literal wording choice using free-edit semantics.

Extend those proofs with explicit per-passage, per-group decisions rather than a new fuzzy
merge engine. Groups are wording, timing and speaker assignment. A preview shows the exact
base/current/incoming values; Apply binds the decisions, both current heads, base, creation,
recording revision and proposed-payload hash. Recompute the whole plan under the writer
transaction. Do not accept client-supplied merged passages.

Incoming wording uses the existing free-text edit and invalidates stale word mapping. An
explicit incoming timing choice still requires the exact current literal text and a valid
current word map. Changed/split/merged/deleted passages without proven correspondence remain
unresolved; do not reintroduce them by matching IDs or similar text. Speaker choices never
rename or merge voice identities by inference. Every unselected passage dictionary remains.

Accepted and rejected conflict choices need immutable review observations. Keeping the current
wording is a real decision even when the payload is unchanged. Preserve that decision in a
same-payload review event/version with source references; do not silently discard it as a no-op.
Long-lived suggestions and bulk Accept all remain separate from this bounded explicit review.

## Processing artifacts and environments

Original-input and import-artifact receipts are already implemented. The remaining gap is
producer-time capture of exact raw API responses, worker request/output files and the actual
processing environment. Report generation must never inspect today's environment or scan old
files to imply those observations existed earlier.

A small `provenance_artifacts.py` boundary can capture stable, bounded, owned regular files
and produce strict role/hash/size/format receipts. It must derive ownership from the producer
service, refuse links and read growth, and expose no filename/path/text/credential in the
report projection. A set of receipts binds to the exact producing job and result creation
version; caller-supplied dictionaries cannot self-assert historical capture. Retained-file
availability stays distinct from the earlier hash observation.

Environment receipts belong to the actual worker and stage. Capture only an explicit
allowlist of interpreter/package versions, app/worker/lock hashes and concrete device/recipe
facts. Do not capture environment variables, host/account names, arbitrary package output or
tokens. An API model name is the requested hosted identity, not recoverable provider weights.
Converted-model source pins, recipes and generated artifacts remain distinct observations.

### Implemented internal artifact boundary

`provenance_artifacts.py` supplies the observation boundary. The isolated implementation now
integrates public local whole-recording enhancements with durable producer checkpoints,
creation-transaction binding, inheritance and historical report validation. Schema-14 Working
versions and explicit conflict review are implemented. Other operation capture gaps remain
explicit; this is not evidence of real inference or deployment acceptance.

The integration contract is deliberately small:

1. The producer finishes and durably flushes its credential-free request/output files. It
   supplies the actual managed directory from trusted job/recording ownership and holds the
   applicable producer/relocation guard through checkpoint publication. Hashing stays outside
   a SQLite writer transaction and queue lock.
2. `capture_file(path, managed_root, role=..., format=...)` observes exact stable bytes and
   returns an immutable in-process observation. The four roles are `request`, `raw_response`,
   `output` and `environment`; formats are JSON, JSONL, text or opaque bytes. Empty opaque
   responses are observations, never proof of a successful transcription. Environment is JSON
   only, bounded to 64 KiB and validated against fixed nullable worker-fact sections.
3. `make_checkpoint(captures, producer=...)` accepts observations, not caller dictionaries.
   Its producer anchor is `{recording_id, job_id, request_sha256, stage}`. `request_sha256`
   binds preserved exact frozen request bytes; it is never a later hash of mutable
   `jobs.params`. When a request artifact is included, its exact hash must equal the anchor.
   Omitted artifact roles explicitly remain `not_captured`.
4. The trusted integration persists that checkpoint with its job. At result creation it
   proves job ownership and the exact creation row, then uses `bind_creation(checkpoint,
   producer=..., creation={result_id, version_id, payload_sha256})` in the committing
   transaction. The helper performs no database lookup and grants no authority from an
   arbitrary dictionary. Preserve the original producer lineage and inherited observations.
5. `validate_checkpoint`, `validate_bound` and `public_projection` are pure historical
   validation against exact trusted reference dictionaries. They read no current file,
   package, interpreter, model or runtime. The public projection contains hashes, sizes and
   the allowlisted worker observations, with no path, filename or job/result/version IDs.
   Retained-file availability is always `not_checked`.

Request/response role classification must be honest. A normalized SDK dump is output rather
than original wire-response bytes. Where a historical job has no retained exact request,
leave the evidence uncaptured; do not synthesize it from today's settings. The helper cannot
capture inaccessible provider weights, prove that public packages were not modified, or turn
an observed environment into a complete reproducibility claim. Unknown environment sections
remain null. Package versions use bounded numeric release forms and known CPU/CUDA/ROCm build
suffixes, excluding arbitrary local metadata that could leak host/user information.

The helper refuses linked/reparse/junction children and multi-link regular files. Bounded
reads detect growth, truncation, replacement and path/descriptor changes, including separate
ctime pairs on Windows. POSIX opens use no-follow/nonblocking flags where available. These
checks complement the caller's managed-directory guard; they do not establish directory
ownership themselves or permit capture from imported file paths.

## Verification before activation

- Migration backup/lease/rollback; exact existing research-row preservation; legacy observations.
- View switching leaves role state/events unchanged; promotion detects stale heads/revisions.
- First creation, later creation, deliberate clearing, Trash/restore and producer-idempotency
  cases retain exact data and never auto-promote an alternative.
- Preview/apply faults roll back pointer/event/projection together; independent notes survive.
- Conflict choices preserve Unicode, originals and unselected dictionaries; changed text cannot
  inherit incompatible word timing; all earlier versions remain restorable.
- Artifact receipts detect changed/linked/growing files and binding drift; historical report
  reads require no current media, cache, runtime or credentials.
- Browser checks cover role labels, explicit promotion, drafts, alternatives, export selection
  and conflict review. Real research acceptance is separate from synthetic evidence.
