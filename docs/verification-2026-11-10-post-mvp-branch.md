# Post-MVP branch verification checkpoint

10 November 2026. Branch `codex/post-mvp-work`, based on clean public main `708a27b`.
This work has not been merged/pushed to main, published, copied into Desktop main or loaded
in the installed MVP 0.1.0. Do not read this receipt as a replacement for its release receipt.

## Scope and independent review

The user selected short documentation/reviewed descriptions, specific empty-page/Library
changes, checked-provider transcription selectors, an actual Text word-following prototype,
design-only failed-output recovery, offline Runtime/anonymisation work and a hidden-task audit.
Later steering limits frontend decisions pending colleague feedback. Broader editor changes,
new visible prototype controls and production anonymisation integration were not added.

Root owns SPEC/TODO/decisions and integration. Separate helpers own documentation, Library,
connection guards, Text prototype, Runtime, anonymisation and hidden-task evidence. Another
helper reviews source and semantics independently; it identified proof replay, discovery
generation, Save/failure-race, malformed-observation and stale-selector issues before acceptance.
Their resolution/regression gate must pass before declaring the connection work complete.

## Verified focused evidence

| Scope | Evidence and practical limits |
| --- | --- |
| README/guides | 158-line main guide, six focused guides; 82 local outgoing file/anchor checks and incoming README links across 313 tracked Markdown files pass. Pins, copy mode, credentials/data retention and platform/licence limits preserved. No repeated install or external URL probe. |
| Empty page | Synthetic Chromium test passes at 1440/390px and creates a project through the existing action. Reuses unchanged official blue vertical SVG; supplied PNG preserved. Mobile test caught an old rule hiding the remaining Create action; that rule is corrected. |
| Checked connections | 53 focused readiness/browser/save/credential/version cases pass; actual capture→restore check proves row 10 stripped from serving state. Further provider/model/native-profile gates pass. Independent review Go after stale-proof, generation, race and malformed-record fixes. Only explicit checks; no live availability/ASR claim. |
| Library | 42 browser cases pass, including read-only access to legacy hidden entries, actual disposable deletion, ownership blockers and wide/narrow layout. Input grows from 26px to 554/270px. Backend visibility API retained for compatibility; no real library deleted. |
| Text prototype | 28 distinct browser cases across scoped runs pass: exact saved wording, missing/stale timing, native selection/typing/Undo/Redo, Follow off/scroll/resize, 5,004-word Unicode/newline/long-token geometry and Save/Split/Cancel. Reviewer gives Go for prototype only. |
| Runtime release | Eight new ownership-change cases fail against old code; final lock suite passes 17, with three explicit Linux flock/native-symlink skips. Guard verifies acquired identities/content then unlinks marker/rmdir only. Independent review Go; historical runtime-busy and Linux/UCloud acceptance remain unresolved. |
| Anonymisation | 27 focused tests pass, independently rerun. Six deliberate bilingual gap probes reproduce DA 3/7 and EN 3/5 exact hits plus one policy-dependent false suggestion. These are synthetic observations, not detector-quality estimates. No model executed or in-app workflow added. |
| Hidden-task contracts | Fourteen existing targeted tests pass for early saving, same-ID recovery/shutdown, concurrency, exports, reports, Auto and instance fences. A new deterministic same-recording media/inspection/native overlap test also passes with actual tiny FFmpeg playback and mocked native responses. |

Focused evidence was collected during concurrent branch development. A final assembled source
gate is recorded below; intermediate passing runs alone do not establish every interaction.
No timing/throughput improvement is claimed from this busy-host verification.

## Integration failures and corrections

- Library layout red test reproduced a floated note squeezing the confirmation into a
  vertical column. Scoped CSS and label markup fix it without changing deletion authority.
- Empty-page mobile test exposed reliance on the removed duplicate hero Create button;
  retain the existing sidebar action rather than add a new workflow.
- New connection guards correctly refused unchecked synthetic native/Runtime fixtures.
  Fixtures now explicitly perform/save a synthetic successful check, preserving the refusal.
