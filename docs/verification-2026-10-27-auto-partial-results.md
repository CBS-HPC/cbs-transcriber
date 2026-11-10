# Automatic ASR partial results — offline verification

27 October 2026. T84c / ADR 0070 / V124. Internal full-ASR production/recovery now preserves
explicit language/alignment outcomes. Public/model/remote Automatic admission and general
Processing defaults remain closed; this is not activation of Automatic throughout the app.

## Delivered behavior

- An unresolved language retains ASR text and real passage bounds, with exact untimed token
  placeholders linked to the canonical decision digest. `aligned` records stage execution,
  not complete timing coverage; not-requested alignment produces no artifact. Requested
  speaker detection is independent. Cached unbound/changed outcomes are refused.
- Valid zero-row ASR produces an empty result, preserving distinct no-speech/uncertain
  evidence without a fabricated passage. Fixed-language conversion stays unchanged.
- Host validation captures one source/metadata snapshot before old API settlement/cleanup.
  Repeated finalization callbacks reuse that snapshot; retries validate afresh. Complete
  cached results need no runtime/readiness check. New results preserve the reviewed active
  result, Unicode text, available replay intervals and immutable history.
- Partial/empty labels and actual capabilities persist. ASR pins come from captured evidence;
  other pins must come from used stages' frozen identities, otherwise remain unknown.
  Historical reads never substitute today's registry or require audio/runtime files.
- Reports distinguish configured language from the decoder's Automatic report, its outcome,
  unknown detector mapping and uncalibrated policy. Strict capture/export rejects mixed or
  contradictory origins and identifies missing outcome/model-pin evidence. Private fields
  stay outside metadata-only reports; T43 merge ancestry is preserved.
- Internal full-ASR options support does not admit unsupported public work. Transcribe and
  existing-source request models reject Auto before endpoint work; the follow-up builder
  also refuses it before runtime/network checks, enqueue or parent ASR, even with no extras.
  Fixed/omitted-language request bytes retain their recorded hashes. New Auto JSON readers
  check sizes before opening and cap reads against growth; local artifacts retain the 32 MiB
  boundary before parsing or cleanup.

## Evidence

Final combined unit/API run: **358 passed in 129.84 s**, no skips. The saved artifacts are
ignored `test-results/auto-language-host-final.xml` and `.txt`. The combined selection includes
Auto worker/pipeline/report/admission, fixed worker/source, history/report/merge-parent,
ordinary follow-up and Processing regressions. The three browser cases below are separate.

Focused runs passed before that combined run and overlap it:

- Worker: **159 passed**, 58.19 s; `auto-language-outcome-worker-final.*`.
- Host/source: **108 passed**, 59.59 s. This command reported to the agent's tool transcript
  only, with no saved log/JUnit: `uv run pytest tests/test_auto_language_pipeline.py tests/test_language_source_metadata.py tests/test_auto_language_boundary.py tests/test_whisperx_transcription.py tests/test_retranscription.py tests/test_enhancement.py -q`.
- Reports/history: **113 passed**, 30.85 s; `auto-language-reports-affected.*`.
- Public admission: **6 passed**, 5.98 s; `auto-host-admission.*`. Ordinary follow-up and
  Processing regressions: **18 passed**, 21.03 s; `auto-host-admission-regression.*`.
- Independent read-only review: **GO**, with **189 distinct cases** across its worker/report,
  admission, host and size-bound runs. Do not add those to the other overlapping counts.

**Three Chromium cases are verified:** the initial run passed both empty-result cases;
the replay case then passed its focused rerun in 15.30 s. The initial replay failure was a
test-only CSP-blocked string polling expression (the existing B43 lesson); a function predicate
fixed it without changing production CSP or playback. Evidence: `auto-language-browser.*`,
`auto-language-browser-replay.*`. The inspected screenshot `automatic-partial-result.png`
shows passage timing, explicit unavailable-word label, literal HTML-like transcript text and
replay without a current-word highlight. Browser fixtures use the real cached host finalizer,
not general Auto admission or actual ASR.

Review findings and lessons:

- Three red origin-conflict cases became green (B161); valid-looking decisions cannot
  contradict explicit requested-language metadata. The report suite's initial two empty
  fixture failures were corrected to use Store's valid empty payload, preserving the generic
  import normalizer's rejection of empty input.
- Four tiny oversized/growing-reader cases pass (B162), without allocating large files.
- Initial admission assertions expected validator text that the existing HTTP error handler
  deliberately redacts. Assertions now preserve that privacy behavior. Production guards
  remain enforced. Aligned/diarized word-derived bounds retain existing behavior; exact ASR
  replay bounds are required for unavailable word timing.
- Older synthetic full-ASR fixtures now provide their required alignment stage and invoke
  the mandatory validation callback. No production shortcut was added for incomplete mocks.

Whole-repository Ruff lint/format passes **302 Python files** at the final check; Git whitespace
checks pass.
No frontend source changed, so no frontend formatting/syntax rerun was needed.

## Limits

All tests use temporary libraries, synthetic audio/text, stub ASR/diarization and mocked
services. FFmpeg and Chromium handle generated media. No real language-quality benchmark,
model download/inference, paid request, private transcript access, live UCloud allocation or
upload, real-library migration, backend restart, main merge or push occurred.

Next: preparation for both aligners, strict Auto remote acceptance/carry, public opt-in/full
operation coverage and settings initialization, then licensed language-policy evaluation
before broad defaults. Bounded repair/alignment-only and mixed-language behavior remain open.
Live UCloud/Mac/NVivo acceptance and the final project audit are separate.
