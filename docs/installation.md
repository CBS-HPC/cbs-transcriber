# Installation help

Start with [README — Install and start](../README.md#install-and-start-windows-and-macos).
The ordinary-user route is a versioned release wheel and its matching constraints file,
installed with uv. No GitHub account, Git, Node.js or separate FFmpeg is required for the
public release. Use approved prerequisite routes on managed computers.

## Release and platform limits

The current wheel release is [v0.1.0](https://github.com/CBS-HPC/cbs-transcriber/releases/tag/v0.1.0).
Use uv 0.9.29 or later and standard CPython 3.13. Apple Silicon needs macOS 14+ and a native
Terminal; Rosetta may select Intel packages. Windows x64 and Apple Silicon include CPU local
inference packages. Intel Mac installations omit the incompatible pinned local inference
stack and support the core/API/UCloud workflows. Apple GPU/MPS and free-threaded Python are
outside this guide.

Allow about 6 GB for installation, plus download/cache space and your recordings/models.
The MVP profile hides unfinished features; it does not shrink the dependency download.
Installing packages does not start transcription, contact a transcription provider or fetch
inference model weights. uv-managed Python is separate from Conda/system Python; the app gets
its own tool environment.

Fresh isolated Windows wheel checks passed startup, media preparation, transcript import/export
and playback-range checks using an existing Python on the development PC. The user's uv-tool
startup on that PC is also verified. These are not clean-device acceptance. Actual clean
Windows, Apple Silicon/Intel Mac installation, Keychain, playback, updates/removal and launcher
clicks remain pending. A signed installer, notarized Mac app and automatic updater are not
released. See the [release receipt](verification-2026-11-10-mvp-release.md) and
[launcher verification limits](installation-shortcuts.md).

## What the install command preserves

Always pair the wheel and constraints from the same release. The constraints select runtime
versions, the released UCloud client wheel and supported CPU PyTorch artifacts. They do not
make `uv tool install` consume the source repository's `uv.lock` or provide all of that lock's
artifact-hash guarantees. Do not remove constraints to force an incompatible installation or
add a global PyTorch index.

Keep `--no-config --link-mode copy` in release installation, update and retry commands. Copy
mode avoids observed Windows Cloud Files/hardlink error 396; it uses more disk space and does
not change transcription speed. Running the command from another folder does not relocate
uv's tool environment or cache.

## Download the release files first

If direct download fails, open the release page and download both files:

- `cbs_transcriber-0.1.0-py3-none-any.whl`
- `cbs-transcriber-0.1.0-constraints.txt`

Open PowerShell/Terminal in their folder and run:

```text
uv tool install --python 3.13 --no-config --link-mode copy --constraints "cbs-transcriber-0.1.0-constraints.txt" "./cbs_transcriber-0.1.0-py3-none-any.whl"
```

A missing/private release may return 404. Confirm the release with the maintainer. If access
requires sign-in, download through your signed-in browser; never put a GitHub token in a
command. GitHub CLI is unnecessary.

## uv or the app command is not found

After installing uv, reopen the terminal and run `uv --version`. If Windows still finds an
older copy, inspect:

```powershell
Get-Command uv -All
& "$env:USERPROFILE\.local\bin\uv.exe" --version
```

Conda or another uv may be first on PATH. If the official installer's copy is suitable, use
`& "$env:USERPROFILE\.local\bin\uv.exe"` in place of `uv` in subsequent PowerShell commands.
There is no need to reinstall it or change Conda/global PATH just because another copy appears
first. The version floor is not a guarantee about every future uv resolver release.

If `cbs-transcriber` is missing after successful installation:

```text
uv tool update-shell
```

Reopen the terminal and try again. If managed-device policy blocks these steps, ask IT.
The [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/),
[Python guide](https://docs.astral.sh/uv/guides/install-python/) and
[tool guide](https://docs.astral.sh/uv/guides/tools/) explain these separate installations.

## Other installation or startup problems

| What you see | Next step |
| --- | --- |
| Cloud Files/hardlink error 396 | Repeat the matching release command with `--link-mode copy`. Do not delete your library or clear the whole cache. |
| A partial installation left a conflicting app command | Confirm the conflicting executable belongs to CBS Transcriber before adding `--force` to the matching install command. |
| Wheel or constraints file not found | Check both exact filenames and open the terminal in their download folder. |
| No compatible package or constraint resolution fails | Check CPython 3.13, architecture and macOS version. Retain the constraints. |
| Permission denied | Use a writable per-user location; ask IT about managed-device restrictions. |
| Browser did not open | Read any terminal startup error, then visit `http://127.0.0.1:8765`. |
| Port 8765 is occupied | Use the existing app if it is yours. For another application, use `cbs-transcriber --port 8766`. |
| This library is already open | Save edits, finish/stop jobs and press Ctrl+C in the earlier app's terminal. Closing its tab or changing ports does not release the library. Do not delete its lock file. |

## Update or reinstall

Save edits, finish or stop processing, create a complete backup, then stop the app. Do not
replace or synchronize the environment of a running app.

For the next approved release, use that release's wheel and matching constraints with
`--reinstall-package cbs-transcriber`. An unqualified `uv tool upgrade` does not select those
assets. To reinstall the current release, use:

```text
uv tool install --python 3.13 --no-config --link-mode copy --reinstall-package cbs-transcriber --constraints "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs-transcriber-0.1.0-constraints.txt" "https://github.com/CBS-HPC/cbs-transcriber/releases/download/v0.1.0/cbs_transcriber-0.1.0-py3-none-any.whl"
```

Start the app and refresh the browser. HTML/JavaScript/CSS are served without caching.
An installation check does not require paid transcription or a model download. Recreate a
launcher if its environment was replaced/moved; remove only its old named file first.
See [uninstall and retained data](storage-and-removal.md#uninstall-and-optional-cleanup).

## Startup options

```text
cbs-transcriber --port 8766 --no-browser
cbs-transcriber --choose-library
cbs-transcriber --data-dir "D:\My local transcription library"
cbs-transcriber --profile mvp
```

`--choose-library` asks at startup. `--data-dir` opens a particular library for this launch
without changing the remembered library. `--projects-dir` changes the default parent for new
projects; existing projects stay where they are. See [storage locations](storage-and-removal.md).
On macOS, use an absolute Mac path such as `/Users/yourname/Research/Library`.
For source installations, prefix commands with `uv run --locked`.

## Release profiles and advanced features

The published wheel packages MVP. `--profile mvp` selects it explicitly. Profile precedence is
the command option, then `CBS_TRANSCRIBER_PROFILE`, then packaged `catalogs/release.json`, then
`full`. The development `main` source uses full. Settings shows a non-full profile for reports.

MVP hides passage Re-transcribe, Topics & Sentiments, Research Report, CBS Runtime transcription,
UCloud timing/speakers/repairs, Automatic rules, alternate Python runtime, Realign after saving,
comparison Use wording, UCloud backup upload/retention, restore and Custom/UCloud vLLM setup.
Existing results, exports and recovery actions remain available. Hidden new requests are refused.

Some linked technical guides describe full-profile features. A maintainer can explicitly use
`--profile full` for separately arranged work, such as backup-restore testing; this does not
establish acceptance of those features. See [ADR 0145](decisions/0145-release-profile.md).
For source/ZIP installation and contributor checks, see [development](development.md).