- The opted-in Split test initially expected trimmed text; its oracle now retains the exact
  left trailing space required by the existing structural contract. Product behavior unchanged.

## Final assembled gate

On the assembled source after formatting, **207 backend cases passed, three skipped**, and
**eight browser cases passed**. The backend gate combines connection/save/native-model,
Runtime-lock/consent, media/native overlap, anonymisation, Library visibility, concurrency
and native download recovery. The browser gate covers empty-page creation, Library deletion,
connection failure/setup and all five opted-in Text prototype cases. Changed Python files
pass Ruff lint/format; JavaScript syntax, Prettier 3.6.2 and CRLF-aware whitespace checks pass.

A broader run selected 5,526 non-browser cases (621 browser cases deselected). It was
intentionally stopped around 17% after three failures and two teardown errors: unchecked
synthetic API/Runtime setups, an outdated polling mock keyword and a Delete mock affecting
unrelated temporary-directory cleanup. These require scoped fixture fixes and reruns, not
an assumption that the whole suite passed. The interrupted process emitted no JUnit; private
coverage records must distinguish observed completed modules, affected consumers and the
unfinished remainder. Production source stayed unchanged during the run. Continuation and
final union-coverage verification are pending. Generated logs/screenshots/JUnit remain private.

The scoped fixture/mock corrections passed their affected module gates and survivor/recovery
reruns. Further fresh-transcription fixtures explicitly perform synthetic connection checks;
negative/unconfigured tests remain unchecked. The completed segmented backend gate covers
**all 5,526 unique selected cases: 5,500 passed and 26 skipped**, with no uncovered or latest
failing/error outcomes. Initial/final raw ordered inventories agree. Independent reconstruction
matches the totals and XML hashes. This is segmented evidence, not a claimed monolithic run:
838 passing cases retain count-checked original completed-module progress, and 4,688 have
JUnit outcomes (4,662 passed, 26 skipped). The skipped cases were rerun separately to record
their actual reasons; repeated executions are not added to the unique totals.

The private audit first normalized backslashes inside parameter labels and collapsed a pair
of distinct identifiers. Execution selected whole modules, so no case was omitted. The audit
now preserves literal parameter tails/case/Unicode, normalizes only file prefixes and follows
Pytest's XML name mapping. Exact raw inventories, per-case outcomes and all source hashes
reconcile. Full-repository Ruff lint/format pass; production source stayed unchanged.

The additional five-module provider browser gate (25 cases) is still in progress. The earlier
eight-case assembled browser gate and scoped Library/editor gates passed; no claim is made
that all 621 browser cases have been run on this branch.

## Remaining decisions and acceptance

The [seventeen-item audit](audits/post-mvp-hidden-tasks-2026-11-10.md) retains real/provider,
platform, accessibility, performance, licence and institutional gaps without duplicate
implementation requests. The [failed-output proposal](proposals/native-failed-output-recovery-2026-11-10.md)
does not create reruns or dispose of remote evidence. Runtime generations remain disabled.
Anonymisation identifier scope, pseudonyms and mapping custody/publication need decisions.

The [timing-hint remedy](proposals/legacy-timing-hints-2026-11-10.md) now has independent
design Go after adding versioned timing adoption and preserving explicit unchecked cloud
history scope. It has not been implemented; no runtime writer or portable pin was changed.

The Text prototype is off by default; see [opt-in/revert and limits](testing/text-editor-follow-prototype.md).
Synthetic Windows Chromium is not Mac/Safari/Firefox/IME/zoom/high-contrast/screen-reader
acceptance. The installed package and its environment are unchanged.

Codex usage after MVP publication belongs to the separate open phase in
[the usage ledger](development-usage.md). Its first cumulative checkpoint records 208,531,816
tokens through 16:20:13 UTC; the phase remains open and later observations are excluded.
Credit/cost values remain unknown, not zero. Published MVP counters are unchanged, and Claude
accounting remains assigned to Claude.
