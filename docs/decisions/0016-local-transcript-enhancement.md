# 0016 — Optional local transcript enhancement

Accepted: 20 October 2026. Extends independent saved results and the durable local queue.
The initial short-window restriction below is extended by [0017](0017-bounded-alignment-windows.md).
The separate-environment default is superseded by [0025](0025-standard-whisperx-installation.md).

Use a separate user-installed Python environment with WhisperX 3.8.6, rather than adding
Torch/WhisperX to the app environment. The bundled worker accepts JSON and a mono 16 kHz
WAV prepared by the app's FFmpeg. CPU is the default; CUDA is optional. Alignment and speaker
detection can run independently. No speech recognition model replaces reviewed text.

Freeze the selected result and options when enqueuing. One enhancement worker runs alongside
the existing media/API workers. Keep completed stage artifacts for explicit retry and append
one independent result through the existing job-ID idempotence constraint. Do not select it
over the currently reviewed result. Cancellation stops the child process tree; no token appears
in command arguments, job parameters or subprocess logs. HF credentials share the existing
Hugging Face credential scope. Pass them only through the transient child environment.

Use pinned HF snapshots for Danish/English alignment and pyannote Community-1. Models and
tokenizer data must already be cached by default. Downloading missing weights needs explicit
consent on each run; disable HF/pyannote telemetry. This run's tests install no inference
packages and download no weights. Runtime checks inspect installed versions without inference.

WhisperX requires audio windows and interpolates missing timing by default. Supply only actual
passage intervals (maximum 60 seconds), disable interpolation, and retain missing/low-confidence
word timing as missing. For short untimed recordings, one combined alignment searches the actual
full audio interval and maps the words back to the existing passages. Long untimed text needs
a later coarse-window stage and is rejected explicitly in this first increment. Do not pretend
that proportional timing or a repeated phrase's first occurrence establishes alignment.

Speaker attribution uses actual overlap. With reliable word timing, split only at word boundaries
and retain every character in order. Without word timing, retain the passage and mark mixed or
unknown attribution instead of inventing textual speaker boundaries. Enhanced results have their
own restoration baseline; the source result retains all earlier originals, edits and timings.

Design review before implementation:

- BLOCK resolved: app.py retry previously treated every non-transcription job as media and would
  reset recording status. Give enhancement its own branch and regression test.
- HARDEN: external alignment can omit words or return invalid intervals. Validate outputs against
  frozen source text/windows; reject out-of-bounds data and preserve unaligned text.
- HARDEN: default interpolation fabricates missing word intervals. Explicit `interpolate_method`
  must be `ignore`; synthetic engine tests assert it. Pin the supported runtime version.
- HARDEN: source edits during a run must not alter its input or be overwritten. Freeze inputs and
  test edits plus active-result retention, cancellation/retry and result isolation.
- NOTE: real runtime accuracy, GPU behavior, memory and gated-model acceptance remain opt-in
  manual verification. These are not established by mocked inference.

Gate: GO for the bounded local increment with V31–V33 and the checks above. Long untimed
alignment and UCloud enhancement remain unfinished TODO work, not claimed supported paths.

Sources: [WhisperX 3.8.6 alignment](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/alignment.py),
[interpolation utility](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/utils.py),
[runtime dependencies](https://github.com/m-bain/whisperX/blob/v3.8.6/pyproject.toml),
[Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
