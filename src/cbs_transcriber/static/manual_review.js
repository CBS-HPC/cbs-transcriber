"use strict";

// Manual review describes saved passage content, independently of ASR hints and Working.
const manualReviewLabels = {
  not_checked: "Not checked",
  checked: "Checked",
  needs_review: "Needs review",
};
let manualReviews = null;
const nextManualReviewButton = document.getElementById("next-uncertain");
nextManualReviewButton.disabled = true;
nextManualReviewButton.title =
  "Go to the next Not checked or Needs review passage, wrapping to the beginning.";

function currentManualReviews(view) {
  return (
    view === manualReviews &&
    view.epoch === viewEpoch &&
    view.id === state.recording?.id &&
    view.result === state.recording?.active_transcript_id &&
    view.revision === state.recording?.revision &&
    view.version === state.recording?.active_version?.id
  );
}
function manualReviewLocked(view) {
  return structureLocked() || pendingResultMutations.has(view.id);
}
function manualReviewPassages(view) {
  if (!view || !currentManualReviews(view)) return [];
  return [...document.querySelectorAll("#transcript > .passage")];
}
function canNavigateManualReviews(view) {
  return (
    view &&
    currentManualReviews(view) &&
    view.ready &&
    !view.loading &&
    !view.loadError &&
    !manualReviewLocked(view) &&
    !document.getElementById("history-dialog").open
  );
}
function refreshManualReviewNavigation() {
  const view = manualReviews;
  nextManualReviewButton.disabled =
    !canNavigateManualReviews(view) ||
    !manualReviewPassages(view).some((passage) => {
      const row = view.rows.get(passage.dataset.segment);
      return row && row.status !== "checked";
    });
  if (view && currentManualReviews(view) && view.focusId) {
    const passage = document.getElementById(`passage-${view.focusId}`);
    passage?.classList.add("review-focus");
  }
}
function nextManualReview() {
  const view = manualReviews;
  if (!canNavigateManualReviews(view)) {
    refreshManualReviewNavigation();
    return;
  }
  const passages = manualReviewPassages(view);
  const current = passages.findIndex(
    (passage) => passage.dataset.segment === view.focusId,
  );
  let next = null;
  for (let step = 1; step <= passages.length; step++) {
    const passage = passages[(current + step) % passages.length];
    const row = view.rows.get(passage.dataset.segment);
    if (row && row.status !== "checked") {
      next = passage;
      break;
    }
  }
  if (!next || !currentManualReviews(view)) {
    refreshManualReviewNavigation();
    return;
  }
  view.focusId = next.dataset.segment;
  document.querySelectorAll("#transcript .review-focus").forEach((passage) => {
    passage.classList.toggle("review-focus", passage === next);
  });
  next.classList.add("review-focus");
  next.scrollIntoView({ block: "center" });
  const segment = state.recording.segments.find(
    (item) => item.id === view.focusId,
  );
  if (Number.isFinite(segment?.start)) seekTo(segment.start);
}
nextManualReviewButton.addEventListener("click", nextManualReview);
document
  .getElementById("history-dialog")
  .addEventListener("toggle", refreshManualReviewNavigation);
document
  .getElementById("history-dialog")
  .addEventListener("close", refreshManualReviewNavigation);
