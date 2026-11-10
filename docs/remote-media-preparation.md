# T87 — Local playback preparation versus UCloud inference preparation

Offline evaluation, 27 October 2026. **No processing defaults changed.** No UCloud job, network
transfer or inference benchmark was run. This evaluates the current code and synthetic local
FFmpeg work; live throughput, GPU idle time and allocation cost still need measurement.

## The two preparations

`media.prepare_media()` makes local browser playback: mono 44.1 kHz, 128 kb/s MP3, plus browser
video when needed. It also establishes decoding/duration. CBS Runtime later takes that MP3
and `enhance_window()` decodes it to mono 16 kHz PCM16 WAV for upload. Native Transcriber
already uploads originals and uses its own remote application pipeline.

Moving the second conversion to UCloud does not remove the first conversion or the current
`recording.status == ready` gate. It therefore cannot by itself remove the long local
"Preparing" step observed by the user.

## Small reproducible local measurement

Generated a 600-second, 330 Hz mono tone at 48 kHz, encoded as AAC 64 kb/s M4A. Used the
bundled FFmpeg 7.1 Windows executable, `-benchmark`, three repetitions per conversion and
median wall time. The fixture is synthetic and is not representative interview inference.
FFmpeg user/system CPU measurements cover that subprocess, not the whole application.

| Stage | Output bytes | Median wall time | Median FFmpeg user + system CPU |
| --- | ---: | ---: | ---: |
| Source M4A | 4,937,253 | — | — |
| Current browser MP3 conversion | 9,601,082 | 11.179 s | 12.766 s |
| Current MP3 → inference WAV conversion | 19,200,078 | 1.452 s | 2.891 s |

The WAV is 3.89 times the source size in this fixture. A 16 kHz mono PCM16 WAV uses about
32,000 bytes per second before its header; compressed originals vary substantially. Cached
local I/O, codec choices and this Windows development machine limit generalization.

Local reproduction, each conversion three times after generating the source:

```text
ffmpeg -f lavfi -i sine=frequency=330:sample_rate=48000:duration=600 -ac 1 -c:a aac -b:a 64k synthetic.m4a
ffmpeg -benchmark -i synthetic.m4a -map 0:a:0 -vn -ac 1 -ar 44100 -c:a libmp3lame -b:a 128k playback.mp3
ffmpeg -benchmark -i playback.mp3 -vn -ac 1 -ar 16000 -c:a pcm_s16le inference.wav
```

Raw measurements and the generated files remain under ignored
`test-results/remote-media-benchmark/`; no recording is committed.

## Proposed next implementation and acceptance

1. Finish T85's durable shared allocation first. Keep browser playback preparation local and
   independent. Evaluate uploading an immutable compressed inference source once per member,
   then decoding on UCloud after readiness and checksum validation. Do not silently change
   native Transcriber's existing original-upload behavior.
2. Verify/pin the remote decoder before private upload. The current runtime recipe does not
   explicitly pin an FFmpeg executable; an arbitrary executable in a Terminal image is not
   enough evidence. A changed runtime recipe requires explicit preparation under the existing
   consent protocol. Record decoder/version, source hash, selected audio stream, sample rate,
   channels, duration and any real time-origin conversion in provenance.
3. Separate inference-source readiness from local playback readiness if early UCloud execution
   is desired. Do not weaken actual decoding validation to a header-only check. A copied
   immutable source, verified remote decoding, browser playback errors and cloud inference
   outcomes need distinct durable states. Keep useful remote results if local video preparation
   fails; keep playback useful if the cloud task fails.
4. Test codec delay, leading silence, nonzero/negative video PTS, multiple audio streams,
   resampling, long/VBR files and damaged/truncated files. Verify that source-relative words,
   video and local playback agree; never assume equal timebases because durations look close.
5. Measure public licensed audio/video on the actual approved MIG: local preparation CPU/wall
   time, bytes uploaded, transfer duration/retries, remote decode time and peak CPU/RAM, GPU
   idle/allocation time, and end-to-end time. Contrast current WAV uploads with compressed
   uploads. Do not claim financial savings from the local microbenchmark.

Potential benefit is primarily lower upload volume and overlap with local playback work.
Remote decoding while a GPU is reserved also consumes allocation time; a separate CPU
preparation job introduces another allocation and recovery protocol. Choose the default from
the live measurements rather than introducing that extra job speculatively.
