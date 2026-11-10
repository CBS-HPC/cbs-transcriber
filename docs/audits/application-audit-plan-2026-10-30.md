# Remaining application audit plan

Prepared 30 October 2026. This extends the
[transcription, batch and Activity audit](transcription-batch-2026-10-30.md).
It is a coverage plan, not a claim that every area has passed review.

## Method and evidence

Each slice starts from a recorded commit and the current SPEC/TODO/ADRs. Inventory its
API routes, service functions, background work, frontend actions and existing tests together.
Trace the accepted behavior through those layers, then exercise successful, failed, stale,
cancelled and interrupted operations in a temporary library. Use synthetic or pinned public
fixtures and mocked providers. A passing happy path alone does not close a slice.

Record findings with severity, source references, reproduction, affected data/behavior,
regression and fix commit. Keep unverified leads separate from reproduced defects.
Update SPEC bug records and add an ADR for persistence, timing or job-semantics changes.
An independent reviewer checks high-impact fixes. Rerun affected checks after integration;
record the exact scope/commit of broader unit and browser runs instead of adding overlapping
test counts together.

Mark each slice **not reviewed**, **reviewed with evidence**, **finding open**, **fixed and
verified**, or **requires external acceptance**. "No reproduced defect" is a scoped result,
not a guarantee that the component has no errors.

## Coverage inventory

| Slice | Scope and source families | Essential checks | Current position |
| --- | --- | --- | --- |
| A01 Library and persistence | `store.py`, `storage.py`, `libraries.py`, startup/chooser routes | Schema migration preservation; one serving lease; stale locks; discovery/switching; unavailable/moved roots; filesystem links, permissions, Unicode and long paths | Reviewed startup drain and library-handoff/project-move identity fixes integrated (ADRs 0115/0118); joint lifecycle/credential slice 51 passed/1 skipped. Real Cloud Files hydration, Mac and crash-durable move acceptance remain open. |
| A02 Projects, recordings and metadata | Project/recording routes in `app.py`, collections, notes, managed storage and moves | Project isolation; stale replies/revisions; exact move ownership; copy/commit/finalization failures; drafts/navigation; collections never moving media | Reviewed upload uncertainty and metadata draft/action fixes integrated; targeted A02/A07 backend 22 and browser 19 passed. Later destination/descendant ownership fault tests are integrated under ADR 0118; real platform/move acceptance remains. |
| A03 Imports and transcript normalization | `transcripts.py`, upload/import routes, CSV/JSON parsers | Both CSV layouts and field order; malformed/oversized/deep input; literal structured words; Unicode/dotted names; missing/partial/overlapping timing; first passage retained; raw receipt correspondence | Synthetic audit found confidence overflow, quadratic association, speaker 0 loss and ambiguous CSV loss. Reviewed fixes integrated; generation-bound legacy receipts and strict new imports passed 37 integrated checks. |
| A04 Editing and speakers | `transcripts.py`, `transcript_merge.py`, `result_actions.py`, editor UI | Word/free-text transitions; split/merge/delete/restore; exact text/unselected dictionaries; word-map validity; speaker assignment/renaming; conflicting saves; no fabricated timing | Synthetic audit reproduced delayed-dialog draft loss, stale notes projection and unsaved draft loss; reviewed fixes integrated, combined A04/A06 browser checks 27 passed. |
| A05 Playback | `media.py`, `audio_inputs.py`, `remote_audio_input.py`, player/review UI | HTTP ranges; damaged/unsupported media; audio/video position/speed; Replay without word timing; active word/segment; Follow immediately and above footer; seeking across gaps/overlaps | Synthetic range matrix passed 27 checks. Reviewed viewport, stale handoff and keyboard-scroll fixes integrated; shared backup/playback browser slice 48 passed. FFmpeg cancellation fix passed 75 focused cases; broader platform acceptance remains. |
| A06 Comparison and search | `alignment.py`, `wording_adoptions.py`, both comparison panes | Specific wording after omissions; repeated phrases; uncertain/unmatched rows; literal Unicode search highlights in both panes; view-only alignment; revision-bound wording adoption | Synthetic audit reproduced CRLF/leading-newline loss on unchanged edit, misaligned highlights and inconsistent Unicode case matching. Reviewed fix integrated; combined A04/A06 browser checks 27 passed. Alignment/wording API positive tests passed. |
| A07 Providers, settings and credentials | `providers.py`, `catalog.py`, `huggingface_access.py`, `ucloud_discovery.py`, `runtime_compute.py`, settings UI and packaged catalogs | Model/capability identity; visibility versus real ASR support; defaults versus saved opt-outs; Protected approval in UI/API/worker; endpoint/token scope; forget/change during jobs; redaction/redirects; disabled models | Reviewed stale-version/default/credential and Settings-open fences integrated; targeted A02/A07 backend 22 and browser 19 passed. Versionless legacy saves and SQLite/keychain failure atomicity remain limits. |
| A08 Jobs and Activity | `jobs.py`, `input_admission.py`, `features.py`, `routing.py`, `native_batch.py`, `ucloud_native.py`, `runtime*.py`, `remote_jobs.py`, `remote_release.py`, deletion modules | Group continuity/counts; actual allocation ownership; late joins; retries/uncertain POSTs; Stop/drain; cleanup beyond recent history; polling races; model reuse/resource bounds | Current deep audit; final integration gates and real-provider acceptance remain |
| A09 Enhancement and repairs | `enhancement*.py`, `local_runtime.py`, `local_timing.py`, `repair*.py`, `danish_models.py` | Local/cloud routing; actual bounded clips; per-target recovery/cancellation; stale Apply; exact unselected content; warm caches; model/decoder identity; language evidence; partial timing/uncertainty | Speaker-map and frozen-pin fixes integrated; 15 focused cases passed. Explicit-null legacy singleton correction passed 161 isolated cases; full integration suite rerunning. Real quality/resource acceptance remains. |
| A10 Versions and provenance | `history.py`, `result_roles.py`, `result_conflicts.py`, `result_merges.py`, `restored_owners.py`, all `*_capture.py`, `provenance_artifacts.py`, `research_reports.py` | Immutable Working pins; independent revisions; Trash/restore; conflict parent hashes; producer artifact capture; exact report scope; unknown legacy evidence; privacy of reports | Working pins, version restoration, explicit conflict review and scoped local/Runtime/API/native producer capture are implemented. Reviewed canonical report/source ancestry fixes passed 45 integrated cases. Composed fresh-admission/portable-restore evidence and researcher acceptance remain open. |
| A11 Backups and restoration | `backup.py`, all `portable_*.py`, library/backup UI | Consistent capture; byte/row/hash preservation; bounded untrusted DB checks; source not modified; collision-safe publication; interrupted resume; restored-owner quarantine; cloud/retention controls | Reviewed reservation, exact-destination approval, retention and Settings fixes integrated; backend 45 and shared browser 48 passed. Live storage acceptance remains. |
| A12 Exports and interoperability | `exports.py`, `docx_export.py`, transcript exporters, report ZIPs | Windows/Mac NVivo formats; readable/DOCX/subtitle/CSV/JSON outputs; timestamp bounds/escaping; filenames/ZIP paths; exact Working version; collection/bulk selection; skipped manifest; all passages retained | Reviewed navigation, UTF-8 filename, snapshot and timestamp fixes integrated; combined import/export 53 passed. Synthetic NVivo pack delivered; actual NVivo imports remain unverified. |
| A13 Research prototypes and evaluation | `annotations.py`, `evaluation.py`, `evaluation_suite.py`, `review_evaluation.py`, `review_hints.py`, evaluation scripts | Exact source/codebook versions; literal evidence; immutable suggestions/reviews; no implied AI execution; exports exclude unapproved/private review text; WER/CER weighting; timing/speaker metric limits | Reviewed fixes for partial saved timings, frozen Runtime admission and literal codebook/evidence UI integrated; 95 backend and 10 browser cases passed. No automatic analysis or real quality acceptance is assumed. |
| A14 Installation, platform and documentation | `__main__.py`, `shortcuts.py`, installation/MIG scripts, `pyproject.toml`, `uv.lock`, README/AGENTS/ADRs and assets | Clone/ZIP and locked dependencies; no admin/silent sync; shortcuts/quoting; source identity; Windows/Mac paths; optional inference policy; actionable errors; docs versus behavior; licence/asset notices | Offline helper/shortcuts checks exist; clean Windows and both Mac types remain |

