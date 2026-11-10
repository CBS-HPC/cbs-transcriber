# Preserve original timing and distinguish correction from rewriting

Accepted, 2026-10-20. Keep original text/word intervals alongside editable values. A word
replacement retains its interval; a free-text rewrite invalidates word timing for that
passage until restored. This avoids presenting invented timing as alignment and allows
both user-selected editor modes; automatic realignment is deferred. Segment boundaries
remain usable for replay, and optimistic revisions prevent competing tabs losing edits.
NVivo output is an export, never the internal timing representation.
