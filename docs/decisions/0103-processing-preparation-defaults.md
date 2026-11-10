# 0103 — Prepare requested work by default; preserve saved and frozen policy

Accepted 30 October 2026 under the user's explicit request to enable both Processing
checkboxes by default: download missing local models and prepare UCloud models when needed.

Change only fresh/unset Processing settings. An explicitly stored false remains false, and
partial settings saves preserve either omitted flag. Reading settings, checking hardware and
saving preferences must not start downloads, preparation, uploads or jobs. Existing HF gated
speaker access and Protected-provider checks remain separate requirements.

Low-level LocalSettings/EnhancementOptions keep their legacy false defaults: old frozen job
documents missing a field must not acquire consent from a new global default. New explicit
requests freeze their effective saved policy in the existing request boundary.

Automatic placement never grants UCloud preparation. A retranscription request originally
set to Automatic may resolve to a stamped UCloud runtime whose specific model is missing;
that second readiness failure must not consume standing consent. Only originally explicit
UCloud placement can use it. This closes a reproduced routing edge without changing the
explicit UCloud workflow or frozen retry meaning.

Synthetic settings, save/Retry/routing and browser checks cover absent/partial settings,
all saved boolean combinations, frozen legacy options, the missing-model routing edge and
no network side effects on reads. No existing real-library preferences were rewritten.
