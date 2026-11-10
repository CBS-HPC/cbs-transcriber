# Research versions and producer evidence — 29 October 2026

## Scope and isolation

Implemented in the managed `research-versions` worktree on `codex/research-versions`,
starting at `main` (`cbaa573`). The existing source environment's Python was reused with
child-only `PYTHONPATH` pointing to this worktree. No dependency synchronization, live-library
opening/migration, private recording access, model download, inference, provider allocation,
remote write, main merge or push was performed.

All Store/API/browser fixtures use temporary synthetic libraries. Mock inference writes
synthetic output, and the new producer fixtures write actual bounded synthetic WAV geometry.
These checks establish application contracts, not model quality or remote acceptance.

## First reviewed stage

- `f7808f3`: explicit passage-group conflict decisions, bounded base/current/incoming preview,
  fingerprint-bound Apply and pure historical reconstruction. Keeping current values creates
  an immutable review observation. Unselected dictionaries, named voices, originals and
  producer ancestry remain intact.
- `3c96751`: durable capture for existing public local whole-recording enhancements. Exact
  request/audio binding precedes inference; actual-worker environment observations and validated
  stage files are sealed before checkpoint publication. Creation binding, inherited observations
  and report validation use trusted stored rows and no current runtime/files.
- `e08e2a9`: schema-14 explicit Working **versions**, public current/historical marking,
  independent review selection and exact default project exports/reports. Legacy/new recordings
  stay unassigned. Trash clears the designation atomically; restore and producers never assign it.

Schema-13 migration takes a premigration backup under the library lease, creates empty role
tables in one transaction and preserves every existing research row. Working events bind exact
immutable versions/hashes and separate role revisions. Metadata reports validate bounded event
chains and use scoped aliases. Manifest-only ZIPs explain an entirely unassigned project.

### Executed checks

Commands use the existing environment's `python -m pytest`; `PYTHONPATH` points to this
worktree's `src`. Counts overlap and must not be added as independent coverage.

| Scope | Result |
| --- | --- |
| New Working service/API tests, then roles/Trash/history/export regression | First 6 passed; final combined scope 54 passed in 144.07 s |
| Working-version Chromium workflows, including closed-History and out-of-order previews | 4 passed in 107.43 s |
| New conflict decision/unit/report tests | 14 passed |
| Conflict and existing merge regression | 79 passed in 150.61 s |
| New explicit conflict Chromium workflows | 5 passed in 123.58 s |
| Public producer and existing enhancement regression | 18 passed in 40.44 s |
| Broader producer/report/source-evidence regression | 142 passed in 235.73 s |
| Final producer ownership/durability and Working-report tests | 8 passed in 41.71 s |
| Schema migration, inspection, annotations, notes, policy, Runtime, hints and library regression | 151 passed in 373.77 s |
| Python Ruff and formatting for the first-stage tree | Passed; 454 files formatted |
| JavaScript syntax and exact Prettier 3.6.2 | Passed |

The conflict browser screenshot is `test-results/result-conflict-review.png` in this worktree.
Historical restoration, notes and draft preservation remain covered by existing adjacent
workflows as well as the new explicit selection tests.

### Independent review and corrections

Independent design/code review verified separate view/Working identity, historical version
pinning, no implicit migration/first-result assignment, atomic Trash clearing, exact owner/hash
checks, draft guards and safe explicit conflict choices. It found and corrected these concrete
issues before the stage was finalized:

- Project export had trusted a valid Working pointer without comparing the role event.
  Export now validates state/event/ownership/hash in the same snapshot; pointer-only corruption
  produces a clear skipped item and no transcript bytes.
- A slow historical Working preview could arrive after History closed or replace a later
  selection. Request tokens and exact source-dialog/version guards now reject both cases;
  both have Chromium regressions.
- Working export had parsed the result's current projection before loading the selected
  immutable payload. A one-case regression reproduced a skipped valid Working version after
  projection corruption. The Working query now omits current-head text/names entirely;
  the complete role/export regression passed **21 tests in 6.04 s** after the fix.
- A second unbounded request read followed bounded capture. It is replaced with bounded
  observed size/hash comparison against the exact expected request bytes.
- Omitting the explicit producer receipt keyword could discard a present persisted checkpoint.
  Store now refuses that omission after the earlier producer-idempotency check.
- Output replacement between checkpoint publication and normalized result consumption could
  bind a different result. The normalized result is materialized and captured files verified
  before returning; deterministic replacement is refused without a result commit.
- Historical producer ownership now also checks the saved result's actual producing job,
  rather than trusting the context's job ID alone.
