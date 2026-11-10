# Producer-artifact boundary — 28 October 2026

The internal `provenance_artifacts.py` boundary captures stable, bounded, managed regular
files and explicitly allowlisted environment manifests. Checkpoints bind an exact request
hash and producer stage. Creation binding requires trusted result/version/payload references.
Pure historical validation does not open files or infer current environment facts. Public
projection omits paths, private IDs and file bodies; retained-file availability is not checked.

The helper is **not wired into producers or Store**. In particular, normalized SDK dumps are
output rather than original response bytes, and existing retained files cannot retroactively
be described as durable producer observations. Working-result roles, conflict handling and
environment recovery remain separate tasks.

Verification: **62 passed, 3 Windows symlink-permission skips**. Both actual Windows junction
cases passed. Ruff check and formatting passed. The tests cover strict metadata/size/hash
bindings, replacement/drift, hard links/reparse points, exception redaction and owned-resource
lifetime. A red-first stream-allocation failure probe exposed a descriptor leak; caller-owned
descriptor closure fixes it (B214/V144). No external security impact or live data loss is claimed.

The next vertical slice is [local full-ASR capture and creation binding](proposals/local-asr-artifact-integration-2026-10-28.md).
It must durably seal the credential-free request before inference, distinguish missing legacy
capture, append exact stage observations, and bind creation inside the existing result/job
transaction. No post-commit history patch or new inference solely to manufacture evidence.

No serving checkout/library, credentials, model weights or provider execution were changed.
Architecture: [0087](decisions/0087-producer-processing-artifact-observations.md).
