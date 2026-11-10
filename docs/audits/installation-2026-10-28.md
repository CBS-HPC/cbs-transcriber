# Windows and macOS installation audit — 28 October 2026

29 October follow-up: the [released-wheel source adoption](../verification-2026-10-29-ucloud-release-wheel.md)
supersedes this audit's Git-prerequisite/candidate-pending state after its own verification.
The observations and recommendations below describe the original audit, not a fresh install.

Status: two verified launcher defects fixed and regression-tested. Source-install dependency
plans checked on Windows; clean-machine and real macOS acceptance remain pending. This audit
did not install dependencies, open the app, change shortcuts, access credentials, stop processes,
download model weights or touch a research library.

## Scope and fixed defects

Reviewed the setup guide, `pyproject.toml`, `uv.lock`, launcher/library registry, folder picker,
platform selectors, CI matrix and deployment proposal. Changes are confined to
`src/cbs_transcriber/libraries.py`, `src/cbs_transcriber/__main__.py` and new regression tests in
`tests/test_installation_audit.py`.

1. **Malformed library registry could stop startup.** Valid JSON was assumed to be an object,
   and its `recent` field was assumed to be a list. A null/list/string/number root raised an
   exception; null/number `recent` values raised an exception, while string/object values became
   bogus recent paths. The loader now checks these shapes and falls back safely, preserving a
   valid current selection. It retains existing string/path semantics and the bounded history.
2. **An advisory settings write could prevent the browser opening.** After the app became
   ready, remembering its selected library ran before opening the browser. A permission or
   other filesystem error in that write interrupted the callback. The launcher now reports
   the failed bookmark without exposing exception details and still opens the ready app.
   Only `OSError` is caught. Explicit `--data-dir` remains a one-off selection, and
   `--no-browser` remains respected.

Both defects were reproduced before the fixes: all 10 new parametrized cases failed for the
expected reasons. The final focused run passed **37 tests**, including registry, launcher,
library selection, mocked macOS/Windows folder dialogs, Intel runtime reporting and default
project paths. Paths with spaces and Danish characters were included. Browser/server callbacks
were mocked; no real launch occurred.

```text
uv run --no-sync pytest tests/test_installation_audit.py -q -o addopts='' --tb=short --junitxml=test-results/installation-audit-red.xml
uv run --no-sync pytest tests/test_installation_audit.py tests/test_launcher_registry.py tests/test_libraries.py tests/test_folder_picker.py tests/test_mac_runtime.py tests/test_default_project_folder.py -q -o addopts='' --tb=short --junitxml=test-results/installation-audit-green.xml
uv run --no-sync ruff check src/cbs_transcriber/__main__.py src/cbs_transcriber/libraries.py tests/test_installation_audit.py
uv run --no-sync ruff format --check src/cbs_transcriber/__main__.py src/cbs_transcriber/libraries.py tests/test_installation_audit.py
uv run --no-sync cbs-transcriber --help
```

Ruff, formatting, scoped whitespace checks and CLI help passed. The XML files are local test
evidence, excluded from version control; they are not evidence of a fresh installation.

## Dependency plans

The app pins Python 3.13 and uv's locked dependency graph. Local inference dependencies exclude
Intel macOS; that platform receives the core app and an explicit unsupported-local-runtime
message. Windows x64 and Apple Silicon include WhisperX 3.8.6 and the pinned PyTorch stack.
Apple Silicon's overall local stack requires macOS 14+, even though individual dependencies
may have older wheel tags. Apple GPU/MPS execution is not implemented.

Executed with uv 0.9.29 on the existing Windows development machine:

| Synthetic package target | Offline locked dry run | Meaning |
| --- | --- | --- |
| `x86_64-pc-windows-msvc` | Passed | Windows x64 dependency selection resolves. |
| `x86_64-apple-darwin` | Passed | Intel core selection excludes the unsupported local inference stack. |
| `aarch64-apple-darwin`, deployment target 14.0 | Passed | Apple Silicon/macOS 14 package selection resolves. |
| `aarch64-apple-darwin`, uv's default target | Failed as expected | The synthetic default is macOS 13; pinned ONNX Runtime has no compatible distribution there. |

```text
uv sync --locked --offline --dry-run --python-platform x86_64-pc-windows-msvc
uv sync --locked --offline --dry-run --python-platform x86_64-apple-darwin
# In a task-scoped PowerShell process only:
$env:MACOSX_DEPLOYMENT_TARGET = '14.0'
uv sync --locked --offline --dry-run --python-platform aarch64-apple-darwin
```

