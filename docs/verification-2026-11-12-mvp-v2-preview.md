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

## Setup-dialog refinement

Connect UCloud is now an independent Settings-style modal, preserving first-use eligibility,
optional HF, explicit Skip/Close/Escape and Settings handoff. Empty fields are pale yellow
only when saved-presence metadata proves the relevant key is missing; blank retained keys
and unknown HF presence stay neutral. Accepted Saves retain their outcome through another
tab's completion or rejected navigation, including partial/uncertain inline recovery.
Settings retains saved held Runtime profile IDs without offering them for new work.

The original immutable MVP gate recorded 64 passes and ten failures. Two exposed the saved
profile projection issue; two needed an actual visible Skip before background Activity; six
used full-profile catalog assumptions under MVP. The reviewed remedies retain exact provider
rows, hidden model flags, keys, denials, saved language and stale-response oracles. No Runtime
feature or new-work catalog was enabled to make tests pass.

Fresh immutable gates passed **39 MVP and nine full-profile cases**, without skips. With
35 explicitly unaffected prior MVP outcomes and one unchanged full-rule outcome, qualification
is 74 MVP plus ten full profile observations: 75 distinct test identities over 84 observations,
not a fresh monolithic all-green 74-case run. All captured inputs matched before/after. Original
failures remain private; independent source/fixture/coverage review is Go. Receipt
`setup-dialog-independent-audit.json` SHA-256:
`6e9b4b9550494e0625d6d87b4b0a2aba9a27d189b2ef2e34cdde55f64593cc06`.

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
| app.js | `7a31629b47e6e9b3726440b8e04d175f1541d895d6ae2610cf4defea40cc8f0f` |
| app.css | `3275bcd38f6eab6c5508768ebe8fdc45f99a13297fecfafd1aeff6e667887b03` |
| index.html before server-instance/profile injection | `72707bf263c0e3e14f6edcb7a2304796dc7ed7076f5fc3ab0527116c7e698dfe` |
| release.json | `c9919f5cb11bd398f86a63ec1db86db89a9f2282112ac88817b838a3907a2d88` |

The user confirmed the 12-member batch is a real overnight native job and requested keeping
the same library. A private SQLite preservation snapshot and identity witnesses were taken;
this is not a complete media backup. Opening schema 19 retained all 72 job identities, both
native owners, exact frozen job/owner parameters and member lists, and all 12 saved results.
At first launch the 12 formerly running rows became Interrupted as expected, retaining
their explicit same-allocation Retry. The agent did not invoke Retry, submit a new job or
request remote cleanup. The user subsequently reported that Retry worked perfectly.
Read-only local observations then confirmed the same allocation **12456177**, all 12 member
jobs completed with 12 saved member results, 24 total results (12 pre-existing plus 12 new),
stored remote state SUCCESS and no cleanup error. This is one accepted Windows overnight
queue/process → app close/reopen → explicit Retry case. It does not prove early retrieval
while RUNNING, every failure phase/device, automatic startup reconnect or an independent
inspection of UCloud Trash.

The separate first-use demonstration uses port 8766 and a synthetic Library/Projects/Home,
null keyring and disabled startup remote checks. Actual credentials and the real library are
unchanged; deleting them is unnecessary. Its browser completion marker is separate by origin.
Frontend refinements are served without restarting the app or forcing a user-tab refresh;
finish an open edit before refreshing. The verified modal/highlight bytes match on both ports;
no server restart, user-tab refresh or real credential/library mutation was performed.

Runtime live preparation, spelling, PII-Tracer, multi-job scheduling and CPU fallback are
not introduced by this preview. [Plan and capacity research](work-plans/mvp-v2-preview-2026-11-12.md).
