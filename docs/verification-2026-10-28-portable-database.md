# Contained database-claim observations — 28 October 2026

T91b1 / internal S1b / ADR 0088 / V140 verifies supplied claims about one hash-bound synthetic
database object. No app/UI/queue integration, real-library migration or Restore was introduced.
Full S1/PR02, package composition, history/mirror semantics, ownership, capture, credential
exclusion, imported-owner quarantine, runtime security acceptance and restoration remain open.

## Verified boundary

`inspect_database_object` validates/copies strict schema-12 claims before guards, binds an owned
quiescent regular file through bounded size/hash/stat/sidecar checks, and executes only captured
hash-verified LF worker/codec bytes with the exact catalog. The isolated `-I -S -B` child has a
minimal environment, no app package/site/Store import and no inherited API/HF/UCloud credentials.
Request/response bytes are bounded; stderr is discarded and replies contain no paths, values
or upstream exception bodies. The parent owns pipe threads and the child through completion,
timeout, overflow, construction failure and original guard BaseException.

The child establishes its 64 MiB SQLite-only heap cap and observed allocation-refusal sentry
before opening the object. It hardens/readbacks the connection, gates every exact schema object
and metadata/encoding/version against the pinned family, then checks integrity/FKs and all nine
literal CHECK predicates. Only fixed approved SQL identifiers/expressions are used. It streams
the captured typed codec with actual aggregate row/encoded-byte budgets. Final file evidence,
child reaping, settled I/O and closed pipes precede success.

Result scope is `database_claims_only`, `matched_supplied_database`, `restore_ready: false`.
The SQLite version, heap observation and executed-source hashes describe this inspection,
not the original data producer. No supplied claim dictionary becomes a sealed manifest.
Quiescence is a precondition; hostile concurrent namespace safety, total Python/OS heap,
uninterruptible OS timing and native-parser isolation are not established. No public hostile
package admission or maintained SQLite security baseline is accepted by these tests.

## Final automated evidence

The two new test scopes use `--noconftest` so the concurrent app implementation and inference
environment are not loaded. Exact ignored artifacts are listed below.

| Scope | Final result | Artifact under `test-results/` |
| --- | --- | --- |
| `tests/test_portable_database.py` | 41 passed, no skips; terminal 84.71 s, XML 72.998 s | `portable-database-parent-final.xml` |
| `tests/test_portable_database_worker.py` | 55 passed, no skips; terminal 251.99 s, XML 246.506 s | `portable-database-worker-final.xml` |

**96 distinct new cases pass.** They cover actual captured-worker observations plus bounded
protocol/fault tests: strict claims/source pins, copy-before-guard, schema/encoding/version
refusal, hashes/drift/links/sidecars/corruption, private text, unchanged parent heap/input bytes,
CHECK/NULL/affinity semantics, row/encoded/VM limits, timeout, blocked stdin, overflow, nonzero
exit, original guard identity, thread construction/start failures, reaping and closed handles.
The tests use tiny synthetic databases/processes; a full library/capacity benchmark is not claimed.

Independent review gave **whole-stage GO** with these additional overlapping controls:

- Eight public-parent/real-captured-worker checks on the final parent/worker hashes below:
  full pinned fixture, literal Unicode/JSON/private canary, actual heap sentry, unchanged
  parent heap/file, claim copying and bad-reference/version refusal. Evidence:
  `portable-database-review-public.txt` and its retained runnable script.
- Seventy readonly/immutable/query-only truth cases for all nine literal CHECK expressions:
  invalid integer/label/compound cases and valid NULL/affinity/text/real/BLOB controls.
  Evidence: `portable-database-review-check-predicates.txt` and retained script.
- Ten actual child-process fault checks on the earlier parent
  `0961406804e55e82e4f6095b6b2d6cfe5fe9d096f1ef066d75838db01b4254f9`, before final source-I/O
  curation/worker pin: construction/start failures, overflow, stalled writer timeout and exact
  BaseException, EOF while child remains alive, nonzero/privacy and discarded-stderr flood.
  Children were reaped, pipes closed and I/O threads settled. The final parent test scope
  covers the corrected source. Evidence: `portable-database-review-lifecycle.txt`.

These probes overlap the 96 tests and are not added to their total. One tool wait delayed the
independent lifecycle output without reporting a test failure; the recovered log contains ten
passes. Its broker-delay cause is unknown and is not evidence of a production hang.

