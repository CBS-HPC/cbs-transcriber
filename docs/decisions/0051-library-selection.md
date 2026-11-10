# 0051 — Choosing and switching libraries without --data-dir

Accepted 23 October 2026.

Researchers with more than one library had to type a long `--data-dir` path. A library
redirected by a packaged app was especially hard to find, for example
`AppData\Local\Packages\OpenAI.Codex_…\LocalCache\Local\CBS Transcriber`. ADR 0050 moved the
default, so a plain launch now opened a new, empty library while their projects sat elsewhere.

## Decision

- **Registry.** `<home>/CBS Transcriber/libraries.json` holds only paths: the current library
  and up to ten recent ones. It is written after a server has started on a library that was
  discovered, chosen or switched to. An explicit `--data-dir` or `CBS_TRANSCRIBER_DATA_DIR`
  launch is a one-off and never changes it, so test or temporary libraries cannot become the
  default.
- **Launching without `--data-dir`** (and without `CBS_TRANSCRIBER_DATA_DIR`):
  1. If the remembered library still exists, it opens.
  2. Otherwise the launcher gathers candidates: recent libraries, the ADR 0050 default, the
     earlier app-data default, and copies redirected by packaged apps (Windows
     `Packages\*\LocalCache\Local`, macOS `~/Library/Containers`). It opens the only candidate,
     or a new default library when none exists.
  3. When several exist, a small chooser page is served on the same port.
  4. `--choose-library` always asks.
- **Inspection is read-only.** Listing reads the schema version and project/recording counts
  through a read-only SQLite connection. It never migrates, locks or edits another library.
  Choosing validates the library: readable, not a newer schema, and not leased by another
  server.
- **Switching in the app.** The Library dialog asks the launcher to stop the server and start
  it again on the chosen library. The lease of the old library is released before the new one
  is taken. The switch is refused while any job is queued or running, so local work is never
  interrupted silently. Draining open connections is capped at five seconds, so a paused media
  stream cannot block the switch. If the new library fails to start, the launcher reopens the
  previous one.
- **New libraries.** A new library is created only in an empty or new folder, and never in a
  folder synced by OneDrive, iCloud or Dropbox, because syncing can damage the SQLite database.
- **No automatic moves.** Libraries are never moved or merged automatically.

## Consequences

Same-origin checks protect both the chooser and the switch endpoints. Servers that were not
started by the launcher (tests, embedding) report that switching is unavailable.
