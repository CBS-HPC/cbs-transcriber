# Original inspection and media preparation: performance work plan

Status: planned and in progress. The user asked to optimize concurrency for both activities,
not just investigate them. Start with the bounded local controls being implemented in parallel;
measure before choosing larger defaults or changing source-evidence contracts. UCloud preparation
is an evaluation item, not an authorized upload or a promise to use cloud compute.

## Current behavior and problem

Import publishes a managed original and a playback media job. A transcription request creates
or reuses a separate original inspection; it is not automatically part of the earlier playback
conversion. `audio_inputs.freeze()` currently checks an owned regular file, hashes its bytes,
identifies and checks FFmpeg, probes the first audio stream/timeline, decodes the complete first
audio stream to 16 kHz mono PCM while counting frames and hashing decoded bytes, then rehashes
the source and checks the decoder again. It publishes a generation-bound receipt only after all
checks succeed. [ADR 0075](../decisions/0075-common-original-transcription-input.md),
[ADR 0082](../decisions/0082-durable-original-inspection.md), and
[ADR 0085](../decisions/0085-durable-input-admission.md) explain why.

`media.prepare_media()` separately generates mono 44.1 kHz MP3; video also gets a separate
H.264/AAC transcode, up to 1280 px. The queue starts two inspection and two media workers; FFmpeg
itself can use multiple CPU threads, and other lanes can run concurrently. Therefore the
number of files in progress is not the number of CPU threads. The observed 12-file activity
span is not a per-stage benchmark: it includes queue time and lacks durable attempt timings.

Playback and original-input admission are distinct: new original-v1 inference does not wait for
the browser derivative. Native Transcriber keeps one compatible selected-batch barrier; API and
CBS Runtime may admit ready members independently. Do not conflate the exact requirements of
these three paths, or present an inspection as proof of how a remote model decoded audio.

## 1. Instrument and benchmark safely

1. Map the real paths for import, media conversion, first inspection, cached reuse, fresh native
   admission, API chunking and CBS Runtime compressed/original transport. Identify stages that
   run twice and any actual critical-path wait. Record implementation references and current
   receipt fields; first resolve current outstanding Runtime failures independently.
2. Add opt-in, bounded, local-only stage metrics for queue wait, source ownership/open/stat,
   first SHA-256 read, decoder identity/version, first-stream and timeline probes, full decode/
   PCM digest and frame count, second source hash, final identity check, receipt publication,
   and media audio/video conversion. Measure CPU time, elapsed time, read/write bytes, peak
   memory, disk saturation, cancellations and time to first ready file/batch. Never log paths,
   names, text, audio, tokens, arbitrary FFmpeg logs or high-cardinality IDs. Instrumentation
   must not widen API/report data, change receipt semantics or hold queue/SQLite locks over I/O.
3. Generate disposable synthetic WAV, compressed audio and audio/video inputs, including a few
   short files, multiple long files, mixed lengths, many small files and representative codecs.
   Include non-zero/negative timestamps, delayed audio, multiple streams, corrupt/truncated
   inputs, sparse/changed files and unsupported codecs. Use bounded duration and disk, not real
   interviews. Run cold/warm-cache and repeated trials on Windows; record CPU cores, RAM, disk,
   power/thermal state and exact app/FFmpeg version. Randomize comparable runs; report medians,
   spread and tail latency rather than a single time.
4. Compare controlled **2, 3 and 4** workers for each lane, then mixed inspection + media runs
   under a shared process/CPU budget. Include individual FFmpeg thread counts and one concurrent
   transcription/enhancement simulation. Measure total time, time-to-first-ready, contention,
   responsiveness, cancellation latency, memory and failures. Do not run this benchmark on the
   user's real recordings or change a running application's environment. A worker increase is
   justified only if it improves relevant elapsed/tail time without unacceptable resource or
   interactive regressions; do not assume four workers are faster on four physical cores.

## 2. Implement bounded concurrency and scheduling

- Provide a user-adjustable, bounded concurrency policy for **both** inspection and playback
  preparation; preserve explicit saved settings and give a conservative default until measured.
  Coordinate one shared resource budget with FFmpeg thread limits, local inference and decoder
  processes rather than multiplying independent lane caps. Report active/queued work accurately.
