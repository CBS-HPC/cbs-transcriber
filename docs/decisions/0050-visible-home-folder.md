# 0050 — One visible folder for the library and new projects

Accepted 23 October 2026. Supersedes the default locations in V90.

Researchers could not find their data. The library lived in hidden application data
(`%LOCALAPPDATA%\CBS Transcriber`, or `~/Library/Application Support/CBS Transcriber` on macOS),
and a packaged launcher on Windows could redirect it further into `AppData\Local\Packages\…`.
New projects defaulted to `Documents\CBS Transcriber projects`, falling back to the home folder
when Documents was synced. On CBS laptops OneDrive syncs Documents and Desktop, and on Macs
iCloud often does, so the two defaults ended up in unrelated places.

## Decision

Windows and macOS use the same layout in the user's home folder:

```
<home>/CBS Transcriber/
    Projects/   new projects, one short-ID subfolder each (recordings, snapshots, exports)
    Library/    central database, backups and app-managed files; do not edit by hand
```

That is `C:\Users\<name>\CBS Transcriber` on Windows and `/Users/<name>/CBS Transcriber` on
macOS. The home folder itself is not redirected by OneDrive Known Folder Move or iCloud
Desktop & Documents, so recordings are not synced by default. Zotero (`~/Zotero`) uses a
similar visible home-folder default.

- **Existing libraries stay where they are.** If `<home>/CBS Transcriber/Library` has no
  library but the earlier app-data default does, the app keeps using the earlier location and
  the launcher says so. Nothing is moved automatically; a move remains an explicit, backed-up
  step.
- **Overrides are unchanged.** `--data-dir` / `CBS_TRANSCRIBER_DATA_DIR` set the library;
  `--projects-dir` / `CBS_TRANSCRIBER_PROJECTS_DIR` set the projects parent.
- **Existing projects never move** and keep their recorded storage paths.
- **Defaults only apply to the launcher.** Tests and libraries opened without a projects
  parent keep `<library>/projects`.

## Consequences

The project dialog and the launcher show both paths. A library left in a packaged app's
redirected folder must be opened with `--data-dir` or moved deliberately.
