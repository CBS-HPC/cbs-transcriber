# CBS Transcriber

CBS Transcriber helps you turn Danish/English research recordings into transcripts, review
text while listening, and export your work to Word, NVivo and other formats. It runs on your
computer in a browser; transcription can use OpenAI or UCloud.

The current release is **MVP 0.1.0**. Some unfinished features are hidden. Windows startup
and selected workflows have been exercised on the development PC; clean-device Windows and
Mac acceptance is still pending. See the [release checks and limits](docs/verification-2026-11-10-mvp-release.md).

## Install and start (Windows and macOS)

Use the versioned release with **uv**, which installs the app in its own environment.
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

Run this same command in PowerShell or Terminal:

```text
uv tool install --python 3.13 --no-config --link-mode copy --constraints "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt" "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl"
```

Keep the wheel and constraints from the **same release**, including `--link-mode copy`.
Copy mode avoids Windows Cloud Files/hardlink failures such as error 396. Installation
fetches packages; it does not start transcription or download inference model weights.
See [download alternatives and troubleshooting](docs/installation.md) if it stops.

### 3. Start the app

```text
cbs-transcriber
```

The browser opens at **http://127.0.0.1:8765**. Keep the terminal open; **Ctrl+C** stops the
app. Closing its browser tab does not stop it. Save edits and stop any earlier app copy
before starting this installation. If the command is missing, run `uv tool update-shell`
and reopen the terminal.

Optional Desktop launcher, on either Windows or macOS:

```text
cbs-transcriber --create-shortcut --shortcut-location desktop --profile mvp
```

Windows creates a `.lnk`; macOS creates a `.command` file that opens Terminal. Omit
`--shortcut-location desktop` for the Start menu or `~/Applications`. Existing launchers
are never overwritten. See [launcher options and removal](docs/installation-shortcuts.md).

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
`--reinstall-package cbs-transcriber` to its install command. Keep `--link-mode copy`.
An unqualified `uv tool upgrade` does not choose the approved wheel/constraints pair.
Recreate a launcher if its installation was replaced or moved. See [update details](docs/installation.md#update-or-reinstall).

## Uninstall

Stop the app with **Ctrl+C**, then run:

```text
uv tool uninstall cbs-transcriber
```

Remove the optional `CBS Transcriber.lnk` or `CBS Transcriber.command` launcher separately.
**Your libraries, recordings, backups, cached models and saved credentials remain.** Removing
the app does not stop remote UCloud jobs or erase research data. For optional data or key
removal, follow [what remains and how to remove it deliberately](docs/storage-and-removal.md#uninstall-and-optional-cleanup).

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
