# T84 and multi-recording enhancement: next offline sequence

Read-only review of `99cb57f`, 30 October 2026. This is a proposal, not a behavior change.
Prefer fixed-language bulk enhancement first. Reuse the existing shared Runtime; keep general
Automatic language closed until its source/compatibility contract is accepted.

## Current inventory

| Area | Implemented at this revision | Remaining gate |
| --- | --- | --- |
| Full-ASR language | Configured receipts; explicit experimental Auto for supported saved CBS Runtime Whisper models; durable decision, partial/empty outcomes, both-aligner preparation, strict remote validation and carry. | General defaults are not Auto. Fixed-Danish Edda/Røst remain fixed. This is one language decision per input, not code-switching support. |
| Existing-text/repair language | Fixed `da`/`en`; exact source/version/clip receipts; bounded local Auto worker primitive (<=60 s, <=120 words, <=1,200 characters), separate decision/outcome and warm effective-language cache. | Processing, public enhancement/follow-up/repair admission and remote existing-text Auto remain closed. Clip evidence explicitly says text compatibility is not assessed. |
| Whole-result UCloud enhancement | `freeze`/`freeze_profile` create a batch plan. Compatibility includes project, connection, operation, options, runtime/model/code/compute identity; per-file source IDs are separate. Independent results, Stop/recovery/cleanup and finite resident cohorts exist. | No selected-recordings action/API for adding features to existing results. Current project bulk UI transcribes and may add frozen follow-ups. |
| Post-ASR follow-ups | Their own options/connection freeze; result plus exact-source follow-up commit atomically; compatible UCloud follow-ups can share enhancement owners. | Auto follow-ups are explicitly refused; do not infer permission or language from a later Processing setting. |
| Selected timing/repairs | Dedicated bounded-bundle MIG singleton and revision/current-map guarded Apply. | `realign`/`timing_units` do not join the ordinary shared enhancement pool. Keep this boundary. |

Code anchors: `enhancement.py:91,115,496`; `features.py:32`; `enhancement_api.py:203,232`;
`runtime_store.py:494,566`; `runtime_batch_remote.py:347,1076`;
`enhancement_worker.py:887`; `app.py:398,532,2328`.
The 27 October language proposal and Runtime design document describe earlier baselines;
their old “pending” statements are not the current implementation inventory. ADRs0067–0072,
0065 and0094 describe the delivered stages more accurately.

## Smallest implementation sequence

1. **Settle the bulk allocation promise with an offline test first.** Existing compatible
   ready jobs share an open owner, but this is not a guaranteed one-owner-per-selection
   barrier: `input_admission._cohort` returns one non-native job (`:730`); owners have a
   100-member cap, 60-second idle grace and 120-second late cutoff. Test a slow last inspection,
   sealed/draining owner, existing owner with insufficient seats, failure/cancel and restart.
   If “one allocation for the accepted compatible selection” is required, add a fresh versioned
   preparation-group barrier/reservation: resolve every source outcome, choose one owner with
   enough seats, then attach the healthy set in one writer transaction. Do not repurpose the
   native-only group or extend idle timers as a substitute. Otherwise describe existing
   best-effort shared admission honestly in the UI.

2. **Add one fixed-language whole-result bulk entry point.** Use one same-project bounded
   request containing each recording's exact `result_id`/revision and common options. Reuse
   `snapshot_routing`/`choice`/`accept`, original inspection, current Protected checks and
   `freeze_profile`; accept durable intents atomically rather than looping single-file HTTP
   calls. Freeze selection and form generation before awaits. Reject duplicates/foreign or
   stale results, retain per-file preparation failures, and use existing request/owner Activity
   groups. Preserve reviewed results, named voices, Working pins and independent outputs.
   Gate: two compatible files -> one POST when ready; incompatible options/projects -> separate
   owners; partial local commits, Stop scope, lost replies, recovery and retained cleanup.
   Add no scheduler, queue service, model cache or remote API.

3. **Advance T84e only through an internal host-binding slice.** Construct a bounded unit
   from the exact frozen source and original/preparation receipt, bind the worker request,
   WAV/sample window, decision and outcome back to that unit, and verify offset-once/null
   mapping and stale Apply. Scoped-original global PCM/frame claims remain null.
   Keep requested `auto` immutable; effective language belongs in a
   verified receipt and copied runtime options. First exercise detector/aligner adapters with
   mocks, including restart, source changes and Danish/English cache transitions. No public
   Auto flag or upload authority follows from this internal receipt.

4. **Resolve compatibility before public Auto alignment/follow-ups.** A recording-wide ASR
   decision is not proof for a short repair; matching audio language is not proof that imported,
   translated or edited text matches the speech. Choose an accepted exact compatible-source
   or bounded text/speech assessment contract before publishing timings. Preserve literal
   text, original scope and unselected dictionaries; uncertain/unsupported adds no guessed
   timing. Direct clip detection provides no no-speech evidence. Do not load a detector for
   diarization-only work. Mixed-language and translated-text cases are explicit counterexamples,
   not an implied supported feature.

5. **Only then extend remote/carry/defaults.** Introduce a fresh operation/protocol and
   captured validator/stage allowlists for Auto existing-text units; do not weaken the current
   full-ASR-only remote predicate or change old owner identities. Bind per-unit language files
   to accepted inputs and reuse decisions/checkpoints without redetection or ASR replay.
   Readiness must cover the actual language-policy/dependency set (today Router cloud caching
   assumes one requested language and keys only project/diarization). Effective languages may
   vary between members without changing frozen owner compatibility; cache reuse must include
   effective language and pins. Change Processing defaults last, distinguishing new
   initialization from old missing/saved `da`/`en`. Keep OpenAI/native omission semantics and
   existing fixed-Danish profile choices; never send their literal `auto` or backfill history.

## Verification in this review

All work used temporary synthetic libraries, fake media/worker output and mocked transfers.
The real host finalizer/coordinator/Store smoke proves two existing-text enhancements with
independent source versions share one allocation, save separate results and retain originals.
It also proves different requested languages separate owners and an unmarked legacy job stays
singleton. Together with current public/remote Auto-gate tests: **18 passed in16.83 s**.
Six existing late-admission, shutdown/recovery, Stop/harvest and finite-cohort regressions:
**6 passed in30.27 s**. Evidence and the runnable smoke are under
`test-results/stretch-language-batching/` in this checkout. No production or official test
source was changed.

These tests establish current protocol/lifecycle behavior, not language accuracy, calibration,
model load speed, GPU/MIG memory/reuse, real UCloud/Linux locks, Mac behavior or cost. Prepare
an inert representative Danish/English/silence/noise/translation/code-switching acceptance
matrix now; actual inference/provider qualification remains separately authorized work.
