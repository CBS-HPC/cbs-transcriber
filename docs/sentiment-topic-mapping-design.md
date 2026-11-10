# Topic mapping and sentiment: research and proposed design

The [AI analysis and NVivo roadmap](ai-analysis-nvivo-roadmap.md), requested 26 October 2026,
sets the broader long-term scope and delivery order: validate REFI-QDA/NVivo interchange first,
then shared UCloud AI Platform/OpenAI coding, summaries and review, followed by discovery and
sentiment/stance. The research and earlier implementation suggestions below remain background;
they do not imply that these AI features are delivered.

Research checked 21 October 2026. This document proposes the wider T38 analysis workflow.
The first manual evidence/review slice is implemented on the isolated research branch under
[ADR 0033](decisions/0033-topic-evidence-prototype.md); see its
[prototype guide and limits](topic-evidence.md). Automatic model execution, discovery and
research accuracy remain pending. No interview material, credentials, model weights or paid
inference were used in this research.
Coordinate implementation with the [T43 provenance design](research-provenance-design.md)
and its [research roadmap](research-workflow-roadmap.md).

## Recommendation

The feature is feasible. Start with **researcher-defined topics and evidence-linked suggestions**
on a selected, frozen transcript version. A researcher reviews each suggested topic and its
positive, negative, mixed, neutral or uncertain evaluation. Add exploratory topic discovery
across projects only after this smaller workflow can be evaluated and reproduced.

Keep analysis separate from transcription and word/speaker enhancement: it interprets existing
text and must never rewrite a transcript, alter its timings, identify a person, or replace
researcher judgement. The suggested user-facing name is **Topics & sentiment**, with
**Suggest topic coding** and **Explore topics** as distinct operations.

The initial practical path is a compact local multilingual embedding model for topic retrieval,
plus an optional text-instruction model on UCloud for evidence-linked coding. An explicitly
approved text API can be another comparison backend. Generic sentiment classifiers are useful
baselines, but do not solve topic-specific sentiment by themselves.

## What the feature should mean

| Operation | Research question | Output |
| --- | --- | --- |
| Topic discovery | What subjects recur in this collection? | Candidate topics, descriptions and supporting passages; researcher names/merges/rejects them. |
| Topic coding | Does this passage discuss a defined topic? | Zero or more approved topic IDs and exact evidence spans. |
| Topic-specific sentiment | How is a particular topic evaluated in this passage? | A label for the topic/evidence pair, with context and review state. |
| Stance, later | Does the speaker support or oppose a specified proposition? | A separate, explicitly defined support/oppose construct. |

For example, the synthetic Danish sentence “Jeg kan lide fleksibiliteten, men systemet er for
langsomt” can evaluate flexibility positively and system performance negatively. One overall
negative score would discard that distinction. Mere mention of a topic is not an opinion;
“the new system starts Monday” is not evidence that its speaker likes the system. Reporting
another person's opinion also does not establish the current speaker's attitude.

Use five sentiment labels with a versioned codebook:

- **Positive / Negative:** supported evaluation of the specified topic or aspect.
- **Mixed:** both favourable and unfavourable evaluations of that same target in scope.
- **Neutral:** topic is present but the evidence contains no evaluative position under the
  agreed coding rules. Do not silently equate neutral with a balanced personal attitude.
- **Uncertain:** insufficient context, irony, transcription uncertainty, ambiguous attribution
  or conflicting interpretation. Record the reason; this is not neutral.

“Topic absent”, “not processed”, model refusal and processing failure are separate states.
Do not force every passage into a topic or polarity. Support several topics in one passage.
Emotion, vocal affect, personality, diagnosis and speaker identity are outside this design.

## Current research: useful methods, no universal winner

This is a targeted survey of primary sources, not an exhaustive leaderboard. None of the
verified sources establishes the best model for CBS Danish/English research interviews.

