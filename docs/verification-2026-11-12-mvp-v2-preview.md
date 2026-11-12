# MVP v2 preview 1 — 12 November 2026

The local preview is loaded on port 8765 from `codex/post-mvp-uninstall-guide`, based on
verified checkpoint `d133143`. No public-main merge, push, Desktop source update or release
publication occurred. The existing development interpreter/environment was reused without
synchronization; the user did not install the preview manually.

## Changes

- Local App/.venv installation, exact-interpreter startup/shortcut/update and method-specific
  uninstall guides; sibling Library/Projects and shared credentials/caches stay separate.
- Passage text was initially increased from 14 to 16 px, then refined to **15 px** on user
  feedback; Text input/highlighting mirror remain 15 px. Passage actions are now 12 px with
  24 px minimum height, sidebar text 13 px/headings 14 px. Timing replay text is 15 px and
  timing fields 13 px. No broader interface redesign.
- One existing guarded Follow pass after comparison alignment settles, fixing the measured
  larger-font short-window regression without changing drafts, playback or Follow-off intent.
- Two narrow browser-fixture corrections: visible Text-pane Cancel selection and the accepted
  partial-timing save notice, including persisted wording. Shared helper bodies are unchanged.

## Qualified evidence

| Scope | Result | Qualification |
| --- | --- | --- |
| MVP browser | 56 distinct passes | 48 unaffected final-run outcomes plus eight fresh full review-layout module passes after its stale status oracle was corrected. |
| Backend/launcher | 23 distinct passes | 22 initial passes plus the unchanged standalone smoke node with a short Windows fixture root. |
| Native recovery | Eight passes | Fresh MVP-profile mocks: exact same allocation on reopen/retry, uncertain POST protection, saved siblings/edits, early results and terminal-only cleanup. |
| Readability differential | Two earlier smoke cases; nine private geometry observations | Wide/narrow metrics, overlay wrapping, caret/focus/undo and baseline/candidate/fix comparison; not added again to the 56-case total. |

The independent audit reconciles **87 unique scoped passes**, no skips or unresolved gate
outcomes. Each gate's input hashes match before/after; collection/JUnit/result identities
are bijective. This is a reconciled result, not a new monolithic full backend run. The base's
[complete verification](verification-2026-11-11-post-mvp-morning.md) is reused where unaffected.
Original failed receipts and diagnostics remain private. The standalone source-preservation
failure was a 277-character Windows path with an existing parent; shorter fixtures pass
unchanged. No timeout widening, weakened geometry checks or claim to fix platform limits.

Independent receipt `independent-final-audit.json` SHA-256:
`e65bee1d9b2ffcb0ffee9ed8c954c8f58865f72a6214c6f25f1189832fc77db0`.

The font refinement has a separate immutable, independently reviewed **23-case MVP browser
gate**, all passed without skips. It covers 1440/760/390 px wrapping and real action bounds,
sidebar long names/notes, playback geometry, caret/focus/native undo, speaker assignment and
compact Working controls. Source/test hashes match before and after; collection and outcomes
are bijective. These cases overlap the earlier gate and are not added to its distinct total.
Backend/native/installation checks were not repeated for CSS-only changes. Receipt
`font-balance-independent-audit.json` SHA-256:
`4602132628ecab6b2ac4742c0919044980243abc1790e3dea149dc13ec5823ec`.

## Installed-wheel check

Released **0.1.0**, not this preview, was installed in a disposable local environment with
private CPython 3.13.11 and no global executable registration. GitHub asset digests matched;
all 135 packages installed. Module help, four installed core startup/media/import-export/
byte-range checks, private-folder Windows COM/no-overwrite and stopped-environment repair
passed. Sibling canaries and release assets stayed byte-identical.

Automatic approval review blocked recursive removal of that disposable environment, giving
only “blocked by policy.” No removal or equivalent workaround was attempted; the environment
is retained and full-environment uninstall remains unverified. Actual Desktop clicks,
CBS-managed/personal clean-device and Mac acceptance remain separate.

Receipt `local-folder-final-receipt.json` SHA-256:
`4e5cabb54609f421bf6235c0179e89aac11a713323b246715d8ef008d7071374`.

## Loaded build and preservation

Health responds, and `/api/features` reports **MVP v2 preview 1**, profile `mvp`; the underlying
package version is still 0.1.0. Served assets exactly match the verified candidate bytes:

| Asset | SHA-256 |
| --- | --- |
| app.js | `b5ec372d935240165ee5417c613d8a677c82f64fe8d36c6fede3d4dcd5b2118f` |
| app.css | `c1bcaef51a80988271bcb867271853763683b75cbab0eef4319c58738d04bb57` |
| release.json | `c9919f5cb11bd398f86a63ec1db86db89a9f2282112ac88817b838a3907a2d88` |

The user confirmed the 12-member batch is a real overnight native job and requested keeping
the same library. A private SQLite preservation snapshot and identity witnesses were taken;
this is not a complete media backup. Opening schema 19 retained all 72 job identities, both
native owners, exact frozen job/owner parameters and member lists, and all 12 saved results.
The 12 formerly running local rows now show Interrupted as expected; explicit Retry
reconnects their recorded allocation. No Retry, new submission or remote cleanup was invoked.
The visible browser Activity card retains all 12 members and its recovery control; the tab
is left open. Actual remote status/result retrieval remains for the user's acceptance test.

Runtime live preparation, spelling, PII-Tracer, multi-job scheduling and CPU fallback are
not introduced by this preview. [Plan and capacity research](work-plans/mvp-v2-preview-2026-11-12.md).
