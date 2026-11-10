# MVP rc7 activation — 10 November 2026

MVP rc7 60e07a9 is running at port 8765 from immutable package
`2bfb635383d8a5f191c999f0883a6801d39efc81`, code
`60e07a9b8da178c0c6f1278b562dd518fc36d55c`. Its reviewed Save/Split improvements
are merged into main and the Desktop checkout. The package remains immutable.

## Changes and verification

- Reuse bounded canonical JSON and mirror-binding facts with fresh actual-byte hashes;
  retain current ownership, revision, Working, inverse, source and cold-fallback checks
  ([ADR 0164](decisions/0164-host-canonical-json-facts.md)).
- Share exact-context hint requests and repaint decorations instead of rebuilding every
  passage a second time ([ADR 0165](decisions/0165-hint-request-and-paint-reuse.md)).
- The assembled backend gate passed 77 cases; eight browser cases and the separate native
  twelve-member preservation regression passed. Independent backend/frontend reviews gave Go.
  This is scoped evidence, not a fresh monolithic repository suite. Five pre-existing existing-
  Working tests hit the unchanged schema-18/schema-19 guard and remain a separately recorded
  limitation; these changes do not activate that guarded startup migration.
- The [matched speed audit](audits/rc7-save-split-speed-2026-11-10.md) records three observations
  per action/fixture. At 1,800 passages, controls-ready medians improve from 16.36 to 8.20 seconds
  for Save and 14.91 to 7.52 seconds for Split. At 100 passages both remain below one second,
  with slightly slower small-fixture measurements. All content/timing and source checks pass.
  The user accepts this speed for MVP; further optimization is explicitly post-MVP.

## Preservation and loaded identity

The previous rc6 process was idle: no queued/running local jobs, native batches or prepared/
active export tasks. It shut down gracefully without force. Online/offline/post checkpoints
match all database tables, recovery files and 24 originals; none are missing. Offline/post
database SHA-256 is `6c248cc4607e871a4cbaac8abc36a3477238495bb6ffce26a9e8bba7ab933518`.

The served profile/label and exact app.js, app.css, manual_review.js and deleted_passages.js
bytes match the immutable package. All four responses use no-store. The running package has
no spelling feature. The existing serving interpreter was reused without synchronization.
Private receipts are in Temp/rc7-http-activation-verification.json and the checkpoint root
named by Temp/mvp-rc7-checkpoint-root.txt; raw performance files remain in Temp.

## Real acceptance and limits

Before this activation, the user confirmed native Retry failed successfully recovered the
remaining existing output on the same allocation and cleaned owned run files to UCloud Trash.
Local receipts confirm all twelve results saved, eleven earlier result IDs unchanged and
cleanup complete. No new ASR allocation was submitted. This supersedes the failed/pending rc6
recovery status in the [earlier morning checkpoint](verification-2026-11-10-morning-followups.md).

This does not prove new transcription for genuinely missing output, live incremental download,
shutdown/relaunch handoff, clean installation, Mac or NVivo acceptance. Spelling remains parked
after MVP. A GitHub wheel release and its fresh-install verification are subsequent work.
