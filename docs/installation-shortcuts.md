# Optional launchers for wheel and source installations

After [installing the app](../README.md#install-and-start-windows-and-macos), create a Desktop
launcher with the same command in **Windows PowerShell** or **macOS Terminal**:

```text
cbs-transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

Windows creates `CBS Transcriber.lnk` on your Desktop; macOS creates
`~/Desktop/CBS Transcriber.command`. Creation exits without starting another app instance.
Omit `--shortcut-location desktop` for the default destinations listed below.

For a developer/source installation, run this from its installed app folder instead:

```text
uv run --locked cbs-transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

This explicit action creates one launcher for the current user, then exits without opening a
library or starting a server. Installation and ordinary startup never create launchers.

| Platform | Default destination | Launcher |
| --- | --- | --- |
| Windows x64 | Your Start menu's Programs folder | `CBS Transcriber.lnk` |
| Apple Silicon/macOS 14+ or Intel Mac | `~/Applications` | `CBS Transcriber.command` |

For a Start menu or home Applications launcher instead, run:

```text
cbs-transcriber --create-shortcut --profile mvp
```

Windows resolves the system's current-user folder, including redirected/localised Desktop
folders; macOS uses `~/Desktop`. Managed computers may restrict PowerShell, Windows Script Host
or script execution. Ask CBS IT if creation or launch is blocked; this flow does not change
computer policy.

Version 0.1.0 leaves the launcher's icon at the system default; it does not automatically
assign a CBS icon. Custom icons are supported by the operating systems, but automatic
assignment needs a separate launcher update and platform verification. It does not require
changing your recordings or reinstalling Python.

Open the Windows launcher from Start or Desktop. On macOS, open `CBS Transcriber.command`
from your home Applications folder or Desktop in Terminal. Keep the terminal open;
**Ctrl+C** stops the app. Normal port/library-lock checks still apply: a launcher neither
reuses nor stops an already-running app.

The launcher binds this installation's Python interpreter and starts
`python -m cbs_transcriber` directly. A wheel/tool installation is checked against its actual
installed distribution; a source installation must retain its source folder and `.venv`.
Clicking performs no uv dependency synchronization or model download. Moving/removing the
installed environment breaks its launcher, so recreate after moving/replacing an installation.

Supported saved options are `--profile`, `--port`, `--no-browser`, `--choose-library`,
`--data-dir` and `--projects-dir`. For example:

```text
cbs-transcriber --create-shortcut --profile mvp --port 8766 --no-browser
```

Explicit data/project paths become absolute at creation. Provider settings, credentials and
current environment-variable values are not copied into the launcher. Without explicit
paths, the app's normal library/project defaults apply.

An existing same-name file or symlink is refused. To recreate or remove a launcher, delete
only the named `.lnk` or `.command` file. Your installation, libraries, projects and recordings
remain. Uninstall a uv tool separately with `uv tool uninstall cbs-transcriber` after stopping
it; remove the obsolete launcher too.

This is a per-user launcher, not a signed/notarized macOS `.app`, packaged installer, automatic
updater or system service. Real clean-device Windows/Mac relaunch acceptance remains pending.
Intel Mac launchers do not add unsupported local inference or Apple GPU execution.

The Windows mechanism follows Microsoft's
[WSH COM shortcut documentation](https://learn.microsoft.com/en-us/powershell/scripting/samples/creating-.net-and-com-objects--new-object-).
The macOS launcher is an executable shell script; see Apple's
[Terminal guide](https://support.apple.com/en-lamr/guide/terminal/apdd100908f-06b3-4e63-8a87-32e71241bab4/mac).

The original source-launcher verification on 30 October 2026 passed 58 focused synthetic/mock
checks. On 10 November, 37 focused source/tool/launcher/profile checks and independent review
passed. A fresh isolated installed wheel created a real Windows COM shortcut in its disposable
tool folder: interpreter, arguments/MVP profile, working folder and no-overwrite behavior were
verified. No actual Desktop/Start menu shortcut was created or clicked. Native Mac execution
and real clean-device/launcher relaunch acceptance remain unverified.
