# Focused rc5 Save passage / Split audit — 10 November 2026

The user's long-transcript slowdown is reproducible in immutable MVP rc5. All six
1,800-passage observations exceeded three seconds; the six 100-passage observations
stayed below one second. This is a size-dependent performance failure, with content
and timing correctness checks passing. It is not evidence that the split operation's
text-boundary calculation is slow.

## Exact scope and source identity

- Package: `6eb908cc0da860acdab63e62f78f13a2505baaf0`; code:
  `89c196dabcbb52fe5d340338755fd99a6048c48b`.
- Measured source: `C:/Users/kgp.lib/.codex/worktrees/mvp-2026-11-10-rc5/CBS Transcriber`.
  Source inventory verification passed after each process; its Git worktree remained clean.
- Windows 11, Python 3.13.5, eight logical CPUs, Chromium 156.0.8078.4. Existing developer
  interpreter only; no environment synchronization or installations.
- Separate 100/1,800-passage processes, fifteen seeded Danish-like words per passage,
  three speakers, three observations per action. Each fixture has one recording and one
  result, ending with seven content versions. Larger projects/histories were not simulated.
- Only Save passage and Split at cursor were measured. Disposable Home/library/project,
  memory-only credentials and a random loopback port; no live library, port8765, provider,
  private media, model download or running-app mutation.
- Controls-ready means an enabled real editor accepted unsaved text; that probe was then
  cancelled and checked not to change saved text/revision. Background settlement includes
  the probe's requests and is an upper bound, not the duration for which editing was blocked.
- Timings start after preparation has opened the editor, changed text or positioned a valid
  caret, and enabled the relevant button. Three samples are not p95 or a machine-independent
  latency guarantee. No production/test source was changed for this investigation.

## Observed baseline

Seconds, medians unless a range is shown. Nested server spans must not be added.

| Passages | Action | Actual input ready (range) | Action ready | Primary API | Render total | Background with probe |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 100 | Save | 0.735 (0.668–0.749) | 0.686 | 0.475 | 0.096 | 1.130 |
| 100 | Split | 0.654 (0.595–0.782) | 0.605 | 0.385 | 0.094 | 1.201 |
| 1,800 | Save | 16.362 (9.625–16.928) | 15.988 | 12.566 | 1.745 | 26.108 |
| 1,800 | Split | 14.910 (13.861–15.581) | 14.486 | 10.325 | 1.961 | 29.064 |

The long mutation responses are about 4.94 MB, versus 268 KB for the short fixture.
Each measured mutation caused two full transcript renders. Save's longest browser task
median was 2.572 s; Split's was 3.203 s. Render totals include the full measured interval,
including hint arrival and the input probe; they are not all necessarily before input readiness.

Save preserved the original intervals of retained words and left the appended word untimed;
no enhancement job was created. Split's saved two passages matched the exact production
transform after binding its generated right-hand ID. Input probes preserved saved content.

## Primary API work and call counts

The existing request instrumentation observed the following counts in every long request:
`Store.recording` twice, `Store.save_transcript` once, `_head_version` once,
`_append_version` once, `snapshot_project` once, `json_response` once, and two calls through
the Store module's `encode_payload` alias. That alias count does not include history-module
canonical validation. Split's actual transform took a 3.7 ms median.

| Long-request span | Save median | Split median |
| --- | ---: | ---: |
| Store.save_transcript, inclusive | 11.004 s | 8.601 s |
| Direct snapshot_project child | 3.094 s | 2.340 s |
| Store.save_transcript excluding that child, computed per sample | 7.909 s | 6.346 s |
| Recording projections, aggregate | 1.915 s | 1.352 s |
| JSON response encoding | 0.599 s | 0.664 s |

Removing or deferring snapshots alone would neither explain nor solve the remaining writer
cost, and would change an accepted durability guarantee. It is not proposed here.

## One accepted backend CPU profile

A separate disposable 1,800-passage fixture profiled exactly one direct
`Store.save_transcript` using `cProfile.Profile(timer=time.thread_time)` while other verification
could run. Its wall time is not an uncontended comparison and its instrumented CPU totals are
not end-to-end latency. The first diagnostic attempt was refused before the writer because the
temporary harness used the wrong timing-policy literal; the accepted run imports the actual
`TEXT_TIMING_POLICY` constant.

- Store.save_transcript: 5.734 s cumulative thread CPU; its direct snapshot child: 1.563 s.
  The remaining writer CPU is 4.172 s.
- Nine JSON dumps: 2.313 s cumulative CPU; thirteen JSON loads: 1.328 s. These overlap
  the named callers below and must not be added to them.
- Three canonical `history.encode_payload` calls: 1.281 s. Two belong to `_head_version`
  and `_append_version`. The third belongs to one `decode_payload` call (0.766 s total,
  including its 0.438 s canonical encode).
