"use strict";

// Deleted content is shown separately from the live transcript and never becomes playback,
// search, export or manual-review input until a proved, revision-fenced restoration succeeds.
const deletedPassagesSection = document.createElement("section");
deletedPassagesSection.id = "deleted-passages";
deletedPassagesSection.className = "deleted-passages";
deletedPassagesSection.setAttribute(
  "aria-labelledby",
  "deleted-passages-title",
);
deletedPassagesSection.hidden = true;
deletedPassagesSection.innerHTML = `<h3 id="deleted-passages-title">Deleted passages</h3>
  <p class="field-note">These passages are inactive. Restoring one keeps your later edits.</p>
  <p data-deleted-draft class="field-note" hidden>Save or cancel your current passage correction before restoring a deleted passage.</p>
  <fieldset class="deleted-passage-list" aria-label="Available deleted passages"></fieldset>`;
document
  .getElementById("transcript")
  .insertAdjacentElement("afterend", deletedPassagesSection);
let deletedPassagesView = null;

function currentDeletedPassages(view) {
  return (
    view === deletedPassagesView &&
    view.epoch === viewEpoch &&
    view.id === state.recording?.id &&
    view.result === state.recording?.active_transcript_id &&
    view.revision === state.recording?.revision &&
    view.head === state.recording?.active_version?.id
  );
}
function refreshDeletedPassageControls() {
  const view = deletedPassagesView;
  if (!view || !currentDeletedPassages(view)) return;
  const dirty = state.dirty;
  deletedPassagesSection.querySelector("[data-deleted-draft]").hidden = !dirty;
  deletedPassagesSection.querySelector("fieldset").disabled =
    dirty ||
    view.busy ||
    structureLocked() ||
    pendingResultMutations.has(view.id) ||
    timedWordSaving ||
    passageTextSaving ||
    !!state.comparison;
}
function deletedPassageError(view, key, message) {
  if (!currentDeletedPassages(view)) return;
  const row = [
    ...deletedPassagesSection.querySelectorAll("[data-deleted-item]"),
  ].find((item) => item.dataset.deletedItem === key);
  if (!row) return;
  let error = row.querySelector("[role=alert]");
  if (!error) {
    error = document.createElement("p");
    error.className = "deleted-passage-error";
    error.setAttribute("role", "alert");
    row.appendChild(error);
  }
  error.textContent = message;
}
async function restoreDeletedPassage(view, item) {
  if (
    !currentDeletedPassages(view) ||
    view.busy ||
    structureLocked() ||
    pendingResultMutations.has(view.id) ||
    timedWordSaving ||
    passageTextSaving ||
    state.comparison
  )
    return;
  if (state.dirty) {
    refreshDeletedPassageControls();
    return;
  }
  const context = recordingContext();
  // Match the shared structural writer's path so its Working choice has the same target.
  const path = `/recordings/${view.id}/segments/${item.segment_id}/restore`;
  const target = manualEditTarget(path, "POST", {
    delete_version_id: item.delete_version_id,
  });
  const key = `${item.delete_version_id}:${item.segment_id}`;
  view.busy = true;
  refreshDeletedPassageControls();
  try {
    const choice = await workingEditChoice(target, true);
    if (!choice || !currentDeletedPassages(view) || !sameRecordingView(context))
      return;
    if (state.dirty) return;
    const restored = await changePassageStructure(
      { id: item.segment_id },
      "restore",
      { delete_version_id: item.delete_version_id },
      context,
      view.revision,
    );
    if (restored) return;
  } catch (error) {
    if (currentDeletedPassages(view) && sameRecordingView(context))
      deletedPassageError(
        view,
        key,
        `Passage restoration was not confirmed. ${error.message}`,
      );
  } finally {
    view.busy = false;
    if (currentDeletedPassages(view)) refreshDeletedPassageControls();
  }
}
function renderDeletedPassages(payload) {
  const recording = state.recording;
  if (
    !payload ||
    !recording?.active_transcript_id ||
    payload.result_id !== recording.active_transcript_id ||
    payload.revision !== recording.revision
  ) {
    deletedPassagesView = null;
    deletedPassagesSection.hidden = true;
    return;
  }
  const items = payload.deleted_segments ?? [];
  const ids = new Set();
  if (
    !Array.isArray(items) ||
    items.length > 100 ||
    items.some((item) => {
      if (
        !item ||
        typeof item.segment_id !== "string" ||
        !item.segment_id ||
        item.segment_id.length > 200 ||
        typeof item.delete_version_id !== "string" ||
        !item.delete_version_id ||
        item.delete_version_id.length > 200 ||
        !Number.isInteger(item.insertion_index) ||
        item.insertion_index < 0 ||
        item.insertion_index > recording.segments.length ||
        typeof item.text_preview !== "string" ||
        Array.from(item.text_preview).length > 240 ||
        typeof item.text_truncated !== "boolean"
      )
        return true;
      const key = `${item.delete_version_id}:${item.segment_id}`;
      if (ids.has(key)) return true;
      ids.add(key);
      return false;
    })
  ) {
    deletedPassagesView = null;
    deletedPassagesSection.hidden = true;
    return;
  }
  const signature = JSON.stringify(items);
  if (
    deletedPassagesView &&
    currentDeletedPassages(deletedPassagesView) &&
    deletedPassagesView.signature === signature
  ) {
    refreshDeletedPassageControls();
    return;
  }
  const view = {
    epoch: viewEpoch,
    id: recording.id,
    result: recording.active_transcript_id,
    revision: recording.revision,
    head: recording.active_version?.id,
    signature,
    busy: false,
  };
  deletedPassagesView = view;
  const list = deletedPassagesSection.querySelector("fieldset");
  list.replaceChildren();
  deletedPassagesSection.hidden = !items.length;
  document.getElementById("deleted-passages-title").textContent =
    `Deleted passages (${items.length})`;
  items.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "deleted-passage-item";
    row.dataset.deletedItem = `${item.delete_version_id}:${item.segment_id}`;
    const label = document.createElement("span");
    label.className = "badge";
    label.textContent = "Inactive";
    const preview = document.createElement("p");
    preview.className = "deleted-passage-preview";
    preview.textContent = item.text_preview + (item.text_truncated ? "…" : "");
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "button";
    restore.dataset.deletedRestore = "";
    restore.textContent = "Restore passage";
    restore.setAttribute("aria-label", `Restore deleted passage ${index + 1}`);
    restore.addEventListener("click", () => restoreDeletedPassage(view, item));
    row.append(label, preview, restore);
    list.appendChild(row);
  });
  refreshDeletedPassageControls();
}
document
  .getElementById("transcript")
  .addEventListener("input", refreshDeletedPassageControls);
// A clean editor can be cancelled without changing the content revision or lookup receipt.
document.getElementById("transcript").addEventListener("click", () => {
  queueMicrotask(refreshDeletedPassageControls);
});
if (mergeUndoOptions?.response)
  renderDeletedPassages(mergeUndoOptions.response);
