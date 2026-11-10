# 0048 — Model review hints beside results

Accepted 22 October 2026.

Researchers want to see which passages and words a model was less sure about, so proofreading
can start there. Providers already return such signals, but import kept only text, times and
speakers.

## Signals kept

| Source | Passage | Word |
| --- | --- | --- |
| UCloud Transcriber (Whisper + WhisperX) | `avg_logprob`, `no_speech_prob` | alignment `score` |
| OpenAI whisper-1 (`verbose_json`) | `avg_logprob`, `no_speech_prob`, `compression_ratio` | — |
| OpenAI gpt-4o-(mini-)transcribe (`json`) | — | token log-probabilities, requested with `include=["logprobs"]` and mapped onto words through exact UTF-8 byte offsets (least likely token wins) |
| Local/UCloud WhisperX enhancement | — | alignment `score`, sliced with split fragments |

## Storage and validity

Schema 10 adds `result_hints(result_id, hints, created)`, with a schema-9 backup. Hints are
captured with each new result in the same transaction, keyed by passage ID together with the
SHA-256 of the passage text they describe. They never enter passages, version payloads,
history, reports or exports. Results created earlier are rebuilt once, on first view, from
their preserved raw responses or enhancement outputs, and only when every rebuilt passage
equals the result's first saved version; otherwise "unavailable" is cached. Passage repairs
and imports are not rebuilt.

The API returns only hints whose passage still has the exact text, so any edit removes that
passage's marks; the response counts edited passages.

## Display

Signals are compared within one transcript, because absolute values were uninformative on real
data (Whisper's -1.0 average log-probability trigger flagged nothing; 40% of alignment scores
were below 0.3). The editor marks, by rank, at most the chosen share (5/10/20%, default 10%) of
passages and of words, and only those below a ceiling (average log-probability -0.25; word
fit/probability 0.5). Whisper's own warnings (no-speech > 0.6 with log-probability < -1;
compression ratio > 2.4) always flag. Alignment-only results flag passages where half the words
are marked. Labels say which signal applies and that these are model signals, not calibrated
correctness: alignment fit is not transcription accuracy. "Next to review" moves through flags.

Comparing two models' wording (the existing comparison view) remains the complementary check.