function manualReviewDraft(sid) {
  return (
    String(state.editing) === sid &&
    (state.dirty || timedWordSaving || passageTextSaving)
  );
}
function manualReviewRow(value, ids) {
  if (
    !value ||
    !ids.has(String(value.segment_id)) ||
    !Object.hasOwn(manualReviewLabels, value.status) ||
    !Number.isSafeInteger(value.review_revision) ||
    value.review_revision < 0 ||
    !/^[a-f0-9]{64}$/u.test(value.passage_sha256)
  )
    throw new Error(
      "Review progress did not match this saved passage. Reload it.",
    );
  return { ...value, segment_id: String(value.segment_id) };
}
function manualReviewScope(view, response) {
  if (
    response.result_id !== view.result ||
    response.revision !== view.revision ||
    response.version_id !== view.version ||
    response.read_only !== false
  )
    throw new Error(
      "The saved transcript changed. Reload it before marking passages.",
    );
}
function manualReviewControls(view, row, sid) {
  const select = row.querySelector("[data-review-status]");
  select.disabled =
    !view.ready || view.pending.has(sid) || manualReviewLocked(view);
  select.querySelector('[value="checked"]').disabled = manualReviewDraft(sid);
  select.title = manualReviewDraft(sid)
    ? "Save or cancel this passage's correction before marking it Checked."
    : "Review of the saved passage. Changing it later requires review again.";
  const retry = row.querySelector("[data-review-retry]");
  if (retry) retry.disabled = select.disabled;
}
function refreshManualReviewControls(segmentId = state.editing) {
  const view = manualReviews;
  if (!view || !currentManualReviews(view)) return;
  const row = document
    .getElementById(`passage-${segmentId}`)
    ?.querySelector("[data-manual-review]");
  if (row) manualReviewControls(view, row, row.dataset.manualReview);
}
function paintManualReviews(view, only = null) {
  if (!currentManualReviews(view)) return;
  const progress = document.getElementById("manual-review-progress");
  progress.hidden = false;
  const checked = [...view.rows.values()].filter(
    (row) => row.status === "checked",
  ).length;
  const needs = [...view.rows.values()].filter(
    (row) => row.status === "needs_review",
  ).length;
  const summary = document.createElement("span");
  summary.dataset.reviewSummary = "";
  summary.textContent = view.ready
    ? `Manual review · Checked ${checked} of ${view.ids.size} · Needs review ${needs}`
    : view.loadError
      ? "Manual review · Progress unavailable"
      : "Manual review · Loading progress…";
  progress.replaceChildren(summary);
  if (view.loadError) {
    const message = document.createElement("span");
    message.setAttribute("role", "alert");
    message.textContent = view.loadError;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button";
    retry.textContent = "Retry review progress";
    retry.disabled = view.loading || manualReviewLocked(view);
    retry.addEventListener("click", () => loadManualReviews(view));
    progress.append(message, retry);
  }
  state.recording.segments.forEach((segment, index) => {
    const sid = String(segment.id);
    if (only !== null && sid !== only) return;
    const heading = document
      .getElementById(`passage-${sid}`)
      ?.querySelector(".passage-heading");
    if (!heading) return;
    let row = heading.querySelector("[data-manual-review]");
    if (!row) {
      row = document.createElement("span");
      row.className = "manual-review-row";
      row.dataset.manualReview = sid;
      const select = document.createElement("select");
      select.dataset.reviewStatus = "";
      select.setAttribute(
        "aria-label",
        `Review status for passage ${index + 1}`,
      );
      Object.entries(manualReviewLabels).forEach(([value, label]) => {
        select.add(new Option(label, value));
      });
      select.addEventListener("change", () => {
        // In-place speaker/word saves may retain this heading across content revisions.
        const current = manualReviews;
        if (!current || !currentManualReviews(current) || !row.isConnected)
          return;
        const desired = select.value;
        select.value = current.rows.get(sid)?.status || "not_checked";
        saveManualReview(current, sid, desired);
      });
      row.appendChild(select);
      heading.insertBefore(row, heading.querySelector(".passage-actions"));
    }
    const select = row.querySelector("select");
    select.value = view.rows.get(sid)?.status || "not_checked";
    row
      .querySelectorAll("[data-review-message], [data-review-retry]")
      .forEach((node) => node.remove());
    const error = view.errors.get(sid);
    if (error || view.pending.has(sid)) {
      const note = document.createElement("span");
      note.dataset.reviewMessage = "";
      note.setAttribute("role", error ? "alert" : "status");
      note.textContent = error?.message || "Saving review…";
      row.appendChild(note);
    }
    if (error?.desired) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.dataset.reviewRetry = "";
      retry.textContent = "Retry";
      retry.setAttribute(
        "aria-label",
        `Retry review status for passage ${index + 1}`,
      );
      retry.addEventListener("click", () =>
        retryManualReview(view, sid, error),
      );
      row.appendChild(retry);
    }
    manualReviewControls(view, row, sid);
  });
  refreshManualReviewNavigation();
}
async function loadManualReviews(view) {
  if (!currentManualReviews(view)) return false;
  const load = ++view.load;
  view.loading = true;
  paintManualReviews(view);
  try {
    const response = await api(
      `/recordings/${view.id}/passage-reviews?result_id=${encodeURIComponent(view.result)}`,
      { background: true },
    );
    if (!currentManualReviews(view) || view.load !== load) return false;
    manualReviewScope(view, response);
    if (
      !Array.isArray(response.passages) ||
      response.passages.length !== view.ids.size
    )
      throw new Error("Review progress is incomplete. Retry loading it.");
    const rows = new Map();
    for (const value of response.passages) {
      const row = manualReviewRow(value, view.ids);
      if (rows.has(row.segment_id))
        throw new Error("Review progress contains a repeated passage.");
      const previous = view.rows.get(row.segment_id);
      rows.set(
        row.segment_id,
        previous?.review_revision > row.review_revision ? previous : row,
      );
    }
    view.rows = rows;
    view.ready = true;
    view.loadError = null;
    return true;
  } catch (error) {
    if (currentManualReviews(view) && view.load === load)
      view.loadError = `Could not load review progress. ${error.message}`;
    return false;
  } finally {
    if (currentManualReviews(view) && view.load === load) {
      view.loading = false;
      paintManualReviews(view);
    }
  }
}
async function saveManualReview(view, sid, desired) {
  if (
    !currentManualReviews(view) ||
    !view.ready ||
    view.pending.has(sid) ||
    manualReviewLocked(view) ||
    !Object.hasOwn(manualReviewLabels, desired) ||
    (desired === "checked" && manualReviewDraft(sid))
  ) {
    if (currentManualReviews(view)) paintManualReviews(view, sid);
    return;
  }
  const expected = view.rows.get(sid);
  if (!expected || expected.status === desired) return;
  const operation = {};
  view.pending.set(sid, operation);
  view.errors.delete(sid);
  paintManualReviews(view, sid);
  try {
    const response = await api(
      `/recordings/${view.id}/segments/${encodeURIComponent(sid)}/review`,
      {
        method: "PATCH",
        background: true,
        body: {
          result_id: view.result,
          revision: view.revision,
          status: desired,
          review_revision: expected.review_revision,
          passage_sha256: expected.passage_sha256,
        },
      },
    );
    if (!currentManualReviews(view) || view.pending.get(sid) !== operation)
      return;
    manualReviewScope(view, response);
    const row = manualReviewRow(response.passage, view.ids);
    if (
      row.segment_id !== sid ||
      row.status !== desired ||
      row.review_revision <= expected.review_revision
    )
      throw new Error(
        "The saved review status could not be confirmed. Retry to check it.",
      );
    if (row.review_revision < view.rows.get(sid).review_revision)
      throw new Error("Review status changed elsewhere. Choose again.");
    view.rows.set(sid, row);
  } catch (error) {
    if (currentManualReviews(view) && view.pending.get(sid) === operation)
      view.errors.set(sid, {
        message: `Review was not confirmed. ${error.message}`,
        desired,
        expected,
      });
  } finally {
    if (currentManualReviews(view) && view.pending.get(sid) === operation) {
      view.pending.delete(sid);
      paintManualReviews(view, sid);
    }
  }
}
async function retryManualReview(view, sid, error) {
  if (
    !currentManualReviews(view) ||
    view.pending.has(sid) ||
    manualReviewLocked(view)
  )
    return;
  const operation = {};
  view.pending.set(sid, operation);
  paintManualReviews(view, sid);
  const loaded = await loadManualReviews(view);
  if (!currentManualReviews(view) || view.pending.get(sid) !== operation)
    return;
  view.pending.delete(sid);
  const latest = view.rows.get(sid);
  if (loaded && latest.status === error.desired) view.errors.delete(sid);
  else if (
    loaded &&
    latest.review_revision === error.expected.review_revision &&
    latest.passage_sha256 === error.expected.passage_sha256
  ) {
    await saveManualReview(view, sid, error.desired);
    return;
  } else if (loaded) {
    view.errors.set(sid, {
      message: "Review status changed elsewhere. Choose again.",
    });
  }
  paintManualReviews(view, sid);
}
function renderManualReviews() {
  const recording = state.recording;
  if (!recording?.active_transcript_id || !recording.active_version?.id) {
    manualReviews = null;
    const progress = document.getElementById("manual-review-progress");
    if (progress) progress.hidden = true;
    refreshManualReviewNavigation();
    return;
  }
  if (!manualReviews || !currentManualReviews(manualReviews)) {
    const previous = manualReviews;
    manualReviews = {
      epoch: viewEpoch,
      id: recording.id,
      result: recording.active_transcript_id,
      revision: recording.revision,
      version: recording.active_version.id,
      ids: new Set(recording.segments.map((segment) => String(segment.id))),
      rows: new Map(),
      errors: new Map(),
      pending: new Map(),
      load: 0,
      ready: false,
      loading: false,
      loadError: null,
      focusId:
        previous?.epoch === viewEpoch &&
        previous.id === recording.id &&
        previous.result === recording.active_transcript_id
          ? previous.focusId
          : null,
    };
    loadManualReviews(manualReviews);
  } else paintManualReviews(manualReviews);
}
// Updating a draft changes no saved review state and must not redraw its editor.
document.getElementById("transcript").addEventListener("input", (event) => {
  const row = event.target
    .closest("[data-segment]")
    ?.querySelector("[data-manual-review]");
  if (row && manualReviews && currentManualReviews(manualReviews))
    manualReviewControls(manualReviews, row, row.dataset.manualReview);
});
renderManualReviews();
