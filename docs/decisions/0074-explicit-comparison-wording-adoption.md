# 0074 — Explicit comparison wording adoption

Accepted 27 October 2026 for T43f. This extends explicit researcher review, without changing
the working-result/alternative model or creating a persisted suggestion/rejection workflow.

A one-to-one comparison row can offer Use wording as a candidate action. Wording alignment
is heuristic and supplies no consent or timing evidence. Preview must resolve the actual
current source and destination versions, show their full literal wording and passage positions,
and require an explicit Apply. Ambiguous multi-passage rows offer no guessed adoption action.
Opening the preview does not seek, change text or create jobs.

Only another live saved result in the same recording may supply wording. Resolve both passage
IDs uniquely. Reject empty/whitespace source text and exact-text no-ops; otherwise retain exact
Unicode and whitespace. The server computes edit_segment(destination, free, source_text).
Destination ID, audio bounds, speaker, originals, retained old words, unknown fields and every
unselected passage stay intact. New text invalidates current word timing. Do not copy source
timings or automatically queue alignment, even if ordinary editor realignment is enabled.

Preview owns one read transaction. Its fingerprint binds the recording revision, both live
result/head identities and canonical hashes, selected passage IDs, exact text hashes, policy
and proposed payload. Apply recomputes under one writer transaction and atomically updates
both projections and appends wording_adopt. Replayed/stale previews refuse; rollback changes
nothing. Source, jobs and independent notes remain unchanged.

The two explicit adoption/merge services share only read_pair and append_edit safety helpers;
each retains visible transaction, fingerprint and post-commit snapshot ownership. Preserve the
producer source_version_id and reviewed predecessor. Record a strict server-built source
reference and passage/text hashes in wording_adoption context. Research reports must verify
source ownership/hash and reconstruct the exact successor from its predecessor and source.
Serialize version/passage aliases, not arbitrary passage IDs, text, names or reasons.

The modal preserves draft/navigation guards and requires Refresh after a stale Apply. An old
backend gives restart guidance rather than falling back to an untracked generic text edit.
Historical versions remain restorable; later source Trash preserves historical evidence.

This stage records accepted wording. Long-lived suggestions, rejection logs, multi-passage
adoption and automatic model promotion remain separate T43 decisions. No inference, remote
processing, schema migration or institutional research-compliance claim is introduced.
