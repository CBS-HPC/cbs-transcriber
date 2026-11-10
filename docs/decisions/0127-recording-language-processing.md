# 0127 — Recording language for processing

Status: implementation under verification. This extends ADR 0125 to new enhancement and
passage operations; it does not upgrade frozen jobs or assert language detection.

The recording override takes precedence over its project default; when both are unset,
retain the existing saved model or Local Processing language. The visible spoken-language
controls are the recording and project controls. Hiding the older OpenAI and Local Processing
controls does not erase their saved values. Partial Processing updates preserve every omitted
field inside the same database write transaction; provider updates preserve an omitted language
for the same saved provider/model identity. Existing clients may still send an explicit value.
Provider-specific protocol parameter names and model capability settings stay separate.

New full enhancements, passage timing, passage retranscriptions, selected-passage repairs and
opt-in edit realignment resolve the same preference. An explicit unsupported language is
refused before a job is accepted for language-dependent work; current WhisperX timing/repair supports Danish and English,
with Danish-only model restrictions retained. A free-text edit is still committed if automatic
realignment cannot be queued: return a visible warning and retain any previous work. Do not
present a stored preference as a detector result or assume every model accepts every catalog
language. New transcription and batch follow-ups continue to freeze their selected language.
Speaker-only diarization does not use an ASR or alignment language. It accepts other selected
languages and freezes the preference and revision separately; its legacy options-language field
is retained as an unused compatibility setting and is not evidence of detected language.

New operations freeze the actual language and preference scope. Their project and recording
language revisions are checked in the same SQLite transaction that accepts the work, after
any asynchronous runtime/routing probe. A changed preference produces a conflict instead of
silently queueing a stale choice. Queue locks never cover network probes. Existing queued and
running jobs, retries, their captured artifacts and legacy receipt bytes retain their original
options. For direct jobs, a present language scope is retained in job metadata; a UCloud
passage-repair identity binds it without changing identities of older jobs that lack it.

Verification uses temporary synthetic libraries and mocked runtime/network paths. Real
transcription, word-timing quality, automatic-language behavior and other devices remain
separate acceptance work.
