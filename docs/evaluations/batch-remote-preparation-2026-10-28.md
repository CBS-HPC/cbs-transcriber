# T87 — Many-file preparation: local measurements and remote options

28 October 2026. **Offline assessment; no production default or runtime protocol change.**

Remote CPU preparation is worth a controlled CBS Runtime trial for compressed recordings,
but uploading every original is not a general many-file optimization. In this eight-file
synthetic mix, originals totalled **10.24 MB**, versus **4.07 MB** for the inference WAVs.
Compressed M4A originals were smaller; raw stereo WAV and video originals were larger.
Browser conversion was a separate, substantially greater CPU task in this fixture. Moving
only inference preparation would leave that browser work on the user's computer.

This builds on the [earlier codec/timebase experiment](remote-media-preparation-2026-10-27.md)
and [ADR 0075](../decisions/0075-common-original-transcription-input.md). The accepted common
source is the immutable original. Reusing browser MP3 as the new inference source is no
longer the chosen direction. Existing frozen jobs keep their prior contracts.

## What was measured

The new [standalone script](../../scripts/evaluate_batch_preparation.py) reuses the earlier
seeded tone/noise generator, PCM reader and bounded FFmpeg measurement helper. It generates
eight files with **127 seconds of nominal generated duration**: four AAC/M4A files, two stereo
48 kHz PCM16 WAVs and two 640 × 360 H.264/AAC videos. Individual durations are 15–17 seconds;
the first four contain 63 nominal seconds. Actual decoded frame counts/durations are
recorded separately. Files with the same duration reuse the deterministic
waveform, so this is neither eight independent interviews nor a speech-quality dataset.

The experiment measures:

- Direct-original, first-audio-stream conversion to mono 16 kHz PCM16 WAV for four and eight
  files, using one or two local preparation workers. Every output's PCM/hash/geometry is
  checked against the same file's other local runs; concurrency changed none of them.
- Browser MP3 and, for video, H.264/AAC MP4 preparation as a separate eight-file/two-worker
  batch. These derivatives never feed inference preparation.
- Three optional remux/lossless payloads, each admitted as a candidate only when local
  normalized PCM hash, sample count, rate, channels and width exactly match its reference.
- Payload-only transfer arithmetic at explicitly assumed effective rates. No connection,
  upload, UCloud job, decoder on Linux, model, GPU or inference was used.

Measured host: Windows 11 build 26100, Python 3.13.5, eight logical CPUs,
imageio-ffmpeg 0.6.0, bundled FFmpeg `7.1-essentials_build-www.gyan.dev`. Binary SHA-256:
`2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3`.
The report records command arguments, input/output hashes, script hashes and stage timings.

Each configuration ran **once**. Worker order alternated between the four- and eight-file
cases; caches and other host activity were uncontrolled. Wall time includes process startup,
waiting, output validation and local I/O. FFmpeg CPU is its reported user plus system CPU,
summed across subprocesses; script CPU is reported separately. Missing CPU observations
would remain unavailable rather than zero. These short runs do not establish sustained
throughput, a worker-count recommendation, or an ETA for hundreds of long recordings.

Importantly, this measures the **transformation phase**, not the complete application.
Production source hashing/inspection, completeness/timeline proof, queue/database work,
model readiness, uploads, retries and cleanup are excluded. The script's generated results
are assessment records, not production `original-v1` preparation receipts. Synthetic fixture
creation itself took 43.24 s and 28.72 Python CPU s; that generation cost is excluded from
the preparation comparisons because users already possess their recordings.

## Local many-file results

| Files / recording seconds | Local workers | Batch wall | FFmpeg CPU sum | Script CPU | Subprocess wall sum |
| --- | ---: | ---: | ---: | ---: | ---: |
| 4 / 63 s | 1 | 2.134 s | 0.375 s | 0.063 s | 2.060 s |
| 4 / 63 s | 2 | 1.326 s | 0.344 s | 0.094 s | 2.531 s |
| 8 / 127 s | 2 | 2.582 s | 0.578 s | 0.141 s | 4.658 s |
| 8 / 127 s | 1 | 2.591 s | 0.424 s | 0.141 s | 2.411 s |

Two workers overlapped subprocesses, but the eight-file wall time was essentially unchanged
in this single run. Do not extrapolate the four-file improvement into a promised batch
speedup. Existing bounded local preparation can already overlap independent files; moving
the same work remotely needs measurements of the actual remote bottleneck.

