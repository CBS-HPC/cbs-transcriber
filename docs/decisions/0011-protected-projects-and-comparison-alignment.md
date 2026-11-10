# 0011 — Protected projects and visually aligned comparisons

Accepted: 20 October 2026. Supersedes the three protected categories in 0008.
The user chose one category and approved the clearer label Protected.

Offer Standard and Protected in creation/settings. Protected still requires explicit approval
of each provider configuration. Schema 4 maps sensitive/restricted/confidential projects to
Protected, backs up existing schema-3 databases, and preserves recordings, transcripts, result
edits, original timings and job snapshots. Providers previously approved for all three legacy
categories remain approved; partial approvals need renewed approval to avoid silently extending
their permissions. Legacy queued settings normalize when read, without rewriting paid work.

Comparison uses a local, ordered, bounded-lookahead matcher over normalized wording and actual
passage intervals. It can group up to three adjacent passages on either side, tolerate changed
wording and leave gaps for omissions. This is a best-effort visual aid, not semantic equivalence.
Long passages bound comparison tokens; their complete display text remains untouched.
No embeddings, external API requests or additional dependencies are needed.

Keep the existing editable active result and read-only comparison. Reparent their original
passage elements into matched groups and equalize group height after layout/font/width changes.
Link scroll positions only in side-by-side view. Searching temporarily restores original
passage order; clearing search restores alignment. Edits/revisions invalidate alignment caches.
Discard stale requests when switching comparisons. All result reads remain recording-scoped.

Visual comparison alignment does not compute word timing. Whisper uses word timestamps;
OpenAI diarization supplies speaker/passage intervals; the other configured OpenAI models
return plain text. A separate ASR/forced-alignment/diarization pipeline such as WhisperX can
produce both. That pipeline is future hosting work, not part of this comparison change.

Validation: unit/API checks cover splits, omissions, timing fallback, mutation isolation,
cross-recording access, schema migration, approvals and retained queue snapshots. Chromium
checks vertical alignment, gaps, resizing, linked scrolling, editing, search and comparison
removal. No live transcription requests are used.

Sources: [OpenAI transcription guide](https://developers.openai.com/api/docs/guides/transcription),
[WhisperX](https://github.com/m-bain/whisperX),
[pyannote Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
