# Danish speech recognition models for UCloud (research, 24 October 2026)

Follow-up: the [27 October licence and holdout review](danish-model-licence-review-2026-10-27.md)
records pinned licence evidence and evaluation-data caveats. Its
[candidate manifest](../research/model-evaluation-candidates.json) is research input only;
CBS clearance, runtime validation and comparative measurements remain open.

Desk research for the TODO roadmap "Danish speech models". Revisions, licences and file
hashes were read from the Hugging Face API. All other facts come from model cards and the
public Open Danish ASR Leaderboard. Nothing was downloaded or run. All numbers are claims
from publishers or the leaderboard, not measurements on CBS interviews. "Est." marks our own
estimate.

## Summary

- **Your figures check out.** The model cards report CoRal v3 CER of 11.35 / 3.63 % for
  Hviske v5.3 (beam 5, "strict" normalisation), 11.6 / 4.5 % for Røst, and 27.5 / 10.1 % for
  Whisper large-v3 (conversation / read-aloud). The cards do not state the normalisation for the
  Røst and large-v3 numbers.
- **Røst "v2" has been renamed.** It is now `CoRal-project/roest-v3-whisper-1.5b`, and the old
  repo name redirects to it. The commit is the same `7182cce` that `catalogs/speech_models.json`
  already pins.
- **New since your notes.** **Edda v0.2** (Alexandra Institute / Danish Foundation Models,
  October 2026) is a set of plain Whisper fine-tunes under **Apache-2.0**. It leads the open
  Whisper-family models on the leaderboard and is the easiest drop-in for our WhisperX
  runtime. **Hviske v6** (September 2026) replaces v5.3 and scores better.
- **Not competitive on conversational Danish:** NVIDIA Parakeet, Canary, and NB-Whisper.
- **Recommended order:**
  1. Edda v0.2 large and turbo, plus Røst v3, as pinned faster-whisper models.
  2. A Hviske engine (v6 or v5.3) after the non-commercial licence is cleared.
  3. Score everything against large-v3 on reviewed CBS clips.

## Candidates

| Model | Repo @ commit (date) | Architecture, parameters | Licence / gating | Inference stack | Timestamps | Languages |
| --- | --- | --- | --- | --- | --- | --- |
| Whisper large-v3 (baseline) | `openai/whisper-large-v3@06f233f` (2024-08-12); in use: `Systran/faster-whisper-large-v3@edaa852` | Whisper encoder-decoder, 1.543 B | MIT (OpenAI). HF metadata says Apache-2.0. Not gated. | transformers; faster-whisper (in use) | Segments; words through WhisperX alignment | Multilingual. The app fixes one language per job. |
| Røst v3 Whisper 1.5B | `CoRal-project/roest-v3-whisper-1.5b@7182cce` (2026-06-19) | Full fine-tune of large-v3 on CoRal v3, 1.543 B | Custom licence adapted from OpenRAIL-M; the licence text is in another repo (Alvenir ApS). Commercial use allowed, with use restrictions (see below). Not gated. | transformers. Third-party CT2 build: `pluttodk/roest-v3-whisper-1.5b-ct2@4ddeb1a` | Not documented | Danish |
| Edda v0.2 large | `danish-foundation-models/edda-v0.2-large@796bf03` (2026-10-09) | Full fine-tune of large-v3 on about 2,600 h (FTSpeech, CoRal v3, NST, FLEURS, CV17), 1.543 B | Apache-2.0. Not gated. | transformers. The authors have not tested CT2. | Not documented | Danish |
| Edda v0.2 (turbo) | `danish-foundation-models/edda-v0.2@698f8d0` (2026-10-09) | Fine-tune of large-v3-turbo, 0.809 B | Apache-2.0 | as above | Not documented | Danish |
| Edda v0.2 duo | `danish-foundation-models/edda-v0.2-duo@fb0de9b` (2026-10-10) | Large and turbo decoded together in one beam search, 2.352 B | Apache-2.0 | transformers with `trust_remote_code`. Not faster-whisper. | Not documented | Danish |
| Hviske v5.3 | `syvai/hviske-v5.3@5d1a098` (2026-08-14) | Conformer encoder-decoder (`cohere_asr`), 2.066 B. Fine-tuned from hviske-v5.1, which comes from `CohereLabs/cohere-transcribe-03-2026`. | **CC BY-NC 4.0** since 2 May 2026. Commercial use needs a licence from syv.ai. Not gated. | transformers 4.57.6 + `trust_remote_code`; vLLM 0.19 | **None.** The base model has no timestamps; vLLM rejects `verbose_json`. | Danish |
| Hviske v6 | `syvai/hviske-v6@323b5ff` (2026-09-27) | Whisper-turbo encoder + Qwen3-0.6B decoder, 1.24 B | **CC BY-NC 4.0**, with NOTICE (derived from Apache-2.0 saga-2-m, Qwen3 and Whisper). Not gated. | transformers + `trust_remote_code`; vLLM plugin | **None** (stated) | Danish only. The card warns that other languages "will not degrade gracefully". |
| Brage v1 (deferred) | `Harmonium/brage-v1@f8eb64d` (2026-10-06) | large-v3 fine-tune, 1.55 B. Optional Munin-7B + 4-gram rescoring. | Gated. Custom licence: Apache-2.0 plus CoRal restrictions. | transformers ≥ 5.17 + custom decoder | Not documented | Danish |

