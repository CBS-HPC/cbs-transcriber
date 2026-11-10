# 0052 — Opt-in realignment of edited passages as timing-only versions

Accepted 23 October 2026. Amends ADR 0016 for this one case.

A free-text edit clears the passage's word timing, because the old word map no longer fits the
text. Until now, word timing came back only through **Add word timing**, which creates a separate
result (ADR 0016, ADR 0026). During a long review this produced a trail of repair results, and
researchers asked for edited passages to regain timing automatically.

## Decision

- **Opt-in, per library.** "Realign edited passages" in the editor is stored in settings row 6
  and is off by default.
- **Queueing.** After a free-text save that changed the text, the server queues a local job
  when the active result expects word timing and the passage has bounded audio within the
  passage-repair limits. The job aligns only that passage, offline, with no downloads and no
  speakers. A still-queued job for the same passage is cancelled as superseded. Queueing never
  fails or delays the save.
- **No new result.** The worker freezes the source version as usual. It stores the realigned
  words on the job instead of appending a result.
- **Applying in the editor.** The editor applies ready timing only while no draft, dialog or
  save is open. A revision-checked request adds the words to the same result as a new version
  with action `timing_realign` (context: passage and job). The text, the model's original text
  and words, and every other passage stay exactly as they were. The passage is marked
  `words_current` and keeps `edited`.
- **Never applied, and marked stale, when** the passage's text or bounds changed since the job
  froze it, or the passage no longer exists. Timing for a result that is not the active one
  waits until that result is active again.
- **Activity** hides finished realignments; failures stay visible.

## Why this does not break the no-overwrite rule

Reviewed text never changes; only an empty word map is filled for text the researcher wrote.
The change is an ordinary appended version, attributable and undoable through History.
Enhancement and repair of whole results still append separate results.

## Consequences

Each save of an edited passage can start a short local alignment on the local lane (ADR 0049).
While it is queued or running, manual local enhancement of that recording waits. Partial
alignments keep `words_current` with untimed words, which playback already supports.
