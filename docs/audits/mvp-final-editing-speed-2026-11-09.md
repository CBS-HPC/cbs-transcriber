# Final rc3 editing-speed audit — 9 November 2026

Run after activation, before spelling work, against immutable MVP package
7e798db829499cce04056abae9be87b162b0054d (source d423eb5). The extracted ZIP's SHA-256 is
9e63a669422ee3ee23f3b4bbbf9a7d8afd9d32cb5710d47c961210835a5314d0.
Independent 100/600-passage synthetic processes used fifteen words per passage, three speakers
and three main observations per action. Source and correctness checks passed. No live library,
port8765, provider, model download or private recording was used.

The long fixture still misses the three-second investigation target: **19 of 24 content-mutation
samples** exceeded it. Independent review marks were zero of three, so the combined count is
19/27. The initial Temp summary's 22/27 total was an arithmetic error; raw counts and the table
below are authoritative. All measured 100-passage content mutations stayed below three seconds.

| Action | 100 passages: actual input | 600: primary API | 600: actual input | 600: background settled |
| --- | ---: | ---: | ---: | ---: |
| Save | 0.72 s | 2.64 s | 3.83 s | 5.89 s |
| Rename speaker | 0.43 s | 2.06 s | 2.39 s | 4.54 s |
| Assign speaker | 0.49 s | 2.60 s | 3.23 s | 5.70 s |
| Merge | 0.52 s | 4.13 s | 5.33 s | 10.04 s |
| Undo merge | 0.65 s | 5.02 s | 5.83 s | 8.41 s |
| Split | 0.48 s | 2.40 s | 3.14 s | 7.56 s |
| Delete | 0.47 s | 2.52 s | 3.30 s | 7.87 s |
| Undo split | 1.82 s | 6.51 s | 7.29 s | 12.15 s |
| Checked mark | 0.06 s | 0.07 s | 0.17 s | 0.30 s |

Medians, n=3. Actual input means an enabled editor accepted an unsaved draft, which was then
cancelled while confirming saved content stayed exact. Background settlement includes requests
caused by that probe; it is a perturbed upper bound, not how long typing was disabled. Rendering
also includes opening/cancelling the probe. Overlapping server spans must not be added. Three
samples do not establish p95 or another machine's acceptance.

Opening the editor remained fast at 600 passages (median 16 ms), as did Next to review (about
1 ms). These are programmatic browser checks, not physical keyboard or speech-quality tests.
Normal Text edits retained exact old intervals and left appended words untimed; Undo split
restored the exact passage; marks did not alter content/Working versions; ZIP contents passed.

## Investigation targets

- The 600-passage save response is about 1.64 MB. Primary API includes roughly 1.90 s in Store
  work and 0.42 s in the framework's recursive response serialization. Snapshot, recording and
  encoding spans are nested. First-sample browser JSON parsing was only about 11 ms.
- Undo split's 6.51 s primary request substantially exceeds its snapshot/response spans.
  A separate component profile confirms repeated payload decoding and structural inverse work
  in the shared bounded history scan. Investigate verified canonical-payload reuse and targeted
  Apply discovery while retaining fresh hashes, ownership, lifecycle, budget and revision checks.
- Keep synchronous durable snapshots and atomic history/Working writes. A storage/database
  rewrite is not established by these observations. Target redundant encoding/mirror checks
  and native-JSON response work before adding new caching infrastructure.
- The separate two-format export returned 202 in 0.53 s and streamed its 193 KB ZIP in 0.80 s.
  A single 14.63 s click-to-input probe is not a ZIP-worker duration: it used a global Playwright
  role selector to close the dialog. A matched same-server diagnostic confirmed selector
  attribution: global role Close took 18.94 s versus 0.659 s for a scoped CSS real-pointer Close;
  subsequent Edit/fill remained below one second. No export-product fix is warranted. The
  temporary audit wrapper now uses the scoped Close; ordinary mutation timing probes are unchanged.

## Evidence and next gate

Temp contains editing-final-packaged-rc3-{100,600}.json/.log and
editing-final-packaged-rc3-findings.md. The wrapper was editing-final-audit-nov9.py; its recorded
SHA-256 was afc5a3e52c601daf30dd967245aa3dbc13589ebc0590371c59b91527a79648bc.
The corrected Close-only wrapper SHA-256 is
822907a7f237792956df31288699fc321db0631948f4ad48d99af438a20f6655.
The attribution report is Temp/editing-export-click-attribution-nov9.md, with the unchanged-source
matched pair in editing-export-click-pair-diagnostic-nov9.json. Keep the original export
observation rather than relabelling it as worker time or overwriting its source identity.

Correctness and source-byte identity passed, but the speed gate remains open. A separate
codex/mvp-editing-speed-final worktree now holds narrowly scoped optimizations and review;
the running rc3 and immutable extraction remain untouched. Recheck the consolidated candidate
on a quiet host, then its exact packaged build after activation. Spelling work waits for this
gate and must remain on a separate unmerged branch for the user's later approval.

## Reviewed candidate recheck

Candidate 8128cf5041aa648b5fdf743bed7f93fe2f5c521f includes the ADR 0159 response,
Store and structural-reader changes. The same quiet 600-passage fixture and three observations
per action passed all content, timing, Working, source-identity and export checks. The corrected
Close-only wrapper was unchanged (candidate metadata rather than packaged/activated metadata).

