# 0170 — Deactivate direct OpenAI while retaining compatible transport

11 November 2026. User-authorized post-MVP implementation on `codex/post-mvp-morning`.
No main integration or installed-app change. Extends ADRs 0145 and 0167 without deleting
provider history, credential evidence or the compatible SDK/client implementation.

## Decision

The direct OpenAI service is unavailable for normal setup and new transcription. A shared
predicate identifies provider family `openai` or an actual parsed endpoint hostname equal
to `api.openai.com` after case/IDNA/trailing-dot normalization. Custom/vLLM labels cannot
bypass the official-host check. Non-OpenAI compatible endpoints keep their existing protocol
implementation; this does not expose the separately deferred Custom/UCloud vLLM UI.

Refuse fresh selection/default assignment, shared admission, provider Save and model checks.
Use the existing curated ValueError/HTTP400 path, without secrets or a new schema/setting.
Model-check refusal precedes credential lookup and connection-check observation changes.
Public direct profiles remain readable but project `connection_ready=false` and
`connection_state=service_disabled` without rewriting saved configuration/check receipts.
The normal Settings tab/model choices and new-run selectors omit the direct service.

Enforce the same predicate at the actual model-list and audio-request network seams, before
SDK/client creation and before opening new audio. Keep the OpenAI-compatible SDK, request
formats, parser, catalog/history labels and explicit nonofficial endpoint support intact.
Do not put an unconditional refusal in require_key, require_approval or JobQueue.process:
those paths precede legacy raw-response finalization as well as actual requests.

## Existing work and recovery

There is no startup scan, silent cancellation, retargeting or credential/result deletion.
Already accepted requests drain under the existing executor rules. Stop remains available.
Same-ID Retry and worker processing may finalize complete local raw responses/chunks without
a provider call. A missing/new part that would contact the direct service fails locally with
a clear disabled-service message; accepted parts, raw evidence and results remain. This
guarantees no new direct call after activation without inventing a new cache-proof mechanism.
Frozen request settings and historical captures are not rewritten.

## Gate

Verify family/host normalization and custom-label bypass refusal with zero SDK/audio access;
all fresh create/update/default/selection/admission paths; check-state/credential immutability;
queued and partial Retry with no outbound request; complete legacy raw and fresh chunk local
finalization; accepted-request drain/Stop; and a nonofficial compatible MockTransport request.
Native UCloud and existing result/history/export access must remain available. Existing tests
that exercise a generic protocol must use an explicit synthetic compatible endpoint rather
than bypass the guard globally. No paid calls or live provider tests.
