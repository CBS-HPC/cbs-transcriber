# Native UCloud Transcriber on B200 MIG

Status: **live acceptance planned, not run**. The offline planner below prepares reviewable
specifications and an inert disposable Store; it is not evidence that native Transcriber
supports MIG. No UCloud lookup, allocation, upload or model download accompanies this work.
A separately reviewed live adapter and explicit authorization for its project, public inputs,
allocation count, wall time and spend ceiling are required before execution.

The question is whether the **native Transcriber Batch application** can execute real ASR
on `gpu-nvidia-b200-1-mig.1g`, including its complete model/timing/speaker stack. CBS Runtime
WhisperX running inside a Terminal allocation is a different application and cannot answer it.
Success qualifies only the tested app version, model, options and MIG product. It does not
change the production native compute policy.

## Existing policy and test seam

- [ADR 0039](../decisions/0039-fixed-compute-model-settings.md) fixes new native requests to
  `gpu-nvidia-b200-1-gpu`. [ADR 0063](../decisions/0063-separate-runtime-mig-compute.md) separately
  freezes MIG compute for CBS Runtime; it does not authorize native MIG admission.
- [ucloud_native.py](../../src/cbs_transcriber/ucloud_native.py) defines that full-GPU constant.
  `require_compute()` enforces it; fresh admission calls it from `input_admission.prepare()`
  and the application preflight. `check_setup()` also refuses a non-B200 native product.
  Discovery filters its eligible products to that exact full-GPU ID. These guards stay intact.
- `NativeSetup` can represent an exact product reference, and `template_info()` with
  `template_job_id=""` builds a direct application specification using that frozen product.
  `build_directory_specification()` then validates declared inputs/model/language/JSON options.
  These lower-level seams permit a separate qualification adapter without a public compute
  chooser, a production settings edit or a change to `COMPUTE_ID`.
- **Do not reuse a B200 template for the MIG candidate.** A template carries its own product;
  changing a settings field is insufficient proof that the submitted job uses MIG. Use the
  direct app path, assert the final specification product, then verify the observed remote
  allocation product as well. Do not patch a guard or rewrite an accepted/frozen job.

The future live adapter must create a new disposable local Store and new test-owned native jobs, freezing
the candidate product before any upload/submission/capture. It must drive the real native
batch worker, result writer and Stop/retry/cleanup controls, with the Store lease held through
drain. It may seed these qualification jobs directly at the frozen-job seam; that is **not**
acceptance of the ordinary HTTP/UI admission path, which remains B200-only. Keep the adapter
outside the serving app and do not open the real library or enumerate existing user jobs.
The offline stage does not execute those worker/control paths. Implement and review the live
adapter before the live step; no live execution command is supplied here.

## Offline plan and identity checks

Run [scripts/native_transcriber_mig.py](../../scripts/native_transcriber_mig.py) with previously
supplied, credential-free app metadata and exact three-field product references. The script
does not discover or retrieve them. The output directory must be new and its parent must exist:

```powershell
uv run python scripts/native_transcriber_mig.py --application-json app.json --b200-product-json b200.json --mig-product-json mig.json --output test-results/native-mig-plan --model base
```

Application JSON is limited to 64 KiB, 50 parameters and 100 options per enumeration. Only app
identity/type and the parameter fields needed by the native builders are retained; supplied
funding, product-support, tool and credential claims are excluded. Nonempty unowned free-text
defaults are refused. Both product files must contain exactly `id`, `category` and `provider`;
the MIG candidate must equal the fixed B200 MIG reference. These checks establish input shape
and identity only. They do not establish current project funding, permissions or app support.
`--model` must be an exact canonical ID in the packaged native catalog. Bare aliases such as
`parakeet` or `whisper/base`, other provider/Runtime IDs, inherited `CBS_TRANSCRIBER_MODELS`
overrides and cached foreign catalogs are refused before any output/Store is created. This
includes refusal before application imports can eagerly load an inherited catalog. This
qualification rule does not change production catalog overrides or legacy native settings.
The sparse committed app fixture deliberately lacks enum options and cannot pass alone as
current metadata. Mocked tests add explicitly synthetic options.

