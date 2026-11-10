# 0058 — Untimed passages are searched between their timed neighbours

Accepted 23 October 2026. Amends ADR 0056 and the passage repair rule "never infer bounds from
adjacent passages".

When alignment cannot place part of a passage whose source had no bounds, it leaves untimed
fragments with no start or end. Until now they showed a separate "No word timing" tag and
could not be timed on their own. The researcher asked for one "Add word timing" tag for every
passage that lacks word timing, without any difference between them. The researcher also asked
for no yellow highlighting: the tag alone marks the passage, as for uncertain passages.

## Decision

- **One tag.** Every passage lacking word timing in the open result shows "Add word timing".
  The read-only comparison shows no timing tags; its note names the count.
- **Search window, not timing.** For a passage without its own start and end:
  - It is aligned together with the adjacent untimed passages in the same run of passages.
  - The search covers only the audio between the end of the nearest earlier passage with a
    known end and the start of the nearest later passage with a known start. If there is no
    such passage, the recording's start or end is used instead.
  - The window must be at most 60 seconds, and the group at most 120 words and 1,200
    characters.
  - Words are split back to their passages by character position.
  - The window only limits the search. It is never stored as a passage's timing. A passage
    gets a start and end only from its own words, and only when every one of them was aligned.
    Uncertain words stay untimed, and timed words still highlight.
- **Too long.** If a passage or its window exceeds the limits, the same tag opens Add word
  timing / speakers… for the whole result.
- **No highlighting.** Passages lacking word timing are no longer tinted or bordered.
- Unchanged:
  - A passage's own bounds are used when it has them.
  - The full-recording replay of an unlocated passage still assigns nothing.
  - Text never changes.
  - The output is added in place as one revision-checked `timing_realign` version, skipping
    passages that changed meanwhile (ADR 0056).

## Consequences

The neighbours' times bound the search, so a neighbour placed wrongly by alignment can narrow
it wrongly. In that case the aligner finds fewer reliable words and they stay untimed; no timing
is invented. Jobs record their units (`timing_units`: passage IDs and the searched window), so
the provenance of each search is kept with the job.
