# Manual transcript editing: source trace and next optimizations

Originally reviewed main `5051a6b` against the [synthetic timing pilot](manual-editing-pilot-2026-11-03.md)
and the [audit plan](../work-plans/manual-transcript-editing-performance.md); implementation
notes reflect main through `02315b3`. This is a
code-path audit, not a full measured performance acceptance or a finding that all editing
behaviour is correct. Existing measurements use noisy, busy-machine synthetic fixtures; no
private recordings, serving-library writes or cloud requests were used in this review.

## Action map

| Action | Current path and performance implication |
| --- | --- |
| Open free-text Edit / Cancel | A normal Edit replaces only the selected passage text with its editor when no editor, draft or comparison is active; other cases retain the full redraw. Late hint replies do not replace an open editor. Cancel still rebuilds the transcript. Focus, playback, uncertainty and draft handling need broader acceptance. |
| Save passage / correct a word / restore original | `PATCH .../segments/{id}` reads the recording, changes one passage, saves the full result and returns the full current recording. The client then rebuilds details and transcript. A free-text change can also queue an optional realignment; its cost must be measured separately. |
| Split / merge up or down / delete | Each structural API reads the current recording, derives the full replacement passage list and calls the same versioned writer. The response triggers a full transcript/comparison refresh. Merge opens a modal without rebuilding the transcript when no editor or draft is open; dirty-editor cases retain the full redraw. |
| Assign speaker / rename speaker | Both use the same full-result save and project snapshot; the frontend already has a guarded badge-only refresh when the saved revision and UI state permit it. Comparison, open editors and other cases use a full redraw. |
| History and restore | Version-list pages return metadata in pages of 25; inspecting a version fetches and verifies its full immutable payload. Restore reads the chosen payload and appends a new version through the same writer. History is not a general Undo operation. |
| Search and uncertainty | Search input synchronously filters/rebuilds the transcript when no draft is dirty. Each redraw recomputes rank-based hints, including a global sort, and timed words produce individual DOM buttons. Benchmark these separately before changing debounce, ranking or rendering. |
| Playback | Passage lookup uses a cached interval index and binary search. The index is per segments-array identity; optimizations must preserve partially timed words, silent gaps, replay boundaries, follow-playback and navigation. |

The main paths are `static/app.js` (`renderTranscript`, `renderPassageText`, `patchPassage`,
`changePassageStructure`, `refreshSpeakerBadges`, `loadHistory`), `app.py`'s segment/speaker
and version-restore routes, `transcripts.py`'s edit/split/merge/delete routines, and
`store.py::save_transcript`. The writer validates and hashes the new payload, checks the old
head and recording mirror, writes the result and recording with a revision fence, appends an
immutable version in the transaction, then publishes the project snapshot and returns a full
recording. `snapshot_project` reads every recording and live result in that project and rewrites
its readable outputs. These checks and outputs are part of correctness, not optional delay.

## Ranked follow-up

1. **Cached review hints (implemented):** on a cache hit, `review_hints.hints_for` previously fetched and
   decoded the complete result, and `current_hints` fetched it again. The hints query already
   joins result to recording; the second read is the one whose current text hashes filter
   stale hints. The first result read now occurs only on a cache miss. Focused synthetic tests
   cover wrong-recording rejection, edited text, and a transient rebuild error. Benchmarking
   the end-to-end effect and broader cache cases remain open. This does not remove current-text
   checks or authorise reuse across results or revisions.
2. **Repeated client work:** profile transcript node construction, uncertainty ranking,
   hint interval overlap scans and search independently. `renderPassageText` scans hint
   ranges for each timed word and plain-text interval; any sweep/index must preserve the
   existing first-match behaviour for overlapping or unsorted ranges and Unicode code-point
   offsets. Cache or coalesce only with exact revision, search and uncertainty invalidation.
   A 3 November exploratory Chromium microbenchmark compared the existing renderer with a
   temporary binary lookup for sorted, disjoint hint ranges; the candidate fell back to the
   original scan for overlapping, unsorted or malformed ranges. In 120 synthetic cases,
   including Unicode, missing timing and those fallback cases, the generated markup matched.
   The benchmark warmed both renderers, alternated their order and took the median of nine
   rounds. For 300 renders of a 20-word passage with two marked ranges, the existing renderer
   took 32.2 ms and the candidate 33.9 ms. For five renders of a 1,000-word passage with 100
   ranges, they took 47.1 ms and 28.1 ms respectively. The latter saves about 3.8 ms per
   synthetic render on this host, but normal passages were slightly slower in this sample.
   These are noisy in-page measurements, not full-transcript paint, real interview traces,
   concurrent workloads or cross-platform acceptance. The candidate was discarded: its narrow
   synthetic benefit did not justify adding a new matching path before representative profiling.
   Ranking, search and DOM construction remain separate open measurements.
3. **Merge modal (implemented):** opening it avoids a full redraw only when no
   passage editor/draft is open, while starting a replacement hint load after `clearEdit`.
   The Working-choice prompt is integrated and awaited before clearing the editor; cancellation
   preserves the draft and Working role. Synthetic browser checks cover the prompt, redraw,
   late hints and draft handling. Broader exact-source and real-use performance acceptance remain
   open; the implementation has not yet been activated in the running app.
4. **Durable writer and project snapshot:** profile independent project sizes and histories.
   The pilot shows repeated payload conversion and whole-project snapshot generation, but
   does not justify skipping integrity checks or deferring publication. Test corruption,
   exact Unicode/number bytes, concurrent edits, snapshot failure reporting and rollback before
   any reuse or incremental snapshot proposal. Preserve the implemented atomic Working-version
   contract; neither job authority nor immutable evidence may be weakened.
   A later change reuses decoded segments and speaker names only where the active saved result
   and recording mirror have exactly equal stored JSON. The reuse is confined to one database
   snapshot; nonmatching or unavailable results are decoded independently, and all readable
   files are still regenerated. Synthetic tests compare exact output bytes and cover corrupt
   mirrors, failed writes, concurrent database changes and Trash. A six-round alternating-order
   trial with 1,000 synthetic passages per recording measured a median 73.94 ms before versus
   76.59 ms after for one recording, and 470.86 ms versus 426.08 ms for six recordings; output
   hashes matched in every round. These noisy local measurements do not establish real-user
   latency or a general speedup. Full snapshot and history profiling remains open.
5. **Complete acceptance:** benchmark word correction, speaker updates, History preview/restore,
   typing, search, playback and two-window delayed/lost replies on independent 100/1,000/10,000
   passage fixtures. Capture paint and p50/p95 under controlled conditions and test Windows and
   macOS separately. Existing small exploratory samples cannot establish a speed guarantee.

Relevant existing checks include `tests/test_review_hints.py`, `tests/test_browser_passages.py`,
`tests/test_browser_speaker_rename_performance.py`, `tests/test_browser_render_numbering.py`,
structural transcript and result-history suites, and the synthetic benchmark scripts linked in
the pilot. Changes still need their own meaningful tests and exact integrated-source review.
