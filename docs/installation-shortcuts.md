# Shortcuts for local, tool and source installations

The recommended post-MVP local layout is `<user home>/CBS Transcriber/App/.venv`.
[Fresh installation](installation.md) creates a Desktop launcher after package installation
and the module help check. It exits before starting the server. Creation is explicit;
ordinary startup does not create another shortcut.

To create or retry only the shortcut, use the exact installed Python from any folder.

Windows PowerShell:

```powershell
& "$env:USERPROFILE\CBS Transcriber\App\.venv\Scripts\python.exe" -m cbs_transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

macOS Terminal:

```bash
"$HOME/CBS Transcriber/App/.venv/bin/python" -m cbs_transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

Windows creates **CBS Transcriber.lnk** in the system's current-user Desktop folder,
including a redirected/localised OneDrive Desktop. macOS creates
`~/Desktop/CBS Transcriber.command`. A synced shortcut does not install the environment
on another computer; its destination belongs to this installation.

Omit `--shortcut-location desktop` for the Windows Start menu Programs folder or
`~/Applications` on Mac. Both current launchers use system-default icons; custom CBS icons
remain an optional, unimplemented idea.

## What opening the shortcut does

The launcher uses this environment's Python with `-m cbs_transcriber`, its installation
working folder and saved options. It does not call the global `cbs-transcriber.exe` or
run uv synchronization. No activation, dependency installation or model download occurs
merely from opening it. Normal app behavior and its port/library checks still apply.

The app normally opens a browser at http://127.0.0.1:8765. Keep the terminal open;
**Ctrl+C** stops it. A launcher neither reuses nor stops an already-running app copy.

The shortcut remains bound to this installation. Recreate it after moving/replacing an
environment; recreate virtual environments rather than copying them to a new path or PC.
Existing same-name files and symlinks are refused. Check the old destination, remove only
the intended `.lnk`/`.command` file and retry creation. Removing the shortcut does not remove
the app or research data. See [uninstall choices](uninstall.md).

## Saved options

Creation retains `--profile`, `--port`, `--no-browser`, `--choose-library`, `--data-dir` and
`--projects-dir`. Add them to the creation command, for example `--port 8766 --no-browser`.
Explicit data/project paths become absolute when the shortcut is created. Credentials and
current environment-variable values are not copied into it; normal app defaults apply
when no data path is provided.

## Existing uv-tool or source installs

For a uv-tool installation whose console command runs, its existing explicit command is:

```text
cbs-transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

If that `.exe` command is denied on Windows, first diagnose the tool's actual interpreter
without reinstalling:

```powershell
$appPython = Join-Path (uv tool dir) 'cbs-transcriber\Scripts\python.exe'
& $appPython -m cbs_transcriber --help
```

Only if the interpreter/module check succeeds, use that exact `$appPython` with the shortcut
arguments above. The existing shortcut function validates the installed distribution and
interpreter for either wheel-environment layout. Success does not establish CBS policy
approval or prove all dependencies work.

For an already installed developer/source checkout, use its separate locked workflow:

```text
uv run --locked python -m cbs_transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

## Permissions and verification limits

Managed PCs may restrict Python, PowerShell, Windows Script Host, executables or scripts.
If creation or launch is blocked, retain the exact error/target/time and ask CBS IT or the
maintainer. This guide changes no security policy and does not establish that relocating
an environment solves Access denied.

Windows uses [WSH COM shortcuts](https://learn.microsoft.com/en-us/powershell/scripting/samples/creating-.net-and-com-objects--new-object-).
Mac uses an executable shell script opened in Terminal; see [Apple's Terminal guide](https://support.apple.com/en-lamr/guide/terminal/apdd100908f-06b3-4e63-8a87-32e71241bab4/mac).
Neither is a signed installer, notarized Mac `.app`, automatic updater or system service.

Earlier source/tool unit checks and a disposable Windows COM-shortcut creation passed;
actual Desktop/Start-menu click acceptance, native Mac launch and the new local-folder
workflow remain separate real-device checks. See [the release evidence](verification-2026-11-10-mvp-release.md).
