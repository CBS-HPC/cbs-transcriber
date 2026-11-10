# Background original-input admission — 29 October 2026

This stage completes the previously inert schema-13 original inspector and connects whole-file
transcription/enhancement to it. The serving `c3c94e6`/schema-12 checkout was not restarted or
migrated. All implementation checks below use temporary libraries, synthetic originals and
mocked providers. The separately documented four-output native recovery is not part of this
implementation test evidence.

## Implemented behavior

- HTTP acceptance freezes metadata, identities/options/features, consent and exact enhancement
  history atomically. It performs no source hashing/decoding, runtime probe or external request.
  Jobs remain intents on the same IDs until background preflight materializes executable params.
- Two source-inspection workers and two admission workers run alongside media/API lanes. A short
  recording can inspect, materialize and save mocked ASR while another inspector is blocked;
  a slow optional-runtime probe does not block unrelated ready API admission. Shutdown drains
  owned work before releasing the library lease.
- Source readiness is independent of browser playback. New original-v1 whole-file jobs use
  the immutable managed original while playback prepares or has failed. Legacy jobs, saved
  durations and bounded repair behavior retain their separate contracts.
- Native selections wait for every source/preflight outcome before one allocation. Valid
  members may proceed together; excluded outcomes are durable/resolved and cannot quietly
  ordinary-Retry into another original allocation. SQL attachment failure rolls back owners
  and is settled as a visible local failure without killing the worker lane.
- Cancel/Retry use exact phase/epoch/generation authority. Preparation consent is explicit:
  a fingerprint-bound preview creates idempotent successors from exact frozen accepted scope,
  preserving predecessors, historical input and settings. Successful/materialized siblings do
  not rerun. Only confirmed prepare_runtime changes; ordinary Retry cannot acquire consent.
- Protected approvals and provider identity are checked independently for parents/follow-ups.
  Visibility changes alone do not cancel accepted work. Admission receipts remain immutable
  through later job checkpoints. Every fresh WhisperX boundary freezes retain-estimates;
  old missing-policy requests keep exact legacy behavior.
- Completed source hashes anchor before cancellation/shutdown even at the final validation
  boundary. Malformed or non-object job JSON is quarantined without resetting raw evidence,
  hiding ownership, or blocking healthy recordings. Ambiguous active work still blocks a new
  request for that same recording.
- Import publishes a closed original, recording/notes/collection metadata and media job in
  one transaction. Postcommit notification failure cannot delete the committed source.

## Evidence and limits

- Final owner aggregate: **138 passed, 1 skipped** across admission/API/consent/workers,
  original inspection, physical audio input and source recovery. The skip is the existing
  Windows account restriction on creating a managed-original symlink.
- Separate HTTP contract delta: **1 passed**, retaining duplicate/replacement/stale conflict
  HTTP409 and Protected HTTP403 with unchanged jobs and no expensive preparation.
- Independent review: **10 final targeted tests passed**, plus synthetic probes for invalid
  JSON roots, completed-hash Stop/recovery, mixed native outcomes, exact/idempotent consent,
  frozen settings, owner rollback and worker drain. No remaining blocker in this reviewed scope.
- Preparation Activity/consent browser sets and pending-control regressions passed. They test
  captured tokens, stale-action refusal, explicit consent/cancellation, invalid evidence,
  continuation receipts and renamed captions without a duplicate pending action.
- Provider/language/enhancement compatibility set: **102 passed** after explicit real-WAV
  admission fixture migration. Native batch/cleanup compatibility: **28 passed**; standalone
  native compatibility: **42 passed**. Test totals overlap broader checks.
- Global Ruff check, Python formatting and frontend formatting pass after shared-source cleanup.

The first broader run also exposed older tests that assumed synchronous preparation, plus
the HTTP-status regression fixed above. Their migration preserves execution/policy/recovery
assertions, uses the actual inspector/admission stages, and seeds genuine historical contracts
for legacy retries. It never auto-drains the generic client or rewrites new frozen receipts.
The complete broad integration result is tracked separately; this document does not claim
a whole-suite pass, real provider execution or production activation.

Architecture: [0082](decisions/0082-durable-original-inspection.md),
[0085](decisions/0085-durable-input-admission.md),
[0089](decisions/0089-valid-timing-estimates-and-coverage.md). Material regression lessons are
SPEC B217–B221/B225. Automatic second-pass alignment remains deferred.