Scoped Ruff lint/format and AST/compilation pass for all four new Python files. Existing
transport, row-codec and catalog hashes are unchanged; their completed broad suites were not
repeated. No frontend, browser or dependency change is part of this stage.

## Wheel execution

An isolated source image of committed `6debce5` plus the two new modules built a wheel offline,
using the existing interpreter without synchronizing/installing the serving environment.
Both ZIP members match the final source hashes. A fresh `python -I -S -B` process imported
the parent directly from that wheel, generated a tiny trusted full-catalog fixture and ran the
actual captured child. It returned the exact observation scope, kept the canary out of its
response, left input bytes unchanged and left the parent SQLite heap cap at zero before/after. FastAPI
and Torch were not loaded. Evidence: `test-results/portable-database-wheel.txt`.
This is packaging/execution evidence on this Windows host, not real Mac, clean-machine or
hostile-package acceptance. No additional broad run was needed after exact ZIP/hash comparison.

## Reproduced findings

- **B209/B210:** support-object construction after Popen leaked a real fault child; list/dict
  error codes bypassed curated parsing with TypeError. Three permanent cases were red, then
  green after preallocation/immediate process ownership and exact string enum guards.
  Evidence: `portable-database-parent-{red,green}.xml`; independent construction/error-code logs.
- **B211:** a 4 KiB metadata LENGTH cap leaked into integrity checks and falsely refused a
  SQLite-valid 6 KiB non-key checked BLOB in the complete pinned fixture. One case was red,
  then green after the post-schema/pre-check lift to the data limit.
  Evidence: `portable-database-worker-checked-value-{red,green}.xml`.
- **B212:** bundled SQLite 3.45.3 readonly and immutable integrity/quick checks reported success
  for stored CHECK violations that writable mode detected. Same-file/value/ignore-flag controls
  and the full fixture reproduced false success. Nine literal approved predicates close this
  reader's acceptance gap without RW opening or a DDL parser. Seventeen focused cases passed,
  followed by the final full scope. Separate NOT NULL controls detected violations correctly.
  Evidence: `portable-database-review-readonly-checks.txt`, `portable-database-review-readonly-notnull.txt`,
  `portable-database-worker-checks-{red,green}.xml`. The upstream cause and other-version behavior
  remain unproven; this is not a generalized SQLite/security claim.
- **B213:** controlled trusted-resource open/read PermissionError exposed its path canary.
  Two cases were red, then green after narrow I/O curation, with guard PermissionError identity
  preserved. Evidence: `portable-database-source-io-{red,green}.xml`.

An early negative CHECK fixture also lacked a referenced project, and the first empty VM
fixture did not execute enough instructions for a callback. Those setup issues were corrected
with explicit FK controls and 2,000 synthetic rows; they are not additional production bugs.

The initial disposable 1 MiB heap design probe used no database file: readback, small positives,
execute-time allocation refusal before fetch and refusal to raise the PRAGMA cap all passed.
Evidence: `portable-database-design-heap-probe.{py,txt}`. Final default-child checks use the
64 MiB policy. SQLite-only accounting/configuration/sentry caveats remain in ADR 0088; no large
model weights or OS sandbox capability is implied.

## Frozen source image

SHA-256 uses LF-normalized source, matching captured execution and Git blobs:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `src/cbs_transcriber/portable_database.py` | 15,855 | `e91e40c7c80c633dd3f9be17274ee277230143106adda7fd457a08c0646359cc` |
| `src/cbs_transcriber/portable_database_worker.py` | 22,936 | `d58bbdec746fa3e6967a05093c2fb77f62a3dd75d8bf0b59935c9631f4d7dfee` |
| `tests/test_portable_database.py` | 14,857 | `74e6809b3d0b8fe5f5e17cf57b4eb1a9bbc3dc3d5f972eae1ffaa25c15446d4a` |
| `tests/test_portable_database_worker.py` | 24,095 | `239feda112a94ecf50191199497914f35919b8a37d57f79f65758c31518d53d8` |

Only this stage's four source/test files and owned documentation hunks belong in its commit.
Concurrent admission/model/repair/provenance/UI work, TODO cleanup and intentional document
deletions remain the other chat's work. No private package/library, provider job, paid service,
model download, real migration, backend restart, merge or push occurred here. The final project
audit still follows the remaining implementation rather than being replaced by this review.
