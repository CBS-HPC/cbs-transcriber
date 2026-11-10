# Transcription-start observation — 28 October 2026

The user reported that clicking Transcribe did nothing, then confirmed that it works again.
The original cause is unconfirmed. No source fix or app restart is attributed to this change.

Read-only metadata checks found the running backend healthy and idle, the selected model
enabled, and no known queue/capacity/readiness blocker. A temporary fresh browser view showed
the intended behavior: no selected recording disables Transcribe selected; selecting one
enables it. No transcription request was submitted to the real app.

Six isolated Chromium diagnostic cases then used the exact `c3c94e6` frontend assets with
synthetic stores and intercepted transcription requests. Bulk and individual clicks reached
their requests and handled acceptance, rejection and preparation confirmation without a
JavaScript exception or stuck busy control. The six cases passed; ignored evidence is
`test-results/start-transcription-c3-diagnostics.xml`.

The temporary pinned-asset diagnostic was removed after recording this result. It depends
on historical Git objects and is not an appropriate permanent test prerequisite for a copied
source installation. Existing current-frontend regressions remain. This evidence does not
prove what happened in an older browser state or establish successful UCloud inference.