Full commits (HF API, 24 October 2026):

```text
CoRal-project/roest-v3-whisper-1.5b       7182cced29631ef7d7776eb8c01f24624b9b9b04
danish-foundation-models/edda-v0.2-large  796bf033ce51d03bfe793844fa3412e72997b826
danish-foundation-models/edda-v0.2        698f8d04b5970f963d31c4079c05a76fa7f4f5bb
danish-foundation-models/edda-v0.2-duo    fb0de9b0fd76386b2f475dee12eb65da25994060
syvai/hviske-v5.3                         5d1a09822018702dc51d763e3a867b62d26b3501
syvai/hviske-v6                           323b5ffdf54ef22196340a0b37cb11e668fefc5a
Harmonium/brage-v1                        f8eb64d83e6f32090a272a369b3f5930b326289f
```

Every Danish fine-tune was trained on the CoRal v3 *train* split. None of the cards documents
English or code-switched audio. The Cohere base of Hviske v5.3 does not list Danish among its
languages. Its card warns of "inconsistent performance on code-switched audio" and says the
model is eager to transcribe non-speech, so it recommends a VAD.

## Benchmarks and GPU fit

The CoRal CER columns come from the model cards. The other columns come from the leaderboard
results dataset at commit `16c7646`. The leaderboard runs one harness on five test sets with the
same normalisation for every model: NFKC, lowercase, punctuation removed, numbers canonicalised
and Danish fillers removed. Its speed figures are on an A100 with each model's own stack.

| Model | Card CoRal CER conv / read | LB CoRal conv WER / CER | LB read CER | LB FLEURS-da WER | LB mean WER (5 sets) | Weights → GPU fit on a 23 GB MIG slice |
| --- | --- | --- | --- | --- | --- | --- |
| Whisper large-v3 | 27.5 / 10.1 | 48.57 / 36.86 | 10.48 | 12.36 | 23.63 | 3.1 GB; peak about 4.5 GB (docs/ucloud-mig.md) → fits |
| Røst v3 Whisper 1.5B | 11.6 / 4.5 | 19.57 / 11.68 | 4.68 | 9.48 | 13.42 | Same architecture as large-v3 → fits (est.) |
| Edda v0.2 large | 9.20 / 3.51 | 15.68 / 9.12 | 3.46 | 6.79 | 8.45 | Same as large-v3 → fits (est.) |
| Edda v0.2 (turbo) | 8.93 / 3.64 | 15.28 / 8.84 | 3.58 | 6.99 | 8.58 | 1.6 GB; about 2.5 GB like turbo → fits (est.) |
| Edda v0.2 duo | 8.28 / 3.14 | 14.22 / 8.21 | 3.10 | 5.98 | 7.73 | About 5 GB of weights (card) → fits (est.) |
| Hviske v5.3 | 11.35 / 3.63 | 19.18 / 11.33 | 3.76 | 9.80 | 14.06 | 4.1 GB bf16 + activations → fits (est.) |
| Hviske v6 | 10.63 / 4.91 | 18.00 / 10.53 | 4.83 | 7.73 | 10.14 | 2.8 GB bf16 → fits (est.) |
| Brage v1 (+Munin) | n/a | 15.88 / 9.30 | 3.26 | 5.65 | 8.13 | About 15 GB with Munin (card) → fits, but tight |

