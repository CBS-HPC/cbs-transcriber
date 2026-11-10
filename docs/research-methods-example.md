# Synthetic research workflow and methods example

This is the weekend plan's researcher-facing report example. It uses existing Store/history,
Working and research-report writers on a new disposable library. All text, names, model labels
and times are synthetic. It exercises software behavior, not research quality or journal
acceptance. No audio is provided, no worker/model runs and no existing library is read.

With an existing development environment and a new output directory:

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe docs/examples/research_methods_demo.py test-results/research-methods-example
```

The parent directory must exist. Existing output entries are refused. Remove any inherited
`CBS_TRANSCRIBER_MODELS` override first; the example refuses it before application imports.
The imported Store must come from the same checkout. No installation, environment sync,
library registration, server, external API or model download is involved.

## Read the example

Start with the generated `START-HERE.md`, then compare:

| Artifact | What to inspect |
| --- | --- |
| `oracle.json` | Six workflow observations with action/sequence/payload hashes, expected literal visible wording, frozen source/Working/current hashes and explicit mock/capture limits. No raw internal IDs or hidden original text. |
| `metadata-report.zip` | Genuine default `manifest.json` and `methods.md`, with no transcript text. The report follows the pinned Working version and reports one unassigned recording as skipped. |
| `selected-text-report.zip` | The same default Working scope with explicit text inclusion. Its transcript preserves the chosen Danish/English wording, internal CRLF, emoji, distinct combining/precomposed accents and named synthetic voices. |
| `example-manifest.json` | Byte counts/hashes of generated files and relevant builder/writer source hashes. Written last as the completion marker; a missing marker means incomplete output. Hashes are integrity references, not a signature or acceptance grant. |

The scenario imports exact JSON bytes, freely corrects wording, names voices and freezes that
source version. A mock enhancement is inserted through the existing frozen-result Store seam.
Its sequence-one version is explicitly marked Working. Later edits change both the original
source and the active enhancement head. The reports still choose the pinned earlier version;
neither later rewrite enters their selected text or frozen ancestry. This demonstrates the
difference between reviewing a current result and choosing a research version.

In each ZIP, start at the version where `selected=true`. Match its `payload_sha256` and
`source_version` to the oracle, then follow report-scoped `predecessor` aliases. The selected
enhancement has sequence 1; its frozen source has sequence 3 (`speaker_rename`), preceded by
the free-text correction and import. Later source/active rewrites remain in the disposable
library but outside this selected lineage. Reports are generated without modifying those
saved versions. The disposable database and raw import are removed, not distributed.

## Interpret model evidence and missing history

The declared `synthetic-model-not-executed` is observed source metadata. The example supplies
no producer capture policy, worker stages, real environment/model pin or inference output.
It therefore leaves producer artifacts uncaptured and preserves report provenance gaps.
An exact source version/hash proves which stored wording was supplied to this mock creation;
it does not prove that a model executed, produced accurate timing or identified a person.
Import-byte receipts do not reveal an ASR environment before import or establish current file
availability. Passage bounds are fixture values; this exercise measures no audio alignment.

Review and amend the actual exported `methods.md` for a researcher's chosen workflow. No
automatic anonymity, verified authorship, provider reproducibility, institutional approval or
universal journal compliance follows from it. Metadata/hashes can be linkable. The two report
ZIPs exclude seeded project/recording names, notes, review reasons, hidden original wording
and unselected later text; this is a scoped privacy regression, not an anonymisation feature.

Regeneration uses fresh IDs/observation times, so ZIP bytes need not repeat. Compare scope,
literal text, version relationships and the new recorded hashes. The completion marker says
only that this builder's bounded checks succeeded. This is not a complete backup/restore,
portable package, live provider, NVivo, model-quality or device qualification.

See [Research history](research-history.md) and the
[remaining workflow roadmap](research-workflow-roadmap.md) for the implemented contracts and
the acceptance work that remains separate.
