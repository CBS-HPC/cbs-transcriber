# 0080 — Runtime progress from verified observations

Accepted 28 October 2026 for batch progress clarity. This changes read-only Activity projection
and presentation, without changing worker protocol, allocation ownership or saved journals.

Count saved results only from completed members under the existing atomic result receipt.
Distinguish failed, cancelled, interrupted, preparing, uploading, waiting, running and saving.
Accepted/uploaded work is waiting until current accepted-task and running-owner evidence
actually identifies it as running. Unknown or older state remains unknown.

Expose an active file's stage only through the exact curated stage vocabulary and current
member/attempt evidence. Specific validated stopped/interrupted reasons may correct the
presentation of an older generic processing message without rewriting that historical row.
Do not display arbitrary worker text or claim that cancelled work is an ASR exception.

The stored progress number can contain a synthesized fallback and does not always prove a
measured percentage. The batch bar therefore reports saved-result count, not guessed work
completion or ETA. Stage labels distinguish recognition, alignment, speaker detection and
saving. Recognition percentage remains unreported unless a future producer explicitly supplies
bound evidence; never invert undocumented weights to invent it.

Keep expandable per-file outcomes, shared Stop/Retry/cleanup, Clear finished and retained
obligations intact. Native Transcriber and CBS Runtime keep their distinct grouping/lifecycle.
Any later measured per-stage percentage needs its own producer observation contract.
