# Automatic language — gated full-ASR worker verification

27 October 2026. T84b / ADR 0069 / V123. This is a standalone-worker implementation,
not general Automatic support in the app. Processing, queue and remote admission still
require existing Danish/English choices; no app/default or frozen-job change was made.

## Behavior and boundaries

Full-ASR Automatic uses the loaded multilingual faster-whisper model with `language=None`.
Its completed `transcription.json` atomically retains segments and a separate validated
`language_decision`: requested/effective language, bounded observations, derived outcome,
experimental policy, exact WAV/request/model identity, engine version and full-ASR input
window. The detector's internal VAD window is explicitly unknown. The policy's 0.8 support
cutoff is uncalibrated; it is not a measured accuracy/confidence guarantee.

Supported Danish/English selects the matching tokenizer, aligner and cache through copied
runtime options. Frozen requested `auto` and request bytes stay unchanged. Uncertain,
unsupported and no-speech outcomes retain the ASR checkpoint and return a curated failure
before timing or speakers, including when old downstream files exist. No fake alignment
artifact or completed word-timing result is produced. Empty decoding alone is uncertain;
no-speech requires validated zero post-VAD duration too. English-only models and unsupported
Auto operations are refused before inference. Configured legacy cache behavior stays intact.

Completed ASR is reused without loading a detector, checking today's inference version or
substituting current model pins. An interruption before that completed checkpoint may
repeat ASR. A fixed parent explicitly refuses `language_decision` before its legacy fallback;
it cannot accept hidden Automatic metadata as old configured output.

## Verification

**176 distinct unit/API cases pass**, across focused runs and reruns. Overlapping results
are not added together:

- Worker run: **118 passed in 32.04 s** (75 Auto cases and 43 affected existing worker cases).
  A final stricter literal scope guard adds **3 passed in 3.86 s**; the new Auto file contains
  78 cases. Evidence: ignored `test-results/auto-language-worker-verified.*` and
  `auto-language-scope-final.*`.
- App/remote refusal and curated-error boundaries: **11 passed in 3.14 s**, covering da/en
  settings, HTTP 422 without settings/jobs mutation, unchanged remote refusal, all five
  curated errors without private upstream fields, and parent refusal before input/runtime
  access. Independent review reran the Auto/boundary suites: **86 passed in 25.05 s** before
  the final three guard cases; those overlap the preceding counts.
- Parent pipeline and Processing regressions: **44 passed in 16.18 s**. Evidence: ignored
  `test-results/auto-language-parent-regression.*`.
- Independent review: **GO** for this hidden stage. It reproduced a valid-decision cache
  accepting empty text and identified first-call request-context misbinding. Ten cached-row
  and twelve new/cached request-context regressions were **22 red → 22 green** after Auto-only
  semantic checks. Evidence: ignored `auto-language-cache-boundary-{red,green}.*`; SPEC B160.
- Repository-wide Ruff lint and format pass (**295 Python files** at verification).
  This stage has no frontend change, so browser checks were not repeated.

The named V123 checks include `test_auto_full_asr_binds_real_input_and_routes_only_copied_effective_options`,
`test_auto_alignment_cache_uses_resolved_language_across_jobs`,
`test_unresolved_decision_preserves_asr_without_fake_alignment_or_speakers`,
`test_completed_auto_asr_is_not_repeated_after_alignment_interruption`,
`test_cached_auto_requires_usable_normalized_bounded_rows` and
`test_auto_request_binding_checks_frozen_language_model_and_operation`.
The runnable suites are `tests/test_worker_auto_language.py` and
`tests/test_auto_language_boundary.py`; existing worker, source and Processing suites provide
the compatibility checks described above.

## Remaining work

App integration must validate Auto decisions at acceptance/carry, define durable partial
results and capabilities, prepare both aligners, extend strict remote contracts and publish
truthful source/history/report metadata. Bounded repairs and alignment-only work need their
own language scope; general defaults follow those paths and licensed language evaluation.
Mixed-language alignment is not delivered by a recording-wide decision.

All evidence uses synthetic PCM, real local decoding/mapping, stub ASR/alignment, temporary
libraries and mocked boundaries. No model weights, actual inference, paid requests, private
transcript bodies, UCloud allocations/uploads, backend restart or real-library migration.
The concurrent T43 merge work and intentional document deletions remain separate.
