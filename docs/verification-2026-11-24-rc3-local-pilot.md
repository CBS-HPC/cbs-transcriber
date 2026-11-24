# MVP v2 rc3 local pilot verification

24 November 2026. The local wheel packages source
`d993fccbe2183a9b055b0faa1097ab6034145d98`, including WebSocket redirect
refusal B587. It has version `0.2.0rc3`, packaged profile `mvp`, and label
**MVP v2 rc3**.

## Package and fresh Windows install

- The offline build validated all 164 archive entries against the wheel
  RECORD and matched the packaged product source.
- Wheel SHA-256
  `a989969a15d401f79525ab01a0d9720ea0c565dd47b664e92295bd1a78f2a5aa`.
  Matching constraints SHA-256
  `b94157af4ef0f45c1b763f8692d4d19c5d3682f10b970398aa2eef990cc0f6c8`.
- A fresh app environment on the development Windows PC installed managed
  Python 3.13.11 and all 136 constrained packages. Dependency consistency
  passed. The installed app accepted a synthetic WAV and transcript, saved
  typed word times, exported the edit and reopened it after Ctrl+C and
  restart.
- The local preview is at http://127.0.0.1:8765/. Its served HTML, JavaScript
  and CSS match the wheel. It uses a separate pilot library and session-only
  credentials. The first readiness probe timed out under regression load.
  The existing launch then served and passed all asset checks.
- The [colleague pilot guide](testing/colleague-test-guide-0.2.0rc3.md)
  covers install, startup, a Desktop shortcut and the target workflows.
  Managed Windows permission and shortcut acceptance remain open.

## Browser regression

The original Library refusal case reproduced a failure because its isolated
fixture mistook the backup-list read for a pending create and lacked
`crypto.randomUUID`. The B588 test-only correction provides a UUID and waits
for the backup-task POST before checking the Create button and the original
disabled-state assertions. The corrected case passed in Chromium on identical
packaged product bytes. Fixture lint and format checks passed.

The frozen broader regression is tracked separately. Its inventory is
7,146 backend and 759 Chromium cases. The full-default catalog contract passed
in its separate scratch configuration. Complete suite outcomes are pending
at this checkpoint. The wheel and this record remain local.

## Model defaults follow-up for the next local rc3 build

The loaded pilot saved all six native UCloud models as enabled on its first
connection. The first-use browser case reproduced that result with a synthetic
account. B589 sends the catalog's native defaults through the existing
version-checked Settings save. Fresh connections enable Large-v3, Large-v3-turbo
and NVIDIA Parakeet v3. Large-v3 stays preselected and saved choices are preserved.

Seven focused Chromium cases passed in the MVP profile and 20 provider/catalog
backend cases passed. These checks qualify the local source follow-up. The
already-loaded pilot and its wheel are the earlier build. Recompilation is held
for further testing.

### Revised fresh model selection

The final requested defaults enable Large-v3-turbo and NVIDIA Parakeet v3.
Turbo is selected initially. Large-v3 is unchecked and remains available in
Settings. Saved connections retain their choices.

Forty-nine backend cases passed. Four Chromium scenario assertions passed.
One test server exceeded its teardown limit after the first-use case. Waiting
for setup readiness exposed an obsolete hidden-form expectation after clearing
the key. The test now verifies the documented reminder and passed a clean
isolated rerun. The running pilot still serves the earlier wheel.

## Passage Undo and Redo follow-up

The local colleague candidate adds **Undo / Redo** to a passage row. It steps
through verified text, word-time and eligible speaker-assignment edits. The
recording toolbar and Restore original stay available. Structural edits and
other changes without a proven passage scope bound the row history.

The focused gate passed 64 backend cases and seven Chromium cases in the MVP
profile. The new browser case interleaves saves in two passages, then walks two
Undo and two Redo steps in one row. The other row keeps its saved edit. The
first browser run caught a row closure after a successful undo. B591 fixes the
continuation by following the editor token from the committed view. The final
gate also covers stale and late replies, draft safety and word-time Undo.

