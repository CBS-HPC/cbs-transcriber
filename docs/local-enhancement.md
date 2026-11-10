# Local transcript enhancement

Choose a saved result, then **Add word timing / speakers…** beside the result selectors. Select word
alignment, speaker detection, or both, and the processing location. The app freezes that
result's current text and creates a separate result. It keeps the reviewed result selected.
Local processing sends no audio or transcript text to an API provider, including for Protected projects.

Passages flagged with missing word timing have a compact **Add word timing** action. It uses the
recording's language, or its project default, with saved local settings as the legacy fallback,
and cached models. The count warning also offers repair of
all eligible missing passages. Only actual passage audio intervals are decoded; untimed or overly
large passages use full-result enhancement. Other passages and speaker names stay unchanged.
**Review repair** selects the new combined result after completion; the source is kept.
**Review enhanced result** also appears beside the result controls when an enhancement of the
selected source is ready. Completion during editing waits for the draft to settle; it does not
silently change the reviewed transcript or its playback timings.

The passage worker keeps one alignment model warm for five idle minutes. Cancelling, changing
runtime/language/device, failure or app shutdown releases it. First use can still be slow. Whole-
result/UCloud enhancement keeps its existing execution path. New requests use the saved
Processing download preference; completed/frozen retries retain their original permission.

## Standard installation

WhisperX 3.8.6 and CPU PyTorch are standard dependencies. Install or repair them with:

```powershell
uv sync --locked
```

The app selects its own Python environment by default. No separate environment is needed.
Package installation can take several minutes and downloads substantial libraries, but no
model weights. **Check runtime & GPU** checks installed versions and CUDA access in the selected
interpreter without inference or model downloads. CPU-only installations report no accessible
CUDA device. It does not validate model access/cache or inference quality.

New local jobs and retries needing processing fail before queueing when the runtime is not ready.
Legacy jobs check before preparing audio. Complete checkpoints can still be finalized without
WhisperX. A retry retains its original Python path and options; start a new enhancement if you
want to use a different runtime. Source results are preserved.

