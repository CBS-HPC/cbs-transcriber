# 0132 — Staged project settings

Project name, classification, default language and storage are drafted in Project settings.
Cancel discards the draft; Save applies changed values. A storage move still requires explicit
confirmation and retains the existing copy-before-switch, active-job and ownership safeguards.

The existing endpoints are separate operations, not a transaction. Save applies the move last;
if a prior request fails or its reply is lost, the dialog reports confirmed successes and that
other outcomes may be unconfirmed. Before dispatch, the browser stores a per-project warning;
only a confirmed response clears it. An unconfirmed request may still finish after a refresh,
so the warning survives page reload and requires explicit acknowledgement before another Save.
This browser-local warning is not a server-side operation receipt or cross-browser guarantee.
It never claims that a committed move was rolled back. Name and classification accept optional expected
old values checked atomically with their writes; storage accepts the expected old path under
its existing queue/snapshot exclusion. Language retains its existing revision check. Legacy
clients without expectations keep their behavior. A stale draft receives a conflict instead
of silently overwriting a newer value.

Verification uses synthetic projects and mocked failures. No live library move is needed.
