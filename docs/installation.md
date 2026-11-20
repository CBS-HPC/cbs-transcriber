# Installation help

Follow [README — Install and start](../README.md#install-and-start-windows-and-macos) for
prerequisites. This post-MVP guide uses one local environment at
`<user home>/CBS Transcriber/App/.venv`. It installs the released wheel and matching
constraints; Git, Node.js, a separate FFmpeg installation and environment activation are
unnecessary.

## Folder layout

```text
<user home>/CBS Transcriber/
  App/.venv/    application and its Python dependencies
  Library/     default working library
  Projects/    default project recordings and results
```

The commands calculate the user-home path, so the terminal may start on the Desktop or
elsewhere. Keep App, the active library and project folders outside OneDrive/iCloud. A
Desktop shortcut may be in a synced Desktop folder; its destination is local to this PC.
Existing libraries retain their selected locations. Installation does not move, open or
reset them.

The commands below use the currently published [MVP 0.1.0 release](https://github.com/CBS-HPC/cbs-transcriber/releases/tag/v0.1.0).
They do not publish or install the unreleased post-MVP branch. Local-folder installation is
a documented workflow, not evidence that the colleague's Windows Access denied error is
fixed. Its cause and real CBS-managed/personal Windows and Mac acceptance remain open.

## Fresh Windows installation

First check `uv --version` (0.9.29 or later) and install approved CPython 3.13 as described
in README. On managed PCs, use CBS-approved prerequisite routes. Save work and stop other
CBS Transcriber app copies before first launch.

Paste this whole block in PowerShell. An existing App folder is refused, including a
partial installation: use the update/repair guidance below instead of overwriting it.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $appDir = Join-Path $env:USERPROFILE 'CBS Transcriber\App'
    $appPython = Join-Path $appDir '.venv\Scripts\python.exe'
    $wheel = 'https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl'
    $constraints = 'https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt'
    if (Test-Path -LiteralPath $appDir) {
        throw 'The App folder already exists. Use the update/repair instructions.'
    }
    New-Item -ItemType Directory -Path $appDir -ErrorAction Stop | Out-Null
    uv --no-config venv --managed-python --python 3.13 (Join-Path $appDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed; stop here.' }
    uv --no-config pip install --python $appPython --link-mode copy --constraints $constraints $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed; stop here.' }
    & $appPython -m cbs_transcriber --help
    if ($LASTEXITCODE -ne 0) { throw 'Startup check failed; keep the error for troubleshooting.' }
    & $appPython -m cbs_transcriber --create-shortcut --shortcut-location desktop --profile mvp
    if ($LASTEXITCODE -ne 0) { throw 'App installed, but shortcut creation failed. See launcher help.' }
}
```

`--managed-python` selects uv-managed Python rather than an active Conda/system interpreter.
The final command creates the Desktop shortcut and exits. It does not start the server.
The help check verifies the module entry point; it does not prove playback, credentials or
all native dependencies work. Continue with normal startup below.

## macOS local-folder installation

Use native Terminal, approved uv 0.9.29 or later and CPython 3.13. Apple Silicon requires
macOS 14+; avoid Rosetta. Intel Mac excludes the incompatible local inference stack.
Paste this whole block; the subshell stops on failure without changing your shell settings.
An existing App folder or link is refused.

```bash
(
    set -eu
    app_dir="$HOME/CBS Transcriber/App"
    app_python="$app_dir/.venv/bin/python"
    wheel='https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl'
    constraints='https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt'
    if [ -e "$app_dir" ] || [ -L "$app_dir" ]; then
        printf '%s\n' 'The App folder already exists. Use the update/repair instructions.' >&2
        exit 1
    fi
    mkdir -p "$app_dir"
    uv --no-config venv --managed-python --python 3.13 "$app_dir/.venv"
    uv --no-config pip install --python "$app_python" --link-mode copy --constraints "$constraints" "$wheel"
    "$app_python" -m cbs_transcriber --help
    "$app_python" -m cbs_transcriber --create-shortcut --shortcut-location desktop --profile mvp
)
```

The final command creates `~/Desktop/CBS Transcriber.command` and exits. If it fails, keep
the exact error; the preceding package installation may already have completed. Do not
repeat the fresh-install block or remove the environment blindly.

## Start the installed app

Double-click the created Desktop launcher, or use the exact installed Python from any folder.

Windows PowerShell:

```powershell
& "$env:USERPROFILE\CBS Transcriber\App\.venv\Scripts\python.exe" -m cbs_transcriber --profile mvp
```

macOS Terminal:

```bash
"$HOME/CBS Transcriber/App/.venv/bin/python" -m cbs_transcriber --profile mvp
```

The browser opens at **http://127.0.0.1:8765**. Keep the terminal open; **Ctrl+C** stops the
app. Closing its browser tab does not stop the app. The launcher calls this environment's
Python directly, so startup does not install/update packages or depend on the global
`cbs-transcriber.exe` command.

If you prefer `uv run`, explicitly select the environment and skip project/environment-file
discovery and synchronization. These commands also work from a Desktop terminal.

Windows:

```powershell
uv run --no-project --no-config --no-env-file --no-sync --python "$env:USERPROFILE\CBS Transcriber\App\.venv\Scripts\python.exe" python -m cbs_transcriber --profile mvp
```

macOS:

```bash
uv run --no-project --no-config --no-env-file --no-sync --python "$HOME/CBS Transcriber/App/.venv/bin/python" python -m cbs_transcriber --profile mvp
```

See [shortcut details](installation-shortcuts.md) for Start menu/Applications destinations
and retained launch arguments.

## Update or reinstall

Save edits, finish/stop jobs, make a complete backup and stop **every app copy** first.
Do not synchronize or replace a running environment. Keep the App folder and environment
in place; virtual environments should be recreated rather than moved/copied.

Use the next approved release's exact matching wheel/constraints pair. For reinstalling the
current 0.1.0 release, use the appropriate command below, then repeat its module help check
and normal startup. A failed command is not successful repair; keep its complete output.

Windows:

```powershell
uv --no-config pip install --python "$env:USERPROFILE\CBS Transcriber\App\.venv\Scripts\python.exe" --link-mode copy --reinstall-package cbs-transcriber --constraints "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt" "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl"
```

macOS:

```bash
uv --no-config pip install --python "$HOME/CBS Transcriber/App/.venv/bin/python" --link-mode copy --reinstall-package cbs-transcriber --constraints "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt" "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl"
```

If fresh installation stopped after environment creation, and that exact environment's
Python runs, retry its package-install command with the same approved pair. If the Python
itself cannot run, stop and diagnose the failure; do not delete the parent folder, change
security policy or repeatedly reinstall. A shortcut failure after successful package
installation can be retried separately using [launcher help](installation-shortcuts.md).

## Download the release files first

If direct download fails, obtain both versioned files from the same release through an
approved browser. Use their absolute downloaded paths in place of `$wheel`/`$constraints`
on Windows or `$wheel`/`$constraints` on Mac; retain the exact environment target and copy
mode. The wheel is `cbs_transcriber-0.1.0-py3-none-any.whl`; its matching constraints are
`cbs-transcriber-0.1.0-constraints.txt`. Never put GitHub tokens in commands.

Constraints select release versions and supported CPU artifacts; they do not make a wheel
installation consume the source `uv.lock` or provide all of that lock's artifact-hash
coverage. Do not drop constraints or add a global PyTorch index to force installation.

## Troubleshooting and acceptance

| What you see | Next step |
| --- | --- |
| App folder already exists | Use update/repair above. Do not overwrite, move or delete it automatically. |
| uv is missing or an old version appears | Reopen the terminal; on Windows inspect `Get-Command uv -All`. Use the approved suitable uv executable; do not change global PATH or Conda silently. |
| `cbs-transcriber.exe` gets Access denied | The local flow uses the exact Python module entry point. Test its `--help` command; success narrows the failing stage but does not establish a policy verdict. |
| Python or shortcut also gets Access denied | Keep the exact error, time and resolved target; ask CBS IT/maintainer about permissions and application-control records. No policy bypass or blanket administrator step is part of this guide. |
| Cloud Files/hardlink error 396 | Retain `--link-mode copy` and use unsynced local App/library folders. Do not clear shared caches or delete research data. |
| Shortcut already exists | Identify the named shortcut's old destination before removing only that shortcut and recreating it. The app installation is separate. |
| Port 8765 is occupied | Save/stop the earlier app if it is yours; do not terminate an unknown process. An intentional different-port launch may use `--port 8766`. |
| Library is already open | Stop its earlier app copy with Ctrl+C. Closing a browser tab or changing ports does not release the library; never delete its lock file. |

Allow about 6 GB for packages plus download/cache space and recordings/models. Package
installation starts no transcription and downloads no inference model weights. uv-managed
Python, the package environment, research data and model caches are separate.

Earlier isolated Windows wheel/uv-tool smoke and disposable COM-shortcut checks passed;
they are not acceptance of this new local-folder layout. Verify a fresh installation,
module startup, actual shortcut click, media playback, OS credentials, restart, stopped-app
update/repair and uninstall under ordinary accounts on CBS-managed and personal Windows,
Apple Silicon/macOS14+ and Intel Mac. A successful `--help` check does not establish all of
these. See [prior release evidence](verification-2026-11-10-mvp-release.md).

## uv or the app command is not found

The local-folder route requires uv only for installation/update, and does not add the
app command to PATH. Start it with its exact Python or shortcut above. If uv is missing,
reopen the terminal after its approved installation. On Windows, `Get-Command uv -All`
shows which executable is selected; the official per-user install normally provides
`$env:USERPROFILE\.local\bin\uv.exe`. Use the intended suitable version rather than
silently changing Conda or global PATH.

For an earlier uv-tool copy only, `uv tool update-shell` can make its installed app command
discoverable after reopening the terminal. This addresses **command not found**, not
**Access denied**. Keep those failure modes separate.

## Earlier uv-tool installations

Existing `uv tool install` environments remain a separate supported legacy layout. They
are not moved by this guide. Inspect `uv tool list` and `uv tool dir`; use the installed
tool's actual Python when diagnosing it, and uninstall only an intentionally retired tool
copy. Do not manually pip-mutate an environment managed by `uv tool`.

## Stable latest-release installer

The branch's earlier `scripts/install_latest.py` prototype targets **uv tool installation**.
It is not the local-folder workflow above, and its stable release asset remains unpublished
(v0.1.0 does not contain it). Do not use its future URL as an installation step. A later
separately approved release must align its installer, guide, package and acceptance before
advertising a stable latest command. See [release preparation](releasing.md).

For uninstall/data-retention choices, use [the uninstall guide](uninstall.md). Developers
retain the separate [source installation](development.md).

## Release profiles and advanced features

Add `--port 8766`, `--no-browser`, `--choose-library`, `--data-dir "<absolute library path>"`
or `--projects-dir "<absolute parent>"` to the module launch command as needed. Explicit
paths affect the selected/default locations as documented in [storage help](storage-and-removal.md).
They do not migrate existing libraries or projects.

The published wheel packages MVP; `--profile mvp` selects it explicitly. Its unfinished
features remain hidden, with existing data/recovery available. Some technical linked guides
describe full-profile features; switching profiles is not evidence those features are
accepted. See [ADR 0145](decisions/0145-release-profile.md).

## Primary references

- [uv environment creation and explicit interpreter targeting](https://docs.astral.sh/uv/pip/environments/)
- [uv run options](https://docs.astral.sh/uv/reference/cli/#uv-run)
- [Python virtual environments and their path-specific lifecycle](https://docs.python.org/3.13/library/venv.html#how-venvs-work)