CPU is the default. Advanced users may select a separate CUDA runtime with compatible Torch
packages and an NVIDIA driver, following the
[pinned WhisperX dependencies](https://github.com/m-bain/whisperX/blob/v3.8.6/pyproject.toml).
The project lock installs CPU Torch; it does not change GPU drivers or configure CUDA.
Runtime settings and recording/project language preferences persist in the central SQLite library.

A small [Danish CPU benchmark](cpu-alignment-benchmark.md) observed a 7.4-second warm alignment
for a 6.84-second clip. First-use startup/setup was much slower; this does not predict speaker
detection or long-recording performance.

## Models, downloads and credentials

**Settings → Local Processing → Download missing local models when a job needs them** is enabled
for fresh/unset preferences; saved opt-outs stay off. It permits pinned model/tokenizer downloads
for new processing requests, not audio uploads. Installation, startup, hardware checks and
opening/saving Settings do not download model weights. Frozen retries retain their original
permission. First-time preparation may take time and disk space. Review each linked model's terms:

| Stage | Pinned model | Revision |
| --- | --- | --- |
| Long-text window discovery | [Systran multilingual small](https://huggingface.co/Systran/faster-whisper-small) | `536b0662742c02347bc0e980a01041f333bce120` |
| Danish alignment | [saattrupdan/wav2vec2-xls-r-300m-ftspeech](https://huggingface.co/saattrupdan/wav2vec2-xls-r-300m-ftspeech) | `7a60985d596e7a9e26b01a0707fe59daac6ffe84` |
| English alignment | [facebook/wav2vec2-base-960h](https://huggingface.co/facebook/wav2vec2-base-960h) | `22aad52d435eb6dbaf354bdad9b0da84ce7d6156` |
| Speaker detection | [pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) | `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` |

The runtime also needs NLTK's `punkt_tab` tokenizer data. It downloads that data only when this
run permits downloads. Community-1 downloads require accepted model terms and an HF read token.
Tokens use the app's OS credential store or server session, sharing the existing HF setup
scope. Password fields clear when closing the dialog. The worker receives only the HF token
in its temporary environment; OpenAI/UCloud credentials are excluded. Tokens are absent from
job snapshots, command arguments, results and logs. HF and pyannote telemetry are disabled.
See [pyannote's documented telemetry switch](https://github.com/pyannote/pyannote-audio#telemetry).

## Timing and preservation

For long or dense passages, a preliminary local recognition pass finds distinctive matching
phrases. It uses a pinned multilingual small model in 30-second slices with overlapping context.
Only unique, consistently ordered phrases anchor text to audio; repeated/conflicting wording
and large gaps remain uncertain. Short timed passages keep their actual imported windows.
Known passage intervals constrain anchors. Recognition is used only to locate search windows:
its generated text never replaces reviewed words and its timestamps are not published as word timing.

WhisperX aligns the supplied words within windows of at most 60 seconds, 120 words and 1200
characters. Interpolation is disabled. New requests retain valid low-score timing estimates
and flag uncertainty for review; genuinely missing timestamps stay missing. Older frozen
requests keep their original score-filtering policy. Partial
alignment keeps a bounded source sentence intact with its actual replay interval. Individually
valid words still highlight, and the sentence stays flagged as incomplete. An originally unbounded
source instead separates reliable runs from unlocated gaps, preserving all characters and whitespace.
The new result is labelled **partial word timing** and records word coverage. No gap receives a
fabricated interval. Older fragment results offer **Replay source passage** only where exact frozen
source evidence supplies an interval; this plays the whole source sentence, not an estimated fragment.
New requests retain valid low-score timing estimates and preserve uncertainty hints; frozen
legacy requests retain their score-filter policy. Word and complete-passage coverage are
separate measures of availability, not accuracy. No automatic second alignment pass is enabled.
Word confidence scores are not calibrated probabilities. Phrase recognition can be wrong and
is deliberately conservative; inspect results against the audio.

Speaker detection labels overlap with actual word/passages intervals. Reliable timed words
allow splitting at speaker changes while retaining every character, including whitespace.
Without word timing, mixed-speaker passages receive a mixed label; no textual boundary is
guessed. New speaker IDs are independent of previously renamed people. Detection is not
proof of personal identity.

Enhanced results have a restoration baseline at the frozen reviewed text. The source result
retains its earlier imported originals, edits, timings and speaker names. Select it any time.
Edits made while enhancement runs are preserved in that source and never overwritten.

## Queue and recovery

One local enhancement runs at a time alongside the existing preparation/transcription workers.
Activity supports cancellation and explicit retry; successful alignment and speaker stages are
saved separately under `recordings/<id>/enhancements/<job-id>`. Preliminary recognition slices
and successful alignment windows are also checkpointed, so a late interruption need not repeat
the entire alignment. Retry reuses completed stages and
keeps the same frozen text/options. Restart marks interrupted work for explicit retry, without
automatically rerunning inference. The default time limit is six hours and the maximum supported
recording duration is six hours / 100,000 source words. Float32 audio is converted in bounded
blocks and mapped from disk. Model inference runs sequentially; these structural bounds do not
establish peak RAM/VRAM use. Derived WAV/float audio and checkpoints use additional disk space
and stay in the managed project folder. Source recordings and raw stage output remain recoverable.

Automated acceptance uses synthetic inference, mocked packages, temporary libraries and real
subprocess cancellation. It validates word preservation, output bounds, stage reuse, credentials,
Protected-project use, playback/result selection and restart. It does **not** establish actual
WhisperX accuracy, runtime memory use, GPU compatibility or successful gated-model access.
For separate remote execution, see [UCloud enhancement](ucloud-enhancement.md). The remote
runtime and cache must be prepared first; local credentials are never sent to that worker.

## Automatic language development boundary

Internal full-ASR Automatic has verified decision checkpoints and explicit partial-result
semantics under [ADR 0069](decisions/0069-gated-automatic-asr-language.md) and
[ADR 0070](decisions/0070-automatic-asr-partial-results.md). This is not a general app setting:
Processing and existing-text/passage enhancement retain their Danish/English choices.
An explicit saved **CBS Runtime / WhisperX** model can opt into Automatic for full ASR under
Settings → UCloud → Available models (ADR 0071). Both Danish and English aligners
are prepared when timing is requested; the normal explicit preparation confirmation remains.
The prototype's support threshold is uncalibrated; its internal VAD detector window is unknown.
Completed ASR retains its decision for retries. Unavailable alignment preserves text and real
passage replay with null word intervals; speaker detection is independent. Empty results keep
their distinct no-speech/uncertain outcome. Source/version/report metadata records those limits,
and complete cached stages can finalize without an inference runtime.
Pooled remote acceptance and stage carry validate the frozen per-file decision and alignment
outcome, including partial/empty results. General defaults and bounded repairs remain later
integration work. No claim of automatic language accuracy or mixed-language alignment is made.

The standalone worker also has an internal local `bounded_alignment` operation (ADR 0072).
It validates one complete prepared input of at most 60 seconds before decoding, then probes
only the first actual contiguous 30 seconds at most, without VAD or ASR generation. A separate
decision checkpoint permits retry without redetecting. It does not assess whether supplied
text matches the audio, establish speech presence, or prove the clip's original-recording
mapping. Its output records an alignment attempt, not verified agreement. This developer seam
is not exposed by Add word timing, Processing settings, public requests or UCloud admission;
those integrations and their source/compatibility/carry safeguards remain pending.
