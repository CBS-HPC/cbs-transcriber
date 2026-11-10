# Cross-stage integration check — 28 October 2026

This check combines the newly integrated original-input, report, history, launcher,
evaluation, batch-preparation assessment and Activity projections. It supplements their
individual reviews and focused evidence; it does not repeat or replace the full test suite.

```text
uv run --no-sync pytest tests/test_original_input_integration.py tests/test_original_input_recovery.py tests/test_audio_input_reports.py tests/test_version_history_review.py tests/test_installation_audit.py tests/test_evaluation_suite.py tests/test_batch_preparation_assessment.py tests/test_runtime_progress.py -q -o addopts='' --tb=short --junitxml=test-results/2026-10-28-cross-stage.xml
```

**93 passed in 98.87 seconds.** The JUnit result is ignored local evidence. Ruff check passed
on the affected production modules, benchmark and new tests. Format checking found one
layout-only fixture line; formatting that file and checking it again passed. The shared
frontend had already passed Prettier 3.6.2 and JavaScript syntax checks after both edit owners
released it. The separate 13-case Runtime browser run and 125-case source/bundle run remain
recorded in their own verification documents; their totals must not be added to infer unique
whole-project coverage.

Verified commits from this coordination pass:

- `e0003fa`: launcher/registry fixes and source-install audit.
- `cf4e0ed`: delayed-restore notes and captured model-pin preservation.
- `fb1e16e`: offline stratified evaluation and licence addendum.
- `c415031`: truthful Runtime batch Activity progress.
- `632fe03`: direct-original source/preparation evidence, with T87b activation hold.
- `9bd52cc`: inert fixed-language alignment bundles.
- `d607e9e`: synthetic multi-file preparation assessment.

All processing inputs were synthetic or the existing licensed public fixtures. No private
recording, paid/provider job, model-weight download, live cleanup, dependency reinstall,
real-library migration, server restart, main merge or push occurred. The running app remains
on its separate verified `c3c94e6` checkout. T87 source inspection still needs background
admission and playback-readiness separation before release; remote preparation, large-repair
routing, actual model evaluation, real Mac/clean-machine acceptance and institutional review
remain pending.
