# Local lane concurrency benchmark — 5 November 2026

Synthetic, offline evidence for [ADR 0146](../decisions/0146-adjustable-local-concurrency.md).
No real recordings, network calls, provider, UCloud job or model were used.

## Method

[`scripts/benchmark_local_concurrency.py`](../../scripts/benchmark_local_concurrency.py) generates
seeded, distinct five-minute stereo tone/noise files with the bundled FFmpeg (rotating 48 kHz
PCM WAV, 128 kbit/s MP3 and 128 kbit/s AAC M4A; about 57.6 MB, 4.8 MB and 4.8 MB each) and
hard-links 16 of them into a fresh temporary library per run. Each run drives the real queue
lanes inside the library lease:

- **media**: playback preparation (`media.prepare_media`, MP3 for browser playback).
- **inspect**: original source inspection (`audio_inputs.freeze`: two source hashes, probes and a
  full decode to 16 kHz PCM with timeline proof).
- **admission**: request preparation of 16 API intents whose inspections are already ready.
- **api**: API transcription of admitted original-v1 jobs (local MP3 part preparation, then a
  mocked provider call that sleeps 3 s and returns one segment, then the saved result).
- **pipeline**: upload-and-transcribe — playback, inspection, admission and API lanes together,
  until every transcript and every playback file is finished.
- **idle**: only admission workers, with 16 intents waiting for inspections that never start
  (claim polling cost); **idle-default**: the complete default queue on an empty library.

It reports wall time, time to first completion, per-job time, machine CPU utilisation, Python
and sampled FFmpeg child CPU, peak private memory of the process tree, SQLite `BEGIN IMMEDIATE`
waits and `database is locked` errors, and claim-lock waits/hold fraction (via an instrumented
lock). Runs alternate order between repeats. Reproduce with:

```powershell
uv run python scripts/benchmark_local_concurrency.py --output test-results/lc/NEW `
  --files 16 --seconds 300 --workers 1 2 4 6 8 --lanes media inspect admission api idle idle-default `
  --pipeline 2/2/2/2 4/4/2/4 6/6/2/6 8/8/2/8 --repeat 2
```

Keep `--output` short on Windows: deep project paths exceed the legacy 260-character path limit
and API part preparation then fails with `FileNotFoundError` (seen once during calibration).

## Host and caveats

Windows 11 (26100), Python 3.13.5, FFmpeg `7.1-essentials_build-www.gyan.dev`, Intel Core
i7-8665U: **4 physical cores, 8 logical processors** (`os.cpu_count()` = 8), 16 GB RAM with about
2 GB free. The machine was **shared and busy**: OneDrive sync (~0.5–0.9 core), the user's running
CBS Transcriber (~0.5–0.8 core with no FFmpeg children) and other agents' test suites. Machine CPU
sampled for one second before each run was usually 0.8–1.0. Absolute times are therefore
inflated and noisy (the same configuration varied up to 3×); use them for relative trends only.
One pipeline run (6/6/2/6, first repeat) spanned a host suspension and is excluded. The resource
sampler itself costs about 0.13 core (1.34 Python CPU s per 10 s), included in Python CPU below.

## Baseline: code before ADR 0146, workers set explicitly

Wall time in seconds for 16 files, as **best / median of two** runs; memory is the peak private
memory of the app process plus FFmpeg children. No run produced a failed job or a
`database is locked` error.

| Lane | 1 | 2 (old default) | 4 | 6 | 8 | Peak memory 1 → 8 workers |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Playback media | 81.0 / 128.5 | 51.7 / 65.4 | 45.5 / 56.8 | 34.8 / 50.5 | 30.2 / 40.0 | 106 → 331 MB |
| Source inspection | 39.1 / 52.6 | 26.8 / 31.9 | 17.3 / 21.1 | 16.0 / 18.6 | 15.1 / 17.1 | 113 → 352 MB |
| Admission (ready inputs) | 1.2 / 1.3 | 1.0 / 1.0 | 1.0 / 1.1 | 1.0 / 1.1 | 1.0 / 1.2 | 74 → 77 MB |
| API, 3 s mocked latency | 95.9 / 124.3 | 58.5 / 68.8 | 50.1 / 52.8 | 35.2 / 67.2 | 31.3 / 36.7 | 108 → 330 MB |

FFmpeg-bound lanes add roughly 30–35 MB per extra parallel task. Playback conversion cost about
6 FFmpeg CPU seconds per five-minute file regardless of workers, so its wall time is bounded by
free cores: four physical cores, two of them mostly taken by other software. Inspection spends
about as much CPU in the app process (two SHA-256 passes, PCM digest, timeline parsing, 50 ms
database cancellation polls; ~2.2 s per file) as in FFmpeg (~2.3 s per file), and stops
improving beyond four. Admission is metadata-only and fast at any count. Mocked API requests
keep scaling because they mostly wait, but real providers impose rate limits (see ADR 0146).

Upload-and-transcribe pipeline (media/inspection/admission/API workers):

