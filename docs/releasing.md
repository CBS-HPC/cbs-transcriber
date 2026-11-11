# Prepare the stable installation entry point

This is a maintainer procedure, not authorization to publish. The repository currently has
ordinary test CI in `.github/workflows/tests.yml`; release publication is a separate manual
action. No new release, asset upload or change to v0.1.0 is performed by this implementation.
The published MVP does not have `install_latest.py`, so its stable download URL is not usable
yet. Until a separately approved publication, keep the working versioned installation route
in README.

## One release, one matching asset set

1. Freeze the approved source commit, package version and release profile in a separate
   packaging checkout. Never build by synchronizing a running app's environment. Keep the
   package's valid version, such as `0.1.1`, and matching stable tag, such as `v0.1.1`.
2. Build the ordinary wheel with `uv build --wheel`. Retain its real name, for example
   `cbs_transcriber-0.1.1-py3-none-any.whl`; do not rename a wheel to contain `latest`.
3. Export runtime requirements from that same frozen `uv.lock` with
   `uv export --locked --offline --no-dev --no-emit-project --no-hashes`. Pass the export and
   that lock to `scripts/export_wheel_constraints.py`. Name its output
   `cbs-transcriber-0.1.1-constraints.txt`, using this release's actual version. This preserves
   the existing pinned CPU artifact choices and hashes; it does not make tool installation
   consume the whole source lock.
4. Copy the exact reviewed `scripts/install_latest.py` into the release assets as
   **`install_latest.py`**. Its name stays the same across releases. It has no dependencies
   beyond the standard library and does not go inside the app wheel.
5. Run the focused bootstrap and constraints tests, plus the separately agreed disposable
   wheel/package checks. Bootstrap tests mock the GitHub API, download bytes and uv install;
   they do not install or replace the running app. Windows/Mac clean-device, launcher and
   actual latest-URL acceptance remain separate checks.
6. At a separately authorized publication, upload the version-named wheel, its matching
   constraints and the unchanged-name bootstrap to **the same release**. Retain other approved
   receipts/assets. Verify the uploaded assets' sizes, anonymous URLs and GitHub SHA-256 digest
   metadata against the exact local files. Missing digest metadata blocks the bootstrap.
7. Mark that approved, non-draft, non-prerelease release as latest. Do not overwrite the
   historical v0.1.0 assets/tag to introduce the installer. Only after verifying its public
   URL should the primary guide switch from the versioned command to the stable command.

The future entry point is
[`releases/latest/download/install_latest.py`](https://github.com/CBS-HPC/cbs-transcriber/releases/latest/download/install_latest.py).
The [user guide](installation.md#stable-latest-release-installer) records both install and
explicit `--update` commands. Later releases keep that script asset name and those commands;
the valid wheel/constraints version names change only in the release assets.

## Bootstrap boundary

The script reads the public repository's latest-release metadata once, then freezes the
selected tag and exact version-named asset pair. It requires uploaded assets from this
repository, their exact versioned HTTPS URLs, bounded sizes and SHA-256 digests. It validates
the actual downloaded bodies before passing local temporary files to uv. A latest-release
change during download cannot mix a wheel with another release's constraints.

The public API cannot redirect. Asset redirects are restricted to GitHub's HTTPS asset
hosts. No GitHub token is requested or embedded. GitHub-hosted code and metadata remain a
shared trust boundary: these digests do not supply independent signing or all dependency
hashes. Versioned wheel installation remains the reproducible fallback.

The invoking uv executable is retained through uv's `UV` child environment variable. The
PEP 723 header declares Python 3.13 and an empty dependency set; `--no-project --no-config
--no-env-file` also keeps project configuration and environment files out of the launch.
The installer retains `--link-mode copy`, and only explicit `--update` requests app
reinstallation. It never uses `--force`, starts the app or changes PATH.

The script reserves port 8765 until uv exits. A running default-port app blocks the bootstrap
before it requests release metadata/assets or calls uv. The outer uv launch can still fetch
the bootstrap and its Python interpreter first. This is a default-port guard, not proof that every
custom-port app is stopped; the update instructions require stopping every copy. There is
no global application-install lease or atomic rollback guarantee if uv installation fails.

## Primary sources

- [uv script metadata and isolated dependencies](https://docs.astral.sh/uv/guides/scripts/)
  supports an empty dependency list and an explicit Python requirement.
- [uv 0.9.29 remote-script implementation](https://github.com/astral-sh/uv/blob/0.9.29/crates/uv/src/commands/project/run.rs)
  accepts HTTP(S) script targets; the documented minimum uv version supports this mechanism.
- [uv 0.9.29 child executable propagation](https://github.com/astral-sh/uv/blob/0.9.29/crates/uv/src/lib.rs)
  sets `UV` to the executable used for that invocation.
- [GitHub latest-release links](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)
  provides a stable download URL for a constant release asset name.
- [GitHub latest-release API and asset metadata](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)
  describes public stable-release lookup and the asset download URL, size and digest fields.
- [Python wheel filename specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/#file-name-convention)
  requires the distribution version in the wheel filename.