- That immediate cold revalidation follows
  `working_manual.apply -> result_roles.move_automatic -> version -> verify_payload ->
  decode_payload`, despite the successor having just been canonically generated.
- `verify_payload` ran five times; all fresh actual-byte hash checks remain necessary.

This supports testing a narrow optimization: register successfully generated canonical bytes
in the existing bounded digest-only validation memo. Keep fresh hashes on every later read,
no cached mutable payloads, and all ownership/Working/revision checks. Cache seeding must happen
only after successful serialization, UTF-8 encoding and hashing; malformed/noncanonical input,
changed bytes, foreign ownership, rollback and eviction need focused negative tests. Any pinned
portable checker sources affected by a future change must be reviewed/repinned. No such change
has been made as part of this audit.

## Scoped frontend / response causes

- [Save route](../../src/cbs_transcriber/app.py#L1899) and
  [Split route](../../src/cbs_transcriber/app.py#L2392) return a full recording projection.
  [Normalized passages](../../src/cbs_transcriber/transcripts.py#L334) retain both current
  `words` and a copied `original_words` array; the default response carries both arrays for
  all 1,800 passages, although this mutation changes only one or two passages.
  [Store.save_transcript](../../src/cbs_transcriber/store.py#L1413) still performs a full
  post-commit recording read and synchronous project snapshot before returning.
- [patchPassage](../../src/cbs_transcriber/static/app.js#L6998) and
  [changePassageStructure](../../src/cbs_transcriber/static/app.js#L7400) rebuild the whole
  transcript after the response. A one-passage edit therefore rebuilds the other 1,799
  passages and their 27,000-word UI.
- [loadHints](../../src/cbs_transcriber/static/app.js#L5802) can perform another whole render
  on a fresh revision when `previousHints` is omitted. The existing speaker-update path passes
  previous hints to suppress an unchanged repaint. Changed hints need a genuine refresh;
  retaining stale revision hints is not an optimization.
- Each measured interval had three hints GETs, one undo-options GET and one passage-reviews
  GET. The extra hints requests include the input probe opening/cancelling while the first
  hints request is outstanding. This is a concrete missing in-flight coalescing case, not a
  claim that an idle user Save always launches three hints requests. The probe makes the
  background totals explicitly perturbed.
- No compact editor-response helper was found in rc5. An optional compact projection alone
  saves response serialization/wire/parse work; it does **not** remove the Store read already
  paid. A true Save/Split delta needs a strict recording/result/base-revision envelope,
  exact one-/two-passage splice and refreshed warnings/Working/metadata. Keep existing API
  defaults. Prefer an explicit header/body opt-in: app guard regexes anchor mutation paths,
  so appending `?compact` without adjusting them can bypass intended Working/language checks.

Recommended sequence: prove the small canonical-generation memo optimization; coalesce
same-revision in-flight hints without weakening late/draft guards; then review a bounded editor
response and in-place one-/two-passage render. Preserve full history, immutable originals,
Working fences, synchronous snapshots and the default public API throughout.

## Reproduction and evidence

Retained private Temp artifacts:

- `editing-save-split-rc5-audit-nov10.py`, SHA-256
  `cdd7057bd024fc78aec6c5653228efb2ae6f683c242b237b1a16c59204b76c43`.
- Its reused `editing-candidate-audit-nov9.py`, SHA-256
  `bb767a1922be181bfb4701922e62d1c91322baf4ddbadf46a6327042c60060b8`.
- `editing-save-split-rc5-{100,1800}-nov10.json` and `.log`.
- `editing-save-cpu-1800-rc5-nov10.py` and `.json` (accepted CPU profile).

Run from the exact immutable rc5 folder with the existing developer interpreter,
`BENCH_REVISION=6eb908cc0da860acdab63e62f78f13a2505baaf0`, `CBS_TRANSCRIBER_PROFILE=mvp`,
startup/remote-release checks disabled and `PYTHONDONTWRITEBYTECODE=1`. The wrapper invocation is:

```text
python <Temp>/editing-save-split-rc5-audit-nov10.py --sizes 1800 --samples 3
  --structural-samples 3 --opens 1 --words 15 --speakers 3 --actions save structure
  --label rc5-save-split-1800 --out <Temp>/editing-save-split-rc5-1800-nov10.json
```

Use `--sizes 100` in a fresh process for the short case. A three-second regression gate is
red when either action's `editable_over_3000ms` is nonzero; the baseline is 3/3 for both long
actions, 0/3 for both short actions. Source inventory SHA-256 after both measured runs is
`0ff562436db78af89a59dbeb03e096be41afd902c9e0c364816925909020db5c`.
