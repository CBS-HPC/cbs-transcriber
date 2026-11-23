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

Browser layout measurements and larger or different-device acceptance stay separate.
See the [synthetic scale check](verification-2026-11-23-rc3-scale.md).
