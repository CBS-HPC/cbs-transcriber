# Anonymisation models and tools for Danish transcripts — evaluation, 30 October 2026

Status: desk research only (web sources read on 30 October 2026). Nothing was installed, run or
measured. Scores are the publishers' own (self-reported) unless stated otherwise. It complements
[the anonymisation proposal](../proposals/anonymisation-2026-10-30.md), which already plans local
rules, a known-names list and PII-Tracer on a UCloud MIG slice.

App constraints checked in this worktree: Python 3.13, CPU `torch` 2.8.0 and `transformers` 4.57.6
are locked in `uv.lock` (WhisperX 3.8.6). Any candidate that needs another `transformers` major
version, `spacy-transformers` or `trust_remote_code` cannot share the app `.venv` as it is.

## Short answer

1. **No dedicated, evaluated Danish PII model was found.** The PII-specific models (PII-Tracer,
   OpenAI Privacy Filter, GLiNER-PII variants, ai4privacy, Piiranha, OpenMed, LiquidAI) do not list
   Danish or publish no Danish numbers. Danish evidence exists only for general NER (person,
   organisation, location and, for DaCy fine-grained, 18 OntoNotes types).
2. **Best local Danish detector for the app now: ScandiNER**
   (`saattrupdan/nbailab-base-ner-scandi`, MIT, ~0.2B BERT). It has the strongest published
   DaNE result, runs through the plain `transformers` pipeline already in the lock, and needs no
   remote code. It covers only PER/ORG/LOC/MISC, so Danish rules must cover the rest.
3. **Best published evidence on conversational Danish: DaCy fine-grained** (Apache-2.0). It cannot
   share the app environment, because `spacy-transformers` pins `transformers<4.53.3`. Use it in
   an isolated evaluation environment first.
4. **Practical combination:** Danish rules + project known-names list + ScandiNER locally (works for
   Protected projects without upload). Add PII-Tracer on UCloud as an optional second pass for
   approved projects. Treat GLiNER-X and an instruction LLM as evaluation candidates, not defaults.
5. **Every model needs a Danish evaluation** on reviewed transcripts before its thresholds are
   trusted. Review stays mandatory.

## Candidates

"Local CPU in app" means: runs on CPU in Python, without `trust_remote_code`, and without changing
the locked `transformers` 4.57.6.

### Danish and Scandinavian NER

| Candidate | Licence | Danish evidence | Entities | Size | Local CPU in app |
|---|---|---|---|---|---|
| [ScandiNER `saattrupdan/nbailab-base-ner-scandi`][scandiner] | MIT | DaNE micro-F1 87.44 ± 0.81 (card); DaNE test PER 95.53, ORG 82.58, LOC 88.32, micro 89.25 ([DaNLP table][danlp-ner]). DANSK cross-domain avg 0.64, Conversational 0.66 ([DANSK paper][dansk], Table 8) | PER, ORG, LOC, MISC | ~0.2B (676 MB) | **Yes.** `BertForTokenClassification`, 512 positions, no `auto_map` |
| [DaCy fine-grained `chcaa/da_dacy_{small,medium,large}_ner_fine_grained`][dacy-fg] | Apache-2.0 | DANSK macro F1 0.82 (large), Conversation 0.80; PER 0.86, ORG 0.79, LOC 0.93 ([DANSK paper][dansk], Tables 6 and 9; trained on DANSK, so in-distribution) | 18 OntoNotes types incl. PERSON, ORGANIZATION, GPE, LOCATION, FACILITY, DATE, TIME, NORP, EVENT, MONEY | 22M / 278M / 355M | **No.** [`dacy` 2.9.0][dacy-pypi] needs `spacy[transformers]`; [`spacy-transformers` 1.4.0][spacy-trf] pins `transformers<4.53.3`. Models declare spaCy `>=3.5,<3.6` |
| DaCy `da_dacy_*_trf` 0.2.0 ([GitHub][dacy-gh]) | Apache-2.0 | DANSK Conversational 0.74 (large); DaNE micro 86.89 for v0.0.0 large | PER, ORG, LOC, MISC + parser etc. | small–large | No (same `spacy-transformers` pin) |
| [spaCy `da_core_news_lg`][spacy-da] | CC BY-SA 4.0 | NER F 0.81 (card); DANSK Conversational 0.72, cross-domain avg 0.49 | PER, ORG, LOC, MISC | small CNN | Not in the lock (would add spaCy); ~420 sentences/s ([DaNLP][danlp-ner]) |
| [`alexandrainst/da-ner-base`][da-ner-base] (DaNLP BERT) | Apache-2.0 (older card: CC BY-SA 4.0) | DaNE micro 84.04 ([DaNLP][danlp-ner]) | PER, ORG, LOC, MISC | BERT-base | Yes, but weaker than ScandiNER; [DaNLP is archived][danlp-gh] (March 2025) |
| [DaAnonymization (`textprivacy`)][daanon] | Apache-2.0 | None published | DaCy NER + regex CPR, phone, email; "Person 1" pseudonyms; prior-knowledge `individuals` map | — | No: 0.1.1 from 2022, pre-alpha, unpinned DaCy. Reuse ideas only |

