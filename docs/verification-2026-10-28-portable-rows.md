# Typed portable row evidence — 28 October 2026

T91b0 / ADR 0083 / V139 delivers a pure streaming codec and one frozen schema-12 catalog.
It does not deliver the S1b database reader, full S1/PR02, capture, package composition,
restoration, upload or institutional acceptance. Existing S1a, v1, app APIs and jobs are unchanged.

## Boundary and exact policy

`portable_rows.digest_table(table, rows, guard=...)` consumes caller-observed rows without
opening a database or input filename. The output binds the table, static ordered columns,
schema fingerprint, exact typed cells and row count under `cbs-portable-table-v1`. Unicode,
JSON whitespace/line endings, NULL/BLOB distinctions and observed binary64 bits remain exact.
There is no database/source/package provenance or restore-ready claim in this result.

The canonical resource pins 19 tables, seven declared indexes and 29 generated indexes from
`e96303d11aea75d295027480b08ee8835329b0d2`. Approved SQL, column/key/FK/index metadata and UTF-8
encoding are structural data; source revision/hash/observation provenance is separate.
The structural fingerprint is
`038150c5376a827bec4a8d9c3f61c6d19e95fda7cd8a401dd8b3a81c8a20ef42`.
This covers one frozen family, not every historical schema-12 variant or developing schema 13.
No current Store import or discovery can widen it. No stored SQL is executed by the codec.

The codec snapshots each accepted list/tuple before later guards, enforces total typed PK
order and strict native values/row width, and bounds row/value/count/encoded bytes. UTF-8 is
counted/hashed in bounded chunks and byte payloads use bounded views. Guards and iterator
exceptions retain their identity; no partial successful digest is returned. A caller iterator
or guard can itself block/allocate, so this is not hard process/time containment.

## Evidence

- `uv run pytest --noconftest tests/test_portable_rows.py -q
  --junitxml=test-results/portable-rows-final.xml`: **86 passed in 2.76 s** (terminal summary;
  JUnit suite 1.283 s), no skips. Synthetic rows require no app, database, inference or provider.
- Independent reviewer **GO**, with **76/76 hand-built wire/reference probes** on the exact
  frozen source/resource. Those overlap the unit contracts and are not added to 86.
  Evidence: `test-results/portable-rows-review-probe.txt`; the independent script is retained
  as `test-results/portable_rows_independent_probe.py`. The reviewer also checked the source
  hashes against committed files and catalog key types/index counts.
- Actual Git `checkout-index` in an isolated temporary repository under `core.autocrlf=true`
  and `core.safecrlf=true` retained identical catalog bytes. The canonical 51,340-byte resource
  contains no real CR/LF or trailing newline; SQL newlines remain escaped JSON data. No shared
  attributes change was needed. Evidence: `test-results/portable-rows-checkout.txt`.
- An isolated image of committed `e96303d` plus the new source/resource built a wheel using
  `uv build --wheel --offline --no-config` and the existing interpreter. ZIP bytes match the
  tested source/resource. A fresh `python -I -S` process imported the module/resource directly
  from that wheel and reproduced the handwritten null-row digest. It loaded no SQLite,
  FastAPI or Torch modules and required no installed runtime dependencies/site packages.
  Evidence: `test-results/portable-rows-wheel.txt`. No serving environment was synchronized,
  installed or changed. This is resource-packaging evidence, not clean-machine/Mac acceptance.
- Scoped Ruff lint/format and AST/compilation pass. No frontend changes or browser acceptance
  are implied by this internal protocol component. No old broad suite was repeated.

The independent vectors cover all 19 empty-table domains; exact integers, text, Unicode
composition, NUL, line endings, JSON, bytes, signed zero, infinities and NaN payloads; ambiguous
concatenations/types; row width; typed/non-null/unique/increasing keys and composite keys;
resource pins; caps including final trailer; guard/iterator identity; and mutable row snapshots.

## Reproduced failure and future-reader gate

B208's initial full run reported **83 passed, three failed in 4.38 s** (JUnit 2.078 s).
Synchronous guards appended, shrank or replaced cells in an accepted list while the draft
hashed it, changing the serialized row after width/key validation. Taking the bounded tuple
snapshot before validation/later guards fixed all three; the final run above is green.
Evidence: `test-results/portable-rows-initial.xml` / `portable-rows-final.xml`. This is controlled
fault injection, not a production incident or a guarantee against hostile concurrent mutation.

The proposed in-process untrusted reader was not implemented: cooperative SQLite progress
and per-value limits did not establish aggregate schema/heap containment. ADR 0083 records
the sourced design gate for a separately bounded child/opening protocol. The reviewer used
one small synthetic in-memory CHECK-expression probe to confirm integrity-check ordering;
catalog generation separately used trusted committed literal DDL in memory without Store.
Neither opened an untrusted/private database object or proves actual backup inspection.

No private library/package/transcript, provider call, model weights, live allocation, cleanup,
database migration, backend restart, merge or push was involved. Focused protocol review does
not replace the final requested whole-project audit after implementation.

## Frozen source image

SHA-256 uses LF-normalized Python source, matching Git blobs; resource bytes are exact:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `src/cbs_transcriber/portable_rows.py` | 6,221 | `e7e0200ca86757590f1ce95cb31c86657062bdb6a946fd6899bed31877efb862` |
| `src/cbs_transcriber/catalogs/portable_schema_12.json` | 51,340 | `c971fcad9df182f484e6978f85c9b8fc290994219afd01e04391b036c1a03330` |
| `tests/test_portable_rows.py` | 13,983 | `2cbcca83c90055b71c3f2e32983c72c315258e4d2dbcab390b4a91219f5fea3a` |

Concurrent T87b/schema-13 implementation, its tests/decisions and longstanding TODO/document
cleanup belong to the other chat and must not be included in this stage's commit.
