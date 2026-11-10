# 0076 — Evaluate the accepted producer's immutable creation

Accepted 27 October 2026 for T89a. This hardens ADR 0006's opt-in FLEURS runner; it does not
run a model, clear a licence, extend the public corpus or establish a quality baseline.

## Problem

Reading a recording's active transcript after an evaluation job completes can score another
provider or a later reviewed passage under the original model label. Taking `original_text`
from that active view does not establish which producer made it. Exporting the active view
can also produce a different artifact from the text used for scoring. A settings reread just
before enqueue still leaves a race between that read and the accepted job's frozen settings.

## Bind acceptance and observation

Select the initial configured provider explicitly. Before treating enqueue as successful,
checkpoint returned job IDs and require a one-to-one association with the expected recordings.
Validate the accepted jobs' frozen provider/settings against that selection. Public status
polling does not include the private settings snapshot, so capture the necessary allowlisted
identity from acceptance rather than reconstructing it later from current settings.

At completion, require each same job/recording to identify its exact live producer result.
Verify the result's recording/job association, then select its immutable sequence-one machine
creation version. Read and verify that exact version, including the canonical payload hash,
before scoring or exporting. Do not use a later head, active recording projection, source
enhancement child or legacy observed baseline as a substitute for missing creation evidence.

The version endpoint adds derived display defaults. Hash the canonical segments/speakers
snapshot, not the decorated HTTP object. Score creation text and export that exact version;
UI corrections, active-result switches and wording adoption must not improve measured ASR.
Version-specific reads remain bound even if another version becomes current in between calls.

## Evidence and failure semantics

Record safe per-clip producer/result/version identities and canonical payload hash, plus exact
prediction/export hashes and actual export format. Preserve the existing legacy export filename
only with an explicit readable-text format label; its `.nvivo.txt` suffix is not evidence that
the artifact is a native NVivo transcript. Never copy arbitrary job parameters, credentials,
private notes or upstream bodies into the evaluation report.

Missing/ambiguous/mismatched jobs, frozen settings, results or creation versions make the run
incomplete. A result already in Trash is not silently restored or replaced. Preserve observed
job IDs for inspection; never cancel, resubmit or turn incomplete coverage into a score. Existing
timeouts and explicit paid-call opt-in remain. No new app API, schema or queue behavior is needed.

Old report files are observations without these receipts; do not backfill or upgrade them by
reading today's active results. A new receipt establishes which app-produced immutable state
was scored, not independent proof of remote inference, licence clearance, original-media
timebase, calibrated accuracy, timing/speaker quality or representative interview performance.

Verify with deterministic mocked result switches, later edits/adoption, settings/enqueue races,
bad association/creation/hash cases and API integration on synthetic libraries. Real provider
evaluation remains a separately authorized command; routine tests never make a paid call.
