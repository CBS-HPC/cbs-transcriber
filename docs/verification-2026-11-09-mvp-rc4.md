# MVP rc4 — 9 November 2026

Loaded label: **MVP rc4 02193a2**. Code commit
02193a28ffa9f86422114b2d3a947a402bafcc23; immutable package/tag
c897efaff51adfd24be6de80356a192cbd85455e / mvp-2026-11-11-rc4. The package differs from
the code commit only in catalogs/release.json. Main keeps the full profile. No GitHub Release
was published; the existing repository remains private with main as its only remote branch.

The local source ZIP is 5,440,752 bytes / 874 entries, SHA-256
749a3b9ffde06c2cf4e55bd61146552e6b9bb53cfbf732b99544f690f9ca235d.
CRC, safe paths, absence of environment files, twenty source/catalog pins and extracted-source
identity passed. Python source pins normalize CRLF to LF as the production readers do.

## Changes and verification

Includes all [rc3 editor/export/native fixes](verification-2026-11-09-mvp-rc3.md), plus the
reviewed [manual editing optimizations](decisions/0159-manual-edit-hot-paths.md): remove repeated
encoding and structural-history work, retaining fresh hashes, ownership, revision/Working
fences, exact inverse proofs, legacy response behavior and synchronous snapshots. No schema,
storage rewrite, new authority cache or inference change.

Layered author/independent checks are recorded in ADR 0159. The immutable rc4 source passed
63 profile checks (only the exact main-default assertion excluded), three MVP browser
walkthroughs and all four standalone installation smoke checks with bundled FFmpeg. All 132
source hashes stayed unchanged. Existing development/Desktop interpreters were used without
installation or synchronization. These synthetic checks are not clean-machine acceptance or
a fresh monolithic full-suite run.

Evidence: Temp/rc4-packaged-verification-nov9.json, rc4_release_profile_nov9.xml/.txt,
rc4_MVP_browser_nov9.xml/.txt and cbs-installation-rc4-nov9.json. The local Releases ZIP has
a matching JSON manifest; the actual package source is a separate immutable worktree.

## Activation and preservation

The verified idle rc3 process received Ctrl+C and exited normally. No force-stop or remote-job
control was used. Online/offline/post checkpoints matched every table, all twelve originals
and recovery metadata on schema19. Database copy SHA-256 remained
fe1062e208886bc3f7d8ce0a153776e490bf9548410fa7f9145f25b403abbf18.
The earlier intentional six native default changes to 24 hours are retained; frozen jobs and
unresolved old UCloud evidence remain unchanged.

The HTTP profile/label, current library, health and exact bytes of five served JS/CSS modules
were verified, including no-store headers. Launcher23064/listener32496 were recorded privately.
The Desktop checkout advanced to main while preserving its seven pre-existing tracked
deletions and two stashes. Server environment and credentials were not synchronized.

Private receipts: .codex/backups/cbs-transcriber/mvp-rc4-20261109T164717Z and
Temp/nov9-rc4-activation.json, rc4-http-activation-verification.json,
rc4-desktop-preservation-before.json and -after.json. The checkpoint directory's timestamp
is the local clock with a literal Z suffix; it is not an asserted UTC timestamp. The SQLite
copy and original hashes are not a complete media backup or verified restore.

## Final speed gate and remaining scope

The quiet pre-package 600-passage candidate improved save median from 3.83 to 2.57 seconds
and Undo split from 7.29 to 2.55 seconds. Two of 24 content observations remained above the
three-second investigation target (Undo merge, median 3.13 seconds). The exact activated
600-passage package recheck passed correctness and unchanged-source checks, with zero of 24
content samples above three seconds: save median 2.69 seconds, Undo merge 2.89 seconds and
Undo split 2.49 seconds. The separate exact 100-passage check also passed every oracle and
source recheck, with zero of 24 content observations above three seconds: save median 0.46 s,
Undo split 0.37 s. All 48 content observations and six review marks stayed below that target.
These small samples are not p95 or a guarantee. The [audit](audits/mvp-final-editing-speed-2026-11-09.md)
separates actual editability, primary API, render and background-settled timing.

This closes the scoped final speed gate. Spelling work may branch from the stable package,
but it must remain unmerged
for later user approval. Real UCloud/reconnect/early-result/large-batch, multi-GB export,
clean-machine, backup/restore and NVivo acceptance remain separately scoped. CBS Runtime,
anonymisation and UCloud backup development remain parked.