These results apply to source on `codex/rc3-passage-history-20261124`. The
local wheel and running preview still use the earlier package above. Build and
fresh Windows installation of this follow-up remain pending.

## Failed transcription status on the project page

T167 and B592 use the newest visible per-file failure from Activity. A recording
with no transcript that would otherwise show Ready to transcribe displays
Failed - See Activity. Cancellation or dismissal clears that badge in place.

Seven Chromium cases passed in the MVP profile on this product source. The
synthetic native batch showed two failed badges and returned both to ready after
one mocked Stop. An earlier failed attempt did not replace the cancelled one.
The test also checks dismissal and reload. The existing retained cleanup case
passed after scoping its locator to the group summary (B593). Provider calls
were mocked. The earlier rc3 wheel remains the loaded pilot.

## Confirmed cleanup of an ended native batch

T168 and ADR 0197 add a saved/unsaved count and explicit confirmation to
**Retry remote cleanup** when a verified ended native batch has unsaved
outputs. The confirmation keeps local recordings, results, original response
files and versions. Only the exact UCloud working folder is moved to Trash
and verified there. The old Retry is closed and the resolved Activity item
can be cleared. No live UCloud action was taken for this change.

Three focused backend cases and six distinct Chromium cases passed with
synthetic libraries and mocked provider responses. The new backend case
proves the side-effect-free preview, stale fingerprint refusal, suspended
remote refusal, saved output preservation, protected cleanup scope and
idempotent release replay. The Chromium test verifies both dismissal and
confirmation, the unsaved count and the resolved Activity state. Its first
run raced the asynchronous dialog. B595 fixes the locator timing only. The
running preview uses the earlier package.

## Broad gate attribution

The exact-wheel broad gate on `d993fcc` ran three lanes in parallel for 4 h 12 min.
Backend: 7,114 passed, 26 skipped and 5 failed of 7,145. Chromium: 750 passed,
9 failed and 1 teardown error of 759. The separate full-default catalog case
passed. Claude reran every failing case on its own, one lane at a time, on
`d993fcc` and on `b7c006f`, and the reproducible ones on main `021d576`.

- The five backend failures passed in isolation on both sources: three Runtime
  batch waits, one Runtime preparation cancel wait and the hung spelling-lookup
  time bound. They are timing limits exceeded under load.
- The Library refusal case is the B588 fixture correction; it passes on `b7c006f`.
- Four Chromium cases passed in isolation and are load-related: inline replay,
  the first-use MIG save, the Runtime backend guard and the Runtime consent toast.
- Two cases also fail intermittently on main, so they predate rc3: playback-
  independent admission (`[error]` or `[preparing]`) and the speaker-label
  follow-up (`selected-json-speaker_labels-*`), whose transcription job
  sometimes fails with "The Working authority differs from its admission."
  That race is under separate investigation.
- Two are rc3 regressions, fixed here: B596, where a text-only Save passage was
  refused after any recording refresh, and B597, where the Settings tab row was
  too wide at 390 px. The focused rerun also found B598, a stale large-v3
  expectation after the Turbo default change on the candidate.
- The teardown error (the language-save case's server still running 10 s after
  stop) reproduced in both isolated reruns. Those ran while a heavy job from
  another project was using the PC, and took 9-11 min. It passed 10 later
  isolated runs and the full rerun below. It is attributed to load-sensitive
  shutdown timing.

After the fixes all 25 previously failing nodes passed in one session (5 backend
and 20 Chromium, 2 min). The word-times editor, result-trash, Hugging Face Activity and
first-use modules (61 Chromium cases) and four further Settings modules passed. The new B596 regression
fails on the unfixed file. These results qualify source, not the installed
`d993fcc` wheel, which stays loaded until a rebuild is agreed.
