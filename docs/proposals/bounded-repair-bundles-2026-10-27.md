# T90 brief: bounded passage bundles before UCloud routing

27 October 2026. Design handoff, not delivered remote admission or an Automatic threshold.
Existing local repair, frozen jobs and UCloud transports keep their current behavior. No
recording, library, provider or model was accessed to prepare this brief.

## Why a bundle is needed

The current re-transcription host prepares/uploads a recording WAV; the worker extracts the
selected intervals afterward. A passage-scoped inference request therefore does not establish
clip-only network transfer. Adding large repair routing must first make its transfer scope
explicit and verifiable. Native Transcriber, old SSH/API work and shared Runtime ownership
remain separate; a bundle does not confer permission to upload or clean a remote folder.

The user has selected the immutable original recording as the common inference source. That
preparation change is separate work. A repair bundle must consume its eventual trustworthy
extraction/timebase receipt, rather than independently selecting playback MP3 or assuming a
legacy prepared WAV has original-recording timestamps. Existing frozen input bytes stay frozen.

## Prove the source, then the selected projection

`Store.freeze_result` identifies a full immutable version and its canonical payload hash.
Passage-timing jobs subsequently retain only selected passages in `source_snapshot`, while
carrying that full version hash. Rehashing the trimmed snapshot as though it were the complete
version would reject valid inputs or falsely establish provenance.

The host must read the exact preserved version through its ownership/integrity boundary,
verify recording/result/version/hash, and prove that the selected snapshot is an exact permitted
projection. Supplied IDs and hashes alone cannot establish SQLite ownership. Keep reviewed
text, original bounds, speaker labels and IDs exact. Record full-version and projection hashes
separately. No live result or newly edited passage may silently replace frozen input.

Reuse `timing_units` and `joined_text`: adjacent unbounded passages share one bounded neighbour
gap, with existing 60-second/120-word/1,200-character limits and exact character ranges. Retain
the distinction between a search window and passage timing. A search gap never becomes a
passage's start/end; only its own completely aligned words can establish those bounds.

## Smallest transport unit

Use a job-owned directory, with no general ZIP/TAR extraction framework:

```text
manifest.json
units/00000/request.json
units/00000/audio.wav
units/00001/request.json
units/00001/audio.wav
COMMITTED.json
```

The strict versioned manifest should bind:

- Operation and job/recording/result/version identity, full source hash and selected projection.
- Exact frozen options and runtime/model identity, with no credentials or private paths.
- Unit ordinal, exact passage membership and separately labelled source/search/target intervals.
- Actual extraction sample rate/frame interval, clip geometry, request/clip sizes and hashes.
- Alignment text/character ranges only for alignment; re-transcription remote requests omit
  reviewed wording and surrounding passages. Local lineage hashes are not remote text input.
- A marker written last that binds the exact manifest bytes and complete expected inventory.

Set byte/count/JSON-depth limits before publishing this ABI. They must follow existing repair
limits and bounded transfer behavior; do not invent a new performance threshold in this brief.
Reject extra/missing/duplicate units, links, nonregular files and mismatched existing artifacts.
Fresh publication uses owned paths; unchanged committed bytes can be reused, never rewritten
under an already accepted task. Cancellation retains diagnostics and completed unit evidence.

## Sample mapping and output

Freeze actual extraction frame rounding, including its source timebase and requested versus
sample-rounded interval. A float offset copied from the UI is insufficient evidence of which
samples were sent. The original-audio preparation receipt must supply that correspondence.

Alignment units have no implicit padding. Re-transcription separately records its existing
0.5-second padding on each side, clamped to recording limits, and its unpadded permitted target.
Retain recognized words only when their midpoint lies within that target. Never assign padded
audio bounds to reviewed passages.

Check every present output endpoint, including partial maps with a missing opposite endpoint
or score. Preserve legitimate nulls. Persist clip-relative checkpoints unchanged; map valid
output once to recording time in a copy. Retry must not apply an offset twice. Preserve each
unselected passage dictionary and reuse same-result revision/text/bounds guards when applying
timing. Re-transcription continues to append an alternative result without replacing review.

Remote acceptance/carry must eventually bind exact owner/task/attempt and input/output receipts.
Do not infer those obligations from a local commit marker. Finalization, Stop, recovery and
cleanup must continue to satisfy existing Runtime ownership and conservation rules.

## Proof before routing

Use frame-coded synthetic audio to test separated clips, fractional boundaries, record edges,
padding and gap groups. Include swapped clips, malformed geometry, oversized whole-WAV refusal
before decode, request/source tampering, selected-only/full-version distinction, cancellation,
partial output, offset-once retry, stale application and exact unselected-passage preservation.

Then complete producer, transport, admission, recovery and application integration with mocked
UCloud. No public remote repair control should be enabled from the isolated validator alone.
Bounded Auto language needs its own same-scope source evidence or text/speech compatibility
proof; a recording-wide language observation cannot establish a mixed-language clip's language.

Automatic routing threshold/capacity, the final remote ABI, trustworthy extraction receipts and
bounded language compatibility remain open. GPU performance, real UCloud transfers, language
quality and private interview acceptance require separate authorized evidence.
