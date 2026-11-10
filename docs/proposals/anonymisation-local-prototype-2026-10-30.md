# Local anonymisation review — design and synthetic acceptance plan

Status: proposed design, 30 October 2026. No production feature, schema, job, provider or
key store is introduced. The [original proposal](anonymisation-2026-10-30.md), dated
[model survey](../evaluations/anonymisation-models-2026-10-30.md) and
[weekend scope](../work-plans/weekend-2026-10-31.md#anonymisation-options-to-resolve)
remain inputs; their open decisions remain open. This document does not revalidate model
claims or select a detector. It refines the local, transcript-only demonstration.

## Smallest useful experiment

Take one exact Working version, suggest local literal spans, record explicit per-occurrence
decisions and speaker labels, and construct a separate text-only derived preview. The source
is unchanged. A final review displays the complete derived text and the export inventory.
Detection and completed review do not establish that every identifier has been removed.
The demonstration uses fictional markers and reserved `.invalid` addresses only.

The executable [example](../examples/anonymisation_demo.py) has three operations: suggest
email/explicit known literals, construct a literal span, and derive a reviewed preview. Its
inputs are caller-owned synthetic objects; it never reads a library/file or calls Store,
an application route, a provider or a model. It prints only the demonstration export.
It represents review decisions; it is not evidence that a person performed that review.
Square-bracket replacement labels are fixture values, not proposed production defaults.

## Exact source and review contract

| Item | Proposed contract |
| --- | --- |
| Source | Same-recording, live-result Working **version**, verified canonical payload/hash, plus recording/result/version IDs and Working-role revision from one database read. An active view or newest result is not a substitute. Missing designation refuses; no fallback. |
| Coordinates | Passage identity within that immutable payload and half-open Unicode code-point offsets `[start,end)`. Match the literal quote exactly; retain CR/LF, combining marks and emoji. Do not calculate coordinates on normalised/casefolded text or concatenate passages. |
| Browser selection | Convert DOM UTF-16 selection offsets to source code points; round-trip the literal quote against that same version. Display and submission keep one frozen source/review context. |
| Suggestion | Immutable source binding, passage, offsets, literal quote, category, detector/rule version and observed score or explicit `not_applicable`. A pattern score is not a model probability. Same spelling does not establish entity identity. |
| Review | Every suggestion gets an explicit Keep or Replace decision; added spans use the same literal validation. Replacement text and speaker labels require review. No empty/implicit decisions and no default Accept-all. Keep applies no replacement for that suggestion; it neither certifies the wording as safe nor blocks another explicitly approved overlapping replacement. |
| Conflicts | Identical suggestions may share displayed evidence while retaining their detector origins. Accepted overlapping/nested replacements must be resolved explicitly; never merge, trim or pick one by confidence. Occurrence-wide actions preview exact members and scope before approval. |
| Changed source | A new source version/hash, Working role revision/designation, Trash state or review revision invalidates Apply. Keep the old draft bound to the old source for inspection; never rebase its offsets automatically. Returning to an earlier Working version still has a new role revision. |
| Publish, later | Re-read ownership, live state, exact version/hash, role revision and decision fingerprint under the existing writer/snapshot discipline. Recompute the complete successor server-side and append a new result plus creation/review receipt atomically and idempotently. A posted hash/source object is never authority. |

The prototype models the version/hash/role fence in memory and decides every **supplied**
suggestion; it cannot prove that a caller supplied a complete detector run. Production must
freeze the run's ordered suggestion manifest/count/hash, rule/known-term revisions, complete
source scope and review revision/fingerprint, then verify that exact set when applying. An
incomplete/resource-refused run cannot become “no identifiers found.” Ownership, Trash,
writer CAS, durable retries and history receipts require a separately reviewed implementation;
the example cannot grant them. Existing `Store.add_result` changes the active view, so the
production design must explicitly choose a publication seam that preserves the researcher's
source/active/Working choices. Do not reuse it blindly or overload a generic source dictionary
to self-assert an anonymisation receipt. `source_version_id` keeps its producer meaning.
The later review interface must render literal text with native text/value nodes, preserve
drafts by source/run identity and refuse stale replies; source text is never HTML.

## Local rules and human decisions

Start with the fixed example's literal known-term and email candidates. Exact known terms
match original code points with Unicode word-edge checks; explicit spelling/genitive/case
variants are separate reviewed entries. The example deliberately performs no fuzzy matching,
casefold offset mapping, automatic entity linking or cross-passage matching.

For a later rules-only trial, version each rule and add synthetic positive/negative fixtures
before enabling phone, CPR-like, IBAN/account, URL, date or address candidates. These are
candidates, not claims of valid identifiers. Do not turn a failed checksum/date/context check
into proof that text is non-identifying. Spoken numbers, ASR errors, indirect identifiers,
rare occupations/events and combinations of otherwise ordinary facts require manual review.
The dated survey is a starting point for rule validation, not a production accuracy result.

Known terms, their variants and original-to-replacement mappings are sensitive source data.
Keep them out of logs, generic settings, exports and methods reports. For the experiment they
exist only in disposable memory. Review each speaker's display label too: source speaker IDs
and names can themselves identify a person. Generate export-scoped voice aliases and require
explicit reviewed display replacements; do not silently pseudonymise named speakers.

## Derived content and export allowlist

The first demonstration drops **all timing** rather than retaining partially stale word
maps. It reconstructs passage text from non-overlapping replacements, preserving every
unreplaced character exactly. It creates new export-scoped passage/voice aliases and copies
no source dictionaries wholesale. Audio/video redaction and playable media are separate work.
Later unchanged-word timing retention needs literal word/offset correspondence and dedicated
coverage tests; replacement words never inherit or fabricate timing.

| Shareable demonstration | Private/local-only input or receipt |
| --- | --- |
| Fixed format/version; numeric passage order; reviewed text; export-scoped voice alias; explicit reviewed voice display label | Source IDs/hashes, original text/word maps, alternate/original wording, arbitrary imported fields, notes, codebook evidence, review reasons, filenames, titles, collection/project names, paths, timestamps, media URLs, history, raw provider output and entity mappings |
| Fixed method identifier, rule version and `explicit-demo-decisions`; timing marked omitted | Full suggestions/quotes, per-occurrence decisions, source/result lineage and key material |
| Fixed basename such as `transcript-0001.json` | Original basenames, recording/result labels and storage paths |

Use a dedicated export serializer and archive/manifest allowlist. Current ordinary transcript
JSON limits passage fields, but it resolves source speaker names; current project ZIPs also
include filenames, labels, collection/project names and version metadata (`transcripts.py`
`export_transcript`, `exports.py` `project_archive`). Renaming visible text alone is insufficient.
Do not route this experiment through ordinary project downloads or research reports. Detector
counts/categories and source hashes are also withheld until their disclosure policy is chosen.
The example returns its private source/review receipt separately and prints only the allowlist.

Backup/restore remains a complete recoverable local-library workflow. A transcript export
allowlist does not exclude sources, notes, Trash or a future key from backups. The original
proposal's “key excluded from backups by default” requires a separate accepted storage/capture
design; it cannot be promised by omitting one export field. Keeping original transcripts also
means that discarding a mapping alone does not make the library irreversibly anonymised.

## Decisions deliberately not made

| Decision | Options to review before production |
| --- | --- |
| Replacement style and scope | Category tags; researcher-chosen labels; stable project pseudonyms. Define cross-recording linkage, inflection and collision behavior; matching names alone never links people. |
| Entity/key lifetime | No persisted mapping in the experiment. A production reversible key needs owner, storage location, access, encryption/key custody, backup/restore, export exclusion, rotation, retention and deletion/withdrawal decisions. “Irreversible” needs a scope covering retained originals and history. |
| Publication and designation | Whether derived results stay inactive; an independently versioned reviewed-anonymised designation; what subsequent edits invalidate. No automatic replacement of Working or automatic broad-export selection. |
| Future detector execution | Local cached/pinned model, prepared UCloud job or hosted service; approvals, text scope, chunking, offsets, retention and cancellation must be designed independently. No model/default/threshold selected here. |
| Metadata/timing release | Which identifiers, aggregates, timestamps, speaker attributes and methods evidence may be shared; default text-only trial omits them. |
| Institutional acceptance | CBS researcher/IT/DPO decisions and representative Danish evaluation are separate from synthetic software checks. No claim of anonymity or real NVivo compatibility follows. |

## Acceptance and next gate

| Gate | Evidence required |
| --- | --- |
| Synthetic exactness | Emoji before a hit, decomposed Danish diacritics, CRLF/leading LF, repeated literals, word-edge negatives, multiple passages and input immutability. Literal offsets/quotes must round-trip; unchanged characters remain exact. |
| Review/authority | Keep/Replace/manual additions; all suggestions decided; wrong quote/range/category, stale payload/version/role revision, missing speaker review and overlapping replacements refuse. Production later adds stale replies, decision CAS, all-occurrence membership and writer/idempotency interleavings. |
| Export leak probes | Seed distinct canaries in original/alternate text, words, speaker IDs/names, filenames/titles/paths, notes, history and receipt fields. Serialize every allowed demo field and prove forbidden keys/canaries are absent. Kept wording with no other approved replacement remains visible, demonstrating the limit of review. |
| Resource/cancel, later | Fixed source/rule/candidate budgets, bounded hostile-input parsing, cancellable rules/worker and whole-source review status. Refusal never appears as “no identifiers found.” The small example is not a public parser or a resource qualification. |
| Independent review | Review source/hash authority, Unicode coordinate conversion, replacement conflicts, alias/export allowlists, source/active/Working preservation and the undecided key choices before any production implementation. |

Run the disposable demonstration with the existing Python environment:

```powershell
python docs/examples/anonymisation_demo.py
python -m pytest tests/test_anonymisation_design_example.py -q -o addopts=''
```

The tests establish only this synthetic contract. Next deliverable is a reviewed design and
researcher choice of the open policies, then a numbered persistence/provenance decision and
implementation plan. T88's evidence/review foundations are reusable; shared AI execution,
qualitative coding, QDPX/NVivo validation and actual research-interview evaluation remain
their own roadmap work. No change to SPEC/TODO acceptance status is implied by this proposal.