The planner reuses `template_info()` through an in-memory metadata snapshot and the released
0.1.9 `build_directory_specification()` path. It verifies the two pinned public WAV hashes and
sample geometry, copies only those files into a new disposable Store, freezes independent
two-file B200/MIG owners and marks all four jobs and both batches **cancelled**. No worker is
started. Every frozen endpoint is `https://offline.invalid`; project/storage references are
also offline placeholders. These snapshots are qualification inputs, not historical producer
evidence or successful original-input admission. No producer observations or results are made.
Do not open this Store as the serving library or rewrite its snapshots into a live run.

`plan.json` contains the exact app/model/enum/product/specification/member mapping and a
checksum. `validate_plan()` rebuilds the native specifications and checks that mapping;
`validate_specification()`, `validate_results()` and `validate_cleanup_identity()` check supplied
specifications, bounded per-member output bytes and an exact owned-folder/member claim.
The checksum detects changed content relative to the supplied checksum; it is not a signature
or external trust anchor. These APIs do not re-observe a remote job or independently reopen
and verify a subsequently changed Store. Result checks parse supplied bytes without committing
them, and cleanup checks perform no file operation or cloud control.

Every returned observation keeps live execution, GPU execution, metadata eligibility, local
result commit and public admission `not_checked`, and cleanup authority `not_granted`.
There is no execute, reconnect, Stop or cleanup entrypoint. A future live phase requires a new
approved real target and owner, fresh producer capture and separately reviewed lifecycle code;
an offline plan does not grant that authority. See
[tests/test_native_transcriber_mig.py](../../tests/test_native_transcriber_mig.py) for the mocked
checks and network/credential tripwires. Production B200 admission/discovery remains unchanged.

## Inputs and initial run matrix

Use the repository's existing public, attributed
[Danish FLEURS subset](../../tests/fixtures/fleurs_da/README.md), not interview recordings.
Start with unchanged `da_dk_0000.wav` and `da_dk_0001.wav` (23.34 seconds combined). Verify
both against [manifest.json](../../tests/fixtures/fleurs_da/manifest.json), WAV frame geometry
and the recorded source revision. Retain the CC-BY-4.0 attribution with exported evidence.
Use explicit `da`, JSON output and the same native options for each paired run. Assign each
file its own new job UUID filename and preserve the mapping to its fixture hash.

| Native model | Purpose | Scope of a possible pass |
| --- | --- | --- |
| `base` | First, smallest catalog Whisper smoke case | This exact native app/model/options tuple only |
| `large-v3-turbo` | Follow-up production-sized Whisper candidate | Separate two-file paired acceptance, after explicit authorization |
| `nvidia/parakeet-tdt-0.6b-v3` | Follow-up distinct native engine | Separate paired acceptance; require exact version proof for any generic enum token |
| Other catalog models | Later demand-driven qualification | No inference from another model's success |

The catalog's model memory hints are not peak native-application VRAM measurements. They do
not include proof about simultaneous alignment, speaker models, framework workspaces or app
parallelism. A CBS Runtime memory readiness floor is not a native Transcriber requirement.
Do not assume that either `base` or the listed follow-ups fits the slice until it runs.

For the initial `base` qualification, authorize at most three allocations, serially:

1. One full-B200 control containing the two unchanged files.
2. One MIG candidate containing those same two files, including local coordinator restart
   and reconnect to that same allocation.
3. One separate MIG Stop exercise, only after a successful complete MIG run.

The stock native configuration exposes an hours-based allocation limit (minimum one hour);
record the actual requested allocation and a shorter operator monitoring/Stop deadline before
starting. Confirm a monetary ceiling using the then-current project/product terms. No automatic
second POST, alternate machine, model escalation or additional trial is included in this count.
Each follow-up model needs its own explicit allocation budget and paired comparison.

## 1. Read-only eligibility gate, when live testing is authorized

Capture a bounded, credential-free metadata subset for the exact test project and app version:

1. Resolve native Transcriber Batch application identity, flavor, application type, tool/backend
   and supported provider. Retrieve its current declared input, output, model, language, format
   and optional parameters. The observed 1.12
   [schema subset](../../tests/fixtures/ucloud_transcriber_batch_1_12.README.md) is a starting
   reference, not current app/product support evidence. A WEB-capable Batch flavor must still
   have the declared boolean `jupyterLab` switch forced false by `batch_overrides()`.
