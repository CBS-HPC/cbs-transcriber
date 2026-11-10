# Evaluating reviewed Danish and English clips

`scripts/evaluate_reviewed.py` compares saved predictions entirely offline. It makes no API
requests, allocates no jobs and downloads no data/models. Use the small public FLEURS suite
for a workflow check; meaningful speaker/timing evaluation needs reviewed interview clips.
Keep private clips, annotations and predictions outside Git/OneDrive in your chosen library.

## Reference suite

Use a folder with audio files and `manifest.json`, following the existing FLEURS manifest:

```json
{
  "dataset": "Reviewed interview clips",
  "revision": "review-1",
  "evidence": "Describe reviewers, recording conditions and provenance",
  "clips": [
    {
      "id": "danish-01",
      "file": "danish-01.wav",
      "sha256": "REPLACE_WITH_THE_ACTUAL_64_CHARACTER_AUDIO_HASH",
      "language": "da",
      "duration_seconds": 3,
      "text": "Hej København.",
      "reference_words": [
        {"word": "Hej", "start": 0.2, "end": 0.8, "speaker": "R1"},
        {"word": "København.", "start": 1, "end": 2, "speaker": "R2"}
      ]
    }
  ]
}
```

The example words/times are invented to illustrate the format; they are not ground truth.
Provide reviewed annotations for your actual audio. IDs/filenames must be unique/basenames;
the scorer streams and verifies the audio hashes before comparing any method. Languages are
`da`/`en` (regional forms such as `da_dk` are accepted). Record the reference clip duration.
Each side is limited to 2000 annotated words / 12,000 characters per clip to bound the lexical
traceback. Split long interviews into reviewed clips with recording-relative offsets adjusted
to those clips, retaining provenance. Do not infer timestamps from text length.

Reference words are optional: text-only references can score WER/CER but cannot establish
word timing or speaker accuracy. A missing word interval is unknown. Full annotation text must
match the reference after the documented Unicode normalization; reference starts stay ordered.

## Predictions and method comparison

Each prediction file maps exactly the reference clip IDs to model output:

```json
{
  "danish-01": {
    "text": "Hej København.",
    "words": [
      {"word": "Hej", "start": 0.25, "end": 0.85, "speaker": "A"},
      {"word": "København.", "start": 1.1, "end": 2.1, "speaker": "B"}
    ],
    "speaker_map": {"A": "R1", "B": "R2"},
    "measurements": {
      "source": "Describe how this run was measured; include hardware/runtime",
      "elapsed_seconds": 1.5,
      "time_scope": "inference only, excluding queue/preparation/model download",
      "peak_rss_bytes": 1000000,
      "memory_scope": "worker process peak RSS"
    }
  }
}
```

These measurements are illustrative, not observed performance. Omit unmeasured fields.
`words` may be omitted for text-only models; missing intervals stay null. A word list must
cover the complete prediction, including words without timing, so omissions cannot be hidden.
Speaker mapping is supplied explicitly by the reviewer; identical strings are not assumed to
identify the same person. Unmapped IDs count as incorrect when a mapping is supplied.

Instead of `text`/`words`, a prediction can contain the app's saved-result `segments` array.
The converter respects its word offsets/timing validity, keeps speaker IDs, and flags recorded
review edits. Free-text corrections cannot recover obsolete word intervals. Choose uncorrected
provider results for ASR accuracy comparisons; an enhanced result can include prior human review.
The report's edit flag reflects supplied saved-result metadata, not an independent provenance check.

Run from the repository, using actual paths outside the checkout:

```powershell
uv run python scripts/evaluate_reviewed.py --suite C:\Evaluation\reviewed-clips `
  --prediction 'OpenAI whisper-1=C:\Evaluation\whisper.json' `
  --prediction 'UCloud large-v3=C:\Evaluation\large-v3.json' `
  --output C:\Evaluation\reports\comparison-01.json
```

Output must be a new file; existing reports are never overwritten. Reports retain numeric
per-clip metrics, language/corpus totals and measurement provenance, excluding transcript bodies.
The scorer does not send private data anywhere. Treat supplied IDs/provenance as private too.

## Interpreting the report

- **WER/CER:** existing NFC/casefold/punctuation/whitespace normalization. Corpus rates use
  summed edit counts divided by summed reference counts, not an average of clip percentages.
- **Word timing:** exact lexical sequence alignment, independent of timestamps. Only matched
  words with actual reference and prediction intervals contribute boundary errors. Read coverage
  alongside mean/median/p90 boundary error and the fraction with both boundaries within 250 ms.
  Missing/substituted words reduce coverage. Repeated-word traceback ties are disclosed; review
  those cases because more than one minimum lexical alignment can exist.
- **Speaker words:** correctly attributed, lexically matched words divided by all speaker-
  annotated reference words in clips with explicit mappings. Deletions/substitutions count
  against accuracy. Mapping coverage identifies unscored clips. This is not continuous-region
  diarization error rate (DER); it does not evaluate overlap, non-speech or collars. See the
  [pyannote metric reference](https://pyannote.github.io/pyannote-metrics/reference.html#diarization).
- **Performance:** supplied elapsed time / declared clip duration is real-time factor (lower is
  faster). Peak RSS is a reported process-memory quantity, not GPU memory or a model sizing
  recommendation. The scorer does not measure these values itself. Aggregates disclose coverage;
  different time/memory scopes are not silently combined into one ratio/peak.

Record model/revision, response capability, language, runtime/hardware, chunking/concurrency,
warm/cold cache and exactly which stages were timed. Compare Danish and English separately and
include multiple speakers, accents, background noise, longer omissions and real CBS recording
conditions. Synthetic metric tests and short FLEURS clips do not establish representative model
rankings. Actual reviewed interview benchmarks remain pending until fixtures/runtimes are available.
