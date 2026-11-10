# T87 — Compressed inference input and normalization evidence

27 October 2026. **Offline evaluation only; leave production defaults unchanged.**
No original interviews, UCloud allocation, API requests, model weights or inference were used.

The next opt-in candidate should reuse the **existing browser MP3**, with a pinned remote
decoder and an explicit preparation receipt. It halved the upload payload in this fixture and
reproduced today's exact model-input PCM when decoded with the same binary/recipe. Normalized
16 kHz FLAC is a useful lossless reference, but required local normalization/encoding and was
larger than that MP3. Uploading originals needs separate quality and timebase acceptance.

## Current path and experiment scope

[`media.prepare_media()`](../../src/cbs_transcriber/media.py) selects the first audio stream and
creates mono 44.1 kHz, 128 kb/s browser MP3. [`enhance_window()`](../../src/cbs_transcriber/enhancement.py)
later decodes that MP3 to mono 16 kHz PCM16 WAV. The worker currently accepts that WAV shape,
creates its disk-backed float samples, and binds language evidence to the prepared WAV/request
hashes and actual frame count in [`enhancement_worker.py`](../../src/cbs_transcriber/enhancement_worker.py).
The batch input protocol currently requires `audio.wav`. Compressed input is not implemented.

This extends the earlier 600-second tone conversion assessment with **PCM identity, stream,
container and failure checks**, rather than repeating its three-run median benchmark. That
earlier document was read from Git (`git show 48b7a45:docs/remote-media-preparation.md`); its
working-tree deletion was left untouched.

The new [standalone script](../../scripts/evaluate_media_transport.py) generates 30 seconds of
seeded stereo tones/noise at 48 kHz, with leading and periodic silence. It encodes synthetic
AAC/M4A, prepares the browser MP3, and compares candidate inference inputs. This is **not speech
or an interview-quality benchmark**. No recognition/timing/speaker accuracy was measured.

Measured environment: Windows 11 build 26100, Python 3.13.5, imageio-ffmpeg 0.6.0, bundled
FFmpeg `7.1-essentials_build-www.gyan.dev`. Binary SHA-256:
`2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3`.
Every conversion was run **once**; wall time includes process startup/waiting and host load.
CPU time is FFmpeg's reported user + system time, excluding Python and application work.
All rows labelled remote simulation ran on this same Windows computer, not on UCloud.

| Operation / candidate | Payload bytes | Local subprocess wall | FFmpeg CPU | Prepared PCM equals current path? |
| --- | ---: | ---: | ---: | --- |
| Browser MP3 creation, common existing local work | 481,631 | 2.053 s | 0.828 s | — |
| Current MP3 → PCM16 WAV, local inference preparation | 960,590 | 0.564 s | 0.109 s | Reference |
| Upload existing browser MP3; decoder simulation | 481,631 | 0.703 s | 0.140 s | **Exact** |
| Encode normalized 16 kHz FLAC locally | 645,047 | 0.720 s | 0.172 s | See round trip below |
| Decode that FLAC, remote simulation | 645,047 | 0.411 s | 0.016 s | **Exact** |
| Upload original AAC/M4A; decoder simulation | 315,619 | 0.332 s | 0.156 s | **Different** |

The two exact round trips produced mono PCM16 at 16 kHz with **480,256 frames / 30.016 s**,
matching the current path's raw PCM SHA-256:
`243b99657988d4f5b4e94da34472a267804a425397aa08f2d3eb40ebf2917f4a`.
The comparison hashes decoded samples, not WAV headers. Direct original decoding had the
same frame count but a different PCM hash; bypassing today's lossy browser preparation changes
the inference input. This experiment establishes no accuracy advantage or disadvantage.

## Decisive format and failure checks

| Synthetic case | Measured result | Implication |
| --- | --- | --- |
| AAC packet-copy remux from M4A to Matroska | Decoded frames changed from 480,256 to 480,597: **+341 samples / 21.3125 ms** at 16 kHz. PCM hash changed; both first decoded source PTS readings were 0.0. First 10 ms block above RMS 500 moved from 1.00 to 1.02 s. | Equal starting PTS and similar duration do not prove identical normalized audio. Container/trim handling is a plausible cause; this experiment does not isolate its precise metadata field. |
| Two audio streams; second marked default with a 2.5 s source PTS offset | Automatic selection matched the second stream's PCM, not the first. Explicit mapping of stream 0 reproduced the first-stream/remux PCM. The second stream's first source PTS was **2.5 s**, yet its decoded WAV's first signal appeared at 0.02 s. | Freeze the stream and timeline recipe. A decoded WAV has a sample timeline; it does not preserve container PTS as an automatic playback offset. Never invent the offset from duration alone. |
| Original M4A truncated to half its bytes, metadata retained at the front | Default decoding returned **0** and wrote **243,712 frames / 15.232 s**, rather than 30.016 s. | A checksum can faithfully identify an already damaged source. Successful process exit alone is insufficient evidence of complete audio. |
| Same truncated input with `-xerror -err_detect explode` | Nonzero exit; a partial WAV still existed. | Validate success and completeness before publishing a prepared-file receipt or renaming a pending output. A file's existence is not completion. |

