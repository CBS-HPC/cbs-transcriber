# 0083 — Typed table evidence and a frozen schema catalog

Accepted 28 October 2026 for the internal T91b0 foundation, after independent design review.
This accepts a pure row encoder and one frozen schema catalog. It does not deliver the S1b
database reader, full S1/PR02, capture, package composition, restore or upload. ADR 0081's
transport observation and existing v1 behavior remain unchanged.

## Keep observation and authority separate

`portable_rows.digest_table(table, rows, *, guard=None)` hashes a caller-owned stream of
already observed rows. It never opens SQLite or a supplied filename, constructs Store, executes
SQL, migrates, registers a library or grants processing/restore/cleanup authority. Its result
is a typed-table digest with policy/table/schema identifiers, row count, SHA-256 and encoded
byte count. It is not evidence that those rows came from a particular database, source version
or sealed package. Future capture and inspection must establish that binding separately.

## Pin the policy; do not discover today's schema

The packaged `catalogs/portable_schema_12.json` freezes the schema family derived from committed
`e96303d11aea75d295027480b08ee8835329b0d2`, not the developing Store. Record the source hashes
and exact approved CREATE SQL, columns/types/defaults/nullability, primary/foreign keys and
indexes. There are 19 tables and seven named indexes, with their generated indexes retained.
No trigger, view or virtual table is part of this family. Deliberately absent owner-journal
foreign keys remain absent; do not add them to make the catalog look more complete.

Pin the catalog resource bytes and keep source provenance outside its structural fingerprint.
The fingerprint is SHA-256 of `cbs-portable-schema-v1\0` plus the canonical structural payload.
Changing provenance alone cannot change the meaning of an identical structural policy.
No environment override, current Store import or incoming database can expand the allowlist.
The structural payload pins UTF-8 database encoding; it does not cover arbitrary collations
or UTF-16 databases. A future reader must prove the encoding and its fixed total query order.
Schema number 12 alone does not establish membership: older migrated variants need explicit
fixtures/catalog decisions. The separately developing schema 13 is not supported by this pin.

The catalog is data for later comparison, not SQL to execute during hashing. Generating it may
use only trusted committed literal DDL in a synthetic in-memory database, without Store.
Catalog agreement will not prove history, mirrors, project ownership or file completeness.

## Exact byte protocol

Unsigned lengths/counts are 64-bit big-endian. The stream is:

1. `cbs-portable-table-v1\0`, `S`, then the 32 structural-fingerprint bytes.
2. A framed TEXT table name, `C`, column count, then each static ordered column as framed TEXT.
3. For each row, `R`, then one typed frame for each static column.
4. `E`, then the row count.

A typed frame is a one-byte tag, payload byte length and payload. NULL is `N` with an empty
payload; INTEGER is `I` with signed 64-bit big-endian bytes; REAL is `R` with IEEE-754 binary64
big-endian bytes; TEXT is `T` with exact UTF-8; BLOB is `B` with exact bytes. Preserve observed
float bits, including signed zero and nonfinite values, without JSON numeric conversion.
This does not recover a value SQLite already normalized before observation. JSON stored in
TEXT remains literal text, including whitespace, Unicode composition, NUL and line endings.

Accept only exact native NULL/int/float/str/bytes cells and tuple/list rows of the exact width;
never coerce booleans, subclasses, mappings or arbitrary objects. Enforce the catalog's total
primary-key order, rejecting NULL, wrong storage type, duplicate and decreasing keys. Text keys
use UTF-8 BINARY byte order; integer keys use numeric order. Composite attempt keys retain
`job_id, attempt`; other keys are their frozen catalog keys, not inferred rowids.
Snapshot each accepted row into a bounded tuple before key validation and later guard calls.
Otherwise a synchronous guard can mutate an accepted list while it is being hashed (B208).

Limits are 32 MiB per payload and encoded row, one million rows per table and 8 GiB for the
whole encoded stream including domains/trailer. These are initial internal format limits, not
measured capacity or universal backup support. Count/encode/hash large strings and bytes in
bounded chunks, with caller guards between rows/chunks; retain no completed row bodies.
An arbitrary caller iterator or guard can block/allocate independently, so this is not a hard
wall-clock or process-memory guarantee. Exceptions produce no successful digest observation.

## Opening untrusted databases remains a separate gate

The proposed in-process reader did not establish its bounded-memory/CPU claim. SQLite's
[LENGTH limit](https://sqlite.org/c3ref/c_limit_attached.html) bounds one value/row, while
[progress callbacks](https://sqlite.org/c3ref/progress_handler.html) are cooperative. They do
not bound all schema loading before application-level inventory checks. The local SQLite
3.45.3 build lacks the newer schema-object limit.

The next reader needs a small isolated child: configure and verify an enforceable SQLite heap
cap before opening the object, enforce parent timeout/cancel/kill-and-drain, and exchange
bounded sanitized data. [Heap limits](https://sqlite.org/c3ref/hard_heap_limit64.html) apply
across a process and have configuration exceptions; never set a global cap in the serving
app or imply an OS security sandbox. Hash/stat/sidecar checks, strict schema-first validation,
read-only hardening and unsupported-runtime refusal remain necessary.

Validate exact allowed schema before integrity/FK operations: [integrity_check](https://sqlite.org/pragma.html#pragma_integrity_check)
checks CHECK constraints, and a synthetic probe confirmed expression evaluation. Read-only
metadata selection did not evaluate the probe. [Immutable mode](https://sqlite.org/uri.html)
skips locking/change detection; it requires a caller-owned quiescent object and later use-time
revalidation. None of that filename-opening behavior is implemented by this decision.

Verify the pure protocol with independent hand-built vectors, type/framing separation, exact
Unicode/JSON/BLOB/floating values, key order, caps, guards and source/catalog integrity. Never
present those checks as database integrity, actual capture, restore or institutional acceptance.
