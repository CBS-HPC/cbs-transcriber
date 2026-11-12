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
| 2 | Readability | Implemented: passage text 14 to 16 px; editor and playback overlay 13 to 15 px together; timing fields 11 to 13 px. One guarded post-alignment Follow pass fixes the measured short-window comparison regression. Wrapping/focus/drafts/undo checks pass. No broader redesign. |
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
