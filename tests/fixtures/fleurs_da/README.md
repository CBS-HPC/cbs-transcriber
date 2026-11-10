# Danish FLEURS reference subset

Five unchanged WAV files and their exact matching reference texts from
[FluidInference/fleurs-full](https://huggingface.co/datasets/FluidInference/fleurs-full),
revision `1cca811bb8ea4d370345f108f00518167040282c`, retrieved 20 October 2026.
The mirror identifies these as the Danish portion of the
[Google FLEURS](https://huggingface.co/datasets/google/fleurs) test split.
Selection: the first five file IDs, `da_dk_0000` through `da_dk_0004`.
Total: 63.96 seconds, approximately 2 MB, mono 16 kHz WAV.

`manifest.json` contains the reference text, original URLs, durations, per-audio SHA-256
hashes, and the hash of the full upstream transcription file. The transcription file's
leading file ID was separated from its text; no transcript correction was applied.
No full dataset download or network access is needed for routine tests.

## Attribution and license

FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech.
Alexis Conneau, Min Ma, Simran Khanuja, Yu Zhang, Vera Axelrod, Siddharth Dalmia,
Jason Riber, Clara Talber, and Ankur Bapna (2022).
[Paper](https://arxiv.org/abs/2205.12446).
WAV extraction and file naming: FluidInference's `fleurs-full` mirror.

The dataset is distributed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
These public test assets retain that license, independently of the application's code.
Redistribution must retain attribution and the license link and identify changes.
The audio is unmodified; this repository selects five clips and packages the text in JSON.
No endorsement by the dataset authors or CBS is implied.

## What the suite measures

This is a small text-accuracy smoke benchmark for comparing app/provider configurations.
It is read speech, with no reference word boundaries, speaker turns, video, overlap, or
interview noise. It cannot validate diarization, highlighting alignment, or representative
interview accuracy. Keep synthetic timing tests and browser acceptance checks too.

Scoring uses Levenshtein edits divided by reference length: **WER** at word level and
**CER** at character level. Rates are fractions (0 is perfect; insertions can exceed 1).
Corpus totals are summed before division, so short clips do not get disproportionate weight.
Normalization `nfc-casefold-punctuation-to-space-whitespace-v1`:

1. Unicode NFC and case folding; retain Danish `æ`, `ø`, and `å`.
2. Replace every Unicode punctuation character with a space.
3. Collapse whitespace and trim; normalize to NFC again.
4. Split on whitespace for WER; include normalized spaces for CER.

Punctuation and capitalization are therefore not scored. Hyphens and apostrophes split
words. No number expansion, accent removal, translation, or model-specific cleanup occurs.
Raw and normalized text are both retained. Compare scores only under the same policy;
these numbers need not match published benchmarks using other normalization.
Missing, extra, duplicate, or non-text predictions fail; an explicitly empty prediction
counts as deletions. No accuracy threshold is imposed before obtaining a real baseline.

References: [WER metric](https://huggingface.co/spaces/evaluate-metric/wer/blob/main/README.md),
[CER metric](https://huggingface.co/spaces/evaluate-metric/cer/blob/main/README.md).

## Running

See the [evaluation instructions](../../../README.md#reference-evaluation) for offline
scoring and opt-in transcription through the running app. Automated tests mock model output;
passing them establishes pipeline correctness, not measured recognition accuracy.
