# Version control redesign (proposal)

Status: proposal for review, 23 October 2026. Nothing has been changed yet. If accepted, it
replaces parts of ADR 0007, 0016, 0026, 0044 and 0052 with a new ADR.

## What is wrong today

A recording collects many **results**:

- every model run;
- every import;
- every word-timing or speaker enhancement;
- every passage repair;
- every passage re-transcription.

Each result has its own linear **History** of versions. This causes four problems:

1. **Clutter and doubt.** After a few steps a recording has five or more results, and it is
   unclear which one is "the transcript".
2. **Forks.** Adding word timing creates a copy, so corrections made in the original are
   missing from the copy and the other way round. The stale-source warnings (V94) only point
   this out.
3. **Two places for one idea.** Manage results lists the results; History lists one result's
   versions.
4. **Wrong mental model.** Researchers think of one transcript that improves over time, with
   other model outputs as alternatives to compare against.

## Principles

- **One working transcript per recording.** It improves through versions and is the one you
  review, export and report.
- **Machines never overwrite people.** Automatic steps add versions. They change only what
  they own (timing, speaker labels) and only where the text is still what they worked on.
  They never change words you wrote.
- **New wording from a model is a suggestion until you accept it.** Accepting adds a version
  that records where the wording came from.
- **Everything stays recoverable.** Every version is immutable, restorable and attributed:
  person or job, model, source.
- **Other model runs are alternatives**, not competing working copies.

## Proposed model

| Concept | What it is | Where you see it |
| --- | --- | --- |
| **Working transcript** | The recording's single reviewed document with a linear version history | Editor; Versions → History |
| **Version** | One immutable state with action, actor, origin (job, model, adopted alternative) and reason | Versions → History |
| **Alternative** | The raw output of another model run or import, kept read-only | Versions → Alternatives; Compare with |
| **Suggestion** | Proposed new wording for specific passages (re-transcription, or a passage adopted from an alternative), shown inline | Editor, per passage: Accept / Reject |

### How each operation maps

| Operation | Today | Proposed |
| --- | --- | --- |
| First transcription or import | New result | Becomes the working transcript (version 1) |
| Later model run or import | New result | New **alternative**; compare, adopt passages, or make it the working transcript |
| Manual correction, merge, split, delete, speaker change | Version | Version (unchanged) |
| Word timing / speaker labels (whole recording) | New result | Version on the working transcript, merged per passage (below) |
| Passage repair / realign edited passages | New result / version | Version (as realignment already does, ADR 0052) |
| Re-transcribe passages | New combined result | **Suggestions** on those passages; accepting adds a version |
| Restore an earlier version | Version | Version (unchanged) |
| Trash | Per result | Alternatives can go to Trash; the working transcript's versions are never deleted |

### Merging machine output into a transcript that may have changed

A job freezes the version it started from, as today. When it finishes:

- **Nothing changed since the job started:** apply its output as one version.
- **Some passages changed:** for each passage, match by passage ID and exact text hash.
  - Text unchanged: apply timing or speakers.
  - Text edited, split, merged or deleted: leave as the researcher made it, and list it as
    "not updated by this job".

  The version records which passages were updated.
- **Speaker names you set always win** over detected speakers.

### Branches and merges, like Git (the researcher's suggestion, 23 October)

The researcher asked whether branching and merging could solve this. The problem is manual
corrections made to the original after a WhisperX enhancement started, so that changes have no
single linear order. It maps well:

- **Branch.** A job (word timing, speakers, re-transcription, another model) is a branch from
  the version it froze: its **base**. The app already stores that base (`source_version_id`)
  with every frozen job.
- **Two sides change.** Meanwhile the researcher keeps editing the working transcript ("ours").
  The job produces its output ("theirs").
- **Three-way merge per passage.** Git compares both sides with the common base, line by line.
  Here the unit is the passage, matched by ID, with exact text compared:
  - Only theirs changed it: take theirs (for example new timing on unchanged text).
  - Only ours changed it: keep ours.
  - Both changed different things: combine. Your text with their timing is applied only if
    the timing still fits your text, otherwise the timing is left out and the passage is
    listed.
  - Both changed the same thing (you edited the wording, and a re-transcription also
    changed it): a **conflict**. It is shown as a suggestion beside your text; you choose,
    nothing is overwritten.
- **Merge commit.** The merge is one new version whose history names both parents (your
  latest version and the job's output), so History shows where the two lines joined. Every
  earlier version stays restorable.
- **Already in place.** Passage word timing (ADR 0056) and realignment (ADR 0052) are a simple
  form of this: they merge timing into your latest version and skip passages you changed.

What would not be copied from Git: free-form branch names, rebasing and rewriting history.
Researchers need one working transcript and an auditable trail, not parallel long-lived
branches. Alternatives (other model runs) stay read-only branches that you compare with and
take passages from.

Proposed: implement the general three-way passage merge in step 1 below, for all machine output
(whole-recording timing and speakers too), with conflicts shown as suggestions (step 2).

## The Versions dialog (tabs)

1. **History**: the working transcript's versions with origin labels, a preview of each
   version's text, **Download** and **Restore as newest version**.
2. **Alternatives**: other model runs and imports. **Compare**, **Use passage wording**
   (creates suggestions), **Make working transcript** (adds a version), and Trash.
3. **Trash**: removed alternatives with Restore. Shown only when something is in it.

## Existing libraries

- The current active result of each recording becomes its working transcript.
- Other results become alternatives, read-only, with all their history kept.
- Enhancement and repair results that were made from the active result stay available as
  alternatives. Optionally, their timing can be pulled into the working transcript with the
  per-passage merge, on request only.
- No history is rewritten. The migration takes a backup, as earlier migrations did.

## Effort and order

1. Versions dialog with tabs; enhancements, repairs and realignment as merged versions.
   Alternatives are only re-labelled. About two days.
2. Re-transcription and adopted passages as suggestions. About one to two days.
3. Migration and labelling of existing results; reports and exports updated for "working
   transcript, version N". About one day.

## Questions for you

1. Should a later model run ever become the working transcript automatically, for example
   when the current one has no edits yet? Or always only when you choose?
2. Should suggestions be accepted per passage, or also all at once?
3. Is "working transcript" a clear name? Alternatives: "Main transcript", "Reviewed transcript".
