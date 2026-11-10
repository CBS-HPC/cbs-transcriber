# 0088 — Contained database-claim observations

Accepted 28 October 2026 for internal T91b1/S1b, after independent design review and a tiny
disposable heap-cap probe. Implementation acceptance still requires its recorded synthetic
process/database checks. This is not public hostile-package admission, full S1/PR02, capture,
restore, upload, a runtime-security baseline or institutional approval. No app route/queue,
library migration or serving environment is changed.

## One standalone observation

`portable_database.inspect_database_object(path, *, expected_sha256, expected_size,
expected_claims, guard=None)` binds one caller-supplied database reference. The caller owns
quiescent local staging and the custody of that reference. Strict claims contain exactly
schema_version, schema_fingerprint and tables; require schema 12, ADR 0083's fingerprint and
all 19 known table names with bounded native integer counts and lowercase SHA-256 values.
Validate and copy claims before caller guards. A dictionary cannot assert a sealed manifest.

Return only `database_claims_only`, `matched_supplied_database`, safe hash/size/schema/table
observations, inspection-runtime evidence, explicit unchecked scopes and `restore_ready: false`.
Inspection-runtime evidence describes this check, never the original transcription producer.
Do not return database values, source paths, raw errors or a generic valid-backup flag.
Package binding, historical payload/mirror semantics, project/file ownership, capture/role
inventory, credential exclusion, imported-owner quarantine and restore remain unchecked.

## Capture exact leaf code

The parent bounds and hash-verifies the worker source (64 KiB), committed codec source
(16 KiB) and exact canonical catalog (128 KiB). Normalize captured Python CRLF to LF before
hashing, transmitting and compiling those same bytes. Never reopen source after validation
or select an app/helper pyc. Catalog bytes remain exact; no text normalization or override.

Use the app interpreter with fixed `-I -S -B -c` bootstrap code, shell disabled and only
SystemRoot/WINDIR where Windows needs them. No credentials, Python paths, loader variables,
HF tokens or inference runtime are inherited. The bounded bootstrap verifies and executes
captured worker bytes. The worker verifies captured codec/catalog bytes and supplies that
fixed catalog through an in-memory resource adapter; no app package/Store/site import is needed.
This is a fixed leaf protocol, not a caller-selectable script or module framework.

## Parent owns the process and pipes

Frame the bounded worker bytes before strict canonical request JSON (at most 256 KiB). Bind
one canonical stdout response (at most 16 KiB) to the exact request. Discard stderr; never
retain exception text, paths, row bodies or native diagnostics. Reject malformed, unknown,
oversized, mismatched or nonzero-exit responses.
Check exact primitive field types before enum membership; malformed error codes cannot bypass
the curated invalid-response refusal (B210).

Use bounded raw-pipe writer/reader ownership, regular guard/deadline polls and explicit
kill/wait/drain/join/close on every failure, cancellation or overflow. Preserve the original
caller guard exception, including BaseException. Success requires the child reaped, both I/O
threads settled, every pipe closed and final file checks completed. No response grants a
successful observation while child work remains alive.
Establish process ownership immediately on Popen return, before allocating events/threads or
other support state; construction failures must also terminate and reap that child (B209).

