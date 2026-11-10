# Source installation and development

Ordinary users should follow the [release-wheel guide](../README.md#install-and-start-windows-and-macos).
Use source installation when developing or when the maintainer recommends it. Work in a
fresh writable local folder outside OneDrive/iCloud, with approved uv/Git prerequisites and
standard Python 3.13. Never synchronize/reinstall a running app's environment.

## Clone or source ZIP

```text
git clone --branch main https://github.com/CBS-HPC/cbs-transcriber.git "CBS Transcriber"
cd "CBS Transcriber"
uv sync --locked
uv run --locked cbs-transcriber --profile mvp
```

`main` is development source, not the published wheel's exact code. Use a verified release
tag/commit when reproducing a release. Git is needed only for clone/update operations.
The [main source ZIP](https://github.com/CBS-HPC/cbs-transcriber/archive/refs/heads/main.zip)
is a fallback without Git: extract to a fresh folder containing `pyproject.toml`, `uv.lock`
and `src`, then use the same locked uv commands. [Earlier wheel-source package-plan evidence](verification-2026-10-29-ucloud-release-wheel.md)
does not establish clean-machine acceptance.

To update a stopped, backed-up clone, use `git pull --ff-only`, then `uv sync --locked`.
Preserve local changes; do not reset them or reuse another installation's `.venv`. Update a
ZIP installation in a fresh folder while preserving external libraries/projects.
Use `uv run --locked cbs-transcriber --create-shortcut --profile mvp` for an optional
[source launcher](installation-shortcuts.md).

If an empty `.venv/cbs_transcriber-<version>.dist-info` folder has an Access denied error on
Windows, stop the app and inspect that exact folder's Read-only attribute. Clear it only on
that folder, remove it only if empty and retry `uv sync --locked`. Keep the repair confined
to `.venv`; preserve permissions and all library/project data. Prefer an unsynced source folder.

## Verification and architecture

```text
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run playwright install chromium
uv run pytest -m browser -o addopts=''
```

Tests use temporary libraries, synthetic/licensed public fixtures and mocked providers.
Routine tests make no paid provider calls. Browser checks start a local server and need
Chromium; users do not need Playwright. Frontend files use Prettier 3.6.2 with LF line endings;
there is no frontend build or Node requirement for ordinary use. Keep the generated `uv.lock`
in Git; do not edit it manually.

Read [AGENTS](../AGENTS.md), [SPEC](../SPEC.md), [TODO](../TODO.md),
[architecture decisions](decisions/README.md) and [terminology](../CONTEXT.md) before changes.
Use synthetic libraries for development/migration checks. Activating an existing real library
is a separate idle, backed-up operation. Current admission work is recorded in SPEC/TODO;
old original-inspection prerequisite notes are not the current activation status.

Public commits must descend from the clean public history. Earlier development history is
private in CBS-HPC/cbs-transcriber-private-history; never push those old ancestors/branches/tags
to the public repository. Read [brand/font notices](../src/cbs_transcriber/static/brand/README.md)
and the [licensing audit with publication update](audits/licensing-2026-10-30.md). Public
availability does not supply an app reuse licence or institutional/model clearance.

To package a source MVP profile on its release branch, run
`uv run python -m cbs_transcriber.release_profile mvp --label "MVP rc1"`, then commit the profile
file. [Profile rules](installation.md#release-profiles-and-advanced-features) preserve reads,
exports and recovery while hiding unfinished new work. The app serves loopback only and
rejects cross-origin mutations; it is a single-user app, not shared internet hosting.

## Reference evaluation

The repository includes five licensed Danish FLEURS clips, 64 seconds total. Their
[sources, hashes, licence and scoring limits](../tests/fixtures/fleurs_da/README.md) are preserved.
Score saved predictions without a provider request by supplying all five IDs as a UTF-8 JSON
object, for example `{"da_dk_0000": "recognized text", "da_dk_0001": "..."}`:

```text
uv run python scripts/evaluate_fleurs.py score predictions.json --output report.json
```

An explicitly authorized live comparison can use the configured running app:

```text
uv run python scripts/evaluate_fleurs.py run --app-url http://127.0.0.1:8765 --allow-api-calls --output evaluation-results/whisper-da-001
```

This sends all five public clips to the selected provider, may incur charges and creates an
evaluation project. Use the actual app port and a new output folder. The runner validates
accepted settings and scores the producer's immutable creation version, independent of later
edits/enhancements. Reports retain safe receipts, hashes, predictions and WER/CER; keys are
excluded. Legacy `.nvivo.txt` names contain readable text, not native NVivo rows. Missing or
inconsistent producer evidence leaves an incomplete run. Inspect `run.json` and Activity before
retrying: requests may already have arrived and there is no automatic API retry.

WER/CER compare normalized reference wording; this tiny read-speech fixture does not establish
interview quality, word timing or speaker accuracy. For reviewed word/speaker evidence and
explicit runtime/memory observations, use [reviewed evaluation](reviewed-evaluation.md).
Real model baselines and representative research-quality acceptance remain separate.
