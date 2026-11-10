# 0087 — Producer-time processing artifact observations

Accepted 28 October 2026 as an independent T43 provenance slice. A small artifact boundary
captures stable bounded managed files at producer checkpoints: request, raw response, output
and an explicitly allowlisted environment manifest. Never collect arbitrary environment
variables, credentials or current runtime facts while reading history.

Opaque in-process captures become producer-bound checkpoint receipts and, after atomic Store
integration, exact creation-version receipts. Keep source job/request and result/version hashes
distinct. Historical validation and public projection are pure, omit paths/body/private IDs,
and report retained-file availability as not_checked. Missing roles are explicit gaps; legacy
artifacts are never backfilled as if captured during their original run.

This boundary alone does not implement working-result roles or complete environment recovery.
Roles require an independent working pointer and append-only observations, separate from the
active review view. Explicit promotion, deliberately cleared roles and conflict decisions must
land together with their migration/writer/export hooks in a later reviewed slice.
