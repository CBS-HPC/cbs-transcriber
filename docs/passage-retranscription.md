# Re-transcribing selected passages (investigation)

Status: implemented 23 October 2026 for WhisperX (local/UCloud); see ADR 0054. OpenAI clips
with a cost confirmation remain open. Requested 22 October 2026.

## Is it feasible?

Yes. The app already re-processes selected passages for word timing ("passage repairs"). That
infrastructure covers most of what re-transcription needs:
- it freezes the selected passage IDs;
- it cuts each passage's actual audio interval;
- it checkpoints per passage;
- it appends **one** combined result while every unselected passage stays exactly as it was.

Re-transcription would reuse that flow, with a speech model producing new wording instead of
alignment of the existing wording.

## Proposed workflow

1. **Selection.** In the editor, choose passages; candidates include those the uncertainty view
   flags. Only passages with known start/end times qualify, because an untimed passage has no
   audio interval to cut. Older fragments with a frozen source interval can use that interval.
2. **Audio windows.** Cut each passage's interval with a little padding (about 0.5 s each side),
   so words at the edges are not clipped.
3. **Model.** The user picks the provider:
   - **OpenAI:** one request per clip; the cost is proportional to the clip length.
   - **Local Whisper/WhisperX:** short clips suit the local runtime well and need no upload.
   - **UCloud:** all selected clips go into **one** Transcriber Batch job (the shared-batch
     mechanism from ADR 0045). There is no allocation per passage.
4. **Mapping back.**
   - Keep only words whose midpoint lies inside the original passage interval; padding words
     belong to the neighbours.
   - Shift word times by the window start.
   - Keep the passage's speaker label.
   - Never invent timing for words the model did not time.
5. **Result.** Append one new result: the selected passages carry the new wording and every other
   passage is copied unchanged. It is recorded as a provider result with scope
   "passage re-transcription" and a passage count. The reviewed result is never overwritten.
6. **Review.**
   - Open the new result with **Compare with** set to the old one, so the differences are
     highlighted.
   - The new passages bring their own uncertainty hints.
   - Accepting a re-transcription is ordinary editing in the result the researcher chooses.

## Safeguards

- **Protected projects:** the same provider approval and frozen settings apply, and they are
  checked again before each clip is uploaded.
- **Cost:** confirm the number of clips and the total audio minutes before sending anything to
  a paid provider.
- **Context:** sending neighbouring text as a prompt could improve continuity across the cut,
  but it uploads more transcript text. Keep it off by default, especially for Protected projects.
- **Retries:** reuse the existing per-passage checkpoints, so a retry never repeats a completed
  clip.

## Effort and open questions

About 1–2 days including tests: it is mostly reuse of passage repairs, the chunked provider
path and the shared UCloud batch.

Open questions for the researchers:
- Should a re-transcribed passage replace the old wording in a copy, as proposed, or appear
  as an alternative inside the same result?
- Should the user be able to pick a different language per passage?
