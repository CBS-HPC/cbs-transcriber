# Manual transcript editing: initial synthetic pilot

Status: **initial investigation, not completion of the performance audit**. These samples
reproduce slow passage editing on one busy Windows machine; they are too few and noisy for a
p95, acceptance claim, or reliable before/after comparison. No real recordings, cloud jobs,
credentials, model downloads or serving-library writes were used.

## Method

- Application commit `f21933c1d34e6db59ce12ea551c1eda862aac668`; Windows 11, Python 3.13.5,
  Chromium 156.0.8078.4, eight logical CPUs. Other development tests were running concurrently;
  machine load, power state and storage contention were not controlled.
- A disposable independent library and server contain deterministic synthetic Danish/English
  passages, 20 words per passage, three speaker labels and a 1440 × 1000 viewport. The timed
  fixture has 20 valid timed words per passage; the untimed fixture has plain text. Each save
  appends a history version; results are therefore not independent fixed-history samples.
- `open_edit_sync_ms` measures the browser's synchronous click handler, including rebuilding
  the visible transcript, but not a measured screen paint. `save_to_observed_settled_ms` starts
  at the Save click and ends when the browser test observes a later revision and closed editor;
  browser-driver polling is included. `fetch_to_response_ms` is the browser's PATCH-to-response
  interval, not a direct measure of SQLite time. The remainder is response-to-observation and
  includes browser work and polling. No timestamps from different clocks are subtracted.
- For the timed fixture, optional Python wrappers recorded inclusive spans for
  `Store.save_transcript`, `_append_version`, and `snapshot_project`. These spans overlap and
  are **not additive**. They do not separately measure queueing, JSON request/response handling,
  all database reads, or audio/playback. Instrumentation overhead was not quantified.

