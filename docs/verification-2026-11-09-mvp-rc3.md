# 9 November verification — MVP rc3

The consolidated follow-up starts from code 20b8ee7. It adds the requested editor fixes,
individual deleted-passage restoration, native progress/recovery improvements and background
ZIP preparation. Verification uses the existing environment, synthetic libraries/media and
mocked providers. No new live allocation, private upload, weight download or environment sync.

## Included changes

- Double-click opens Text. Text has Replay without a duplicate saved-text panel; timing mode
  retains its preview. Ordinary Text v2 preserves exact unchanged-word timing, uniquely matched
  replacement intervals and untimed inserts. Ambiguous edits refuse with the draft retained.
  Legacy edit/history/adoption and explicit Full realignment keep their existing semantics.
- Deleted passages can be restored individually from bounded verified history and exact current
  placement. Later edits and named speakers remain. Lifecycle barriers prevent duplicate content
  after delete/restore/merge/split or whole-version restoration. No schema migration is needed.
- Native Activity reads bounded curated processing-log observations, distinct from remote state
  and local completion. Fresh native setups request 24 hours; explicit/frozen limits stay exact.
- Supported frozen transcriber/1.12 allocations may preserve completed members while others run.
  Exact fresh processed evidence and two stable valid reads are required. Previously preserved
  raw bytes use terminal recovery. Keep one pending owner and terminal-only cleanup; later manual
  edits are retained. The deployed image-to-publisher-source mapping and live acceptance remain
  unverified; unknown application references keep terminal retrieval (ADR 0155).
- Project/recording ZIP preparation moves to Activity and survives dialog/navigation closure.
  One active preparation, three retained ready ZIPs, cancel/discard and native streamed download.
  Freeze selection/content before 202. Ordinary edits remain usable; capture/file moves/Delete/
  library switch are fenced while preparing. Shutdown drains and removes temporary artifacts.
  These are server-lifetime downloads, not restart-durable jobs or library backups.
- All unversioned JavaScript/CSS modules receive no-store, including the new editing/export
  modules. Native single-transcript export behavior remains compatible.

## Layered verification

| Gate | Result |
| --- | --- |
| Text policy/API/contained proof | 52 final cases; 228 legacy/history/adoption/structure regressions |
| Independent text correspondence | 14,641 exhaustive all-optimal oracle pairs; invalid-reuse reproductions refuse |
| Editor timing/preview/double-click | 17 focused browser checks; 14 Full and three MVP migrated legacy checks |
| Deleted-passage backend/structure | 173 distinct cases after lifecycle correction |
| Deleted-passage UI | Seven MVP cases, including drafts, Working choices and late replies |
| Restored-passage portable compatibility | One report/complete-capture/isolated-restore chain; domain transformation remains not_checked |
| Native log progress | 42 focused cases, plus independent polling/guard checks |
| Native duration defaults | 64 setup/specification/preservation cases and a UI probe |
| Incremental native outputs | Final 26-case module passed in 184.49 s; 97 prior regressions and independent retained-raw/guard checks |
| Background export backend/API | Final 22 mounted cases; broader 99-pass export gate with two platform setup skips |
| Independent export review | Four actual mounted operation/instance/snapshot tests and three response-disconnect checks |
| Background and migrated export UI | 16 distinct MVP cases; two additional legacy direct/project ZIP workflows |
| Final shared backend integration | 68 policy/duration/portable cases passed after route integration |
| Host/instance/profile boundaries | 96 cases including four new static-module freshness checks |
| Final assembled MVP walkthrough | Five cases passed in 35.92 s; 15 source hashes unchanged during the gate |
| Static checks | Whole-repository Ruff check and format (789 files), changed JS syntax and Prettier 3.6.2 passed |

Counts above overlap and must not be added. This is scoped layered evidence, not a fresh
monolithic run of every repository test. Earlier failing fixture runs remain alongside corrected
reruns. Product regressions found during review are recorded as B384–B389 in SPEC.

