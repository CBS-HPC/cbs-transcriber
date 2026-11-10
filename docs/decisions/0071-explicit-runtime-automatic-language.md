# 0071 — Explicit Automatic language for CBS Runtime transcription

Accepted 27 October 2026 for T84d. Extend ADRs 0069–0070 to an explicit full-ASR
CBS Runtime model choice. This does not change Processing defaults or enable Automatic
for existing-text alignment, bounded repair, generic follow-ups or other providers.

## One saved model, one explicit choice

Provider Settings → UCloud → Advanced settings lists saved CBS Runtime / WhisperX models
with a language control. Choices are Use Processing, Automatic (experimental), Danish and
English. Blank preserves the saved Processing fallback. Display recognized older aliases
canonically without rewriting their stored value; show an unsupported legacy value explicitly.

`PUT /api/providers/{id}/runtime-language` accepts only the choice and the exact 16-character
saved-provider version. Under one writer transaction, check that version and the existing
Runtime identity, then update only the raw row's language. Preserve unknown fields, name,
enablement, Protected approvals, credentials, defaults and frozen jobs. This operation neither
creates other models nor contacts UCloud. Conflicts refresh only that row, preserving drafts;
older backends return a restart message without claiming a save.

The connection form retains its own original profile snapshot/version. Refreshing a language
row after conflict, or retrying it successfully, cannot attach a new token to stale connection
or approval drafts. Advance that form baseline only from its matching original version after
a verified language-only mutation with unchanged non-language fields. Otherwise the generic
save must remain stale and refuse the overwrite, even if the per-model row can save normally.

Ordinary connection saves must preserve the latest saved language of their Runtime profile.
When connection backfill creates a different model from an Automatic source, give the new
model the existing blank fallback instead of opting it in. Preserve existing model rows.
Provider responses derive their displayed settings and concurrency token from the same raw row.

## Preparation and frozen execution

Only explicit Runtime full-ASR planning accepts `auto`. Freeze the requested choice in the
existing options/owner identity. Two compatible Auto recordings may share an allocation while
retaining different per-file decisions. Changing the profile affects only later requests.
Protected and enabled-model checks remain authoritative.

Requested alignment prepares the selected ASR model, coarse recognition model, and both pinned
Danish and English aligners. Speaker preparation stays separately requested and gated. Never
look up an `auto` model snapshot, infer language in a readiness check, or prepare/download
implicitly. Missing preparation still requires the existing explicit confirmation.

## Remote acceptance, output and carry

The remote controller accepts Auto only for explicit full transcription with empty source
passages, a supported multilingual model, literal stage flags and bounded recording duration.
Keep fixed-language legacy request shapes and option hashes unchanged. Before any output
classification, prove the accepted request's exact bytes so changing Auto to a fixed language
cannot bypass validation.

Use the frozen worker's pure validators. Read bounded source bytes, verify their manifest hash,
and compile those exact bytes into a separate module; do not import a selected bytecode cache
or reopen the path for execution. Recheck source identity and require the helper's compatible
keyword interface before new Auto admission. This needs no inference packages or weights.

Validate Auto ASR decisions against both current request/audio and accepted descriptors, plus
the frozen model pin. Validate requested alignment outcomes and usable numeric word bounds;
validate speaker turns before advertising complete or reusable output. Refuse undeclared stage
caches at new admission. Carried stages retain their original receipts and exact bytes.

Partial results can retain verified ASR and independent speakers despite missing or malformed
alignment. Empty ASR is reusable only after decision proof. The host repeats these semantic
checks before advertising carry or committing results. Complete checkpoints recover without
inference readiness, private input transfer or a new task/allocation; public ownership and
heartbeat controls may still be updated. Preserve final harvest after Stop and existing
receipt/cleanup rules.

## Other providers and limits

New OpenAI profiles, official OpenAI transcription transport (including a Custom profile at
the exact official host), and new native Transcriber specifications reject literal `auto`.
Their blank-language omission behavior stays unchanged. Native inherited templates are checked
after explicit overrides; owned-job recovery does not rebuild or rewrite its submission.
Genuine custom endpoints retain their explicit capabilities. Loading older settings remains
compatible.

The detector policy remains experimental and uncalibrated. It selects one reported recording
language, not mixed-language passage support. Unsupported/uncertain language retains ASR with
explicit unavailable timing. General Automatic defaults require broader operation coverage and
licensed evaluation. Verification here uses synthetic libraries, mocked transfers and Chromium;
live UCloud, model quality, Mac and NVivo acceptance remain separate.
