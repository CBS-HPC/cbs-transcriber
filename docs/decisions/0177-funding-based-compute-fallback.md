# 0177 — CPU fallback depends on project funding

Policy direction accepted 12 November 2026 for **both native UCloud Transcriber and CBS
Runtime**. Implementation and model-specific CPU acceptance remain pending. Existing fixed-MIG
rules and frozen jobs are unchanged; this decision does not enable fallback or submit work.

## Agreed rule

1. If the selected UCloud project has sufficient usable GPU funding, use one B200 1g MIG,
   including waiting in its queue. Busy/full hardware, a timeout or a shorter CPU queue does
   not authorize CPU fallback.
2. If complete successful evidence proves GPU funding is absent, exhausted or insufficient
   for the accepted request, consider exactly `cpu-amd-zen5-8-vcpu`, category `cpu-amd-zen5`,
   provider `ucloud`. Require usable CPU funding and a compatible application/model/recipe.
3. Unknown funding stays unknown. Failed reads, incomplete pagination, malformed units,
   wrong-project evidence or storage-credit errors never mean zero GPU hours. If the CPU
   route cannot be qualified, show the reason rather than silently choosing another machine
   or speech model.

Funding means the **selected project's** usable allocation, including hierarchical limits;
the API key or a positive quota on a different project is insufficient. New settings alone
cannot rewrite already accepted work. Freeze project, exact product/device and versioned
decision at fresh acceptance; retries/restarts keep them. An uncertain submission cannot
become a second CPU allocation. Parallel MIG scheduling is a related but separate decision.

## Evidence and remaining design

The ordinary-user wallet API is `GET /api/accounting/v2/browseWallets`, scoped with the
selected project header and complete pagination. Match project owner, COMPUTE product
category and provider. `maxUsable` represents usable hierarchical funding; quota minus usage
is not an equivalent substitute. Read accounting units/frequency and product price/fraction
before interpreting balances as hours. Do not use provider-role-only accounting APIs.
[Wallet schema](https://github.com/SDU-eScience/UCloud/blob/master/provider-integration/shared/pkg/accounting/accounting.go),
[hierarchical accounting](https://github.com/SDU-eScience/UCloud/blob/master/core2/pkg/accounting/accounting_internal.go).

Positive `maxUsable` establishes current funding eligibility, not a reservation sufficient to
finish a future job. The checked upstream submission path tests the locked compute wallet;
it does not establish a full-walltime reservation. The
[user guide](https://docs.cloud.sdu.dk/guide/submitting.html#machine-type) asks for funding
covering estimated cost. A versioned, conservative request-cost estimate and its safety margin
still need design and tests. Do not invent a provider requirement from maximum walltime or
apply an AU native benchmark multiplier to every Runtime model.

Runtime additionally needs a separate positively verified CPU recipe/requirements identity,
CPU execution/precision/model checks and CPU-flavored generation selection. Missing CUDA is
not proof of CPU readiness. Preserve existing CUDA/legacy identities and GPU memory checks;
see [ADR 0176](0176-isolated-runtime-generation-recovery.md). Native Transcriber must verify
the selected application/template's CPU contract too. Current
setup discovery/profile validation assumes a GPU product; extend that boundary so
a legitimate CPU-funded-only project can be configured without first requiring MIG funding.
Published
[CPU/GPU measurements](../evaluations/whisperx-cpu-gpu-speed-2026-11-12.md) inform planning,
not compatibility or institutional acceptance.

Before activation, mock complete/partial wallet reads, units/expiry/hierarchy, sufficient and
insufficient balances, BUSY/FULL with funding, unknown checks, CPU ineligibility, accepted-job
freezing, Stop and uncertain submission. Real CPU model/memory/output acceptance is separately
scoped. No live accounting reads, allocation, preparation, model download or cleanup is
authorized by this documentation.
