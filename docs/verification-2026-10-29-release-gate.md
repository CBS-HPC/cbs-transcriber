# Release checkpoint — 29 October 2026

Development source: `feature/runtime-batches-and-review-layout`, checkpoint `3801ca3`
(including admission/repair implementation `c0a6c26`). This checkpoint has not been
merged, deployed or used to migrate the original library. The remaining roadmap is
separate from the current release gate.

## Isolated database-upgrade rehearsal: passed

An explicit SQLite URI `mode=ro` connection to the existing schema-12 library produced
a consistent SQLite backup in a fresh private, unsynced local staging directory. Only
`Store(staging_root)` was constructed. Neither app lifespan nor queue recovery ran.

The rehearsal compared exact typed values, including float bits and text/BLOB distinctions,
in deterministic primary-key order across all 19 preexisting tables. It also compared old
column definitions, indexes, triggers and foreign-key declarations. Preserved rows included:

- 2 projects, 18 recordings, 31 results, 43 result versions and 20 notes versions;
- 92 jobs, 13 providers, 6 settings rows and 24 review-hint rows;
- 1 native batch and 3 Runtime owners, with 13 members and 13 attempts.

The sole schema addition was nullable `recordings.original_input`, NULL on every recording;
the copy advanced to schema 13. Integrity and foreign-key checks passed. The generated
schema-12 migration backup matched the exact premigration rows/schema, and reopening the
copy produced no additional backup or row changes. The original database remained schema 12,
without the new column. No media, credentials, network or inference runtime was accessed.

Private helper SHA-256:
`0ba4818a69e6f6cf9f1bcb6a0e63d0c0cb6a6bd05d0d85c34cba8df568e35317`.
Private result receipt SHA-256:
`dc36ef50aaac276e790240e6d75eb1762972a82bfddfaa3b8b2499b8be6a7db4`.
The helper, copy and detailed receipt remain outside Git. An initial helper generation
refused its own newly generated read-only backup URI; the exact allowlist was corrected
before a fresh successful rehearsal. That was a harness issue, not a migration defect.

This is database-migration evidence. It does not establish media restoration, complete
backup/restore support, successful production activation or a real-provider run. A real
activation still requires a fresh backup and idle, controlled shutdown.

## Broad verification and targeted corrections

The previous remaining-module process produced no final report. Fresh complete default
non-browser and sequential Chromium suites finished with persistent logs, JUnit reports
and exit receipts under ignored `test-results/`. Their initial results are retained;
subsequent affected-case checks are reported separately, not as a second whole-suite run.

- Unit/API: **2,958 passed, 3 failed, 17 skipped** (2,978 selected; 173 browser cases deselected).
  The three failures were outdated speaker-preparation consent/Protected HTTP expectations
  (SPEC B226), reproduced independently and corrected without runtime changes. All three
  pass focused reruns. The five thread warnings came from tests manually starting new lanes
  without their caller-held lease (B228). All three affected modules pass with thread warnings
  promoted to errors: **15 passed, no warnings or skips**. All assertions/deadlines remain.
- Browser: **158 passed, 15 failed** (173 selected). Thirteen failures concern fresh source
  fixtures, pre-materialization routing, deferred consent and the expanded model catalog;
  all are covered by the final **20 passing affected/adjacent cases**. Fixture migration also
  removed obsolete chunk mocks and an incorrect Activity toggle; real tiny-original decoding
  and prepared receipts now run, while inference remains synthetic. No receipt validator,
  explicit-consent, source/head, token-isolation or producer-count assertion was bypassed (B229).
- The two import cases sent files before the recording view loaded. Normal JSON/CSV import,
  edit and private metadata-report workflows pass after waiting for the view, with all byte,
  version and privacy assertions retained. A deterministic held-response regression then
  proved the missing early-event guard. The import handler now refuses unloaded or
  route-mismatched targets before POST and freezes the loaded ID/revision. Retry after load
  works; no parser, persistence or raw-artifact validation was weakened (V38/B227).

- Ruff check: passed.
- Ruff formatting: 440 files already formatted.
- Application JavaScript syntax and Prettier 3.6.2 HTML/CSS/JavaScript checks: passed.
- `git diff --check`: passed.
- Final deduplicated unit/API coverage: **2,961 passed, 17 skipped**, no outstanding failures.
- Final deduplicated browser coverage: **175 passed**, no outstanding failures, including
  two new held-response import regressions for initial loading and recording switches.

The final coverage counts use exact JUnit test identities across the broad and affected
reruns, rather than summing overlapping scopes. They do not describe one all-green invocation.
The skip reasons are 15 Windows-account symlink restrictions, one opt-in tiny conversion
check and one Linux-only flock test. The optional tiny-conversion proof has separate earlier
evidence; it is not trained-model quality or Linux/Mac acceptance.

The source change during verification is confined to the import handler; backend Python
source remains the initial checkpoint. The guard and full JSON/CSV import/edit/report paths
were checked after that change. Private logs/JUnit reports and the identity-based coverage
receipt remain under ignored `test-results/`.

The offline integration and copy-migration gates are closed for this checkpoint. It is ready
for a controlled merge; actual activation still needs a fresh backup and idle shutdown. No
main merge, restart, original-library migration, environment synchronization, provider
allocation, private upload, model-weight download, push or publication was performed.

Subsequent user-authorized merge and activation are recorded separately in
[backend activation](verification-2026-10-29-backend-activation.md); they do not change the
scope or counts of the offline checks above.