Notes:
- DANSK covers seven domains including spoken conversation transcripts ([paper][dansk]). It is the
  only published Danish NER evidence near our text type. The dataset page was not accessible
  (HTTP 401), so its access conditions and licence are **unverified**.
- DaCy 2.8.0 (August 2026) added Python 3.13 support ([releases][dacy-rel]). Whether the
  fine-grained models (built for spaCy 3.5) load under a current spaCy is **unverified**.
- CPU speed: ScandiNER 4.16 samples/s vs DaCy large 0.65 (card; hardware not stated). DaNLP
  measured on an M1 CPU: ScandiNER ~5 sentences/s, DaCy large ~1, DaCy small ~44. A 1-hour
  interview (~10k words, roughly 600–800 sentences, our estimate) is therefore ~2–3 minutes for
  ScandiNER and ~10+ minutes for DaCy large. These are estimates, not measurements.

### Multilingual PII models

| Candidate | Licence | Danish | Entities | Size / context | Local CPU in app |
|---|---|---|---|---|---|
| [PII-Tracer][pii-tracer] / [vLLM packaging][pii-tracer-vllm] | MIT | Not listed. [PII-TRACE paper][pii-trace] covers 13 languages but names only en/it/ru/ko; no Danish result | person, email, phone, address, URL, date, account number, secret, other_pii (+ sensitivity) | 0.6B, 4096 tokens | **No** locally (`trust_remote_code`, `transformers>=5.2`). The UCloud deployment in the proposal avoids repository code |
| [OpenAI Privacy Filter `openai/privacy-filter`][opf-hf] ([repo][opf-gh], [model card][opf-paper]) | Apache-2.0 | Not evaluated. Reported languages: nl/en/fr/de/it/es + 13 non-European. "Primarily English" | 8: account_number, address, email, person, phone, URL, date, secret | 1.5B total / 50M active, 128k context | **No** in app: native class only from [`transformers` 5.6.0][tf-560]; separate `opf` CLI supports `--device cpu` |
| [GLiNER-X `knowledgator/gliner-x-{small,base,large}`][gliner-x] | Apache-2.0 | `da_ddt` F1 0.765 / 0.764 / **0.866** (vs 0.644 for `gliner_multi-v2.1`); benchmark labels not stated | Zero-shot, any labels (Danish label names work in examples) | 0.3B / 0.5B / 0.9B (mT5-large encoder), `max_len` 1024 | Probably yes: [`gliner` 0.2.29][gliner-pypi] needs `transformers>=4.51.3,<5.17`. Its config uses the `stanza` word splitter (extra dependency; possible model download — **unverified**) |
| [`urchade/gliner_multi_pii-v1`][gliner-multi-pii] | Apache-2.0 | None. Fine-tune data covers en/fr/it + 2 ([dataset][gliner-pii-data]) | 60+ PII types, zero-shot | mDeBERTa-v3-base, `max_len` 384 | Probably yes (same `gliner` range) |
| [`knowledgator/gliner-pii-{edge,small,base,large}-v1.0`][gliner-pii] | Apache-2.0 | None. Backbones are **English** DeBERTa-v3 ([base][gliner-pii-base-cfg], [large][gliner-pii-large-cfg]) | 60+ PII types | F1 75.5–83.25 on a synthetic set | Yes, but Danish is unlikely to work well |
| [`knowledgator/gliner-stream-pii-v1.0`][gliner-stream] | Apache-2.0 | None. Multilingual masking F2 68.21% (EN 90.53%); weakest on multilingual legal text | Open labels | Qwen3-0.6B backbone | Unverified |
| [GLiNER2-PII (Fastino)][gliner2-pii] | Unverified | None: 7 languages (en, fr, es, de, it, pt, nl) per a [third-party mirror][gliner2-mirror] | 42 PII types | 205M | Separate `gliner2` package; unverified |
| [OpenMed `privacy-filter-multilingual(-v2)`][openmed] | Apache-2.0 | None: 16 languages, no Danish | OPF-style | — | Unverified |
| [LiquidAI `LFM2.5-Encoder-350M-PII-Detector`][lfm] | Unverified | None in the listed languages | 40 PII types | 350M | Unverified |
| [ai4privacy `llama-ai4privacy-multilingual-categorical-anonymiser-openpii`][ai4p] | MIT | None: 8 languages, no Danish | 20 types (names, IDs, address parts, dates) | 0.1B ModernBERT-base | Yes, but no Danish training data |
| [Piiranha `iiiorg/piiranha-v1-detect-personal-information`][piiranha] | **CC BY-NC-ND 4.0** (earlier MIT) | None: 6 languages | 17 types | mDeBERTa-v3-base, 256 tokens | Excluded: no Danish, non-commercial/no-derivatives |

