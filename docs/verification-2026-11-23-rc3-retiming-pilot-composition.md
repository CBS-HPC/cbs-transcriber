# rc3 retiming pilot composition

The isolated local composition `3bdee7e` merged retiming safety source `2fcc730` with
the rc3 editor source `e7d245e`. The resulting tree matched a separate merge-tree
comparison. Each of the 641 tracked source and test file hashes matched before and
after verification.

| Synthetic gate | Passed |
| --- | ---: |
| Retiming, capture, atomic Apply, legacy dispatch, manual timing and Undo | 170 |
| Typed word-time saves | 20 |
| Cross-feature stale timing draft | 1 |
| Chromium retiming eligibility, timing drafts and Undo | 19 |

The cross-feature test used real local API admission and a fake aligner. After retiming
Apply, a previously opened edit-v4 timing draft was refused with HTTP 409. The exact
recording and its history were retained. Reopening at the current revision allowed the
timing edit while preserving the retimed sibling. The focused Python, JavaScript,
format and Git whitespace checks also passed.

The fresh policy retains the alignment-fit and search-window limits and the separate
saved-passage bounds. Existing plans keep their original behavior. These checks show
synthetic coexistence and data preservation. A concrete audio example is still needed
to judge whether the corrected timing matches the spoken words. The composed pilot
stays on its own local branch pending that acceptance.
