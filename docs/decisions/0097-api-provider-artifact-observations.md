# 0097 — API provider request and output observations

Date: 29 October 2026

## Decision

Fresh API transcription jobs (OpenAI, custom OpenAI-compatible and UCloud vLLM) freeze
`provider_artifact_policy: api-artifact-v1` only in original-input admission's `prepare`,
the same fresh boundary that freezes the local and Runtime capture policies (ADR 0093).
Generic insertion, Retry, recovery and older materialized jobs never derive the policy;
an absent marker keeps the exact earlier request, checkpoint and result behavior. Native
UCloud Transcriber, CBS Runtime, enhancement and repair jobs are not eligible.

Before any audio for a part is uploaded, the coordinator writes a credential-free logical
request descriptor to `chunks/<job>/request-<part>-<attempt>.json`, observes its exact
bytes and records the attempt through `provider_capture`'s short job writer. The descriptor
binds the recording/job, part index and attempt, provider type, a SHA-256 of the base URL,
the frozen original snapshot hash, the prepared part identity (or the unchanged original)
and the logical SDK fields. Every later part and retry replays the first recorded fields;
within one process the catalog is fixed, so a changed field set only occurs after a restart
and is then sent explicitly. Keys, tokens and Authorization headers are never written.

The accepted part worker preserves the SDK's normalized response as before, then observes
the descriptor and `response-<part>.json` outside writer transactions. The coordinator
publishes that observation together with the normalized part, replacing the generic
chunk checkpoint for policy jobs. A response file preserved before its observation was
published is reused without another request and recorded only as a normalized part hash:
it remains an explicit gap and is never backfilled from file existence. Failed requests
retain their descriptor with an unobserved outcome. Before creation the coordinator records
the exact assembly hash, and the writer proves that every saved part matches its observed
unit and that the parts assemble to that hash.

`Store.add_result(provider_artifacts=...)` re-validates the persisted job document, its
saved parts and assembly in the creation transaction, then binds every checkpoint to the
new sequence-one version and payload hash. Source dictionaries cannot assert capture;
omitting a present observation fails. Edits, restoration and merges inherit it unchanged.
Generic job writes cannot change the policy, observations, settings, source snapshot,
transport or preparation receipt.

The descriptor is `logical_sdk_request`, not the multipart HTTP request, and the output is
`normalized_sdk_output`, not wire bytes. Server processing, the environment and file
retention remain `not_captured`/`not_checked`. Reports verify the bound copy against the
creation row and the job's stored observations and parts in one bounded snapshot, then emit
part ordinals, hashes, sizes, input identities and the logical fields only.

Portable inventories classify request descriptors as `processing_input`; capture verifies
every promised request/output file. The contained semantic checker executes this exact
pinned source to verify present job and version observations and their mapped files. It no
longer fails closed for the `provider_artifact_*` namespace; malformed values still fail.

## Not covered

Native UCloud Transcriber submissions and downloads, including shared native batches,
are covered separately by ADR 0100.
No live provider, wire-level capture, real-library activation or browser acceptance is
claimed. Older API jobs keep their explicit gaps.
