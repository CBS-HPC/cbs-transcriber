# rc3 synthetic library and export scale verification

23 November 2026, Windows and Chromium. The frozen product source was `b11ea3bc`.
The fixture had 1,204 recordings, 1,203 saved results, 1,800 passages in its longest
transcript and four valid 128 MiB WAVs. The library and server were separate and
local, with no transcription calls. Fixture setup coalesced repeated project
snapshots into one ordinary snapshot. That final snapshot took 11.55 seconds once.
These measurements begin with the seeded library.

## Opening a large project

| Observation | Result |
| --- | --- |
| Project recordings API, 1,204 rows and 719,998 response bytes | 9.98 and 16.29 s |
| Chromium navigation until all recording cards appeared | 21.08 and 24.24 s |
| Find a recording with the search control | 0.26–0.41 s |
| Show 1,800 passages, then highlight the requested search phrase | 6.02 s, then 0.49 s |

A separate in-process ABBA check returned the same canonical list in all four runs.
Existing route calls took 14.318 and 13.373 seconds, opening 1,206 SQLite connections
each. Borrowing a single connection for the existing verified inspection lookup took
0.234 and 0.245 seconds, with three total connections per call. The experiment used
1,204 uninspected inputs. A product change needs the same projection for valid,
failed, pending, malformed and archived evidence. Browser rendering is a separate
cost from API response time.

## Background ZIP with original recordings

The browser click to accepted task took 0.41 seconds in the completed run. The
dialog closed while preparation continued. Activity responded in 0.18 seconds
while the task was running. A separate cancellation during copying settled in
0.11 seconds, clearing that run's staging.

The finished 540,442,714-byte ZIP was ready 99.75 seconds after the click. Native
Chromium download took another 19.00 seconds, without a JavaScript Blob. The ZIP
contained 2,409 entries. Verified all 1,204 originals by length and SHA-256, all
1,203 transcript wordings, and the expected skipped transcript for the recording
without a designated Working version. The download SHA-256 was
`d1f10b1ec6d2a4c2f198b3a7527c9359329c120d26f8011ba2e6ce8c803a0478`.
No browser page errors were observed. The server process working set rose from
124.4 MB to 138.3 MB. The sampled visible temporary files peaked at 669.2 MB.
Activity discard and shutdown left no export archive. The first run had reached
1,110 recordings at the harness's 180-second wait limit, then drained and cleared
its staging on shutdown. Concurrent work and host load limit the timing estimates.

A browser automation command needed about 23 seconds to locate and close
Export Settings. The settled run measured 675 ms from the actual dispatched
click to the dialog close event. Use the latter for the app interaction.

H15b and H20 remain partial synthetic coverage. Public speech dataset ingestion,
end-to-end media preparation, multi-GB transfers or archives, different devices,
and large backup/restore/deletion acceptance are separate checks.
