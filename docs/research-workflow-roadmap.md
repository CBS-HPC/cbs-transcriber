# Research provenance and reproducible workflow roadmap

Requested 21 October 2026; implementation inventory reviewed 31 October against integration
`778a5b6`. This app is a research tool. The roadmap includes delivered foundations and remaining
work; current features must not be presented as meeting every journal's methodological policies.
Research version history covers the data and process as well as the app source code in Git.

The [history and evidence guide](research-history.md) describes immutable result/notes versions,
exact frozen enhancement lineage, historical exports, metadata-first methods reports and
restoration of an earlier transcript as a new version. Explicit Working designations pin exact
versions independently of the current review view; preview-bound merge/conflict choices retain
their cited parents. These are implemented features, not remaining interface proposals.

Fresh capture-policy jobs also bind local enhancement/timing, CBS Runtime full-ASR, API and
native Transcriber observations to their actual creation/application versions. Capture is
scoped: API outputs are normalized SDK responses, native submissions are credential-free
projections, remote environments may be uncaptured and legacy gaps are never backfilled.
See [ADR 0093](decisions/0093-working-versions-and-review-evidence.md),
[ADR 0097](decisions/0097-api-provider-artifact-observations.md) and
[ADR 0100](decisions/0100-native-transcriber-artifact-observations.md).

Initial history activation on 22 October followed backup and preservation checks. Later
integration and activation have separate dated evidence in the [current worklist](../TODO.md);
this inventory does not establish which branch changes are running. The
[original design](research-provenance-design.md) remains a reference for broader goals, not
the current completion ledger.

## Versioned materials and processing lineage

The following goals extend the delivered version/capture contracts; individual missing
observations remain explicit rather than making every item a new implementation task.

- Preserve immutable recording identities/checksums and raw imported/provider artifacts.
- Add an append-only history of transcript edits, speaker changes, notes and processing steps,
  recording time, actor when known, prior version and reason. Compare/restore without deletion.
- Trace each export through recording → prepared audio/chunks → raw transcription → normalized
  result → reviewed corrections → timing/diarization → final reviewed version and export.
- Retain app commit/environment/lock identity, preparation settings, provider/model identities,
  available pinned revisions, language, chunk offsets, thresholds, execution location and outcomes.
  Report opaque/unavailable provider version information explicitly; never invent it.
- Distinguish automatic recognition, manual correction, alignment and diarization from verified
  speaker identification. Record anonymization, inaudible speech, code switching, exclusions,
  partial timing and methodological conventions affecting interpretation.
- Keep recordings, identifying metadata and audit records in managed storage; never place private
  research materials into Git fixtures or upload them automatically.

## Workflow report and evidence bundle

Export a researcher-reviewable workflow description and machine-readable manifest tied to exact
versions, inputs, settings, quality checks, human changes, limitations and output hashes. Record
which steps can rerun offline. Preserve observed API responses because repeat requests or
stochastic inference may differ; explain limits to exact regeneration.

Current research reports default to metadata/checksums for exact Working versions, report
unassigned recordings as skipped and include selected transcript text only by explicit choice.
An explicit-version API selection retains its named scope. Reports do not include media or
unselected historical text. Metadata and hashes may still be linkable; a methods report is not
an anonymisation or complete-backup format.

Broader de-identified sharing and private-mapping storage need a separate accepted design;
see the [synthetic anonymisation proposal](proposals/anonymisation-local-prototype-2026-10-30.md).
Document access/retention and relevant model/dataset licences without claiming clearance.

Research the selected journal's current author/data/methods instructions and institutional rules
before designing report templates. Researchers must inspect and amend descriptions and explain
restricted/unavailable materials. No generic export should be labelled universally journal compliant.

## Acceptance work

1. Extend the bounded synthetic preservation scenario through fresh admission and complete
   portable capture/isolated restoration. Check exact Working payloads, immutable history,
   source bytes, disconnected providers and inert restored jobs together. Existing component
   tests and the legacy-producer/database-inventory scenario establish narrower scopes.
2. Trace chosen transcript/export content through the preserved versions and available producer
   observations, without paid calls or mutable endpoints. Verify hashes and missing-evidence
   flags; do not imply reconstruction of an unobserved earlier step.
3. Retain current migration, stale-edit, notes/role revision, conflict, privacy and cancellation
   regressions in the final integrated checks. Synthetic checks are distinct from filesystem
   power-loss behavior, live cloud transfer/restore and model-quality acceptance.
4. Keep redaction, key custody, deletion/withdrawal, retention and approved storage decisions
   separate from checks that existing captured bytes and references agree.
5. Review reports with researchers against selected journal instructions and representative
   Danish/English interviews, separately from automated software tests.

Complete processing-environment coverage, opaque provider internals, historical unknowns,
larger-scale history/report handling and researcher acceptance remain open. The existing
restore-as-new-version, Working/conflict interfaces and scoped producer capture should be
extended only for an evidenced gap, not reimplemented as unfinished work.
