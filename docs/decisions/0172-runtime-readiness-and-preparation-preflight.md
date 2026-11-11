# 0172 — Runtime stamp proof and retained-lock preflight

11 November 2026. User prioritizes CBS Runtime stability before PII-Tracer. This slice fixes
confirmed host/supervisor disagreement without reclaiming the historical preparation lock or
activating the disabled generation prototype. Live UCloud/Linux acceptance remains separate.

## Confirmed defect

Host `enhancement_api.stamped()` accepts a runtime folder plus any `stamp.json` path. The
supervisor checks the requirements digest. A synthetic Store with a known running legacy
reader and a present wrong-digest stamp bypassed the host reader fence while the shell
selected its install branch. Installation/flock were stubbed; this does not prove the cause
of the user's historical failure. A separate source audit found that the disabled generation
prototype's settings row 9 collides with shipped local concurrency; do not activate it.

## Decision

Host and supervisor must recognize the same exact canonical stamp bytes already produced by
the existing writer: `{"requirements_sha256":"<the pinned requirements SHA-256>"}`.
Host checks require the API-reported FILE stamp and DIRECTORY runtime kinds and reject
explicit link flags. Released storage metadata does not guarantee physical lstat/no-follow
proof; only the shell proves its POSIX stamp is regular and non-link. State that limit rather
than presenting host metadata as physical proof. Reject malformed,
wrong/truncated/oversized content, whitespace/extra fields, wrong digest and wrong file kind
as unprepared. Read only bounded stamp bytes through a short, bounded transfer path; do not
reuse the existing 120-second generic download unchanged for a Settings/readiness probe.
Keep transport/metadata uncertainty distinct from a definite missing/invalid stamp. An
unavailable check never grants readiness or permission to prepare destructively.

Before a new submission whose actual computed/frozen shell flag is PREPARE=1 (including
model preparation when the base stamp is valid), refuse if its exact `.prepare-lock` path
already exists or a known legacy reader is active, independently of stamp validity. Return
a curated Activity diagnostic with no run-file upload or job-submission POST; read-only
download-ticket POSTs remain legitimate observations. Cover singleton enhancement, shared
Runtime batch and standalone preparation paths. PREPARE=0 cached readers remain usable,
and same-allocation output recovery must
not be blocked by this new-submission check. Absence is only a racy observation: shell
exclusive lock acquisition, `.in-use`, known legacy-reader refusal and owned release remain
authoritative. A lock appearing after preflight must still cause safe shell refusal.

Never infer reclaim authority from age, terminal owner, a missing Activity card or a successful
metadata listing. Do not remove locks/cache, rewrite frozen jobs, alter generation settings,
resubmit uncertain jobs or claim all ASR/speaker caches ready from a base stamp. Existing
requirements/model pins and immutable producer evidence remain unchanged.

## Gate and remaining recovery work

Add durable synthetic host/shell stamp-agreement tests, unknown-read/deadline/resource tests,
legacy-reader fences, all three pre-POST paths with zero uploads/job-submission POSTs on
retained lock, cached ready/recovery cases and a lock created after preflight. Keep every existing lock ownership
and cancellation invariant. Mock network, downloads and installation; never prepare live.

The historical retained lock still needs safe recovery design and real acceptance. A future
explicit immutable-generation recovery must first resolve the prototype's persistence clash,
freeze generations at all admission/retry/follow-up boundaries and prove promotion/ownership.
This preflight fix is not permission to enable that prototype or to claim Runtime acceptance.
