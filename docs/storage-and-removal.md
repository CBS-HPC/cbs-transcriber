# Storage, backups and removal

## Find your data

New installations use one visible folder under your home directory:

| Contents | Windows example | macOS example |
| --- | --- | --- |
| Library/database/backups | `C:\Users\<name>\CBS Transcriber\Library` | `/Users/<name>/CBS Transcriber/Library` |
| New project folders | `C:\Users\<name>\CBS Transcriber\Projects` | `/Users/<name>/CBS Transcriber/Projects` |

These defaults avoid Desktop/Documents locations often synced by OneDrive/iCloud. Keep the
working library on local unsynced storage appropriate for your recordings. Existing libraries
stay in their current location. Earlier defaults include `%LOCALAPPDATA%\CBS Transcriber`
and `~/Library/Application Support/CBS Transcriber`; packaged desktop apps may have redirected
Windows app data into their `LocalCache\Local` folder.

The terminal prints the actual paths. **Library** shows known libraries and switches between
them after work is stopped. Opening another library restarts the app and remembers it.
`--choose-library` asks on startup; `--data-dir` or `CBS_TRANSCRIBER_DATA_DIR` selects an
explicit library for that launch. `GET /api/storage` reports the library actually open.

The central `library.sqlite3` is authoritative and includes transcript text, edits, notes,
settings and queue state. Do not edit it or other library files by hand. New project folders
hold managed originals, playback files, transcript sources/raw responses, `project.json` and
readable snapshots under `exports`. Those snapshots are additional copies; opening a copied
`project.json` as an independent project is unsupported. A snapshot-write warning does not
mean the database edit was lost. Older projects may retain `<library>/recordings/<id>`.

## Choose or move project storage

New projects create an ID-owned subfolder beneath the selected parent, leaving unrelated
files alone. A blank parent uses the default Projects folder. `--projects-dir` or
`CBS_TRANSCRIBER_PROJECTS_DIR` changes that default only for new projects.

To move an existing project, open **Project Settings**, choose a new storage parent and
**Save**. Review the move confirmation. Finish/cancel its queued and running work first.
The app copies before switching paths, so large projects need temporary space for both
copies. A failed copy retains the original. Unremoved old copies are reported. Only managed
project files move; the central database and OS credentials stay where they are.

## Back up before an update or transfer

Use **Library → Backups → Create complete backup** after finishing/stopping processing.
This captures the database, originals and available managed research evidence. Save its
small trusted receipt elsewhere; restore requires the commit SHA-256. Older database-only
inventories list originals but do not contain their media bodies.

Restore creates a separate new library and never overwrites/merges an existing one. Some
restore and UCloud transfer controls are hidden in MVP. Real transfer/restore acceptance and
CBS IT/DPO decisions remain separate. See [complete backups and restore](portable-backups.md).

If manually copying for preservation, stop the app and copy the **whole library folder plus
every separately located project folder**. Project snapshots alone are insufficient.
Do not copy an installation's `.venv` to transfer the app.

## Delete recordings or projects

Use **File Settings** to delete a recording, or **Project Settings** to delete a project.
Read the preview and confirmation; project deletion asks for its name. Deletion permanently
removes that managed research data from the app, including transcripts, notes and history.
It is different from recoverable **result Trash**. Source files outside managed storage remain.

Delete cancels captured queued/running jobs and waits for workers to stop. A shared UCloud
allocation affecting other recordings needs extra impact confirmation. The remote-folder
confirmation lists exactly which owned folders will move to UCloud Trash; folders needed by
surviving recordings are retained. It never empties Trash. Uncertain submissions and legacy
single-job journals may need reconciliation first. Closing/leaving the dialog stops further
deletion checks; it does not undo a deletion already committed.

Files are staged locally before database deletion. Recovery restores files for live entries
or clears already-deleted staged files; it does not restart an uncommitted Delete. A failed
physical cleanup leaves **Retry local cleanup** in Activity even after the project is gone.
Unresolved file recovery keeps the library readable but blocks changes. Resolve outstanding
local recovery/remote cleanup before backup, storage relocation or switching libraries.
Remote cleanup can remain independently actionable after local deletion.
Power-loss, cloud-sync and wider filesystem durability acceptance remain pending.

## Uninstall and optional cleanup

Save edits and resolve processing you want stopped. Closing the app or uninstalling it does
not stop a remote UCloud allocation; use its Activity Stop/recovery controls first if required.
Stop the app with **Ctrl+C**, then run:

```text
uv tool uninstall cbs-transcriber
```

Delete only the optional `CBS Transcriber.lnk` or `CBS Transcriber.command` launcher from
Desktop/Start menu/`~/Applications`. Removing a launcher does not uninstall the app.

Uninstall intentionally leaves the following items. They are separate decisions:

| Retained item | If you explicitly want to remove it |
| --- | --- |
| Libraries and project folders, including originals, transcripts, notes/history and local backups | First verify an independent backup and identify every actual library/project location. Prefer the app's preview/confirmation flows for managed deletion. Remove only the exact folders whose research data you intend to erase; never infer ownership from a parent folder's name. |
| Downloads, exported documents and separately copied backups | Review and remove those specific copies yourself when your retention policy permits. |
| Inference-model caches | Identify which model/cache this app used and whether another application shares it before deleting specific cached models. Hugging Face commonly uses `~/.cache/huggingface/hub`, but environment overrides and alternate runtimes can change it. Clearing caches can force large downloads later. |
| Saved keys/tokens | In Windows Credential Manager or macOS Keychain, inspect entries belonging to **CBS Transcriber** and remove only the intended credentials. Accounts may use endpoint hashes; ask the maintainer to identify them if unclear. Another app installation may share them. Deleting a provider configuration alone does not remove its shared key. |
| uv-managed Python, uv and uv's package cache | Keep them if another tool/project uses them. They are not part of this app's tool uninstall. |
| Preferences/library-list records and environment variables | They may remain outside the removed tool. Review only app-specific records with the maintainer. Uninstall does not remove a separately configured `OPENAI_API_KEY`. |
| Remote run files and backups | Review them in the relevant service. The app's cleanup moves owned run files to UCloud Trash; uninstall does not empty Trash or erase cloud backups. |

Removing a local key is not revocation at its service. Revoke/rotate unwanted OpenAI, UCloud
or Hugging Face tokens in the issuing account when that is your intended outcome. Do not
delete unrelated OS credentials or clear shared model/Python caches as an uninstall shortcut.

## Supported imports

Media includes MP3, WAV, M4A, FLAC, OGG, MP4, MOV, MKV, WebM, MPEG/MPG, MPGA, AAC, WMA and AVI,
subject to the installed FFmpeg decoder. Video needs an audio stream. Playback uses MP3 or
H.264/AAC MP4. Damaged/unsupported files report preparation errors. Media uploads stream to
disk with a 20 GB limit.

Transcript imports accept UTF-8 JSON with a `segments` array, a segment list or plain `text`,
and Whisper/Transcriber CSV with `start`, `end`, `text` and optional `words`/`speaker` columns.
Column order is flexible. Word fields may contain JSON arrays or safe Python literal lists.
Missing/incomplete timing stays partial/untimed. Transcript uploads are limited to 32 MB.
The app has its own parsers/exporters; the earlier external preparation scripts/guides were
retired from the public tree. Real UCloud/NVivo file acceptance remains separate.
