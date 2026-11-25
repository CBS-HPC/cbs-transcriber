# Night composition verification, 20-21 November 2026

Local branch: `codex/night-post-v2-20261120`. Final tested product/freeze:
`f96362fd608dbc4b65977f04d52020a7339968ff`. Later documentation commits retain these exact
product/test bytes. Main, Claude's original branches, the rc2 package and the running preview
were preserved. No publication, push, app reload, environment sync or live/paid UCloud work.

## Implemented and reviewed

The candidate composes Claude's `fec9273` with project-deletion checkpoint `8cad1eb` and
derived review dropdown `9b56e20`, followed by the B518 shutdown correction. ADR0190/0191,
V254-V258 and T160/T163 record the behavior.

Deletion review fixed reciprocal backup admission before Stop/copy, premature Keep responses
before actual COMMIT, Keep winning before reserved control claims, and queued deletions
advancing after global shutdown began. Keep/shutdown never wait for a decision while holding
the manager lock. Both native and Runtime lost-Trash-reply tests preserve exact consent and
recover the same folder through verified absence. Recording deletion remains legacy.

The dropdown retires the separate Review suggested tag. Display-only Uncertain wins over
Timing missing when both apply; exact-content manual marks win. Derived values are never
stored as manual states. Timing added retains Needs review and its reason. Existing filters,
uncertainty decoration, keyboard access, drafts, caret and late-reply protection are retained.

## Exact final frozen gates

| Group | Passed | Skipped | Scope |
| --- | ---: | ---: | --- |
| Backend | 448 | 2 | 24 selected deletion, shutdown, capture/restore, host/origin, review/timing and atomic Apply modules |
| Editor/review/deletion browser | 80 | 0 | 17 selected modules on the composed source |
| First-use/provider browser | 28 | 0 | Two disjoint setup/provider modules on the same source |

Exact XML node identities reconcile to **558 unique cases: 556 passed and two skipped**.
No repeated component or diagnostic runs are added. All 614 common product/test hashes match
before/after all three gates, and were re-read from disk before this receipt. The broader
setup map also remained unchanged. All three runners verified the frozen source identity.

Scoped static checks passed: all 23 changed Python files through Ruff/format, three Node
syntax checks, five frontend files through cached Prettier 3.6.2, and whitespace. Independent
source review covered deletion and the composition; the final three-line shutdown fix has
independent red/green tests and source review. Private commands, XML, hashes and review
receipts remain with the handoff. Synthetic libraries and mocked/loopback providers only.

## Corrections and qualifications retained

- An expanded component run hit the unchanged Windows path-budget guard in ten successful
  portable scenarios. The complete 30-case module passed from a shorter synthetic base with
  source/guards/assertions unchanged; exact identities reconcile its component result (B516).
- The spelling overlay runner initially exposed cached Spylls only to the child, leaving
  the host availability check unsatisfied. The untouched base failed the same case. Correct
  test-only host and child visibility of existing Spylls 0.1.7 produced the passing 58-case
  component gate and final browser gates, without installs or guard bypasses (B517). Normal
  installed-worker, real spelling quality/licensing and clean-machine acceptance remain open.
- The first composition at `8aae11e` passed 445 backend/two skips plus 108 browser cases. A later
  deterministic queued-shutdown proof still failed; B518 was fixed and the full selected
  backend/browser gates above were repeated on `f96362f`. Earlier receipts are preserved.

## Separate work

This is focused local source verification. The whole backend/browser suites, real H24
Windows/macOS/Cloud Files/held-file/crash/UCloud acceptance, real models, rc2 installation/
shortcuts and colleague usability acceptance are separate. The running app remains the
existing MVP v2 preview1, not this candidate. No replacement package was built or loaded.

The wider-window timing analysis remains proposal-only: changing search alone cannot change
bounded passage timing. The user must choose whether repair may correct saved passage bounds.
The cursor prototype remains memory-only/off. Background backup Activity is still design
work; only its common capture/deletion admission prerequisite was implemented.
