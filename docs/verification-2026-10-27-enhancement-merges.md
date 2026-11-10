# T43 — Explicit enhancement merges: offline verification

27 October 2026. ADR 0068 / SPEC V121. This is the first conservative merge stage, not the
broader working-transcript/alternative migration or automatic adoption of new model wording.
The running backend and real library remain unchanged at the user's request.

## Delivered

Versions → Results offers Preview merge for a derived enhancement of the reviewed source.
The service verifies its frozen base, incoming creation mapping, both heads and exact content
hashes. A read-only preview lists safe timing/speaker changes and skipped/conflicting passages.
Apply recomputes the plan under one writer transaction and appends a revision-checked
result_merge version. The reviewed wording, originals, structure, unknown metadata and named
voices stay intact. The incoming result, jobs and notes are not rewritten.

Research reports verify and traverse base/incoming/creation references inside their bounded
snapshot. They use aliases and existing metadata allowlists, without adding raw identifiers,
transcript text, private labels or save reasons to metadata-only output. The existing producer
source_version_id keeps its meaning; the reviewed predecessor remains linear and recoverable.

The modal requires a fresh preview after stale Apply, disables no-op application, preserves
independent notes and guards delayed preview/apply replies by recording/result/view/edit
generation. An older backend gives explicit restart guidance before any merge write.

## Review and regressions

An independent design review blocked generic passage-ID matching: enhancement generation can
renumber or split passages. The accepted gate requires exact creation/base correspondence.
An independent implementation review reproduced a second issue: incoming UNKNOWN could erase
an existing unnamed voice. That regression failed before the fix; known labels are now retained
while independent valid timing can still update. No further verified blocker was reported.

The initial report integration also failed four focused tests because it omitted merge parents
and their integrity checks. All five report evidence/privacy cases now pass.

## Verification

- Final combined unit/API group: **133 passed**, 68.93s. Command:
  `uv run pytest tests/test_transcript_merge.py tests/test_transcript_merge_review.py tests/test_result_merges.py tests/test_merge_report_evidence.py tests/test_history_api.py tests/test_research_history.py tests/test_research_reports.py tests/test_version_restore.py tests/test_result_trash.py -q --tb=short --junitxml=test-results/enhancement-merges-affected.xml`.
- This includes 35 initial pure merge cases, the independently reproduced voice regression,
  29 service/API cases, five report cases and neighboring history/Trash/report coverage. Earlier
  113-case and 41-case focused runs overlap; do not add their counts.
- Final Chromium group: **17 passed**, 211.03s: five new merge cases plus existing Results/
  Trash, History and version restoration. New cases cover real preview/apply, reviewed wording
  and named voices, incoming history preservation, no-op behavior, passage-draft confirmation,
  retained notes, stale refresh, delayed navigation and old-backend 404 guidance.
  Command: `uv run pytest -m browser -o addopts='' tests/test_browser_result_merge.py tests/test_browser_result_trash.py tests/test_browser_history.py tests/test_browser_version_restore.py`.
  This run reported to stdout only; no browser JUnit XML or log file was saved.
- Scoped Ruff lint/format, Prettier 3.6.2 and JavaScript syntax checks pass. The synthetic modal
  screenshot `test-results/result-merge-preview.png` was inspected, including literal HTML-like
  text rendering. Test artifacts are ignored by Git.

All tests use temporary libraries, synthetic transcripts and mocked/stubbed execution. No paid
service, real UCloud job, private recording, weight download, real-library migration, restart,
main merge or push was performed. Real user/research acceptance remains separate.