One 120-second inspection deadline covers source capture, hash checks and child phases; do
not restart it for each phase. A separate two-second cleanup budget bounds normal cleanup.
These are cooperative operation bounds: OS process creation, blocking filesystem/system calls
and caller guards are not preemptible wall-clock guarantees. [Python subprocess documentation](https://docs.python.org/3.13/library/subprocess.html)
also warns that communicate buffers output and initial process creation may outlast a timeout.
Do not reuse unbounded communicate/capture_output for the production protocol.

## Establish SQLite containment before the object opens

In a fresh child, use a trusted in-memory bootstrap connection first. Refuse unavailable
configuration APIs and known incompatible memory-accounting configurations. Set a 64 MiB
SQLite heap cap, read it back and verify small allocations before/after an execute-only
`randomblob(cap + 1)` sentry. That execution must raise MemoryError before fetching. An
unexpected success is closed without fetching and the runtime is refused. The independent
1 MiB disposable probe confirms this ordering on the tested runtime.

[The PRAGMA can only lower a heap cap](https://www.sqlite.org/pragma.html#pragma_hard_heap_limit),
so do not probe at a tiny cap and pretend to raise it in the same child. [Heap accounting](https://www.sqlite.org/c3ref/hard_heap_limit64.html)
is process-wide SQLite accounting with configuration exceptions, not total Python/native
memory. Never set this global cap in the serving process. An unreported nonenforcing build
may allocate about 64 MiB during the fixed sentry before refusal; this is a bounded capacity
test, not an allocation-free promise or OS sandbox. No ctypes/DLL-discovery workaround.

Only then open the bound object read-only/immutable and harden/read back connection options:
defensive on; trusted schema, extensions, triggers, views, writable schema and ambiguous
quoting off; query-only, no checkpoint-on-close, mmap off, memory-only temporary work, and
fixed SQL/column/expression/attachment/length limits. Deny unneeded SQL actions/functions.
Reject unsupported runtimes rather than silently dropping hardening. SQLite still runs with
the user's OS permissions; this stage does not accept hostile native-parser isolation or
assert a maintained runtime-security baseline.

## Bind bytes, then inspect the fixed family

Require an absolute regular nonlinked/reparse-free single-link object, declared size at most
8 GiB and no adjacent WAL/SHM/journal entries. Hash through bounded reads with size/growth and
same-API stat witnesses; recheck hash, identity, size and sidecars afterward. Quiescence is
mandatory: immutable mode skips locking/change detection. These checks do not prove immunity
to hostile concurrent namespace replacement or future availability.

Before integrity/FK or row operations, compare every schema object, approved stored CREATE
SQL, column/default/key/FK/index metadata, UTF-8 encoding and schema version against the frozen
ADR 0083 family. Unknown SQL, views, triggers, virtual tables and other schema variants refuse.
Only fixed catalog identifiers enter queries. Never execute incoming schema SQL or construct
Store. Integrity checks may evaluate CHECK expressions; unknown definitions must be rejected
first. Require one integrity-check success and no foreign-key violation.
After the exact schema gate, lift metadata-only value limits to the supported data limit
before those checks. CHECK-constrained fields can contain larger SQLite-valid typed values;
leaving the 4 KiB metadata limit active falsely refused a supported 6 KiB fixture (B211).
Also run literal approved predicates for the nine CHECK expressions in the six pinned tables.
Use `WHERE NOT(expression)` to preserve SQLite's CHECK NULL/type semantics, without a general
DDL parser or read-write connection. Same-file controls on the bundled SQLite 3.45.3 observed
readonly/immutable integrity_check success for stored CHECK violations that writable mode
detected (B212); the cause and other-version behavior are unproven. Therefore readonly pragma
success alone cannot establish this reader's CHECK acceptance. A separate NOT NULL probe
reported violations correctly, so no duplicate generic constraint engine is introduced.

Stream all 19 fixed column/primary-key ordered queries through the captured codec. Validate
actual counts/hashes and enforce aggregate two-million-row/eight-GiB encoded budgets while
streaming, including table domains/trailers. Per-table codec limits may only be lowered to
remaining aggregate budgets; never accept expected claim totals as proof of actual work.
Retain the codec's 32 MiB row/value and one-million-row table limits. No JSON/history text is
parsed or rewritten. These limits define this internal reader, not universal backup support.

## Acceptance remains explicit

Use actual tiny synthetic child processes to verify heap ordering, successful/no-write reads,
schema-first refusal, corrupt/mismatched objects, typed digests, global budgets, file drift,
sidecars/links, privacy and parent heap independence. Exercise bounded overflow, stalled stdin,
timeout and caller BaseException while writing; prove reaping, draining and closed handles.
Mocks alone cannot establish process containment. No private database/package or running app
is used. Full historical/ownership/capture/quarantine/security and restore acceptance remain
separate even after this reader passes its own checks.