2. Use native discovery's `_products()` eligibility checks as a starting point: exact provider,
   compatible declared backend, enabled product support and project funding. Confirm the
   candidate's full reference is exactly
   `{id: gpu-nvidia-b200-1-mig.1g, category: gpu-nvidia-b200, provider: ucloud}` and that the
   provider currently advertises that exact supported slice and an admissible queue state.
   Retain its GPU/fraction metadata separately from host RAM. Re-resolve the full-B200 control
   reference independently; do not invent its category from the product ID.
3. Verify any available app-specific product restrictions as well as general provider/backend
   support. Provider availability alone does not prove this native application can use MIG.
   An explicit incompatibility blocks submission and is a recorded eligibility failure.
   If the service exposes no stronger app/product capability claim, record that uncertainty;
   only the bounded, explicitly authorized live run can then establish execution support.
4. Resolve the selected model from actual enum options. Record canonical selected identity,
   actual submitted value and declared label. A generic `parakeet` token requires a label
   proving the canonical v3 model; a v2/unknown label is a refusal, not a fallback. Validate
   explicit language and JSON output. No extra input/init scripts, inherited unowned mounts,
   Jupyter server, arbitrary command, SSH or CBS Runtime supervisor may enter this test.

Save the final specification before upload/POST. Compare the B200/MIG pair: application,
model/language/feature parameters and fixture bytes must match; product, unique owner/folder
and necessary job/input UUIDs differ. Do not call a metadata-only or mocked check a live pass.

## 2. Real two-file execution and result collection

For each control/candidate, use a new exact owned remote root containing `input/` and `out/`.
Refuse a pre-existing root without this run's ownership receipt. Keep control credentials in
the credential store/session; publish no token, `.env` content or credential-bearing URL in
the run journal, commands, evidence or app parameters.

Run the production native directory builder and worker behavior:

1. Verify both immutable public originals; upload only the two UUID-named files; verify each
   transfer before adding its upload receipt. Native Transcriber requires inputs before POST;
   it does not have the CBS Runtime doctor/model-preparation-before-private-upload protocol.
   Model retrieval by the stock app is a possible part of this authorized paid run.
2. Record the credential-free submission projection and durable submission intent before the
   single released `ucloud-workflow` submission call. Count submitted allocations explicitly.
   Persist the returned remote ID immediately. Never run the B200 pair concurrently as an
   automatic rescue for a failed MIG candidate.
3. Retrieve this ID only. Check observed application, full product and owned resources against
   the saved specification. A requested MIG field is insufficient if the observed allocation
   differs or cannot be identified. Record queue/start/terminal timestamps and native app phases
   actually exposed by UCloud; distinguish queue delay, model/startup work and inference time.
4. Obtain existing provider/app GPU diagnostics or job resource telemetry when available:
   visible GPU/MIG identity, actual device memory, peak GPU memory and any OOM/CPU-fallback
   evidence. Do not use host RAM as VRAM or add an init script/change the stock app merely to
   manufacture telemetry. Record unavailable measurements as `not_observed`. A passing GPU
   test needs positive evidence that native inference used the MIG GPU, such as native CUDA
   execution diagnostics bound to that device or provider GPU activity during the observed
   inference stage. Allocation metadata and output success alone cannot rule out CPU fallback.
   If that evidence is unavailable, report `completed_gpu_unverified`, not a GPU execution pass.
   Device-memory and peak-memory claims require their own observations.
5. Collect both original JSON outputs through the actual native path. Match each exact input
   UUID and actual submitted model/language using `native_batch._download()`; reject missing,
   duplicate or ambiguous files. Preserve exact bytes and hashes before parsing. Require two
   independently parseable, nonempty transcript results associated with the correct recordings,
   plus the producer/result/version evidence supported by the adapter's frozen capture policy.
6. Check the enabled native word-timing/speaker contract against the successful control. Do
   not claim full feature acceptance when required fields are absent/invalid or timing/speakers
   were silently disabled to fit MIG. Record legitimate partial timing and feature-specific
   gaps explicitly; these fixtures cannot establish timing/speaker accuracy.
7. Verify each per-file result commits once and that output collection/retry cannot replace
   reviewed text or duplicate results. A terminal SUCCESS with no usable per-file output is
   a failure. Keep valid sibling output if the other member or wrapper fails.

