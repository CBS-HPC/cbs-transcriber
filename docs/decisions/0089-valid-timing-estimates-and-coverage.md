# 0089 — Valid timing estimates and explicit coverage

Accepted 28 October 2026. The user chose to retain valid WhisperX timing estimates and
flag uncertainty. This changes new requests only. Frozen jobs without a timing_policy keep
the legacy score-filter behavior and exact request serialization; retries never silently
adopt today's policy. New public request builders explicitly freeze retain-estimates.

The legacy 0.3 score cutoff is not calibrated against timing accuracy. A finite, positive,
in-bounds interval remains usable under retain-estimates even when its alignment score is
low. Preserve alignment-fit hints for researcher review. Keep existing finite/bounds/order,
literal word-map and free-text invalidation checks; never interpolate absent intervals.
The score cutoff control is hidden for new jobs because it does not control their review
hints. Existing rank-based uncertainty controls remain available.

Coverage reports timed lexical words separately from complete, partial and untimed passages.
One missing word makes a passage incomplete, so a count of incomplete passages must not be
presented as a percentage of words without timing. The UI and research report use their
current valid word maps, preserving Unicode and edited-map semantics. Coverage establishes
availability, not audio-verified timing accuracy. Comparison remains read-only.

The inspected saved interview has 8,184/11,986 timed words (68.3%) and 230/1,175 complete
passages (19.6%). Retained stage receipts explain the filtered intervals; historical results
are not rewritten. See the metadata-only investigation in
[runtime timing coverage](../evaluations/runtime-timing-coverage-2026-10-28.md).

Automatic second-pass alignment is explicitly deferred as later fine tuning. Manual repair
and the separately planned UCloud routing remain independent work.
