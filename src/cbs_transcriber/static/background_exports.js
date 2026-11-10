"use strict";

// ZIP preparation belongs to the serving library, independently of the dialog
// and current recording. The normal jobs poll is its only polling loop.
const CBSBackgroundExports = (() => {
  let known = false,
    checking = null,
    submitting = false;
  const pending = new Set(),
    messages = new Map();
  function taskId(job) {
    const id = job?.export_task?.id;
    return job?.kind === "export" &&
      typeof id === "string" &&
      /^[0-9a-f]{32}$/u.test(id) &&
      job.id === `export:${id}`
      ? id
      : null;
  }
  function jobs() {
    return (state.jobs || []).filter((job) => taskId(job));
  }
  function active() {
    return jobs().some((job) => ["queued", "running"].includes(job.state));
  }
  function blocked() {
    return !known || !!checking || submitting || active();
  }
  function dialogStatus(context, zipped) {
    if (context.pending || context.backgroundExportError) return;
    let message = "";
    if (zipped) {
      message =
        submitting || active()
          ? "An export is being prepared. Follow progress in Activity. Closing this window does not cancel it."
          : !known || checking
            ? "Checking prepared exports…"
            : jobs().some((job) => job.export_task.ready)
              ? "A prepared ZIP is available in Activity. Prepare ZIP creates another export."
              : "";
    }
    if (message || context.backgroundExportStatus) {
      $("#export-status").textContent = message;
      context.backgroundExportStatus = !!message;
    }
  }
  function observe(latest) {
    known = true;
    const ids = new Set(latest.map(taskId).filter(Boolean));
    for (const id of messages.keys()) if (!ids.has(id)) messages.delete(id);
    updateExport();
  }
  async function refresh() {
    if (checking) return checking.catch(() => {});
    checking = pollJobs();
    updateExport();
    try {
      await checking;
    } catch {
      known = false;
    } finally {
      checking = null;
      updateExport();
    }
  }
  async function start(path, body, current) {
    if (blocked())
      throw new Error("Check Activity before preparing another export.");
    submitting = true;
    updateExport();
    // Open on this explicit click, not on a later response after navigation.
    $("#activity").hidden = false;
    try {
      // A previous dialog's failed response cannot mark a newer view stale.
      // Successful tasks still belong to Activity after that dialog closes.
      const response = await fetch(`/api${path}`, {
        method: "POST",
        cache: "no-store",
        headers: {
          "X-CBS-Request": "1",
          ...CBSLibraries.instanceHeaders(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      const job = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (current()) CBSLibraries.stale(job);
        throw new Error(
          typeof job.detail === "string" ? job.detail : "Export failed.",
        );
      }
      if (!taskId(job))
        throw new Error(
          "The export outcome is unknown. Check Activity before trying again.",
        );
      // A fresh sequence prevents an older jobs response from hiding this task.
      await pollJobs();
      return job;
    } catch (error) {
      // A lost reply must not trigger an automatic second preparation.
      await refresh();
      throw error;
    } finally {
      submitting = false;
      updateExport();
    }
  }
  function downloadURL(job) {
    const id = taskId(job),
      path = id ? `/api/export-tasks/${id}/download` : null;
    return path &&
      job.export_task.ready &&
      job.export_task.download_url === path
      ? CBSLibraries.resource(path)
      : null;
  }
  function card(job) {
    const id = taskId(job);
    if (!id) return "";
    const task = job.export_task,
      busy = pending.has(id),
      url = downloadURL(job),
      progress = Number.isFinite(job.progress)
        ? Math.max(0, Math.min(1, job.progress))
        : null,
      message = messages.get(id);
    return `<article class="job" data-export-task="${id}"><div class="job-head"><span class="job-kind">Export preparation</span></div><h3>${h(job.recording_name)}</h3><p>${h(job.message)}</p>${task.ready ? '<p class="field-note">This ZIP stays available while this app is running. Discard it when finished; your library files are kept.</p>' : ""}<progress ${progress === null ? "" : `value="${progress}"`} max="1" aria-label="Export preparation progress"></progress>${message ? `<p class="field-note" role="status">${h(message)}</p>` : ""}<div class="job-footer"><span class="badge ${["queued", "running"].includes(job.state) ? "working" : job.state === "failed" ? "failed" : ""}">${h(task.ready ? "Ready" : job.state)}</span><div class="export-task-actions">${url ? `<a class="button" data-export-download data-export-action="download" ${busy ? 'aria-disabled="true"' : `href="${h(url)}"`} download>Download ZIP</a>` : ""}${task.can_cancel ? `<button type="button" class="button" data-export-cancel data-export-action="cancel" ${busy ? "disabled" : ""}>Cancel preparation</button>` : ""}${task.can_discard ? `<button type="button" class="button" data-export-discard data-export-action="discard" ${busy ? "disabled" : ""}>Discard ZIP</button>` : ""}</div></div></article>`;
  }
  function redraw(id) {
    const job = jobs().find((item) => taskId(item) === id),
      node = document.querySelector(`[data-export-task="${id}"]`);
    if (job && node) node.outerHTML = card(job);
  }
  $("#jobs").addEventListener("click", async (event) => {
    const control = event.target.closest("[data-export-action]");
    if (!control) return;
    event.preventDefault();
    const id = control.closest("[data-export-task]")?.dataset.exportTask,
      job = jobs().find((item) => taskId(item) === id),
      action = control.dataset.exportAction;
    if (!job || pending.has(id)) return;
    if (
      (action === "download" && !downloadURL(job)) ||
      (action === "cancel" && !job.export_task.can_cancel) ||
      (action === "discard" && !job.export_task.can_discard) ||
      !["download", "cancel", "discard"].includes(action)
    )
      return;
    pending.add(id);
    messages.delete(id);
    redraw(id);
    try {
      if (action === "download") {
        const current = await api("/export-tasks", { background: true }),
          ready = current.tasks?.find((item) => taskId(item) === id),
          url = downloadURL(ready);
        if (!url) throw new Error("This prepared ZIP is no longer available.");
        // Health uses the instance header before the browser streams the ZIP.
        // A stale page retains its draft instead of downloading a 409 JSON body.
        await nativeExportDownload(url);
        messages.set(
          id,
          "Download started. Keep this ZIP until you have saved it.",
        );
      } else {
        await api(`/export-tasks/${id}/${action}`, {
          method: action === "discard" ? "DELETE" : "POST",
        });
        await pollJobs();
      }
    } catch (error) {
      messages.set(id, error.message);
    } finally {
      pending.delete(id);
      redraw(id);
    }
  });
  return { observe, blocked, dialogStatus, refresh, start, card };
})();
