# T89 — Pinned research strata and supplied-output comparison

28 October 2026. ADR 0079 / V134. This stage performs deterministic offline scoring; it
does not run a model, acquire weights, submit jobs or establish model performance on CBS data.

## What is available

The [evaluation plan](../research/danish-evaluation-plan.json) binds the exact canonical
payload hash of the existing seven-candidate manifest and the five licensed public Danish
FLEURS WAVs. The public clips total 63.96 seconds and remain a read-speech smoke test.
Separate Danish interview, English interview and Danish/English code-switching strata have
no pinned references yet. The report retains those gaps and missing candidate/cohort runs;
missing measurements are not zero error or evidence of equivalence.

The interview targets are editable planning targets, not a statistical power calculation or
journal requirement. `recording_group` must explicitly identify reviewed source recordings
to count that coverage; separate clips do not automatically become independent interviews.
Code-switching clips retain a primary `da`/`en` label for the existing scorer and an explicit
`code_switching: true` annotation. The mixed cohort is reported separately, without inferring
language-identification or switch-boundary accuracy.

## Inspect readiness without running any model

```powershell
uv run python -m cbs_transcriber.evaluation_suite `
  --plan research/danish-evaluation-plan.json `
  --candidates research/model-evaluation-candidates.json `
  --suite 'fleurs-da-smoke=tests/fixtures/fleurs_da' `
  --output "$env:TEMP\cbs-evaluation-readiness.json"
```

Output must be new. To score saved outputs, add one or more `--run <file.json>` arguments.
References are selected explicitly with `COHORT=FOLDER`; the plan never opens arbitrary
stored paths. New references require a revised plan with a canonical manifest pin and an
explicit rights observation. Reference WAVs must be regular, hashed, complete files with
matching declared duration. This loader supports WAV reference clips, not arbitrary media.

## Supplied-output contract

Use schema `cbs.transcriber.supplied_model_predictions.v1` with these exact fields:

| Field | Meaning |
| --- | --- |
| `id` | Unique bounded local run label; the report replaces it with an alias. |
| `candidate_id`, `candidate_revision` | Exact selected research candidate and 40-character pin. |
| `artifact` | Object with `model` and exact `revision` for the actual claimed artifact. |
| `runtime` | Object with `engine`, `version`, `device`; unknown values remain explicit. |
| `cohort_id`, `reference_manifest_sha256` | Exact planned reference identity. |
| `predictions` | Every clip ID once, using `text`/optional `words` or saved-result `segments`. |

Predictions may also contain `speaker_map`, `measurements` and a boolean
`contains_reviewed_edits`. Explicit reviewed wording is refused for this uncorrected-ASR
comparison. Missing review metadata is still unverified; the tool cannot detect undisclosed
human correction. Timing and speaker annotations follow [the existing reviewed scorer](reviewed-evaluation.md).
Measurement keys are `elapsed_seconds`, `time_scope`, `peak_rss_bytes`, `memory_scope`.
Time scopes are `inference_only`, `full_pipeline`, `queue_to_completion`; RSS scopes are
`worker_peak_rss`, `process_tree_peak_rss`. Values are operator observations. GPU memory,
cost, language-boundary accuracy and actual inference environments are not measured here.

The wrapper reuses the existing WER/CER, lexical timing/speaker and performance calculations.
It does not pool distinct strata into a leaderboard. Reports omit transcript bodies, raw
clip/group/run labels and arbitrary runtime/licence fields. They retain model/source identities
and hashes; they are not anonymized or tamper-proof evidence.

All run identity is labelled `operator_supplied_predictions` and `identity_verified: false`.
Matching pins demonstrate internal consistency, not execution of the claimed model. A
converted artifact remains separately pinned and carries an unresolved conversion/licence
gap. These observations are not the verified app-producer receipt from ADR 0076, and are
never silently upgraded to one. Hosted opaque API models need a separate identity adapter;
`whisper-1` must not be relabelled as the pinned HF Whisper artifact.
The `openai-compatible` runtime engine means an API transport for a claimed HF artifact;
it does not mean the official OpenAI service or verify the declared server model.

## Licence follow-up

The [small evidence addendum](../research/model-licence-addendum-2026-10-28.json) extends the
[existing candidate review](danish-model-licence-review-2026-10-27.md); candidate pins and
catalog choices are unchanged.

- The pinned Hviske v6 notice names a historical saga-2 commit and an unversioned Qwen base.
  A second unauthenticated check retrieved and hashed that historical card after initial
  HTTP 401 responses. It declares Apache-2.0 and its base licence types; the standalone
  LICENSE request returned 404. The card also declares FLEURS test-slice checkpoint selection,
  adding a holdout caution without proving training lineage for Hviske. Its complete governing
  text chain and exact initialization artifacts remain unresolved. No gated-file access was attempted.
  [Publisher notice](https://huggingface.co/syvai/hviske-v6/blob/323b5ffdf54ef22196340a0b37cb11e668fefc5a/NOTICE),
  [named historical card](https://huggingface.co/capacit-ai/saga-2-m/blob/f1c014f383d59be1302c4eac350f6335a30b8f68/README.md).
- A separately pinned public Qwen licence was retrieved and hashed. The notice does not
  identify its decoder revision, so this new pin cannot establish historical derivation.
  [Pinned Qwen licence](https://huggingface.co/Qwen/Qwen3-0.6B-Base/blob/da87bfb608c14b7cf20ba1ce41287e8de496c0cd/LICENSE).
- The app's exact Systran CTranslate2 artifact declares MIT and describes an unversioned
  upstream conversion command. Its identity differs from the source candidate and requires
  artifact-specific review. [Pinned conversion card](https://huggingface.co/Systran/faster-whisper-large-v3/blob/edaa852ec7e145841d8ffdb056a99866b5f0a478/README.md).

The addendum distinguishes notice/card hashes from governing legal-text hashes. None is a
model-weight hash or CBS clearance. Hviske noncommercial scope, Røst's custom conditions,
ancestry, code and recording rights retain the existing review requirements.

## Verification and limits

Tests use the committed public five-clip set and generated silent WAVs with explicitly
synthetic text/annotations. Synthetic supplied predictions test arithmetic and missing
coverage only; they are not recognition or quality benchmarks. Routine tests make no network,
model, inference or paid service calls. Small primary-source text/metadata retrieval for the
licence addendum is separate from those tests.

`uv run --no-sync pytest tests/test_evaluation_suite.py -q -o addopts=''` passed 25 cases,
including both explicit reviewed-flag regressions after their recorded red run. A subsequent
transport-identity contract case passed separately (1 passed, 25 deselected). Scoped Ruff
check/format passed. The existing queue/playback/persistence/export integration test passed
after its mock located recording IDs from validated ancestors rather than assuming the old
chunk-path depth. The independent review confirmed the reviewed-flag fix and public text hashes.
Its final verdict was GO with no remaining verified blocker; this concerns the offline
comparison contract, not model performance or institutional licence approval.

Actual Danish/English interview references, code-switching data, model execution, resource
measurements, conversion validation and CBS clearance remain pending.
