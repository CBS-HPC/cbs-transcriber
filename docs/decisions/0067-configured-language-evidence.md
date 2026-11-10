# 0067 — Preserve configured ASR language evidence

Accepted 27 October 2026 as the first implementation stage of T84, supporting T43 provenance.
This does not enable Automatic language detection or change saved/frozen language defaults.

The worker currently discards faster-whisper's language information. New fixed-language ASR
and passage re-transcription outputs will preserve a small inline `language_evidence` list.
Each receipt describes the configured decoder language, with origin `configured` and null
probability. A forced decoder's reported probability of one is not detection confidence.
An upstream language label may be retained separately, without calling it observed speech
language. Record the pinned model repository/revision and actual engine version when available;
unknown runtime metadata remains unknown.
The receipt also names the frozen model choice. Bound reads use the job's frozen pin when
available; historical validation does not replace captured revisions with today's registry or
require the old inference package to be installed on the reviewing computer.

Bind evidence to the exact prepared `audio.wav` and frozen `request.json` SHA-256 values.
Full ASR uses the actual decoded frame range; passage calls record their padded, sample-rounded
slice. Windows explicitly use the `prepared_audio` timebase. They do not prove original-media
timestamps, recording-wide language or clip-only network transfers. Hash input files once per
operation, not once per passage. Do not put private paths, text, tokens or task identities in
these receipts; a valid completed ASR stage may be carried into a later attempt unchanged.

The derived float-audio cache must also belong to that source. Its former size-only reuse can
silently retain samples from a different WAV with the same frame count. Bind a completed cache
to streamed WAV/PCM hashes and frames, with its marker written last; missing or mismatched
identity requires regeneration before inference. A partially written cache is never ready.
This does not authorize changing a completed legacy ASR stage or inventing its input evidence.
Before new ASR, compare the actual mapped input buffer with this source binding too: a stable
filename alone cannot establish which inode a previously opened Unix mapping still reads.

Keep the helpers in the existing standalone worker so isolated Python and UCloud jobs need no
new module or dependency. Existing output filenames and expected-stage sets stay unchanged.
The optional inline field travels with its stage's existing hash receipt. New worker bytes
remain part of the frozen runtime code identity; existing remote owners are not upgraded.

Cached legacy stages without evidence remain readable and byte-for-byte unchanged. Do not
fabricate historical detection or regenerate ASR to populate metadata. A present receipt is
strictly validated against the prepared input, request, pinned model and actual window before
being reused or attached to a result. No changes belong in transcript text, timings or speakers.

Result source/version observations may retain validated evidence. Metadata-only research reports
use a separate bounded allowlist, excluding arbitrary passage IDs and paths; configured choice
is labelled as such. Automatic detection, uncertain/unsupported outcomes, default initialization
and alignment-only language resolution remain subsequent T84 work under the design proposal.

Verification uses synthetic WAVs, stubbed ASR and temporary libraries, including legacy
checkpoint recovery without inference. No weights, paid requests or live UCloud execution.
