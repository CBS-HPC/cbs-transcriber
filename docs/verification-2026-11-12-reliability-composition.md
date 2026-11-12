# MVP v2 reliability composition — 12 November 2026

Independent **Go** for product/test checkpoint `ed94739472f799782134fa60441a949098cf7108`
on local `codex/mvp-v2-reliability-candidate`. Later commits add documentation only. The
candidate is not loaded in the user's app, merged to main or published.

## Exact composition

| Component | Local commit | Scope |
| --- | --- | --- |
| Setup/readability and compute-plan base | `4546123` | Served UI from `dbb3581`, plus documentation; existing browser Go retained. |
| Native startup recovery | `653b0e9` | Exact-ID singleton/batch retrieval, durable Stop and no-submit worker authority. |
| Runtime observation stability | `857a727` | Typed bounded same-allocation reads, terminal publication settling and atomic Stop/Ready fencing. |

The root and independent reviewer verified all **566 product/test/configuration files**
against the owning worktrees: 551 unchanged base files, eight native overlays and seven
Runtime overlays. The two overlay sets are disjoint. There were no production/test merge
conflicts; documentation conflicts retained the current UI/funding policy and both backend
receipts. The candidate descends from clean public main `708a27b`.

## Fresh combined gate

The exact union of final native 76 and Runtime 117 case identities contains **193 distinct
tests**. Collection matched that set with no omissions/extras. The assembled source passed
**193/193**, with zero failures/errors/skips, in 365.09 seconds. All 566 captured inputs were
unchanged before/after; the imported package resolved to this candidate's source. Independent
source review found no new interaction blocker at startup, Stop/Retry, library ownership or
HF/preparation observation. This is a focused composition gate, not a fresh whole-repository
regression run.

| Retained private evidence | SHA-256 |
| --- | --- |
| Combined XML | `7d2cb4a00696d17710911e00aa88b6f2a0a24a95faf6bbeb19be9cd3b3b2d8b0` |
| Independent composition review | `91e9d08379e8ea7a5c45c14899cb357def85b3ccedade3fd929c1799ba5be92b` |
| Root source-union proof | `b6cb24056796a2ea2e88d289053c0ce50e32717f8b929297bd4ea673b4e920be` |

UI bytes are unchanged from the separately reviewed setup/readability gates: app.js
`7a31629b47e6e9b3726440b8e04d175f1541d895d6ae2610cf4defea40cc8f0f`, app.css
`3275bcd38f6eab6c5508768ebe8fdc45f99a13297fecfafd1aeff6e667887b03`. The retained browser
evidence qualifies 74 MVP/10 full-profile observations across 75 distinct identities; this
composition did not rerun browser tests or add a new frontend behavior.

## Remaining boundaries

- Native historical generic failures, fresh singleton generic-curated failures, uncertain
  submissions and cleanup-only obligations remain manual. See the
  [native receipt](verification-2026-11-12-native-startup-reconnection.md).
- The historical Runtime preparation lock remains untouched and unresolved. Disabled
  generation Stage 1 is verified separately on `codex/mvp-v2-runtime-generations` at
  `401769e` with 46 distinct cases; its module is not integrated into this candidate.
  Actual owner/folder/promotion/admission/HF/CPU/capture/restore proofs and live acceptance
  remain required. See [ADR 0176](decisions/0176-isolated-runtime-generation-recovery.md).
- Funding-based CPU fallback applies to both engines but remains unimplemented, including
  affordability, CPU-only setup and model/recipe qualification; see
  [ADR 0177](decisions/0177-funding-based-compute-fallback.md).
- No real provider job, preparation, transfer, cleanup or model download was performed.
  Main, Desktop edits, environments, running preview processes and real library remain
  preserved. A later preview load requires idle/draft preservation and a verified build
  label/served assets; the running preview still has the earlier Python backend.

The separate usage checkpoint ends at 12:32:09 UTC. Later composition, verification and
documentation are excluded from that still-open cumulative phase.
