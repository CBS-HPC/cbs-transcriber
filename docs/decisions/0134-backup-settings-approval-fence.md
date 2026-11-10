# 0134 — Fence backup destination approval and concurrent saves

Accepted 3 November 2026. Extends ADR 0109 without changing frozen transfer journals.

Library's backup connection choices identify individual stored provider IDs and show the
server and project. The selection controls storage credentials, not transcription models
or GPUs. Explicit approval binds to the identity displayed when Save was requested. A
changed server/project is refused; removing or converting the selected profile makes the
saved destination unavailable without rewriting it. A dirty draft loses consent if a later
connection refresh changes or removes that identity.

Saved backup settings use a bounded v3 envelope with a fresh random write ID on every save.
An OS operation lock serializes writes and compare-and-swap checks the exact previously read
file digest before replacing the file. Older plain and v2 settings remain readable and are
not silently migrated. The fresh ID prevents an old revision from matching after a sequence
of distinct writes returns to identical settings. A conflict requires reloading and reviewing
the destination; it never grants approval. Existing journal fingerprints still describe the
resolved destination and do not incorporate the write ID, preserving exact recovery semantics.
When the saved provider is unavailable, report the revision of the same file observation
that failed to resolve. After a conflict, reloading shows the current saved destination,
transfer origins and approval state, and
adopts its revision while retaining the unapproved draft; applying that draft remains an
explicit save and approval decision.
An unsuccessful or uncertain save disables upload and clears the draft approval. A lost
reply requires a fresh read because the save may already have committed.
New uploads, cloud restores and retention previews include the reviewed settings revision
and resolved target fingerprint; admission refuses an unseen destination. Upload and preview
recheck the exact target at the point they freeze it, and later retention Apply retains its
existing preview-fingerprint check. Previously journalled transfers keep their frozen target.
Each upload approval guard also checks the request's revision, so revocation followed by a
return to identical fields stops further transfer until an explicit new request is reviewed.
An already accepted network request cannot be recalled when approval changes concurrently;
the next guard blocks subsequent requests, and an in-flight part may already have been sent.

Synthetic tests cover held UI replies, changed identities, stale saves, serialization and
ABA. Real UCloud transfer, platform lock behavior under crashes and institutional acceptance
remain separate checks.
