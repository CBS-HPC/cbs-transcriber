# 0168 — Release only the preparation lock this invocation acquired

10 November 2026. Offline CBS Runtime safeguard on the post-MVP branch. Extends ADR 0065;
it neither enables the isolated-generation prototype nor authorizes live preparation.

## Decision

The generated supervisor records device/inode observations for its acquired `.prepare-lock`
directory and exclusive owner marker. Release requires the same ordinary directory and
regular marker, the exact original generation bytes and no additional entries, including
hidden ones. Links, changed identities, missing markers or foreign contents retain evidence.

Release removes only that marker, then attempts to remove the empty directory. It never
recursively removes `.prepare-lock`. A remembered `HOLDING_LOCK=1` flag alone grants no
authority over a replacement path. Repeated release is inert.

No lock reclaim is added. Age, a terminal allocation or an apparently absent owner cannot
establish that all readers or writers have stopped. The persistent `.in-use` reader-lock
inode and existing exclusive preparation/legacy-reader refusals remain unchanged. Shared
runtime/model folders remain outside run-file cleanup.

## Limits and recovery

Cooperating supervisors never replace a held lock. Path checks and POSIX unlink/rmdir cannot
atomically fence unrelated hostile filesystem replacement between check and use. A late
extra entry can leave a directory after its owner marker was removed; uncertainty remains
for investigation. This is a defensive release guard, not a crash-safe lock-recovery protocol.

Git Bash tests exercise owner/replacement/content refusal and ordinary release. Windows link
emulation does not prove real symlink behavior; native symlink and Linux flock acceptance
are separately identified. Existing remote scripts and frozen recipes are not rewritten.
Real Linux/UCloud process death, locking, model reuse and preparation acceptance remain open.

The [offline Runtime proposal](../proposals/post-mvp-runtime-preparation-2026-11-10.md)
separately reviews disabled isolated generations and the evidence needed before activation.
