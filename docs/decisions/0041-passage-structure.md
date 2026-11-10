# 0041 — Manual passage structure and exact history

Accepted 22 October 2026.

Researchers need to correct passage segmentation without transcribing again. Merge and split
are explicit edits of the active saved result. Each operation checks result identity and
revision, then atomically updates its recording projection and appends an exact history version.
Every unselected passage remains unchanged. Reports classify both actions as manual review.

Merge accepts only adjacent passages, inserts one explicit space and retains the earlier ID.
A differing speaker requires an explicit existing label. Bounds must be justified by both
source passages; a missing source interval does not borrow its neighbor's interval. Real word
intervals may remain individually usable without claiming complete word timing.

Split uses a Unicode code-point cursor. Complete word maps split only between actual words,
retaining their original intervals and silence gaps. Without complete word mapping, the user
must supply a playback position strictly inside actual passage bounds. An untimed passage can
split into untimed halves. There is no interpolation. The later half receives a unique ID.
Current text concatenates exactly to the pre-split text, including whitespace and Danish text.

Originals are split only when their mapping to current words is demonstrably one-to-one.
Otherwise each half gets its own current-text restore baseline; the exact earlier source and
original mappings remain in history. This prevents Restore original from reviving an unrelated
full-passage map. History export can retrieve the earlier structure; whole-version restore UI
remains separate roadmap work.
