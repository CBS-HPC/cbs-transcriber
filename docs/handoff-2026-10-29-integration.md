# Handoff — integration branch, 29 October 2026 (evening)

Codex stopped at its spend cap (11:51 UTC) with work on seven branches. Claude combined it on
**`claude/integration`** (worktree `C:/Users/kgp.lib/claude-worktrees/cbs-integration`).
The running app is still on `main` (schema 13) from the main checkout; it was not activated,
and the live library was only read.

## What `claude/integration` contains

- Codex's integration (`codex/delete-cancels-jobs`): Delete cancels jobs, Working versions,
  local timing capture, Runtime cohorts and artifact hooks, remote preparation, schema 14/15,
  portable capture/restore/UCloud storage (ADR 0096) with routes wired.
- Merged follow-ups (each branch kept): `claude/portable-ui` (backup route/browser tests and six
  running-app fixes), `claude/runtime-retry-race` (Windows `\\?\` realpath containment race in
  `Store.directory`), `claude/provider-capture` (API producer evidence, ADR 0097),
  `claude/activity-remove` (Remove from Activity, one card per batch request and per multi-file
  upload, card layout fix, ADR 0099), `claude/runtime-consent` (ask once at Transcribe selected,
  standing "Prepare UCloud models automatically", request-wide Prepare, ADR 0098).
- Also: the two install-guide/release-wheel commits from `feature/runtime-batches-and-review-layout`,
  transient original read errors reported as `read_failed`, the "Added to the local queue" note
  removed, and tests updated for Working-version defaults.
- Bug memory: SPEC B265–B267, B275–B284; invariants V160–V163.

## Verification

- Full unit suite on the final merge: 3305 passed, 20 skipped, 1 load-only failure
  (`test_runtime_model_reuse_evaluation::test_actual_worker_constructor_counts_and_exact_stage_bytes`,
  Windows rename permission error while browser tests ran in parallel; passes alone).
  `test_enhancement.py::test_v32_runtime_check_and_hf_settings_are_redacted` can also time out
  under heavy load (real 90 s runtime check; 65 s unloaded).
- Browser suite: 191 passed before the consent merge; its 4 failures (pre-Working tests) were
  fixed and pass. Final full browser suite on 83599ed: 197 passed, 0 failed. Pushed to origin.
- After merging native UCloud Transcriber capture (5998b8b, ADR 0100): 3317 passed, 20 skipped,
  2 load-only failures that pass alone (`test_repair_cloud` test-side directory rename hit
  WinError 5; `test_runtime_auto_batches` retry wait timed out under load).
- Ruff check/format and Prettier 3.6.2 clean. Pinned portable sources verified with
  `scripts/repin_portable_semantics.py` (LF-normalised pins; `core.autocrlf=true`).

## Next steps

1. **User approval required:** activation. Back up the live library, wait until no job is
   queued/running, stop the app, move the running checkout to the integrated code (schema
   13 → 15), check every preexisting row is preserved, restart, reload the browser. The venv
   needs `uv sync --locked` (ucloud-workflow now comes from the release wheel).
2. User test: a multi-file Røst batch should ask once, show one Activity card and run all files;
   the 11 already-stopped siblings in the live library continue with one Prepare.
3. Open work: native UCloud Transcriber producer evidence (no capture policy yet); live
   acceptance items in TODO.md (Runtime model reuse/MIG, many-file preparation, large Repair all,
   Danish evaluation, deployment, UCloud backup approval by CBS IT/DPO).

## Machine notes

- `uv sync` hardlink mode fails here (cloud-file hardlink error); use `--link-mode=copy`.
- Bash heredocs with quotes break in the agent shell; write helper scripts to files.
- Never rewrite sources with PowerShell Get-Content/Set-Content (mojibake).
- An unrelated stash `stash@{0}` ("On feature/ucloud-vllm: …") exists; leave it alone.
