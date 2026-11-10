# Comparison alignment review

20 October 2026. User reports weak matching between transcription methods and asks to match
specific wording. T22 now replaces the original heuristic; the findings below describe the
previous implementation. See [the accepted decision](decisions/0013-whole-transcript-wording-comparison.md).

## Findings from alignment.py

- The matcher makes local greedy choices, with only three passages of lookahead and groups
  of up to three passages. A larger omission or a very different segmentation can leave the
  remaining transcript out of step.
- For long passages it compares only the first and last 160 tokens, discarding the middle
  from scoring. Distinctive wording in that middle cannot anchor the match.
- A shared time interval can yield an accepted match with little wording in common. Timing
  is useful supporting evidence, but should not establish a wording match on its own.
- Repeated short expressions can attract false matches. The result exposes only a matched
  flag, with no confidence, anchor words, or explanation.
- Display alignment is at passage-group level; exact matching phrases within a group are
  not shown. Thus visually paired passages can still make comparison hard.

## Implemented replacement

1. Tokenize whole transcripts while retaining token-to-passage and character offsets. Normalize
   Unicode, case and punctuation for matching while preserving all displayed text and Danish
   letters. Do not discard the middle of long passages.
2. Find distinctive shared words and short phrases across the full transcript. Prefer rare
   multiword anchors over repeated fillers. Select an ordered chain of anchors so a later
   exact phrase can resynchronize after a long omission.
3. Align the intervening token sequences with standard-library sequence matching. Partition
   long sequences with an ordered chain of unique three-word phrases. Derive passage groups
   from accepted token links, rather than limiting
   comparisons to identical sentence boundaries or three-passage groups.
4. Require distinctive textual support. Timing alone does not create links; repeated fillers
   remain unmatched and partial groups have a review note. Never manufacture word timings or
   edit saved transcripts.
5. Show matched phrases and wording differences inside aligned rows. Expose confidence/uncertain
   groups and highlight a shared literal search across both results while keeping rows aligned.
   As of 21 October, automatic alignment replaces the manual occurrence-selection form; see
   [ADR 0022](decisions/0022-automatic-comparison-search.md). The local phrase-anchor API remains
   available for existing clients.

Exact/fuzzy wording should be the initial scope. Semantic paraphrase matching is a separate
question and must not obscure literal transcript differences or send text to a remote model.

## Acceptance cases for T22

- Exact distinctive phrase in the middle of a long untimed transcript.
- Identical content split into more than three passages on one side.
- Ten or more omitted/inserted passages, followed by a unique shared phrase.
- Repeated Danish fillers followed by a name or other distinctive phrase.
- Danish Unicode, case/punctuation changes, spelling errors and ASR substitutions.
- Two different texts with identical timing remain uncertain/unmatched.
- Every source passage remains visible, with monotonic ordering and no duplicated assignments.
- API-supplied anchors remain local/read-only and are checked against the requested results.
- Browser comparison starts aligned; searching highlights both panes without hiding context.
- Synthetic long-transcript performance; representative hour-long interviews remain manual work.

Eleven focused unit/API cases and the comparison Chromium workflow pass, including the
original four reproduced failures and explicit phrase anchors. A 2,000-passage synthetic check
took about 0.32 seconds locally. Representative reviewed Danish/English interviews remain
unassessed. The current FLEURS set has short single-speaker clips and does not cover these
comparison failure modes. This remains literal wording matching, not semantic paraphrase matching.
