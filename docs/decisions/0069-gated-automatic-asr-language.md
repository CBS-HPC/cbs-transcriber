# 0069 — Automatic language in full-ASR worker checkpoints

Accepted 27 October 2026 for T84b, following ADR 0067 and the automatic-language review.
This is a gated standalone-worker stage. App settings/defaults, queue admission, remote
validation, passage repairs and research-report publication keep their existing behavior.
Do not expose general Automatic before those integration paths are implemented and tested.

For a new full-ASR request with `language: auto`, use the existing pinned multilingual
faster-whisper model and its normal VAD transcription with `language=None`. Require the
loaded model's multilingual flag; an English-only model silently forces English and cannot
provide this detection evidence. Explicitly retain one detection segment and the engine's
0.5 early-stop threshold. That threshold does not make the engine abstain from guessing.

Atomically store normalized segments and a separate `language_decision` in the existing
`transcription.json`. Keep ADR 0067's configured-only `language_evidence` unchanged. Bind
the decision to prepared WAV/request hashes, actual full-ASR input window, model choice/pin,
observed/unknown engine version and an explicit versioned decision policy. The full window
describes the processing input, not the audio inspected by the language detector. The engine
detects on VAD-concatenated features without returning their source map, so record
`detector_window: null`. Never claim that it examined prepared-audio seconds 0–30 or proved
the language of every passage.

The initial policy accepts only Danish/English with valid probability at least 0.8, usable
ASR rows and noncontradictory VAD metadata. This is a conservative, **uncalibrated prototype
policy**, not an accuracy/confidence guarantee. Record unsupported and uncertain outcomes
without substituting Danish or English. `no_speech` requires valid zero post-VAD duration
and no retained ASR rows; empty decoding alone is uncertain. Malformed upstream metadata
must not become a confident decision. Preserve all usable ASR text regardless of the outcome.

Only a supported decision may choose recognition language, Punkt, pinned alignment model
and cache identity. Use copied runtime options, retaining the requested `auto` and frozen
request bytes. An unresolved decision returns a curated language error before alignment or
diarization; retain the ASR checkpoint, create no fake timing output and claim no completed
word-timing result. Auto passage re-transcription and alignment-only work are refused before
inference until their own bounded-language contracts exist.

An Automatic cached stage must have a valid matching decision. Validate it without loading
models, checking today's inference version or replacing captured model pins. A legacy stage
cannot acquire a decision by retrospective detection or silently be reused as Automatic.
Reject an Automatic stage under a fixed-language request too. Existing fixed-language
requests and legacy checkpoint bytes remain unchanged.
Validate the parsed frozen language/model/full-ASR context and usable cached rows as well
as their identity hashes/count: a hash-bound empty-text row is not usable ASR evidence.

The durability boundary is the completed atomic ASR checkpoint. Interruption after that
checkpoint or during alignment must not redetect or retranscribe. An interruption after
the engine returns info but before the ASR generator completes has no committed ASR
checkpoint and may repeat that work. This stage does not promise a separately durable
pre-ASR language decision; adding one would require its own carry/receipt protocol.

General rollout still needs parent partial-result semantics, source/history/report
allowlists, settings initialization, native/API compatibility, model preparation and remote
validation/carry, plus alignment-only and bounded repairs. Evaluate language policy on small
licensed Danish/English, silence/noise and mixed-language cases before broad default use.
Tests here use synthetic WAVs and mocked ASR; no detector accuracy, timing or GPU claim.
