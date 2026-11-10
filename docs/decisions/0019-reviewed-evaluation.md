# 0019 — Reviewed timing and speaker evaluation

Accepted, 20 October 2026. Extends the existing hash-pinned FLEURS WER/CER evaluator.

Use the same offline clip manifest, with optional reviewed word intervals/speaker IDs and Danish/
English language labels. Predictions carry text, optional complete word annotations and optional
explicit speaker-ID mapping. No model calls or dataset downloads occur during scoring. Stream
audio hashes and bound annotation sequence alignment to 2000 words per side per clip; split long
interviews into reviewed clips. Preserve the existing Unicode WER/CER normalization.

Align annotated words using exact lexical Levenshtein traceback, independent of timing. Report
matched timing coverage, boundary error and within-250ms fraction together; no interpolation or
imputed reference intervals. Report alignment tie decisions because repeated words can have
multiple equally minimal lexical alignments. This metric is not forced-alignment ground truth.

Speaker attribution uses a reviewer-supplied mapping from predicted IDs to reference IDs.
Count correctly attributed, lexically matched words against all speaker-annotated reference
words, so omissions cannot improve the score. Report coverage and missing mappings. Do not call
this DER: it does not score continuous speech regions, overlap or collars. A future reviewed
diarization dataset can support a separately specified DER metric.
See [pyannote's metric definitions](https://pyannote.github.io/pyannote-metrics/reference.html#diarization)
for continuous-region diarization evaluation; no pyannote metric dependency is added here.

Elapsed time/peak RSS supplied with a prediction are explicitly reported measurements, with
provenance and scope; missing measurements stay null. Compute real-time factor from elapsed
time and actual clip duration. Never claim synthetic or mocked results establish model quality,
measured memory requirements, or representative interview accuracy.

Verification uses synthetic Danish/English text/timing/speakers, omissions and repeated-word
ties, missing data, invalid annotations/mappings/measurements, corpus weighting, hash corruption
and offline CLI output. Real reviewed interview baselines remain separate external acceptance.
