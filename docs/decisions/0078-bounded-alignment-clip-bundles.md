# 0078 — Bounded alignment clip bundles

Accepted 28 October 2026 for the next T90 prerequisite after ADR 0077. This is a local
producer/validator contract, not remote repair admission, a routing default or live acceptance.

Build only from an owned frozen repair job, its exact full historical version and the permitted
selected projection. Reuse repair_sources inspection and verify read coherence before writing.
Full-version, frozen-snapshot and selected-projection hashes have distinct meanings. Mutable
status observations do not become immutable bundle identity.

The first operation is fixed-language alignment. Require the job's full original-v1 source
snapshot and a frozen repair_runtime descriptor. Every consumed extraction receipt must already
be bound to that job's audio_preparations and match the selected unit's exact sample window.
Legacy jobs remain valid under their existing execution path but cannot enter this primitive
without the required evidence. Re-transcription, padding/ASR filtering and Auto clip language
remain separate contracts; reject them before publication.

Use the existing timing-unit/character mapping rules, including adjacent unbounded passages
inside a proven neighbour gap. A search window never becomes an untimed passage's timestamps.
At most 200 consumed units matches the current preparation-receipt cap; grouped units may
cover the existing 1,000-passage selection limit. Bound JSON, inventory and total PCM bytes
before publishing. No remote defaults or current public selection limits change here.

Generate ordinal-only paths in a job-owned generation directory. Copy only selected validated
PCM frames into canonical PCM16 WAV containers, stripping ancillary metadata. Preserve the
extraction receipt locally; bind the canonical output's own bytes/hash and identical sample
hash/count in the bundle. Manifest and committed marker bind the exact expected inventory;
write the marker last. Reject links, extra/missing/swapped files, malformed geometry and changed
existing artifacts. Reuse exact committed bytes; retain incomplete generations for explicit
disposition instead of silently overwriting or replaying remote work.
Refuse hard-linked artifacts as well as symbolic links. Check physical byte limits before
hashing/PCM reads, then use bounded reads with same-descriptor identity checks. Publish the
marker with atomic create-if-absent semantics; the managed volume must support hard links.
Unsupported volumes receive a curated refusal, retaining the incomplete generation.

Alignment requests contain only selected literal text and its exact character mapping.
Exclude surrounding passages, speaker names, paths, credentials and unneeded original fields.
Runtime identity is a frozen declaration, not proof of approval, deployed models or transfer.
The builder performs no whole-original decode, upload, allocation or SSH operation.

Output validation requires exact bundle/unit binding and declared clip coordinates. Validate
every present endpoint/score while preserving legitimate nulls. Offset a copy exactly once
using actual sample coordinates; reject recording-space output instead of offsetting twice.
Each word's character span must fit exactly one selected passage; a joined cross-passage word
is refused rather than guessed or clipped. Mapper inputs are trusted in-process validator
returns, never an externally supplied manifest declaration.
Application still requires the existing revision/text/bounds guards. Remote publication,
acceptance, per-unit recovery, Stop, cleanup and Automatic routing need later reviewed slices.
