# Licence audit for open-source publication — 30 October 2026

Scope: branch `claude/integration` at `c3cb2cc` (worktree `cbs-integration`), read-only. Evidence comes
from tracked files, `uv.lock`, `importlib.metadata` in the Windows x64 `.venv`, byte inspection of
bundled binaries, the Hugging Face model API and public licence pages. This is an engineering audit,
**not legal advice or CBS clearance**. Severity: **BLOCKER** (fix before the repository is public),
**ACTION** (fix or decide before or soon after publication), **NOTE** (record only).

10 November housekeeping: the user requested removal of the external `Transcriber_-_Python`
folder and ZIP. They are removed from the current tree; historical copies remain in Git.
The dated findings below are retained as audit history. No repository visibility change,
publication clearance or history rewrite is implied by removing the current files.

10 November publication update: inspection of the actual CBS-New-Bold.ttf nameID13 terms
confirms an express use/distribution grant to CBS and collaborators. A missing open font
licence is therefore not itself a CBS redistribution refusal; preserve its separate proprietary
notice. No project-wide open-source licence has been selected. The user approves publishing
a clean current snapshot while keeping the removed external reference bundle and old Git
history in a private repository. The dated F1/F2 blanket publication-block wording below is
historical, not a current prohibition on publishing owned source under that explicit decision.
Third-party notices, model terms and the remaining institutional/reuse-licence review still
apply. Application wheels contain the app/resources, not a bundled Python environment or
redistributed FFmpeg/model distribution.

## Summary of findings

| # | Finding | Severity |
| --- | --- | --- |
| F1 | No project licence: no LICENSE/NOTICE, no `license` in `pyproject.toml`, no file headers | BLOCKER |
| F2 | Proprietary font `CBS-New-Bold.ttf` is tracked (and in history since `f57f6b3`) | BLOCKER |
| F3 | `Transcriber_-_Python/` and `Transcriber_-_Python.zip`: unknown authorship or permission; docx metadata names individuals | BLOCKER until cleared |
| F4 | CBS logo SVGs: trademark, needs CBS Communications approval and a licence exclusion | ACTION |
| F5 | `imageio-ffmpeg` installs a GPLv3 FFmpeg build (libx264/x265/xvid); PyAV wheels bundle LGPLv3 FFmpeg | ACTION (docs); BLOCKER for any binary bundle |
| F6 | Røst v3 uses a custom Alvenir OpenRAIL-M licence; catalog says `openrail`; UI shows no terms | ACTION |
| F7 | Default Danish aligner `saattrupdan/wav2vec2-xls-r-300m-ftspeech` is `license: other` (Folketinget terms) | ACTION |
| F8 | pyannote `speaker-diarization-community-1` is CC-BY-4.0 and gated: attribution is missing | ACTION |
| F9 | Publication hygiene: a private path, commit identities, docx metadata, internal process documents | ACTION |
| F10 | Every Python dependency is permissive or weak-copyleft; there is no GPL/AGPL Python package | NOTE |
| F11 | `ucloud-workflow` 0.1.9 is MIT + CC-BY-4.0, public, and anonymously downloadable | NOTE |
| F12 | No copied third-party code, vendored JS/CSS or icon sets found | NOTE |

## 1. The project's own licence

- **F1 BLOCKER.** `git ls-files` contains no LICENSE, COPYING, NOTICE or AUTHORS file. `pyproject.toml`
  has no `license`/`license-files`, and no source file carries a licence or SPDX header.
  `cbs-transcriber 0.1.0` metadata therefore declares no licence. If published like this, the
  repository is "all rights reserved", not open source.
- The wheel target (`[tool.hatch.build.targets.wheel] packages = ["src/cbs_transcriber"]`) includes
  `static/brand/` fonts and logos, so any PyPI or wheel release would redistribute F2/F4 assets too.
  A default hatchling sdist would also include tracked reference files (F3) and fixtures.
- Compatibility of the dependencies and assets found with candidate project licences:

