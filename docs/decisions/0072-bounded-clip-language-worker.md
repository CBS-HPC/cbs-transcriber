# 0072 — Bounded prepared-input language for local alignment

Accepted 27 October 2026 for the T84e standalone worker stage. This is an internal
experimental operation, not a new Processing choice or public repair option. It extends
the separate full-ASR work in ADRs 0069–0071 without treating an ASR receipt as clip proof.

## Narrow operation

An explicit top-level `operation: "bounded_alignment"` marker identifies this worker request.
Require local execution, requested language `auto`, concrete CPU/CUDA, cached models only,
alignment requested, and transcription, re-transcription, speakers and whole-recording mode
explicitly false. Require exactly one nonempty text unit of at most 120 words/1,200 characters,
with known bounds covering its complete prepared input: start zero and end the requested
duration, at most 60 seconds. The actual mono 16 kHz PCM WAV must agree within one sample.
Validate frozen request context and actual header/frame duration before decoding or importing
an inference runtime. Reject larger supplied input rather than decoding it and checking later.

This proves a bounded prepared input, not its original-recording provenance. The current host
can also create temporary joined units for ADR 0058 search gaps. Their selection, mapping,
source-version proof and original timing must be enforced at the future host boundary. Never
stamp a search gap as the timing of its source passages.

## Detection and durable decision

Use the already pinned coarse multilingual faster-whisper model. Preserve the tokenizer-file
guard and require the loaded model's multilingual flag plus its expected 16 kHz/30-second
extractor geometry. Verify the complete source-bound float mapping before taking a view.
Call `detect_language` directly on the first actual contiguous `min(frames, 480000)` samples,
with VAD disabled and one detection segment. This encodes language features without ASR text
generation. Unlike full ASR, its actual sampled window is known; do not concatenate VAD
audio or claim that all 60 seconds were inspected.

Atomically write `alignment_language.json` before tokenizer/aligner work. Bind exact prepared
WAV and frozen request SHA-256, actual input and detector windows in `prepared_audio` time,
captured detector model repository/revision and observed/unknown engine version. The request
hash also binds the exact text, unit ID and bounds. Retain requested `auto`; the separate
reported language/probability is an experimental candidate. Use a versioned, uncalibrated
0.8 abstention policy, supporting only Danish and English. Invalid/low observations are
uncertain; another confidently reported language is unsupported. Direct detection supplies
no speech-presence evidence, so this contract has no `no_speech` outcome.

Record `text_compatibility: "not_assessed"`. Audio language does not establish that translated
or rewritten text matches it. General public alignment needs an exact compatible source or
a bounded text/speech compatibility assessment before this restriction can be lifted.

## Alignment, cache and retries

Only a supported decision selects the tokenizer and pinned aligner through copied runtime
options; frozen request bytes stay `auto`. Reuse the existing effective-language/device
alignment cache: pins are immutable within that worker process. Do not add a speculative
resident detector cache or claim loading/performance measurements.

Successful execution means `alignment_attempted`, not verified text/speech agreement or
complete word coverage. Bind that outcome to the canonical clip-decision digest in
`alignment.json`. A pure reader validates exact source unit IDs/tokens, interval bounds and
outcome before reuse. Each present timing component must be legal even when another endpoint
or fit score is unknown; a later host cleaner cannot stand in for this standalone boundary.
Retain legitimate unknown/partial maps. It remains separate from full-ASR decisions and validators.

An uncertain/unsupported decision returns a clip-specific curated code and preserves the
checkpoint without an alignment artifact or invented timing. A retry reuses the decision
without loading/detecting again. The one short clip uses no inner alignment-window cache:
an interruption before the atomic final alignment may repeat alignment, never detection.
Interruption before decision publication can repeat detection; no completed decision exists then.
New decisions refuse pre-existing unbound final/window/ASR artifacts; never retrospectively
stamp an old alignment. Present malformed or mismatched
receipts fail closed. A complete validated clip result returns from `execute` before inference
imports. The CLI still checks its installed runtime in `process_directory`; this stage does
not claim runtime-free CLI execution.

## Admission and evidence boundary

EnhancementOptions, public request/follow-up validation, Processing defaults, host job builders
and UCloud controller/carry remain closed for Auto existing-text work. The new marker is not
an accepted remote request shape. No database schema or legacy option serialization changes.
Old fixed-language and full-ASR checkpoints remain readable with their exact previous bytes.

Use synthetic clips/detector responses to verify scope before decode, exact sampling, no ASR,
unknown language, restart after decision and during alignment, warm Danish/English transitions,
tamper/refused unbound artifacts, bounded parsing and unchanged existing admission. No inference,
weights, private recordings or remote jobs. Language quality, text compatibility, live execution
and public host/provenance integration are subsequent work.
