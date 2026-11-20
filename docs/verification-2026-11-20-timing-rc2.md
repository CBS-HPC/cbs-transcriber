# MVP v2 timing reliability candidate — 20 November 2026

Local branch `codex/weekend-20261120`, based on `0d871d7`. Claude's timing WIP from
`6468632` was preserved into this isolated worktree; its original worktree was not edited.
ADR numbering was reconciled with the independent spelling work: timing uses ADR 0189/V241,
while ADR 0186/0187 and V237–V240 remain available to the spelling/editor track.

## Changes and scope

- Advisory Windows progress reads tolerate transient unreadability; writes retry briefly.
  A separately locked pending name is retained for the next write. Required output JSON
  continues to propagate file errors (B468/B481, V242).
- Activity distinguishes busy-file, filesystem and missing-data processing failures, and
  reports Aligning words before aligning the first window (B469/B470).
- Fresh passage timing refuses only windows whose pinned wav2vec2 frame upper bound cannot
  contain the normalized text. Repair all leaves those windows out; frozen units skip them
  without launching a worker or decoding audio and retain truthful outcomes. No interval is
  widened and no timing is invented (ADR 0189/V241).
- The synthetic cancellation worker publishes its child PID atomically (B486). Application
  cancellation code, five-second test deadlines and process-tree assertions are unchanged.

## Frozen core evidence

The final2 gate passed **377 backend cases** in 300.22 s and **17 browser cases** in 173.72 s.
All **599 captured source/test hashes** remained unchanged through both groups, Ruff check,
Ruff format check (11 files) and pinned Prettier 3.6.2. This is focused regression coverage,
not a claim that the entire repository suite was repeated.

Backend coverage includes passage admission/frozen skips, warm-worker lifecycle, queue errors,
worker stage order, model reuse, timing capture/atomic Apply, legacy timing-policy oracles,
manual timing routes, history/report/portable semantics and bounded launcher shutdown.
Browser coverage includes timing tags/bulk counts, inline word timing and Text timing retention.
The launcher test emits two FastAPI on_event deprecation warnings in its test fixture.

Independent Windows verification passed the real-held-reader progress test. Both process-tree
cancellation variants passed three fresh subprocess runs each after the fixture correction.
An isolated widened publication race captured the empty-PID ValueError before the correction;
the atomic counterpart passed with the original deadlines. Some earlier startup failures were
not causally attributed, so they are not all relabelled as the same fixture defect.

Earlier sandbox-only failures refused traversal of a Temp parent; host runs retained the
production guards. Earlier deeply nested capture fixtures exceeded Windows path limits and
passed on short disposable paths. These changes did not bypass application file guards.

## Final frontend parity and packaging

The NEL/BOM/file-separator eligibility correction passed the final **22-case browser gate**
in 181.97 s, including five real UI/API boundary cases and retained astral-code-point counts.
All 599 captured hashes remained unchanged during that gate; only app.js and its browser
test differ from final2, and all 115 backend Python modules match the 377-pass receipt.
Thus the final focused composition covers **399 distinct backend/browser cases**, with
overlapping reruns excluded. Node syntax, scoped Ruff and pinned Prettier pass.

The test file's pre-existing CRLF endings were restored after the 22-case run with identical
AST and LF-normalized bytes. The five added cases passed again (41.75 s), with all product
bytes unchanged and a new stable 599-file receipt. No deadline/assertion was weakened.

Pending: separate local 0.2.0rc2 packaging checkout, wheel/constraints generation and byte checks.
Do not describe packaging as passed yet.

## Acceptance limits

All processing/provider tests use synthetic libraries, stubs or mocks, with no model downloads
or real UCloud calls. A real WhisperX run on the user's audio remains H15a. Clean Windows/Mac,
shortcut clicks, installed-worker and real provider acceptance remain separate. Main, the
installed app, existing environments, libraries, original Claude worktrees and remote jobs
were not changed. No push, publication or preview reload occurred.
