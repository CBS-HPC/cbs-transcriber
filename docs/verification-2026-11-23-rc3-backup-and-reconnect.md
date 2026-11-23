# rc3 backup and native reconnect verification

Windows 11, 23 November 2026. These checks used local feature source, the existing
development Python environment and an isolated full-profile library with public FLEURS
clips. The installed MVP and pilot packaging have separate acceptance gates.

## Native UCloud reconnect

On source `9575be8`, H7 (restart while queued), H8 (explicit Retry after upload interruption)
and H9 (restart during download) passed. H7 and H9 used the same UCloud allocation without
manual Retry. H8 reused the confirmed file, replaced the partial upload, and submitted the
batch once. Saved recordings kept one result and one version each. The owned folders moved
to UCloud Trash. These are three validated Windows scenarios. One additional batch completed
before the intended interruption. Recovery of an eligible genuinely missing output and the
unrelated older cleanup obligation remain open. See the
[release checklist](testing/human-release-checklist.md#h7--automatic-reconnect-after-closing-the-app-paid).

## B580 and the Windows local restore

When Windows enumerated a cold recording folder, its reported directory size changed.
The previous comparison treated the size change as a new filesystem object and refused
backup. B580 retains directory type, identity, timestamps and inventory checks, allowing
this directory-size change. Regular files keep the size and SHA-256 checks.

- Both regression cases failed on the earlier source. The fixed source at `b11ea3bc` passed
  340 tests with five Windows link-permission skips across portable transport, capture, routes,
  semantics, packages, backup approval, Activity backup and project deletion. All 619 captured
  product and test source hashes matched before and after the gate.
- The isolated live Windows app at equivalent fix `d02e87d` completed local Activity backup in
  18.3 seconds. The new local restore finished in 104.57 seconds. Prerequisites and disk size
  limit these timings to this small public-clip library.
- Compared the restored SQLite rows with the database sealed into that package, checking database
  integrity, foreign keys and the documented path and operational state rewrite. Four projects,
  12 recordings, 12 transcript results, 12 result versions and 16 note versions matched.
  Each of the 36 files (12 originals, 12 processing inputs, 12 provider responses) matched its
  manifest SHA-256. Historical jobs and native batches were archived, providers were disconnected,
  and registration occurred only in the isolated test profile.
- An isolated app instance with workers disabled opened the restored copy and served its
  four projects and all 12 recordings. The browser's switch-library workflow, removable
  and synced storage, abrupt shutdown and macOS remain open.

## UCloud backup

The attempt to resume the exact owned upload stopped at HTTP 400 because the transfer
origin was outside the approved list. No objects were verified. The local package and
the existing remote-folder journal remain available. Cloud upload, download, restore,
and retention have yet to pass this test. Do not infer transfer approval from a storage
listing or use a changed origin against the existing operation.
