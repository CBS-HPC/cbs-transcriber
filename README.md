# CBS Transcriber

CBS Transcriber helps you transcribe research recordings, review the text while listening,
and export your work to Word, NVivo and other formats. The app runs on your computer and opens
in a browser. Supported languages depend on the transcription model. The CBS pilot focuses on
Danish and English.

The current release is **MVP 0.1.0**. Some unfinished features are hidden. Windows startup
and selected workflows have been exercised on the development PC; clean-device Windows and
Mac acceptance is still pending. See the [release checks and limits](docs/verification-2026-11-10-mvp-release.md).

This local post-MVP branch is **unreleased**. It disables the direct OpenAI service and adds
first-use UCloud/optional-HF setup with background model preparation. The published MVP and
release wheel are unchanged. This guide now documents the requested local-folder installation;
see the [branch verification](docs/verification-2026-11-11-post-mvp-morning.md).
The local **MVP v2 preview 1** adds larger transcript/editor text; see its
[checks and loaded-build limits](docs/verification-2026-11-12-mvp-v2-preview.md).

## Install and start (Windows and macOS)

Use the versioned release with **uv**, in a local environment under your home folder.
You do not need Git, Node.js or a separate FFmpeg installation. Allow about **6 GB** for
installation, plus download/cache space, recordings and optional models. Keep your working
library outside OneDrive/iCloud.

| Computer | Release scope |
| --- | --- |
| Windows 10/11 x64 | App and CPU local inference packages. |
| Apple Silicon, macOS 14+ | App and CPU local inference packages; Apple GPU/MPS is unsupported. |
| Intel Mac | OpenAI/UCloud, import, review and export; local inference packages are excluded. |

Use standard CPython 3.13. On Apple Silicon, run Terminal natively rather than through Rosetta.
Actual Mac installation, playback and launcher checks remain pending.

### 1. Check uv and install Python

Open **PowerShell** on Windows or **Terminal** on macOS:

```text
uv --version
```

If this reports **0.9.29 or later**, keep it. If uv is missing or older, use your approved
software route; managed computers may need IT. The official installers are:

Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS Terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installing uv, reopen the terminal and check `uv --version` again. Then run:

```text
uv python install 3.13
```

