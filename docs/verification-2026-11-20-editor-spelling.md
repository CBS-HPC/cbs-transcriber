# Post-v2 spelling and Text uncertainty — 20 November 2026

Separate branch `codex/weekend-spelling-20261120`. Claude's redesign and double-click trigger
are `b271985`/`95795de`. Codex added missing-engine refusal regression `eb4f45f` and Text-draft
uncertainty decoration `11f9b2f`. Root independently read the new decoration code and reviewed
a synthetic screenshot. These commits are not the loaded MVP v2 preview or the rc2 package.

## Focused evidence

**165 distinct focused cases:** 61 metadata/task/portable/MVP, 40 service/recording-vocabulary/
cache, one missing-engine refusal, five existing worker cases and 58 browser cases.
The final 58 browser cases passed in 555.99 s and include all seven new uncertainty cases;
the separate seven-case rerun passed in 59.38 s. Earlier 20 browser cases are included in 58
and must not be counted again. Node syntax, pinned Prettier 3.6.2, scoped Ruff and whitespace
checks passed. Production JS/CSS stayed fixed through the browser gate. A loopback guard
was added to the new test module after its first seven cases had run; the final guarded
seven-case rerun validated that final test file. Do not claim all test bytes were frozen
through the earlier 58-case run.

The browser cases retain double-click native selection, Alt+Enter, suggestion replacement,
Escape/caret return, private recording/project words, cached editor behavior, metadata/review
controls and the MVP feature boundary. Text uncertainty remains distinct from spelling,
including untimed words, Unicode/CRLF draft mapping and native editing state.

## Cached-engine harness qualification

The existing test environment lacks Spylls. No environment was synchronized or installed.
Tests used the exact already-cached pure Python Spylls 0.1.7 through an isolated test-only
worker import wrapper. Production credential and network isolation remained in place;
later runs suppressed bytecode writes. The ordinary installed worker was not changed to
inherit that path. This proves the exercised engine behavior through the harness, not
normal installation, missing-dependency recovery or clean-machine acceptance.

## Still separate

Spelling remains hidden/refused by MVP. Dictionary and engine licensing, real Danish/English
suggestion quality, installed-worker/package startup, real users, Firefox/Safari native
selection/Undo, real OS IME input and screen readers remain open. No public dictionaries/models were downloaded,
private transcript was uploaded, app reloaded, main branch merged/pushed or release published.
Recording-vocabulary/settings/portable changes need composition checks when integrated with
the independently verified timing candidate. Bug numbers B488–B491 preserve the spelling
findings without colliding with core export/timing B467-B470.
