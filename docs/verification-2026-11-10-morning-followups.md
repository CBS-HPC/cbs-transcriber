# Morning follow-ups — 10 November 2026

Activation update: **MVP rc6 634395a** is running at port 8765 from immutable package
`71af74193a05127586bfea4ed181b80d92612508`, based on main code `634395a`. The source-only
checks below preceded activation; real-provider acceptance remains separate.

## Included and checked

- Button captions omit trailing ellipses, including Delete Recording, Library, recovery and
  timing busy buttons. Forty affected browser cases, caption scans, syntax and formatting pass.
- Manual progress, Next to review and uncertainty controls share the available row. Five
  browser checks pass; actual synthetic screenshots at 1600/1000/760/420 pixels confirm wide
  alignment and narrow wrapping without page overflow. An older hint test now uses a valid
  one-word correction rather than an ambiguous rewrite rejected by the existing timing policy.
- Double-click opens Text with the exact mapped word selected. Seven browser cases pass,
  including repeated words, emoji, CRLF, partial/unbounded maps and dirty-draft protection.
  Opening creates no content/Working/history/job mutation.
- Save is disabled for pristine/reverted text. Split is disabled at boundaries, over a range,
  while dirty or during save/structure locks. Cancel replaces one passage instead of rebuilding
  the transcript, retaining unrelated DOM/search marks, scroll, manual controls and playback.
  Six new cases pass on three/600-passage fixtures after five initial failures; six existing
  edit-open/playback cases also pass. Node identity checks establish the avoided full redraw;
  this is not a new representative p95 latency measurement.
- Native log I/O excludes mandatory guard work from its three-second allowance. The exact
  copied-library guard reproduced the timeout with instant correct responses; the deterministic
  regression failed before the change. Seventy-three log/incremental cases and nine independent
  targeted cases pass. All guards, actual slow-I/O refusal and output/cleanup authority remain.
  See [ADR 0160](decisions/0160-native-log-io-budget.md).
- Native output recovery retries only transient reads on the recorded allocation, preserving
  saved siblings and bounded per-member diagnostics (ADR 0162). The broad seven-module run had
  160 passes and one fixture failure: backoff waits were mistaken for remote polls. Corrected
  two-module rerun passed all 55 cases. Poll-oracle/metadata-guard checks pass (five cases),
  as do the final parse/eleven-sibling
  preservation checks (two cases) and independent follow-ups. Real terminal output was verified
  present and parseable read-only; no live result import or new transcription was performed.

Whole-file JavaScript syntax/Prettier and scoped Python Ruff/format checks pass. This is layered
focused evidence, not a fresh monolithic repository suite or real-provider acceptance.

Private receipts are in Temp: browser_review_row_nov10.txt, cbs-review-row-visual-nov10.json,
browser_doubleclick_selection_nov10.xml, browser_editor_controls_green_nov10.xml and
browser_editor_existing_follow_nov10.xml. Caption/uncertainty rerun reports are under the
integration worktree's ignored test-results folder. The native author/independent reports are
retained by their scoped verification runs.

## Activation and remaining acceptance

After activation, the user confirmed the quick functional checks for double-click/Text editor
controls, Checked/Needs review with Next to review, and exports continuing after dialog closure.
The real Retry failed attempt still failed Finn Hansson's existing remote output after three
connection-error download attempts; eleven sibling results remain saved on the same allocation.
This is a failed real recovery check, not an acceptance pass. The user also requested a focused
long-transcript Save/Split speed audit; those measurements and fixes remain in progress.

The follow-up metadata-only native transfer guard removes per-block sibling transcript/Working
hydration while preserving fresh explicit checks (ADR 0163). Its new regression failed against
rc5; the current recovery/publication gate passes all 58 cases, including the streamed
twelve-member and changed-project checks. Independent review and scoped Ruff/format checks
pass. Private reports: Temp/native-guard-red-nov10.xml and native-guard-green-nov10.xml.
This fix is loaded in rc6; real Retry failed acceptance remains pending.

The [Save/Split audit](audits/rc5-save-split-speed-2026-11-10.md) is complete. At 1,800 synthetic
passages, n=3 controls-ready medians were 16.36/14.91 seconds; at 100 passages both were below
one second. Full responses, repeated history serialization/validation and duplicate renders
are identified. No Save/Split optimization is included in this native fix. Backend and frontend
follow-ups are being implemented on separate isolated branches; they are not loaded or merged.

### rc6 activation

The idle rc5 process shut down gracefully without a force-stop, with no active jobs/batches or
prepared/active export tasks. Online/offline/post checks preserve all database tables and 24
originals exactly; database SHA-256 remains
72ffdc06814461fd9aebf4145b0ae8de02d79576a01eac3b378d002114932055.
Private receipts: Temp/rc6-http-activation-verification.json and
.codex/backups/cbs-transcriber/mvp-rc6-20261110T104418Z/offline-post.json.

The served MVP label and four static asset bodies match immutable package 71af741; all four
responses use no-store. The package contains no spelling feature. The 63 profile cases pass
with one intentional main/full-default assertion deselected, three MVP browser checks pass,
and all four existing-environment installation smoke checks pass. These do not establish a
clean-machine install or real-provider recovery acceptance. The source ZIP passes CRC, paths,
source equality and 20 portable pins: 878 entries, 5,400,121 bytes,
SHA-256 4d149478869161df6ff586f0509d9ee8118953661715eff3d46ea318f74dc778.
No serving environment was synchronized and no public release was published.

### Previous rc5 activation

After API and database checks found no active jobs, the previous process shut down gracefully.
Online/offline/post checkpoints preserved every database table and all 24 originals; the
offline/post database SHA-256 is unchanged. No serving environment was synchronized. The
served label/profile and exact bytes of app.js, app.css, manual_review.js and deleted_passages.js
match the immutable rc5 package; responses use no-store. The package contains no spelling.js.
Private receipts: Temp/rc5-http-activation-verification.json and
.codex/backups/cbs-transcriber/mvp-rc5-20261110T091638Z/offline-post.json.

The source ZIP passed CRC, path, source and 20 portable-pin checks (881 entries, 5,464,615 bytes;
SHA-256 c0008a51009fb053394574e882f3d4a71d0eba9854cc3886d4eceae37abd815b).
The packaged profile passed 63 tests with one intentional full-default assertion deselected;
three MVP browser walkthroughs and all four installation smoke checks passed. These checks
used existing interpreters and do not establish clean-machine Windows or Mac acceptance.
The local archive is in CBS Transcriber/Releases/mvp-2026-11-10-rc5.zip; no public release was
published. The separate spelling preview is closed, unmerged and parked until after MVP.

The [tester guide](mvp-tester-guide-2026-11-11.md) covers editing, undo/restore, review marks,
Working, exports and recovery. The [dependency audit](audits/task-dependencies-2026-11-10.md)
records remaining composed-workflow and shared-resource checks. Real early downloads,
representative throughput and final release/installation/documentation decisions remain open.
