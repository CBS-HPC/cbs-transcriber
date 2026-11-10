# Historical Git-backed uv tool-install experiment

**Superseded on 10 November 2026:** the ordinary-user release route is now a versioned
GitHub wheel plus matching constraints, described in [Install and start](../README.md#install-and-start-windows-and-macos).
The experiment below concerns installation from Git source, not the wheel release. Its
results do not establish fresh Windows/Mac wheel installation, public release availability
or shortcut acceptance. Preserve this record as historical evidence.

At the time of this experiment, the proposed Git-backed `uv tool install` route was not
accepted as an installation method. The repository was private and real clean-machine
Windows/Mac installations, updates and uninstalls had not been exercised. The locked source
installation remained available.

On Windows with uv 0.9.29, a disposable synthetic Git repository was served over loopback
HTTP. Its tool package depended on a wheel supplied by an HTTP URL in `tool.uv.sources`.
With isolated uv tool, executable and cache directories, `uv tool install` resolved and
installed both synthetic packages using that URL. The test contacted only the loopback
server and did not alter the app's environment or library. It demonstrates source handling
for this uv version, not the complete CBS Transcriber dependency graph.

In a second run, the synthetic repository contained an intentionally invalid `uv.lock`.
The same install still succeeded. Thus this tool-install path did not use that repository
lock; it must not be described as reproducing the source install's locked versions or hashes.
A separate Windows `git+file:` experiment hit a uv resolver panic; it is not evidence about
the HTTPS GitHub route. Current uv behavior may change in later versions.

An offline wheel build of the application included the `cbs-transcriber` console entry point,
the three frontend files, provider catalog and examined Runtime worker scripts. This is a
limited artifact inspection, not an installed-app smoke test or proof that all resources and
platform-specific dependencies work. See the main README for the current wheel route and the
existing [source wheel verification](verification-2026-10-29-ucloud-release-wheel.md) for
the checksum-locked clone/ZIP workflow.