| Licence found | Where | Effect on project licence |
| --- | --- | --- |
| MIT, BSD-2/3, ISC, PSF-2.0, 0BSD, Zlib, CC0, MIT-CMU, CNRI-Python | most dependencies | Compatible with Apache-2.0, MIT, BSD and GPL |
| Apache-2.0 | openai, transformers, huggingface_hub, lightning, nltk, … | Compatible with Apache-2.0/MIT projects. GPLv3 is compatible; GPLv2-only is not |
| MPL-2.0 (file-level) | certifi, tqdm (MPL-2.0 AND MIT) | No effect when used unmodified as installed packages |
| LGPL-3.0+ | FFmpeg DLLs inside the PyAV `av` wheel | No effect on our source. Bundling triggers LGPL notice, source and relink duties |
| GPL-3.0+ | FFmpeg executable in the `imageio-ffmpeg` wheel | Run as a separate process (`subprocess`), so it is aggregation rather than linking. Bundling triggers GPLv3 duties |
| CC-BY-4.0 | FLEURS fixtures, pyannote weights, ucloud-workflow docs | Attribution only. Keep fixtures under CC-BY-4.0, separate from the code licence |
| CC-BY-NC-4.0 | Hviske (catalog candidate only) | Not bundled. NC limits apply only to anyone who deploys it |
| Custom (Alvenir OpenRAIL-M, Folketinget, CBS NEW) | Røst, FTSpeech aligner, CBS NEW font | Models are not bundled. The font must be removed or excluded (F2) |
| OFL-1.1 | CBS Sans (Inter) fonts | Fonts stay under OFL as separately licensed files. Allowed |

## 2. Python dependencies (`pyproject.toml`, `uv.lock`, installed metadata)

None of these are committed to the repository. Users install them from PyPI, the PyTorch CPU index
or the GitHub release through `uv sync --locked`. CBS distributes only the lock file, unless it
later ships an installer, ZIP with `.venv`, container or wheel bundle.

**Direct runtime dependencies (Windows venv metadata):**

| Package | Version | Licence (metadata or licence file) |
| --- | --- | --- |
| fastapi | 0.151.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| openai | 3.35.1 | Apache-2.0 |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause wrapper **plus bundled FFmpeg 7.1 executable, GPL-3.0+** (F5) |
| keyring | 25.7.0 | MIT |
| platformdirs | 4.13.5 | MIT |
| ucloud-workflow | 0.1.9 | `MIT AND CC-BY-4.0` (LICENSE + LICENSE-DOCS in dist-info) |
| httpx | 0.28.1 | BSD-3-Clause |
| websockets | 17.3 | BSD-3-Clause |
| whisperx | 3.8.6 | BSD-2-Clause (wheel also ships `assets/pytorch_model.bin` VAD weights; licence not separately verified) |
| faster-whisper | 1.2.1 | MIT (bundles `assets/silero_vad_v6.onnx`, Silero VAD) |
| torch / torchaudio / torchvision | 2.8.0+cpu / 2.8.0 / 0.23.0 | BSD-3-Clause / BSD / BSD (wheels carry third-party notices) |

Dev only: pytest 9.1.1 (MIT), ruff 0.17.1 (MIT), playwright 1.64.0 (Apache-2.0). Playwright downloads
Chromium for developers only. Users and the app never need it.

