# Danish Runtime profiles and timing estimates — 28 October 2026

Development branch: `feature/runtime-batches-and-review-layout`. The serving detached
checkout and real library were not changed. Test totals below overlap; do not add them
into a whole-suite total.

## Implemented and verified

- Three separate CBS Runtime profiles: Edda v0.2 turbo, Edda v0.2-large and Røst v3.
  Their language is Danish and their first supported operation is full-recording ASR.
  Existing Whisper defaults, saved rows/denials and frozen jobs are preserved.
- Finite pinned source inventories, verified local safetensors conversion using the locked
  CT2 stack, distinct immutable derived-cache identities and marker-last publication.
  Source hashes for large weights come from pinned publisher LFS metadata; those weights
  were not downloaded during this work.
- Exact frozen helper/catalog companions for isolated remote execution; source, request,
  audio, recipe and derived-artifact receipts validated on recovery without current weights,
  cache, registry or inference imports. History/reports retain only bounded validated evidence.
- New requests can freeze `retain-estimates`; valid low-score intervals survive with their
  raw review hints. Missing policy retains legacy filtering and exact request serialization.
  The independent admission slice must inject the policy at every fresh public boundary.
- Settings shows fixed-Danish model-language controls, leaves old Whisper language controls
  editable, and separates timing coverage for words and passages. The obsolete score cutoff
  is hidden for fresh jobs because review hints use their independent rank controls.

## Evidence

- Helper: **35 passed, 3 skipped** (two Windows symlink-privilege cases and the opt-in physical
  converter case in the normal run). The separate tiny generated Whisper→CT2 conversion
  passed with network blocked and no trained weights or inference. Wall time was affected by
  laptop/tool suspension; this is compatibility evidence, not a speed benchmark.
- Worker: Danish integration **23 passed**; combined runtime/timing/Auto batch **42 passed**;
  a broader affected ASR/remote set **361 passed, 1 skipped**. These sets overlap.
- Independent review: **35 helper tests passed, 3 skipped; 21 runtime tests passed**; separate
  `python -I` exact companion loading and historical tamper probes passed. No blocker remained
  in that reviewed scope.
- Profile/catalog subset: **93 passed, 4 deselected**. An earlier broader run reached old
  enqueue fixtures that lack the new caller-held inspection/admission lease. Those fixtures
  are being migrated explicitly; they are not a passing admission integration claim.
- Conversion provenance and legacy language/report set: **75 passed**; final expanded
  provenance file **24 passed**. Pure historical tests forbid cache, model and runtime access.
- Fixed-Danish Settings Chromium case: **1 passed**. Existing Settings simplicity cases:
  **3 passed**. Timing coverage/repair browser set: **5 passed**, plus one screenshot rerun.
  Screenshots were inspected. Tests use synthetic recordings and temporary libraries.
- Final combined Settings/Danish browser run: **12 passed** after formatting and integration.
  The explicit provider/enhancement API fixture migration subsequently passed **102 cases**;
  this resolves the older fixture failures above without bypassing the real source/admission
  pipeline. Full admission lifecycle/consent acceptance is still a separate verification stage.

## Remaining acceptance

Finish durable admission and all compatibility checks before activating this branch. New
large model preparation still requires explicit user consent. Real Edda/Røst inference,
GPU resource/performance measurements, Danish/English interview evaluation and CBS licence
clearance are not established by these tests. Hviske 5.3/6 and automatic second-pass alignment
remain explicitly deferred. No new provider allocation, private upload, paid API call,
shared-environment sync, backend restart, migration, merge or push was performed.

Architecture: [0084](decisions/0084-danish-whisper-runtime-models.md),
[0089](decisions/0089-valid-timing-estimates-and-coverage.md).
