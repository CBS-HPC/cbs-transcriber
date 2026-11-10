# Anonymisation of final transcripts — proposal, 30 October 2026

Status: proposal for the user's decisions; nothing is implemented yet.

The [local review design and synthetic demonstration](anonymisation-local-prototype-2026-10-30.md)
refine the weekend experiment. They require explicit occurrence/speaker decisions and a
text-only export allowlist; production pseudonym, key and backup policies below remain open.

## What already exists (CBS Inference)

`CBS Inference/ucloud-vllm-agent` hosts **PII-Tracer** (`perplexity-ai/PII-Tracer-vLLM`,
revision `9f34cd1f5010a1de5a7b464eaf3c3a39db01b15a`, MIT): a 596M-parameter bidirectional
Qwen3 token classifier with 37 BIOES labels over nine categories (`private_person`,
`private_email`, `private_phone`, `private_address`, `private_url`, `private_date`,
`account_number`, `secret`, `other_pii`) plus one conversation-level sensitivity score.

- Served by vLLM 0.31.0 in pooling/`token_classify` mode behind a reviewed authenticated
  adapter (no model repository code, no `trust_remote_code`): `POST /v1/pii` returns spans
  with character offsets, category and mean label probability, a masked text
  (`[PRIVATE_PERSON]` …), sensitivity and token count.
- Validated envelope: bfloat16 on one B200 MIG 1g.23gb slice, 4096-token context (longer
  input is refused, never truncated), concurrency 2; ~1.1 GiB weights.
- Live checks (6 October) covered correctness of offsets/masking, auth and load with synthetic
  Danish emails. **Detection accuracy on Danish interviews has not been measured.**

## Goal

Produce a shareable, anonymised copy of each recording's final (Working) transcript and of
project exports, with the researcher in control: the reviewed transcript is never changed,
every replacement is reviewed, and the method is documented for papers and data archives.

## Proposed design

1. **Suggestions from two sources.**
   - *Local rules (offline, no upload):* emails, phone numbers, CPR-like numbers, URLs,
     account/IBAN numbers, plus a per-project list of known names (participants,
     organisations, places) matched as whole words, case- and diacritic-aware. Catches
     Danish names a model may miss and works for Protected projects without any approval.
   - *PII-Tracer on UCloud:* text-only upload of the Working version's passages, chunked
     below 4096 tokens at passage boundaries with overlap for spans crossing a boundary.
     Frozen model identity/revision, Protected approval and preparation consent as for other
     UCloud work. Run mode is a decision below.
   - Named speakers are pseudonymised automatically from the speaker table.
2. **Review (mandatory).** An *Anonymise* view per recording highlights suggested spans with
   their category and confidence; accept/reject each, select text to add a span, and apply a
   decision to every occurrence of the same entity. Low-confidence spans are flagged.
3. **Replacement.** One project-wide entity table maps each distinct entity to a stable
   replacement, so "Mette" is the same pseudonym in every interview. Style is a decision
   below (consistent pseudonyms such as "Person 3" / "Organisation 2", or category tags).
4. **Output as a new result.** Applying the review appends an *Anonymised* result based on
   the exact Working version (never overwriting it). Words outside replaced spans keep their
   timing; replaced spans keep the passage bounds but carry no word timing.
5. **The key (entity ↔ original).** Stored only in the local library, never in exports,
   research reports or (by default) backups. Retention is a decision below.
6. **Exports and reports.** Project export, NVivo export and research reports gain an
   "Anonymised versions" choice; reports document the method (rules, model and revision,
   counts of accepted/rejected/added spans per category) without original text.
7. **Evidence.** Requests, detected spans and review decisions are bound to the exact source
   version and the anonymised result, like the other producer records (ADR 0093/0097/0100).
   Detected spans contain original text, so they stay local and out of exports.
8. **Evaluation.** Before relying on the model for Danish, measure precision/recall per
   category on a small reviewed sample; review stays mandatory regardless.

## Phases

- **P1 (offline, testable now):** entity table, local rules and known-names list, review
  view, replacement, anonymised result, key storage, anonymised exports and report method.
- **P2:** PII-Tracer integration in the chosen run mode, chunking/span mapping and
  evidence, with mocked tests; live acceptance with the user.
- **P3:** Danish evaluation on reviewed samples, thresholds, NVivo polish.

## Decisions needed from the user

1. How PII-Tracer runs: a batch job per selection on the CBS Runtime MIG (no idle cost, no
   public endpoint), a connection to a hosted endpoint from the vLLM agent, or both.
2. Replacement style: consistent pseudonyms, category tags, or a per-project choice.
3. Key retention: keep a local re-identification key, or make anonymisation irreversible.
4. Whether to start P1 now.
