# 0013 — Whole-transcript wording comparison

Accepted: 20 October 2026. Supersedes the matching heuristic in 0011; its original-element
layout, read-only comparison and recording-scoped access remain in use.

The three-passage greedy matcher loses alignment after long omissions, ignores distinctive
wording in long passage middles and can accept unrelated texts on timing alone. Compare all
normalized words, retaining their original passage IDs and Unicode character offsets. Use
standard-library sequence matching; on long transcripts, an ordered chain of unique exact
three-word phrases divides the sequence into smaller gaps. This avoids a new inference or
alignment dependency. Require distinctive wording for automatic passage links; repeated fillers
and overlapping audio intervals alone do not establish a match.

Build consecutive display groups from accepted word links, preserving every source passage
exactly once and in order. Return matching/different text spans and strong/partial/unmatched
display confidence. These are heuristic wording judgments, not calibrated probabilities or
semantic equivalence. Case and punctuation are ignored; spelling substitutions stay visible.

Allow a user to search an exact normalized phrase and select one occurrence in each result.
The anchor constrains only this browser's display alignment, is validated against both current
results, and is discarded on result/revision changes. No database schema or transcript mutation
is needed. API operations remain scoped to the recording; no text is sent to external services.

The UI wraps existing text nodes, retaining playback buttons and editing handlers. Matching
wording is underlined and differences have an amber background; partial groups have an explicit
review note. Playback highlighting remains based solely on actual supplied audio timestamps.

Validation covers long omissions, long passage middles, more than three splits, repeated Danish
fillers, Unicode, phrase anchors and isolation. Chromium checks word marks, phrase selection,
vertical groups, resizing, linked scrolling, editing and search. A synthetic 2,000-passage
comparison took about 0.32 seconds locally after phrase partitioning, compared with about
6 seconds using unpartitioned sequence matching; this is not an hour-long interview benchmark.
Representative reviewed Danish/English interviews still need manual assessment. Very repetitive
or paraphrased text may remain unmatched, and explicit anchors must be chosen with care.