Frontend coverage includes every view in `static/app.js`, `app.css`, `index.html`,
`conflict_review.js`, `portable.js`, `library.js` and `library.html`. Packaged catalogs,
runtime recipes, public fixture manifests and standalone scripts are included in their
owning slices. Recheck the source inventory at execution time so new files are assigned.

## Cross-cutting passes

1. **Data safety and recovery:** process exit before/after commit, write/fsync/rename failure,
   file locks, source replacement, stale retries, lost replies, cancellation and shutdown.
2. **Security/privacy:** loopback/host/origin checks, credential handling, redirects, untrusted
   fields/paths/HTML, subprocess arguments, downloads, exports/logs and protected-provider
   policy. Validate actual paths; do not infer a vulnerability from a suspicious name alone.
3. **Concurrency:** simultaneous edits/notes/roles, import/preparation/transcription, Stop/Retry,
   Delete/backup/moves and polling. Preserve queue/snapshot/SQLite/lease ordering.
4. **Usability/accessibility:** keyboard/focus/labels, error visibility inside dialogs, empty/
   loading/failed states, long text, narrow windows and explicit save/processing status.
5. **Performance/resources:** bounded reads/output/memory, large histories and transcripts,
   cancellation latency, repeated polling and queue fairness. Report measurements with input
   and environment; synthetic timings are not real GPU throughput claims.
6. **Architecture/tests/docs:** route-to-service contracts, duplicated decisions that caused
   defects, untested branches and stale fixtures. Avoid a broad rewrite during correctness
   work; extract a shared primitive only when it addresses an evidenced problem.

## Release and external acceptance

Verified lost work, incorrect history, permission bypass, duplicate remote submission,
untracked private remote files, unsafe restoration/deletion or a cancellation deadlock blocks
activation. Fix or explicitly remove the affected new path before calling the build ready.
Lower-impact issues may remain as named tasks with a reason and test evidence.

Real UCloud job/model/MIG execution, actual NVivo imports, clean Windows/Apple Silicon/Intel
installations, network-filesystem/power-loss behavior and CBS IT/DPO/licensing decisions are
separate acceptance work. Mocks, dependency plans and checksums do not establish those facts.
Use the [weekend plan](../work-plans/weekend-2026-10-31.md) for execution order.
