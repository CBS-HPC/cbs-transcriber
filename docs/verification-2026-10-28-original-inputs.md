# T87 — Original-source preparation and report evidence

28 October 2026. Development-branch evidence for ADR 0075. The running app remains on
`c3c94e6`; this work has not changed its server, library, jobs or provider settings.

## Delivered stage and activation hold

Fresh whole-recording transcription, whole-result enhancement and their whole-result
follow-ups now build an `original-v1` source observation from the immutable managed original.
API chunks and local/CBS Runtime WAVs derive directly from it. Native Transcriber continues
to upload the original. Official OpenAI can receive eligible original bytes when format,
single-stream and upload-limit checks permit; otherwise its frozen transport uses chunks.
Existing jobs without this policy retain their exact legacy source and retry behavior.

The source observation records physical bytes/hash, explicit first audio stream, normalized
sample clock, actual decoded frames/PCM hash, decoder/recipe identity and a separate container
duration observation. It never rewrites `recordings.duration`. Conversion receipts bind actual
prepared bytes and PCM geometry to exact source sample windows; conflicting same-scope receipts
are refused. Full completed API checkpoints can be finalized without today's source, decoder,
provider or key; unfinished work still needs its unchanged frozen source.

**This is not a release-ready readiness change.** The current first-stage HTTP enqueue still
performs source inspection synchronously and still requires playback readiness. T87b must move
inspection into a durable background lane and materialize frozen requests without blocking the
request lifecycle (V8). Do not activate this stage as the completed preparation fix. The
[queue proposal](proposals/original-readiness-queue-2026-10-28.md) records the required next
slice. Selected compatible native inputs must retain one batch allocation, including when
some source inspections finish later.

Bounded timing, selected-passage enhancement, passage re-transcription and incidental edit
realignment retain their documented legacy paths until cached source inspection and bounded
bundle integration can establish their mapping without decoding outside the requested scope.
Remote original/compressed decoding is also separate; the worker WAV ABI is unchanged here.

## Report behavior

Metadata-first research reports validate saved source ownership and every preparation binding
inside their read snapshot. They emit aliases, hashes, sizes and sample/window observations,
with a separate 32 MiB context budget checked before loading metadata. They open no recording,
decoder, runtime or provider. Internal IDs, filenames, paths and free-text decoder output are
omitted. Local decoded PCM observations do not establish a remote service's internal processing.
Missing preparation evidence, legacy sources and unchecked retained-file availability remain
explicit gaps. Existing report payload and import-source budgets remain separate.

## Verification

Independent wiring/recovery and codec reviews approved the scoped source implementation;
the report implementation also passed independent review. Focused evidence overlaps and must
not be summed as a whole-suite total:

| Scope | Passing evidence |
| --- | --- |
| Source helpers, recovery and jobs | 46 passed; one Windows symlink-permission skip |
| Final fresh/API/follow-up integration | 43 passed |
| Native, Runtime, WhisperX and bounded compatibility | 106 passed |
| Conflicting same-scope receipts | 9 passed |
| Completed-result recovery | 5 passed |
| Protected approval revoked during path validation | 1 passed |
| Physical compressed/priming/discontinuity/chunk matrix | 10 passed |
| Final truncated-MP3 and complete/spoof-positive checks | 10 passed |
| Original-input report and affected history/report cases | 134 passed across two focused runs |

The report stage adds 17 cases. Physical media cases cover complete supported compressed
containers, AAC/MP3 priming, explicit video offsets, discontinuities, silence-aware chunk
offsets and an MP3 cut after complete frames. FFmpeg can exit zero on that last truncated
input; the narrow demuxer completeness diagnostic is now refused without duration heuristics
or mistaking a media title for a diagnostic. AAC conversion avoids a redundant zero seek
which changed PCM despite preserving frame count. Logger parsing accepts both Windows and
POSIX pointer forms.

Relevant reproducible scopes use repository-local Python without changing the active environment:

```text
uv run --no-sync pytest tests/test_audio_inputs.py tests/test_original_input_recovery.py tests/test_original_input_integration.py -q
uv run --no-sync pytest tests/test_audio_input_reports.py tests/test_version_history_review.py -q
```

Ruff/format and scoped whitespace checks passed. Test fixtures are synthetic or the existing
licensed public subset. No paid inference, private audio inspection, model download, UCloud
allocation, remote transfer, dependency installation or running-app restart was involved.
