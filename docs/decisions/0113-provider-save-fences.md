# 0113 — Fence provider saves before policy and credentials

Accepted 30 October 2026. Refines provider configuration writes without a schema migration.

A legacy default-provider save ignored its supplied version and could restore a stale Protected
approval. Re-reading the mutable default could also retarget a request; writing setup credentials
before validation let rejected saves change credentials. Settings-open responses could overwrite
newer drafts. Synthetic API and browser interleavings reproduced these cases.

Select a default provider once per save. Within the provider writer transaction, check any
supplied expected version and preserve omitted policy/model fields from that selected row.
Validate names, models and settings before credential writes; stale saves return conflict.
Guard Settings opening by request generation. Explicitly injected HTTP clients that follow
redirects are refused before requests; normal production transports already disable redirects.

Versionless legacy callers retain their established last-write behavior. Operating-system
credential stores and SQLite do not form a distributed transaction; this change does not claim
atomicity across external credential-store or database failures. Existing frozen jobs retain
their settings and policy checks.

When persistent credential deletion raises PasswordDeleteError, confirm the exact credential
is absent before treating deletion as successful; otherwise retain the session value and return
the existing redacted failure. A truly absent entry remains an idempotent success. Mocked
keyring and database fault tests document partial external writes and explicit retry; no actual
macOS credential-store acceptance is implied.
