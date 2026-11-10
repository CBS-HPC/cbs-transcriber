# 0070 — Preserve Automatic ASR with explicit alignment outcomes

Accepted 27 October 2026 for T84c. Extend ADR 0069's full-ASR checkpoint into verified host
results and research metadata before enabling public or remote Automatic admission.
This supersedes ADR 0069's orchestration rule that unresolved language stops all later
processing. The direct ASR helper still returns its curated language codes.

## Partial results and stages

When Automatic cannot select a supported aligner, retain the ASR text and its real passage
bounds. If alignment was requested, write `alignment.json` with every ASR-derived passage ID
and token in order, but literal null `start`, `end` and `score` fields. The artifact includes
`language_outcome` with exact keys `schema`, `state` and `decision_sha256`. Schema is 1;
state is `unavailable_language`; the digest binds canonical JSON of the validated decision.
Missing, false, zero or additional timing fields cannot stand in for null timing.

Supported Automatic alignment writes the same binding with state `aligned`. This means the
aligner ran, not that every word has usable timing. Validate existing artifacts; never stamp
an old unbound alignment as if it belonged to the new decision. When alignment was not
requested, create no artifact and record a derived `not_requested` outcome. Continue requested
speaker detection independently of language; retain its existing failure/checkpoint behavior.

Save validated empty ASR as an empty result with its actual `no_speech` or `uncertain`
decision, without inventing an empty passage or interval. Empty decoding alone still cannot
establish no speech. Only this validated Automatic path permits empty ASR conversion; retain
the fixed-language converter's existing behavior.

## Acceptance and provenance

The host validates the exact frozen options, request/audio hashes, ASR rows/model pin,
decision and requested alignment artifact before saving a result or permitting cleanup.
The full-ASR merge callback captures one validated local result/metadata snapshot; repeated
callbacks during that finalization use it, and later retries validate afresh. Complete cached
stages finalize without an inference runtime or readiness check. Preserve existing reviewed
results and active selection; append the new result/version through ordinary producer rules.

Only an `aligned` outcome offers word capability. Otherwise expose passage timing and, when
requested, speakers, with a durable partial/empty label and source outcome. Preserve actual
coverage separately. The ASR pin comes from the validated decision. Record non-ASR pins only
when captured in the frozen job identity for stages actually requested/used; unknown local
aligner/speaker pins stay unknown instead of using today's registry during recovery.

Historical validation is pure: no audio files, installed runtime or current model registry.
Capture and report use strict paired decision/outcome allowlists and reject conflicting
requested-origin metadata. Mixed configured receipts and Automatic decisions publish neither
language block. Reports distinguish the decoder's reported language from aligner selection,
explain unknown VAD detector mapping and the experimental uncalibrated policy, and show missing
outcomes/pins as provenance gaps. Keep private IDs, paths, text and credentials out of metadata
reports. Preserve the independently verified merge ancestry from ADR 0068.

## Admission boundary

The frozen full-ASR options reader may accept `auto` only with explicit full transcription.
LocalSettings/Processing, model planning and remote validators retain their existing choices.
Public existing-transcript enhancement and follow-up bodies explicitly refuse Auto before
endpoint work. The follow-up builder also refuses it before checking runtime/readiness or
queuing any parent ASR, even when no extra features are needed. Otherwise widening the shared
reader could trigger a paid/provider transcription before an unsupported follow-up fails.

No new filenames, database schema, implicit model download or changed legacy option defaults.
Public/profile/remote Automatic admission, both-aligner preparation, bounded repairs and
general defaults remain later stages. Verification is synthetic/mock only; no live or language
accuracy claim.
