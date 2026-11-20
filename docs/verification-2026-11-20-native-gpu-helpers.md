# Native full-GPU observation helpers — 20 November 2026

Local helper implementation: `2caa3b6`, hardened by `13e2027`, on
`codex/weekend-native-gpu-helpers-20261120` from `0d871d7`.
Claude's earlier five-file activation WIP is preserved separately at `5286404`; it is not
the implementation verified here.

## Verified scope

The new production helper accepts bounded supplied bytes only and performs no I/O
and has no routes, worker, Activity, job-policy, submission, Stop or cleanup integration.
The helpers preserve exact product/context matching, 30-second consecutive observations,
strict input-size/output-emptiness observations and fail-closed parsing. Funding remains outside these
helpers; later integration must reuse the existing bounded observer. They do not infer a
count of free machines or reserve capacity.

Independent review found boolean GPU-count acceptance (B482, three red cases), then
unknown/contradictory update evidence and a resumable-state gap (B483, 21 red cases). Final helper gate:
**182 passed**, 2.72 s, with scoped Ruff passing. Before that final parser hardening, the
helper/native-MIG/capture/startup/compute group passed **279 cases**, 93.10 s. The changed
helper was rechecked by the final 182-case gate; do not add those overlapping counts.
Only helper code changed after the 279-case gate.

The closed update fields and final-state predicate follow the pinned primary source:
[UCloud jobs.go at 2c23d295](https://github.com/SDU-eScience/UCloud/blob/2c23d295318cabdb960a8baa69dc175e806254ac/provider-integration/shared/pkg/orchestrators/jobs.go).
That source defines SUCCESS, FAILURE and EXPIRED as final; SUSPENDED can resume.

## Still open

T154 is not a working full-GPU fallback feature. Production integration still needs the
registered release gate, singleton/batch routes and controls, allocation-control ownership,
fresh full evidence/consent binding, truthful capture and portable pins, and tests covering
Stop/Delete, crash/reopen, stale CAS, uncertain POST and retry-to-MIG. Live capacity,
funding and queued-cancellation behavior require separate real-account acceptance.
No real UCloud calls, preparation, paid work, uploads, cleanup, app reload, main merge,
push or release occurred in these checks.
