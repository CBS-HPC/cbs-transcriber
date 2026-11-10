# Version-history review — 28 October 2026

Focused independent review of delivered history, restoration, enhancement merges, wording
adoption and research reports, including the pending T87 original-input evidence additions.
Reviewed the original checkout at `1dda240b12fc5ac125222288863a0bdd8876ece0` plus its current
work. This is not a whole-project audit or completion of the remaining T43/T87 stages.

Outcome: **GO for the reviewed version-history paths after the two narrow corrections
below.** No additional verified blocker was found in this focused review.

## Verified findings

### P2 — A delayed historical restore reply could regress newer saved notes

`static/app.js`, the `#history-restore` response handler, accepted the returned recording
without comparing its independent notes revision with the current browser state. The
merge, wording-adoption and result-Trash handlers already made this comparison.

Reproduction: start a historical restore, close History while its reply waits, finish a
recording-notes save, then release the older restore reply. Transcript view identity is
unchanged by a notes save, so `acceptRecording` accepted the old notes/revision and the
editor rendered them. The database's newer notes were preserved; this was a browser-state
regression rather than a demonstrated persistent data loss.

The new source-level regression executes the actual restore handler and view-acceptance
functions in an isolated Node VM. It originally failed with earlier notes replacing newer
saved notes. The authorized narrow fix checks the same recording view and preserves the
higher notes revision before accepting the restore response. Its three cases now pass:
newer saved notes, newer navigation and a newer unsaved passage draft.

### P2 — Reports omit already captured older Whisper ASR revision pins

`research_reports._processing` accepts model-revision keys only for `da`, `en`, `coarse`
and `speakers`. Actual ASR origins also store `whisper-medium`, `whisper-large-v3-turbo`
and `whisper-large-v3`. Those known keys are silently removed by the report projection.

An end-to-end synthetic result first proves that its immutable origin contains an exact
Whisper ASR pin, then generates a report and checks that pin. The report has no
`model_revisions` field and the assertion fails. New language receipts can independently
carry the ASR pin, so the concrete affected case is already captured ASR metadata from
before those newer language protocols. The saved origin remains intact.

Reported before modifying report code. The authorized correction extends only the fixed
known-stage allowlist, retaining the existing strict repository/revision validation.
End-to-end regressions now preserve each of the three captured Whisper pins in both the
manifest and methods text, without changing its durable origin. Another regression proves
invalid repositories/revisions, unknown stage keys and private extra fields remain omitted.
No current model registry, environment inspection or provenance backfill is introduced.

## Reviewed contracts

- Version payloads retain exact Unicode, ordering, original text/word maps and canonical
  hashes. Restoration reads an exact same-result version, writes through the normal
  revision guard and appends a successor instead of replacing earlier history.
- Result creation retains the producer job; `source_version_id` remains the frozen
  processing ancestor, while `predecessor_id` records the previous review state. New
  original-input observations are inherited through edits and explicit result actions.
- Enhancement merges prove incoming creation/base correspondence before following IDs.
  Timing and speaker groups remain separate; reviewed wording, originals, structural edits,
  unknown fields and named voices remain authoritative. Apply recomputes under a writer
  transaction and binds exact heads, hashes, live results and the recording revision.
- Wording adoption resolves an explicit same-recording passage pair, preserves the target's
  bounds/speaker/originals and invalidates its current word timing. The report reconstructs
  the exact successor from its preserved predecessor and accepted source.
- Trash changes live selection while retaining result rows and historical evidence. Frozen
  work can finish against its exact source; producer idempotency does not restore Trash.
- Reports capture selected versions and lineage in one bounded SQLite snapshot. Payload,
  result-source metadata and version-context byte budgets are checked before loading their
  respective bodies. Parent ownership/hashes and saved evidence bindings are checked.
  Default exports use scoped aliases and allowlisted metadata; explicit text inclusion
  includes selected states, with no unselected original text or private review reasons.
- New T87 report evidence is validated from saved observations, without reopening media,
  inspecting today's decoder or treating local PCM measurements as remote-service proof.

No additional verified data-preservation, ownership or stale-transaction defect was found
in these paths during this review. This statement describes inspected contracts, not an
exhaustive proof over every possible history or a claim of tamper-proof authorship.

## Evidence and limits

Focused command:

```text
uv run pytest tests/test_version_history_review.py -q -o addopts=''
```

- Initial restore probe: **1 failed**, 6.67 s.
- After its narrow fix, notes/navigation/draft probes: **3 passed**, 7.69 s.
- Added origin-to-report ASR-pin case: **1 failed, 3 passed**, 7.39 s.
- After both corrections, the seven new probes plus relevant merge/adoption report,
  ownership and processing-metadata regressions: **22 passed**, 16.81 s. Exact command:

  ```text
  uv run pytest tests/test_version_history_review.py tests/test_merge_report_evidence.py tests/test_wording_adoption_reports.py tests/test_history_api.py::test_report_route_rejects_foreign_versions_and_leaves_history_unchanged tests/test_research_history.py::test_processing_metadata_allowlist_never_copies_credentials_paths_or_endpoint -q -o addopts=''
  ```

- Ruff check and format check for the new regression file passed; `node --check` passed
  for the frontend. The changed report module also passed Ruff check/format. After the
  History and Activity owners released the shared frontend, the coordinating agent ran
  Prettier 3.6.2 and repeated JavaScript syntax verification successfully.

The probes use synthetic data and isolated interpreter state. No running app, real library,
recording body, credential, provider, inference runtime or backend restart was used. No broad
test suite was repeated. Existing focused service/browser tests were inspected alongside
the implementations; the new UI probe does not claim a complete browser acceptance run.

Working-result/alternative roles, persisted suggestions/rejections, complete historical
artifact/environment recovery and scalable ancestry summaries are remaining design or
implementation work. They are not reported here as regressions in the delivered contracts.
