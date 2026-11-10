# Explicit comparison wording adoption — 27 October 2026

T43f, ADR 0074 and V128. A researcher may preview and explicitly adopt one literal passage
from another live result in the same recording. A heuristic one-to-one comparison row offers
the action, but does not authorize it. Multi-passage and stale comparison rows offer no action.

## Preserved data and evidence

Preview resolves both actual heads in one bounded read. Apply recomputes its fingerprint
under the writer transaction, updates both projections and appends one `wording_adopt`
version. It uses the existing free-edit transform, preserving destination bounds, speaker,
originals, retained old words, unknown fields and every other passage. New wording invalidates
word timing. The source, jobs, notes and producer ancestry are unchanged. No alignment job
is queued, including when ordinary editor realignment is enabled.

Reports traverse the exact saved source within their existing version/payload budgets. They
verify its ownership/hash and reconstruct the complete successor from the predecessor and
literal source text. Corrupt references or unrelated successor changes refuse export. The
manifest emits version/passage aliases without raw passage IDs, wording, names or reasons.
A source later moved to Trash remains historical evidence.

## Verification

- Service/API plus existing merge/history/Trash/pure/report checks: **159 passed**, including
  **41 new adoption cases**. Coverage includes literal NFC/NFD and whitespace, real/absent
  bounds, no-op/blank/duplicate/oversize input, strict request fields, stale heads, Trash,
  corrupted mirrors, transactional rollback and snapshot visibility after commit.
- New report checks plus existing merge evidence: **13 passed**. The initial report run had
  seven expected failures before ancestry projection/verification was implemented. Final cases
  verify privacy, source Trash and refusal of forged source/text/passage/context evidence or
  unrelated changes. These runs overlap the five established merge-report cases above.
- Research/import-report integration: **71 passed**. New-import artifact capture is a
  separate concurrent T43e stage; these checks verify the shared report module integrates.
- Browser comparison/adoption/layout/enhancement merge: **17 passed** in 427.08 seconds,
  then **3 passed, 6 deselected** in 109.40 seconds for the 404 correction, including two new
  real passage-deletion cases. There are **19 distinct browser cases** across these runs.
  XML: `test-results/wording-adoption-browser.xml` and
  `test-results/wording-adoption-errors-browser.xml`. The modal screenshot was inspected.
- Ruff lint and formatting passed across the checkout; scoped Prettier 3.6.2 passed.

An independent implementation review found no data-loss, atomicity, timing-copy or source
mutation defect. It found a diagnostic ambiguity: every preview 404 initially recommended
restarting, although a deleted passage also returns 404. The UI now reserves restart guidance
for the default missing-route response and directs missing-item cases to reload/reselection.
Existing V128 covers this stale-input behavior; no new invariant is needed.

## Limits

Synthetic temporary libraries and isolated browser servers only. No provider calls, new
schema, inference, private-recording access or automatic promotion. The running app was
separately restarted from verified commit `c3c94e6`; this later feature remains on the
development branch until separately activated. Working/alternative role migration, persisted
suggestion/rejection histories and multi-passage conflict resolution remain later T43 work.
