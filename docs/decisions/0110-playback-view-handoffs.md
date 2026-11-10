# 0110 — Keep playback handoffs and follow scrolling in the current view

Accepted 30 October 2026. Refines playback view behavior without changing saved transcript
timings or result history.

An asynchronous Audio/Video switch could resume playback after navigation; a short viewport
could place the active word under the floating video; and mirrored comparison scrolling could
interrupt keyboard scrolling. Synthetic browser reproductions showed each failure.

Bind metadata handoffs to the current recording, view and handoff generation; navigation and
new switches invalidate earlier work and remove pending listeners. Set the selected media's
initial position, speed and play state before waiting for metadata. Subsequent seek, speed,
play, pause or Replay controls belong to the selected media and must never be overwritten by
stale saved handoff values. Expected aborts cannot pause newer playback. Preserve bounded Replay.

Constrain floating video so the transcript retains a usable visible area above the player in
short windows. Keep transcript panes focusable and scrollable by keyboard; mark actual mirrored
scroll changes so comparison synchronization does not echo and interrupt native scrolling.
These are display and playback controls, not inferred or edited word timestamps.

Verification uses generated media and synthetic Windows Chromium viewports; real macOS and
other-browser acceptance remain separate.
