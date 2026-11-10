# Runtime backend compatibility — 27 October 2026

The user requested that the running backend remain unchanged. Read-only Activity inspection
confirmed that it has no Runtime owner contract; selected file jobs fail during supervisor
rendering without a shared allocation. A browser refresh updates static assets only.

Before any CBS Runtime transcription enqueue, current browser code now checks the existing
read-only batch-settings route. An older backend's 404 produces an explicit restart/no-job
message before confirmation or transcription POST. The common request helper retains HTTP
status so only an actual 404 receives that interpretation. Other network/HTTP refusals keep
their original curated errors. Native UCloud and OpenAI do not require this probe.

The old-backend browser reproduction failed before the change because it queued the request.
Four Chromium cases passed after the change (46.03s): runtime 404 queues nothing, runtime 200
posts a two-file request once, and native/OpenAI use their existing paths. Evidence is in
`test-results/runtime-backend-guard-green.xml` (ignored). A read-only harness of the actual
request/helper functions also verified unchanged 403, 500 and network errors. Scoped Ruff,
Prettier 3.6.2, JavaScript syntax and whitespace checks pass.

Tests used synthetic libraries, mocked providers and intercepted enqueue calls. No real
allocation, private upload, library migration, backend restart or fake historical grouping
occurred. Activation of the verified backend remains a separate authorized step.

A final read-only check of the running app confirmed that its presets still identify the two
CBS Runtime models with engine `whisperx`, while the batch-settings route returns 404. Thus
the guard's tested model/route condition also matches this older live backend. No enqueue
request was made during that check.
