# CBS Runtime accepted-task artifact hooks — 29 October 2026

This completes the client-side Runtime full-ASR observation boundary in ADR 0093, alongside
the separately reviewed helper/Store binding. It adds no remote protocol, provider request,
allocation, inference stage, upload or runtime/model preparation.

The coordinator records a real request origin with the existing publication-intent CAS.
It captures only content-validated collected stage files plus actually downloaded controls,
then commits their observations in the same attempt/epoch/task update that records terminal
processing. Reusable stage receipts keep their existing meaning. Actual outcome bytes are
preserved through the existing single bounded read for marked requests; legacy normalization
and missing capture policy retain their behavior. The final producer document is built from
persisted owning attempt receipts and bound in the Store's sequence-one transaction.

Focused actual coordinator/Store checks cover both original-derived local WAV and compressed
original preparation. They verify exact raw outcome bytes, request/input digest binding,
observations before terminal transition, exact result/version/payload binding, cached
finalization without new observations, one ASR call and one saved result. Failed tasks record
only their valid ASR subset and observed controls; an invalid complete alignment cannot acquire
a validated collection receipt or create a result. Historical project reports are exercised
with current file reads and artifact observation forbidden, and malformed present remote
preparation evidence is refused. Per-attempt archives retain preparation controls.

The pure helper and actual Store binding cases are tested separately. Final combined scoped
counts are recorded when integration verification completes; earlier focused checks passed
three publication/creation/cache cases and five partial/refusal/real remote-report cases.
Those overlapping counts must not be added.

All inputs/libraries are synthetic, UCloud/control-plane operations and inference are mocked,
and the existing source environment is reused with child-only `PYTHONPATH`. No model weights,
paid request, real recording, private upload, live service restart or main checkout edit is
part of these checks. Capture describes observed bytes and their producing boundaries;
worker environment and provider-internal raw response remain `not_captured` when unobserved.
