# Durable local transcription queue with explicit provider capabilities

Accepted, 2026-10-20. Use one in-process worker and SQLite checkpoints, retaining each
successful API chunk and its raw response. This keeps local operation simple; stopped
in-flight jobs require explicit retry because remote completion cannot be inferred safely.
Separate calls cannot establish global speaker identity, so chunk speakers are scoped.
An OS-owned library lock prevents two live servers from recovering or claiming the same queue.
Provider settings explicitly choose word-timed, speaker-segment, or plain JSON output.
The user removed OpenAI Batch API and text-processing features after confirming that the
Batch API excludes the audio transcription endpoint. Ordinary multi-file API requests remain.