Dry runs did not install, remove or download packages. uv documents both the no-write dry-run
behavior and the default macOS target/override. These are package-selection checks, not native
execution or clean-account acceptance. [uv CLI reference](https://docs.astral.sh/uv/reference/cli/#uv-sync)

The repository's CI defines Windows, Apple Silicon and Intel Mac jobs. This audit did not run
remote CI or certify those runners. Windows ARM is outside the currently documented target.

## Fresh-install distribution gap and verified wheel alternative

At audit start, the copied-folder/ZIP instructions omitted a prerequisite: `ucloud-workflow`
was sourced from a pinned Git repository. That installation path requires a Git executable,
even when users did not clone CBS Transcriber. uv 0.9.29 explicitly discovers and launches Git.
[Pinned uv implementation](https://github.com/astral-sh/uv/blob/0.9.29/crates/uv-git/src/git.rs)

A separate temporary project confirmed this without network or package installation: actual uv
was run with an empty cache, no Git on its child-process PATH, a local-file Git dependency and
`uv lock --offline --no-index --python <existing-python>`. It failed with the missing-Git
diagnostic. This probe did not modify the app lockfile or user's environment. A warm-cache
successful dry run does not prove that a fresh ZIP installation works without Git.

The released wheel offers a better distribution path than adding Git to researchers' setup.
Read-only verification of the official release found:

- Annotated `v0.1.9` resolves to commit
  `a521fe7546e2735d0e8d9872c2486e02ad6b6fc8`, the same commit locked by the app.
- `ucloud_workflow-0.1.9-py3-none-any.whl` is 68,711 bytes. Its SHA-256 is
  `9eca466ce3157e4e9727c6ed4eae68a169dc24c61f2e78c8098bf669da20ea7c`, matching both
  the published checksum file and GitHub's asset digest.
- Wheel metadata identifies `ucloud-workflow` 0.1.9, Python `>=3.11`, and runtime dependencies
  `httpx>=0.27`, `rich>=13.7` and `typer>=0.12`. Its universal pure-Python wheel tag is
  `py3-none-any`; the declared distribution licence is `MIT AND CC-BY-4.0`.
- All 19 source-package files are present. Eighteen match the exact commit's Git blob bytes;
  `catalog.py` matches after CRLF/LF normalization. No source-content difference was found.

Only the small wheel/checksum and bounded public Git metadata/source responses were read;
wheel code was not executed. [Official release](https://github.com/CBS-HPC/ucloud-api/releases/tag/v0.1.9),
[published checksums](https://github.com/CBS-HPC/ucloud-api/releases/download/v0.1.9/SHA256SUMS.txt),
[wheel](https://github.com/CBS-HPC/ucloud-api/releases/download/v0.1.9/ucloud_workflow-0.1.9-py3-none-any.whl),
[annotated tag](https://api.github.com/repos/CBS-HPC/ucloud-api/git/tags/b8b969663c65d8bb7eb3f0ee73a544193c1032bc)

Recommended follow-up: replace the Git dependency source with that checksum-pinned released
wheel, regenerate the lock with uv, rerun the package plans and relevant integration tests,
then update the shared setup guide. This audit did not change dependency metadata or the
lockfile. Until that change passes verification, the copied-folder guide needs Git explicitly.
Future standalone distribution also needs to account for uv-specific source configuration;
other installers do not automatically inherit it.
[uv dependency source documentation](https://docs.astral.sh/uv/concepts/projects/dependencies/)

A temporary candidate was subsequently prepared without changing the checkout or active
environment: the dependency is `ucloud-workflow==0.1.9`, its source is the release-wheel HTTPS
URL, and **uv generated** the candidate lockfile. uv 0.9.29 rejects URL hash fragments in
`tool.uv.sources`; the generated wheel record instead holds the exact SHA-256 above. The wheel's
68,711-byte size was independently rechecked; uv does not include a size field for this URL
source. All **151 other package entries** remain semantically identical, including versions,
sources, artifact hashes, markers and dependency metadata. The total remains 153 packages.

The candidate passed all three locked/offline/dry-run package targets listed above with an
empty child-process PATH, so no Git executable was available. The interpreter and uv executable
were supplied by absolute path. These checks created no `.venv` and did not change the candidate
lock. This supports adopting the wheel source; the Git-free copied-folder claim remains
conditional on the main project actually adopting and verifying that change. This was not a
clean-machine installation, and the current checkout's dependency files were left untouched.

## Deployment acceptance still required

The source launcher exists. A packaged desktop installer, Windows Start/Desktop shortcuts,
signed/notarized Mac bundle, automatic update/rollback helper and uninstaller are still proposed
in `docs/cbs-deployment-proposal.md`; this audit did not create any OS shortcuts. The documented
uv install commands match its vendor guide, but CBS-managed device permission and software
distribution policy require separate acceptance.
[uv installation documentation](https://docs.astral.sh/uv/getting-started/installation/)

The implementation uses loopback, preserves existing libraries in place, and defaults new data
outside the checkout. Library and project paths stay separate from credentials. Mocked tests
cover folder cancellation, Unicode/spaces, hidden Windows helper invocation and manual fallback.
They do not establish real Finder dialogs, Windows Credential Manager, macOS Keychain, playback
codecs, native inference, shortcut relaunch or installation without administrator rights.

Before a CBS pilot, use public fixtures on a clean managed Windows x64 account and real Apple
Silicon/macOS 14+ and Intel Macs. Verify setup, registry permissions, unsynced storage, folder
selection, credential retention, launch/relaunch/port conflicts, media playback, local runtime
readiness, restart, updates with schema backup and data-preserving uninstall. Real Mac and CBS
policy acceptance remain external gates, separate from the automated evidence above.