Leaderboard speeds: Hviske v5.3 153×, v6 67×, Edda turbo 34×, Edda large 14×, Røst 14×,
large-v3 11× real time.

Large-v3 scores 27.5 % conversation CER on the cards and 36.86 % on the leaderboard. Decoding and
normalisation alone move results this much, so only our own harness can rank the models for CBS
interviews.

The fine-tunes saw CoRal and FTSpeech training data, so they have an in-domain advantage on
those test sets; large-v3 did not. Models screened out on leaderboard conversation WER:

- Parakeet TDT 0.6B v3: 50.19
- Canary-1b-v2: 46.13
- parakeet-rnnt-110m-da-dk: 50.02
- large-v3-turbo: 63.04
- Qwen3-ASR-1.7B: 41.91

NB-Whisper covers only Norwegian and English. Cohere Transcribe does not cover Danish. saga-2-m
is gated and has been CC BY-NC since 24 September 2026; its encoder is already part of
Hviske v6.

## Licence findings that need review

- **Hviske v5.3.** The same weights (`model.safetensors` sha256 `452fcffc…56dc`) were first
  published with an Apache-2.0 card ("inherited from the base model") between 28 April and
  2 May 2026, then relicensed to CC BY-NC 4.0. A commit two minutes later removed the
  references to the upstream Cohere model from the card. Do not rely on the earlier Apache card
  without legal advice. Treat the model as non-commercial.
- **Røst v3.** The licence restricts voice imitation (4a) and use "to detect or infer aspects
  and/or features of an identity" of persons (4b). Derivatives must carry the restrictions, so
  any CT2 conversion we host does too. The licensor may restrict use remotely (clause 7), and
  disputes go to arbitration in Aarhus. The repo has no LICENSE file of its own.
- **Edda.** Edda is released as Apache-2.0 but was trained on CoRal v3. CoRal v3 is a gated
  dataset under OpenRAIL-D, which forbids speech synthesis and biometric identification. Brage
  was trained on the same data and chose to inherit the CoRal restrictions; Edda states only
  that "the corpora carry their own licences".
- **Personal data.** No licence addresses GDPR. All candidates run inside our own UCloud job, so
  no audio goes to a model publisher. Downloading a gated repo shares the downloader's Hugging
  Face identity with its publisher.

## Integration into the app

**Whisper family (Edda large and turbo, Røst v3; later Brage without Munin).** These would be
added as more pinned faster-whisper models, exactly like ADR 0053:

1. **Convert once.** Convert the pinned HF revision once with the runtime's CTranslate2 4.8.3:
   `ct2-transformers-converter --model <snapshot> --copy_files tokenizer.json
   preprocessor_config.json --quantization float16`. Copying `preprocessor_config.json` keeps the
   128 mel bins. The worker refuses a model without `tokenizer.json`.
2. **Host and pin the conversion.** Record SHA-256 hashes for every output file. Host the
   conversion where the runtime can fetch it by commit, for example a CBS-owned HF repo that
   carries the upstream LICENSE/NOTICE. Prefer this to the third-party `pluttodk` build; use
   that build only as a cross-check.
3. **Register it.** Add a `MODELS`/`WHISPER_MODELS` entry in `enhancement_worker.py` and a
   catalog profile such as `whisperx/edda-v0.2-large` with `"engine": "whisperx"` in
   `provider_models.json`. Add the snapshot to explicit preparation (`fetch_models`).
4. **Restrict the language.** Allow only Danish (`da`) for these profiles; refuse English jobs.
5. **Keep the current decoding.** Keep VAD, beam 5 and no previous-text conditioning. Check that
   the fine-tunes still emit usable segment timestamps. If they do not, the VAD chunks still
   give real bounds of at most 30 s, and WhisperX alignment supplies the word timing as today.

**Non-Whisper (Hviske v5.3 or v6).** A separate transcription engine, followed by the existing
alignment, speaker and merge stages:

1. **Catalog engine.** Add a new catalog `engine` value, such as `hviske`, frozen per job like
   ADR 0053. This changes job semantics, so it needs its own ADR.
