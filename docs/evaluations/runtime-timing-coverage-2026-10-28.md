# Runtime timing coverage investigation — 28 October 2026

Read-only investigation of eight recent saved results in the user-reported project.
The serving checkout was the isolated verified commit `c3c94e6`, schema 12. The
development Store was never opened on the real library. SQLite was opened with
`mode=ro`; raw stage JSON was read locally with a 32 MiB limit. No transcript text,
recording names, private paths, credentials or real row identifiers are included here.
No job, result, setting, remote folder or running process was changed.

## Observed coverage

Every result requested fixed Danish, large-v3 transcription, alignment and speakers.
The frozen host filtering threshold was `min_score=0.3`. Raw alignment stages were
present, including words whose score was below that threshold. Host
`enhancement.merge_output` deliberately replaces the timestamps of those words with
nulls. A passage is complete only when its entire lexical word map is timed.

| Result alias | Duration, seconds | Saved passages | Complete passages | Saved words | Individually timed words | Null word intervals |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Earlier short A | 6.84 | 1 | 1 | 12 | 12 | 0 |
| Earlier short B | 18.24 | 1 | 0 | 33 | 28 | 5 |
| Long interview A | 4526.27 | 1321 | 296 | 12539 | 8028 | 4511 |
| Reported long interview B | 4544.96 | 1175 | 230 | 11986 | 8184 | 3802 |
| Medium clip | 415.17 | 60 | 37 | 914 | 880 | 34 |
| Short C | 5.10 | 1 | 0 | 12 | 7 | 5 |
| Repeated short B | 18.24 | 1 | 0 | 33 | 28 | 5 |
| Short D | 11.34 | 1 | 0 | 23 | 20 | 3 |

The reported long interview therefore has 68.3% individually timed words and 19.6%
complete passages. The other long interview has 64.0% individually timed words and
22.4% complete passages. These are different denominators; neither measures timing
accuracy. The actual pinned checkout's `research_reports._coverage` was also run
on these saved payloads: its lexical-token denominators and timed-token counts
exactly match the word counts in this table, including 8,184/11,986 for long B.

Every inspected local transcription, alignment and diarization file matched the
size and SHA-256 in its durable `runtime_output_receipt` (24/24 stage files).

All incomplete saved passages were incomplete solely because at least one word had
null timing. Across all eight results, no retained finite interval was nonpositive,
nonmonotone, or outside its exact saved passage bounds. There was no additional
observed loss from the host's window-tolerance check versus strict passage bounds.
Saved words do not contain alignment scores: scores are deliberately stored separately
as review hints, so their absence from saved word dictionaries is not evidence loss.

## Raw stages versus the saved projection

Long interview A had 12,539 raw words: 12,527 had positive intervals, 4,499 were
below the threshold, and 12 lacked timing. Those 4,511 words exactly account for the
saved null intervals. Its raw alignment median score was 0.372.

Long interview B had 11,988 raw words: 11,985 had positive intervals, 3,801 were
below the threshold, and three lacked timing. Two low-fit punctuation-only tokens
were deliberately omitted from the saved word map; the remaining 3,802 account for
all saved null intervals. Its raw alignment median score was 0.387. Literal transcript
wording was not printed or exported by this investigation.

The medium clip had 914 positive raw intervals; 34 scores were below 0.3, exactly
matching its saved null count. The short clips show the same mechanism. A single
below-threshold word can make a whole passage incomplete even when most other words
remain usable for partial playback.

The captured model pins were the same across these results:

- ASR: `Systran/faster-whisper-large-v3`, revision
  `edaa852ec7e145841d8ffdb056a99866b5f0a478`.
- Danish alignment: `saattrupdan/wav2vec2-xls-r-300m-ftspeech`, revision
  `7a60985d596e7a9e26b01a0707fe59daac6ffe84`.
- Speakers: `pyannote/speaker-diarization-community-1`, revision
  `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`.

The saved request used `whole_recording=false`; full-ASR supplied bounded passage
times. Fixed-language evidence reported requested/effective/reported `da`, with null
configured probability. No ASR, missing-stage, or bounded-language failure explains
the observed nulls. Similar low-fit/filtering behavior was already present in older
singleton Runtime results; the symptom is not unique to shared allocation pooling.

## Threshold sensitivity, without modifying results

These counts apply the numeric policy to existing raw intervals only. Complete raw
passages precede speaker splitting and must not be compared directly with the saved
passage count above. No lower cutoff is asserted to be accurate or safe.

| Threshold | Long A usable words / complete raw passages | Long B usable words / complete raw passages | Medium usable words / complete raw passages |
| ---: | ---: | ---: | ---: |
| 0 | 12527 / 1207 | 11985 / 1083 | 914 / 59 |
| 0.05 | 11415 / 647 | 11148 / 592 | 906 / 51 |
| 0.1 | 11290 / 612 | 11045 / 560 | 901 / 47 |
| 0.2 | 9924 / 356 | 9953 / 317 | 897 / 43 |
| 0.3 | 8028 / 184 | 8184 / 140 | 880 / 36 |

[ADR 0048](../decisions/0048-review-hints.md) already records that many absolute
alignment scores fell below 0.3 and distinguishes alignment fit from correctness.
The current finding establishes the cost of the frozen threshold, not the quality
of discarded intervals. An accuracy decision needs bounded, reviewed audio/word
comparisons; lowering a threshold alone is not a demonstrated model-quality fix.

## Synthetic reproduction and verification

`tests/test_timing_coverage_diagnosis.py` uses the actual `merge_output` and report
coverage functions with three synthetic ten-word passages. Each has one positive
raw interval scored 0.299. With the explicit frozen 0.3 cutoff, zero passages are
complete while 27/30 words retain timing. Text, source inputs and passage bounds stay
exact. A separate counterfactual projection with cutoff zero retains all raw timing;
it neither changes defaults nor claims those timestamps are correct.

Command: `uv run --no-sync pytest tests/test_timing_coverage_diagnosis.py -q -o addopts=''`.
Result: **2 passed in 10.95 seconds**. This was a diagnostic reproduction of existing
behavior, not a red-to-green production fix. No inference engine or provider ran.

The immediate supported conclusion is that Runtime successfully saved the stage
outputs, but the chosen alignment model produced many low-fit words and the host
filter plus strict passage completeness made the result appear largely untimed.
No unexpected normalization loss was established in these inspected results. Keep
the raw stage evidence and frozen policy; evaluate any quality or display change
separately before applying it to saved research data.
