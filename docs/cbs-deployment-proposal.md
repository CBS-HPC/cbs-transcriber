# CBS deployment proposal

Status: planned discussion, 21 October 2026. No installer, launcher or plugin is released.

## Service purpose and success criteria

Clarified by the user on 22 October 2026: this project is intended to provide a CBS-supported
transcription service. The core use case is interview-based research in Danish
and English, with convenient transcription, listening, correction, speaker handling and exports.
The supplied LIBAS comparison frames the need: reduce licensing and administration burdens while
retaining a usable audio-and-text research workflow around UCloud and other approved processing.

The present app is a local, single-user client with remote processing options. The initial pilot
can use that delivery model; shared hosting, institutional sign-in and multi-user administration
require a separate decision and implementation. The service owner will agree eligibility for
researchers and students during pilot planning.

Evaluate the service against these outcomes:

- **Usable interview workflow:** import audio/video, transcribe, follow playback, correct text and
  speakers, recover original passages, and export complete results without manual CSV conversion.
  Installation and ordinary use should be accessible to researchers without terminal commands.
- **Research quality:** measure Danish/English transcription accuracy, usable timing/speaker
  coverage and time spent correcting representative interviews. Keep observed quality separate
  from software-test results, and preserve versions and a defensible workflow description.
- **Total cost:** measure cost per processed audio hour and expected annual service cost,
  including UCloud GPU/credit allocation, optional API fees, storage, maintenance and staff support.
  Establish available credits and charging ownership as part of that comparison.
- **Simple access and support:** identify the service owner, eligible users, onboarding route,
  supported Windows/Mac devices, updates, documentation and escalation path. Measure setup time,
  failed/retried jobs and support effort during the pilot.
- **Appropriate data handling:** agree institutional processing/provider eligibility, storage,
  retention, deletion and backup arrangements; keep Protected controls explicit and preserve
  research material through review, updates and migration.

Pilot acceptance centres on transcription, review, export and dependable operation. Optional
topic/sentiment interpretation remains a separate research workflow. Define the pilot cohort,
representative recordings, acceptance targets and cost baseline with LIBAS/CBS IT before a
CBS-wide service decision. Procurement, access policy and institutional approval remain CBS
decisions.

## Recommended pilot

Ship a versioned local application with a normal shortcut. Make the CBS ChatGPT Work
plugin an optional guide for installation, launch, use and troubleshooting. Researchers
must also be able to use the app without ChatGPT. Agree the supported operating systems,
distribution owner and support route with CBS IT before choosing installer technology.

The current developer workflow uses Python 3.13, `uv sync --locked` and the repository's
`.venv`. `__main__.py` starts a loopback server and opens the browser after it is ready.
It does not yet reopen an existing instance automatically. A native macOS chooser is now
implemented with mocked tests. Apple Silicon/macOS 14+ includes local inference; Intel installs
the core app without the unsupported pinned WhisperX stack. Actual macOS release acceptance
has not been completed. See `README.md`, `pyproject.toml` and `src/cbs_transcriber/storage.py`.

## Application package and shortcuts

- Produce a pinned release with checksums, dependency inventory, licenses and release notes.
  Package the tested Python/runtime dependencies; do not require researchers to manage Git
  or a terminal. Keep installation outside OneDrive and outside the research library.
- Windows: install for the current user where CBS policy permits; create a Start menu
  shortcut and offer a Desktop shortcut. macOS: provide a signed/notarized application
  bundle for Applications, with normal Dock access. These are proposed deliverables.
- A launch helper should acquire the library lock, reuse only a verified app instance for
  that library, and reopen its browser. If a port is occupied by another process, display
  an actionable error; never stop that process or assume it is CBS Transcriber.
- Keep the server on `127.0.0.1`, with the existing origin protections. Add visible launch,
  error and shutdown controls so no terminal window is needed. Closing a browser tab alone
  must not imply that an overnight batch has stopped.
- Keep model-weight downloads explicit. Report installation size and local enhancement
  readiness separately; retain the standard WhisperX dependency requirement.

## Optional CBS ChatGPT Work plugin

A plugin can package reusable skills and optional tools; OpenAI documents separate plugin,
workspace-skill and runtime access controls. Installing a plugin does not grant machine,
connector or service permissions. A local executor and its applicable permissions are
needed to perform installation or start a local process. Browser/cloud guidance alone
must fall back to instructions. [Skill controls](https://learn.chatgpt.com/docs/enterprise/skills),
[plugin permissions](https://learn.chatgpt.com/docs/plugins#how-permissions-and-data-sharing-work).

Plan three concise workflows: **Install / update**, **Open Transcriber**, and **Use / diagnose**.
They should detect OS and installed version, select an approved release, verify its checksum,
run the supported installer/launcher, and check readiness without reading recordings or keys.
Use deterministic reviewed helpers rather than generated shell installation commands.
Keep any optional local tools narrowly scoped; no arbitrary shell or transcript access is
needed for routine launch. No automatic inference, remote upload or UCloud allocation.

CBS workspace admins would own distribution and access. OpenAI documents role-scoped workspace
publishing separately from public-directory publishing; availability in CBS must be confirmed.
[Workspace plugin publishing](https://developers.openai.com/plugins/build/plugins#publish-a-local-plugin-to-your-workspace).
An execution-dependent skill should be marked Desktop only using the supported manifest
setting; this is a capability requirement, not an elevation mechanism. A local MCP server is
optional and would need its own deployment review; public local-MCP submission currently has
additional eligibility requirements. [Desktop-only plugins](https://developers.openai.com/plugins/deploy/desktop-only).

## Data, updates and removal

- Enter provider tokens in the app's credential-store-backed settings, not in chat or a
  shared plugin. Do not attach interviews, transcripts or private paths to support prompts.
  Diagnostic bundles should be opt-in, redacted and limited to versions and safe error codes.
- Show the actual library and project folders, accounting for redirected Windows profiles.
  Store data per user, keep Protected-project controls, and agree backup/retention with CBS.
- Apply updates only after saved edits and active jobs are resolved. Verify the release,
  back up SQLite before migrations, and preserve external project folders and credentials.
  Rollback requires a schema-compatible app or restoration of a matching backup; never
  silently run an old version against a newer schema.
- Uninstall removes application/runtime files and shortcuts only. Preserve the library,
  project folders and credentials by default; offer a separate explicit removal procedure.
  Removing the ChatGPT plugin must not uninstall or delete research data from the app.

## Pilot acceptance and decisions

Test on real managed CBS Windows and macOS devices, including Apple Silicon and any Intel
Macs intended for support. Resolve locked WhisperX/PyTorch/FFmpeg dependency availability
per architecture; synthetic CI success cannot establish macOS installation support.
Verify a clean install without administrator rights where allowed, shortcut relaunch,
port conflicts, folder chooser, keychain, Danish filenames, audio/video playback, local
enhancement readiness, restart recovery, update/rollback and uninstall preserving data.
Use public fixtures; live providers and private interviews need separate acceptance.

Discuss next: CBS IT ownership and signing, supported OS/architectures, installer delivery,
ChatGPT workspace policy/local execution availability, support owner, and pilot researchers.