2. **Segment and transcribe.** Split the audio into speech regions of at most 28–30 s with the
   bundled Silero VAD in faster-whisper. Hviske v5.3 was trained on clips of at most 31 s.
   Batch-transcribe the regions in bf16 from the pinned snapshot (`local_files_only`,
   `HF_HUB_OFFLINE=1`). Load the repository's `trust_remote_code` files only at the pinned,
   reviewed revision, or vendor them.
3. **Write passages with real bounds.** Write `transcription.json` rows with the VAD region
   bounds, which are real audio intervals. Rows without text are dropped; no timing is invented.
   Then `align_passages` (Danish wav2vec2), pyannote and `merge_output` run unchanged. Review
   hints contain only alignment scores, because the cards document no segment
   log-probabilities.
4. **Runtime.** This needs a new hash-locked runtime revision. Hviske v5.3 pins
   transformers 4.57.6, the same version as our runtime lock, but also needs `sentencepiece`,
   `librosa` and `soundfile`. Hviske v6's transformers version is unstated. Do not use vLLM
   for batch jobs, because it brings a second heavy runtime.

## Evaluation plan

1. **Reviewed CBS clips (decisive).** Prepare 10–20 clips of 2–5 minutes from at least five
   interviews, with varied speakers, dialects and rooms. Give each a verbatim reference with
   word times and speakers, following docs/reviewed-evaluation.md, and keep them outside
   Git/OneDrive.
   - Run every candidate through the same UCloud pipeline (same VAD, alignment and speakers) as
     an uncorrected provider result.
   - Export the saved-result `segments` as prediction files. Run
     `uv run python scripts/evaluate_reviewed.py --suite <dir> --prediction large-v3=<json>
     --prediction edda-large=<json> … --output <new file>`.
   - Compare WER/CER, timed-word coverage and words within 250 ms, speaker word accuracy (with
     an explicit `speaker_map`), and elapsed time.
   - Log peak GPU memory on the MIG slice separately; the report records only RSS.
   - Note that `normalize()` casefolds and strips punctuation but does not remove fillers or
     canonicalise numbers. Agree on the transcription convention first.
2. **Public Danish set.** Keep the existing FLEURS-da fixture (CC-BY-4.0, 5 clips) as a smoke
   test. Add about 50–100 clips from the CoRal v3 *conversation test* split as a hashed suite
   outside the repo, after the researcher accepts the dataset terms. Report it separately,
   because the fine-tunes are in-domain for CoRal.
3. **Code-switched English.** No public Danish–English code-switching corpus was found. Build at
   least 10 reviewed clips with English terms and passages from consented interviews, plus a
   few FLEURS-en clips. Score them separately, and check whether English words get any timing
   from the Danish wav2vec2 aligner. If the Danish-only models fail on English, keep large-v3
   for mixed interviews, or re-transcribe flagged passages (ADR 0054).
4. **Decision rule.** Adopt a model as the Danish default only if it has clearly lower WER/CER
   than large-v3 on the reviewed conversational clips, no loss of timing coverage, and
   acceptable English handling. The researcher sets the thresholds.

## Not verified

- **GPU memory and hardware.** Every GPU memory figure is an estimate. Blackwell/MIG
  compatibility of the transformers, CT2 and `trust_remote_code` stacks is untested, and so are
  the exact MIG product IDs (T73b).
- **Timestamps after fine-tuning.** It is unknown whether the Whisper fine-tunes still emit
  reliable timestamp tokens, and whether a CT2 conversion keeps their accuracy.
- **Benchmark details.** The normalisation behind the card numbers for Røst and large-v3 is not
  stated. The leaderboard result files omit model revisions and decoding settings. The
  leaderboard is run by RyeAI, which also lists its own model. Edda does not explicitly confirm
  that all benchmark test sets were excluded from training.
- **Language behaviour.** No candidate documents English or code-switched behaviour.

## Open questions

**For the researcher**

1. How much English do the interviews contain: whole interviews, passages, or single terms?
2. Should transcripts be verbatim, with fillers and restarts as in CoRal, or cleaned, as in
   FTSpeech?
3. Which interviews may be used for the reviewed suite, and does participant consent cover
   evaluation?
4. Are any projects externally funded or carried out with companies? This decides whether
   CC BY-NC is usable.

**For CBS IT, legal and the DPO**

