# 9 November verification — MVP rc2

This follow-up adds the requested passage review, editing, export and native UCloud reliability
changes to the [rc1 checkpoint](verification-2026-11-09-mvp-rc1.md). It uses the existing Python
environment, synthetic libraries and mocked providers. No new live UCloud allocation, model
download, private recording upload or environment synchronization was used for verification.

## Implemented behavior

- Undo split restores the exact proven original passage while preserving unrelated edits.
- The open passage editor follows saved spoken words without moving the draft's caret.
- Not checked, Checked and Needs review marks have an independent progress counter. Changed
  checked passages need review again. Schema19 preserves marks in complete backups and restore;
  changed marks also invalidate an earlier destructive-operation preview.
- Native uploads and transcript downloads use at most four concurrent file transfers. Accepted
  work drains on failure/Stop; the coordinator retains receipts and imported results. Activity
  distinguishes uploaded, remote processing, download and local save stages.
- Temporary status-poll gateway/transport failures reconnect to the saved remote job with bounded
  backoff. Monitoring failure cannot resubmit or terminate it. Closing the app preserves an accepted
  allocation; after relaunch, explicit Retry retrieves that allocation. General automatic startup
  recovery and a broader cleanup dashboard are deferred beyond this MVP.
- Export Settings selects recordings and multiple formats. ZIPs use safe recording-name folders,
  keep an exact read snapshot and report unavailable format combinations as skips.
- Word-and-timing editing uses one ordinal/word dropdown with an optional Edit word input.

## Verification evidence

| Gate | Outcome |
| --- | --- |
| Final assembled new backend regression gate | 158 passed in 231.29 s: Undo split, exports, review/CAS/performance/deletion, portable review preservation, native transfers/polling/lifecycle |
| Native transfer, capture, matching and polling evidence | 96 unique passing cases across focused runs; an independent reviewer repeated all 21 polling/lifecycle cases |
| Portable compatibility and source claims | 110 broad cases passed, followed by 13 final review/budget cases; catalogs13–18 remain unchanged |
| Undo split and editor-follow browser checks | Five new cases passed, including draft/caret and Working guards |
| Manual review browser checks | Ten MVP cases passed, including stale/failed replies, drafts and persistence |
| Export/browser integration | 17 selected MVP export/edit/navigation cases passed; the two stale-library GET/POST checks passed again with explicit method assertions |
| Word-selector browser checks | Three MVP cases passed; the two affected cases passed again after enforcing zero hidden options |
| Existing timing/editor compatibility | 14 full-profile cases and nine MVP cases passed; five realignment cases intentionally excluded from MVP |
| Existing stale-library module | Passed, retaining draft, API refusal and download guards |
| Static checks | Ruff check/format, modified JavaScript syntax and Prettier3.6.2 passed |
| Packaged MVP variant | 63 profile tests and three MVP browser cases passed; only the main-default-full assertion was excluded here |
| Packaged standalone smoke | Four core checks and bundled FFmpeg passed using the existing Desktop Python and synthetic storage |

The assembled report is `mvp-rc2-integrated-nov9.xml` in the maintainer's Temp folder.
Focused manifests include `native_transfers_polling_nov9_passed.json` and
`browser-manual-review-passed-nov9.json`. This is layered scoped evidence, not a new monolithic
run of every repository test on the final RC. Earlier failed runs remain beside corrected reruns.
Legacy export/timing fixtures were adapted to actual dialog controls without dropping their
scope, timing, draft, caret or Working assertions. B377–B381 record the confirmed product defects.

A five-mark direct component pilot on a synthetic 2,350-passage transcript improved from
2,231.9 ms median to 161.68 ms after bounded reuse of verified passage fingerprints. Every hit
still hashes current bytes and checks live authority/CAS. Cold validation after content changes
remains; busy-host component timings do not establish end-to-end browser latency or p95 acceptance.

## Release and activation

