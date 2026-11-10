# Danish model licence and evaluation evidence — 27 October 2026

This is a bounded evidence update to [the 24 October review](danish-asr-models.md), not a new accuracy ranking. Seven public model revisions were verified through Hugging Face's model metadata API; only small metadata, model cards and public licence/notice text were fetched. No weights, gated files, private data, inference or paid APIs were used.

The machine-readable [candidate manifest](../research/model-evaluation-candidates.json) freezes full commits and evidence links. It is evaluation input only: it does not change the app's catalog, enabled models or defaults. Every candidate remains `pending_cbs_review`, with runtime `validated: false`. A model licence does not establish rights in training data, base weights, code, tools or recordings, or constitute CBS legal clearance.

| Candidate | Verified weight-licence evidence | Important evaluation qualification |
| --- | --- | --- |
| Edda v0.2 large | Card explicitly declares Apache-2.0. [Pinned card](https://huggingface.co/danish-foundation-models/edda-v0.2-large/blob/796bf033ce51d03bfe793844fa3412e72997b826/README.md). | Lists FLEURS **train**, alongside other training splits. Authors did not test CTranslate2 conversion. |
| Edda v0.2 turbo | Same explicit declaration, independently pinned. [Pinned card](https://huggingface.co/danish-foundation-models/edda-v0.2/blob/698f8d04b5970f963d31c4079c05a76fa7f4f5bb/README.md). | FLEURS train exposure; Danish specialization does not verify English/code-switching. |
| Røst v3 | Metadata says `openrail`; the card instead links the specific custom Alvenir licence. [Pinned card](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b/blob/7182cced29631ef7d7776eb8c01f24624b9b9b04/README.md). | CoRal v3 fine-tuning is documented; FLEURS inclusion/exclusion is not established. |
| Hviske v5.3 | CC-BY-NC-4.0 declaration. [Pinned card](https://huggingface.co/syvai/hviske-v5.3/blob/5d1a09822018702dc51d763e3a867b62d26b3501/README.md). | **New caveat:** publisher used 10% of each CoRal test split for checkpoint selection. The complete test splits are therefore not untouched model-selection holdouts. |
| Hviske v6 | Own weights CC-BY-NC-4.0; bundled Apache/MIT notices concern ancestry/code. [Pinned card](https://huggingface.co/syvai/hviske-v6/blob/323b5ffdf54ef22196340a0b37cb11e668fefc5a/README.md), [NOTICE](https://huggingface.co/syvai/hviske-v6/blob/323b5ffdf54ef22196340a0b37cb11e668fefc5a/NOTICE). | FLEURS appears in training mix. Publisher reports excluding five benchmark test sets by transcript hash; this was not independently audited. |
| Whisper large-v3 | HF metadata says Apache-2.0, whereas upstream explicitly describes code **and weights** as MIT. [Pinned HF card](https://huggingface.co/openai/whisper-large-v3/blob/06f233fe06e710322aca913c1bc4249a0d71fce1/README.md), [upstream statement](https://github.com/openai/whisper/blob/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/README.md#license). | Keep the declaration discrepancy visible until artifact-specific review resolves it. Existing Systran conversion is a separate artifact. |
| Parakeet TDT 0.6B v3 | CC-BY-4.0 declaration. [Pinned card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/blob/541d1f99c6b0c3cd0b11a95167540bb8edefd82b/README.md). | Danish/English included among 25 languages. Training names Granary/NeMo ASR Set 3.0; FLEURS is named for evaluation, not proven excluded from all training stages. |

## Exact Røst licence resolution

The v3 card's `Alvenir/coral-1-whisper-large/blob/main/LICENSE` link resolves to `CoRal-project/roest-v1-whisper-1.5b`. The [public licence snapshot](https://huggingface.co/CoRal-project/roest-v1-whisper-1.5b/blob/a6c1e24d9f10e6289607a1ba32341b68e8660688/LICENSE) was downloaded and hashed: 16,554 bytes, SHA-256 `f575b6361ff69b52388967f69219f0cc7f9ae91f96482e5ad038261b2728799e`.

It contains custom identity-inference and impersonation restrictions, downstream conditions, and Danish dispute provisions alongside OpenRAIL-M terms. Review must consider the intended speaker-label workflow; this document does not conclude that diarization violates the licence. The snapshot establishes the text currently linked by the pinned card, not which version governed an earlier release.

## Licence hashes and limits

`license.sha256` is the SHA-256 of verified full licence bytes, not the model weights or model card. It is therefore populated only for the linked Røst text. A null value means no corresponding full governing text was independently hashed; it does not mean no licence applies. Other declarations link pinned cards, preserving exactly what was verified.

For comparison, the [upstream Whisper MIT licence](https://github.com/openai/whisper/blob/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/LICENSE) is 1,063 bytes with SHA-256 `b5d65a59060e68c4ff940e1eddfa6f94b2d68fdf58ed7f4dd57721c997e35e9d`. That hash is recorded as separate evidence rather than misrepresenting the HF declaration. Hviske v6's NOTICE is not a grant of Apache permission for its own noncommercial weights.

## Holdouts and adoption gates

The app's five existing Danish fixtures come from a [pinned FLEURS **test** repack](https://huggingface.co/datasets/FluidInference/fleurs-full/blob/1cca811bb8ea4d370345f108f00518167040282c/README.md). Training on FLEURS train demonstrates dataset-family exposure; it does **not** show that these test clips leaked. Conversely, an absent mention does not prove exclusion. The manifest's `documented_training` means the card names FLEURS training data, while `not_established` retains uncertainty. Neither value independently audits exact clip identity or ancestor models.

Before adoption, require:

1. CBS review of the exact weight licence, attribution/notices, distribution and research/commercial-use scope. In particular, clarify funded/company-linked use for NC models and Røst's custom restrictions and externally linked version.
2. Separate base-weight, training-data and executable-code review. Hviske custom/remote code needs pinned inspection before execution; a conversion needs its own source, revision and notices.
3. An authorized, held-out Danish/English interview set, with code-switching and difficult speaker/acoustic cases, identified independently of training or checkpoint selection. Keep the five public read-speech clips as a smoke test.
4. Reproducible measurement of text accuracy, usable timing, speakers, latency and memory for the exact runtime/artifact. No publisher score is presented as a CBS benchmark, and no runtime compatibility or memory claim was validated in this review.

No candidate is enabled or cleared by this document. Legal clearance, real runtime acceptance and research-data governance remain separate decisions.