- Evaluate short/long-file fairness against FIFO, but do not reorder frozen selections or split
  a native batch's admission barrier. Avoid starving long jobs and distinguish ready members from
  allocations. Preserve queue → snapshot → database ordering, same-Store lease, exact owner
  cancellation, subprocess kill/reap/drain and safe shutdown. Lowering a limit must not kill
  existing work; cancellation, retry, crash/restart and delete must remain deterministic.
- Add targeted synthetic tests for limits under concurrent claims, overlapping lanes, fairness,
  user changes while busy, repeated cancellation and bounded worker/child cleanup. Benchmark the
  final implementation against the existing two-worker behavior on the same synthetic fixtures.

## 3. Reduce duplicated I/O and latency without weakening evidence

Evaluate alternatives independently; implement only after equivalent outcomes are demonstrated:

- **Earlier inspection:** queue it after an import commits, so work can finish before a user
  transcribes. Bound contention with playback preparation, preserve current errors/controls,
  deduplicate concurrent requests and never open media under an import/database lock. This
  moves work earlier; it is not by itself proof of lower total work.
- **Shared reads or decode:** investigate one source pass/one decode that can feed both playback
  and receipt generation. The current playback MP3/44.1 kHz and original-v1 PCM/16 kHz recipes
  are different; a playback artifact or container duration cannot stand in for a PCM digest,
  sample count, source hash, timeline, decoder identity or receipt. Compare exact reference
  receipts, frame/offset behavior, supported codecs and cancellation/partial-output handling.
- **Hash during import/copy:** record a byte anchor only after an owned, closed original has
  committed and its identity/stability is verified. A streaming upload hash alone cannot prove
  the committed file was unchanged afterward. Preserve before/after ownership checks, growth
  bounds and retry anchors; test interrupted copies, replacements, links, modifications and
  races. Do not backfill or assert old evidence.
- **Cache and probes:** reuse only a validated, generation/epoch-bound receipt for the exact owned
  original and decoder. Measure redundant executable identity and first-stream probes, and remove
  only a provably redundant operation. A source change, incompatible decoder/recipe, restored
  owner or inconsistent receipt must fail closed. Legacy frozen jobs retain their original path.
- **Lighter native path:** determine whether native UCloud Transcriber needs full decoded-PCM
  evidence or could use a separately versioned, explicit integrity/timeline policy. Require a
  decision record, acceptance and provenance changes, exact codec/stream tests, and distinct
  handling for API/CBS Runtime and historical jobs. Never silently weaken original-v1 receipts.

## 4. Playback and remote alternatives

Audit whether a given source can safely be copied/remuxed instead of transcoded for playback;
verify seeking, duration, A/V synchronization and browser support on Windows and Mac before
changing recipes. Separately measure video encoding versus audio output and whether one FFmpeg
invocation safely produces both. Handle cancellation, temporary-file publication, retries,
decoder errors, missing streams and concurrent playback; no partial output may appear ready.

For large batches, compare local versus UCloud media preparation end to end: upload and download
bytes/time, queue/startup time, CPU/GPU allocation and charges, local/remote contention, failure
and resume, supported codecs, privacy/classification approval, consent and retention/cleanup.
Include already-remote original cases separately; never count remote compute alone as the total
cost. No private audio or paid UCloud jobs are authorized by this plan; live tests need a
concrete public/synthetic fixture, budget and approval.

## Deliverables and decision gate

Produce a small evidence table per fixture and lane, including exact source/decoder versions,
worker and FFmpeg thread limits, machine conditions, distributions, resource peaks, failures
and time-to-first/last-ready. Identify which optimizations can ship independently and the
smallest measurable change; record rejected options and why. Keep mocked/synthetic, real
device and real UCloud results distinct. Update TODO/SPEC and add an ADR if persistence,
receipt, timing, job semantics or remote behavior changes. Independent review must verify
source/receipt equivalence, privacy and cancellation/recovery guarantees before activation.
