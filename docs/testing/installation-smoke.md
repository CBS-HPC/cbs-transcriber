# Offline source-install smoke check

After completing [README's clone or source-ZIP installation](../../README.md#install-and-start-windows-and-macos) with Python 3.13 and
`uv sync --locked`, run from the folder containing `pyproject.toml`:

```text
uv run --no-sync python -B scripts/check_installation.py
```

`--no-sync` keeps this check from changing or repairing the environment. Missing dependencies
must be resolved through the installation instructions. Git, a browser, an API account, model
weights and a separate FFmpeg installation are not required by the check. It opens no listening
server and does not contact the running app.
The helper is [scripts/check_installation.py](../../scripts/check_installation.py).

To keep a shareable UTF-8 JSON report, choose a new filename:

```text
uv run --no-sync python -B scripts/check_installation.py --report installation-smoke.json
```

The report is created exclusively: an existing file, including a symlink, is never overwritten.
Its parent folder must already exist. Exit codes are `0` for a passing core smoke, `1` for a
failed core smoke, and `2` when the requested report cannot be created/written. A zero-length
report left by an interruption is incomplete; choose another filename when rerunning.

The helper reports process architecture, numeric OS/Python versions, installed package versions
without local build labels, and bundled FFmpeg availability/version. It emits fixed failure
codes instead of exception messages, paths, logs, recording IDs, credentials or usernames.
FFmpeg selection uses the locked package's platform filename directly; a missing bundle or
failed/timed-out 10-second version probe refuses without trying conda or system FFmpeg.
It ignores inherited library, model-catalog and FFmpeg overrides during its isolated checks,
then restores those environment values. It never discovers or opens a real library or reads
the credential store.

The smoke creates and removes a temporary library containing one second of synthetic silent
WAV audio. It checks core startup, upload, manual media preparation through the app's media
worker, a literal Unicode transcript import/export, and an exact 100-byte HTTP playback range
through the in-process app. Background workers stay off; FFmpeg conversion has a 30-second
cancellation bound. Temporary media, SQLite files and conversion logs are removed afterward.
Except for an explicitly requested report, it writes only within its temporary working area
and suppresses Python bytecode writes.

The report's `core_status=pass` is deliberately narrow. It does **not** qualify background
queue scheduling, browser decoding or audible playback, transcription quality, GPU/local model
inference, Keychain/Windows credential-store integration, remote providers, shortcuts,
installation/update/uninstall, or a clean-machine installation. No models are imported or
downloaded; inference packages are inspected through installed metadata only.

The policy field expects CPU local-inference packages on Windows x64 and native Apple Silicon
with macOS 14+. Intel macOS is core-only and may omit the incompatible local inference packages.
Apple GPU/MPS support is not claimed. An x86_64 process on an Apple Silicon machine may be
running under Rosetta; the helper reports process architecture rather than inferring hardware.
Missing local packages remain visible even if the core smoke passes.

For T94 acceptance, run this on each actual clean Windows x64, Apple Silicon/macOS14+ and Intel
Mac installation, retain its report, then complete real browser/playback, source-clone/source-ZIP
and update/uninstall checks separately. Record OS/device and route in the acceptance notes
outside this redacted report. Mocked platform-policy tests and a Windows developer-environment
pass are not actual Mac or clean-machine qualification.
