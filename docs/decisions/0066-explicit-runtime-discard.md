# 0066 — Explicit disposition of retained CBS Runtime files

Accepted 27 October 2026. Extends ADR 0065 without weakening its automatic cleanup policy.

Failed or partial private runs may retain useful output, even when no terminal outcome file
was written. Stop, Clear finished, ordinary cleanup and local deletion cannot decide whether
the researcher intends to abandon that recovery. Add a separate whole-allocation action.

The preview first requires an inactive local owner and a fresh exact terminal allocation
identity, including its frozen application/product and owned folder resource. It checks local
originals, frozen source versions and published inputs. A bounded read-only remote collector
archives every attempt's available known stage files in separate local evidence folders.
Outputs without verifiable outcome receipts remain explicitly unverified. Complete output
without a saved durable result refuses discard and directs the researcher to recover it first.
No preparation, inference, upload, allocation or termination belongs to this collector.

Preview uses POST because preserving downloaded evidence writes local files. It returns only
member names, saved/missing result status, stage counts and a SHA-256 confirmation fingerprint;
neither transcript bodies nor credentials belong in the response. The separate confirmation
dialog lists every affected file. Cancel makes no abandonment decision.

Confirmation repeats preservation and terminal checks, then compares the complete stable
evidence fingerprint. A single SQLite transaction checks membership/attempt/result/source
state, fences coordinators and recovery, and records immutable human consent before moving
the exact owned folder to UCloud Trash. Preserve successful result identities and Trash status,
all versions, failure diagnostics and local evidence; missing results are never called complete.
Known outputs from prior attempts must not overwrite the latest attempt's working files.

Trash failures retain the exact decision for explicit retry and keep the obligation visible.
A lost Trash reply requires proving absence by listing the saved parent. No operation empties
UCloud Trash. Uncertain submission, identity conflict, changed preview, missing local preservation
or unavailable terminal evidence refuses the destructive step. Consent cannot authorize another
allocation, additional members, later outputs or an ordinary cleanup action.

Use temporary libraries, synthetic originals and mocked UCloud for implementation verification.
Real allocation recovery, Trash semantics and library activation need separate acceptance.
