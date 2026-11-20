"use strict";

// Undo last change / Redo (ADR 0180). The server proves each step from saved version history
// and returns it with the passage-undo options; these buttons only offer that exact step.
// Typing undo inside a text box stays with the browser: there is no global Ctrl+Z.
const changeStepNouns = {
  free_rewrite: "text edit",
  word_correction: "text edit",
  manual_save: "text edit",
  word_timing_edit: "word timing edit",
  original_restore: "Restore original",
  passage_speaker: "speaker change",
  speaker_rename: "speaker rename",
  passage_merge: "merge",
  passage_split: "split",
  passage_delete: "delete",
  passage_merge_undo: "‘Undo merge’",
  passage_split_undo: "‘Undo split’",
  passage_delete_undo: "‘Restore passage’",
  timing_realign: "added word timing",
  result_merge: "safe updates",
  result_conflict_review: "conflict decisions",
  wording_adopt: "wording adoption",
  version_restore: "version restore",
};
let changeStepPending = false;

function changeStepLabel(kind, block) {
  const noun = Object.hasOwn(changeStepNouns, block?.action)
    ? changeStepNouns[block.action]
    : "change";
  return kind === "undo" ? `Undo last ${noun}` : `Redo ${noun}`;
}
// Only a reply for the exact visible result, revision and head version is ever offered.
function changeStepView() {
  const response = mergeUndoOptions?.response,
    recording = state.recording;
  if (
    !featureOn("undo_last_change") ||
    !response ||
    !recording?.active_transcript_id ||
    response.result_id !== recording.active_transcript_id ||
    response.revision !== recording.revision ||
    response.head_version_id !== recording.active_version?.id
  )
    return null;
  return response;
}
function changeStepBlock(response, kind) {
  const block = response?.[kind];
  return block?.available === true &&
    typeof block.version_id === "string" &&
    block.version_id &&
    typeof block.action === "string" &&
    Number.isInteger(block.passages_changed) &&
    typeof block.confirm === "boolean"
    ? block
    : null;
}
function changeStepBusy() {
  return (
    changeStepPending ||
    structureLocked() ||
    pendingResultMutations.has(state.recording?.id) ||
    timedWordSaving ||
    passageTextSaving ||
    !!state.comparison
  );
}
function changeStepTitle(kind, block) {
  const parts = [`Version ${block.sequence}`];
  const time = new Date(block.observed_at);
  if (!Number.isNaN(time.getTime())) parts.push(time.toLocaleString("en-GB"));
  if (Number.isInteger(block.passage_number))
    parts.push(`passage ${block.passage_number}`);
  if (block.passages_changed > 1)
    parts.push(`${block.passages_changed} passages change`);
  if (block.speakers_changed) parts.push("speaker names change");
  const effect =
    kind === "undo"
      ? "Saves the text as it was before this change; History keeps both."
      : "Saves the undone change again; History keeps both.";
  // The button may truncate a long label, so the tooltip repeats it in full.
  return `${changeStepLabel(kind, block)}: ${parts.join(" · ")}. ${effect}`;
}
function renderChangeSteps() {
  const response = changeStepView();
  for (const [kind, button, icon] of [
    ["undo", document.getElementById("undo-last"), "↶"],
    ["redo", document.getElementById("redo-last"), "↷"],
  ]) {
    if (!button) continue;
    const block = changeStepBlock(response, kind);
    const text = block
      ? `${icon} ${changeStepLabel(kind, block)}`
      : `${icon} ${kind === "undo" ? "Undo" : "Redo"}`;
    if (button.textContent !== text) button.textContent = text;
    button.disabled = !block || state.dirty || changeStepBusy();
    const message = response?.[kind]?.message;
    button.title = !state.recording?.active_transcript_id
      ? ""
      : !response
        ? "Checking this result's saved history…"
        : !block
          ? typeof message === "string"
            ? message.slice(0, 300)
            : ""
          : state.dirty
            ? "Save or cancel your passage correction first."
            : changeStepTitle(kind, block);
  }
}
async function reloadAfterStaleStep(context) {
  // Another window or a background timing Apply saved first: show its newest change.
  const latest = await api(`/recordings/${context.id}`, { background: true });
  if (
    !sameRecordingView(context) ||
    state.dirty ||
    latest.active_transcript_id !== context.result ||
    latest.revision <= state.recording.revision ||
    !acceptRecording(context, latest)
  )
    return false;
  clearEdit();
  renderEditorDetails();
  renderTranscript();
  renderComparison();
  return true;
}
async function runChangeStep(kind) {
  const response = changeStepView(),
    block = changeStepBlock(response, kind);
  if (!block || state.dirty || changeStepBusy()) return;
  const context = recordingContext(),
    revision = state.recording.revision,
    label = changeStepLabel(kind, block);
  if (block.confirm) {
    const count = block.passages_changed;
    const message = `${label}? This changes ${count} passage${count === 1 ? "" : "s"}${block.speakers_changed ? " and speaker names" : ""}. The current text stays in History.`;
    if (
      !confirm(message) ||
      !sameRecordingView(context) ||
      state.recording.revision !== revision
    )
      return;
  }
  const path = `/recordings/${context.id}/transcripts/${context.result}/${kind}-last`,
    body = {
      revision,
      head_version_id: response.head_version_id,
      version_id: block.version_id,
    };
  changeStepPending = true;
  passageStructurePending = true;
  passageStructureRecording = context.id;
  renderChangeSteps();
  if (typeof refreshDeletedPassageControls === "function")
    refreshDeletedPassageControls();
  // Same lock as a passage structure change: nothing else edits this result meanwhile.
  let controls = [],
    disabled = [];
  try {
    if (!(await workingEditChoice(manualEditTarget(path, "POST", body), true)))
      return;
    if (
      !sameRecordingView(context) ||
      state.recording.revision !== revision ||
      state.dirty
    )
      return;
    controls = [
      ...document.querySelectorAll(
        "#transcript textarea, #transcript [data-action], #transcript [data-review-status], #transcript [data-review-retry], [data-deleted-restore], #passage-merge-form button, #passage-merge-speaker",
      ),
    ];
    disabled = controls.map((element) => element.disabled);
    controls.forEach((element) => {
      element.disabled = true;
    });
    saved("Saving…");
    const recording = await api(path, { method: "POST", body });
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    state.replayEnd = null;
    renderEditorDetails();
    renderTranscript();
    renderComparison();
  } catch (error) {
    if (sameRecordingView(context)) {
      saved(`${label} was not saved — transcript unchanged`);
      if (error.status === 409) {
        const reloaded = await reloadAfterStaleStep(context).catch(() => false);
        if (reloaded)
          error.message +=
            " The newest saved change is now shown; review the Undo button again.";
      }
    }
    throw error;
  } finally {
    changeStepPending = false;
    passageStructurePending = false;
    passageStructureRecording = null;
    controls.forEach((element, index) => {
      if (element.isConnected) element.disabled = disabled[index];
    });
    // A transcript rendered during the lock marks what it disabled (renderTranscript).
    document
      .querySelectorAll("#transcript [data-structure-disabled]")
      .forEach((element) => {
        element.disabled = element.dataset.structureDisabled === "true";
        delete element.dataset.structureDisabled;
      });
    if (
      context.epoch === viewEpoch &&
      context.id === state.recording?.id &&
      context.result === state.recording?.active_transcript_id
    ) {
      refreshMergeUndoOptions();
      if (typeof renderManualReviews === "function") renderManualReviews();
    }
    renderChangeSteps();
  }
}
document.getElementById("undo-last").addEventListener(
  "click",
  handle(() => runChangeStep("undo")),
);
document.getElementById("redo-last").addEventListener(
  "click",
  handle(() => runChangeStep("redo")),
);
// Drafts and pending saves disable the buttons; a clean editor can be cancelled freely.
document
  .getElementById("transcript")
  .addEventListener("input", renderChangeSteps);
document.getElementById("transcript").addEventListener("click", () => {
  queueMicrotask(renderChangeSteps);
});
renderChangeSteps();
