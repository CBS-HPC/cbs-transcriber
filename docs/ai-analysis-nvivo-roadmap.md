# AI-assisted analysis and NVivo integration

Long-term roadmap requested 26 October 2026: **T88**, extending T38 (topics/sentiment) and
T43 (research provenance). This is planned work, not a claim of implemented AI analysis or
tested REFI-QDA compatibility. Current reliability, review and UCloud execution fixes retain
their priority. P0/P1/P2 below describe the sequence within this later roadmap.

## Goal and scope

**Transcription → AI-assisted analysis → human review → NVivo export.**

CBS Transcriber should help researchers transcribe interviews, obtain traceable AI suggestions,
review them and prepare structured research material. NVivo remains responsible for advanced
qualitative research: thematic interpretation, coding matrices, code co-occurrence,
cross-interview comparisons and research reporting. This work must not turn CBS Transcriber
into a replacement for NVivo.

## Planned functionality

- **Shared AI provider integration:** UCloud AI Platform and OpenAI API behind a common,
  model-independent analysis engine. Analysis is separate from transcription and audio timing;
  provider/classification approvals continue to apply.
- **AI summaries:** interview summaries and summaries of identified topics, linked to their
  source evidence and retained separately from researcher edits/approval.
- **Topic discovery:** identify recurring topics and suggest relevant codes for human review.
- **Qualitative coding:** deductive application of an existing codebook and inductive
  suggestions for new codes. New model suggestions do not silently change an approved codebook.
- **Hierarchical codebooks:** create, edit and import codebooks while preserving code identities,
  hierarchies and versions.
- **Sentiment and stance:** identify topic-specific attitudes, including aspect-based sentiment;
  define stance separately from sentiment and evaluate both on Danish/English research material.
- **Human review:** accept, modify or reject suggested codes and annotations. Preserve the
  original AI suggestion and the researcher's subsequent decisions.
- **NVivo interoperability:** REFI-QDA Codebook (`.qdc`) import/export and Project (`.qdpx`)
  export, preserving transcripts, code hierarchies, coded passages and supported summaries/memos.
- **Research provenance:** AI provider/model, exact prompts, transcript/codebook versions,
  evidence and human review decisions; keep AI suggestions distinguishable from approved coding.
- **Actual NVivo validation:** verify imported codebooks, coded spans and memos in the target
  desktop applications. Passing XML/archive validation alone does not prove a correct import.

## P0 — Validate interchange and establish the data model

Start with small synthetic interview fixtures and actual NVivo import/export experiments.
Agree the target NVivo Windows/Mac versions and determine supported mappings before promising
interoperability. Test `.qdc` codebook round trips and `.qdpx` project imports containing
hierarchical codes, exact coded passages and supported memos/summaries.

Establish stable transcript/passage/source identities, version-bound text evidence, optional
audio intervals, hierarchical codebook versions, annotation/suggestion states and human review
records. Preserve Unicode text offsets and hierarchy through transformations. Record format or
platform limitations explicitly; never silently flatten hierarchies or drop unsupported content.
Numbered architecture decisions and migration design follow before persistence/job changes.

## P1 — Shared analysis, coding, summaries and review

Implement one analysis workflow with UCloud AI Platform and OpenAI API adapters. Keep model
choices independent of the research data model and retain actual prompts/settings/outputs so
researchers can understand what happened when a provider/model changes.

Add hierarchical codebook creation/edit/import, deductive coding and inductive code suggestions,
interview/topic summaries and researcher accept/modify/reject controls. Bind every run to exact
transcript and codebook versions. Preserve reviewed material when later runs use new prompts,
models or codebooks. Reuse the existing evidence/review and history foundations where suitable.

## P2 — Discovery, attitudes and expanded exports

Add recurring-topic discovery, suggested codes, topic/aspect sentiment and stance analysis.
Evaluate these constructs with representative reviewed Danish/English interview evidence rather
than treating model output as established findings. Expand NVivo exports only for mappings
verified in P0 and subsequent desktop acceptance, including supported summaries/memos.

## Traceability and review requirements

All AI-generated findings must trace to exact transcript passages and versions and, where
available, original recording intervals. Untimed evidence remains valid text evidence; never
invent timestamps to provide an audio link. Summaries require source references as well as codes.

Keep machine suggestions distinguishable from researcher-approved results throughout the UI,
history and export design. Record prompts, model identity, source/codebook versions and each
human decision. Analysis must not rewrite transcript text or invalidate its word timings.
Validate actual NVivo transfers of hierarchies, coded spans and memos, including reviewed
modifications and explicit unsupported-field reports.

## Existing foundations and related plans

The current [manual topic-evidence prototype](topic-evidence.md), immutable transcript history
and methods reports provide foundations. Hierarchical codebook management, shared AI execution,
generated summaries and REFI-QDA interchange are not implemented by that prototype.

Earlier [topic/sentiment research](sentiment-topic-mapping-design.md) remains background evidence;
this roadmap supplies the accepted broader scope and delivery priorities. Coordinate with the
[provenance roadmap](research-workflow-roadmap.md) and [version-control proposal](version-control-redesign.md).
OpenAI Batch API and automatic transcription cleanup remain outside scope.