The separate eight-file browser batch took **7.233 s wall**, **8.999 FFmpeg CPU s** and
0.094 script CPU s, with two local workers. Its outputs totalled **4,097,742 bytes**:
2,041,418 bytes of MP3 and 2,056,324 bytes of video. In this fixture browser preparation
consumed considerably more CPU than inference WAV conversion. This says nothing about
speech recognition quality or how a different video codec/resolution behaves.

If browser conversion were also moved remotely, its outputs would need downloading before
local review. Those 4.10 MB are additional transfer bytes here, not a free consequence of
remote inference preparation. Upload and download rates may differ. Browser offload would
need its own output/timebase/readiness acceptance and must remain a separate choice.

## Original payloads can save or add transfer work

MB below means decimal 1,000,000 bytes. The last two columns are signed
`WAV payload seconds − original payload seconds`, calculated as `8 × bytes / rate`.
Positive means original bytes leave payload headroom; negative means they add transfer
work. They are **counterfactual arithmetic, not measured transfer or GPU-idle savings**.

| Eight-file group | Files | Original MB | Inference WAV MB | At assumed 5 Mbit/s | At assumed 20 Mbit/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| AAC/M4A | 4 | 0.646 | 2.018 | +2.195 s | +0.549 s |
| Stereo 48 kHz WAV | 2 | 6.336 | 1.056 | −8.448 s | −2.112 s |
| H.264/AAC video | 2 | 3.263 | 0.993 | −3.632 s | −0.908 s |
| Entire mix | 8 | 10.245 | 4.067 | −9.885 s | −2.471 s |

For the four-file mix, originals were 4,977,113 bytes and WAVs 2,017,594 bytes. For all eight,
the exact counts were 10,244,676 and 4,066,504 bytes. At assumed 5 Mbit/s the eight-file
payloads alone take 16.391 s versus 6.506 s; at 100 Mbit/s, 0.820 s versus 0.325 s. Session
negotiation, retries, encryption, storage latency and concurrent transfers are absent.

Compressed originals therefore provide a plausible candidate; raw WAV/video need a
per-file decision. Merely having many files does not reverse the byte penalty. These
figures also cannot be added mechanically to local batch wall time: preparation, uploads,
allocation startup and another member's GPU work can overlap.

## Remux and lossless options, with exact local PCM proof

| Representative candidate | Payload bytes | Reference WAV bytes | Local encode/remux wall / CPU | Local decode wall / CPU | Exact reference PCM? |
| --- | ---: | ---: | ---: | ---: | --- |
| Lossless FLAC of original stereo 48 kHz WAV | 1,905,365 | 512,078 | 0.585 / 0.156 s | 0.305 / 0.063 s | Yes |
| Selected AAC track copied from video into M4A | 152,410 | 480,676 | 0.283 / 0.031 s | 0.311 / 0.078 s | Yes |
| FLAC of already normalized mono 16 kHz PCM | 328,843 | 512,078 | 0.299 / 0.031 s | 0.307 / 0.016 s | Yes |

These are three individual fixtures, not batch-wide qualifications. The raw-source FLAC
preserved audio but was still 3.72 times the normalized WAV payload. Normalized FLAC saved
bytes while retaining all normalized samples, but **still required local normalization**;
it is a transfer optimization, not full CPU offload. The video audio-only remux avoided
uploading picture data and matched this reference exactly, making it a useful opt-in trial
candidate under a pinned contract.

The earlier M4A-to-Matroska copy changed decoded samples by 21.3125 ms. Packet copying is
not a general timebase guarantee. Every admitted container/recipe needs exact sample/frame
proof; a mismatch disqualifies it rather than authorizing a guessed offset. A valid Windows
round trip does not prove the pinned Linux decoder will produce the same prepared input.
Equal PCM also does not permit replacing an existing frozen WAV/container hash or reusing
its old language/stage checkpoints under a new transport.

## Recommended staged trial

1. **Finish independent readiness first.** Browser derivatives remain presentation work.
   The source must have its own durable validation/readiness state so one slow video cannot
   block already valid members. Current Stage 1 still waits for browser readiness, so a
   remote decoder alone would not remove that gate. Measure source inspection separately:
   today's original-v1 freeze itself performs local PCM inspection. Moving only the later
   WAV conversion leaves that CPU work locally. Full offload requires a new staged source
   contract where immutable byte identity is frozen first and remote decoded duration/
   samples become validated preparation evidence without rewriting the frozen source.
