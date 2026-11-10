# 0100 — Native UCloud Transcriber submission and output observations

Date: 30 October 2026

## Decision

Fresh native UCloud Transcriber jobs freeze `native_artifact_policy: native-artifact-v1`
only in original-input admission's `prepare`, beside the API (ADR 0097), local and Runtime
policies. Eligibility is a frozen `ucloud` provider with an admitted managed original and
original transport. Generic insertion, Retry, reconciliation, recovery and older frozen
native jobs never derive it; an absent marker keeps the exact earlier journal, raw file,
cleanup and recovery behavior. Single jobs and shared batch members both use the policy.

### Submission boundary

After inputs are uploaded and verified, and before the POST that creates the allocation,
each job that the allocation serves writes `ucloud-<job>.request-<attempt>.json` in its
own recording folder and records it through `native_capture`'s short writer. The
descriptor binds recording/job, attempt, scope (single or batch with its batch ID), a
SHA-256 of the server URL, the frozen original snapshot hash and the remote input name
(`recording<suffix>` or the batch member's `<job-id><suffix>`) with the original's hash,
size and format, plus the frozen model/language used later to match outputs.

The submission itself is a credential-free projection of the exact specification about to
be POSTed: application, product, name, replicas, time allocation, SSH flag and mounts are
recorded exactly; parameter values are recorded when the declared kind is a bounded choice
(enumeration, boolean, numeric) or owned path, or when this app sets the parameter itself
(input, output, model, language, format, batch directory). Other free-text values, such as
a template's `archivePassword`, are still submitted unchanged but are withheld from evidence
by name only, with no value or hash. Unknown top-level fields are likewise withheld by name.
Tokens, transfer sessions and Authorization are never recorded.

The remote journal records `artifact_submission_sha256`, the digest of the accepted
projection: in the single job's `ucloud_run`, or once in the shared batch row. Batch
members each keep their own descriptor (same projection, their own input binding) while the
batch row continues to own folder, upload, POST, remote ID, raw hashes and cleanup receipts.
A reconciled-absent retry records a new attempt; the last attempt is the accepted one.

### Output boundary

Policy jobs keep the exact downloaded output bytes in `ucloud-<job>.json` (legacy single
jobs keep the earlier re-serialized file). The file and descriptor are observed outside
writer transactions as `raw_response`, with the matched output leaf, which must follow the
exact input identity and frozen model/language rule. The observation and the exact assembly
hash of the parsed passages are recorded together. An output preserved before its
observation was published is reused without another download and recorded as
`not_captured`; it is never backfilled from file existence.

`Store.add_result(native_artifacts=...)` re-validates the job document in the creation
transaction, including the owning journal: a remote job ID, the journaled submission digest
matching the last descriptor, batch membership/settings, and for batches the journaled raw
hash. It binds every checkpoint to the sequence-one version and payload hash. Source
metadata cannot assert capture, omitting a present observation fails, and generic job
writes cannot change the observations, settings, source or batch identity. Edits inherit it.

### History and portability

Reports verify the bound copy against the creation row, job document and owning journal
in one bounded snapshot and emit attempt ordinals, hashes, sizes, application/product,
recorded and withheld parameter names, input hash/size/format and the output hash only;
remote execution, the environment and retention are `not_checked`/`not_captured`.
Portable inventories classify descriptors as `processing_input` and verify promised files;
the contained semantic checker runs this exact pinned kernel beside the API kernel.

## Not covered

No live UCloud allocation, real Batch output naming beyond the existing mocked contract,
real-library activation or browser acceptance is claimed. The projection is not the HTTP
request and does not prove remote execution, model weights or the provider environment.