Reproduce as a small pilot, after checking no important work is active. Use the Python environment
already installed for the app without synchronizing it; do not point the script at a real library:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python scripts/benchmark_manual_editing.py --sizes 100 1000 --samples 3 --words 20
python scripts/benchmark_manual_editing.py --sizes 100 1000 --samples 2 --words 20 --timed --server-spans
python scripts/benchmark_manual_editing.py --sizes 1000 --samples 1 --words 20 --timed --server-spans --structures
python scripts/benchmark_edit_payload_encoding.py --passages 1000 --words 20 --samples 5
```

The command creates and removes its own temporary library and reports aggregate synthetic timing
data only. Chromium for Playwright must already be installed. The benchmark refuses a source
module outside its own checkout. An idle-machine study with the plan's full sample count remains
necessary.

## Observations

| Fixture | Samples | Open edit, ms | Save to observed settled UI, ms |
| --- | ---: | ---: | ---: |
| 100 passages, untimed | 3 | 69–94; median 70 | 266–496; median 364 |
| 1,000 passages, untimed | 3 | 950–1,113; median 1,081 | 2,124–2,577; median 2,227 |
| 100 passages, timed | 2 | 163–217 | 2,390–3,444 |
| 1,000 passages, timed | 2 | 1,852–2,307 | 12,859–18,171 |

For the two 1,000-passage timed saves, PATCH-to-response was 10,797–15,922 ms. Within those
requests, `save_transcript` took 5,721–10,079 ms inclusive: version append took 747–1,283 ms
and project snapshot 1,713–3,562 ms. The remaining response interval includes work outside
that method and transport/serialization. The browser observed the settled UI 2,062–2,249 ms
after the response. Load varied substantially, so these spans are a starting point for tracing,
not a reliable attribution of all elapsed time.

### Payload encoding microbenchmark

The synthetic 1,000-passage/20,000-timed-word result produced a 4,034,737-byte projection JSON
and a 3,614,799-byte canonical history payload. In five busy-machine samples, median times were
359 ms to canonicalize/hash the in-memory payload, 289 ms to encode the projection JSON, and
663 ms to decode the projection and canonicalize/hash it again. A defensive deep copy alone
had a 698 ms median. Each reconstructed canonical payload and hash matched the original exactly.
These isolated measurements exclude database and snapshot work, are not a user-facing latency
estimate and need controlled trials. In particular, a naive deep copy to enable reuse may cost
as much as the repeated conversion it removes.

### Exploratory structural edits

One later synthetic run used candidate `edf0da6` (the isolated index-map change), 1,000 passages
and 20 timed words per passage. After one free-text save it split a different timed passage,
merged two other passages down, merged another pair up, and deleted another passage. Each action
added its normal immutable history version, so history grew throughout the sequence. The machine
was busy and these are **single samples**, not comparisons, p95s or acceptance results:

| Operation | Save action to observed settled UI | Request to response | Store save, inclusive |
| --- | ---: | ---: | ---: |
| Split | 19.3 s | 17.1 s | 7.1 s |
| Merge down | 21.4 s | 17.4 s | 8.7 s |
| Merge up | 19.7 s | 17.3 s | 9.5 s |
| Delete | 19.9 s | 17.5 s | 9.9 s |

Opening each Merge dialog required a separate synchronous transcript render, observed at 1.38
and 1.50 s; those times precede the save-action measurements. Delete includes automation of its
confirmation, and all settled measurements include browser-driver polling. Each Store span
includes its version append and snapshot; there is no cross-clock subtraction. The measurements
identify another reason to trace full-result serialization, history and snapshot work without
deferring durable writes, but do not establish which component dominates on an idle machine.

An isolated candidate avoids that redraw only when no passage editor or draft is open. On a later
busy-machine run with 1,000 timed passages it opened the modal in 473–489 ms across three samples.
Opening a modal can still trigger browser layout; this result is not proof of a controlled
speedup against the earlier two samples. Browser checks cover preserving a declined draft,
discarding an accepted draft, stale merge context, a delayed merge response after navigation,
and restarting uncertainty hints when their first response is still pending.

## Ranked investigation

1. **Passage edit entry and redraw.** `renderTranscript()` rebuilds the whole list when opening
   or saving a free-text edit. It repeatedly looks up passage positions with `indexOf` and
   `findIndex`, making that portion grow quadratically. A separate index-map candidate gave
   1,000-passage untimed open samples of 557–626 ms versus 950–1,113 ms in a separate baseline;
   server response times also changed materially, showing the runs were not controlled. This is
   a promising lead, **not a proven improvement**. Filtered numbering/labels, speaker actions and
   one timed split/merge flow pass synthetic browser checks. Re-run randomized, paired trials and
   preserve keyboard access, focus, playback and conflict handling; coordinate integration with
   the concurrent Working-version UI change.
2. **Durable save and snapshots.** Trace validation, payload encoding/hashing, transaction,
   response encoding and project snapshot separately, including payload sizes and history growth.
   The spans above show that neither version append nor snapshot alone explains the whole request.
   The current snapshot path reads every project recording and live result, regenerates each
   result's NVivo text, and rewrites those outputs and `project.json` for each transcript save.
   Measure scaling with unrelated recordings/results as well as the edited result; the second
   pilot fixture shared its project with the first. Any change must preserve exact output bytes,
   atomic replacement, failure reporting, locks, paths and the existing post-commit guarantees.
   A possible narrowly scoped follow-up is reusing privately constructed canonical payload/hash
   bytes for the *new* version only after proving that the exact SQLite result row equals the
   validated serialized input. First establish stable ownership against concurrent mutation;
   copying the full object is not automatically a speedup. Keep the existing old-head and recording
   mirror integrity checks, role/revision fences, canonical Unicode/number handling, source
   lineage, rollback on any row mismatch, snapshot semantics and every other writer's validation.
   Check invalid/non-JSON inputs, no-op versions, corrupted mirrors, lost responses and concurrent
   edits against the old path. Never accept a caller-supplied digest or move durability after a
   success response. Coordinate with the concurrent Working-version persistence change before
   touching the shared writer. This is a design to evaluate, not a change or a claim of safety.
3. **Structural edits and history.** Beyond the single structural samples above, word correction,
   history restore, search, playback and concurrent editors have not yet been benchmarked. Trace their full
   render and server paths; test long and partial timing maps, Unicode, conflicts and draft
   preservation. Existing speaker rename/assignment redraw improvements have separate synthetic
   tests and should be measured with the same harness.
4. **Complete the planned study.** Generate independent fixed-history fixtures at 100, 1,000 and
   10,000 passages; capture at least 100 warm and 10 cold observations per action on an idle
   reference machine, plus rendering/paint and memory traces. Measure instrumented and clean
   overhead. Include Windows and macOS acceptance before making platform-wide claims.

## 9 November checkpoint: repeated Working payload validation

The recovered editing branch now provides a narrow process-local memo for payloads already
fully validated by the unchanged `decode_payload`. Working-version reads still hash the current
stored bytes on every use, query ownership/Trash/observation metadata and enforce their existing
size and role fences. The memo retains at most 512 digests, no transcript text, and assumes the
validator is unchanged for the process lifetime. Eviction or restart requires full validation
again. Normal decoded reads, Store head checks, writes, snapshots and portable checker sources
remain unchanged. Claude's unfinished projection cache and pre-encoded writer parameter were
excluded from this change.

On Windows 11, Python 3.13.5, eight logical CPUs, nine alternating synthetic samples of a
1,000-passage/20,000-word, 3,614,799-byte canonical payload had a median full validation time of
550.9 ms versus 24.7 ms for a warm verify-only read. Reproduce with
`scripts/benchmark_edit_payload_encoding.py --passages 1000 --words 20 --samples 9`.

A separate Chromium 156 pilot used `scripts/benchmark_editing.py --sizes 600 --samples 5
--structural-samples 1 --opens 1 --actions save`. The baseline replaced `history.verify_payload`
with `history.decode_payload` before importing the app; the next run enabled the memo with the
same seeded fixture. Both appended six total versions. Median input-to-settled save was 10.82 s
for full validation and 8.49 s with the memo; median server request was 8.60 s and 6.26 s.
Runs were sequential on a busy host, with five samples each: this is a promising observation,
not a controlled causal estimate, useful p95 or acceptance result. Save latency still exceeds
the MVP's three-second investigation threshold. Rendering, serialization, snapshots and larger
histories still need the broader study.

Focused synthetic checks cover cold/warm validation, changed bytes, noncanonical payloads,
invalid Unicode, eviction, current metadata/ownership, Working edits and portable Working
authority/receipt verification. No real recordings, serving-library writes or provider calls
were involved.

## 9 November follow-up: separate interactive and background latency

A later synthetic Windows/Chromium pilot measured three observations per action on 100 and
600 passages with 15 timed words each. The shared integration source was based on 20b8ee7,
not a final immutable release. Fixture history grew during the sequence. On this busy host:

| Action | 100 passages, settled median | 600 passages, settled median | 600 primary API |
| --- | ---: | ---: | ---: |
| Open passage editor | 51 ms | 123 ms | No request |
| Save passage | 1.46 s | 6.85 s | 3.90 s |
| Rename speaker | 1.13 s | 7.48 s | 4.76 s |
| Assign speaker | 1.07 s | 9.32 s | 5.25 s |
| Merge | 1.51 s | 10.93 s | 5.26 s |
| Split | 2.08 s | 12.05 s | 4.91 s |

Opening the editor and speaker changes avoid a full transcript render. Mutating actions that
did render 600 passages spent approximately 0.57–0.68 s in that step. A separate three-merge
diagnostic on a fresh 600-passage fixture observed median 9.04 s settled, 4.67 s primary POST
and 0.63 s render. Follow-up undo-options and passage-review GET medians were 3.82 s and
2.79 s. Those requests overlap: do not add their timings or interpret full settlement as the
time typing is disabled. Three samples do not establish p95 or a controlled causal comparison.

The next narrow optimization candidate is repeated bounded historical inverse-proof work,
followed by review-context rebuilding, canonical serialization and project snapshots. Retain
fresh identity/hash/CAS checks and destructive Apply authority; never trade them for a faster
number. These observations do not establish a need to replace SQLite or transcript storage.

The user requested a final audit **after the consolidated MVP is packaged and reloaded**,
before spelling work. Use the exact package commit with separate disposable 100/600-passage
fixtures and a 1,000-passage spot check if practical. Record controls actually editable,
primary API, rendering and background settlement separately. Fix and recheck blocking delays.
This final audit has not yet run. See the [current readiness plan](../work-plans/mvp-ready-2026-11-10.md).