| Action | rc3 actual input | Candidate actual input | Candidate maximum |
| --- | ---: | ---: | ---: |
| Save | 3.83 s | 2.57 s | 2.71 s |
| Rename speaker | 2.39 s | 2.00 s | 2.64 s |
| Assign speaker | 3.23 s | 2.00 s | 2.45 s |
| Merge | 5.33 s | 2.76 s | 2.78 s |
| Undo merge | 5.83 s | 3.13 s | 3.27 s |
| Split | 3.14 s | 2.57 s | 2.74 s |
| Delete | 3.30 s | 1.98 s | 2.42 s |
| Undo split | 7.29 s | 2.55 s | 2.56 s |
| Checked mark | 0.17 s | 0.14 s | 0.15 s |

Two of 24 content observations still exceeded three seconds, both Undo merge; manual review
was zero of three. This is substantial improvement, not an all-samples three-second pass.
Undo merge's primary API median was 2.19 s and Undo split's 1.72 s. Export returned 202 in
0.73 s, allowed real draft input in 1.52 s while its worker was still running, and streamed the
verified ZIP in 0.96 s. Keep the API/render/background separation and n=3 limitations above.

The additional exact-mirror shortcut runs only after fresh owned-result/head/hash validation;
different formatting retains canonical comparison. Its author gate passed 39 cases and the
independent reviewer passed 13 affected authority/mirror cases. These counts overlap.
The measured residual does not justify new cache authority or a storage rewrite for this MVP.
Proceed to packaging/activation and recheck that immutable build before spelling work; retain
long-history Undo responsiveness as a measured follow-up. Evidence: Temp/
editing-candidate-mirror-600-nov9.json and .log; the prior 040 candidate evidence is preserved.

## Exact activated rc4 package

The long fixture passed all correctness and source-byte checks against immutable package
c897efaff51adfd24be6de80356a192cbd85455e / code 02193a28ffa9f86422114b2d3a947a402bafcc23,
archive SHA-256 749a3b9ffde06c2cf4e55bd61146552e6b9bb53cfbf732b99544f690f9ca235d.
This was a separate extraction, not the running user's library. The unchanged final 822907
wrapper and same three observations per action were used. The separate exact-package short
fixture also passed every correctness/source check with the same source inventory.

| Action | 600: primary API | 600: actual input | 600: render | 600: background with probe |
| --- | ---: | ---: | ---: | ---: |
| Save | 1.77 s | 2.69 s | 0.56 s | 4.53 s |
| Rename speaker | 1.74 s | 2.09 s | 0.40 s | 4.31 s |
| Assign speaker | 1.63 s | 2.09 s | 0.33 s | 3.64 s |
| Merge | 1.75 s | 2.52 s | 0.58 s | 4.56 s |
| Undo merge | 1.99 s | 2.89 s | 0.49 s | 4.49 s |
| Split | 1.77 s | 2.70 s | 0.60 s | 5.06 s |
| Delete | 1.50 s | 2.25 s | 0.52 s | 5.07 s |
| Undo split | 1.58 s | 2.49 s | 0.50 s | 4.99 s |
| Checked mark | 0.06 s | 0.14 s | 0.14 s | 0.28 s |

Zero of 24 content observations exceeded three seconds; manual review was zero of three.
The slowest actual-input observation was 2.94 seconds. Save improved from the rc3 median
3.83 to 2.69 seconds; Undo split from 7.29 to 2.49 seconds. Preserve the preceding candidate's
two slightly slower Undo merge observations as evidence of workload variance. Three samples
do not establish p95, an unconditional three-second guarantee or other machines' acceptance.

Ordinary editor opening took a 14 ms median; Next to review 0.8 ms. Short generated-media
highlighting, exact retained-word timing, null appended-word timing, Undo split restoration,
Working/history/review invariants and text/JSON ZIP contents passed. Export returned 202 in
0.49 seconds, actual input in 1.53 seconds and streamed 193 KB in 0.57 seconds. That worker
had completed by input acceptance; the earlier candidate independently demonstrated input
while it was running. Neither establishes multi-GB original-inclusive export throughput.

Advisory refresh and whole-recording persistence/response work remain measurable. The 3.6–5.1
second background medians include the input probe and do not mean typing was locked that long.
Larger histories and representative user hardware remain a follow-up, not a reason to replace
SQLite or introduce new cache authority in this MVP. Raw/derived evidence: Temp/
editing-final-packaged-rc4-600.json/.log, editing-final-packaged-rc4-summary.json and
editing-final-packaged-rc4-findings.md. The same source inventory was unchanged after the run.

The 100-passage run also had zero of 24 content observations above three seconds (review zero
of three). Actual-input medians: save 0.46 s, rename 0.31 s, assignment 0.37 s, merge 0.47 s,
Undo merge 0.48 s, split 0.52 s, Delete 0.45 s, Undo split 0.37 s and review 0.06 s. The highest
short-fixture sample was split at 0.82 s. ZIP acceptance was 0.27 s and actual input 0.59 s.
Raw short evidence is editing-final-packaged-rc4-100.json/.log. Combined, all 48 sampled
content actions and six independent review marks stayed below the investigation target.
This closes the scoped final speed gate; broader real-user/large-history acceptance remains.
