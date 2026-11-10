# 0054 — Re-transcribing selected passages with WhisperX

Accepted 23 October 2026. Implements the proposal in `docs/passage-retranscription.md`.

Researchers wanted new wording for doubtful passages without re-running a whole interview,
especially for the passages flagged by the uncertainty view (ADR 0048).

## Decision

- **Selection.** Choose passages with a passage's **Re-transcribe** button, or all bounded
  flagged passages with **Re-transcribe flagged**. Passages must have a known audio interval of
  at most 60 seconds. Untimed or longer passages are refused with the reason, and a request is
  limited to 200 passages. The selection is frozen with the exact source version (ADR 0016), in
  transcript order.
- **Engine.** The WhisperX worker transcribes each passage from its own audio plus 0.5 seconds of
  padding on each side, with word timestamps. It keeps only words whose midpoint lies inside the
  passage; the padding words belong to its neighbours. The new wording then goes through the
  usual bounded alignment. Faster-whisper models are pinned (ADR 0053). Choices: large-v3-turbo
  (default), large-v3, medium and small.
- **No text is sent.** Only audio bounds are sent, never the reviewed wording or surrounding text.
- **Where it runs.** Local, UCloud or Automatic, as for enhancement. Automatic routing uses the
  total clip length. All passages run in one job: on UCloud that is one allocation, through the
  API-only runtime, which must be prepared (checked read-only before queueing). Local runs need
  cached weights, or downloads allowed in the dialog.
- **Result.** One combined result is appended. Selected passages get the new wording, word timing
  and model hints, and keep their speaker and bounds. Passages where nothing was recognised keep
  their original text and are listed in `unchanged_segment_ids`. Every other passage is copied
  unchanged. The reviewed result stays active; **Compare with** shows the differences. The report
  scope is `passage_retranscription`, with the passage count.
- **Paid providers.** OpenAI clips are not offered yet. They would need a cost confirmation
  per request.

## Consequences

Accepting new wording is ordinary review in the result the researcher chooses. The 60-second
limit matches passage repairs; longer passages should be split first. The UCloud path awaits
live acceptance together with T70.
