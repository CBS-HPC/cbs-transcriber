# rc3 word-timing and recording-list verification

The local rc3 composition includes the reviewed word-timing child and the recording-list
fix. The evidence here uses isolated synthetic libraries and the existing Windows
development environment.

## Word timing

- Source `07d9bbd` passed 270 focused backend cases and 74 final-source Chromium
  cases (20 MVP, 54 full). Independent review confirmed the revision and Working fences,
  verified word-map behavior and unchanged portable source pin.
- Source `20cf9b0` composed that editor with the Windows backup and Runtime
  preparation fixes. The combined backend gate passed 267 cases, with 623 source and
  test hashes matching before and after.
- B585 reproduced a typed start of 1.2 s returning to the saved 1.0 s after a transient
  IME split. Source `12aeabe` retains typed times until composition settles and waits to
  save them. All six affected MVP Chromium cases passed. The patch in the combined
  branch is `d89213d`.
- Native-device IME, screen readers, Firefox, macOS and the colleague's acceptance are
  separate checks.

## Recording list

The list request loads project existence, ordered rows, export eligibility and source
readiness through one read-only SQLite snapshot. Readiness still validates the bounded
inspection cache and job and excludes archived ownership. The single-recording lookup
keeps its existing path.

| HTTP GET on one synthetic library of 1,204 recordings | Times | SQLite connections |
| --- | --- | --- |
| Source `1267884` | 12.629 s and 10.914 s | 1,206 per request |
| Source `1505ef0` | 0.602 s and 0.410 s | One per request |

All four canonical responses matched. The benchmark uses the in-process HTTP route and
includes response serialization. It covers uninspected recordings. A separate mixed
fixture verified ready, failed, pending, running, uninspected, corrupt cache, corrupt
job and restored-history reads. All 134 focused inspection, scheduling, API, collection
and export-selection cases passed. The combined branch uses `fcd5ce7`.

## Combined gate

Frozen source `34e8bc6` passed 191 selected backend cases and 22 MVP Chromium cases.
Each gate verified the same 642 source, test and script hashes before and after.
The groups cover the combined app routes, word-timing editor, project export controls
and verified readiness projection.

A separate pre-cutoff browser gate on `e7d245e` passed all 23 selected cases,
with the same 642 source, test and script hashes before and after. These cover
the MVP profile walkthrough, local backup in Activity, project deletion, background
exports, change history and timing Working warnings. The full-profile test runner
used explicit MVP fixtures for the release-profile cases.

## Matched browser check

Four trials per variant compared `d89213d` and `fcd5ce7` in alternating runs on the
same 1,204-recording fixture. Frontend bytes were identical. Medians and observed ranges
follow. The first Activity render settled after two animation frames.

| Stage | Before, median [range] | Fixed, median [range] |
| --- | --- | --- |
| List HTTP, response body included | 7.56 s [7.31–7.67] | 0.37 s [0.31–0.49] |
| Enabled controls and all cards | 8.59 s [8.49–8.68] | 1.38 s [1.17–1.60] |
| Initial Activity settled | 9.81 s [9.61–9.92] | 2.46 s [2.25–2.94] |
| Search input to matching card | 64 ms [63–66] | 65 ms [56–80] |

Each of the eight list responses had the same exact 719,998 bytes. There were no
browser errors or failed local requests. Post-response rendering and search times
had overlapping ranges. CPU busy across navigation varied between 74% and 98% on
this Windows PC. The improved controls-ready time measures the combined result
of the API and the existing page, on this fixture. Larger or different-device
acceptance stays separate. See the [synthetic scale check](verification-2026-11-23-rc3-scale.md).