Independent evaluations say the same thing about language transfer. A [cross-lingual OPF
evaluation][opf-xling] finds it good on structured PII but weak on person names and addresses,
recall-biased, and worse outside its training distribution. [REDACT][redact] (25 languages) reports
Presidio recall of 0.07 on high-sensitivity categories, while GPT-4.1 and Claude Sonnet 4.6 are more
robust. In PII-TRACE, PII-Tracer has the best character F1 (0.629; recall 0.830, precision 0.507),
ahead of OPF (0.492), GLiNER-PII (0.392) and Presidio (0.086). A single 4096-token pass loses
recall on long text, and 50% overlap windows restore it ([paper][pii-trace]).

### Frameworks and LLM approaches

- **[Presidio][presidio-ent]** (MIT; moved from Microsoft to the
  [Data Privacy Stack organisation][presidio-move]); `presidio-analyzer` 2.2.366 supports Python
  3.11–3.14 and has `gliner` and `langextract` (LLM) extras ([PyPI][presidio-pypi]). There is **no
  Danish recognizer** (no CPR; Finland and Sweden only). Danish needs a custom NLP engine plus
  recognizers with Danish context words ([languages][presidio-lang]). It is useful as a pattern
  library, but the app does not need it as a dependency.
- **[OS2datascanner][os2ds]** (MPL-2.0, used by Danish municipalities) has field-tested Danish rules
  ([decision history][os2ds-dec]). Its CPR rule checks date and century validity and exempts
  dates from modulus 11. It also uses a "cpr" context whitelist and document blacklists. Its name
  rule uses a Danish name list, and its address rule needs a Danish street-name list. The
  [`os2ds-rules`][os2ds-rules] wheels (0.1.0, 2023) cover CPython 3.9–3.11 only, with no macOS build.
  Reuse the design, not the package.
- **Instruction LLM on UCloud (vLLM).** Example: [`utter-project/EuroLLM-22B-Instruct-2512`][eurollm]
  (Apache-2.0, Danish listed explicitly, 22.6B, 32k context, `vllm serve` documented). No Danish
  PII evaluation exists. At bf16 it needs roughly 45 GB (our estimate), so it does not fit the
  validated 1g.23gb MIG slice without quantisation or a full GPU. Hybrid regex + LLM confirmation
  improved multilingual PII precision in [RECAP][recap] (relative gains on its own baselines).
  LLMs are best suited to suggest indirect identifiers (job titles, rare events, small places),
  which token classifiers do not model. Their output must be verified against literal spans.

## Danish specifics (rules layer)

- **CPR:** `DDMMYY-SSSS`. The 7th digit and the year give the century. Numbers issued before
  1 October 2007 satisfy modulus 11 ([cpr.dk][cpr-structure]). Valid numbers **without** the check
  digit have been issued since 2007 ([cpr.dk][cpr-nomod]), so never require modulus 11. Instead,
  validate the date, accept a space, hyphen or nothing as separator, and raise confidence for
  context words ("cpr", "personnummer"). Only DaAnonymization and OS2datascanner handle CPR.
- **Phone:** Danish subscriber numbers have 8 digits in series 2–9, with country code 45
  ([BEK 1883/2020][dk-numbering]). In transcripts, digits may be grouped in pairs or spoken as words.
- **Bank:** A Danish IBAN has 18 characters (DK + 2 check digits + 4-digit bank code + 10-digit
  account; mod-97) ([Wise][iban]). Domestic reg. + account numbers need context words (rules).
- **Addresses:** "Street number[letter], floor. side, postcode town". OS2datascanner matches them
  only against a street-name list. The DAWA API closed on 1 October 2026 (HTTP 410), and the
  replacement is the keyed Datafordeler DAR API ([OSM forum][dawa]). A bundled offline street-name
  or postcode snapshot would need its own licence check (**unverified**).
- No model found handles CPR or Danish addresses specifically. PII-Tracer's `account_number` and
  `private_address` and OPF's equivalents have no Danish evaluation.