Record wording, normalized WER/CER, timing coverage, speaker fields, timing overruns and feature
differences against the full-B200 control. Do not require byte-identical ASR output or invent
reference word/speaker timing: these public fixtures have text references only. Two-file smoke
success does not establish long-interview quality, diarization accuracy or safe peak memory for
larger inputs. Native model reuse must be observed from native evidence; CBS resident cohorts
and cached remote model files do not prove native in-memory reuse.

## 3. Restart, Stop, uncertain replies and cleanup

During the complete MIG candidate, close only its isolated local coordinator after the remote
ID is durable, leaving the remote job running. Restart the same disposable Store, then explicitly
retry/reconnect through native controls. Require the same remote ID, one original POST, frozen
model/product/file identity, preserved partial receipts and exactly two final saved results.
Restart is not Stop and must not allocate a replacement.

For the separately budgeted Stop case, use two deterministic longer derivatives of the same
public clips if the short pair completes too quickly. Freeze a new manifest of their recipe,
hashes, frame counts and attribution before authorization/upload. Observe actual RUNNING and
an available processing/output barrier, then issue allocation-wide Stop for that test owner.
Require only that recorded remote ID to be targeted and observe terminal state. A SUCCESS that
won the completion race does not exercise Stop; mark that case `not_exercised`, retain its useful
outputs, and do not silently spend another allocation. Preserve available valid outputs and
show incomplete members truthfully. Retry of this stopped owner may collect the same allocation;
it must not resume ASR with a new paid allocation or imply output disposal consent.

Exercise lost-POST-reply safeguards offline before live work. If a separately authorized live
fault injection intentionally loses a response, keep the owner `uncertain`, issue no second
POST and reconcile by exact unique run name plus observed app/product/owned-resource identity.
No listing absence automatically grants a resubmission. Log any later explicit operator absence
decision. A local restart with a known ID does not claim to have tested a real uncertain POST.

After a successful complete run, verify local raw files, hashes, parsed results and durable
producer receipts, then use ordinary native cleanup for that exact owned root. Confirm the
parent listing no longer contains it after Trash; retry cleanup independently without another
inference request. Test a cleanup/reply failure offline and retain an actionable obligation.
Stop/failure is not ordinary success cleanup authority: preserve its public partial evidence
and obtain an explicit disposition for the exact test folder before an operator release.
Never mark a failed run SUCCESS to satisfy cleanup, purge UCloud Trash, or target shared models,
another run, the real library or any existing user's remote job/folder.

## Acceptance record and decision

Keep one metadata-first record per native app/model/product/options tuple, including:

- code commit, adapter revision, released client version and exact app/tool identity;
- requested/observed full product, model enum/label, language and native options;
- fixture manifest/hashes, two input mappings and one owning allocation/submission count;
- observed state timeline, hardware/peak-memory observations or explicit `not_observed`;
- output hashes, parser/result/version counts, quality/coverage observations and errors;
- restart/reconnect, Stop/race outcome, cleanup receipt and remaining obligations;
- cold/warm status if observable, elapsed/active/billed time, actual charge if available, and
  the paired B200 comparison; unavailable cost/telemetry stays unknown;
- disposition: `not_run`, `blocked_metadata`, `failed_execution`, `failed_outputs`,
  `completed_gpu_unverified`,
  `passed_functional`, or `passed_with_resource_evidence`, with explicit unexercised cases.

**Functional MIG acceptance** requires an actual stock native Transcriber run on the exact
observed MIG allocation, positive native GPU execution evidence, correct model identity,
two useful collected results with the required features, one allocation through restart/recovery,
and resolved/explicitly retained cleanup obligations. Metadata,
mocked outputs, a requested product string, CPU fallback, or CBS Runtime completion cannot
satisfy it. OOM, unsupported backend/product/model, terminal failure, incorrect/ambiguous output
or duplicate paid submission is a failure with preserved evidence; it never triggers full-GPU
fallback. A resource-qualified decision additionally needs actual native GPU/memory evidence
and an explicitly bounded representative workload, not only these two short clips.

Publish the evidence and remaining limits before proposing any production compute-policy
change. Supporting native MIG admission, model-specific defaults/fallbacks or larger workloads
would require a separate accepted decision, SPEC/TODO update and ordinary API/browser regression
coverage. Until that work is accepted, native production requests continue to require full B200.
