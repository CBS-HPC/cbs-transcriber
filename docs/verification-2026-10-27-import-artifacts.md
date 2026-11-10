# New-import artifact evidence — 27 October 2026

T43e / ADR 0073 / V127 adds exact raw-source evidence for new JSON/CSV imports. It does not
implement the undecided working/alternative role model, reconstruct old associations, change
restoration or perform live-library activation. All fixtures contain synthetic text and bytes.

## Capture and report contracts

Preserve original BOM/whitespace/line endings in an exclusive opaque-ID file, flush/fsync/close,
then verify bounded physical bytes/hash and the exact hint-stripped parser payload before one
result/version/projection transaction. Queue → snapshot → SQLite ordering prevents relocation
from switching owned storage between verification and commit. Direct receipt-bearing writes
also take the snapshot lock. Source dictionaries cannot self-assert capture. Failure retains
orphan/partial bytes without publishing a receipt or removing original media.

Reject managed-child symlinks/junctions, collisions, cross-recording files and nonregular or
escaping paths. A legitimate chosen home ancestor may itself resolve through an OS link.
The pure six-field receipt is copied into source and origin context. Existing edit/restore/
Trash/merge inheritance retains it; a derived result keeps its own origin and parent lineage.

Reports read source metadata and versions in the same SQLite snapshot, once per result, with
SQL byte-length preflight and a separate aggregate 32 MiB source budget. Reject malformed,
one-sided or conflicting claims. Legacy absent/exact `not_captured` evidence stays unknown.
Expose recording-scoped aliases, role/hash/size/format and `availability: not_checked` only.
No retained-file reads, private filenames/paths/IDs/text, full-backup or ASR-model claims.

## Verification

Capture and affected API/history/storage/restore/Trash/merge selection:

- **134 passed, 4 skipped in 191.66 s**; includes all 42 new capture cases (38 passed/four
  Windows symlink-creation permission skips). All four real Windows junction checks passed.
- Ignored evidence: `test-results/import-artifact-capture-affected.{xml,txt}`.

Report/source selection:

```powershell
uv run pytest tests/test_import_artifact_reports.py tests/test_research_reports.py tests/test_research_history.py tests/test_merge_report_evidence.py -q --junitxml=test-results/import-artifact-report-affected.xml
```

- **100 passed in 61.01 s**, comprising 57 new report/validator cases and 43 existing cases.
- Four strengthened fresh-copy validator cases reran successfully in 6.48 s; these overlap.
- Source/context snapshot interleaving, pre-read/aggregate budgets, alias identity, malformed
  claims, historical inheritance, privacy and retained-file-free export are covered.

Real Chromium import → free-text review → metadata report download:

```powershell
uv run pytest -m browser -o addopts='' tests/test_browser_import_artifact.py --junitxml=test-results/import-artifact-browser.xml
```

- **Two passed in 65.35 s**, JSON and CSV. Each verifies raw bytes/BOM/CRLF, saved edits,
  two immutable version receipts, scoped aliases, unchanged original bytes, private-field
  exclusion from the downloaded ZIP and no queued inference.
- Those three affected selections cover **207 distinct passing cases** (205 unit/API and
  two browser), after deduplicating overlaps; four permission skips are separate.
- Independent read-only reviewer: **GO**, 47 targeted passes/four permission skips across
  two nonoverlapping selections. These overlap owner evidence, not 47 additional cases.

The shared checkout also contains another chat's unfinished wording/layout changes. To verify
the exact owned candidate independently, a temporary source image combines `c3c94e6` with only
this stage's 15 owned files/projections. The runner asserts the imported application comes
from that image. Capture/report/history/merge plus both browser cases pass there too:
**140 passed, four permission skips in 160.64 s** (`import-artifact-candidate.{xml,txt}`).
These overlap the 207 cases above. One harmless harness warning concerns anyio having loaded
before pytest's assertion rewriting; test assertions remain enabled. Whole-image Ruff lint and
format pass (321 Python files); all 190 source/test Python files parse. No frontend/dependency
change was needed. Candidate hashes and owned-only patches remain in ignored `test-results`.
The concurrent `63e4836` commit adds only activation/backup documentation, not runtime code.

## Failures found and corrected

The first capture suite had three validation failures plus setup/teardown errors from a very
large pytest-generated case ID. Explicit short IDs retain the oversized-input refusal while
avoiding Windows' environment-value length limit (B169). No production limit was relaxed.

A tiny physical probe reproduced an intact file failing verification on its first import.
On this Windows/Python runtime, pathname `lstat` ctime reflected creation time while descriptor
`fstat` ctime reflected change/write time. Device/inode/size/mtime and exact bytes matched;
each API's own observations were stable. A deterministic stat-semantics regression failed
before correction and passed afterward (B168). Compare cross-API device/inode/size/mtime,
ctime only within each API pair, and retain exact bounded hash/parser proof and post-read
drift refusal. This is separate from ADR 0034's atomic JSON writer semantics.

Ignored diagnostic evidence: `import-artifact-stat-probe.txt`,
`import-artifact-stat-semantics-red.{xml,txt}` and compacted
`import-artifact-capture-focused.txt`. Initial report positive cases also exposed this same
capture failure before reaching report assertions; they passed after the capture fix.

## Limits

No inference, model weights, paid calls, UCloud allocation/private transfer, private transcript
inspection, real-library migration, backend restart, main merge or push was performed by this
stage. Other-chat wording/layout/backend activation work is separate and excluded from its
owned commit. macOS and real-library acceptance remain separate. Receipts establish preserved
import-time byte association, not present availability, backup completeness, original ASR
execution identity, authorship, anonymization or institutional/journal compliance. Broader
artifact/environment capture, role-model decisions and the final deep audit remain open.
