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