**Important transitive dependencies:** ctranslate2 4.8.3 (MIT; the Windows wheel bundles NVIDIA
`cudnn64_9.dll` and Intel `libiomp5md.dll`, both under their vendors' redistributable licences);
av 19.0.2 (BSD-3-Clause; `av.libs/` bundles FFmpeg DLLs, and `avutil` reports "LGPL version 3 or
later"); transformers 4.57.6, huggingface_hub 0.36.2, tokenizers 0.22.2, safetensors 0.8.0
(Apache-2.0); pyannote-audio 4.0.7 and pyannote-core/database/metrics/pipeline, pyannoteai-sdk 0.4.0
(MIT licence files; empty metadata; pyannote-audio also ships a WeSpeaker Apache-2.0 file);
lightning/pytorch-lightning 2.6.6 (Apache-2.0); onnxruntime 1.31.0 (MIT); nltk 3.10.3 (Apache-2.0);
numpy 2.5.4 (BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0); scipy, scikit-learn, pandas (BSD);
matplotlib (matplotlib/PSF-style); certifi (MPL-2.0); tqdm (MPL-2.0 AND MIT); regex
(Apache-2.0 AND CNRI-Python); pillow (MIT-CMU); torchcodec 0.7.0 (BSD-3-Clause licence file).

**Missing or unknown metadata (NOTE):** `primepy 1.3` has `License: UNKNOWN`, an MIT classifier only
and no licence file. It is a transitive dependency via torch-pitch-shift. `pytorch-metric-learning`
says "UNKNOWN", but its file is MIT. Neither is copyleft.

Lock packages not installed on Windows (licence from PyPI JSON or general knowledge, not checked
in metadata): cffi (MIT), cryptography (Apache-2.0 OR BSD-3), jeepney (MIT), secretstorage (BSD-3),
pycparser (BSD-3), hf-xet (Apache-2.0), triton (MIT, Linux only), httpx2-jsfetch (BSD-3-Clause,
emscripten only).

**F5 FFmpeg (ACTION).** `.venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe`
(87.6 MB) embeds the configure line `--enable-gpl --enable-version3 … --enable-libx264
--enable-libx265 --enable-libxvid`. The build reports "GPL version 3 or later" (MSYS2 gcc 14.2.0).
`media.py` and `audio_inputs.py` call it through `subprocess` and request `libx264` for H.264
playback MP4s and `libmp3lame`. Implications:
- Distributing **source plus `uv.lock`** is unaffected: users obtain FFmpeg from PyPI themselves.
- **Any CBS binary distribution** (installer, ZIP containing `.venv`, Docker image, UCloud image)
  must include the GPLv3 text and corresponding source or a written offer, plus the LGPL notices
  for the PyAV DLLs. H.264/HEVC encoders also raise patent-pool questions; ask CBS legal.
- The UCloud runtime (`catalogs/ucloud_whisperx_runtime.txt`) installs `imageio-ffmpeg==0.6.0`
  with pip on Linux, with the same considerations. The macOS and Linux binaries were not inspected.

**UCloud runtime lock (NOTE).** `ucloud_whisperx_runtime.txt` pins 120+ packages, including
`nvidia-*-cu12` (NVIDIA proprietary EULAs, redistributed by NVIDIA on PyPI), `pyannoteai-sdk`
and `opentelemetry-*` (Apache-2.0). They are pip-installed into the user's own UCloud project and
never redistributed by CBS. The app disables HF and pyannote telemetry (`enhancement.py:230-232`).

**F11 (NOTE).** `https://github.com/CBS-HPC/ucloud-api` is public and shows "MIT, CC-BY-4.0". The
pinned release wheel URL answered HTTP 302 to a release asset without authentication.

## 3. Models referenced or pinned by the app

The app commits **no model weights**. Weights download only after an explicit user action through
`huggingface_hub` (Settings → Processing or runtime preparation). For UCloud, they go into the
user's own project drive. Pins below are from `enhancement_worker.py:32-62`; the commit SHAs
matched the HF API.

| Model (pinned revision) | Use | Licence (evidence) | Obligations / risk |
| --- | --- | --- | --- |
| Systran/faster-whisper-small `536b066` | coarse locator / small ASR | MIT (HF API) | Attribution when redistributed |
| Systran/faster-whisper-medium `08e178d` | ASR | MIT (HF API) | as above |
| Systran/faster-whisper-large-v3 `edaa852` | ASR | MIT (card; `research/model-licence-addendum-2026-10-28.json`) | Upstream Whisper is MIT; HF card of openai/whisper-large-v3 says Apache-2.0 (discrepancy already recorded) |
| mobiuslabsgmbh/faster-whisper-large-v3-turbo `0a363e9` | ASR | MIT (HF API) | as above |
| facebook/wav2vec2-base-960h `22aad52` | English alignment | Apache-2.0 (HF API) | NOTICE attribution |
| saattrupdan/wav2vec2-xls-r-300m-ftspeech `7a60985` | **Danish alignment (default)** | `license: other`. The card says use must follow Folketinget's licence (`ft.dk/.../deling-og-rettigheder`); FTSpeech terms point to two Folketinget licences | **F7 ACTION:** Folketinget page returned 403, so the terms are **unverified**. CBS must confirm they allow research use, and attribution |
| pyannote/speaker-diarization-community-1 `3533c8c` | speaker labels | CC-BY-4.0, `gated: auto`; the gate collects company/use case and consents to pyannote marketing email | **F8 ACTION:** add attribution. Each user accepts the gate with their own HF account (the app already says so). Copying cached weights to the user's own UCloud project is permitted by CC-BY |
| danish-foundation-models/edda-v0.2 `698f8d0`, edda-v0.2-large `796bf03` | fixed-da CBS Runtime ASR (converted to CT2) | Apache-2.0 (HF API/card; base whisper-large-v3(-turbo) MIT) | Attribution and change notice if converted weights are shared. Training data includes FTSpeech/FLEURS/CoRal (separate rights) |
| CoRal-project/roest-v3-whisper-1.5b `7182cce` | fixed-da CBS Runtime ASR (converted to CT2) | Metadata `openrail`; the card links a **custom Alvenir ApS OpenRAIL-M** licence (16,554 bytes, SHA-256 `f575b636…799e`, per `docs/danish-model-licence-review-2026-10-27.md`) | **F6 ACTION:** use restrictions bind every user (including identity-trait inference). Converted weights are derivatives: if shared (for example a shared UCloud project folder), include the licence and restrictions. Danish law and arbitration. The catalog's `"license": "openrail"` in `danish_whisper_models.json` is misleading |

Catalog-only candidates in `catalogs/speech_models.json` and `research/model-evaluation-candidates.json`
are never downloaded or enabled by the app (NOTE): Whisper base/small/medium/large-v3 (HF says
Apache-2.0, upstream says MIT), whisper-large-v3-turbo (MIT), distil-large-v3.5 (MIT), NVIDIA
parakeet-tdt-0.6b-v3 (CC-BY-4.0), syvai/hviske-v5.3 and v6 (**CC-BY-NC-4.0**: commercial or
company-funded use needs a syv.ai licence) and `CoRal-project/roest-whisper-1.5b-v2` (openrail;
its pinned revision equals the v3 SHA, so check the entry). UCloud Transcriber's own models,
including Parakeet, run inside SDU's app and the OpenAI models are API services: neither is
distributed by CBS. Cohere and Canary appear only in research documents. Publishing these licence
observations is fine (factual metadata).

## 4. Bundled assets and data

| Path | Status | Severity |
| --- | --- | --- |
| `src/cbs_transcriber/static/brand/CBS-New-Bold.ttf` | Proprietary. Embedded notice: Copyright 2012 Tore Rosbo at 1508 A/S, "All rights reserved". Licence granted to "Copenhagen Business School including collaborators". The CBS design guide states that CBS has permanent exclusivity over CBS NEW. It cannot be placed under an open licence; publishing it gives any visitor a copy | **F2 BLOCKER** |
| `static/brand/CBS-Sans-Regular.ttf`, `CBS-Sans-Bold.ttf`, `Inter-OFL.txt` | Inter, renamed by CBS, under OFL-1.1 (per `static/brand/README.md` and the design guide); OFL text included | NOTE: keep OFL; exclude the fonts from the code licence |
| `static/brand/cbs-logo.svg`, `cbs-logo-vertical.svg` | Official CBS logos, unchanged. The design guide does not address external use | **F4 ACTION:** CBS Communications approval. Add a trademark notice; forks must remove branding |
| `static/*.js`, `*.css`, `*.html` | Own code. No vendored libraries, CDN links or third-party icon sets (`grep` for `@license`, CDN hosts, icon-set names and `viewBox="0 0 24 24"`) | NOTE |
| `tests/fixtures/fleurs_da/*.wav` + `manifest.json` + `README.md` | Five unmodified Google FLEURS test clips via FluidInference/fleurs-full@`1cca811`; CC-BY-4.0 attribution, licence link and change statement are present | NOTE: add to NOTICE/REUSE. Mirror licence not re-fetched |
| `tests/fixtures/ucloud_transcriber_batch_1_12.json` | Schema subset of public UCloud app metadata. Its README says it contains no IDs or credentials | NOTE |
| `Transcriber_-_Python/` (2 `.py`, 2 `.docx`) + identical `Transcriber_-_Python.zip` | "Supplied" NVivo-prep scripts and guides. No licence or authorship header. The docx `core.xml` names two individuals as creator and last editor, with `Company` "Statens It". Not used at runtime or in tests (only `ruff extend-exclude`) | **F3 BLOCKER until** the owner grants permission and a licence. Otherwise remove the files (and decide on history) |
| `research/*.json`, `docs/**` | Own analysis. Model-card facts and short quotes, plus links. One `>` quote block (`docs/verification-2026-10-27-supervisor-startup.md`) | NOTE: proposed docs licence CC-BY-4.0 |

## 5. Code provenance

- Searches of `*.py, *.js, *.css, *.html, *.sh` for `copyright`, `SPDX`, `licensed under`, "adapted
  from", "copied from", "ported from", StackOverflow and GitHub blob URLs found nothing. Whisper,
  WhisperX and pyannote are imported, not copied. This was not a full similarity scan (NOTE).
- `ucloud_supervisor.sh` and the `scripts/*.py` are own code (no foreign headers).
- **PII-Tracer:** referenced only in `docs/proposals/anonymisation-2026-10-30.md` (staged) and
  `docs/evaluations/anonymisation-models-2026-10-30.md` (untracked), as `perplexity-ai/PII-Tracer`
  (HF API: MIT, not gated) hosted by the separate `CBS Inference/ucloud-vllm-agent`. **No adapter code
  is in this repository** (no PII matches in `src/`). Audit that repo separately.
- Authorship: 169 commits by `Py310 Smoke <smoke@example.com>` and 47 by `kgp <kgp.lib@cbs.dk>`.
  Much of the code is AI-agent assisted. CBS should state itself as copyright holder and decide on
  an AI-assistance statement (NOTE: AI-assisted code does not block publication).

## 6. Secrets and private-data hygiene (quick scan)

- Tokens: patterns for `hf_…`, `sk-…`, `ghp_`, `github_pat_`, JWTs, AWS keys and PEM keys found only
  the synthetic `sk-synthetic-secret-key` in `tests/test_backup.py:14,36`. OK.
- Private paths: `docs/handoff-2026-10-29-integration.md:4` contains a real Windows user path and
  username. Every other `C:\Users\…` or `/Users/…` occurrence is a placeholder (`<name>`, `researcher`).
  **ACTION:** edit or omit.
- Personal data in metadata: the docx creator and last-modified-by names (F3); commit author email
  `kgp.lib@cbs.dk` in Git history. **ACTION:** decide.
- Live identifiers: `docs/verification-2026-10-29-delete-cancellation.md:5` cites a real UCloud job
  number. No UUIDs or project/wallet IDs were found outside tests. Hosts are public
  (`cloud.sdu.dk`, `design.cbs.dk`); there are no internal hostnames or private IPs. Low risk.
- No interview text, recordings or databases are tracked: the only audio is FLEURS, and transcript
  strings in tests are synthetic. `test-results/*.png` is git-ignored and untracked.
  `git log --diff-filter=D` shows only `static/favicon.svg` ever deleted.
- Internal process documents (handoffs, overnight plans, `docs/cbs-deployment-proposal.md` naming a
  LIBAS/CBS IT pilot, verification logs) are not secret but are internal. **ACTION:** decide.
- History: the CBS NEW font (`f57f6b3`), the docx files and emails remain in every historical
  commit. Deleting them in a new commit does not unpublish them.

## Prioritised action list

1. **BLOCKER** Choose the licence and add `LICENSE`, `NOTICE` and the pyproject `license`/`license-files`
   (PEP 639, hatchling supports it). Optionally add REUSE `LICENSES/` + `REUSE.toml` for per-path licences.
2. **BLOCKER** Remove `CBS-New-Bold.ttf` and use CBS Sans Bold (OFL) or a system font for headlines,
   unless CBS Communications approves public redistribution in writing. Either way, exclude it from
   the project licence.
3. **BLOCKER** Clear or remove `Transcriber_-_Python/` and `Transcriber_-_Python.zip`. If kept, get the
   authors' permission, add a licence and strip docx personal metadata.
4. **BLOCKER (process)** Decide whether to publish full history or a fresh squashed history, given
   items 2-3 and F9. The cleanest option is a new public repository from a cleaned tree.
5. **ACTION** Get CBS Communications approval for the logos. Add a trademark statement ("CBS name, logo
   and CBS NEW are not licensed under …"); Apache-2.0 §6 already withholds trademark rights.
6. **ACTION** Add a "Third-party software and models" README section and NOTICE. State that FFmpeg
   (GPLv3 via imageio-ffmpeg; LGPLv3 via PyAV) is installed by uv and not distributed by CBS, and
   that any future installer needs GPL/LGPL compliance and a codec-patent review.
7. **ACTION** Røst v3: show the Alvenir licence link and use restrictions before preparation. Change
   the catalog `license` to a custom identifier with the LICENSE URL. Decide whether converted
   weights in shared UCloud folders count as distribution.
8. **ACTION** Get CBS legal confirmation of the Folketinget/FTSpeech terms for the default Danish
   aligner, or offer an alternative aligner. Record attribution.
9. **ACTION** Add CC-BY-4.0 attribution for pyannote community-1, FLEURS, and Edda/Whisper (MIT/Apache)
   to NOTICE.
10. **ACTION** Hygiene edits: the handoff path, the live job number (optional) and a decision on the
    internal documents.
11. **NOTE** Fix the `roest-whisper-1.5b-v2` catalog revision mismatch. Keep the Whisper MIT/Apache
    discrepancy visible. Audit the separate `ucloud-vllm-agent` repository before it is referenced
    publicly.

## Recommended project licence

**Apache-2.0** for code. Every dependency is permissive or weak-copyleft. It carries an explicit
patent grant, a NOTICE mechanism suited to model and data attributions, and §6 trademark
exclusion for CBS branding. It is also compatible with GPLv3 if anyone combines it with FFmpeg.
**CC-BY-4.0** for documentation (`docs/`, `research/`, README), matching CBS-HPC/ucloud-api. Keep
separately licensed paths: fixtures CC-BY-4.0, CBS Sans OFL-1.1, logos reserved. If CBS prefers
consistency over the patent clause, MIT is equally compatible. The EU public-sector EUPL-1.2 would
add weak copyleft and is a policy choice for CBS. Copyright line: "Copyright 2026 Copenhagen
Business School" (to be confirmed).

## Draft NOTICE / third-party attribution outline

```
CBS Transcriber
Copyright 2026 Copenhagen Business School. Licensed under the Apache License 2.0.
Documentation: CC BY 4.0.

Trademarks: The CBS name and logo (src/cbs_transcriber/static/brand/*.svg) are trademarks of
Copenhagen Business School and are not licensed under Apache-2.0 or CC BY 4.0.

Bundled third-party material
- CBS Sans (Inter), Copyright 2016/2020 The Inter Project Authors, SIL OFL 1.1
  (static/brand/Inter-OFL.txt).
- FLEURS test clips (tests/fixtures/fleurs_da), Conneau et al. 2022, CC BY 4.0, via
  FluidInference/fleurs-full@1cca811; unmodified audio, text repackaged as JSON.

Installed separately by uv (not distributed with this repository)
- FFmpeg 7.1 (GPL-3.0-or-later build) via imageio-ffmpeg (BSD-2-Clause); FFmpeg libraries
  (LGPL-3.0-or-later) via PyAV (BSD-3-Clause).
- WhisperX (BSD-2-Clause), faster-whisper/CTranslate2 (MIT), PyTorch (BSD-3-Clause),
  pyannote.audio (MIT), Transformers/huggingface_hub (Apache-2.0), ucloud-workflow
  (MIT; docs CC BY 4.0), and the packages in uv.lock under their own licences.

Models downloaded only at the user's request (each under its own licence; not distributed)
- pyannote/speaker-diarization-community-1: CC BY 4.0, gated (accept the terms on Hugging Face).
- Whisper conversions by Systran and Mobius Labs: MIT. facebook/wav2vec2-base-960h: Apache-2.0.
- saattrupdan/wav2vec2-xls-r-300m-ftspeech: Folketinget terms (link).
- Edda v0.2 / v0.2-large (Danish Foundation Models): Apache-2.0.
- Røst v3 (CoRal/Alvenir): custom OpenRAIL-M licence with use restrictions (link).
```

## Decisions needed (user, CBS legal, Communications, IT)

1. Project licence (Apache-2.0 vs MIT vs EUPL-1.2), docs licence and the exact copyright holder.
2. CBS NEW font and logos in a public repository: remove, or get written approval plus an exclusion.
3. Ownership of and permission for the "supplied" scripts and guides. Publish, or drop and rewrite history?
4. Publish full Git history (author emails, F2/F3 blobs) or a fresh history? Add a `.mailmap`?
5. Røst v3: are its identity-inference restrictions acceptable next to diarization? Does a shared
   UCloud folder of converted weights count as distribution?
6. Folketinget/FTSpeech terms for the default Danish aligner (research and any commercial collaboration).
7. Will CBS ever ship binaries (installer, `.venv` ZIP, image)? If so, plan GPL/LGPL source
   compliance and an H.264/HEVC patent review.
8. Which internal documents (handoffs, overnight plans, deployment proposal, verification logs)
   to publish. Is an AI-assistance statement needed?
9. NC models (Hviske) stay catalog-only. Before enabling, decide whether externally funded or
   company-linked projects count as commercial use.

## Not verified

- Folketinget reuse terms (`ft.dk` returned HTTP 403). FTSpeech `LICENSE.html` only points to them.
- macOS and Linux `imageio-ffmpeg` builds. PyAV build flags beyond the `avutil` licence string.
- The licence of WhisperX's bundled VAD asset. The FluidInference mirror and google/fleurs licences
  (taken from the fixture README).
- Font name tables and embedded notices (taken from `static/brand/README.md`). CBS policy on
  external logo use.
- Licences of lock packages not installed on Windows (listed in §2).
- Authorship of `Transcriber_-_Python/`. Complete absence of copied code (no similarity scan).
- The separate `ucloud-vllm-agent` repository and PII-Tracer deployment.