## Transcript-specific limitations

- NER fails on ASR transcripts even without word errors, and F1 is a poor metric for conversational
  transcripts ([Szymański et al. 2023][why-ner]). Use leak-oriented metrics (below).
- Lower-cased or unpunctuated input degrades cased NER models substantially
  ([Mayhew et al. 2019][lowercase]). Whisper output is usually cased, but test a lower-cased slice.
- Misspelled names from ASR, first-name-only mentions, Danish genitive `-s` ("Mettes") and
  dialect forms defeat exact matching. Match normalised variants against the known-names list.
- Context limits: ScandiNER 512 subword tokens and PII-Tracer 4096 tokens. Chunk at passage
  boundaries with overlap and map offsets back to the exact Working text.

## Recommended combination for CBS Transcriber

1. **P1 local default (offline, Protected-safe):** Danish rules (CPR as above, phone, email, URL,
   DK IBAN, CVR and account numbers with context, Danish date expressions, postcode + town) plus
   the per-project known-names list with inflection/variant matching. Add **ScandiNER** for
   PER/ORG/LOC. This adds no dependency: run it in the existing enhancement worker style, with
   download-free cached weights and pinned revision. Pseudonym consistency comes from the
   project entity table, not from the model.
2. **P2 optional cloud pass:** PII-Tracer on UCloud, as already proposed, for approved projects.
   Merge its suggestions with the local ones; show the source and confidence for each suggestion.
3. **Evaluation-only candidates:** (a) GLiNER-X base/large with Danish labels such as
   "arbejdsplads" or "uddannelsesinstitution" for categories beyond PER/ORG/LOC; (b) DaCy
   fine-grained in an isolated environment. Adopt either only through a separate worker
   interpreter if it clearly improves recall; (c) an instruction LLM for indirect identifiers.
4. **Not recommended now:** Piiranha (licence, no Danish), ai4privacy and knowledgator
   `gliner-pii` (no Danish, English backbones), OPF in-app (needs `transformers` 5.6, no Danish
   evidence), DaAnonymization and `os2ds-rules` as dependencies (stale or no wheels).

## What a small Danish evaluation should measure

Data and annotation:
- 6–10 reviewed Danish interviews plus 1–2 English, covering different speakers, dialects and ASR
  quality: roughly 50k words. Aim for at least ~200 person mentions and ~50 per other category.
  These sizes are our suggestion, not a standard.
- Annotate with a written guideline: direct identifiers (names, contact details, IDs,
  addresses) vs quasi-identifiers (workplace, job title, small places, event dates, rare health
  details); and a policy for public figures and interviewer names. Double-annotate ~20% and report
  inter-annotator span agreement.
- Store gold spans as character offsets on the exact Working version, plus entity clusters
  (same real-world entity). Real text stays in the local library; commit only aggregate numbers.
  Repository tests keep using synthetic fixtures.

Metrics:
- Per category: recall, precision, F1 and **F2**, with exact and overlap span matching.
- **Leak rate:** share of entity clusters with at least one missed mention, and transcripts with any
  leaked direct identifier. This is the primary safety number.
- Character-level recall of identifying text.
- **Review burden:** false suggestions per 1,000 words, and reviewer minutes per audio hour.
- **Pseudonym consistency:** share of a cluster's mentions mapped to the same replacement,
  including inflected and first-name-only forms; also count wrong merges.
- Slices: ASR-misspelled names, lower-cased/unpunctuated ablation, numbers spoken as words,
  English or code-switched passages, mentions at chunk boundaries.
- Calibration: precision at recall ≥ 0.95 per category, used to pick thresholds.
- Cost: CPU time and peak memory per 1-hour interview on Windows x64 and Apple Silicon; UCloud
  wall time per job.
- Rules: unit tests with synthetic CPR numbers (including valid non-modulus-11 numbers and invalid
  dates), phone groupings and IBANs.

Systems to compare: rules only; + known names; + ScandiNER; + GLiNER-X; + PII-Tracer; their union;
and, evaluation-only, DaCy fine-grained, OPF and one LLM. Public sanity checks for PER/ORG/LOC:
DaNE (CC BY-SA 4.0); DANSK conversation domain if access allows.

## Unverified or open

- Danish accuracy of every PII-specific model (none published), and PII-TRACE's full language list.
- Whether GLiNER-X downloads stanza resources at load time, and its CPU speed.
- Whether DaCy fine-grained models load under current spaCy, and DANSK access and licence.
- GLiNER2-PII and LiquidAI licences and exact model IDs; OpenMed v2 language list.
- Licence of an offline Danish street-name or postcode snapshot from Datafordeler.
- All CPU timings above are derived estimates; measure them in the evaluation.