2. **Add an explicit fresh-task transport version for CBS Runtime.** Freeze original hash,
   bytes, selected stream, source bounds, supported container, decoder/recipe identity and
   limits. A compressed payload must have its own name/type; never rename it `audio.wav`.
   Keep old WAV jobs, code-hash plans, receipts, uncertain submissions and retries unchanged.
   The runtime must declare/check its pinned decoder capability before private upload. New
   protocol/decoder compatibility belongs to the allocation; each file's source hash stays
   in the member identity rather than fragmenting allocation compatibility.
   Current task manifests require `request.json` and `audio.wav`, and the remote WAV limit
   is 4 GiB (`runtime_batch_remote.py`); the app's original upload limit is 20 GiB. Do not
   reuse that WAV limit or file allowlist implicitly for originals. Define independent
   compressed-input, decoded-frame, time, disk and output limits before admission. An
   oversized video must not slip through merely because its eventual audio would fit.
3. **Upload, prove, prepare, then admit ASR.** After existing runtime/MIG readiness and current
   Protected approval, upload the owned original or explicitly qualified derived payload.
   Verify complete bytes/hash. A bounded CPU preparation stage writes pending output and
   proves completeness, first-stream/sample-clock handling, actual frames, sample geometry,
   prepared hashes and source linkage before publishing an immutable ready receipt. Preserve
   original-v1 refusal of unsupported offsets/discontinuities and known truncated input;
   exit zero alone is insufficient. Bound decode time/output/RAM and forbid external media
   references or network access while decoding. ASR and language evidence bind to that exact prepared
   WAV/request, with no inference after failed or uncertain preparation.
4. **Pipeline members within existing ownership.** Reuse the batch owner, task attempts,
   capacity limits, heartbeats, late-admission cutoff and atomic seal. Start with bounded
   CPU/transfer concurrency and one GPU inference at a time. Preparation of member B may
   overlap GPU work for A, but preparation/acceptance must be visible phases rather than
   fake ASR progress. Do not reserve a GPU owner for missing, still-copying or otherwise
   locally inadmissible byte sources. Remote sample validation is the later preparation gate. An admitted
   task's input/ready/accepted receipts are immutable; reconnection observes them rather
   than uploading again. Stop/shutdown still harvest accepted outcomes, retain failures and
   partial evidence, and perform cleanup only after durable local preservation/results.
5. **Measure on an explicitly approved test allocation before choosing defaults.** Record
   bytes and retries, local remaining CPU, actual remote decode CPU/RAM/wall, first prepared
   task, first/last GPU work, allocation lifetime, end-to-end completion and codec/timebase
   checks on licensed public media. Compare compressed originals, qualified remux and
   local normalized WAV using comparable batches. No GPU-idle, price, remote ETA or quality
   conclusion can be obtained from these local simulations.

CPU decoding inside a GPU allocation can consume paid allocation lifetime while its GPU is
idle. Pipelining may hide some work; it cannot guarantee that. A separate CPU allocation
before requesting a GPU could avoid some GPU waiting, but introduces another funded
resource/ownership/recovery/cleanup lifecycle. This assessment measured neither option on
UCloud and does not justify adding a separate CPU service as the first implementation.

Browser offload, if later desired for many videos, should be a distinct CPU-only proposal
covering approved transfer, source reuse, downloaded derivative hashes, A/V synchronization,
range playback and local readiness. No automatic UCloud use should appear for OpenAI or
local-processing users. Preserve the selected provider/execution, approvals and policy;
this assessment does not authorize uploading their recordings elsewhere.

## Reproduction and verification

```powershell
uv run python scripts/evaluate_batch_preparation.py --output test-results/batch-remote-preparation-NEW --seconds 15
uv run pytest tests/test_batch_preparation_assessment.py -q -o addopts=''
uv run ruff check scripts/evaluate_batch_preparation.py tests/test_batch_preparation_assessment.py
uv run ruff format --check scripts/evaluate_batch_preparation.py tests/test_batch_preparation_assessment.py
```

The successful run is under ignored `test-results/batch-remote-preparation-2026-10-28/`, with
`report.json`, generated inputs, prepared PCM and per-command logs. Use a fresh directory;
the script refuses to overwrite one. It permits only four/eight files, one/two workers and
5–20 base seconds plus at most two seconds per file. Each FFmpeg command has a 30-second
timeout inherited from the earlier helper. The 15 focused tests cover signed transfer
arithmetic, invalid assumptions/workload bounds, output preservation, exact PCM qualification
and a real four-file/two-worker decode. All passed; Ruff/format checks passed.

No real recordings, private library, keys, models, UCloud/API calls, live capacity benchmark,
application restart, worker ABI/CODE_FILES change or production default change was involved.
