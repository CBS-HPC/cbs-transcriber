# Anonymisation: offline bilingual evaluation gate

10 November 2026. Scope: design and synthetic Python probes only, outside the application.
No private interviews/libraries, weights, downloaded models, inference, external APIs or cloud
jobs were used. The [30 October survey](anonymisation-models-2026-10-30.md) remains dated desk
research. Its model, dependency and licence claims have not been revalidated here.

## Reproducible fixture baseline

Run `python -m docs.examples.anonymisation_evaluation` from the repository root. The
[standard-library example](../examples/anonymisation_evaluation.py) assembles six fictional
DA/EN cases and exact gold code-point spans, then calls the existing email/known-literal demo.
All addresses use `.invalid`; fictional names/places are fixture text. It reads no files or
library and prints counts/categories only, with no quotes, source hashes or entity keys.

| Fixed fixture observation | Danish | English |
| --- | --- | --- |
| Cases | 3 | 3 |
| Gold mentions | 7 | 5 |
| Exact detected boundaries | 3 | 3 |
| Missed gold mentions | 4 | 2 |
| False suggestions under this fixture's policy | 0 | 1 |
| Entity clusters with at least one miss | 3 of 4 | 1 of 2 |
| Cases with at least one missed gold mention | 2 of 3 | 1 of 3 |

These are deliberately selected gap probes, not estimates of Danish/English accuracy. The
Danish cases demonstrate literal names/emails, a missed genitive, places and a contextual
rare-role phrase. English demonstrates repeated emails, lowercased/misspelled names and one
template email excluded by the fixture's annotation policy. That last suggestion illustrates
review burden; it does not prove an email recogniser is wrong. All detected boundaries are
deduplicated and matched exactly, ignoring detector labels; this is detection coverage, not
category-classification performance. No model or automatic entity linking was evaluated.

The separate [review example](../examples/anonymisation_demo.py) tests explicit same-entity
labels, different people with equal spelling and a manually linked spelling variant. Those
assignments are programmed decisions, not completed human review. Unit tests check that
private mappings/source canaries remain out of its text-only JSON and that stale sources,
unresolved overlaps and missing decisions refuse. Neither script validates phone/CPR/IBAN,
address or spoken-number rules, generalisation quality, whole-source detection, resource
limits, a real archive/export adapter or release suitability.

## Annotation policy before comparing local candidates

Use the [identifier-scope table and open decisions](../proposals/anonymisation-local-prototype-2026-10-30.md#identifier-scope-to-decide)
to choose names/public-person exceptions, direct versus contextual identifiers, granularity
and intended cross-recording linkage. Annotate frozen exact transcript versions with half-open
code-point spans, category, human-assigned entity cluster and the release policy revision.
Keep ambiguous cases explicitly unresolved; a model score cannot adjudicate them.

For a later approved local evaluation, use separate DA, EN and mixed-language strata. Split by
interview/entity so a known-term list or threshold tuning cannot use held-out names. Evaluate
with and without a researcher-provided known-term list, recording its origin and revision;
gold annotations must never silently populate it. Double-review a sample and every disputed
boundary/entity link before freezing the test set. Private text, quotes, names, mapping keys
and per-interview identifiers stay out of Git and public reports. Counts/tables themselves
need a disclosure decision for small real cohorts.

| Slice required later | What it resolves |
| --- | --- |
| Names/initials/nicknames, first-name-only, Danish genitive, case and decomposed Unicode | Literal coverage, word edges and human variant membership without guessing identity. |
| ASR misspellings, punctuation loss, lowercasing, hesitation/repetition and code switching | Accuracy on actual transcript form rather than clean news text. |
| Contact details, ID/account/secret candidates and numbers spoken as words | Pattern coverage and contextual exceptions; malformed values still need annotation. Use synthetic non-live examples for rule tests. |
| Organisations, addresses/places, dates, rare roles/events and combined facts | Gaps beyond person-name NER; scope-dependent manual review requirements. |
| Long passages, adjacent/overlapping entities, chunk edges and cross-passage references | No truncation; reversible source coordinates; complete mention membership. Cross-passage facts may require review even when spans stay within a passage. |
| Negative/public/template mentions chosen by the policy | False suggestions and unnecessary redaction, separately from detection misses. |

The survey's suggested corpus sizes are planning estimates. Set real sampling targets from
available approved material, category rarity and desired uncertainty; report actual category
denominators. Do not pool DA and EN into a single headline score that hides a weak language.

## Local model trial, separately authorised

Compare rules only, rules plus known terms and each pinned local candidate separately before
their union. The dated ScandiNER/DaCy/GLiNER shortlist supplies hypotheses; it does not select
an English model or establish runtime compatibility. Recheck exact revision, tokenizer,
configuration/weight hashes, licence, language/category coverage and dependency isolation
before obtaining or loading anything. No app-environment sync or new dependency is needed
for the current scripts.

For an actual offline candidate run, use already approved/pinned local artifacts, disable
network access for the process, refuse remote code and record load failures distinctly.
`local_files_only` alone is not proof that no dependency contacts a service. Pin chunking,
overlap, label mapping, thresholds and coordinate conversion. Capture the full-run manifest,
source scope and completion/refusal state; invalid offsets, resource refusal or an interrupted
run cannot produce “no identifiers found.” Keep outputs local. Measure Windows/Apple CPU
load time, warm throughput, peak memory and review effort rather than deriving them from a
model's parameter count. Nothing here authorises a download, installation or model run.

Report exact per-category precision/recall/F2 and character coverage, plus overlap scores as
diagnostics. Use one-to-one mention matching; do not count one broad span as detecting several
gold entities. A partially covered identifier remains a leak for the leak metric. Count missed
mentions, clusters with any missed identifying character, recordings with any direct-identifier
leak, false suggestions per 1,000 words, incorrect entity merges/splits and inconsistent
replacement labels. Report denominators and uncertainty, especially sparse categories.
Threshold tuning uses validation data only. No fixed recall number makes the release safe.

## Next decision and acceptance boundary

The next user decisions are intended release/identifier scope, desired interview linkage and
label style, mapping custody/retention/backup handling, and inactive derived-result publication
with later-edit invalidation. The local prototype proposes conservative options but persists
none of them. Resolve those before collecting private evaluation material or implementing
storage/routes/UI. A separately reviewed architecture decision must define authority, atomic
publication and export eligibility before production work.

Future export acceptance must exercise every selected format/ZIP manifest/path, reviewed
speaker names, replacement text and source/alternate/history/notes/metadata canaries. Missing
reviewed derived versions must skip with a reason, never fall back to an ordinary transcript.
The current experiment exports plain text-only JSON with a fixed method marker; it does not
test NVivo/CSV/Word/HTML adapters or safely releasing audio. Text redaction does not anonymise
voices, original media, metadata or retained source copies. Synthetic passes establish no
research-data anonymity, institutional approval or real NVivo acceptance.
