# 23 November 2026 — rc3 editing and guidance verification

The local rc3 candidate source is `034e0064652bd2672c83782c2b96d325e9b8d5ca`, from `claude/post-v2` `7c88efd`.
It includes the exact server changes, shorter guidance and the empty timing capture fix. Those paths were exercised together.

| Scope | Source | Result |
| --- | --- | --- |
| Server editing, history, Working roles, portable semantics, backup and related routes | `8d7c9a1` | 581 backend cases passed across 28 modules |
| Separate browser editing experiment, playback, comparison, review, timing and Undo | `f36a934` | 134 browser cases passed |
| Guidance, backup, portable settings, deletion, provider setup and export | `ff7483a` | 127 browser cases passed |
| Timing, profile, shortcut and wheel-constraint checks on the candidate | `034e0064652bd2672c83782c2b96d325e9b8d5ca` | 183 backend cases passed, one inherited main-profile assertion failed |
| Candidate composition, including editing, review, Working, provider setup, deletion, backup and export | `034e0064652bd2672c83782c2b96d325e9b8d5ca` | 140 browser cases passed, 0 skipped |

The profile assertion expects the packaged source to say `full` on main. The parent already
carries `MVP v2 preview 1` with the `mvp` profile. The same test fails on the parent’s exact
profile, resolver and test bytes. The independent CLI check writes the proposed `MVP v2 rc3`
label to a scratch file and refuses an unknown profile while preserving that file and the
candidate's packaged metadata. The release gate still needs to apply the intended profile
expectations for its branch.

The browser runs verified unchanged application and test file hashes. They used the existing
Windows Python and Chromium 156.0.8078.4, a cached Spylls package for the full-profile
browser fixtures, disposable libraries and loopback services. The performance fixtures were
run with the MVP profile, identical seeds and a 400-word middle passage. They report medians
from four Save samples, three structural samples and two recording opens per size.

## Exact server changes

Controls-ready medians in seconds for `7c88efd` → `8d7c9a1`, with the same browser code.

| Passages | Save | Split | Merge | Undo merge | Delete |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 600 | 2.502 → 1.900 | 2.556 → 2.273 | 2.923 → 2.062 | 3.141 → 2.389 | 3.572 → 1.940 |
| 1200 | 6.987 → 3.962 | 5.375 → 3.840 | 5.815 → 4.812 | 6.059 → 4.523 | 6.102 → 4.136 |

At 1,200 passages, the measurements also separate the primary server request, transcript
render and browser settlement.

| Action | Server request | Transcript render | Browser settled |
| --- | ---: | ---: | ---: |
| Save | 4.994 → 2.536 | 0.543 → 0.348 | 9.824 → 5.991 |
| Split | 3.529 → 2.251 | 0.446 → 0.328 | 10.123 → 5.829 |

Stored versions, revision and Working checks, exact timing and project snapshot behavior are
covered by the focused backend cases. See [ADR 0193](decisions/0193-exact-editing-performance.md).

## Separate browser experiment

The second sequential pair held the server code constant, comparing `8d7c9a1` → `f36a934`.
Typing is one 39-character Playwright workload with a 15 ms target delay and includes browser
dispatch. All times are in seconds.

| Passages | Recording controls ready | Type in short passage | Type in 400-word passage | Save controls ready | Split controls ready |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 600 | 1.942 → 2.434 | 2.543 → 2.989 | 3.561 → 3.514 | 3.250 → 3.066 | 3.300 → 3.538 |
| 1200 | 2.993 → 3.670 | 3.645 → 1.894 | 5.010 → 7.272 | 11.033 → 8.547 | 11.589 → 8.050 |

The experiment shortened short-passage typing at 1,200 passages and lengthened the 400-word
typing workload. It stays on the separate browser branch. Absolute times varied substantially
between rounds on this PC. Compare each A/B pair within its own round. Larger histories,
repeat runs on representative hardware and installed-build
acceptance are still open. The [pilot guide](testing/colleague-test-guide-0.2.0rc3.md) was
checked with the Windows PowerShell parser. Actual wheel installation, startup and shortcut
acceptance are release checks.