Evidence is retained in the maintainer's Temp folder: free-text-retention-portable-nov9.xml,
free-text-retention-regression-nov9.xml, passage_restore_nov9_passed.json,
native_batch_logs_final42_nov9.xml, native_duration_defaults.txt,
native_incremental_final26_nov11.xml, background-export-final-gate-nov9.xml,
background-export-backend-gate-nov9.xml, browser-background-exports-passed-nov9.json,
mvp_followup_assembled_backend_nov9.xml, mvp_final_boundary_profile_nov9.xml and
assembly-final-mvp-passed-nov9.json. Portable source pins are verified after LF normalization.

## Packaging and activation

Code commit: d423eb561d6fb09401de451f052bf620d08c9092. Immutable packaged commit:
7e798db829499cce04056abae9be87b162b0054d, tag mvp-2026-11-11-rc3, label **MVP rc3 d423eb5**.
Only release.json differs from the code commit; main retains full. The local source ZIP is
5,417,816 bytes / 868 entries, SHA-256
9e63a669422ee3ee23f3b4bbbf9a7d8afd9d32cb5710d47c961210835a5314d0.
CRC and LF-normalized captured-source pins pass. Packaged verification passed 63 profile cases
(only the exact main-default-full assertion excluded), all three MVP walkthroughs, and the
standalone four-check synthetic installation/FFmpeg smoke using the existing Desktop Python.

Activation succeeded on 9 November from the separate immutable packaging worktree. The old
verified console received Ctrl+C and exited normally; no force-stop. Online/offline/post
checkpoints matched every table, all 12 originals and recovery metadata exactly on schema19.
The database copy hash remained c366eb57359fa9c379f93ee5125b82873856bb1c134061a21a6d832af9b4a707.
The HTTP label/profile, current library and exact five served JS/CSS module bytes were verified,
including no-store headers. Launcher25272/listener9628 were recorded in the private local receipt.
The Desktop checkout advanced to the code commit while retaining its seven pre-existing
tracked deletions and two stashes. Old rc1/rc2 artifacts remain unchanged; no GitHub Release.

Receipts are under `.codex/backups/cbs-transcriber/mvp-rc3-20261109T143102Z`, with packaged
evidence in Temp/rc3-packaged-verification-nov9.json and the local Releases ZIP/JSON manifest.
The SQLite checkpoint plus original hashes is not a complete media backup or tested restore.
Older unresolved native allocation evidence remains; stale RUNNING is not terminal proof or
permission to stop/dispose of output. No activation step submitted/retried/stopped a UCloud job.

After the exact activation comparison, the user's six saved native four-hour defaults were
updated to 24 through the existing revision-bound local settings API. Parsed provider rows
changed only those six hours fields; every other provider field/profile and all other database
tables, including frozen jobs, remained exact. No credential values were supplied and no remote
calls started. The separate longer-native-defaults.json receipt records this intentional change;
it is not part of the preceding identical-database activation claim.

## Remaining acceptance and next sequence

The final speed audit completed on a separate extraction of that exact immutable package with
disposable 100/600-passage fixtures. Source bytes and correctness checks passed. All measured
100-passage actions were editable within three seconds; the 600-passage fixture still had slow
saves/structural edits (save median 3.83 s, Undo split 7.29 s). Controls-ready, primary-API,
render and background settlement are reported separately. A separate performance branch now
targets redundant response serialization, payload verification/encoding and inverse-history
work. The immutable rc3 package and running app remain unchanged while those fixes are reviewed.
Spelling waits for those blocking delays and their recheck; it has not started.

Spelling work is separately authorized on a branch from the stable release after this audit.
It must remain unmerged until the user's later approval. CBS Runtime, anonymisation, UCloud
backup acceptance and the broader editor redesign stay parked. Real clean-machine/large-media,
native early-output/reconnect/throughput, local backup/isolated restore and NVivo acceptance
remain separate from mocked or synthetic verification.