[scandiner]: https://huggingface.co/saattrupdan/nbailab-base-ner-scandi
[danlp-ner]: https://danlp-alexandra.readthedocs.io/en/latest/docs/tasks/ner.html
[danlp-gh]: https://github.com/alexandrainst/danlp
[dansk]: https://arxiv.org/html/2402.18209v1
[dacy-fg]: https://huggingface.co/chcaa/da_dacy_large_ner_fine_grained
[dacy-pypi]: https://pypi.org/project/dacy/
[dacy-gh]: https://github.com/centre-for-humanities-computing/DaCy
[dacy-rel]: https://github.com/centre-for-humanities-computing/DaCy/releases
[spacy-trf]: https://pypi.org/project/spacy-transformers/
[spacy-da]: https://huggingface.co/spacy/da_core_news_lg
[da-ner-base]: https://huggingface.co/alexandrainst/da-ner-base
[daanon]: https://github.com/martincjespersen/DaAnonymization
[pii-tracer]: https://huggingface.co/perplexity-ai/PII-Tracer
[pii-tracer-vllm]: https://huggingface.co/perplexity-ai/PII-Tracer-vLLM
[pii-trace]: https://arxiv.org/html/2609.22200
[opf-hf]: https://huggingface.co/openai/privacy-filter
[opf-gh]: https://github.com/openai/privacy-filter
[opf-paper]: https://arxiv.org/html/2608.18274
[tf-560]: https://newreleases.io/project/pypi/transformers/release/5.6.0
[opf-xling]: https://arxiv.org/abs/2608.02616
[redact]: https://arxiv.org/abs/2606.19881
[gliner-x]: https://huggingface.co/knowledgator/gliner-x-base
[gliner-pypi]: https://pypi.org/project/gliner/
[gliner-multi-pii]: https://huggingface.co/urchade/gliner_multi_pii-v1
[gliner-pii-data]: https://huggingface.co/datasets/urchade/synthetic-pii-ner-mistral-v1
[gliner-pii]: https://huggingface.co/knowledgator/gliner-pii-base-v1.0
[gliner-pii-base-cfg]: https://huggingface.co/knowledgator/gliner-pii-base-v1.0/blob/main/gliner_config.json
[gliner-pii-large-cfg]: https://huggingface.co/knowledgator/gliner-pii-large-v1.0/blob/main/gliner_config.json
[gliner-stream]: https://huggingface.co/knowledgator/gliner-stream-pii-v1.0
[gliner2-pii]: https://arxiv.org/abs/2605.09973
[gliner2-mirror]: https://huggingface.co/SemplificaAI/gliner2-privacy-filter-PII-multi
[openmed]: https://huggingface.co/OpenMed/privacy-filter-multilingual/blob/main/README.md
[lfm]: https://huggingface.co/LiquidAI/LFM2.5-Encoder-350M-PII-Detector
[ai4p]: https://huggingface.co/ai4privacy/llama-ai4privacy-multilingual-categorical-anonymiser-openpii
[piiranha]: https://huggingface.co/iiiorg/piiranha-v1-detect-personal-information
[presidio-ent]: https://presidio.dataprivacystack.org/supported_entities/
[presidio-lang]: https://presidio.dataprivacystack.org/analyzer/languages/
[presidio-move]: https://raw.githubusercontent.com/data-privacy-stack/presidio/main/docs/project_transition.md
[presidio-pypi]: https://pypi.org/project/presidio-analyzer/
[os2ds]: https://github.com/magenta-aps/osdatascanner
[os2ds-dec]: https://os2datascanner.readthedocs.io/decision-history/
[os2ds-rules]: https://pypi.org/project/os2ds-rules/
[eurollm]: https://huggingface.co/utter-project/EuroLLM-22B-Instruct-2512
[recap]: https://arxiv.org/html/2510.07551v1
[cpr-structure]: https://www.cpr.dk/cpr-systemet/opbygning-af-cpr-nummeret
[cpr-nomod]: https://www.cpr.dk/cpr-systemet/personnumre-uden-kontrolciffer-modulus-11-kontrol
[dk-numbering]: https://www.retsinformation.dk/eli/lta/2020/1883
[iban]: https://wise.com/dk/iban/denmark
[dawa]: https://community.openstreetmap.org/t/dawa-adresse-api-lukker-den-1-oktober-2026-hvad-med-autoaws/148055
[why-ner]: https://aclanthology.org/2023.acl-long.98/
[lowercase]: https://aclanthology.org/D19-1650/
