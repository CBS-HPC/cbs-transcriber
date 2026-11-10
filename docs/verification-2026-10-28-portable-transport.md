# Portable transport observation — 28 October 2026

T91a / S1a / ADR 0081 / V137 delivers one internal read-only boundary. Full S1/PR02, portable
backup creation, restoration, activation, upload and CBS retention approval remain pending.
No existing v1 API, interface, dependency, database schema or running app was changed.

## Exact scope

`portable_transport.inspect_transport` requires caller-owned quiescent local staging and a
caller-trusted commit SHA. It binds exact bounded canonical commit/manifest bytes and a closed
inventory of streamed content-addressed objects. It checks strict record types/keysets,
declaration/count/byte/depth limits, safe path declarations, case/NFC collisions, deduplicated
accounting, static links/reparse/hardlink refusal, physical identities and final inventory drift.
It never opens a declared file-mapping path or the database, follows a neighbouring trust
receipt, changes the package or grants copying/cleanup authority.

The result is `transport_integrity_only`, `matched_supplied_commit`, and literal
`restore_ready: false`. `declared_mapping_bytes` is not a destination-space estimate.
Database validity/schema/table claims, ownership, role/capture completeness, gap semantics,
credential exclusion/private contents and restore/quarantine remain explicitly `not_checked`.
An opaque non-SQLite database object deliberately passes transport checks while all database
checks remain pending. This is not proof of authenticated custody, hostile concurrent Windows
namespace safety, ongoing availability, backup completeness or actual Windows/Mac restoration.

## Verification

- Final new-file command: `uv run pytest --noconftest tests/test_portable_transport.py -q
  --junitxml=test-results/portable-transport-final.xml`. **106 passed, four skipped in 9.06 s**
  (terminal summary; XML suite time 7.577 s). The four skips are Windows permission refusals
  for synthetic symlink creation. Both actual root/objects Windows junction tests pass.
  The module is standalone standard-library code; skipping the shared conftest avoids importing
  another chat's concurrent app implementation or this computer's inference environment.
- The two unchanged `tests/test_backup.py` cases passed in **3.54 s** in a separate source
  image of committed `4430e44`, with app/backup import paths asserted inside that image.
  Evidence: `test-results/portable-v1-compatibility.{xml,txt}`. They use synthetic temporary
  SQLite/media fixtures, not an actual library. Existing `backup.py` and its tests are unchanged.
- Union: **108 distinct passing cases**, four permission skips. Independent review gave **GO**
  after 21 pure-filesystem probes plus the original two error-path checks and the descriptor
  reuse fault model on the exact final source. Those overlap suite contracts and are not added
  to 108. Final logs: `test-results/portable-transport-review-probe-final.txt`,
  `portable-transport-review-failures-final.txt` and `portable-transport-review-fd-reuse-green.txt`
  in the same ignored directory.
- Scoped Ruff lint/format and source/test AST/compilation pass. No frontend/browser changes,
  inference, weights, provider calls, private packages, live library operations or activation
  occurred. This stage does not substitute for the final whole-project audit.

Synthetic cases cover wrong trust anchors before manifest access, exact JSON/canonical grammar,
malformed declarations, graph closure, sizes/hashes, deduplication, generic portable paths,
count/size preflight, late changes, bounded growing streams, guard cancellation and descriptor
closure, error curation, cross-stat-API ctime semantics, actual junctions and v1 refusal.

## Reproduced review failures

B196: a stream factory raising `MemoryError` leaked the open descriptor. B197: a scandir
iterator error escaped with an uncurated path canary. The targeted permanent regressions first
reported **two failures/five passes**, then seven passes after the first fixes. Evidence:
`portable-transport-review-red.xml` / `portable-transport-review-green.xml` in `test-results`.

B198: independent review then showed why closing a descriptor after an owning factory fails
is insufficient: a real `io.FileIO` close/reuse fault model made that cleanup close unrelated
I/O. Its permanent case was red before the final fix. The reader now retains raw ownership
with `fdopen(closefd=False)` and one outer finalizer. Factory errors and caller guards retain
their exception identity; next-only directory I/O errors receive static curation. The nine-case
focused run passed, followed by the final full new-file run above. This was controlled fault
injection, not a reported production incident; no generic I/O retry or deletion was introduced.

## Frozen source image

SHA-256 uses LF-normalized source bytes, matching Git blobs:

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `src/cbs_transcriber/portable_transport.py` | 16,993 | `fe17dfbf9cd462d286196f7779c84e8037ed2bacc33c61d03d0ce8ee8e2d27f6` |
| `tests/test_portable_transport.py` | 24,620 | `cafc8a82486a999938054be7b83c3910e1488193719cb4a484c1cf076d6d6ce4` |

The existing v1 compatibility run intentionally excludes concurrent uncommitted application
work. Only this stage's source, test, decision and documentation hunks belong in its commit.
Post-commit inspection caught a staging-projection newline error joining two ADR index links
(B199). The documentation-only correction adds an exact indexed one-link-per-line check;
runtime/test source hashes remain unchanged and application tests were not repeated.
