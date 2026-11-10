# 0118 — Verify library handoffs and copied project descendants

Accepted 31 October 2026. Refines existing local library selection and project moves;
no SQLite schema or persistent move journal is added.

An existing selected or automatically discovered library could disappear or be replaced
before opening, leading to a new empty or unintended library. A project move could also
lose its destination root or a copied recording before commit/cleanup and then remove the
old original. Synthetic interleavings reproduced both failures.

Bind a selected existing library and database to observed in-memory identities, then recheck
before constructing its Store. Bind new-empty intent separately; never silently adopt a
replacement. Keep an independently fenced prior-library fallback and treat inaccessible
discovery entries as unavailable rather than breaking the entire listing.

Create move destinations exclusively, record owned descendant identities and streamed content
hashes, and verify copied membership and content before committing the location and again before
removing old originals. A missing, changed or unknown descendant prevents speculative recursive
cleanup. An uncertain commit retains recoverable files; if a committed target disappears, keep
old originals and report the condition rather than claim an automatic rollback. Apply the
existing redirection policy, allowing recognized Cloud Files tags while refusing junctions,
symlinks and unknown redirecting tags.

Synthetic fault tests cover lost/replaced roots and children, library handoffs and Cloud tag
metadata. This is not a crash-atomic persistent move protocol or a guarantee against a hostile
filesystem changing continuously between checks. Actual OneDrive hydration and Mac testing
remain separate acceptance work.
