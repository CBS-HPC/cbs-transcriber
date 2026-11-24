"use strict";

// History change list (ADR 0181): each saved version names its change, and opening it shows
// what changed word by word. Proven older merges, splits and deletions are reverted here
// through the existing exact inverses; the per-passage buttons are not shown meanwhile.
const historyChangeNames = {
  free_rewrite: "Text edit",
  word_correction: "Text edit",
  manual_save: "Text edit",
  word_timing_edit: "Word timing edit",
  original_restore: "Restore original",
  passage_change_undo: "Passage undo",
  passage_change_redo: "Passage redo",
  passage_speaker: "Speaker change",
  speaker_rename: "Speaker rename",
  passage_merge: "Merge",
  passage_split: "Split",
  passage_delete: "Delete",
  passage_merge_undo: "Undo merge",
  passage_split_undo: "Undo split",
  passage_delete_undo: "Restore passage",
  timing_realign: "Added word timing",
  // Same words as the earlier History labels, capitalised.
  result_merge: "Merged enhancement",
  result_conflict_review: "Conflict decisions",
  wording_adopt: "Adopted comparison wording",
  version_restore: "Restored earlier version",
  change_undo: "Undo last change",
  change_redo: "Redo",
  save_no_change: "Saved without changes",
  result_trashed: "Moved to Trash",
  result_restored: "Restored from Trash",
  legacy_observed: "Earlier history unknown",
  provider_result: "Transcription",
  enhancement_result: "Word timing / speakers",
  imported_result: "Import",
};
function historyChangeName(action) {
  return Object.hasOwn(historyChangeNames, action)
    ? historyChangeNames[action]
    : historyAction(action);
}
function historyPassageNumber(segmentId) {
  if (typeof segmentId !== "string") return null;
  const index = (state.recording?.segments ?? []).findIndex(
    (segment) => segment.id === segmentId,
  );
  return index < 0 ? null : index + 1;
}
// Only the current revision's proven inverses are offered (changeStepView fences it).
function historyRevertible(versionId) {
  const response = changeStepView();
  if (!response) return null;
  const proven = (
    Array.isArray(response.revertible) ? response.revertible : []
  ).find(
    (item) =>
      item?.version_id === versionId &&
      ["merge", "split"].includes(item.kind) &&
      typeof item.segment_id === "string",
  );
  if (proven) return proven;
  const deleted = (
    Array.isArray(response.deleted_segments) ? response.deleted_segments : []
  ).find(
    (item) =>
      item?.delete_version_id === versionId &&
      typeof item.segment_id === "string",
  );
  return deleted
    ? { kind: "delete", segment_id: deleted.segment_id, version_id: versionId }
    : null;
}
function historyChangeRow(version, context) {
  context.versions ??= new Map();
  context.undoneBy ??= new Map();
  context.redoneBy ??= new Map();
  context.versions.set(version.id, version);
  const change =
    version.change && typeof version.change === "object" ? version.change : {};
  // Rows load newest first, so an Undo/Redo row is seen before the version it names.
  if (version.action === "change_undo" && change.undone_version_id)
    context.undoneBy.set(change.undone_version_id, version.sequence);
  if (version.action === "change_redo" && change.redone_version_id)
    context.redoneBy.set(change.redone_version_id, version.sequence);
  const parts = [
    `Version ${version.sequence}`,
    historyChangeName(version.action),
  ];
  const passage = historyPassageNumber(change.segment_id);
  if (passage) parts.push(`passage ${passage}`);
  if (version.id === state.recording.result_roles?.working?.version_id)
    parts.push("Working transcript");
  const tags = [];
  const undone = context.undoneBy.get(version.id);
  if (undone) tags.push(`undone by version ${undone}`);
  const redone = context.redoneBy.get(version.id);
  if (redone) tags.push(`redone by version ${redone}`);
  if (historyRevertible(version.id)) tags.push("can be reverted");
  const tagMarkup = tags.length
    ? `<span class="history-tags">${tags.map((tag) => `<span class="history-tag">${h(tag)}</span>`).join("")}</span>`
    : "";
  return `<button class="history-version button${undone && !redone ? " history-undone" : ""}" data-history-version="${h(version.id)}"><strong>${h(parts.join(" · "))}</strong><span>${h(new Date(version.observed_at).toLocaleString("en-GB"))} · ${h(historyActor(version.actor_kind))}</span>${tagMarkup}<code>${h(version.payload_sha256)}</code></button>`;
}
function historyVersionLabel(context, versionId) {
  const version = context.versions?.get(versionId);
  return version ? `version ${version.sequence}` : "an earlier version";
}
function historyChangeNote(context, version) {
  const change = context.versions?.get(version.id)?.change ?? {};
  switch (version.action) {
    case "change_undo":
      return `Undid ${historyVersionLabel(context, change.undone_version_id)}.`;
    case "change_redo":
      return `Redid ${historyVersionLabel(context, change.redone_version_id)}.`;
    case "version_restore":
      return `Restored ${historyVersionLabel(context, change.restored_from_version_id)} as the newest version.`;
    case "passage_merge":
      return "Merged two neighbouring passages.";
    case "passage_split":
      return "Split one passage in two.";
    case "passage_delete":
      return "Deleted a passage.";
    case "timing_realign":
      return "Added word timing; the wording is unchanged.";
    default:
      return "";
  }
}
function historyTokens(tokens) {
  const paragraph = document.createElement("p");
  paragraph.className = "history-change-text";
  tokens.forEach((token, index) => {
    if (index) paragraph.append(" ");
    if (token.op === "skip") {
      const skip = document.createElement("span");
      skip.className = "history-skip";
      skip.textContent = `… ${token.count} unchanged words …`;
      paragraph.append(skip);
    } else if (token.op === "insert" || token.op === "delete") {
      const mark = document.createElement(
        token.op === "insert" ? "ins" : "del",
      );
      mark.textContent = token.text;
      paragraph.append(mark);
    } else paragraph.append(String(token.text ?? ""));
  });
  return paragraph;
}
function historyChangeItem(item) {
  const box = document.createElement("div");
  box.className = "history-change";
  const head = document.createElement("p");
  head.className = "history-change-head";
  const parts = [`Passage ${item.position}`, item.speaker];
  if (item.kind === "added") parts.push("added");
  else if (item.kind === "removed") parts.push("removed");
  else
    for (const change of item.changes ?? []) {
      if (change === "text") parts.push("text changed");
      else if (change === "speaker")
        parts.push(`speaker ${item.speaker_before} → ${item.speaker}`);
      else if (change === "timing")
        parts.push(
          item.timing_words
            ? `word timing changed for ${item.timing_words} word${item.timing_words === 1 ? "" : "s"}`
            : "passage timing changed",
        );
      else parts.push("details changed");
    }
  head.textContent = parts.join(" · ");
  box.append(head);
  if (item.rewritten) {
    const note = document.createElement("p");
    note.className = "history-change-text";
    note.textContent =
      "Passage rewritten; too long to compare word by word. See Full text.";
    box.append(note);
  } else if (Array.isArray(item.tokens) && item.tokens.length)
    box.append(historyTokens(item.tokens));
  return box;
}
function renderHistoryRevert(context, version) {
  const button = document.getElementById("history-revert"),
    note = document.getElementById("history-revert-note"),
    item = historyRevertible(version.id);
  context.revert = item ? { item, version } : null;
  button.hidden = note.hidden = !item;
  button.disabled = !item || state.dirty || changeStepBusy();
  button.title = state.dirty
    ? "Save or cancel your passage correction first."
    : "";
}
async function renderHistoryChanges(context, version, request) {
  const box = document.getElementById("history-changes");
  box.replaceChildren(document.createTextNode("Loading what changed…"));
  renderHistoryRevert(context, version);
  let result;
  try {
    result = await api(
      `/recordings/${context.id}/transcripts/${context.result}/versions/${version.id}/changes`,
      { background: true },
    );
  } catch (error) {
    if (currentHistory(context) && request === historyRequest)
      box.textContent = `What changed is unavailable. ${error.message}`;
    return;
  }
  if (
    !currentHistory(context) ||
    request !== historyRequest ||
    result.version_id !== version.id
  )
    return;
  renderChangeResult(box, result, historyChangeNote(context, version));
}
// Shared by History and the branch view (ADR 0182): builds DOM from text, never markup.
function renderChangeResult(box, result, note) {
  box.replaceChildren();
  const summary = document.createElement("p");
  if (result.too_large) summary.textContent = result.message;
  else if (!result.base)
    summary.textContent = `${note ? `${note} ` : ""}First saved version of this result: ${result.counts.added} passage${result.counts.added === 1 ? "" : "s"}.`;
  else {
    const { changed, added, removed } = result.counts;
    const counts = [
      changed && `${changed} passage${changed === 1 ? "" : "s"} changed`,
      added && `${added} added`,
      removed && `${removed} removed`,
      result.speaker_names?.length &&
        `${result.speaker_names.length} speaker name${result.speaker_names.length === 1 ? "" : "s"} changed`,
    ].filter(Boolean);
    summary.textContent = `${note ? `${note} ` : ""}${counts.length ? `${counts.join(", ")} compared with version ${result.base.sequence}.` : `No passage text, speaker or timing changed compared with version ${result.base.sequence}.`}`;
  }
  box.append(summary);
  if (result.too_large || !result.base) return;
  for (const name of result.speaker_names ?? []) {
    const line = document.createElement("p");
    line.className = "history-change-head";
    line.textContent = `Speaker name: ${name.before} → ${name.after}`;
    box.append(line);
  }
  for (const item of result.passages ?? []) box.append(historyChangeItem(item));
  if (result.truncated) {
    const more = document.createElement("p");
    more.className = "field-note";
    more.textContent =
      "Only the first 200 changed passages are listed; download both versions to compare the rest.";
    box.append(more);
  }
}
document.getElementById("history-revert").addEventListener(
  "click",
  handle(async () => {
    const context = historyContext,
      entry = context?.revert;
    if (!entry || !currentHistory(context)) return;
    if (state.dirty || changeStepBusy()) {
      document.getElementById("history-status").textContent =
        "Save or cancel your passage correction first.";
      return;
    }
    const { item, version } = entry,
      operation = {
        merge: "undo-merge",
        split: "undo-split",
        delete: "restore",
      }[item.kind],
      values =
        item.kind === "delete" ? { delete_version_id: item.version_id } : {},
      view = recordingContext(),
      revision = state.recording.revision;
    // The same exact inverses and Working choice as the former per-passage buttons.
    const target = manualEditTarget(
      `/recordings/${view.id}/segments/${item.segment_id}/${operation}`,
      "POST",
      values,
    );
    if (!(await workingEditChoice(target, true))) return;
    if (!currentHistory(context) || state.recording.revision !== revision)
      return;
    try {
      const done = await changePassageStructure(
        { id: item.segment_id },
        operation,
        values,
        view,
        revision,
      );
      if (!done) return;
      // The saved revert clears the editor view, so close by this History's own context.
      if (historyContext === context)
        document.getElementById("history-dialog").close();
      toast(
        `Version ${version.sequence} reverted; later edits elsewhere were kept.`,
      );
    } catch (error) {
      if (currentHistory(context))
        document.getElementById("history-status").textContent =
          `The change was not reverted. ${error.message}`;
    }
  }),
);
