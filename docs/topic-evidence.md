# Topics & sentiment prototype

This feature records human topic/evaluation coding and reviews externally
supplied suggestions. It does not run a sentiment model, discover topics or make an accuracy
claim. No audio, text or credentials are sent to a provider. Timing and speaker enhancement
remain separate operations.

## Codebook, evidence and review

Open **Topics & sentiment…** for a saved result. Define a project codebook with stable topic
IDs, names and definitions. Inclusion rules, exclusion rules and examples can make the coding
conventions explicit. Approve the codebook to start a run; approval records researcher review,
not institutional certification. Later changes create another codebook version. Keep old IDs
and retire obsolete topics rather than silently deleting their definitions.

A run binds one exact saved transcript version, its SHA-256 hash, one approved codebook version
and its hash, and an explicit passage scope. Opening a run never activates or rewrites a
transcript. If its source or codebook changes, the earlier run remains bound to its original
evidence and is marked as older.

Each annotation codes one topic/evaluation pair. Add several annotations for different topics
in the same passage. Choose positive, negative, mixed, neutral or uncertain, and record whether
the opinion is the speaker's own, a reported opinion or unclear. **Uncertain** requires an
explanation. Multi-speaker evidence requires unclear attribution in this first prototype.
Uncoded passages establish neither topic absence nor exhaustive processing.

Evidence must quote the frozen passage exactly at explicit half-open Unicode code-point
offsets. Repeated wording needs the intended occurrence. Emoji and combining characters remain
unchanged; offsets do not count JavaScript UTF-16 units. The server rejects invented quotations,
wrong offsets, unsupported topics and unknown fields. Evidence navigation uses only actual
source passage intervals; untimed text remains available without inventing timing.

Manual annotations start accepted. Imported suggestions start unreviewed. Accept, Change and
Reject append review events; original suggestions stay immutable. Accept after a correction
retains the latest corrected interpretation. Review revisions prevent one window overwriting
another, and request IDs make identical retries return the committed operation. Reusing a
request ID for different content fails. Rejection does not erase the preceding evidence.

## Importing suggestions

The local JSON API accepts this versioned envelope at
`POST /api/recordings/{recording_id}/annotation-runs/import`:

```json
{
  "request_id": "unique-import-request",
  "bundle": {
    "format": "cbs-topic-suggestions-v1",
    "result_id": "saved-result-id",
    "source_version_id": "exact-source-version-id",
    "source_payload_sha256": "64-lowercase-hex-characters",
    "codebook_version_id": "exact-codebook-version-id",
    "codebook_payload_sha256": "64-lowercase-hex-characters",
    "passage_ids": ["0"],
    "suggestions": [
      {
        "id": "suggestion-1",
        "annotation": {
          "topic_id": "flexibility",
          "polarity": "positive",
          "attribution": "self",
          "evidence": [
            {
              "passage_id": "0",
              "start_char": 0,
              "end_char": 20,
              "exact_text": "I like the flexible "
            }
          ]
        }
      }
    ],
    "producer": {"model": "declared-model", "revision": "declared-revision"}
  }
}
```

IDs, hashes, text and offsets above are placeholders: use the exact local version metadata and
literal passage content. The optional producer declaration is stored as unverified; it is not
evidence that the app ran that model. A speech-provider configuration does not authorize a
future text-analysis service. Invalid items reject the whole import without partial insertion.
The HTTP request is limited to 2 MiB and 1,000 suggestions. Duplicate JSON keys, nonfinite
numbers and extra fields are rejected. Other limits include 100 topics, 1,000 selected passages
and 32 evidence spans per annotation; oversized sources are explicitly rejected. A run contains
at most 1,000 annotations. The prototype dialog starts from a whole saved version; for sources
above the passage limit, the local API supports an explicit smaller `passage_ids` scope.

## Export and storage

Download **Export reviewed evidence** to preserve current reviewed evidence, definitions,
review states and exact source/codebook lineage. It contains research text by design. It excludes
unselected transcript passages, hidden original text, notes, credentials, runtime paths and
raw producer declarations, unreviewed proposal text and earlier review payloads. Rejected items
include their last reviewed evidence; unreviewed items include metadata and an explicit state.
Original proposals and earlier reviews remain available locally through exact item/history reads.
This export is
not an NVivo coding import and does not replace transcript exports or the methods report.

Schema 7 adds codebook versions, annotation runs, immutable items and append-only review
events. Existing schema-6 libraries are backed up before transactional migration; prior
transcripts, history, notes, jobs and settings are preserved. Only temporary synthetic
libraries are used in automated verification; the existing library was also backed up and
upgraded with prior-table preservation checks on 22 October 2026. Explicit project deletion
cascades its annotation history under the app's existing remote-job deletion guards.

Automatic local/UCloud/API inference, topic discovery, aggregate prevalence/maps, validated
Danish/English quality and integration into the methods-report bundle remain later work.
See [ADR 0033](decisions/0033-topic-evidence-prototype.md) and the
[research/design](sentiment-topic-mapping-design.md) for rationale and evaluation requirements.