1. Is CBS research use "non-commercial" under CC BY-NC 4.0 (Hviske)? Does the brief Apache-2.0
   card for the same weights matter?
2. Is the CoRal licence (Røst, Brage) acceptable? This covers clause 4(b) on identity inference
   (relevant next to speaker labelling), flow-down when we host a conversion, the remote
   restriction clause, and arbitration in Aarhus.
3. Can Edda be used on its Apache-2.0 terms alone, given that it was trained on CoRal data?
4. Is running third-party `trust_remote_code` on UCloud with Protected data acceptable, and
   who reviews the code?
5. Whose Hugging Face account accepts the gated terms (Brage, the CoRal dataset)?
6. Where may converted weights be hosted: a CBS HF organisation or the UCloud project drive?
   This also connects to the open UCloud data-processing audit in TODO.

## Edda v0.2-large "hurtig" (checked 30 October 2026)

[`syvai/edda-v0.2-large-hurtig`](https://huggingface.co/syvai/edda-v0.2-large-hurtig) at
`e51b9e9bc1d9e8028f45f154473d65b96955758b` (Apache-2.0) adds a 170M-parameter DFlash drafter
(`drafter.safetensors`, 340,831,920 bytes) for speculative greedy decoding. Its
`model.safetensors` is byte-identical to the already pinned Edda v0.2-large source
(3,087,130,976 bytes, SHA-256 `889d5c42…cd478`), so transcription quality is the same model;
the card reports matching Edda's greedy output on 299/300 clips at about 2.4× the speed of
greedy transformers decoding (not beam search).

The speed-up requires running the repository's `hurtig.py` with transformers on CUDA, batch
size 1, ≤30 s clips and no timestamps. The CBS Runtime profiles run no model-repository code
and use CTranslate2/faster-whisper with WhisperX alignment, so the drafter does not apply
there. No new profile was added; the existing "UCloud Edda v0.2 large (CBS Runtime)" profile
already uses these weights. Revisit only if a CTranslate2-compatible speculative decoder or a
reviewed adapter becomes available and shows a real gain over batched faster-whisper.

## Sources

- Hviske v5.3: [model card](https://huggingface.co/syvai/hviske-v5.3), [commits](https://huggingface.co/syvai/hviske-v5.3/commits/main), [v5.1 card at 24366f1](https://huggingface.co/syvai/hviske-v5.1/blob/24366f1289323f3bd01d47ac6ffc80fe9ecace9a/README.md); Hviske v6: [card](https://huggingface.co/syvai/hviske-v6)
- Cohere Transcribe 03-2026: [card](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026)
- Røst v3 Whisper: [card](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b), [licence (Alvenir)](https://huggingface.co/Alvenir/coral-1-whisper-large/blob/main/LICENSE), [CoRal v3 dataset](https://huggingface.co/datasets/CoRal-project/coral-v3), [CT2 conversion](https://huggingface.co/pluttodk/roest-v3-whisper-1.5b-ct2)
- Edda v0.2: [large](https://huggingface.co/danish-foundation-models/edda-v0.2-large), [turbo](https://huggingface.co/danish-foundation-models/edda-v0.2), [duo](https://huggingface.co/danish-foundation-models/edda-v0.2-duo)
- Brage v1: [card](https://huggingface.co/Harmonium/brage-v1); saga-2-m: [card](https://huggingface.co/capacit-ai/saga-2-m)
- Whisper large-v3: [card](https://huggingface.co/openai/whisper-large-v3), [Systran CT2](https://huggingface.co/Systran/faster-whisper-large-v3), [faster-whisper conversion](https://github.com/SYSTRAN/faster-whisper#model-conversion)
- Open Danish ASR Leaderboard: [site](https://ryeai-danish-asr-leaderboard.static.hf.space/index.html), [results at 16c7646](https://huggingface.co/datasets/RyeAI/danish-asr-leaderboard/tree/16c76460fac6fbea3195bd89ef08fd8c37211931)
- NVIDIA: [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3), [parakeet-rnnt-110m-da-dk](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk); NB-Whisper: [NbAiLab](https://huggingface.co/NbAiLab)
- UCloud: [products](https://docs.cloud.sdu.dk/guide/resources-products.html); NVIDIA [MIG profiles](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/latest/supported-mig-profiles.html)
