# 0104 — Explicit current-user source-install launchers

Accepted 30 October 2026 as the first shortcut stage for Windows and both Mac architectures.

`uv run cbs-transcriber --create-shortcut` creates a current-user Windows Start Menu shortcut
or macOS executable `.command` launcher. `--shortcut-location desktop` selects Desktop.
This action exits before library resolution, migration or app startup. Nothing is created
implicitly by installation/startup, and an existing destination is never overwritten.

Pin the installed source folder and its installed `.venv` Python, preserving supported launch
arguments and absolute user paths. Do not pin another interpreter by resolving a macOS venv
symlink. No credentials or ambient environment enter the launcher. Clicking launches the
already-installed app; it never synchronizes dependencies, downloads models or recreates a
shortcut. Moving/replacing a source installation requires recreating its launcher.

Windows uses native current-user folder lookup and WSH with a fixed PowerShell program;
paths and arguments arrive as JSON data, never generated script source. macOS uses shell
quoting and exclusive file creation. A `.command` is a source launcher with a terminal, not
a signed `.app`, notarized installer or Dock integration. Those packaging stages and real
clean Windows/Apple Silicon/Intel acceptance remain open.

Tests use temporary destinations and mocked native calls. No actual user shortcut was
installed during development. See [the guide](../installation-shortcuts.md).
