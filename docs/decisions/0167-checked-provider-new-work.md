# 0167 — Checked connections for new transcription

10 November 2026. Post-MVP branch only; the installed MVP remains unchanged.
Extends connection/model enable flags (ADR 0042) and Settings-save fences (ADR 0113).

## Decision

New transcription selectors and HTTP admission require an explicitly checked, saved
connection. A missing key, an unfinished native setup or an unobserved connection is not
ready merely because a preset exists. Settings remains available to complete setup.

An explicit Settings model-list/native check issues a bounded server-session opaque save
proof. The proof binds the actual resolved connection, supplied credential and check
generation. Save verifies it under the existing transaction and records the observation in
operational `settings` row 10. It is not part of normalized provider Settings, provider content
versions, job snapshots or research provenance. Public profiles expose only readiness and
its curated state; no credential fingerprint or proof is included there.

Connection identity includes provider family and endpoint, and native project, storage,
application/template, product and field mapping. Name, enable flags, Protected approval,
language/model choice, limits, hours and cleanup preference do not prove a new connection.
Model-list checks additionally bind advertised model availability. Native and CBS Runtime
may share their exact checked connection; this never establishes Runtime/model readiness,
MIG capacity or ASR capability. Changed identity or credentials require a fresh check.

No passive provider probes or arbitrary time expiry are introduced. A successful saved
observation survives restart. An explicit failed recheck may mark only the exact saved
connection/credential unavailable for new work; an unrelated draft or late reply cannot
invalidate it. Starting a newer check invalidates earlier save proofs, including resolution
from discovery input to a final connection identity. Replaying an old successful proof after
a later failure must not restore readiness.

Readiness states distinguish missing credentials, unconfigured, recheck required,
unavailable, model not listed and ready. An unknown/malformed observation fails closed.
Public HTTP admission repeats its guard at the shared acceptance boundary, rather than
trusting a selector. Existing project classification/model enablement checks still apply.

## Preservation and limits

- Frozen jobs, workers, Retry, Stop, same-allocation recovery, cleanup and saved results keep
  their existing authority. A connection outage does not hide those actions or erase evidence.
- Existing unobserved configurations need one explicit check for new work. This is not a
  silent network migration, and restores cannot import operational connection authority.
- Readiness is a past successful setup observation, not a guarantee of current availability,
  funding, inference quality, upload consent or institutional approval.
- Checks still require explicit user action. No test transcription, paid call or preparation
  is performed to establish the synthetic verification evidence.

Mocked checks cover credential/settings changes, exact-identity sharing, stale check/save
responses and fresh-work refusals. Real provider/clean-install acceptance remains separate.
