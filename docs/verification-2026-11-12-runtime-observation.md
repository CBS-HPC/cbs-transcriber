# Runtime preparation observation recovery — 12 November 2026

Implemented locally on `codex/mvp-v2-runtime-reliability`, from public-descendant preview
checkpoint `5cab832`, under ADR 0175 / SPEC V218/T143. This is an observation-only stability
slice, not recovery of the historical retained lock or proof that a real environment is ready.
It is not loaded in the user's running preview, merged to main or published.

## Changes

- A preparation-scoped typed reader distinguishes retryable reads and bounded partial
  publication from permanent authentication/identity/generation/resource/pin refusals.
  Existing shared legacy readers retain their contract.
- Status polling and terminal-artifact settling keep the same durable Activity owner,
  allocation ID, epoch and submission journal. Bounds and deadlines remain explicit.
- Ready publication rechecks current owner, epoch, Stop and deadline under its writer
  transaction. Stop during I/O, backoff or writer wait returns through owned Stop observation.
- Retrieval requests explicitly include application and parameters, matching the released
  SDK and primary API contract; fixtures now enforce those flags instead of always returning
  a full specification. No exception-message parsing is used as authority.

The remote worker/supervisor recipe, requirements/model pins, runtime_capture source and
contained-checker caps are unchanged. No new submission, cache/lock mutation, environment
synchronization, model download or real provider call was performed.

## Verification

Three original-product red cases reproduced transient-status failure, SUCCESS preceding
supervisor publication and late Stop overwritten by Prepared. The initial reproduction also
had three fixture API-shape errors; these were corrected separately and retained. A later
request-flags regression went red before its typed-reader correction.

The final focused gate has **100 passes**, no failures/errors/skips, and 584 identical
before/after input hashes. The separate independent adversarial gate has **17 passes** on
the same final source, with 133 identical captured inputs. XML testcase identities are unique
and disjoint: **117 distinct passes**. The earlier 78-pass/15-failure development run is
retained; its curated-prefix, old completion-hook and narrow fixture-call-shape remedies do
not replace or weaken the final semantic/Stop oracles.

Independent code and evidence review is **Go**. Ruff/format and CRLF-aware whitespace checks
pass. Private reports retain source/test manifests and original outcomes; only hashes are
recorded here.

| Artifact | SHA-256 |
| --- | --- |
| runtime_preparation.py | `dd577b938dbd328377ddbdc75be7b7b99bc52282bd7350442cc752c9e2a11787` |
| runtime_preparation_observation.py | `4bed8add6af21717c7a0a7b314062f0c1df951142eb8b4ae74415d040f73881f` |
| focused-final.xml | `53a18fe1c48a7ad24690504edf95fe7924de16c33a38adad1be7eaf261e14640` |
| independent results.xml | `074647bde10bc2294764cb746b4c36352fd2527c5fd4ea6f129cd4941710f22a` |
| focused-final-before.json = focused-final-after.json | `85a04c842bae4fd37979dc73eeb3fa10a1c00de5a8e526f8e276a828d8143acb` |

## Remaining limits

The historical `.prepare-lock` remains untouched and unresolved. Isolated-generation recovery
requires its own complete admission/legacy/HF/capture/restore proof and controlled live
acceptance; CPU fallback needs a separately pinned flavor/readiness and funding policy.
Physical UCloud mounts/specification normalization, Linux locking, CUDA/MIG memory, actual
model preparation/reuse and real batch/Stop/cleanup acceptance remain unverified.
