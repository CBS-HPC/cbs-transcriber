# 0126 — A smaller initial model selection

## Context

The full catalog makes an initial transcription choice difficult. Users requested one OpenAI,
one general UCloud Whisper, and one Danish UCloud model by default while keeping the complete
catalog available in Settings. Model quality and MIG fit vary by recording and have not been
established for all workloads.

## Decision

A new library starts with the configured OpenAI catalog default. The bundled catalog initially
checks `gpt-transcribe`, native UCloud `large-v3`, and Danish CBS Runtime `whisperx/edda-large`.
Other catalog models appear unchecked and may be enabled explicitly. Danish specialist names
end in `(Danish)`. The UCloud list is a compact grouped checklist; it contains no model-level
language or machine controls. Existing UCloud profile language remains stored and is retained
when saving the connection. Missing `default_enabled` in a custom catalog means enabled, preserving
legacy overrides. Model capability and readiness checks remain authoritative; the default does
not assert that a Runtime is prepared or that a model provides native word times or speakers.

Existing provider rows, their explicit or implicit enabled flags, credentials and approvals are
not changed by these defaults. Missing model rows on an existing partial connection retain the
old enabled-by-default behavior. A fresh library's bootstrap row carries a marker until its
first save, so it can be distinguished from an old partial connection; a brand-new connection
without existing rows also uses the catalog defaults. Previously frozen jobs and retries keep
their exact settings. Only a database that did not exist at initialization gets the new bootstrap
provider; legacy `Settings` defaults and migration of existing libraries remain unchanged. Settings
sends only intentionally changed models and their observed profile versions; a changed profile or
group membership rejects the transaction. Untouched duplicates keep independent flags. Existing
API clients without the optional delta fields keep their previous bulk-selection behavior.

## Consequences

The initial choices are convenience defaults, not quality rankings; Danish model evaluation,
CBS Runtime readiness and large recordings on MIG require separate acceptance. Users can
enable other models before new work. OpenAI's `gpt-transcribe` returns text without native word
timings or diarization; requested additions need the existing separate enhancement path.
