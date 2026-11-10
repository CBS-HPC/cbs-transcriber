# 0034 — Preserve identical worker JSON snapshots on retry

Accepted: 21 October 2026.

A final regression run encountered Windows `WinError 5` while an uncertain UCloud enhancement
retry replaced its unchanged frozen request. The original lock owner is unknown. A deterministic
regression reproduces the same failure through the real queue path with an ordinary Python
reader holding that file open: Windows can allow reads while denying replacement/deletion.

The shared standalone worker JSON writer now compares exact UTF-8 bytes before writing.
Identical content is an idempotent no-op. This preserves the frozen file and lets the normal
uncertain-allocation check refuse another submission. It does not bypass source/approval checks.

Changed content still writes the existing pending file and atomically replaces the target.
Do not delete the old target first, silently ignore errors or introduce generic retries. A denied
changed replacement preserves the prior target and reports the error; after the reader releases
the file, an explicit caller retry can publish the changed bytes. Existing one-owner job/worker
serialization remains required; this helper is not a lock or a concurrent-writer protocol.

Verify exact unchanged bytes avoid replacement, real uncertain-allocation retry makes no second
submission, changed-write failure retains old bytes, and changed replacement succeeds after
release. Tests use synthetic data and a real Windows reader plus a portable injected-failure
case. No new schema or remote operation is introduced.
