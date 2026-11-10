# Evaluation producer receipts — 27 October 2026

T89a / ADR 0076 / V11. Design/reproduction on 27 October; final verification on 28 October.
This is an offline correction to the opt-in public evaluation runner, not a live ASR baseline,
model recommendation, licence clearance or benchmark of the candidate models.

## Deterministic defect

```powershell
uv run pytest tests/test_evaluation_runner_receipts.py::test_evaluation_uses_machine_creation_after_active_result_and_wording_change -q --junitxml=test-results/fleurs-receipts-red.xml
```

The old runner failed the expected assertion in 11.46 s: it reported WER **0.0** instead of
**0.5**. The evaluation producer's creation said `one wrong`; an unrelated active/reviewed
view said `one two`, matching the reference. Taking `original_text` from that active view
and exporting without a result/version selector attributed a different state to the producer.
Evidence is ignored `test-results/fleurs-receipts-red.{xml,txt}`. Synthetic bytes and mocked
HTTP only; no running app/library, provider or private interview was accessed (B175).

## Contract and review

Capture the accepted jobs' frozen selected provider and one-to-one recording associations.
Checkpoint job IDs before rejecting malformed acceptance, retaining recovery evidence without
cancelling/resubmitting. At completion, resolve each exact producer result and sequence-one
worker creation. Verify its canonical payload hash, score its text, export that exact version,
and retain allowlisted prediction/export receipts. Missing/ambiguous/corrupt/trashed-before-
resolution evidence makes the run incomplete; no active/latest-result fallback.

The job-status API excludes private frozen settings, while the enqueue reply includes them.
History LIST deliberately omits producer context; full version GET includes it and verifies
the preserved payload. The latter also adds derived `speaker_defaults`, which are excluded
when recomputing the canonical segments/speakers hash. No API change is required.

Independent design review gave GO for this narrow boundary and identified the first draft's
mistaken reliance on context in history LIST. Validate context on full version GET and prove
the real API response shape with TestClient integration. A subsequent focused run had 24 passes
and two failures in 17.58 s. The real positive exposed another draft assumption: version GET
does not include `recording_id`. Prove that relation through result ownership and the scoped
version route; an explicit contradictory field still fails. The second failure was a mock
asserting on an unknown version path instead of returning HTTP404; correct the fixture to
exercise the runner's incomplete-run behavior (B176). No persistence/API mutation is needed.
An initial concern about Runtime
creation actions was retracted after checking the actual dispatch: Runtime full-ASR writes
`source.kind=provider`, producing `provider_result` even though its job kind is `enhance`.
Ordinary existing-text enhancement remains a distinct, refused evaluation producer shape.

The legacy `.nvivo.txt` filename is preserved, but the actual version-specific export is
readable text and is labelled `export_format=text`. It is not a native NVivo format assertion.
Prediction text hashes describe UTF-8 text; export hashes/sizes describe exact downloaded bytes.
Canonical settings/payload hashes are not claims of raw HTTP byte capture or remote execution.
Runtime's effective frozen ASR language is checked before scoring, but this narrow receipt does
not serialize its full effective options or their identity separately from the profile hash.
It therefore does not establish complete runtime/request reproduction; fuller T43 capture
remains open. No current Git revision or current runtime is substituted for historical evidence.

## Final verification

Verification continued on 28 October after a host/tool pause. A shared-checkout run could not
collect tests because the other chat's unfinished original-input work imported a not-yet-created
`audio_inputs` module. No tests executed in that attempt; that WIP was preserved. A clean image
of committed `52eaa12` plus only this stage's two Python files is used for final verification.

The first clean candidate passed all 62 affected cases in 31.19 s. Independent review then
reproduced a strict-receipt gap: missing predecessor/source keys were treated as explicit nulls.
The actual APIs include these keys. Require their presence in both creation-list metadata and
the full version, retaining four omission regressions (B177). The first 62-case pass predates
that hardening; final focused evidence is recorded separately. The independent red probe is
ignored `test-results/evaluation-independent-probe.{py,txt}`.

After that minimal key-presence guard, all five focused cases (four omissions and the real
public-history API positive) passed in 6.52 s, 50 deselected. The broad and final selections
cover **66 distinct passing unit/API cases**; the real positive overlaps. Evidence:
`test-results/fleurs-receipts-candidate.{xml,txt}` and
`test-results/fleurs-receipts-final-focused.{xml,txt}`. The intended affected selection is:

```powershell
uv run pytest tests/test_evaluation_runner_receipts.py tests/test_evaluation.py -q
```

It was executed with the repository interpreter inside the isolated image, with its `src`
explicitly selected and an imported-app origin assertion. The final focused selection adds
`-k 'explicit_null_ancestry or real_public_history'`. No unfinished source was copied from the
shared checkout. The earlier shared-WIP attempt did not collect any cases and is not a pass.

Independent final reviewer **GO**: 13 additional tiny synthetic probes passed, covering the
two originally missing-field failures, nine invalid Runtime scope/option combinations and
valid ordinary API/Runtime controls. These independently overlap some affected contracts;
they are not added to the 66-case count. Red and green probe logs remain separate:
`evaluation-independent-probe.txt` and `evaluation-independent-probe-green.txt`.

Final normalized-LF source hashes:

- `scripts/evaluate_fleurs.py`: `a566360c03860148e41a70e696d96cd2dad033842634480a3c2863466ea09064`.
- `tests/test_evaluation_runner_receipts.py`: `4b2cdc85bbc76c752cb12b92c7662948cdae3bd3fd85d950f8c5b715c0d90748`.

Scoped Ruff lint/format and compilation pass for both files. Frontend, inference, provider
transports, dependencies and the running backend are unchanged by this stage. No live/paid
API calls, private recordings/library access, weights, allocation, migration, restart, main
merge or push. Browser/inference/live acceptance and representative Danish/English baselines
remain separate; the original-input implementation belongs to the other chat.
