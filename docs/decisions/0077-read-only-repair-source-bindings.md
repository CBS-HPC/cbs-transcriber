# 0077 — Read-only frozen repair source bindings

Accepted 28 October 2026 for T90a. This is an internal source/projection inspection boundary
before bounded bundle production. It does not change queueing, retries, audio preparation,
timing application, provider approval or public/remote repair admission.

## Three different payloads

A repair has a full preserved source version, its actual frozen job snapshot, and the selected
target projection. These are not interchangeable. `Store.freeze_result` preserves the full
version ID/hash. Editor passage timing then trims `source_snapshot.segments` to its timing
units; ordinary scoped enhancement, re-transcription and automatic edit realignment retain
the full snapshot. A full-version hash must never be checked against trimmed snapshot bytes.

`repair_sources.inspect_repair_source(store, job_id)` reads the job, its frozen source and the
exact owning result/version in one SQLite read transaction. SQL byte-length preflights precede
loading/parsing job parameters and preserved payload: 32 MiB each, 64 MiB aggregate. Use the
existing canonical history decoder to verify the full version's exact bytes/hash. No nested
Store read may escape that transaction and substitute a later observation.

Prove job recording → result → exact version ownership, explicit source identifiers/full hash,
and the frozen speaker map. Require a declared supported scoped operation and exactly one
single-ID or multiple-ID selector, with valid unique membership. Ordinary caller selection
order may differ; the derived projection always follows preserved source order. For passage timing,
prove the exact selected-only snapshot and timing-unit membership, including complete maximal
adjacent unbounded groups. For ordinary scoped operations, prove the exact full snapshot.
Compare complete preserved passage dictionaries, retaining Unicode, original text/words,
structural flags, bounds and current word maps. Then derive the selected projection/hash
separately, in source order. Selection order cannot redefine transcript order.

## Receipt and limits

Return a small internal identity/hash/ordered-ID receipt with the operation and source scope.
No transcript bodies, speakers' names, user labels, paths, credentials or arbitrary job fields
are returned. `observed_params_sha256` hashes the exact parameters observed in that read;
parameters may later gain progress/output fields. It is not a frozen request, pooling key or
remote task/attempt identity. Full-version and selected-projection hashes retain distinct names.

This proof concerns a historical frozen source, so it does not require today's active result,
head, recording transcript projection or visibility. A source result later placed in Trash
remains inspectable; inspection never restores it or permits a new upload. Missing historical
version evidence refuses this new inspection without changing existing legacy execution/retry.
Current provider eligibility, cancellation/deletion obligations and stale application guards
remain separate mandatory checks at their existing boundaries.

Frozen window metadata may be checked for supported shape and bounds, but this receipt does
not certify actual decoded samples, an original/prepared time mapping, recording duration,
text/speech agreement or inference readiness. It does not turn a neighbour search gap into
passage timing. Do not recompute a historical window from today's duration or neighbours.
T87 extraction receipts and subsequent T90 bundle/transport/output validation are still needed.

The inspector is read-only and opens no media files, runtimes or network endpoints. Its
production paths are not wired into admission or reports in this slice. A future shared
report must alias internal IDs rather than publish this internal receipt directly.

## Verification

Use real synthetic Store freezing/enqueue shapes plus controlled concurrent writers. Cover
full versus trimmed snapshots, distinct projection hashes, source ownership, dictionary/speaker
tampering, strict selectors/groups, Unicode, legacy missing evidence, newer heads/Trash,
pre-read byte limits, snapshot consistency, no filesystem/runtime access and no mutations.
No real library, private recording, model weights, paid request or UCloud job is required.
