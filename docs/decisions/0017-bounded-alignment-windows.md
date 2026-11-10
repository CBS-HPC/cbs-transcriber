# 0017 — Bounded windows for long transcript alignment

Accepted, 20 October 2026. Extends 0016 for long or dense passages.

Forced alignment over an entire untimed interview creates an unbounded frame/text trellis and
can confidently force unrelated words into the wrong audio. Dividing time in proportion to text
length would manufacture timing. Reviewed text must remain authoritative (V31/V33).

Use the optional runtime's faster-whisper dependency with a pinned multilingual small model,
solely to discover windows. Recognize 30-second audio slices with overlapping context. Keep
recognition results per slice for retry. Unique four-word phrases with at least three distinct
words anchor the existing text; reject conflicting phrase order. Only small gaps between nearby
anchors may share a search window. Repeated/unsupported wording and large gaps remain untimed.
Preliminary ASR timestamps are search bounds, never published as aligned word times.

WhisperX then aligns the original words in windows bounded by 60 seconds, 120 words and 1200
characters, without interpolation. Persist each successful alignment window before proceeding.
Audio uses a disk-backed float32 array, avoiding a whole-recording decode/copy into RAM. Models
run sequentially. These are structural bounds, not a measured peak-memory or accuracy claim.

Partial results split at real token boundaries into timed/untimed passages, retaining every
character. Untimed fragments have no invented bounds. Result metadata records coverage and
the label explicitly identifies partial timing. The source result stays unchanged and selected.

Review: do not use the comparison matcher's fuzzy fallback for audio windows; it accepts weak
matches for visual comparison. Reject order conflicts rather than choosing an arbitrary repeated
phrase. Limit both text and duration even for imported timed passages; no whole-recording CTC
fallback. Exact phrasing can still be hallucinated by ASR: live quality evaluation remains needed.

Verification: synthetic distinctive/repeated/reordered phrases, omissions/substitutions,
long passages, partial timings and Unicode preservation; bounded inference calls and offsets;
recognition/alignment checkpoint reuse; disk-backed PCM conversion; browser partial-result
selection/playback and source preservation. No model downloads or paid requests in tests.

Sources: [faster-whisper 1.2.1 API](https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py),
[WhisperX 3.8.6 dependencies](https://github.com/m-bain/whisperX/blob/v3.8.6/pyproject.toml),
[Systran multilingual small](https://huggingface.co/Systran/faster-whisper-small)
(revision `536b0662742c02347bc0e980a01041f333bce120`, MIT).
