# Frozen repair source bindings — 28 October 2026

T90a / ADR 0077 / V132 implements a read-only source/projection inspector. It does not build
audio bundles, change routing, run a model, authorize upload or enable remote passage repair.

## Verified boundary

`inspect_repair_source(store, job_id)` reads the job, owning result and exact preserved version
in one SQLite snapshot. SQL byte-length checks precede loading/parsing parameters and payload
(32 MiB each, 64 MiB aggregate). Verify the full canonical version hash and exact frozen data:
full snapshots for ordinary scoped/re-transcription/automatic edit jobs, or selected snapshots
for editor passage timing. Derive the selected projection/hash separately in source order.

Real Store and API freeze shapes cover ordinary selector order, expanded adjacent untimed
groups, complete passage dictionaries/originals/speakers, literal Unicode IDs/text, signed-zero
and bool/int distinctions, ownership swaps even with identical payloads, legacy missing proof,
newer heads, active switches, Trash and concurrent writers. Observation never fetches today's
recording transcript projection or opens a second Store connection.

The receipt contains internal identities/hashes/order only. Its whole-parameter hash is named
`observed_params_sha256`: progress/output additions change it, without changing the preserved
source proof. It is not a frozen request or allocation-compatibility key. Timing-unit checks
prove declared membership/shape, not extracted samples, original-media mapping, neighbour-time
accuracy, provider eligibility, inference readiness or complete job validity.

## Test evidence

- **112 passed in 149.03 s**: all 69 new cases plus 43 existing passage-timing,
  re-transcription, research-history and result-Trash regressions. Evidence:
  `test-results/repair-sources-affected.{xml,txt}`.
- An independent committed-only image of `1dda240` plus the two new files passed all
  **69 new cases in 40.89 s**, with assertions that both app and inspector imports belong to
  that image. This overlaps the 112 cases and excludes the other chat's original-input WIP.
  Evidence: `test-results/repair-sources-candidate.{xml,txt}`.
- Independent review **GO** after 13 standalone synthetic SQL probes. They import only
  the new inspector/history, use temporary minimal SQLite tables and exercise full/trimmed
  proof, ordering, ownership, dictionary/speaker/selector tampering, dispatch/numeric guards
  and prefetch limits. Evidence: `test-results/repair-source-review-probe-green.txt`.
- Scoped Ruff lint/format and compilation pass. No frontend/dependency change; no browser or
  inference acceptance is claimed for this internal boundary.

The affected command was:

```powershell
uv run pytest tests/test_repair_sources.py tests/test_passage_timing.py tests/test_retranscription.py tests/test_research_history.py tests/test_result_trash.py -q
```

The isolated image used the existing repository interpreter and its own `src` path. No
unfinished core file was copied. Existing framework fixtures block downloads and isolate the
HF cache; temporary stores/API tests never use the real library or provider credentials.

## Failures and corrections

The first new-file run had 48 passes/six failures because global fixture normalization sorted
unbounded passages to the end. Its intended gap IDs referred to a bounded passage. Normalize
fixture rows individually and preserve observed/edited order with explicit IDs. A subsequent
run had 57 passes/one failure from a nonexistent fixture Trash method; use the real Store
boundary. Production scope/group guards were unchanged for these fixture corrections (B179).

Independent review reproduced two production-boundary gaps. Ignoring top-level `transcription`
could label a full-ASR job as scoped alignment even though the worker prioritizes that marker.
Huge JSON integer bounds also escaped as `OverflowError` from `math.isfinite`. The new DB
regressions had **eight failures/one compatible control pass in 9.41 s**; after minimal typed
dispatch and finite-number guards, all **nine passed in 6.63 s** (B180/B181). Literal false
retains compatibility; truthy/malformed dispatch markers and huge bounds refuse. Evidence:
`repair-sources-dispatch-red.{xml,txt}` and `repair-sources-dispatch-green.{xml,txt}`.

The first standalone review probe's assertions completed but Windows cleanup failed because
its seed SQLite connection was left open. Explicit fixture closure fixed that probe; no Store
or inspector lifetime/validation rule changed (B182). Its final green is separate evidence.

Final normalized-LF source hashes:

- `repair_sources.py`: `baeb48e594ec2658ffcfaad976f639786321678df91bb71dc34e24094d7094dc`.
- `test_repair_sources.py`: `d59aecc4ef3c7c64517e4c85e764b72dd435a622579c37401fa538c10c2b21a4`.

## Limits and next integration

No real-library/private transcript access, media decode, runtime/model check, weights, paid
call, UCloud allocation/upload, migration, restart, main merge or push. Historical inspection
does not restore Trash or grant new processing approval. Missing legacy evidence refuses
only this inspector; existing frozen execution/retry paths are unchanged. Original-input
implementation belongs to the other chat; no Mac/live UCloud acceptance is inferred.

Next T90 work still needs trustworthy extraction/time-map receipts, bounded bundle publication,
remote input/output/carry ownership, cancellation/recovery, stale application and Protected
checks. General Auto repair additionally needs same-scope language/text compatibility. This
receipt alone satisfies none of those audio, execution or institutional approval contracts.
