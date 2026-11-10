# Portable package/database composition — offline evidence

28 October 2026. T91b2, ADR 0090, I.portable-package, V146. Internal observation only;
full S1/PR02 and T91 remain incomplete. No public capture, backup upload, Restore, activation
or private-package security acceptance is delivered by this stage.

## Delivered boundary

`portable_package.inspect_package(root, expected_commit_sha256=..., guard=...)` creates its
own same-call transport capture. The capture contains exact validated manifest bytes and
immutable inventory/stat receipts, without a parsed mutable graph. The database path, byte
identity and schema/table claims derive only from those already hash-bound bytes. Callers
cannot supply a capture, claims dictionary, parsed manifest, resume token or hash cache.

The unchanged contained S1b reader checks the declared database. After its process and pipes
are closed, the composer rechecks every captured file/directory witness and both inventories.
No partial successful observation escapes a refusal or caller guard exception. The combined
output allowlists metadata and reports `package_database_claims_only`,
`matched_supplied_commit`, explicit unchecked scopes and literal `restore_ready: false`.

Public `inspect_transport` retains its interface, output and non-SQLite scope. Each media
object is body-hashed once per call. The database retains its transport hash and the reader's
separate pre/post hashes, plus SQLite page reads. Marker and manifest bodies are read once;
claims are reparsed from captured memory rather than reopening the manifest. There is no
cross-call hash cache or second media-body hash pass.

## Verification

- The affected initial run collected 135 cases: **131 passed, four existing Windows
  symlink-permission skips**, terminal 18.86 s / XML 17.670 s. This includes the unchanged
  transport suite and the initial 25 composition cases.
- The final composition run passed **29 cases**, terminal 14.35 s / XML 12.048 s. Four
  additions cover malformed transport before opening, wrong sealed claims in the actual child,
  and cancellation/closure. The union is **135 distinct passes and four skips**: 106 existing
  transport passes plus 29 new composition passes. Both real Windows junction cases pass.
- Independent review gave **GO** after 15 overlapping probes on the final source hashes.
  These include a real contained child, exact derived arguments, reaped/closed pipes, literal
  old-versus-new public S1a output, immutable capture/no external receipt authority, guard
  mutation, after-child media drift, late marker/manifest/inventory drift, captured-old-manifest
  derivation, BaseException identity, unsupported schema and malformed-claims refusals. These
  probes are not additional permanent-test coverage.
- Instrumented real-child checks observed one media-body hash, one marker and manifest read,
  and three database hashes. The final witness scan occurs after child closure.
- Scoped Ruff lint/format and source compilation pass. S1b, its captured worker, the typed
  codec and schema catalog are unchanged; their completed broad suites were not repeated.

Ignored evidence: `test-results/portable-package-affected.xml`, `portable-package-final.xml`,
`portable-package-review-probe.txt` and its independent probe script.

An offline wheel was built from committed `1a710ca` packaging/source inputs plus only the two
changed modules. The wheel contains the exact final module/resource bytes. A fresh Python
`-I -S -B` process imported the composer from that wheel and ran its actual contained child
against a tiny complete pinned-schema package. Input hashes/mtimes and the parent SQLite heap
limit stayed unchanged; private canaries/paths were absent from the result and neither
FastAPI nor Torch loaded. No installed or serving environment was synchronized or changed.
Evidence: `test-results/portable-package-wheel.txt` and the two standalone wheel probe scripts.

The first wheel harness attempted to extract unrelated reference documents into a long test
path and hit a Windows filename/path limit. Restricting the synthetic build image to its
required `src`, README and pyproject inputs resolved that harness failure. An independent
probe initially counted its own `git show` process as a reader child; correcting only that
ignored harness resolved its pipe assertion. Neither exposed a production defect or required
a new runtime invariant. No generic retry or production limit change was introduced.

## Frozen identities

Hashes normalize Python source CRLF to LF; the catalog is exact raw bytes.

| File | SHA-256 |
| --- | --- |
| portable_transport.py | 129477fda15bef2282d24ab4d4a52431adac89bd05829f79b0c7a3c66c94d576 |
| portable_package.py | 76a0be1e12956520d4bb4630464c0c08ed824687fdfd2591098321464765db7c |
| test_portable_package.py | 527b0a39afa8e2e8cdb3175e3cf7c95207fa8908047ee5360c69920c76fa8756 |
| portable_database.py — unchanged | e91e40c7c80c633dd3f9be17274ee277230143106adda7fd457a08c0646359cc |
| portable_database_worker.py — unchanged | d58bbdec746fa3e6967a05093c2fb77f62a3dd75d8bf0b59935c9631f4d7dfee |
| portable_rows.py — unchanged | e7e0200ca86757590f1ce95cb31c86657062bdb6a946fd6899bed31877efb862 |
| portable_schema_12.json — unchanged | c971fcad9df182f484e6978f85c9b8fc290994219afd01e04391b036c1a03330 |

## Limits and next work

Immutable captured bytes do not freeze the filesystem. Final metadata/inventory checks detect
observed drift, not an atomic snapshot or hostile content-and-metadata restoration. Caller
quiescence remains mandatory; a later copy/restore must revalidate at use. No fresh end-of-call
media-body hashes or future availability are claimed. The S1b 120-second cooperative budget
applies to its database phase, not the potentially much larger transport scan. Its SQLite-only
heap cap does not bound Python/OS heap or establish native-parser security containment.

Only the pinned schema-12 family is supported. History payloads/mirrors, owner/file membership,
capture/role inventory, legacy gaps, credential exclusion, custody/authentication, quarantine,
destination compatibility and restore authority remain unchecked. The separate
[history-consistency proposal](proposals/portable-history-consistency-2026-10-28.md) records a
reviewed design-only next scope; no semantic validator or broader role-model decision is
claimed. It needs a separately accepted bounded policy before implementation.

There is no public API/UI or installation change, so no browser or user-guide change is
required for this internal slice. Synthetic verification is not live CBS/UCloud/Mac/NVivo
acceptance. No private data, real library, provider, model weights, backend restart, migration,
merge, push or publication was used. The discussion chat's concurrent work was excluded from
the wheel and will be excluded from the commit.
