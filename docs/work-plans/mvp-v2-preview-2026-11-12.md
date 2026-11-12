# MVP v2 preview preparation — 12 November 2026

The user resumed three deliverables: finish the local-folder installation/removal guide,
increase transcript/editor fonts modestly, and verify the resulting candidate. The user
does not intend to install the preview manually. Prepare a labelled local preview using
the existing development environment without synchronizing or replacing that environment.
Public main, Desktop source and published MVP 0.1.0 remain unchanged during preparation.

Base: independently verified local checkpoint `d133143` on `codex/post-mvp-morning`.
Integration: `codex/post-mvp-uninstall-guide`; font work: `codex/mvp-v2-readability`.
Preserve unrelated changes, actual libraries, original recordings and remote obligations.

| Order | Deliverable | Status and acceptance |
| --- | --- | --- |
| 1 | Local-folder guide | Implemented: matching released wheel/constraints, uv-managed CPython in App/.venv, exact interpreter startup/shortcut, stopped-app repair and method-specific uninstall. Private Windows install/help/four core smoke/COM/repair checks pass; full environment removal was blocked and is unverified. Keep Library/Projects separate. Real device/Mac acceptance remains open. |
| 2 | Readability | Implemented, refined after user feedback: passage/editor/overlay 15 px, passage actions 12 px with 24 px minimum height, sidebar text 13 px and headings 14 px; timing fields 13 px. Guarded post-alignment Follow retained. An additional independent 23-case MVP browser gate passes at desktop/compact/narrow widths; overlapping earlier cases are not added to the prior distinct total. No broader redesign. |
| 3 | Focused candidate gate | Complete: 56 distinct qualified MVP browser passes, eight mocked native recovery passes and 23 backend/launcher passes. Original failures and narrow fixture/location remedies are retained. The base's broader inventory is reused where unaffected, not rerun or claimed as a new all-green full run. |
| 4 | Preview handoff | Loaded: MVP v2 preview 1 on port 8765, served label/assets verified. The user requested the same library/overnight batch; frozen identities/parameters/results are preserved and Activity offers explicit same-allocation Retry. No user reinstall. Main merge/publication remain separate. |

See [preview verification and limits](../verification-2026-11-12-mvp-v2-preview.md).
Installation evidence uses the released 0.1.0 wheel; it does not establish that the new
preview has been packaged or published. A private Python installation must use a private
install directory and no global executable registration. Never uninstall the user's tools,
shared Python, actual library or model cache as part of this disposable probe.

Runtime live preparation, spelling integration and PII-Tracer are separate v2 work areas.
Do not start jobs, download model weights, steal preparation locks or upload transcripts.
AI-assisted qualitative coding/topic generation/search is an exploratory later-release
candidate, without model execution or a new UI in this preview.

## UCloud scheduling research retained for later planning

The user also requested consideration of splitting large batches across several MIG jobs
and a CPU fallback. No scheduler or compute-selection behavior is changed here.

- The checked CLI's live machine listing reads `GET /api/jobs/retrieveProducts` and exposes
  `support.queueStatus` per product (Available/Busy/Full/Unknown), not a numeric free count.
  Multiple product sizes can share one underlying pool. This observation is not a reservation.
  [Client implementation](https://github.com/CBS-HPC/ucloud-api/blob/main/src/ucloud_workflow/availability.py),
  [upstream contract](https://github.com/SDU-eScience/UCloud/blob/master/provider-integration/shared/pkg/orchestrators/jobs.go).
- Independent 1g MIG jobs are a possible bounded scheduling design, with per-allocation
  Stop/recovery/cleanup and no automatic resubmission of uncertain accepted requests.
  A two-job ceiling and duration-balanced partitions are proposals, not accepted defaults.
- Native Transcriber documents CPU support and recommends at least 16 cores and/or 64 GB
  RAM, while noting that GPU execution is much faster. The app currently restricts fresh
  allocation choices; CPU fallback needs explicit implementation and model-specific tests.
  A CPU queue alternative is not proven faster than waiting for a MIG.
  [Official Transcriber guide](https://docs.cloud.sdu.dk/Apps/transcriber.html).

Only public documentation/source and CLI help were checked; no authenticated availability
query, allocation, preparation, remote cleanup or provider benchmark was performed.

## User checks for the local preview

Finish any open edit before refreshing. The local preview is not a new published wheel.

| Check | Expected result |
| --- | --- |
| Readability | Passage text is slightly smaller than the first preview; Replay/Edit/Merge/Delete and the Recording details/Speakers sidebar are larger. Long names wrap without covering controls. |
| Text editing | Double-click selects that word in Text; Save enables only for a change. Cancel preserves playback/search/review state. Unchanged/replaced words retain their intervals and inserted words remain untimed. |
| Passage structure | Split is unavailable at the ends or inside a word; split/merge can be undone and an individually deleted passage restored. |
| Review and Working | Checked/Needs review persist; Next to review reaches unchecked/review-needed passages. The current Working action is disabled; choosing an earlier version is possible. |
| Export | Selected recordings and several formats, with optional originals, appear in named recording folders. Preparation continues in Activity after closing Export Settings. |
| First-use setup | Use the separate synthetic demo on port 8766; no need to delete real data or keys. The independent dialog and missing-key colour passed their focused gate and are served on both preview ports. Skip/Close/Escape leave setup available through Settings. |
| Overnight UCloud recovery | Already confirmed by the user on 12 November: explicit Retry recovered all 12 members from allocation 12456177 after app close/reopen. Another paid run is not needed for this acceptance point. |

Automatic startup reconnect, parallel MIG jobs, CPU fallback, live Runtime preparation,
spelling and PII-Tracer are not added by these preview refinements.