- **Embedding-based discovery** groups semantically similar passages and derives topic
  representations. BERTopic combines embeddings, clustering and class-based TF-IDF; its
  guided mode can incorporate seed topics. This is a practical discovery baseline, with
  researcher review and explicit outliers. Its documentation notes stochastic variation;
  save embeddings, parameters, seeds and the actual fitted assignments. [BERTopic algorithm](https://maartengr.github.io/BERTopic/algorithm/algorithm.html),
  [guided topics](https://maartengr.github.io/BERTopic/getting_started/guided/guided.html),
  [API and reproducibility caveat](https://maartengr.github.io/BERTopic/api/bertopic.html).
- **Instruction-model topic discovery and coding** can produce readable labels and apply a
  codebook with examples. TopicGPT demonstrates this approach, including topic refinement.
  Its reported Wikipedia results do not establish interview or Danish performance.
  [TopicGPT, NAACL 2024](https://aclanthology.org/2024.naacl-long.164/).
- **Aspect-based sentiment analysis (ABSA)** models a target/aspect and its evaluation,
  rather than assigning one feeling to an entire interview. A 2025 study of aspect sentiment
  quadruples compares prompted LLMs with fine-tuned methods and still identifies a role for
  human annotation. In 2026, DimABSA extends the task to dimensional valence/arousal across
  several languages and domains. Continuous scores are a research direction, not a reason
  to display unvalidated precision in our first version. [ASQP study, XLLM 2025](https://aclanthology.org/2025.xllm-1.15/),
  [DimABSA, ACL 2026](https://aclanthology.org/2026.acl-long.1881/).
- **Evidence and interpretation remain separate questions.** SAINT studies multilingual
  sequence sentiment and token-level interpretability, with language-dependent results;
  its reported languages do not include Danish. Inductive-coding research also finds
  systematic differences between people and LLMs. These support evaluating both label
  quality and evidence quality locally, rather than treating a fluent explanation as proof.
  [SAINT, 2026](https://aclanthology.org/2026.sigul-1.6/),
  [human/LLM inductive coding, Findings NAACL 2025](https://aclanthology.org/2025.findings-naacl.361/).

Inference for this app: a hybrid workflow is more suitable than choosing a supposed
state-of-the-art sentiment score. Retrieve candidate passages, apply the study's codebook,
validate quoted evidence and let researchers adjudicate. Exact quotation establishes where
a suggestion came from; it does not prove the interpretation is correct. A workshop study
on LLM-assisted thematic analysis likewise stresses context, transparency and continued
researcher oversight; it is a preprint, not an interview-accuracy benchmark.
[Ornelas et al., 2025 preprint](https://arxiv.org/abs/2511.14528).

## Small candidate set for Danish and English

These are evaluation candidates, not additions to the shipped speech catalog. Model/task
compatibility, an exact revision, runtime requirements and licensing must be recorded before
installation. Do not download models during ordinary setup or a test run.

| Candidate | Proposed role and execution | Verified facts and limits |
| --- | --- | --- |
| [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) | First local CPU candidate for topic retrieval and passage embeddings; reusable on UCloud. | MIT metadata; Danish and English listed. 12 layers, 384-dimensional embeddings, 512-token limit. Follow documented input prefixes; split before truncation. Similarity is not sentiment or a calibrated topic probability. [Primary card](https://huggingface.co/intfloat/multilingual-e5-small/raw/main/README.md). |
| [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | Larger embedding comparison, preferably UCloud for large collections; optional local evaluation. | Apache-2.0 metadata, 0.6B parameters, 100+ languages and instruction-aware embeddings. Broad language coverage is not evidence of Danish interview accuracy. Compare retrieval quality and memory with E5 before preferring it. |
| [Qwen/Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) | Initial UCloud text-instruction candidate for codebook/topic/sentiment suggestions; optional explicitly prepared local quantized runtime later. | Apache-2.0 metadata; 8.2B parameters; multilingual model with documented OpenAI-compatible serving. General instruction capability is not a validated sentiment model. Freeze chat template, thinking mode, generation settings and exact revision. This is a deliberately bounded candidate, not a claim to be the newest or best model. |
| [alexandrainst/da-sentiment-base](https://huggingface.co/alexandrainst/da-sentiment-base) | Danish-only CPU baseline for passage polarity, not a topic-aware annotator. | Positive/neutral/negative Danish BERT trained using Twitter and EuroParl data. Interview-domain accuracy is unestablished. **License conflict:** page metadata says Apache-2.0, card body says CC BY-SA 4.0. Resolve with maintainer/repository evidence before bundling or treating licensing as settled. |
| [cardiffnlp/twitter-roberta-base-sentiment-latest](https://huggingface.co/cardiffnlp/twitter-roberta-base-sentiment-latest) | English-only CPU polarity baseline for evaluation. | CC BY 4.0 metadata; English Twitter/TweetEval training and three sentiment labels. Pin a commit despite “latest” in the ID. Tweet performance cannot establish research-interview or topic-specific accuracy. |

BERTopic is an optional discovery library, not another language model. Its code has an
[MIT license](https://github.com/MaartenGr/BERTopic/blob/master/LICENSE); embedding models,
datasets, quantizations and their dependencies retain their own conditions. Do not install
the entire discovery stack for a first codebook/sentiment slice.

Resource planning is provisional. Compact encoders are plausible CPU-first candidates,
including Intel Macs, but latency and installation support need real Windows/Intel/Apple
Silicon checks. For scale only, 0.6B weights at two bytes per parameter are about 1.2 GB;
8.2B are about 16.4 GB. These are arithmetic weight-storage estimates, **not minimum RAM or
VRAM**: activations, runtime, token context and concurrent jobs add overhead. Quantization
changes requirements and may change accuracy. Measure peak memory, cold/warm latency and
throughput with the exact runtime; do not promise a CPU speed from parameter counts.

## Processing and user flow

1. Open **Topics & sentiment** for a recording or selected project results. Show the exact
   saved result/revision and whether its text has been reviewed. Default to reviewed text;
   permit unreviewed text with its provenance flag preserved. No word timing is required.
2. Create or select a versioned topic codebook: stable IDs, names, descriptions, inclusion
   and exclusion rules, language-specific examples and optional parent topics. Researchers
   can enter a small list manually; suggested discovery never silently changes it.
3. Choose **Find passages for topics** or **Suggest topic coding & sentiment**. Show execution
   location and the limited text scope before running. Local CPU, UCloud text model and
   approved text API are separate capability choices; an ASR model is not a text annotator.
4. Process bounded passage groups with neighbouring question/answer context and explicit
   speaker roles when known. Do not split only by ASR part boundaries. Keep a manifest of
   coverage, context overlaps, exclusions and token limits; no silent truncation.
5. Review suggestions beside highlighted evidence. Allow Accept, Change and Reject,
   researcher notes, multiple labels and Uncertain. Clicking evidence seeks to its existing
   passage/word interval when available; it never creates timings.
6. A project view filters topics, speakers, review state and polarity and lists evidence.
   Start with a topic-by-recording matrix and a timed evidence list. An optional topic map
   can follow; geometric proximity must not imply causation or a validated code hierarchy.
7. Export reviewed annotations and optionally include unreviewed suggestions with explicit
   state. Keep transcript exports unchanged; JSON/CSV annotation exports include lineage,
   exact evidence, codebook version and review history. Do not claim NVivo coding import
   compatibility until that separate format/workflow is implemented and accepted.

Do not default to a single interview “positivity score”. Counts depend on the denominator:
passages, turns, recordings, coded time and participants differ. Distinguish interviewer
questions from participant evidence. Voice labels are not cross-recording participant IDs;
participant counts require an explicit researcher mapping. Report unprocessed/uncertain
coverage and reviewed status beside every aggregate. Participant-weighted thematic displays
have relevant prior art, but are an optional view rather than proof of prevalence.
[ThemeClouds, HCINLP 2025](https://aclanthology.org/2025.hcinlp-1.14/).

## Evidence contract and compatibility with T43

Analysis should own **annotation runs**, not new editable transcription results. A run reads
an immutable transcript snapshot and produces separate, versioned annotation artifacts.
Suggested and reviewed layers are distinct. Use T43's proposed `source_version_id` and
`source_payload_sha256` identities and event history rather than building a competing
version-control system. Neither document authorizes a schema migration by itself.

Proposed minimal logical records, subject to an ADR and migration review before coding:

| Record | Required identity/content |
| --- | --- |
| Codebook version | Project, parent version, stable topic IDs and definitions, language examples, schema/hash, author/time and change reason. |
| Analysis run | Recording/project, exact result and immutable revision/snapshot hash, codebook version, task, source scope/context IDs, model/runtime/prompt settings, start/end/status and coverage. |
| Suggestion | Run, topic ID, optional target/aspect, polarity or abstention reason, evidence spans, attribution, raw artifact reference and model-supplied score type if any. |
| Review event/version | Prior annotation version, accept/change/reject action, researcher-entered code/label/evidence, local actor when known, UTC time and optional rationale. |

Anchor evidence to `{source_version_id, passage_id, start_char, end_char, exact_text}`,
using half-open **Unicode code-point offsets** into the exact stored passage text. Validate
`passage_text[start_char:end_char] == exact_text` server-side without normalization. Browser
highlighting must convert correctly from code points to JavaScript UTF-16 offsets. Keep
multiple spans when evidence crosses passages; never concatenate away attribution.

Prefer model output containing passage IDs and verbatim quotes. The server computes offsets
only after exact verification. A repeated quote requires an unambiguous occurrence locator;
reject/flag ambiguous spans rather than fuzzy-matching a convenient occurrence. Unknown
passage IDs, invented quotes, empty/out-of-bounds spans and unsupported topic IDs cannot
be accepted as grounded annotations. Validate target and opinion evidence separately when
the model supplies both. Store only a concise justification, not private chain-of-thought.

Each suggestion must distinguish the **opinion holder** from the person currently speaking:
`self`, `reported_other`, `unclear` and the source speaker label are adequate first-version
attribution. Preserve speaker uncertainty. Word/audio navigation derives from source
timings and is labelled as unavailable or passage-level when exact timing is missing.

Editing a transcript does not move annotations to the new wording automatically. Older runs
remain attached to their source version and display “based on an earlier transcript version”.
Rerunning creates a new run; restoring/selecting an old text does not erase analysis history.
Notes are excluded unless explicitly chosen as context and separately frozen/versioned.
Never overwrite a reviewed annotation on retry; deduplicate checkpoints with run/window IDs.

Provenance includes input/codebook/prompt hashes, model ID and exact revision when available,
tokenizer/template/quantization/runtime identity, app/lock identity, seed/decoding parameters,
execution location, submitted context, response IDs/usage, raw outputs, validation failures,
coverage and reviewer changes. Record opaque hosted-model revisions as unknown. Preserve
actual outputs since deterministic regeneration cannot be promised. T43's workflow report
can describe both transcription and subsequent interpretation and their different limits;
it must not assert generic top-tier-journal compliance.

## Architecture, execution and privacy

Keep the seam small: a proposed `annotations.py` owns schema/evidence validation and the
analysis coordinator; `store.py` owns versioned persistence; the existing durable queue owns
run status/checkpoints. A text-annotation transport and a local worker can share a validated
output contract without an inheritance tree. This is a proposal; do not add source modules
or change schema in this design-only task.

The existing speech-provider settings cannot establish text capability. Add a distinct
`text_annotation` capability/profile when implementing this feature, independent of native
Transcriber Batch and of the model used for ASR. UCloud may host a text LLM through the
existing UCloud API package and vLLM hosting path, but the speech deployment is not presumed
to support chat, embeddings or structured annotation. Temporary hosting requires runtime
readiness checks when executing, not a live endpoint merely to save a configuration.

JSON Schema output is available in supported OpenAI text models and vLLM structured output;
the exact server/model version must be checked. Schema conformance constrains shape, not
truth, relevance, attribution or quoted evidence. Handle refusals and truncated output
explicitly, then perform the app's semantic/evidence validation. Keep this transport
separate from `/audio/transcriptions`. [Official OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs),
[vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/).

- Apply the current Protected policy to **text and embeddings**, which can disclose interview
  information. OpenAI/Custom default off and UCloud default on for new configurations;
  preserve explicit choices and require approval for the exact text model/endpoint identity.
  An approved ASR profile does not silently approve a different analysis model.
- Recheck the current classification/approval and frozen identity before every remote request,
  upload and retry. Never fall back to an external API when local/UCloud processing fails.
  Institutional acceptability is not established by the app's eligibility switch.
- Send only selected text/context needed for the task, not recordings or entire projects by
  default. De-identification is optional processing with its own recorded version; it does
  not automatically make a Protected project public or remove re-identification risk.
- Models receive transcript content as untrusted data, not executable instructions. No tool
  execution, URL fetching, code execution or automatic follow-on uploads from model output.
  Keep no-follow-redirect transport, bounded response/window size and plain-text rendering.
- Local operation is download-free/telemetry-free by default with an explicit prepared model
  and sanitized worker environment. Do not inherit unrelated API/HF/UCloud tokens. For remote
  analysis, setup tokens stay in the OS credential store; inference receives only its scoped
  credentials, and tokens never enter annotation manifests, prompts or logs.
- Reuse durable job intent, remote IDs, cancellation ownership and uncertain-submission rules.
  Preserve checkpoint/raw evidence on failure. Download and validate output before optional
  cleanup of app-owned remote folders; do not silently erase evidence needed for recovery.
  Project deletion/retention must account for derived embeddings and annotation history too.

## Evaluation before choosing defaults

Create a small consented or openly licensed **interview** pilot, with separate Danish and
English strata, several speakers and more than one topic. Synthetic passages test software
correctness; they cannot establish model accuracy. The current FLEURS read-speech fixtures
have no interview topic/sentiment reference labels and are not an adequate analysis benchmark.

Researchers define the coding method and codebook before measuring automation. For a
structured codebook pilot, two independent annotators plus documented adjudication can expose
ambiguity; preserve disagreements rather than declaring the adjudicated label absolute truth.
Agreement metrics are appropriate only where consistent code application is the chosen
method; they do not certify every form of reflexive thematic analysis.

Evaluate against held-out recordings/participants, not random neighbouring sentences from
the same interview. Keep pilot examples used in prompts separate from the final test split.
Include negation, reported speech, irony, mixed attitudes, topic absent/neutral, ambiguous
pronouns, ASR errors, Danish/English code switching and differing interviewer prompts.

Report separately:

- Multi-label topic precision/recall and macro/micro F1, per language and topic; include false
  negatives so a tidy evidence list does not hide missed passages.
- Sentiment confusion matrices and macro F1 **conditioned on a correctly identified target**,
  plus end-to-end topic/target/evidence/polarity quality. Do not disguise retrieval failures.
- Exact evidence validity and span overlap, incorrect speaker attribution, uncertain/abstain
  coverage and model disagreement. Similarity, softmax and self-reported LLM confidence are
  not interchangeable probabilities. Calibrate only with an adequate held-out set.
- Researcher acceptance/correction rates and review time; qualitative review of contextual
  usefulness and minority/contradictory cases. Do not use another LLM as the sole judge.
- Discovery stability across seeds/samples, distinctiveness and representative/counterexample
  coverage. Topic coherence alone cannot establish interpretive validity.
- End-to-end cold/warm time, peak process/device memory, model acquisition size, API token
  use/cost where relevant, corpus size and execution location. Record failures and retries.

Researcher-approved acceptance thresholds must precede a default-model claim. Until then,
show **experimental suggestions requiring review** and avoid automated substantive conclusions.

## Implementation slices and acceptance

1. **Annotation foundation — smallest first slice.** One frozen result, one manually entered
   codebook and manual topic/polarity evidence annotations; versioned review events, evidence
   highlighting/seek and JSON export. Add one mocked suggestion adapter using that same
   contract. This delivers a reviewable workflow without committing to an unvalidated model.
   If T43 is not implemented yet, preserve an immutable, hashed source artifact and identify
   the missing general-history integration explicitly rather than using a mutable revision alone.
2. **One optional automated coding backend.** Add a prepared UCloud text-model adapter
   (Qwen3-8B candidate) with bounded windows, schema/evidence validation, durable checkpoints
   and Protected checks. Evaluate before enabling project-wide jobs. A comparison API backend
   needs explicit configuration and policy approval, never an inferred ASR capability.
3. **Local CPU topic retrieval.** Explicit model preparation and offline E5-small embeddings,
   cache keyed by source/model/version, topic definitions as queries and reviewable matches.
   Compare a simple TF-IDF retrieval baseline; keep embeddings if they improve the pilot.
4. **Project discovery and aggregation.** Optional BERTopic/embedding clusters or instruction
   topic proposals, codebook merge/split history, outliers, evidence matrix and coverage-aware
   filters. Consider maps only after the evidence list is useful.
5. **Evaluation and methods reporting.** Pin the pilot, compare Danish/English candidates,
   document limitations and produce T43-linked analysis provenance and reviewed exports.
   Local sentiment classifiers remain comparison baselines until target-aware value is shown.

Software acceptance uses synthetic fixtures and mocked providers: Unicode/repeated evidence,
cross-passage spans, absent timings, malformed/invented evidence, overlapping topics, stale
source/review races, policy revocation between windows, resumable partial jobs, idempotent
completion, cancellation, redacted errors and import/export round trips. Browser acceptance
covers suggestion review, topic filtering and navigation without altering transcript drafts.
Research accuracy and actual Windows/Intel Mac/Apple Silicon/UCloud performance are separate
opt-in acceptance, not conclusions from software tests.

### Open research decisions

- Study-specific coding method, initial topic taxonomy and what counts as neutral/mixed.
- Whether cross-interview participant identities are needed and who maintains that mapping.
- A suitable Danish/English licensed or consented pilot and acceptable error/review burden.
- Prepared text-runtime availability on UCloud and supported Mac/Windows local environments.
- Resolution of the Danish baseline's conflicting license metadata before distribution.

These questions do not block the manual evidence/version foundation. They do block claims
about validated automated analysis, institutional approval or publishable research conclusions.
