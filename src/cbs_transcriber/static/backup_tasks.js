"use strict";

// Local library backups belong to the server (ADR 0192). Settings → Backup and Library →
// Backups both hand off to one task: Activity shows its stages, offers Stop until the backup
// is being saved, and reports a truthful outcome. The ordinary Activity poll is the only loop.
const CBSBackupTasks = (() => {
  const pending = new Set(),
    messages = new Map(),
    STAGES = [
      ["waiting", "Waiting for changes in progress"],
      ["snapshot", "Copying the library database"],
      ["inventory", "Listing files"],
      ["copy", "Copying files"],
      ["verify", "Checking the copy"],
      ["publishing", "Saving the backup"],
    ],
    TITLES = {
      ready: "Backup saved",
      stopped: "Backup stopped",
      failed: "Backup failed",
      interrupted: "Backup not finished",
      attention: "Backup needs checking",
    },
    BADGES = {
      running: "backing up",
      ready: "verified",
      stopped: "stopped",
      failed: "not made",
      interrupted: "not finished",
      attention: "needs attention",
    };
  const node = (id) => document.getElementById(id);
  let tabGeneration = 0,
    lastReady = "";
  function taskId(job) {
    const id = job?.backup_task?.id;
    return job?.kind === "backup" &&
      typeof id === "string" &&
      /^[0-9a-f]{32}$/u.test(id) &&
      job.id === `backup:${id}`
      ? id
      : null;
  }
  function jobs() {
    return (state.jobs || []).filter((job) => taskId(job));
  }
  function running() {
    return jobs().find((job) => job.backup_task.state === "running") || null;
  }
  function size(bytes) {
    if (!Number.isSafeInteger(bytes) || bytes < 0) return "";
    if (bytes < 1024 * 1024)
      return `${Math.max(0, Math.round(bytes / 1024))} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  }
  // Hand off one backup request. An uncertain reply is answered by the exact same request:
  // the server returns the task it already accepted, never a second backup.
  async function start(destination) {
    const body = {
      operation_id: crypto.randomUUID().replaceAll("-", ""),
      destination: destination || null,
    };
    let task;
    try {
      task = await api("/library/backup-tasks", { method: "POST", body });
    } catch (error) {
      if (error.status && error.status !== 408 && error.status < 500)
        throw error;
      try {
        task = await api("/library/backup-tasks", { method: "POST", body });
      } catch {
        await pollJobs().catch(() => {});
        throw new Error(
          "The backup request did not finish normally. Check Activity before trying again.",
        );
      }
    }
    // A fresh poll shows the accepted card even when this view has moved on.
    await pollJobs().catch(() => {});
    return task;
  }
  // Wait for one task's outcome while `current()` holds; null when the view moved on.
  async function settle(id, current) {
    while (current()) {
      const task = await api(`/library/backup-tasks/${id}`, {
        background: true,
      });
      if (task.state !== "running") return task;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    return null;
  }
  function stages(task) {
    if (task.state !== "running") return "";
    const reached = STAGES.findIndex(([key]) => key === task.phase);
    return `<ol class="deletion-stages" aria-label="Backup stages">${STAGES.map(
      ([key, label], index) => {
        const status =
          reached < 0 || index > reached
            ? "pending"
            : index < reached
              ? "done"
              : "current";
        return `<li data-backup-stage="${key}" data-stage-status="${status}" ${status === "current" ? 'aria-current="step"' : ""}>${h(label)}</li>`;
      },
    ).join("")}</ol>`;
  }
  function copyProgress(job) {
    const task = job.backup_task;
    if (task.state !== "running" || task.phase !== "copy" || !task.files_total)
      return "";
    const fraction = Number.isFinite(job.progress)
      ? Math.max(0, Math.min(1, job.progress))
      : null;
    return `<p class="field-note" data-backup-counts>${h(task.files_done)} of ${h(task.files_total)} files · ${h(size(task.bytes_done))} of ${h(size(task.bytes_total))}</p><progress ${fraction === null ? "" : `value="${fraction}"`} max="1" aria-label="Backup copy progress"></progress>`;
  }
  function help(task) {
    if (task.state === "running")
      return task.published
        ? "The backup is being saved and can no longer be stopped."
        : "Reading continues. Edits and new work wait while files are copied. Stop ends the backup before it is saved; no backup is made.";
    if (task.state === "ready" && task.receipt)
      return `Saved at ${task.receipt.folder}. Checked when it was made; this was not a test restore.`;
    return "";
  }
  function card(job) {
    const id = taskId(job);
    if (!id) return "";
    const task = job.backup_task,
      busy = pending.has(id),
      note = messages.get(id),
      active = task.state === "running",
      tip = help(task),
      actions = [
        task.can_stop &&
          `<button type="button" class="button" data-backup-action="stop" ${busy ? "disabled" : ""}>Stop backup</button>`,
        task.state === "ready" &&
          task.receipt &&
          `<button type="button" class="button" data-backup-action="receipt" ${busy ? "disabled" : ""}>Save receipt</button>`,
        task.can_remove_partial &&
          `<button type="button" class="button" data-backup-action="remove-partial" ${busy ? "disabled" : ""}>Remove incomplete copy</button>`,
        task.can_check &&
          `<button type="button" class="button" data-backup-action="check" ${busy ? "disabled" : ""}>Check again</button>`,
        task.can_dismiss &&
          `<button type="button" class="button" data-backup-action="dismiss" ${busy ? "disabled" : ""}>Dismiss</button>`,
      ]
        .filter(Boolean)
        .join("");
    return `<article class="job backup-task" data-backup-task="${id}"><div class="job-head"><span class="job-kind">Library backup</span></div><h3>${h(active ? "Making a backup" : TITLES[task.state] || "Library backup")}</h3><p>${h(job.message)}</p>${stages(task)}${copyProgress(job)}${tip ? `<p class="field-note">${h(tip)}</p>` : ""}${note ? `<p class="settings-note" role="alert">${h(note)}</p>` : ""}<div class="job-footer"><span class="badge ${active ? "working" : attention(job) ? "failed" : ""}">${h(BADGES[task.state] || task.state)}</span><div class="export-task-actions">${actions}</div></div></article>`;
  }
  function attention(job) {
    const task = job.backup_task;
    return (
      !!task &&
      (["failed", "interrupted", "attention"].includes(task.state) ||
        (task.state !== "running" && (task.kept_paths || []).length > 0))
    );
  }
  function redraw(id) {
    const job = jobs().find((item) => taskId(item) === id),
      element = document.querySelector(`[data-backup-task="${id}"]`);
    if (job && element) element.outerHTML = card(job);
  }
  function saveReceipt(receipt) {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(receipt, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `cbs-backup-${receipt.backup_id}-receipt.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("#jobs").addEventListener("click", async (event) => {
    const control = event.target.closest("[data-backup-action]");
    if (!control) return;
    event.preventDefault();
    const id = control.closest("[data-backup-task]")?.dataset.backupTask,
      job = jobs().find((item) => taskId(item) === id),
      action = control.dataset.backupAction,
      task = job?.backup_task;
    if (!task || pending.has(id)) return;
    if (action === "receipt") {
      if (task.state === "ready" && task.receipt) saveReceipt(task.receipt);
      return;
    }
    const allowed = {
      stop: task.can_stop,
      dismiss: task.can_dismiss,
      "remove-partial": task.can_remove_partial,
      check: task.can_check,
    };
    if (!allowed[action]) return;
    pending.add(id);
    messages.delete(id);
    redraw(id);
    try {
      await api(`/library/backup-tasks/${id}/${action}`, { method: "POST" });
    } catch (error) {
      messages.set(id, error.message);
    } finally {
      pending.delete(id);
    }
    await pollJobs().catch(() => {});
    redraw(id);
  });

  // ---- Settings → Backup tab (index.html #backup-panel) ----
  const panel = node("backup-panel"),
    tab = node("tab-backup");
  function current(generation) {
    return (
      generation === tabGeneration &&
      node("settings-dialog").open &&
      state.settingsTab === "backup"
    );
  }
  function status(message) {
    node("backup-settings-status").textContent = message;
  }
  function renderRunning() {
    const active = running();
    node("backup-settings-start").disabled = !!active;
    node("backup-settings-activity").hidden = !active;
    node("backup-settings-running").textContent = active
      ? "A backup is being made. Follow it in Activity; closing Settings does not stop it."
      : "";
  }
  async function loadLast(generation) {
    const [captured, storage] = await Promise.all([
      api("/library/portable-backups", { background: true }),
      api("/storage", { background: true }).catch(() => null),
    ]);
    if (!current(generation)) return;
    if (storage?.library)
      node("backup-settings-current").textContent =
        `This library: ${storage.library}`;
    if (storage?.default_backup_parent) {
      node("backup-settings-destination").placeholder =
        storage.default_backup_parent;
      node("backup-settings-default").textContent =
        `Default: ${storage.default_backup_parent}`;
    }
    const latest = captured.captures[0];
    node("backup-settings-last").textContent = latest
      ? `Last backup: ${new Date(latest.created_at).toLocaleString()} in ${latest.folder}. Library → Backups can check it against its receipt.`
      : "No backup has been made of this library yet.";
  }
  async function showTab() {
    const generation = ++tabGeneration;
    providerFormVersion++;
    node("provider-error").hidden = true;
    state.settingsTab = "backup";
    settingsUnsaved = false;
    document.querySelectorAll("[data-provider-tab]").forEach((button) => {
      const selected = button.dataset.providerTab === "backup";
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    for (const id of [
      "provider-fields",
      "hf-panel",
      "processing-panel",
      "spelling-panel",
    ]) {
      node(id).hidden = true;
      node(id).disabled = true;
    }
    panel.hidden = false;
    panel.disabled = false;
    status("");
    renderRunning();
    node("backup-settings-last").textContent = "Loading…";
    await loadLast(generation).catch((error) => {
      if (current(generation))
        node("backup-settings-last").textContent = error.message;
    });
  }
  // Other tabs only update the tab buttons; follow them so this panel never stays visible.
  new MutationObserver(() => {
    if (tab.getAttribute("aria-selected") !== "true") {
      tabGeneration += 1;
      panel.hidden = true;
      panel.disabled = true;
    }
  }).observe(tab, { attributes: true, attributeFilter: ["aria-selected"] });
  function observe(latest) {
    if (!current(tabGeneration)) return;
    renderRunning();
    const ready = latest
      .filter((job) => taskId(job) && job.backup_task.state === "ready")
      .map((job) => job.backup_task.id)
      .join("|");
    if (ready !== lastReady) {
      lastReady = ready;
      loadLast(tabGeneration).catch(() => {});
    }
  }
  node("backup-settings-destination").addEventListener("keydown", (event) => {
    // Enter never submits the provider form behind this panel.
    if (event.key === "Enter") event.preventDefault();
  });
  node("backup-settings-choose").addEventListener("click", async () => {
    const generation = tabGeneration;
    try {
      const { path } = await api("/storage/browse", { method: "POST" });
      if (path && current(generation))
        node("backup-settings-destination").value = path;
    } catch (error) {
      if (current(generation)) status(error.message);
    }
  });
  node("backup-settings-start").addEventListener("click", async () => {
    const generation = tabGeneration,
      button = node("backup-settings-start");
    button.disabled = true;
    status("Starting the backup…");
    try {
      const task = await start(
        node("backup-settings-destination").value.trim() || null,
      );
      if (!current(generation)) return;
      status(
        task.state === "running"
          ? "Backup started. Follow its progress in Activity; closing Settings does not stop it."
          : task.message,
      );
    } catch (error) {
      if (current(generation)) status(error.message);
    } finally {
      if (current(generation)) renderRunning();
    }
  });
  node("backup-settings-activity").addEventListener("click", () => {
    node("settings-dialog").close();
    $("#activity").hidden = false;
  });
  node("backup-settings-library").addEventListener("click", () => {
    node("settings-dialog").close();
    node("library-open").click();
  });
  return { start, settle, card, attention, observe, showTab };
})();
