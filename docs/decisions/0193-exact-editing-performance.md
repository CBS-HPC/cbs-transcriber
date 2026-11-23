# 0193 — Exact editing performance on long transcripts

Extends [ADR 0159](0159-manual-edit-hot-paths.md). Preserve existing history limits, revision and Working checks, review states, and synchronous project snapshots.

When reading an already verified canonical history payload, decode its passages into fresh objects and reuse the canonical JSON slices for exact comparisons and fingerprints. Re-encode any value without a verified slice. Keep the slice registry local to the request and release earlier rows during scans. Verify the live bytes and bindings on every use that requires their proof.

A saved-recording response can share its newly decoded passage and speaker mirrors with the project snapshot if the snapshot reads exactly the same stored JSON. Project JSON can serialize an identical shared value once and preserve the usual indentation at both positions. A conflicting layout or placeholder collision uses normal serialization. The snapshot still completes before the response. Existing refusal, review and restore behaviour stays authoritative.

Measure controls ready, the main API call, render work and background settlement separately on matching synthetic Windows/Chromium runs. Keep sample counts and commit IDs with the results. On long histories the existing per-passage Undo scan can exhaust its verification budget. Returning fewer proven actions is safer than presenting an unverified undo.