- Windows read-only file handles could not be fsynced. The durable seal uses a writable handle
  without modifying bytes, and a public synthetic workflow exercises the path.

Earlier migration fixtures retained empty future tables while lowering the schema number;
the additive migration follows the repository's existing `IF NOT EXISTS` convention. Older
export/report tests now request their intended explicit reviewed-view scope instead of relying
on the changed default. Their content/integrity assertions were retained.

## Atomic local timing continuation

Three new public/API regression tests were run against the existing split Apply path and all
failed in 36.58 s: job-receipt failure left transcript/version committed; a lost reply returned
409 on retry; newer word timing was overwritten when literal text and passage bounds matched.

The replacement uses one source/revision/current-map checked writer transaction, exact
applied-successor retry receipts and per-unit local timing artifact evidence. Five atomic API
tests plus two public per-unit producer tests passed together with all nine affected existing
realignment/passage-timing cases: **16 passed in 16.32 s**. The first narrow new-only run was
**7 passed in 9.32 s**. The latest public timing scope, including a single root unit,
pending/bound fixed-file inventory and inherited historical report validation, passed
**3 tests in 5.60 s**.

After the final human-readable methods assertions and two additional exactness/Trash replay
cases, the complete new atomic/per-unit scope passed **10 tests in 10.89 s**. Ruff check and
formatting passed across all **459 Python files** in the development tree.
An additional writer guard requires newly bounded passages to use their own complete word
interval endpoints, never their search gap. Its negative case, exact numeric source case and
the existing nullable-word gap workflow passed **3 tests in 4.86 s**.

The old harness had manually executed jobs while their persisted state was still queued and
used non-WAV placeholder bytes for decoded audio. It now marks the actual running producer
state and writes bounded synthetic mono/16 kHz WAV files. Original wording, originals, complete
and partial timing, nullable words, offsets and untouched-passage assertions were preserved.
The existing repeated-Apply case now verifies the same immutable versions are returned without
duplication, matching the new explicit lost-reply behavior.

Independent review checks exact source ownership, canonical typed projection/map comparisons,
strict receipt scalar references, immutable normalized assemblies, no capture backfill, actual
worker input verification and metadata-only historical reconstruction. A generic job update
cannot create or alter an applied receipt or its frozen inputs.

## Runtime full-ASR continuation

The producer contract now records existing Runtime publication/collection observations:
`fb3c5b2` adds the fixed full-ASR policy and fenced attempt receipts; `6226c62` adds the
exact creation-transaction Store binding; `913431b` adds bounded proof validation and actual
Store tests; `6c16242` adds immutable-history report validation and remote preparation scope.
The companion collector hooks are developed against the current remote-preparation variant
separately; their combined collector/ledger gate remains pending. The Runtime worker/request
wire protocol and allocation behavior are unchanged.

| Scope | Result |
| --- | --- |
| Standalone Runtime artifact helper, no FastAPI/models/network | 9 passed in 1.079 s; hardened rerun 9 passed in 0.412 s |
| Actual Store creation/rollback/notes/idempotency/legacy tests plus pure helper | 14 passed in 5.56 s |
| Additional inherited historical-report/privacy case plus previous scope | 15 passed in 9.03 s |
| Latest marked-missing-origin refusal and pure helper cases | 9 passed in 0.574 s |
| Adjacent legacy Store/Working behavior with new binder | 8 passed in 11.44 s |

Store tests use real synthetic member/attempt rows; the existing Runtime ledger finalizer is
mocked to isolate the new binder. The final collector/ledger end-to-end check belongs to the
combined branch. Three real remote-preparation report tests are supplied separately and skip
only in this isolated branch where the remote-preparation module is absent; they must run
against the actual module after integration. No mock validator substitutes for that gate.

Independent review caught an initial validator that would have inferred today's marker for
missing legacy policy, broad eligibility beyond full ASR, and incorrect local-file assumptions
for compressed managed originals/archived attempt outputs. These were corrected before
integration. Request descriptors, downloaded stage bytes and normalized outcome control are
distinct observations. Remote worker environment remains explicitly uncaptured. A present
remote preparation receipt is validated and linked to the exact request; only exact window and
prepared-byte matches use the `remote_decoded_pcm` label.

## Limits

The first-stage producer integration does not invent capture for legacy/cached outputs, prove
provider weights or claim continuing file availability. Missing observations remain gaps.
Same-result timing has a distinct successor binding and preserves the result's original
producer and `source_version_id`. The remaining ordinary API/native producer integration and
combined Runtime collector/remote report gates are tracked separately until verified.
Real-library activation, actual inference, Windows/Mac installation and remote execution
acceptance remain separate evidence.
