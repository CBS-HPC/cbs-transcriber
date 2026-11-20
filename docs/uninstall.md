# Uninstall CBS Transcriber

Removing the app normally keeps your recordings and transcripts. Choose the instructions
for the installation you actually used; a local `.venv` and a `uv tool` installation are
separate copies. Do not delete the parent **CBS Transcriber** folder to uninstall.

## 1. Save and stop

Save edits and finish or stop local work, then stop every app copy with **Ctrl+C** in its
terminal. Closing a browser tab does not stop the app. Preserve a complete backup first
if you need to keep the research work.

Uninstalling does **not** stop an accepted UCloud job. If you want it stopped, use its Activity
controls before closing, or identify and stop your own job through UCloud's web interface
if the app cannot open. Remote files, backups and UCloud Trash are separate; uninstall
neither deletes them nor authorizes discarding unsaved outputs.

## 2. Remove the shortcut

Delete only the **CBS Transcriber.lnk** or **CBS Transcriber.command** launcher from the
Desktop, Start menu Programs folder or `~/Applications`, wherever you created it. Check its
destination if more than one app installation exists. Removing a shortcut alone does not
uninstall the app.

## 3. Remove the intended installation

### Local-folder installation

For the local wheel workflow, the environment is:

| System | Application environment |
| --- | --- |
| Windows | `C:\Users\<name>\CBS Transcriber\App\.venv` |
| macOS | `/Users/<name>/CBS Transcriber/App/.venv` |

After stopping the app, open that **exact App folder** in Explorer/Finder and remove only
its `.venv` directory. This removes the app and the packages in that dedicated environment.
On Mac, **Command+Shift+.** shows hidden folders. Keep any diagnostic files you still need;
you may remove the App folder afterward only if it is empty.

Keep the sibling **Library** and **Projects** folders. If you placed research data or a
custom library inside App or `.venv`, preserve it before removal and ask the maintainer
when ownership is unclear. A chosen library/project path can differ from the defaults.

`uv tool uninstall` does not manage this manually created local environment. Do not use
`uv pip uninstall cbs-transcriber` as a full cleanup: it removes the app package but leaves
the environment and its other packages.

### Earlier uv-tool installation

For a copy installed with `uv tool install`, run in PowerShell or Terminal:

```text
uv tool list
uv tool uninstall cbs-transcriber
uv tool list
```

Confirm CBS Transcriber is absent from the final list. This removes that tool's managed
environment and installed command, including its isolated dependencies. It does not remove
a separate local App environment or its shortcut. See [uv's tool documentation](https://docs.astral.sh/uv/concepts/tools/).

### Developer/source installation

Stop its app and identify the exact checkout and `.venv` used. Preserve uncommitted code
and any data stored there. Removing only that environment removes its installed packages;
removing source files is a separate decision. Do not treat a checkout shared with other
work as a disposable installation folder. See [development instructions](development.md).

If removal reports **Access denied**, keep the exact error and target and ask CBS IT or the
maintainer. Do not force-delete shared uv/Python directories, change application-control
policy or clear permissions broadly.

## What remains

| Retained item | Deliberate removal, if wanted |
| --- | --- |
| Libraries, project recordings, transcripts, edits, notes/history and local backups | Identify every actual location and preserve an independent complete backup. Prefer the app's managed deletion preview/confirmation. Never infer ownership from a parent folder name. |
| Downloads, exports and separately copied backups | Review only the intended copies under the applicable retention policy. |
| Saved API keys/tokens | Review only CBS Transcriber entries in Windows Credential Manager/macOS Keychain. Endpoint accounts may be hashed; ask the maintainer if unclear. Other installations can share them. |
| Model caches | Identify the exact cache/model and other applications using it first. Do not delete a shared Hugging Face cache wholesale; removal can cause large downloads later. |
| uv, uv-managed Python and uv's package cache | Keep them when used by any other app/project. They are separate from removing CBS Transcriber. |
| App preferences/library-list records and environment variables | Review app-specific records separately. Uninstall does not remove a separately configured `OPENAI_API_KEY`. |
| UCloud jobs, remote files, backups and Trash | Manage them separately with the correct job/folder ownership and explicit disposal authority. |

Deleting a local credential does not revoke it at its provider. Revoke/rotate an unwanted
UCloud, Hugging Face or OpenAI token through its issuing account when that is the intended
outcome. Deleting a provider configuration alone does not remove its shared key.

For data locations and complete preservation, see [storage and removal](storage-and-removal.md)
and [complete backups](portable-backups.md). These instructions describe the intended removal
scope; real local-layout Windows/Mac uninstall and data-preservation acceptance remain pending.