| Workers | Repeat | All done | First transcript | All transcripts | Peak memory | Claim lock held |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2/2/2/2 (old) | 1 | 472.0 s | 19.0 s | 403.0 s | 269 MB | 77% |
| 2/2/2/2 (old) | 2 | 146.9 s | 19.9 s | 128.4 s | 273 MB | 31% |
| 4/4/2/4 | 1 | 143.5 s | 48.6 s | 125.7 s | 457 MB | 81% |
| 4/4/2/4 | 2 | 76.5 s | 22.7 s | 68.6 s | 465 MB | 47% |
| 6/6/2/6 | 2 | 74.0 s | 34.3 s | 73.7 s | 655 MB | 58% |
| 8/8/2/8 | 1 | 95.0 s | 55.2 s | 92.7 s | 784 MB | 62% |
| 8/8/2/8 | 2 | 73.7 s | 39.1 s | 73.7 s | 837 MB | 59% |

Within each repeat, four workers per CPU lane finished the batch in about half the time of two;
six or eight added little while memory kept rising. More parallel files also delay the *first*
finished transcript, because playback and inspection of all files share the same cores.

### Claim-lock contention (defect B370)

The shared claim lock was held for 31–81% of every pipeline run. The cause was admission polling:
with 16 admission intents waiting for inspection, every poll fully re-parsed and validated all
of them inside a `BEGIN IMMEDIATE` transaction under the claim lock. Admission workers alone,
with those 16 waiting intents and nothing else running (10 s):

| Admission workers | 1 | 2 | 4 | 6 | 8 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Polls per second | 4.1 | 7.5 | 16.3 | 24.1 | 23.7 |
| Claim lock held | 16% | 39% | 57% | 84% | 99% |
| App CPU s per 10 s | 2.7 | 4.0 | 6.4 | 9.2 | 9.7 |

Single claim-lock waits reached 0.4–2.8 s and single write-lock waits up to 4.1 s in the slowest
pipeline. Adding threads without fixing this would have made it worse. The idle default queue
(16 threads, empty library) made about 30 write transactions per second (admission 8.8,
media 3.9, transcription 3.9 and each enhancement lane 1.9 polls per second).

## After ADR 0146 (commit 21d040f), same fixtures

Two repeats of each configuration, run later while the host was even busier (one-second
background CPU 1.0 before every run), so compare rows within this section rather than with the
baseline's absolute times. No failed job and no `database is locked` error.

Idle polling, 10 s (app CPU includes the sampler's ~1.3 s):

| Scenario | Before | After |
| --- | --- | --- |
| 2 admission workers, 16 intents waiting | 7.5 polls/s, lock held 39%, 4.0 CPU s | 4.5 polls/s, lock held 5%, 1.8 CPU s |
| 4 admission workers, 16 intents waiting | 16.3 polls/s, lock held 57%, 6.4 CPU s | 4.6 polls/s, lock held 5%, 1.6 CPU s |
| Complete default queue, empty library | 16 threads, ~30 writes/s, lock held 44%, 4.4 CPU s | 37 threads, ~24 writes/s, lock held 33%, 3.8 CPU s |

With coalescing each idle lane polls about twice a second (admission/inspection about four times)
regardless of its thread count. The remaining idle claim-lock time is mostly the four CBS Runtime
coordinators' own claim/capacity checks, which this change leaves as they were.

Upload-and-transcribe pipeline (media/inspection/admission/API limits), repeat 1 / repeat 2:

| Limits | All done | All playback ready | All transcripts | First transcript | Peak memory | Claim lock held | Max write-lock wait |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2/2/2/2 (old) | 181.0 / 165.2 s | 180.5 / 165.0 s | 147.3 / 141.7 s | 42.2 / 24.6 s | 264 / 269 MB | 22% / 24% | 87 / 199 ms |
| 4/4/2/2 (then-automatic limits) | 120.3 / 176.1 s | 96.1 / 133.0 s | 120.5 / 176.5 s | 35.4 / 44.2 s | 392 / 396 MB | 25% / 29% | 57 / 108 ms |
| 4/4/2/4 | 120.5 / 114.5 s | 120.1 / 114.2 s | 97.8 / 90.1 s | 31.6 / 29.7 s | 464 / 451 MB | 27% / 32% | 97 / 54 ms |

The claim lock is now held 22–32% of a pipeline instead of 31–81%, and single write-lock waits
stay below 0.2 s instead of reaching 4.1 s. With those then-automatic limits, playback for all 16
files was ready 20–47% sooner than with the old two workers. Transcripts were then bounded by the
two API workers, whose jobs also prepare MP3 parts locally with FFmpeg; four API workers finished
all transcripts about 35% sooner than two. API initially stayed at two by default because real providers
enforce their own rate limits (a 429 fails the part for explicit retry, `max_retries=0`), so
raising it is a deliberate user choice in Settings. Single claim-lock waits still reached about
one second under full CPU saturation; they were not investigated further.

## Conclusions and open points

- These runs informed the initial four-worker automatic media/inspection limits. On 6 November,
  the user chose media = logical processors minus two (2–8), inspection = half the logical
  processors (2–4), API = six and admission = two (ADR 0146). Playback-only observations at six
  and eight workers informed that throughput preference; the exact current combined defaults
  were not measured in the post-change pipeline above. Larger machines and video-heavy batches
  still need measurements (libx264 threads itself, so fewer parallel video files can saturate a
  computer).
- More parallel files delay the first finished file while finishing the batch sooner; there is no
  priority between playback and inspection, which share the same cores.
- Inspection polls its cancellation state every 50 ms through a new SQLite connection (about 85
  connections per five-minute file); relaxing that would lower app CPU but changes Stop latency,
  so it is left for a separate decision.
- Not measured: a quiet machine, real interview recordings, interactive UI responsiveness during
  a saturated batch, Mac hardware and real provider rate limits.

