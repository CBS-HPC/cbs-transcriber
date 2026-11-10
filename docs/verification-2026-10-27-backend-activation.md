# Backend activation — 27 October 2026

Status: activated and healthy on `127.0.0.1:8765`, using the separate verified checkout.
Schema 12 migration and preservation checks passed. The browser was not opened or reloaded.

## Verified source and interpreter

- Captured commit: `c3c94e6bfd4f9baf87e0435d502e450df314d025`.
- Created a separate managed, detached, clean `<verified_checkout>` at that commit. The
  original checkout's uncommitted wording-adoption work was not loaded or modified.
- A child import probe using the existing environment's Python 3.13.5 resolved
  `cbs_transcriber.app.__file__` inside `<verified_checkout>/src`. No dependency sync,
  environment duplication or model preparation was performed.
- The serving listener was exactly `127.0.0.1:8765`, PID 11644, with the existing CBS
  Transcriber entrypoint. Only that verified process was stopped.
- The replacement was launched hidden with the same library and project parent, using
  the existing Python environment, a child-only `PYTHONPATH=<verified_checkout>/src` and
  working directory `<verified_checkout>`. Launcher PID: 36336; serving PID: 35884.
- Command, with private locations replaced by placeholders:

  ```text
  <existing_venv>/Scripts/python.exe -m cbs_transcriber --no-browser --port 8765 --data-dir "<library>" --projects-dir "<existing_project_parent>"
  ```

  Standard output/error logs and the exact operational command/source metadata are stored
  outside the repository under `<backup>`. No dependency sync or credential-store change occurred.

## Backups and baseline

Read-only `/api/storage` and `/api/jobs` checks identified the existing library and showed
no queued/running work. A direct read-only database query repeated the full idle check
immediately before stopping the process.

SQLite's backup API produced `<backup>/online-before-stop.sqlite3`, followed by the
authoritative `<backup>/offline-before-migration.sqlite3` after process termination.
Both backups passed `PRAGMA integrity_check`. The authoritative backup also passed
`PRAGMA foreign_key_check`, with no findings. Schema remained 11 at this point.
The backups and operational manifest are outside the repository, under `<library>/backups`.

The preservation manifest covers all columns and rows of every existing table, with a
canonical logical SHA-256 per table. It contains no transcript payloads. There are 192 rows
across 16 existing tables:

| Table | Rows before activation |
| --- | ---: |
| annotation_review_events | 0 |
| annotation_runs | 0 |
| annotation_items | 0 |
| codebook_versions | 0 |
| collections | 0 |
| jobs | 76 |
| native_batch_runs | 1 |
| note_versions | 17 |
| projects | 2 |
| providers | 13 |
| recordings | 15 |
| result_hints | 12 |
| result_trash | 0 |
| result_versions | 31 |
| settings | 6 |
| transcript_results | 19 |

## Startup observations and post-activation checks

The clean committed runtime worker invokes `runtime_capacity.refresh_blocker` while idle.
Eight historical legacy enhancement journals have recorded remote state `RUNNING`, so an
ordinary startup checks their owned allocation status. Activation was briefly held when
this was identified. The coordinating agent clarified that routine owned status GETs and
their journal updates are within the requested restart, while new processing, retries,
uploads, downloads and cleanup actions are not part of this activation.

The replacement passed `/api/health`; `/api/storage` retained the exact library and project
parent; every returned job includes `runtime_batch_id`, including a null value for legacy
jobs. The database is schema 12 and passes `integrity_check` and `foreign_key_check`.

Comparison with the authoritative backup established:

- Every preexisting project, recording, transcript result, result version, note version,
  provider, native batch, hint, Trash, annotation and collection row is logically identical.
  The original counts above are unchanged for these tables; no old row was removed.
- All 76 old jobs retain their state, chunks and frozen inputs/options. Eight job rows differ
  only in `updated` and the allowed owned status/capacity observation fields in `ucloud_run`.
  No old job was retried by activation.
- Provider configuration, processing, routing and realignment settings are unchanged.
  One Activity visibility timestamp changed while the app was in use. There is no provider
  configuration backfill or selection change in this comparison.
- Full per-table hashes, preservation details and observed additive state are retained in
  `<backup>/after-preservation.json`, without copying payloads into this document.

The listener became healthy at approximately 21:31:01 UTC. Five new enhancement jobs were
created together at 21:31:43 UTC, after startup, with one runtime owner and five members and
attempts. At the preservation snapshot there were 81 job rows and those five new jobs were
running. This is concurrent application use; the activation procedure issued only local
read-only API requests and did not enqueue work. Existing server configuration disables
HTTP access logs, so no method/path log exists to independently attribute that enqueue.
The active work was preserved without another stop, retry or browser reload.

The running managed checkout remains attached and must not be archived while it serves the
app. The original checkout's ongoing uncommitted changes remain separate from this process.
