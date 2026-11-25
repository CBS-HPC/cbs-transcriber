# Offline weekend composition — 20 November 2026

Verified product tree: `4574083` on local `codex/weekend-post-v2-20261120`.
This is separate from the frozen rc2 package `6ff03df`. No installation, app reload,
publication or main merge/push occurred. Spelling stays hidden in MVP; Runtime stays disabled;
native observations have no production callers and do not implement a full-GPU fallback.

## Reviewed parents

| Component | Verified parent | Detail |
| --- | --- | --- |
| Timing and v2 core | 1ae0069; docs through4425520 | [Timing receipt](verification-2026-11-20-timing-rc2.md), [local rc2 artifact](verification-2026-11-20-rc2-package.md) |
| Spelling and Text uncertainty | dad392e (product11f9b2f after Claude95795de) | [Editor receipt](verification-2026-11-20-editor-spelling.md), ADR0186–0188 |
| Disabled Runtime supersession | b6edb5c; docs dee1422 | [Runtime receipt](verification-2026-11-20-runtime-supersession.md), ADR0176 |
| Unintegrated native observations | 2caa3b6/13e2027; docs c04e212 | [Helper receipt](verification-2026-11-20-native-gpu-helpers.md), ADR0184 |

S1 merge `25381cb` combines timing and spelling. `529e27d` carries the core documentation-only
follow-up; `012c8d4` adds the disabled Runtime leaf and `4574083` adds the unused native helper.
Only documentation conflicts needed resolution. Native T154 retains its updated helper status;
spelling T156/T157, core T158 and all distinct B482–B492 findings are preserved.

## Staged gates

| Gate | Result | Frozen scope |
| --- | ---: | --- |
| S1 timing/spelling backend | 556 passed, 1 deliberately deselected;449.61s | 603 captured source/test files |
| Final changed backend | 385 passed;1015.25s | Final605-file composition; Runtime203 plus helper182 |
| Final editor/timing/spelling browser | 81 passed;722.70s | Same final605-file source |
| Changed first-use/provider browser | 28 passed;187.44s | Same final605-file source |

All captured source/test bytes remained unchanged within each immutable gate. The backend
addition is only the previously reviewed Runtime leaf and new no-I/O helper; all other S1
product bytes are unchanged. These are focused composition checks, not the whole repository
suite. Overlapping earlier component/rerun counts are not additional coverage.

The one S1 deselection asserts main packages full. This variant intentionally retains its
MVP profile (with Undo/History/Branches exposed and spelling hidden); exact packaged identity
and recovery gates are checked separately. The assertion was not weakened. The final browser
inventory includes the core's tab-title privacy regression absent from the earlier editor
branch. First-use/provider coverage adds the two changed setup modules, not an unrelated audit.

## Keyboard fixture finding (B492)

The first S1 browser run had **80 passes and one failure**, with all603 hashes unchanged.
It assumed Replay was immediately before the textarea. The new Spelling checkbox becomes
enabled after preferences load and is the actual preceding control; three fast isolated
repeats could pass before readiness, while a wait-ready probe deterministically exposed it.
Only that test now waits for readiness and uses native Shift+Tab through the checkbox to
Replay. Enter, focus, caret, draft, playback and passage-boundary checks remain; no direct
focus shortcut or production change was made. The affected seven-case module and three
fresh exact-node repetitions passed before the final full browser gate. CRLF was preserved.

## Independent composition review

Go on source `4574083`: Runtime source/test bytes match `b6edb5c`; native helper/test match
`13e2027`. All three Runtime flags remain literal False. There are no production references
to the helper. Original native allocation, capture, startup, transfer and batch modules are
unchanged; the unfinished original `native_full_gpu.py` WIP is absent.

All34 S1 changed component files (15 product,19 tests) retain exact working/Git bytes after
the final merges. Source delta from S1 is only the Runtime leaf and new helper. Existing
source modes/bytes and current ownership boundaries are preserved. Scoped final Ruff check
and format check cover34 Python files; Node syntax and pinned Prettier3.6.2 pass for the four
frontend files. No source was changed to satisfy a check during an immutable gate.

## Limits and next steps

Runtime normalized real replies, locking, generation carry and live preparation/batch/recovery
gates remain open. Full-GPU consent/control/capture and actual capacity/cancellation behavior
remain open. Spelling uses the qualified existing cached-engine test wrapper; installed-worker,
licensing, representative quality, real OS IME/Mac and accessibility acceptance are separate.
The [human release checklist](testing/human-release-checklist.md) still owns clean-device,
shortcut/update/uninstall and real-model acceptance. No real UCloud work, uploads, cleanup,
downloads, environment synchronization or user-library changes occurred.

Continue the remaining [weekend plan](work-plans/weekend-2026-11-21.md) from this local
checkpoint, preserving the independent source branches and immutable rc2 artifacts.
