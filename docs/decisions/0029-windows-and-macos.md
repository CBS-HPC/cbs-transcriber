# 0029 — Windows and both Mac architectures

Accepted 21 October 2026. Extend the Windows-first target to Windows x64 and macOS Apple
Silicon/Intel. Real-device acceptance remains required before a verified-support claim.

Keep one Python 3.13 project/lock. WhisperX 3.8.6 and its inference dependencies remain standard
on Windows and Apple Silicon. Exclude that group on Intel macOS, where the pinned Torch 2.8
stack has no compatible wheel. The Intel installation retains API/UCloud transcription, imports,
review, notes and exports; local enhancement reports the platform limitation and points to
UCloud instead of suggesting an impossible reinstall. No alternate local runtime is certified.

The current Apple Silicon inference lock needs macOS 14 or newer. CPU is the supported local
execution target on Mac; CUDA detection does not imply Apple MPS acceleration.

Use native macOS osascript folder selection with a fixed script and argument list; do not
interpolate paths into a shell. Treat user cancellation separately from launcher errors.
Existing platformdirs/Keychain/POSIX lock and process handling remain. Verify clean install,
Keychain, native dialog, FFmpeg playback/seeking and process cancellation on actual Mac devices.
Installer/shortcut/plugin distribution is a separate planned deployment task.
CI now declares Windows, Apple Silicon macos-15 and macos-15-intel unit/API jobs using
[GitHub's documented runner labels](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Those remote jobs have not been run as part of this local change.
