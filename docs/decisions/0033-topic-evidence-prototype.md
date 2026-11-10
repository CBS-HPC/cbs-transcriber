# 0033 — Versioned topic/evaluation evidence before automated analysis

Accepted: 21 October 2026. Implements only slice 1 of sentiment-topic-mapping-design.md,
after the verified T43 history and T44 settings deliveries. No model execution is included.

Ship a clearly labelled research prototype: one versioned, researcher-approved topic codebook
per project, manual evidence annotations, validated imports of externally supplied suggestions,
append-only human review and explicit JSON export. No keyword scorer or mocked model is
presented as production sentiment analysis. Automated local/UCloud/API transports, discovery,
embeddings, aggregate topic maps and accuracy claims remain later evaluated slices.

Schema 7 adds codebook_versions, annotation_runs, annotation_items and annotation_review_events.
Back up schema 6 before one transactional migration; preserve every prior row/history/version.
Run ownership, result-version/hash and codebook-version/hash are immutable. Topics retain stable
IDs; retire rather than silently remove used definitions. Approval is explicit in the codebook
payload. It describes researcher review, not institutional certification.

Validate exact half-open Unicode code-point evidence offsets against frozen passage text.
Require real passage/topic membership and verbatim text; reject fuzzy matches, Boolean indexes,
invented quotes and unknown fields. Derive speaker labels from the source, without inferring
personal identity. Multi-speaker evidence uses unclear attribution unless one holder is explicitly
represented by the supported contract. Timing navigation uses only existing source intervals.

Each annotation is one topic/evaluation pair; multiple pairs can share a passage. Allow positive,
negative, mixed, neutral and uncertain, separately from absent/unprocessed. Uncertain needs an
explanation. Attribution is self, reported_other or unclear. Uncoded passages do not establish
topic absence or exhaustive processing. Imported producer declarations remain unverified.

Keep original suggestions immutable and initially unreviewed. Manual creation appends its first
accepted review atomically. Accept/change/reject create successive review events with optimistic
revisions; accepting an already corrected item retains the latest reviewed wording. Scoped request
IDs/hashes deduplicate retries; conflicting reuse fails. A retry never overwrites a review.
Source/codebook edits cannot retarget old runs. Show older-source status explicitly.

All mutation transactions validate ownership, exact frozen hashes and evidence before committing.
Metadata lists omit quotes/reasons; exact local reads verify integrity. JSON downloads explicitly
include current annotation evidence/definitions and review state, exclude transcript originals,
notes, runtime paths and credentials, and preserve lineage. Existing transcript exports stay intact.
Existing deletion/remote-journal guards still apply and annotation history cascades with its owner.

Keep this interpretation workflow separate from timing/speaker enhancement and ASR configurations.
No provider is called, no audio is uploaded, and no model weights are downloaded in this prototype.
Future text transports need their own capabilities, frozen identities and Protected approvals.
Verify synthetic Danish/English mixed attitudes, Unicode/repeated/cross-passage evidence, malformed
imports, cross-project isolation, stale/conflicting reviews, retries, migration rollback, hashes,
source staleness and browser draft isolation before claiming the slice implemented.
