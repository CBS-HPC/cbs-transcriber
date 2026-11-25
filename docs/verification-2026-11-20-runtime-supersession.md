# Disabled Runtime supersession hardening — 20 November 2026

Local branch `codex/weekend-runtime-supersession-20261120`, based on `0d871d7`.
Verified product commit: `b6edb5c`.
The source change is confined to runtime_generation_preparation.py and its supersession tests.
The root independently reviewed settlement, stored-owner decoding, consent and replay control
flow. This is offline implementation review; every activation switch remains false.

## Findings and correction

B484: a mocked remote SUSPENDED preparation was classified as failed with released capacity,
then qualified for replacement through the old broad terminal classifier. The new private
predicate requires positively final observed states for both replacement and permanent
retired-owner decoding. The legacy runtime_store classifier is unchanged.

B485: after a valid replacement, repeating its digest with evidence=null returned the same
successor without checking the complete body. The correction validates the whole canonical
type-strict body and exact policy before deduplication. Five altered-body variants refuse;
exact lost-reply replay still returns its one recorded successor without new rows.

The final-state contract follows the pinned primary source:
[UCloud JobState.IsFinal](https://github.com/SDU-eScience/UCloud/blob/2c23d295318cabdb960a8baa69dc175e806254ac/provider-integration/shared/pkg/orchestrators/jobs.go).
That contract excludes resumable SUSPENDED. Unknown/unsupported aliases are not closure proof.

## Verification

The unchanged pre-fix supersession/Stage2-adversarial baseline passed **125 cases**, 824.21 s.
Independent probes nevertheless reproduced both required-refusal gaps. Nine focused new
cases were red before the correction and green afterward (**9 passed**, 91.29 s).
Ruff check and format check pass on the two changed files.

The final legacy/HF/Stage1 group passed **249 cases with 3 platform skips**, 426.51 s, across
preparation, consent, locking, observation/adversarial, readiness guards/diagnostics, HF model
plans and generation Stage1/adversarial modules. These results do not replace real platform
acceptance. Final generation/supersession/Stage2 builder/adversarial gate: **203 passed**,
1096.69 s (66 supersession, 68 Stage2-adversarial, 69 generation-preparation). Together with
the disjoint legacy group this is **452 passed, 3 platform skips across 13 modules**. Product
and selected test hashes remained frozen during each gate. Root independently reviewed the
implementation and final scope: Go for this disabled slice. Do not add overlapping reruns
or call this the entire repository suite.

The first full run had 202 passes and one stopped-after-post fixture failure. Its generic
fake returned unsupported CANCELLED; production retrieval passes the observed UCloud state
through without alias normalization. Only that generation test now models a supported
FAILURE reply after terminate, preserving all Stop, remote-ID and ownership assertions. The
corrected isolated case passed in 15.30 s before the final complete rerun. The legacy fake
and production strict three-state predicate were not broadened. Unsupported aliases still
refuse. No source edits occurred during either immutable gate.

## Limits and next gates

No schema migration, public API or frontend control was added. Generation preparation,
candidate reservation and active pointers remain disabled. There were no real UCloud calls,
preparation, paid jobs, downloads, cleanup or lock adoption/stealing. Main, installed app,
original worktrees and libraries were preserved.

Before any activation: actual UCloud normalized replies/folder semantics, mounted locking,
generation carry through deferred jobs/repair/capture/restore, model preparation/reuse, MIG
memory and real batch/Stop/recovery/cleanup still need their separately scoped gates.
The stricter predicate here does not establish correctness of every historical caller of the
broad legacy terminal classifier; any wider lifecycle audit is separate work.