These findings are observations of the pinned local executable and commands retained by the
script. They are not promises about arbitrary codecs, Linux builds or a Terminal image's
unversioned FFmpeg. The stereo/noise fixture is deliberately distinct across streams; no
measured waveform similarity is being treated as a transcript/audio alignment.
Long/VBR inputs, negative PTS, audio/video synchronization and scoped seek boundaries were not
tested; the Matroska fixtures contain audio only.

## Transfer arithmetic and GPU-idle limits

For this fixture, browser MP3 reduced bytes by **49.86%** versus WAV; FLAC by **32.85%**.
At an *assumed effective* 5 Mbit/s, payload-only transfer takes 1.537 s for WAV, 0.771 s for
MP3 and 1.032 s for FLAC. The resulting transfer headroom is **0.766 s / 0.505 s** for added
unoverlapped MP3/FLAC decoding respectively. At 20 Mbit/s it falls to **0.192 s / 0.126 s**.
These are `8 × bytes / assumed bits-per-second`, not measured network transfer times.

Do not substitute the Windows decoder simulations for UCloud CPU or GPU-idle measurements.
Session creation, retries, storage latency, host contention, allocation startup and preparation
overlap were not measured. Current local WAV preparation can overlap allocation startup;
batch upload/preparation can also overlap another member's GPU work. Therefore neither these
local wall times nor byte savings establish GPU-time or financial savings. A separate CPU
allocation would add ownership/recovery work and has no measured justification here.

## Smallest next implementation seam

Keep browser playback and its readiness gate unchanged for the first opt-in trial. Reusing its
MP3 removes the second local decode/WAV upload; it does **not** remove initial browser/media
preparation. Original-source processing before playback is ready requires separate durable
inference/playback states and a broader timebase decision.

1. Introduce a versioned compressed-input contract, retaining every frozen WAV job/retry
   unchanged. Pin the Linux decoder/recipe in the prepared runtime and check it before private
   upload. Never infer its availability from a filename or the Terminal app version.
2. Freeze source bytes/hash, selected audio stream, normalization recipe and actual source
   bounds. Upload unchanged compressed bytes, validate them, then decode in the owned task
   folder with bounded time/output and cancellation. Do not rename MP3 bytes to `audio.wav`.
3. Publish an immutable preparation receipt linking the compressed source and decoder/recipe
   hashes to the actual prepared WAV hash, PCM hash, sample format/rate/channels, frame count
   and explicit timeline mapping. Reject damaged/ambiguous preparation before ASR. The worker's
   WAV-to-float validation remains useful after this new stage.
4. Bind language evidence and stage reuse to that exact prepared-input receipt. The present
   language binding hashes the WAV container; equal PCM alone does not authorize changing an
   earlier frozen WAV identity or reusing its checkpoints under a different payload contract.
5. Validate leading silence, codec trimming, offsets, multiple tracks, damaged media and scoped
   clips against playback before interpreting remote word times. Then measure licensed public
   media on the approved MIG: bytes, transfers/retries, local preparation, remote decode CPU/RAM,
   first/last GPU work, allocation lifetime and end-to-end time. Make a default choice only from
   those measurements and real timing/quality acceptance.

T87's offline comparison is complete; a compressed-input implementation, cross-platform
decoder acceptance, actual transfer/GPU-idle measurements and default changes remain open.

## Reproduce

```powershell
uv run python scripts/evaluate_media_transport.py --output test-results/t87-media-transport-rerun --seconds 30
uv run ruff check scripts/evaluate_media_transport.py
uv run ruff format --check scripts/evaluate_media_transport.py
```

Use a fresh output directory: the script refuses to overwrite one. It accepts 5–60 seconds
and bounds each FFmpeg subprocess to 30 seconds. Its assertions check both exact PCM round
trips, distinct stream content and the incomplete-source failure fixture. The successful run's
commands, every timing/hash/decoded frame count and logs are under ignored
`test-results/t87-media-transport-2026-10-27/` (`report.json`). Only this report and the script
are intended for Git; no generated media or private paths are included.
