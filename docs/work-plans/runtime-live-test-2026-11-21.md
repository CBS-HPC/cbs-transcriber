# CBS Runtime controlled live test — plan for the user's approval (21 November 2026)

The separate test app is running. On 23 November its read-only Check reported that base Runtime
preparation is needed. Each step marked **approval** needs the user's explicit OK in chat at the
time. Verify the exact folder, lock and owner state again before any preparation.
[Offline guard verification](../verification-2026-11-23-runtime-reader-guard.md).

## Starting point (verified 20 November)

- The user deleted the old Runtime folder on the UCloud project drive. A read-only lookup with
  the app's own file API (user OK) found `cbs-runtime`, the version folder
  `whisperx-3.8.6-cu128-a8f866300a50`, `.prepare-lock`, `.in-use` and `stamp.json` all absent.
- The only library the app knows has no Runtime records (0 rows in `runtime_preparations` and
  the Runtime batch tables, no Runtime jobs). The older Codex-hosted library no longer exists.
  So there is nothing to reconcile locally or remotely: a clean slate.
- Synthetic preparation, consent, locking and observation tests pass on `claude/post-v2`
  (157 passed, 3 platform skips), including the ADR 0172 stamp/lock preflight.
- The Runtime is hidden in MVP (`cbs_runtime`), so the test runs in the **full** profile on a
  separate test library and port, never in the user's MVP preview library. All Runtime
  generation switches stay off (ADR 0176); this uses the ordinary runtime folder.

## Steps

1. **Test library (no approval needed).** Start a full-profile app on another port with a new
   test library. Save the UCloud connection there; the key is shared per server through the OS
   credential store, so no key is typed or shown. Add two or three short public clips with
   documented licences (the FLEURS Danish subset already used in the preview).
2. **Speaker model on this PC (approval: download).** Only needed for speaker labels. The saved
   Hugging Face token is valid and the pyannote terms are accepted; preparation downloads the
   pinned speaker model locally so it can be copied to UCloud later. Alternative: run the first
   test without speakers and skip this step.
3. **Runtime preparation on UCloud (approval: credits).** One preparation job installs the
   hash-locked runtime (`catalogs/ucloud_whisperx_runtime.txt`) into
   `<storage>/cbs-runtime/whisperx-3.8.6-cu128-a8f866300a50`, fetches pinned public model
   weights, copies speaker weights from step 2 if chosen, and writes `stamp.json` last. The app
   describes this as several GB and 20+ minutes. It runs on the connection's compute product
   (exact funded B200 1g MIG; no full-GPU fallback, ADR 0063/0124).
4. **Small Runtime batch (approval: credits).** Transcribe the two or three clips as one Runtime
   allocation. Check: one allocation and one Activity card, per-file results saved, timing and
   speakers if prepared, owned run folder moved to UCloud Trash after saving, runtime folder kept.
5. **Interruption checks (approval: credits, same allocation).** During a second small batch:
   stop the app while it is queued or running and restart it (reconnect, ADR 0174); press Stop
   (allocation-wide, credits stop). No resubmission of uncertain work.
6. **Measure and record.** Wall time per stage, MIG memory, files per minute, and the credits
   actually charged, read from UCloud afterwards. Add the results to the human release
   checklist and TODO; any defect gets a bug row and a regression test.

## What is not known yet

- The credit price per hour of the project's MIG product: the user can read it in UCloud; the
  plan does not guess a cost.
- Whether the pinned runtime still installs cleanly on the current UCloud Terminal image.
- Real reply shapes for folder creation and job parameters (TODO T144 items), which this test
  would observe for the first time.

## Stop rules

Stop and ask the user if: a step would create a second allocation; preparation reports a lock
or legacy reader; UCloud returns an uncertain submission; any cleanup would touch a folder the
app does not own; or costs exceed what the user approved.
