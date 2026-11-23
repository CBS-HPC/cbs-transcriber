# CBS Transcriber MVP v2 rc3 · Windows pilot guide

Use a short test recording you have permission to process. Save your work and close any running copy of CBS Transcriber before you start. The commands below use a separate test library.

## Install and open

Open PowerShell in the folder containing both rc3 files. In File Explorer, open that folder, type `powershell` in the address bar and press Enter. Use a local folder with space for the packages and recordings.

Run `uv --version` first. If PowerShell cannot find uv or the version is older than 0.9.29, follow your organisation's installation instructions, then open PowerShell again. On a managed CBS computer, contact CBS IT if the installer or app reports **Access denied**.

Paste this block from the folder containing the wheel and constraints file. It checks for both files and stops if an App folder already exists. If that happens, contact the maintainer for update instructions.

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $wheel = (Get-Item -LiteralPath '.\cbs_transcriber-0.2.0rc3-py3-none-any.whl').FullName
    $constraints = (Get-Item -LiteralPath '.\cbs-transcriber-0.2.0rc3-constraints.txt').FullName
    $app = Join-Path $env:USERPROFILE 'CBS Transcriber\App'
    if (Test-Path -LiteralPath $app) { throw 'The App folder already exists. Ask for update instructions.' }
    uv python install 3.13
    if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
    New-Item -ItemType Directory -Path $app | Out-Null
    $venv = Join-Path $app '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    uv --no-config venv --managed-python --python 3.13 $venv
    if ($LASTEXITCODE -ne 0) { throw 'Environment creation failed.' }
    uv --no-config pip install --python $python --link-mode copy --constraints $constraints $wheel
    if ($LASTEXITCODE -ne 0) { throw 'App installation failed.' }
}
```

Start the app from any PowerShell folder. Leave PowerShell open while you work. Press **Ctrl+C** to stop the app.

```powershell
$root = Join-Path $env:USERPROFILE 'CBS Transcriber'
$python = Join-Path $root 'App\.venv\Scripts\python.exe'
$pilot = Join-Path $root 'rc3-test'
$launch = @('--profile', 'mvp', '--data-dir', (Join-Path $pilot 'Library'), '--projects-dir', (Join-Path $pilot 'Projects'))
& $python -m cbs_transcriber @launch
```

Once startup works, you can create a Desktop shortcut with the same settings. Run this in the same PowerShell window after stopping the app. If a shortcut already exists, use the launch command or ask the maintainer to check which installation it opens.

```powershell
& $python -m cbs_transcriber @launch --create-shortcut --shortcut-location desktop
```

## Try these tasks

1. **Transcribe.** Connect UCloud in the setup dialog or Settings. Create a project and add a recording. Start transcription and follow the file in **Activity** through to its result.
2. **Edit and review.** Double-click a word. Save or cancel a correction. On a timed passage, place the cursor inside a word and check **Start (s)** and **End (s)**.
   Try a small valid time change, save the passage and undo it. Split between words,
   merge passages and undo a change. Play the recording and check word following. Mark passages and try **Show** and **Next to review**.
3. **Export.** Export readable text or Word, plus a project ZIP. Close Export Settings while the ZIP prepares, then download it from **Activity**. Check that the ZIP contains the selected formats and includes original files only if you selected them.
4. **Backup and deletion.** Make a local backup from **Settings → Backup** and follow it in **Activity**. On a disposable project, confirm Delete and follow the card. Try **Keep project** while it is offered. Deletion is permanent once the project has been removed from the library.

Report the build label shown in Settings, what you did, what you expected and what happened. Include the passage time for playback issues and the exact error for setup problems. Keep API keys and interview content out of reports.

To uninstall this test copy, stop the app and remove its shortcut and the `.venv` folder inside `CBS Transcriber\App`. Keep `rc3-test`, `Library` and `Projects` if you want to retain your work.
