# 0043 — Current partial word mappings after structure changes

Accepted 22 October 2026. Extends 0030 and 0041.

The general `edited` flag also covers word corrections that preserve exact intervals. Merging
such a passage with incomplete timing previously hid its usable words, and splitting that
merged passage discarded them. Mapping validity must remain separate from timing completeness.

New merge/split outputs carry `words_current=true` after excluding obsolete input word maps.
Playback, further structure changes and research coverage can use those current mappings even
when `edited=true` and `word_timing=false`. Individual intervals and text offsets still undergo
their existing validation. A changed free-text rewrite removes this positive marker and disables
old word playback; Restore original returns to the independently preserved original map.

Split recomputes completeness for each half from actual lexical coverage, word intervals and
the chosen real audio bounds. A fully covered half regains complete timing; missing words and
words crossing the audio boundary never receive invented intervals. Merge likewise checks its
combined map instead of inheriting a conservative completeness flag from either source.
An unbounded parent can yield a fully located half: its first and last actual word intervals
justify that half's playback bounds. Any half with an unlocated word remains unbounded.
For unbounded passages, the playback index uses each reliable word's actual interval separately;
it never fills the unknown gaps or changes the saved passage bounds to locate the current word.

This is additive passage metadata, with no database migration. Legacy edited/incomplete maps
without the marker remain conservatively disabled, because they may come from free rewrites.
Historical versions remain exact and immutable. Already discarded intervals are not recreated;
earlier versions retain the evidence needed to recover the pre-edit structure.

An affected current structural result may be explicitly revalidated under the library lease
after an idle shutdown and SQLite backup. Bind the repair to the exact result/revision/history
hash; require an unedited map and validate its actual coverage. Change only completeness and
fully justified passage bounds, preserving every character, word object, interval and speaker.
Append an explained `manual_save` version rather than changing earlier history. Verify unrelated
tables and credentials, then compare the post-repair baseline across server restart.
