# 0186 — Recording spelling vocabulary that inherits the project's words

Accepted on 20 November 2026 for the hidden v2 spelling feature (ADR 0185). The user asked for
"both a Project Vocabulary and a recording vocabulary option under recording settings.
Recording vocabulary should inherit the project vocab." Implemented on
`claude/spelling-redesign`; still behind the `spelling` release-profile feature.

## Decision

- **Scopes.** Checking accepts the union of three private word lists: personal (library-wide),
  the recording's project and the recording itself. A recording inherits its project's words;
  it cannot remove them. A recording's words never apply to another recording.
- **Where.** File Settings shows **Project vocabulary** and **Recording vocabulary**; Project
  Settings keeps its Project vocabulary button; Settings → Spelling keeps Personal vocabulary
  (user choice, 20 November). The recording dialog shows the inherited project words
  read-only, next to the recording's own editable list. Ctrl+click on a coloured word also
  offers "Add to recording vocabulary" and "Add to project vocabulary" (ADR 0187).
- **Storage.** Settings row 13 keeps `schema: 1` while no recording has words, so earlier v2
  builds keep reading it. The first recording word adds a non-empty `recordings` map keyed by
  recording ID and writes `schema: 2`; removing the last recording entry writes `schema: 1`
  again. A recording entry stays after its words are emptied, so its revision continues.
  Bounds: 4096 recording entries, the existing 5000 words per scope and 4 MiB per row.
- **Fences.** Each recording scope has its own CAS revision. A save checks that the recording
  exists in the same writer transaction as the revision check. The check context records the
  recording revision in `vocabulary_revision`, so a late check or suggestion after any of the
  three lists changed is refused as before.
- **Delete.** A recording or project Delete fences each deleted recording's vocabulary revision
  in its metadata digest (a change after the preview refuses with 409 and keeps everything).
  The writer transaction removes the deleted recordings' entries and the project's entry before
  deleting the owner rows, because the validator requires live owners. A project Delete also
  removes entries of any recording that still belongs to it. Unrelated scopes and their
  revisions are untouched; a row with nothing to remove is left byte-identical.
- **Restore.** The portable semantics worker accepts schema 1 and schema 2, requires every
  recording key to be a 32-hex ID of a captured recording, and retains the row byte-exact; it
  was repinned. Operational row 12 is still stripped.
- **MVP.** Reads stay open, including `GET /api/recordings/{id}/spelling/vocabulary`; a save
  that only removes words is allowed and one that adds a word is refused as `feature_disabled`
  inside the revision-checked writer, exactly like the other scopes. The File Settings buttons
  carry `data-feature="spelling"`.

## Consequences

- A library opened by an earlier v2 build after recording words exist sees schema 2 as invalid
  vocabulary. That build then refuses spelling reads and, because its Delete fence reads the row,
  refuses project Delete until the newer build removes the recording words. An earlier build's
  recording Delete does not read the row, so it would leave an orphan recording entry that this
  build then refuses as invalid vocabulary. Downgrading is not a supported path; these limits
  are recorded rather than worked around.
- Malformed row 13 now also refuses a recording Delete (previously only a project Delete read
  it). This keeps the fence strict; the error is the existing descriptive vocabulary message.
- Recording words stay out of transcript exports and research reports, like the other scopes.
  Synthetic unit tests cover inheritance, CAS, owner checks, the schema switch, Delete fences
  and restore; real-library activation and researcher review remain open.