This provides a per-user Python for uv. It does not replace your Conda or system environment.
If Windows still finds an older uv, check `Get-Command uv -All`; follow the
[PATH help](docs/installation.md#uv-or-the-app-command-is-not-found) before reinstalling.

### 2. Install MVP 0.1.0

On **Windows**, paste this into PowerShell. It works even when the terminal opens on a
OneDrive-backed Desktop. It creates only `CBS Transcriber\App` under your user folder;
the sibling `Library` and `Projects` folders are preserved. If App already exists, stop and
use the [update or repair instructions](docs/installation.md#update-or-reinstall).

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $appDir = Join-Path $env:USERPROFILE 'CBS Transcriber\App'
    if (Test-Path -LiteralPath $appDir) { throw 'App already exists. Follow the update or repair guide.' }
    New-Item -ItemType Directory -Path $appDir -ErrorAction Stop | Out-Null
    $appPython = Join-Path $appDir '.venv\Scripts\python.exe'
    uv --no-config venv --managed-python --python 3.13 (Join-Path $appDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed. Stop and review the error.' }
    uv --no-config pip install --python $appPython --link-mode copy --constraints "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt" "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl"
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Stop and review the error.' }
    & $appPython -m cbs_transcriber --help
    if ($LASTEXITCODE -ne 0) { throw 'Startup check failed. Keep its error for the maintainer or IT.' }
    & $appPython -m cbs_transcriber --create-shortcut --shortcut-location desktop --profile mvp
    if ($LASTEXITCODE -ne 0) { throw 'The app is installed, but shortcut creation failed. See launcher help.' }
}
```

On **macOS**, use the [matching local-folder commands](docs/installation.md#macos-local-folder-installation).
No environment activation is required. Managed devices may still require approval to run
Python, its dependencies or shortcut creation; changing folders does not establish a fix
for the reported Windows Access denied error.

Keep the wheel and constraints from the **same release**, including `--link-mode copy`.
Copy mode avoids Windows Cloud Files/hardlink failures such as error 396. Installation
fetches packages; it does not start transcription or download inference model weights.
See [download alternatives and troubleshooting](docs/installation.md) if it stops.

An earlier latest-release installer prototype targets uv's tool environment. It is
**unpublished and does not implement this App-folder workflow**. Use the versioned commands
above; [installer publication and adaptation](docs/installation.md#stable-latest-release-installer)
remain separate work.

### 3. Start the app

Double-click the new **CBS Transcriber** Desktop shortcut. To start from PowerShell instead:

```powershell
$appPython = Join-Path $env:USERPROFILE 'CBS Transcriber\App\.venv\Scripts\python.exe'
& $appPython -m cbs_transcriber --profile mvp
```

The browser opens at **http://127.0.0.1:8765**. Keep the terminal open; **Ctrl+C** stops the
app. Closing its browser tab does not stop it. Save edits and stop any earlier app copy
before starting this installation. The shortcut binds this environment's Python directly;
it does not install/update dependencies at startup or need `cbs-transcriber.exe` on PATH.
Windows creates a `.lnk`; macOS creates a `.command` file that opens Terminal. Existing
launchers are never overwritten. See [launcher options, uv run and troubleshooting](docs/installation-shortcuts.md).

## Set up and use

1. **Create a project.** Choose Standard or Protected, a language and a local storage folder.
   Add your audio/video files and watch preparation in **Activity**.
2. **Get a transcript.** Import an existing JSON/CSV transcript through **File Settings**, or
   connect OpenAI/UCloud in **Settings**, choose Provider and Model, then **Transcribe selected**.
   Provider runs send audio to the chosen service and may incur charges.
3. **Listen and edit.** Open a recording. Click timed text to seek, use **Replay** for one
   passage or **Play transcript** to keep listening. Double-click a word or choose **Edit**,
   change the text and **Save**. Missing timing does not prevent text corrections or valid
   between-word splits; uncertain words remain untimed.
4. **Review.** Rename speakers, mark passages **Checked** or **Needs review**, and use
   **Next to review**. **Versions** keeps earlier saved states and other results.
5. **Choose the Working version.** Project exports use this saved version. The first result
   normally becomes Working; edits to the current Working version advance it. Check the
   designation when reviewing alternatives or restoring older text.
6. **Export.** Open **Export Settings** and select recordings/formats. Originals are optional
   and start unchecked. ZIPs prepare in **Activity**, where you download them.

No provider key is needed to import, review or export an existing transcript. Settings stores
remembered keys in Windows Credential Manager or macOS Keychain. A Hugging Face read token
and accepted model terms are needed only for the first speaker-model download.

[Provider setup](docs/provider-setup.md) · [Review guide](docs/review-guide.md) ·
[Exports and NVivo](docs/export-guide.md) · [Colleague walkthrough](docs/mvp-tester-guide-2026-11-11.md)

## Update

Save edits, finish or stop jobs, make a [complete backup](docs/portable-backups.md), then stop
the app. Use the next approved release's wheel and matching constraints, adding
`--reinstall-package cbs-transcriber` to the explicit environment's `uv pip install` command.
Keep `--link-mode copy`. Do not replace or move a running environment.
Recreate a launcher if its installation was replaced or moved. See [update details](docs/installation.md#update-or-reinstall).

## Uninstall

Stop the app with **Ctrl+C**, then follow the [uninstall guide](docs/uninstall.md) for the
installation method you actually used. A local-folder install is removed differently from
the earlier `uv tool install` method. Remove its named shortcut separately.
**Your libraries, recordings, backups, cached models and saved credentials remain.** Removing
the app does not stop remote UCloud jobs or erase research data. For optional data or key
removal, follow [what remains and how to remove it deliberately](docs/uninstall.md).

## Help and further details

New installations use `CBS Transcriber/Library` and `CBS Transcriber/Projects` under your
home folder. Existing libraries stay where they are. Use **Library** to see or switch them;
finish jobs first. [Storage, backups and deletion](docs/storage-and-removal.md) explains how
to preserve your data.

- [Installation help, platform limits and command options](docs/installation.md)
- [Source installation, development and evaluation](docs/development.md)
- [Research history and report limits](docs/research-history.md)
- [Service purpose and pilot decisions](docs/cbs-deployment-proposal.md)
- [Current work and remaining acceptance](TODO.md)

This is a single-user local app, not an internet-hosted service. Clean-device/Mac, wider
provider and actual NVivo acceptance remain separate tasks. Public availability does not
supply a broad app reuse licence; that choice and remaining institutional/model terms are
pending. Keep the separate [CBS brand/font notices](src/cbs_transcriber/static/brand/README.md)
and [licensing review](docs/audits/licensing-2026-10-30.md).
