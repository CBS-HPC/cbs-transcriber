# 0137 — Manually edit a word's timing

Accepted 4 November 2026.

A `timed_word` passage edit changes one mapped word's text and/or start and end in
one revision- and Working-choice-fenced save. Both bounds must be finite, nonnegative,
strictly ordered, inside known passage bounds and monotone against the other words.
Reject ambiguous passage IDs, stale/inconsistent maps and invented missing passage bounds.
Preserve untouched passage and word data, originals, speaker, source evidence and the
partial/complete status. An unchanged interval does not become manually timed. Never infer
timing for other words or promote a partial map to complete.

For a current partial map, `mapped_word` permits a spelling-only correction even
when the word or passage has null timing. It validates the exact lexical mapping,
retains every timing value and provenance marker, and preserves completeness and
the original baseline. It uses the ordinary word-correction history action.

The version action `word_timing_edit` is a manual edit. A durable per-word `manual_timing`
marker distinguishes adjusted intervals from source-model timings. Affected alignment-fit
word hints are suppressed, while transcription-confidence evidence and source artifacts remain
unchanged. Merge, split, restore and speaker-only enhancement preserve marker provenance;
later in-place realignment of marked maps marks its output alignment hints stale rather than
misidentifying model timings as manual. Untrusted alignment output cannot assert markers.
Existing timing-map fences prevent a pending alignment from overwriting a newer edit.

Legacy same-result realignment of an unmarked passage can already leave older alignment-fit
hints attached. This decision does not change frozen jobs or claim to repair that pre-existing
case; a separately versioned policy and compatible recovery design remain necessary. Suppressed
individual timing hints are not yet counted separately in the editor's edited-passage summary.
