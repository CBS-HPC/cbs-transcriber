# 0178 — Bounded project-funding observations

Proposed 12 November 2026 for the user's authorized additional backend work. Conditional
independent design Go; **implementation and acceptance are unstarted**. This is the read-only
foundation for [ADR 0177](0177-funding-based-compute-fallback.md), not a routing decision.

## Contract

One standalone `ucloud_funding.py` observes an explicitly supplied canonical server, selected
project and API key using the released SDK's explicit `CloudSettings`/`UCloudClient`. No
environment or credential discovery, alternate project selection or funded-GPU setup is
required. Existing discovery, routes, settings, schemas and job execution stay unchanged.

Return immutable bounded scalars only: schema, configured server/project, observation times,
completeness, GPU/CPU state, optional integer balance, supported unit/frequency enums and
curated reasons. Never return raw wallets, dictionaries, cursors, keys, bodies or exception
text. Always retain `affordability="not_checked"` and `reservation=False`.

Target categories at provider `ucloud` are `gpu-nvidia-b200` and `cpu-amd-zen5`. States are
`positive_current`, `no_usable_balance`, `absent`, `unknown`. Zero is not an explanation of
exhaustion versus expiry/future allocation/hierarchy. Positive is not whole-request funding.
No summation of duplicate wallets or quota-minus-usage reconstruction.

## Bounded transport and identity

Use only `GET /api/accounting/v2/browseWallets`, exact Project/auth context,
`includeChildren=false`, complete pagination, identity encoding and no redirects. Stream
through the pinned SDK's HTTPX transport with closure and deadline checks through EOF.
Proposed app bounds: 15 seconds overall, 1 MiB/page, 4 MiB aggregate, 32 pages, 3,200 rows and
unique cursors up to 1,024 characters. These are local limits, not provider guarantees.

Require exact project owner/type and well-formed category/provider/product-type identities.
Valid unrelated selected-project wallets are ignored after identity classification. Foreign
or malformed ownership, duplicate targets, malformed target units or any incomplete/error
page makes the **entire observation unknown**, including earlier positive matches. Only a
complete validated traversal can establish absence. Balances require exact integer types;
unsupported negative values conservatively remain unknown, without claiming a universal API
rule. No durable receipt, capture, CPU/model eligibility or new allocation follows.

## Unit evidence and acceptance still required

The proposer supplied full upstream commit `2c23d295318cabdb960a8baa69dc175e806254ac`:
[K8s resource products](https://github.com/SDU-eScience/UCloud/blob/2c23d295318cabdb960a8baa69dc175e806254ac/provider-integration/im2/pkg/integrations/k8s/shared/products.go)
and [shared product schema](https://github.com/SDU-eScience/UCloud/blob/2c23d295318cabdb960a8baa69dc175e806254ac/provider-integration/shared/pkg/accounting/products.go).
The proposed supported resource units are exact GPU/GPU or Core/Core, `floatingPoint=false`,
`displayFrequencySuffix=true`; periodic SECOND/MINUTE/HOUR/DAY frequencies. ONCE, currency
and unknown representations remain unsupported. The reviewer had not independently checked
this full pin: verify it before accepting implementation fixtures. Existing app wallet
fixtures omit these fields and cannot substitute for that contract.

Actual released-SDK MockTransport tests must prove headers/origin, GET-only streaming,
strict schema/units, incomplete/foreign/duplicate evidence, hierarchy zero, byte/row/page/cursor
limits, slow-drip/EOF deadlines, compression/redirect refusal, response closure and secret-safe
errors. No real API calls or existing production caller changes. Funding sufficiency estimates,
CPU-only setup and model/Runtime CPU compatibility remain separate T145 work.