The integration branch starts from main at `418346e`. Main retains the full packaged profile; the
separate local rc2 packaging commit selects MVP. Preserve the Desktop checkout's seven existing
tracked deletions and two stashes. Keep GitHub private and main-only, with an RC tag and local ZIP.

Activation succeeded from Desktop main at `4816c12` with the MVP command-line override and the existing
environment. The verified old console received Ctrl+C and exited normally; no force-stop was
needed. Online and offline checkpoints matched exactly before migration. After startup, all 24
preexisting tables remained byte-for-byte equal under the checkpoint's typed row hashes; all 12
originals and local recovery metadata were unchanged. Schema18 advanced to19 with one empty
passage_reviews table. The HTTP profile/library and served JavaScript/CSS bytes matched the
Desktop source, no local jobs were queued/running, and the new process had no startup errors.

Checkpoint receipts and migration-preservation.json are saved under the private local folder
`.codex/backups/cbs-transcriber/mvp-rc2-20261109T121900Z`. The SQLite copy plus original inventory
is an activation checkpoint, not a complete media backup or tested restore. Seven preexisting
Desktop deletions and both stashes remain intact. GitHub was verified private with only main.

Packaged checks ran at 41d2570, with profile mvp and label MVP rc2. Later activation-record changes
are documentation only; the final artifact manifest identifies the tested and final commits.
The running Desktop main uses the MVP override while retaining full as its packaged default.

The latest failed/cancelled native batch retains remote job12452132 with last-observed RUNNING
state. Every retained remote row was preserved; no activation step retried, stopped, resubmitted
or disposed of it. A stale RUNNING observation is not terminal evidence or cleanup consent.

## Remaining acceptance

Use the [tester guide](mvp-tester-guide-2026-11-11.md) for review marks, Undo split, playback in
the editor, the word dropdown and selected multi-format export. Separately authorized real-provider
checks must cover transient reconnect, close/relaunch retrieval, throughput and long recordings.
Clean Windows/Mac installation, audible responsiveness, real backup/isolated restore and
[NVivo imports](work-plans/mvp-nvivo-test-plan-2026-11-11.md) remain open. CBS Runtime remains
parked. Local spelling dictionaries, the wider editing audit and anonymisation remain on TODO.

## After rc2: manual navigation and original recordings

The user's follow-up changes are implemented in the isolated integration checkout:

- Next to review cycles through visible Not checked/Needs review passages, skips Checked and
  wraps. It remains available with model uncertainty hidden and preserves an open draft/caret.
- Include original recordings is unchecked by default. Chosen originals go once into their
  recording folder in the ZIP; no-transcript rows show a reason and become eligible without
  auto-selection when originals are included. Original-only ZIPs may omit transcript formats.
- Original media is copied through bounded, identity-checked staging outside shared mutation
  locks before ZIP insertion. Missing/changed originals can be reported without partial media
  members or loss of valid transcripts. This uses temporary disk space. Routine eligibility
  metadata uses fixed canonical prefixes instead of parsing full transcript payloads.

Verification: 48 backend/export-selection cases and another 48 legacy export/collection/Working
format/report cases passed; two Windows synthetic symlink setup cases were skipped. Independent
originals checks repeated 18 passes/two setup skips. Twelve
MVP export browser cases, six distinct manual-navigation cases, both legacy export browser
cases and all three assembled MVP walkthrough cases passed. Independent source reviews found
no remaining blockers. Static checks pass; earlier harness failures and corrected reruns remain
in Temp alongside the exact case manifests. B382/B383 record the confirmed behavior/naming gaps.

Logs include `export_originals_backend_nov9.txt`, `browser_export_originals_mvp_nov11.txt`,
`browser-manual-next-passed-nov9.json` and `assembled_MVP_smoke_exports_nov9.xml`.
Large real-media browser/temp-disk behavior and real filesystem-link acceptance remain open.

These follow-ups have not yet been activated. Native batch job12452279 is running, so its
source and worker are preserved while bounded log-progress work and final release checks continue.
