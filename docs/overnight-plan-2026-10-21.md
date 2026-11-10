# Overnight work: 21–22 October 2026

User-selected order: research provenance/history → Provider Settings → sentiment prototype.
Work window: start now, as authorized when the user left on 21 October, until 08:00 on
22 October, Europe/Copenhagen.
Use the existing managed worktree on feature/overnight-research. Main and its running app
remain untouched. Review progress before each continuation; do not repeat completed work.

## 1. Research provenance and history (T43)

Implement the first slices from research-provenance-design.md: additive backed-up history
migration, immutable result/note versions, exact frozen source-version lineage, recording-scoped
read-only history and a privacy-aware evidence/methods report. Existing results remain current
projections; optimistic revisions and atomic transcription/enhancement follow-ups stay compatible.
Legacy baselines must say observed, without inventing past edits, authors or model provenance.

Acceptance: synthetic schema-5 migration preservation/backup; immutable Unicode/timing/history;
stale/failed save rollback; separate notes revisions; idempotent result/follow-up creation;
enhancement references its frozen version after source edits; scoped history access; coherent
report snapshot and explicit metadata redaction/content opt-in; reproducible historical export.
Finish a verified usable slice before moving on. Record wider artifact capture as pending.

## 2. Provider Settings (T44)

Short basic setup plus Advanced settings, retaining separate provider tabs, named models and
capability labels. OpenAI basic path is key plus preset. Native Transcriber keeps token-first
discovery. UCloud vLLM basic setup starts with UCloud/HF tokens and catalog defaults; keep
serving/runtime/model overrides advanced and distinguish configured from deployed/ready.
Configuration must work while endpoints are offline. Retain the released UCloud API dependency,
HF/served identity, secure credential scopes and explicit Protected choices.

Acceptance: browser checks for each tab/default/advanced toggle, several saved models, no
unintended key save, no endpoint/model checks required for offline configuration, preserved
provider choices/queue snapshots and Protected policy enforcement. Do not imply a configured
model has been hosted or tested. Hosting integration can be mocked without allocating resources.

## 3. Topic/sentiment prototype (T38, if time remains)

Follow sentiment-topic-mapping-design.md: approved topic/codebook, exact source version,
passage evidence spans, positive/negative/mixed/neutral/uncertain suggestions and human review.
Keep annotations separate from transcripts and timing/speaker enhancement. Start with contract,
validation, persistence/API and an explicitly labelled prototype tested with synthetic outputs.
Do not expose a mocked scorer as working production analysis. Actual model execution, default
model selection and accuracy claims remain pending a reviewed Danish/English evaluation.

Acceptance: exact evidence/source-version validation, immutable source, multi-topic/mixed cases,
review-state isolation, Unicode offsets, source-edit/staleness behavior, Protected policy and
no-key/no-body logging. Use deterministic fixtures; no interview inference.

## Working rules and stop conditions

- Read AGENTS, SPEC, TODO, decisions and current test evidence first. Amend SPEC/ADRs before
  persistence/job changes. Preserve unrelated edits; use parallel agents only for independent work.
- Run focused tests per slice, then lint/format, full regression and relevant browser workflows.
  Commit small verified stages and update TODO with honest implementation/acceptance status.
- Do not merge to main, push, publish, restart the main app, or migrate the user's real library.
  Test migrations only on temporary synthetic libraries. No private recordings/transcripts,
  real UCloud jobs, paid API calls, large model downloads or other projects' .env files.
- Stop at 08:00, on completion or when no meaningful offline work remains. Disable the follow-up
  then. Stay quiet for unchanged/non-actionable progress; notify completion, a verified failure
  requiring attention or a needed user decision. Prepare a morning handover with evidence,
  remaining limitations and merge readiness.
