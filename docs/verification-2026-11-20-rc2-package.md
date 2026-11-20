# Local MVP v2 rc2 package — 20 November 2026

**Prepared locally, not published, installed or loaded.** Product source is `1ae0069` on
`codex/weekend-20261120`. Separate packaging branch `codex/weekend-rc2-20261120` is frozen at
`6ff03df`; it changes only package version, generated lock version and packaged release label.
Spelling/uncertainty redesign, native full-GPU helpers and Runtime supersession hardening
remain separate post-v2 work and are not added to this rc2 artifact.

| Asset | Bytes | SHA-256 |
| --- | ---: | --- |
| cbs_transcriber-0.2.0rc2-py3-none-any.whl | 1,795,515 | 80938df041d6a4b2028448bcfa994634c72ad1803d986e179d99e62f2e0ab1d9 |
| cbs-transcriber-0.2.0rc2-constraints.txt | 19,484 | 92616b25ab5993c8a78b8318348c7f2a3fb34eb1cc6361e86d096e710d78042a |

## Reproducibility and gates

uv 0.9.29 generated the package-version change offline with no environment synchronization.
Parsed lock comparison proves every dependency/artifact/marker is unchanged; only this
project's version advances from 0.1.0 to 0.2.0rc2. The tool rewrote line endings; no lockfile
was manually edited. Existing uv 0.13.7 could not reuse retained Spylls resolver metadata,
so the already installed lock-era tool was used. Nothing was downloaded or installed.

The PEP 517 wheel was built in a separate process from existing cached Hatchling 1.30.1,
pathspec 1.1.1 and trove-classifiers 2026.9.21.13, with existing packaging/pluggy. Cached
metadata hashes were checked. No build environment was created. The wheel's **152 packaged
source files** match the frozen checkout byte for byte. ZIP CRC, every RECORD digest/size,
METADATA version, console entry point and `py3-none-any` tag pass. The packaged profile is
exactly `mvp` with label **MVP v2 rc2**; the checkout remained clean before/after build.

The matching constraints were generated from this exact locked offline runtime export with
the existing constraints exporter, retaining its pinned CPU wheel artifacts/hashes. Asset
checksums and private receipts were retained outside the repository; constraints contain no
personal source paths. No stable installer asset or installation URL was published/changed.

Installation-helper, wheel-constraints, smoke, release-profile and spelling-MVP checks:
**155 passed, 1 deselected**, 75.46 s, with all 599 source/test hashes unchanged. The single
deselection asserts that main packages full; this intentionally packaged-MVP variant instead
has exact MVP identity checked in wheel bytes. Its assertion was not weakened.

The actual wheel was extracted into disposable staging (no dependency/environment install).
An isolated child imported that extracted distribution, verified version/profile/label and
ran `--help`, exiting successfully with network and credential access blocked. This uses
existing dependencies and does not establish a fresh installation or a running server.

The [timing source gate](verification-2026-11-20-timing-rc2.md) covers 377 backend and final
22 browser cases; do not combine overlapping module reruns into an inflated suite total.

## Remaining acceptance

H1/H2/H5/H6: real fresh install, shortcut clicks, update and removal on the supported managed
and personal devices. H15a: real WhisperX repair on the user's recording. Existing environment
permissions, real UCloud/Runtime, Mac/IME/accessibility and research quality remain separately
scoped. Main, the installed preview, user libraries, originals, jobs and original Claude
worktrees were not changed. Publication and reload still require their own authorization.
