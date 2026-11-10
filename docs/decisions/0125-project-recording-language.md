# 0125 — Project and recording language preferences

Status: implementation under verification. This decision does not claim live provider or
model acceptance.

ADR 0127 subsequently extends the same preference to new timing, repair and enhancement
operations, while preserving the saved legacy fallback for unset choices.

New projects may save a default spoken-language code. A recording may save its own override;
an unset override inherits the project, and an unset project keeps existing model/processing
settings. Metadata stores canonical IANA base language subtags, not display names, inferred
language or region/script variants. The packaged registry supplies choices independently of
provider support. An unset choice is distinct from automatic detection.

Schema 17 adds nullable project and recording choices with independent revisions. User edits
compare those revisions; default values are null and do not change existing behavior. Source
project snapshots and portable backup/restore preserve these fields. Upgrade older portable
libraries with null preferences and zero revisions while retaining their original, hashed
source observations; schema-17 restores preserve the exact values. No saved provider profile,
credential, reviewed transcript or existing job is rewritten.

A fresh transcription freezes the effective per-recording choice and its source scope with the
accepted request. Compare project and recording revisions inside the acceptance transaction
after any readiness probe; a concurrent edit refuses the whole acceptance. Existing frozen jobs,
retries and remote allocations retain their original settings. Native jobs share an allocation
only if their frozen model, language and other allocation parameters match; no file inherits a
different member's setting. A configured choice is never called detected-language evidence.

An explicit CBS Runtime fixed choice is currently Danish or English; Danish specialist models
require Danish. OpenAI language hints use two-letter codes. Reject known incompatible choices
before accepting work. The registry is not a model support list; other provider and model
combinations remain unverified and may fail during preflight or at the provider. The
native Parakeet wrapper's handling of a forced hint is not established; refuse an explicit
project/recording choice for that model until verified. Blank native model settings still mean
the native app/template default, not a guaranteed automatic detector. Enhancement follow-ups
that need local timing or speakers accept only their supported languages. ADR 0127 governs
the later integration into existing-result timing, edit realignment and passage operations.

Tests must cover migration and pre-upgrade backup, empty defaults, native/Runtime compatibility,
per-file mixed-language batching, stale preference edits, frozen retry, original acceptance,
provider approval, exact producer evidence, portable capture/restore across schema 16 and 17,
API/browser races, and narrow layouts. No inference weights, live allocations or private media
are necessary for these checks; real provider execution is separate.
