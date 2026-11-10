# Public-source installation guides — 29 October 2026

T94 is partly complete: README and AGENTS describe the planned public source installation;
anonymous clean-machine Windows/Apple Silicon/Intel Mac acceptance still awaits public
availability and actual device checks. Repository visibility and dependency metadata are
unchanged. This stage adds no installer, shortcut, updater or runtime behavior.

## Delivered documentation

- Exact HTTPS clone URL and main source-ZIP link for CBS-HPC/cbs-transcriber, with separate
  PowerShell and macOS Terminal commands and quoted user-folder paths.
- Git prerequisite for both clone and ZIP while the pinned ucloud-workflow source remains
  Git-based; explicit ZIP extraction, parent-folder creation and package-root checks.
- Windows x64; native Apple Silicon/macOS14+ with CPU local inference; Intel core/API/UCloud
  installation without the incompatible pinned local inference wheels.
- First and later launch, loopback URL/port, terminal lifetime, private service-key setup,
  safe stopped-app clone/ZIP updates and preservation of libraries/projects outside source.
- Database-backup versus media-copy distinction, no copying old environments, no destructive
  reset advice, common setup failures and honest publication/platform acceptance limits.

The commands match the current pyproject/uv.lock, CLI launcher, ADR0029 and
[prior installation audit](audits/installation-2026-10-28.md). The vendor's
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) was checked
for the existing installer commands. A web fetch of this repository did not establish public
availability; no anonymous clone, ZIP download or private authentication was attempted.

## Verification and limits

- All **8 PowerShell** code blocks in the setup/launch section passed PowerShell's parser.
- All **6 shell** blocks passed Git for Windows Bash syntax checking (`bash -n`). This checks
  syntax only; it does not establish native macOS execution or clean-device installation.
- Extraction included indented list fences and required the exact 8/6 counts. An earlier
  unindented-only harness saw just two blocks and was superseded before the final checks.
- `uv run --no-sync cbs-transcriber --help` exited successfully with the documented port,
  library and browser flags. It emitted an advisory about an unrelated parent project file;
  no server or library was opened and no dependencies were synchronized.
- A scoped independent read-only review gave **GO** against the source/dependencies/platform
  contracts. Its minor ZIP parent-folder clarification is included in the final guide.
- Local Markdown targets, owned SPEC task/status and staged whitespace were checked. No
  application tests were repeated for these documentation-only changes.

The shell snippets were parsed, not executed. No clone, download, package installation,
model preparation, provider request, private-library/media inspection, schema migration,
backend restart, repository publication, merge or push was performed. The already-running
main/schema13 app and its shared Python environment were left unchanged.

Still required after publication: a credential-free installation on a clean managed Windows
x64 account and actual Apple Silicon/macOS14+ and Intel Macs, including path/device policy,
Keychain/Credential Manager, playback, local-runtime limitations, updates and data-preserving
removal. Offline package plans and syntax checks do not replace that acceptance.
