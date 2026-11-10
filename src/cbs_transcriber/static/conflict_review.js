"use strict";

// Loaded after app.js: reuse its view/draft guards and its single mutation fence.
function conflictReviewButton(result, context, busy) {
  if (
    result.trashed_at ||
    !context.result ||
    result.id === context.result ||
    result.source.kind !== "enhancement" ||
    result.source.source_result_id !== context.result
  )
    return "";
  return `<button class="button" type="button" data-conflict-review="${h(result.id)}" ${busy ? "disabled" : ""}>Review conflicts</button>`;
}

document.body.insertAdjacentHTML(
  "beforeend",
  `<dialog id="conflict-review-dialog" class="settings-dialog" aria-labelledby="conflict-review-title" aria-describedby="conflict-review-explanation">
    <div class="dialog-heading"><h2 id="conflict-review-title">Review passage decisions</h2><button type="button" class="icon-button" data-close-conflict-review aria-label="Close conflict review">×</button></div>
    <p id="conflict-review-explanation" class="dialog-intro">Choose incoming values only after reviewing the passage. Keep current records a deliberate decision. Unselected groups stay unchanged, and earlier versions remain restorable.</p>
    <p id="conflict-review-source" class="field-note"></p><p id="conflict-review-status" role="status" tabindex="-1"></p>
    <p id="conflict-review-error" class="settings-error" role="alert" hidden></p><p id="conflict-review-summary"></p>
    <div id="conflict-review-passages" aria-label="Passage decisions"></div>
    <p id="conflict-review-truncated" class="field-note" hidden>Only the first 200 changed passages are displayed. Decisions can apply only to these visible passages.</p>
    <div class="dialog-actions"><button id="conflict-review-refresh" class="button" type="button">Refresh preview</button><button class="button" type="button" data-close-conflict-review>Cancel</button><button id="conflict-review-apply" class="button primary" type="button" disabled>Record decisions</button></div>
  </dialog>`,
);
const conflictReviewDialog = $("#conflict-review-dialog");
let conflictReviewContext = null;
function currentConflictReview(context) {
  return (
    conflictReviewContext === context &&
    conflictReviewDialog.open &&
    sameRecordingView(context)
  );
}
function conflictReviewFocus() {
  const active = document.activeElement;
  if (!conflictReviewDialog.contains(active)) return null;
  return active.matches("[data-conflict-choice]")
    ? { passage: active.dataset.passage, group: active.dataset.group }
    : active.id
      ? { id: active.id }
      : null;
}
function restoreConflictReviewFocus(context, focus) {
  if (
    !focus ||
    !currentConflictReview(context) ||
    ![
      document.body,
      conflictReviewDialog,
      $("#conflict-review-status"),
    ].includes(document.activeElement)
  )
    return;
  const target = focus.id
    ? document.getElementById(
        focus.id === "conflict-review-apply" && context.needsRefresh
          ? "conflict-review-refresh"
          : focus.id,
      )
    : [...conflictReviewDialog.querySelectorAll("[data-conflict-choice]")].find(
        (select) =>
          select.dataset.passage === focus.passage &&
          select.dataset.group === focus.group,
      );
  if (target && conflictReviewDialog.contains(target) && !target.disabled)
    target.focus();
}
function conflictValue(value, timing) {
  if (value === null) return "Passage unavailable";
  return timing ? JSON.stringify(value, null, 2) : String(value);
}
function renderConflictReview(context) {
  const preview = context.preview,
    busy =
      context.loading ||
      context.applying ||
      pendingResultMutations.has(context.id);
  $("#conflict-review-refresh").disabled = busy;
  $("#conflict-review-apply").disabled =
    busy || context.needsRefresh || !preview?.summary.decisions;
  $("#conflict-review-source").textContent =
    `Incoming: ${preview?.incoming_label || context.incomingLabel}`;
  $("#conflict-review-summary").textContent = preview
    ? `${preview.summary.decisions} decision(s) · Keep current: ${preview.summary.current} · Use incoming: ${preview.summary.incoming} · Passages changed: ${preview.summary.changed_passages}`
    : "";
  $("#conflict-review-passages").innerHTML = (preview?.passages || [])
    .map(
      (passage, index) =>
        `<article data-conflict-passage="${h(passage.id)}" style="border-top:1px solid var(--border);padding:1rem 0"><h3>${passage.ordinal ? `Passage ${passage.ordinal}` : "Removed passage"}</h3>${Object.entries(
          passage.groups,
        )
          .map(([group, values]) => {
            const selected = context.decisions.find(
              (item) => item.passage_id === passage.id && item.group === group,
            )?.choice;
            const id = `conflict-choice-${index}-${group}`;
            const cells = ["base", "current", "incoming"]
              .map(
                (name) =>
                  `<div style="min-width:0"><strong>${{ base: "Frozen base", current: "Current", incoming: "Incoming" }[name]}</strong><pre style="white-space:pre-wrap;overflow:auto;max-height:14rem;font:inherit">${h(conflictValue(values[name], group === "timing"))}</pre></div>`,
              )
              .join("");
            return `<section data-conflict-group="${group}"><h4>${{ wording: "Wording", timing: "Timing", speaker: "Speaker assignment" }[group]}</h4>${group === "timing" ? `<details><summary>Compare exact timing values</summary><div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem">${cells}</div></details>` : `<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem">${cells}</div>`}<label for="${id}">Decision<select id="${id}" data-conflict-choice data-passage="${h(passage.id)}" data-group="${group}" ${busy ? "disabled" : ""}><option value="" ${!selected ? "selected" : ""}>Leave undecided</option><option value="current" ${selected === "current" ? "selected" : ""}>Keep current</option><option value="incoming" ${selected === "incoming" ? "selected" : ""} ${!values.incoming_allowed ? "disabled" : ""}>Use incoming</option></select></label>${values.refusal ? `<p class="field-note">${h(values.refusal)}</p>` : ""}</section>`;
          })
          .join("")}</article>`,
    )
    .join("");
  $("#conflict-review-truncated").hidden = !preview?.truncated;
}
async function loadConflictReview(context) {
  if (
    !currentConflictReview(context) ||
    context.loading ||
    context.applying ||
    pendingResultMutations.has(context.id)
  )
    return;
  const focus = conflictReviewFocus();
  context.loading = true;
  context.needsRefresh = true;
  $("#conflict-review-error").hidden = true;
  $("#conflict-review-status").textContent =
    "Loading current passage decisions…";
  if (focus) $("#conflict-review-status").focus({ preventScroll: true });
  renderConflictReview(context);
  try {
    const current = await api(`/recordings/${context.id}`);
    if (!currentConflictReview(context)) return;
    if (
      current.active_transcript_id !== context.result ||
      current.revision < state.recording.revision
    )
      throw new Error(
        "The reviewed result changed. Close this review and reload the recording.",
      );
    const preview = await api(
      `/recordings/${context.id}/transcripts/${context.result}/conflicts-preview`,
      {
        method: "POST",
        body: {
          incoming_result_id: context.incoming,
          revision: current.revision,
          decisions: context.decisions,
        },
      },
    );
    if (!currentConflictReview(context)) return;
    if (
      preview.result_id !== context.result ||
      preview.incoming_result_id !== context.incoming ||
      preview.revision !== current.revision ||
      !/^[a-f0-9]{64}$/.test(preview.fingerprint)
    )
      throw new Error(
        "The conflict preview does not match this reviewed result.",
      );
    context.preview = preview;
    context.workingPreview = {
      recording: current,
      current: () =>
        currentConflictReview(context) &&
        !context.needsRefresh &&
        !context.loading &&
        context.preview === preview,
    };
    context.decisions = preview.decisions;
    context.needsRefresh = false;
    $("#conflict-review-status").textContent =
      "Preview only. Choose each passage group deliberately before recording decisions.";
  } catch (error) {
    if (currentConflictReview(context)) {
      $("#conflict-review-status").textContent =
        "Refresh the preview before recording decisions.";
      $("#conflict-review-error").textContent = error.message;
      $("#conflict-review-error").hidden = false;
    }
  } finally {
    context.loading = false;
    if (currentConflictReview(context)) {
      renderConflictReview(context);
      restoreConflictReviewFocus(context, focus);
    }
  }
}
$("#results-dialog").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-conflict-review]"),
    manager = resultsContext;
  if (
    !button ||
    !manager ||
    !currentResults(manager) ||
    manager.loading ||
    conflictReviewDialog.open ||
    pendingResultMutations.has(manager.id)
  )
    return;
  const incoming = manager.recording.transcripts.find(
    (result) => result.id === button.dataset.conflictReview,
  );
  if (!incoming || !conflictReviewButton(incoming, manager, false)) return;
  const context = {
    ...recordingContext(),
    manager,
    incoming: incoming.id,
    incomingLabel: incoming.label,
    decisions: [],
    preview: null,
    needsRefresh: true,
  };
  conflictReviewContext = context;
  conflictReviewDialog.showModal();
  await loadConflictReview(context);
});
$("#results-dialog").addEventListener("close", () =>
  conflictReviewDialog.close(),
);
conflictReviewDialog.addEventListener("close", () => {
  conflictReviewContext = null;
  pendingWorkingEditChoice = null;
});
conflictReviewDialog.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-conflict-review]"))
    conflictReviewDialog.close();
});
$("#conflict-review-refresh").addEventListener("click", () => {
  if (conflictReviewContext) loadConflictReview(conflictReviewContext);
});
$("#conflict-review-passages").addEventListener("change", async (event) => {
  const select = event.target.closest("[data-conflict-choice]"),
    context = conflictReviewContext;
  if (
    !select ||
    !context ||
    !currentConflictReview(context) ||
    context.loading ||
    context.applying
  )
    return;
  context.decisions = context.decisions.filter(
    (item) =>
      item.passage_id !== select.dataset.passage ||
      item.group !== select.dataset.group,
  );
  if (select.value)
    context.decisions.push({
      passage_id: select.dataset.passage,
      group: select.dataset.group,
      choice: select.value,
    });
  await loadConflictReview(context);
});
$("#conflict-review-apply").addEventListener("click", async () => {
  const context = conflictReviewContext;
  if (
    !context ||
    !currentConflictReview(context) ||
    context.loading ||
    context.applying ||
    context.needsRefresh ||
    !context.preview?.summary.decisions ||
    pendingResultMutations.has(context.id) ||
    $("#save-status").textContent === "Saving…" ||
    !canDiscardPassage()
  )
    return;
  const focus = conflictReviewFocus();
  context.applying = true;
  pendingResultMutations.add(context.id);
  $("#conflict-review-error").hidden = true;
  $("#conflict-review-status").textContent = "Recording passage decisions…";
  if (focus) $("#conflict-review-status").focus({ preventScroll: true });
  renderConflictReview(context);
  renderResults();
  if (currentResults(context.manager)) renderManagedResults(context.manager);
  const controls = [
    ...document.querySelectorAll(
      "#transcript textarea, #transcript button, #result-select, #compare-result, #results-manage",
    ),
  ];
  const disabled = controls.map((control) => control.disabled);
  controls.forEach((control) => {
    control.disabled = true;
  });
  try {
    const outcome = await api(
      `/recordings/${context.id}/transcripts/${context.result}/conflicts`,
      {
        method: "POST",
        workingPreview: context.workingPreview,
        body: {
          incoming_result_id: context.incoming,
          revision: context.preview.revision,
          decisions: context.preview.decisions,
          fingerprint: context.preview.fingerprint,
        },
      },
    );
    if (!sameRecordingView(context)) return;
    const recording = outcome.recording;
    if (
      (state.recording.notes_revision || 0) > (recording.notes_revision || 0)
    ) {
      recording.notes = state.recording.notes;
      recording.notes_revision = state.recording.notes_revision;
    }
    if (!acceptRecording(context, recording)) return;
    clearEdit();
    state.replayEnd = null;
    renderEditorDetails();
    renderTranscript();
    if (conflictReviewContext === context) conflictReviewDialog.close();
    if (resultsContext === context.manager && $("#results-dialog").open) {
      resultsContext = {
        ...recordingContext(),
        recording,
        trash: context.manager.trash,
      };
      $("#results-status").textContent =
        "Passage decisions recorded. Earlier versions remain in History.";
    }
    toast("Passage decisions recorded.");
  } catch (error) {
    if (currentConflictReview(context)) {
      context.needsRefresh = true;
      $("#conflict-review-status").textContent =
        "No review was confirmed. Refresh the preview before trying again.";
      $("#conflict-review-error").textContent = error.message;
      $("#conflict-review-error").hidden = false;
    }
  } finally {
    context.applying = false;
    pendingResultMutations.delete(context.id);
    if (currentConflictReview(context)) renderConflictReview(context);
    if (context.epoch === viewEpoch && context.id === state.recording?.id) {
      controls.forEach((control, index) => {
        if (control.isConnected) control.disabled = disabled[index];
      });
      renderResults();
      if (resultsContext && currentResults(resultsContext))
        renderManagedResults(resultsContext);
    }
    restoreConflictReviewFocus(context, focus);
  }
});
