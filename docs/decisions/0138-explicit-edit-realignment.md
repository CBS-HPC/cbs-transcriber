# 0138 — Freeze realignment choice at a passage save

Accepted 4 November 2026.

The new free-text editor sends an explicit boolean realignment choice to the distinct
`edit-v1` passage PATCH endpoint. The choice governs only that save; another tab changing
the library preference cannot change it. The existing endpoint continues to use the saved
library preference and ignore previously ignored extra fields. This preserves older clients'
requests without interpreting their arbitrary extras as consent to new work.

The existing revision and Working-choice checks, opt-in local alignment queue, frozen jobs,
same-result Apply safeguards and failure handling remain unchanged. This is not automatic
second-pass alignment. Timed-word edits never queue alignment.
