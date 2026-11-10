# Manual transcript editing: performance audit plan

Status: initial synthetic pilot started; the complete audit remains open. The
[pilot report](../audits/manual-editing-pilot-2026-11-03.md) records the limited, noisy
measurements and next priorities, including one exploratory split/merge/delete run. Measure the
editing workflow on disposable synthetic libraries before choosing implementation changes.
The [source trace](../audits/manual-editing-code-trace-2026-11-03.md) maps each editing action
and ranks further candidates; it does not replace the controlled measurements below.
Keep this work separate from the parked CBS Runtime work.

## Map and measure the current path

Trace a user action from the transcript workspace through `static/app.js` (including
`renderTranscript`, passage actions, selection, search, playback and focus), the matching
`app.py` recording/segment or speaker endpoint, `transcripts.py` editing/structural logic,
and `store.py::save_transcript`, immutable version creation and project snapshot publication.
Measure the response and subsequent client update/paint separately. Map History's explicit
restore separately from browser text undo; do not assume the app has a general Undo command.
Inspect auto realignment only as an optional side effect of a saved free-text edit; measure
its scheduling separately from the edit save, with inference disabled in the benchmark.

Cover opening a result and finding a passage; typing and saving a free-text edit and a word
correction; split at a cursor, merge up/down, delete with confirmation, assign and rename a
speaker; selecting text, keyboard and focus navigation, restoring a history version, and
interacting while playback/follow playback and uncertainty highlighting are on and off. Run
with a second editor, delayed/lost responses and rapid navigation to expose stale saves,
conflicts, duplicate requests, focus loss and retained drafts. Include no-op edits and the
cost of growing version history. Preserve the current behavior if a proposed faster path
cannot maintain these contracts.

## Reproducible benchmark

- Generate local synthetic text and audio only, with repeatable seeds: short (100 passages,
  about 2,000 words), representative (1,000 passages, about 20,000 words), and stress (10,000
  passages, about 200,000 words), with timed, partially timed and untimed passages, multiple
  speakers and long passages. Include Unicode combining marks, emoji, Danish and mixed scripts,
  punctuation, silence, and IDs/order edge cases. Bound disk, duration and memory; document any
  product limits hit instead of silently reducing the workload. Recreate disposable libraries
  for independent runs and retain fixture generator/seed, not real interview content.
- On an idle machine use the same browser viewport, zoom, app revision, Python/browser versions,
  SQLite/journal settings, CPU/RAM/storage and power profile for comparison. Record concurrent
  workload and display refresh rate. Run cold-start samples separately from warm sessions;
  randomize comparable before/after trials. For each workload/action collect at least 100 warm
  samples across several independent runs plus 10 cold samples; report count, p50, p95,
  variability and failures rather than one best time. Check that repetitions do not change the
  fixture/history size unexpectedly. Do not benchmark the serving library or active jobs.
- Mark input-to-first-feedback, input-to-next paint, request round trip, durable commit/response
  and input-to-settled usable UI. Use browser performance traces to separate JavaScript, DOM
  construction, style, layout, paint, long tasks, garbage collection and frame drops during
  typing/scroll/playback. Add opt-in local instrumentation for API serialization, SQLite reads,
  optimistic checks, history encoding/hashing, writer-lock wait/transaction/commit, response
  serialization and snapshot I/O. Record wall/CPU time, bytes and peak memory without logging
  content, filenames, paths or credentials; avoid raw SQL parameter capture. Account for browser
  and server clock differences by reporting local spans rather than subtracting unsynchronized
  timestamps. Quantify observer overhead and repeat key timings with instrumentation off.

Initial investigation targets, **not established results or release guarantees**, are warm
input-to-feedback p95 under 100 ms and settled save p95 under 500 ms for the short fixture,
and under 1.5 s for the representative fixture on the documented reference machine. Record
p50 as well as p95; set stress-size targets after measuring the baseline. No target permits an
early success message before durable commit. Report operations that exceed targets with their
dominant measured costs, uncertainty and device limitations.

## Investigate and validate changes

1. Rank measured bottlenecks and distinguish growing transcript size from growing history,
   snapshot/file-system latency and background playback/other work. Profile scrolling to the
   first, middle and last passage and concurrent short edits; avoid assuming DOM or SQLite is
   responsible in advance.
2. Evaluate the smallest fixes first: avoid repeated full-list scans and duplicate renders,
   update only affected views while preserving text selection/focus, and reduce unnecessary
   serialization or snapshot work only where evidence supports it. Assess bounded rendering or
   virtualization separately: it must retain accessible reading order, keyboard navigation,
   search, playback highlighting, scroll-to-passage and accurate speaker colors. Compare memory
   and CPU, not latency alone. Do not introduce a cache or schema migration without demonstrated
   benefit and explicit invalidation/recovery semantics.
3. Preserve exact text and Unicode boundaries, unselected passage dictionaries, stable IDs,
   speaker labels, original wording, timed/untimed word semantics and source lineage. Every
   committed edit must remain revision/result fenced and atomically reflected in both result and
   active recording with its exact immutable history version; Working pins and independent notes
   revisions remain unchanged. Verify conflict/no-op/lost-response behavior across two windows,
   Undo/history restoration, split/merge/delete, playback state and switch/close with drafts.
   Never move durability work after the success response merely to improve the number.
4. Add meaningful synthetic API, store and browser regressions for each changed path, including
   accessibility, large transcript memory, error/retry and cancellation. Re-run the same
   benchmarks and compare distributions, regression/failure rates and playback responsiveness;
   require existing correctness suites to pass. Keep a short before/after results artifact with
   environment, seed, commands, sample count and limitations. Test on representative Windows and
   macOS devices separately before claiming cross-platform acceptance.
5. Roll out incremental changes only after review and verified results. Keep changes reversible,
   protect pending edits and existing libraries during activation, and make any new persistent or
   concurrency semantics an explicit architecture decision. Track user acceptance separately
   from synthetic and browser evidence.
