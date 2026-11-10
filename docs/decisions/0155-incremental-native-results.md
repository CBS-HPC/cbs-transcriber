# 0155 — Preserve completed native results while the allocation continues

Accepted 9 November 2026. The user observed a completed JSON beside the processing log while
other files were running. Make supported finished members available without waiting for the
whole batch, retaining its existing owner, capture and cleanup semantics.

Freeze the submitted application name/version before allocation POST. Early retrieval is
enabled only for the observed served pair transcriber/1.12. Older journals without that
reference, unknown pairs, publisher/container version 1.22 and synthetic transcriber-batch
aliases keep terminal-only retrieval. Never rewrite an old submission to grant eligibility.

Require a fresh exact Processed observation for the frozen input, exact output selection by
input/model/language, complete valid JSON and identical bytes on two separate polling passes.
Cached log notes are display-only. Missing, changing or invalid candidates defer while RUNNING;
terminal recovery remains strict. Reuse bounded transfers, raw preservation, capture and atomic
per-member result commits. A later import must not overwrite an early result the user edited.

A member with preserved raw output is ineligible for fresh early download, with checks at
eligibility, transfer and coordinator receive. Its existing capture/save state settles through
terminal recovery; Retry cannot replace accepted bytes after capture-before-commit failure.
Retain one pending member until terminal state so existing owner claim/recovery continues to
monitor. Early completion never authorizes remote cleanup. Trash the owned run folder only
after terminal state and all existing preservation requirements.

Public producer evidence: Transcriber's [per-file pipeline](https://github.com/aau-claaudia/transcriber/blob/6e2f950297aaebe4572efcedab9c675b48ec8d67/app.py#L144-L207)
invokes writers before the processed observation. WhisperX's [writer implementation](https://github.com/m-bain/whisperX/blob/v3.8.6/whisperx/utils.py#L215-L229)
closes output before returning. This supports the finalization assumption but does not pin
the deployed UCloud image to that publisher commit. That mapping and real early-result
acceptance remain unverified. Two stable reads alone do not prove general finalization.

Synthetic checks cover early save and later manual edits, capture, partial/changing output,
unsupported applications, Stop, persistent guard denial, failed-commit Retry with changed
remote output and terminal-only cleanup. The module has 26 distinct cases; 97 existing native
regressions and independent preservation/guard checks passed. Affected checks followed the
final preservation and application-gating correction.
