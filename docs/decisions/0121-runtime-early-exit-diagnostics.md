# 0121 — Preserve Runtime early-exit and recovery evidence

A CBS Runtime allocation exited before any prepared input was published. An attempt to
close an unsealed owner then replaced the original failure with a misleading reconnect
message. The original remote bootstrap log was no longer available after cleanup, so its
specific cause cannot be inferred from the recorded state.

Observe bounded, curated supervisor failures before launching new member work. If an
allocation has already finished unexpectedly, seal its exact admitted local membership
before closing it; do not invent a Stop or a remote delivery. Keep only allowlisted local
stage and exception-class categories, never exception text or arbitrary type names.
Terminal remote evidence wins over generic processing diagnostics, and a disposed owner
cannot later acquire misleading interrupted/reconnect status.

Existing verified local output receipts remain eligible for normal validation and
finalization, even if later bootstrap status reports failure. Their recovery never falls
back to publication or new inference. Every dispatched waiter must be released even when
diagnostic lookup fails. An allocation-wide statement that no input was uploaded requires
evidence across all attempts, not just the current attempt. These rules do not establish
why the historical allocation exited or change cleanup consent and retention.
