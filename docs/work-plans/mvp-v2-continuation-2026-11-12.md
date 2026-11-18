# MVP v2 continuation and Claude handover — 12 November 2026

The user asked Codex to finish its current work and let Claude continue. No further product
implementation was started after that request. Work remains local on public-descendant
branches; main, Desktop changes, serving environments, real library and remote evidence are
preserved. No merge/push/release or app reload is implied by the handover.

## Verified baseline and next work

| Track | Status | Next action |
| --- | --- | --- |
| Reliability candidate `64d8757` | Independent Go; fresh 193/193 focused cases, 566 unchanged inputs | Preserve it. A later labelled preview load needs idle/draft/job checks; it is not loaded. [Receipt](../verification-2026-11-12-reliability-composition.md). |
| Runtime generation Stage 1 `401769e` | Disabled metadata/path/candidate foundation; 46 distinct synthetic passes | Kept separate from that candidate, then composed into the isolated protocol baseline `2df4dfd`; no new execution authority. [Receipt](../verification-2026-11-12-runtime-generations-stage1.md). |
| Runtime Stage 2 (T144) | Design Go; implementation unstarted | Implement only the disabled owner/folder/readiness-promotion slice in [ADR 0176](../decisions/0176-isolated-runtime-generation-recovery.md). Later admission/carry/execution/capture/restore remain mandatory. |
| Funding observer (T146) | Conditional design Go; implementation unstarted | Standalone observer/mock tests under [ADR 0178](../decisions/0178-bounded-project-funding-observation.md); independently verify the full primary unit pin before accepting fixtures. No CPU routing or affordability claim. |
| Existing Working schema 19 (T147) | Implemented 17 November; independent Go (`f484571`, 171 focused cases) | Combined on `claude/v2-integration`; not loaded. Real-library activation follows the authorized activation process. |
| Editor/review (T148/T149) | Show filter and default-on Text highlighting implemented (`claude/editor-review-v2`) | User decisions in [editor/review decisions](editor-review-decisions-2026-11-12.md); Undo/history scope awaits the user. Re-transcription parked. |

Use separate file ownership and independent review. Suggested Runtime Stage-2 ownership is
new `runtime_generation_preparation.py`, the preparation owner/registry and exact-root
observation seams, plus narrowly optional frozen paths in `enhancement_api.py`. No captured
source/pin/cap changes belong in that stage. Future acceptance/carry, batching/execution,
selected-repair and provenance/restore are separate ownership groups; do not activate after
only the owner/promotion slice. Historical retained locks and promoted environments remain
untouched. CPU preparation needs a qualified separate recipe and stays refused meanwhile.

The Working builder owns `working_existing.py` and scoped old-fixture updates; an independent
tester owns real schema-19/native-dependency/contained tests. The funding builder owns only
the new observer/basic tests; independent adversarial tests remain separate. Root owns
SPEC/TODO/ADRs and exact verification receipts. Avoid repeats of completed immutable gates
unless source, fixtures or evidence actually changed.

## Next user discussion: editor and review workflow

The user selected this as the next discussion **when ready**, not permission for an
independent interface redesign. Prepare a short existing-versus-proposed walkthrough:

| Topic | Existing starting point | Decision to discuss |
| --- | --- | --- |
| Word-following in Text | Default-off editable-text prototype; double-click word selection and timing-preserving edits already exist | Replay behavior while typing, caret/focus/selection/undo and missing/changed timings, without the rejected duplicate panel. |
| Review filters | Not checked/Checked/Needs review, counts and Next to review are implemented | Which passages users need to filter/navigate and how manual marks relate to model uncertainty. |
| Re-transcribed-passage tags | Broader tagging is not implemented; the hidden re-transcription source/transfer policy also needs review | How changed passages are identified and revisited without implying approval or silently running inference. |
| Clearer history/rollback | Version history, Undo split/merge and individual deleted-passage restoration exist | What extra passage-level history users need, how far rollback reaches and how it preserves unrelated edits/Working choices. |

Agree the workflow before changing the interface. Spelling underlines/suggestion triggers
remain a separate paused decision, including their interaction with Text double-click.
PII-Tracer remains behind Runtime stability and identifier/output/pseudonym/key decisions.

## Cleanup restriction

Broader cleanup remains [design only](../proposals/native-failed-output-recovery-2026-11-10.md).
The user's explicit 12 November requirement protects ongoing/queued/uncertain/recoverable
allocations, unfinished imports, Activity owners, controls and evidence. Remote/local state
disagreement, unreadable or incomplete evidence, and changed membership cannot become cleanup
permission. Keep Runtime/model/unknown folders excluded and revalidate before exact owned
Trash operations; no root deletion or Trash purge.

Other planned work and real-device/provider/privacy/licensing acceptance stay in [TODO](../../TODO.md).
The usage ledger's last cumulative cutoff is 12:32:09 UTC; later work is excluded/unmeasured,
the fixed MVP baseline is unchanged, and Claude accounting belongs to Claude.
