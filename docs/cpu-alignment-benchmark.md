# Danish CPU word-alignment benchmark — 21 October 2026

This is a small performance smoke test, not an accuracy or long-recording benchmark.
It ran the application's actual WhisperX worker and `align_passages` on the public,
hash-verified FLEURS `da_dk_0002.wav` fixture, using its reference text. Speaker detection
and preliminary recognition were disabled. No interview data or paid service was used.

| Item | Measurement |
| --- | --- |
| Audio / reference | 6.84 seconds / 12 words |
| CPU | Intel Core i7-8665U, 4 cores / 8 logical processors |
| System RAM | Approximately 16 GB; other applications were running |
| Runtime | Python 3.13, WhisperX 3.8.6, PyTorch 2.8.0+cpu; 4 Torch threads |
| First cold library imports | 75.392 seconds |
| First model/tokenizer setup | 31.328 seconds |
| Model load | 0.832 seconds |
| First worker execution, including model load | 12.161 seconds |
| Three in-process warm alignment passes | 8.358, 7.242, 7.372 seconds |
| Warm median / audio duration | 7.372 seconds / 1.078 times real time |
| Words receiving intervals | 12 of 12 |

The full measurement process, including setup and three extra warm passes, took
141.864 seconds. Warm passes reused the loaded model but called real inference again;
alignment output checkpoints were not reused. Tests/browser checks overlapped parts
of this run, so these numbers describe observed development-machine performance.

The pinned Danish model is
[`saattrupdan/wav2vec2-xls-r-300m-ftspeech`](https://huggingface.co/saattrupdan/wav2vec2-xls-r-300m-ftspeech),
revision `7a60985d596e7a9e26b01a0707fe59daac6ffe84`. Setup downloaded its safetensors
weights (approximately 1.26 GB), JSON configuration/tokenizer files and NLTK `punkt_tab`.
Inference then used cached files offline. No speaker model was downloaded.
Fixture provenance, license and hashes are in `tests/fixtures/fleurs_da/manifest.json`.

This fixture has no reference word intervals: 12/12 coverage does not establish timing
accuracy. Long or untimed passages may require an extra recognition pass, and speaker
detection has a different cost. Do not extrapolate this single clip to interview throughput.
The app starts a fresh subprocess for each job; the warm median excludes that startup.
First-use imports were unusually expensive immediately after installing the ML packages.
The measurements and raw alignment remain in the ignored local `test-results` folder.

## Running-app acceptance

After installing the same lockfile into the main checkout and restarting the app, the user-
requested `da_dk_0002` recording was verified against the fixture hash. It had no saved text,
so the reference was imported as an explicitly named FLEURS benchmark source. A real local
alignment job then used cached models with downloads disabled and speaker detection off.

The first full job took **187.146 seconds** from queue creation to completion and produced
12/12 timed words with positive intervals. This includes media preparation, a fresh worker,
cold imports in the newly populated main environment, model loading and inference. The main
environment is in the Windows/OneDrive checkout; this single run does not isolate why its
cold startup was slower than the isolated worktree run. It is the observed app latency,
not the warm inference median. Further warm app runs are needed before estimating throughput.

The new result was appended without changing the selected reference source or its contents.
Comparison against the pre-activation SQLite backup confirmed every unrelated row unchanged;
saved credential presence was unchanged too. Only the authorized sample's source/result/job
were added and that sample's active transcript view initialized. No private audio was processed.

## Reusable passage worker acceptance

The passage-repair implementation (`1efa8a9`) was then exercised through two real enhancement
queue jobs in a temporary library using the same public reference and cached Danish model.
The first took 129.351 seconds including a fresh worker's cold startup; the second took
7.360 seconds end to end. Both produced 12/12 timed words. The subprocess PID was unchanged,
the source/result selection remained unchanged, and app shutdown closed the retained worker.
Downloads were disabled and no existing research project was modified. Unit tests overlapped
this smoke test, so it is lifecycle/functional evidence, not a controlled throughput benchmark.
