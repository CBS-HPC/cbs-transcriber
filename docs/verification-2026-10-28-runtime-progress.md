# Runtime Activity progress — 28 October 2026

ADR 0080 / T85g. This is verified development-branch work; the running `c3c94e6` checkout
has not been restarted or changed.

One existing Runtime allocation card now reports file counts: saved, failed, cancelled,
waiting, processing, saving, interrupted and unknown. A result counts as saved only with
the existing completed-member/output receipt. An accepted or uploaded task remains waiting
until current owner/attempt evidence actually identifies its execution. Stage labels use
the fixed worker vocabulary; older or contradictory observations stay unknown.

The bar measures **results saved locally**, not recognition percentage or estimated completion.
The existing numeric progress can be a synthesized fallback and cannot substantiate those
claims. A validated stopped/interrupted reason can correct the display of an older generic
processing message without rewriting its saved journal.

Shared Stop/Retry/cleanup, expandable per-file outcomes, Clear finished and retained
obligations preserve their existing semantics. Native Transcriber progress is unchanged.

Verification: **18 API cases and 13 Chromium cases passed**. Browser cases cover counts,
active/waiting stages, stopped/unknown fallback, shared actions and layouts at desktop and
760-pixel widths. Screenshots were inspected. Evidence is in ignored
`test-results/runtime-progress-api-regressions.xml`, `runtime-progress-browser.xml`,
`runtime-progress-desktop.png` and `runtime-progress-small.png`. These are separate focused
API/browser totals, not a whole-project test claim.

```text
uv run --no-sync pytest tests/test_runtime_progress.py -q -o addopts=''
uv run --no-sync pytest tests/test_browser_runtime_progress.py -m browser -q -o addopts=''
```

Ruff, Prettier 3.6.2, JavaScript syntax and scoped whitespace checks passed. All stores,
workers and provider responses were synthetic/mocked. No UCloud call, inference, live job,
library migration or app activation was involved. A successful real end-to-end batch and
its remote cleanup remain separate acceptance work.
