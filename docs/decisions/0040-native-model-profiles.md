# 0040 — Native model profiles and two-stage selection

Accepted 22 October 2026.

A native Transcriber connection supplies the same control endpoint, project and storage for
all catalog models. Making the user rediscover those resources for each model obscured
available choices. The UI now separates provider family from model in every transcription
selector. Job payloads continue to identify exact saved provider rows.

Saving a complete fixed-B200 native profile appends missing canonical model rows in the
same SQLite transaction. Offline startup performs the same idempotent expansion while
holding the library lease, before workers start. Group by exact server/project; no network
or credential access is needed. Models come from the validated packaged/override catalog.
Existing rows and explicit Protected approvals/denials remain unchanged, including legacy
aliases. Added rows inherit the source configuration's connection and approval choice;
credentials continue to share the endpoint scope. Neither defaults nor recording selections
nor frozen jobs are rewritten. Incomplete/legacy non-B200 configurations are not expanded.

This keeps queue identity, worker policy and retries on existing contracts. It deliberately
adds ordinary profiles instead of virtual selector values requiring a second resolution path.
Concurrent saves and backfills use the database write transaction to avoid automatic duplicates.
