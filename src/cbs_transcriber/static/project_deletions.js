"use strict";

// Project deletion belongs to the server (ADR 0190). The dialog previews, asks for the
// existing shared-work and UCloud Trash confirmations and hands off at once; Activity shows
// the stages and offers Keep project until the project is removed from the library.
const CBSProjectDeletions = (() => {
  const pending = new Set(),
    messages = new Map(),
    STAGES = [
      ["stopping", "Stopping jobs"],
      ["removing_library", "Removing from the library"],
      ["removing_files", "Removing files"],
    ],
    BADGES = {
      running: "deleting",
      deleted: "deleted",
      kept: "kept",
      interrupted: "not finished",
      failed: "not deleted",
      unresolved: "needs attention",
    };
  let signature = "",
    refreshing = null,
    again = false;
  const left = new Set();
  function taskId(job) {
    const id = job?.project_deletion?.id;
    return job?.kind === "project-delete" &&
      typeof id === "string" &&
      /^[0-9a-f]{32}$/u.test(id) &&
      job.id === `project-deletion:${id}`
      ? id
      : null;
  }
  function jobs() {
    return (state.jobs || []).filter((job) => taskId(job));
  }
  function confirmation(summary, shared) {
    const lines = [];
    if (shared)
      lines.push(
        "Stopping this shared allocation cancels other recordings.",
        summary.affected_recordings
          .map((recording) => recording.name)
          .join(", "),
      );
    const folders = (summary.remote_folders || []).map(
      (folder) =>
        `${folder.server} · ${folder.folder}${folder.action === "retain" ? " (kept for other recordings)" : " → UCloud Trash"}`,
    );
    if (folders.length)
      lines.push(
        (shared ? "\n" : "") +
          "Delete also moves these UCloud working folders to UCloud Trash. Empty Trash in UCloud to free the space.",
        folders.join("\n"),
      );
    return `${lines.join("\n")}\n\nContinue deleting?`;
  }
  // Preview, confirm and hand off. Returns the accepted task, or null when declined or
  // when the dialog's view changed before the hand-off.
  async function start(project, current) {
    const summary = await api(`/projects/${project.id}/deletion-preview`, {
      method: "POST",
    });
    if (!current()) return null;
    if ((summary.remote_blockers || []).length)
      throw new Error(
        "A single UCloud job in this project still needs recovery, remote cleanup or reconciliation in Activity before deleting. Nothing was stopped.",
      );
    const shared = (summary.affected_recordings || []).length > 0,
      trash = (summary.remote_folders || []).some(
        (folder) => folder.action === "trash",
      );
    if ((shared || trash) && !confirm(confirmation(summary, shared)))
      return null;
    if (!current()) return null;
    const body = { operation_id: summary.operation_id };
    if (shared) body.confirm_shared = true;
    if (trash && /^[a-f0-9]{64}$/u.test(summary.fingerprint))
      body.fingerprint = summary.fingerprint;
    try {
      return await api(`/projects/${project.id}/deletion-tasks`, {
        method: "POST",
        body,
      });
    } catch (error) {
      // A lost reply may still have been accepted: Activity is the source of truth.
      await pollJobs().catch(() => {});
      throw error;
    }
  }
  function stages(task) {
    if (task.state !== "running") return "";
    const reached = STAGES.findIndex(([key]) => key === task.phase);
    return `<ol class="deletion-stages" aria-label="Deletion stages">${STAGES.map(
      ([key, label], index) => {
        const status =
          reached < 0 || index > reached
            ? "pending"
            : index < reached
              ? "done"
              : "current";
        return `<li data-deletion-stage="${key}" data-stage-status="${status}" ${status === "current" ? 'aria-current="step"' : ""}>${h(label)}</li>`;
      },
    ).join("")}</ol>`;
  }
  function card(job) {
    const id = taskId(job);
    if (!id) return "";
    const task = job.project_deletion,
      busy = pending.has(id),
      note = messages.get(id),
      running = task.state === "running",
      name = job.recording_name;
    const help = running
      ? task.can_keep
        ? "Only this project is unavailable meanwhile. Keep project stops the deletion until the project is removed from the library; jobs already stopped stay stopped."
        : task.committed
          ? "The project has been removed from the library and can no longer be kept."
          : ""
      : task.state === "deleted"
        ? "Deleted projects can only be recovered from your own backups."
        : "";
    return `<article class="job project-deletion" data-project-deletion="${id}"><div class="job-head"><span class="job-kind">${running ? "Deleting project" : "Project deletion"}</span></div><h3>${running ? `Deleting project ${h(name)}` : h(name)}</h3><p>${h(job.message)}</p>${stages(task)}${help ? `<p class="field-note">${h(help)}</p>` : ""}${note ? `<p class="settings-note" role="alert">${h(note)}</p>` : ""}<div class="job-footer"><span class="badge ${running ? "working" : ["failed", "interrupted", "unresolved"].includes(task.state) ? "failed" : ""}">${h(BADGES[task.state] || task.state)}</span><div class="export-task-actions">${task.can_keep ? `<button type="button" class="button" data-project-deletion-action="keep" ${busy ? "disabled" : ""}>Keep project</button>` : ""}${task.can_dismiss ? `<button type="button" class="button" data-project-deletion-action="dismiss" ${busy ? "disabled" : ""}>Dismiss</button>` : ""}</div></div></article>`;
  }
  function attention(job) {
    return ["failed", "interrupted", "unresolved"].includes(
      job.project_deletion?.state,
    );
  }
  function redraw(id) {
    const job = jobs().find((item) => taskId(item) === id),
      node = document.querySelector(`[data-project-deletion="${id}"]`);
    if (job && node) node.outerHTML = card(job);
  }
  // A researcher inside a project that became inactive lands on the project list. Each
  // deletion asks once: declining an unsaved-draft prompt is not asked again every poll.
  function leaveInactive() {
    const open = state.recording
      ? state.projects.find((p) => p.id === state.recording.project_id)
      : state.project;
    const key = open?.deleting ? `${open.id}:${open.deleting.task_id}` : null;
    if (!key || left.has(key)) return;
    left.add(key);
    if (/^#\/(projects|recordings)\//u.test(location.hash)) location.hash = "";
    else route();
  }
  async function refresh() {
    if (refreshing) {
      again = true;
      return refreshing;
    }
    refreshing = (async () => {
      do {
        again = false;
        // The ordinary poll may supersede this read; wait for a current list.
        for (let attempt = 0; attempt < 5; attempt++)
          if (await refreshProjects()) break;
      } while (again);
    })();
    try {
      await refreshing;
    } catch {
      // The next jobs poll observes the same change again.
      signature = "";
    } finally {
      refreshing = null;
    }
    leaveInactive();
  }
  function observe(latest) {
    const next = latest
      .filter((job) => taskId(job))
      .map(
        (job) =>
          `${job.project_deletion.id}:${job.project_deletion.state}:${job.project_deletion.project_inactive}`,
      )
      .join("|");
    const known = state.projects
      .filter((project) => project.deleting)
      .map((project) => project.deleting.task_id)
      .sort()
      .join("|");
    const inactive = latest
      .filter((job) => taskId(job) && job.project_deletion.project_inactive)
      .map((job) => job.project_deletion.id)
      .sort()
      .join("|");
    if (next !== signature || known !== inactive) {
      signature = next;
      refresh();
    } else leaveInactive();
  }
  $("#jobs").addEventListener("click", async (event) => {
    const control = event.target.closest("[data-project-deletion-action]");
    if (!control) return;
    event.preventDefault();
    const id = control.closest("[data-project-deletion]")?.dataset
        .projectDeletion,
      job = jobs().find((item) => taskId(item) === id),
      action = control.dataset.projectDeletionAction;
    if (!job || pending.has(id) || !["keep", "dismiss"].includes(action))
      return;
    if (action === "keep" && !job.project_deletion.can_keep) return;
    if (action === "dismiss" && !job.project_deletion.can_dismiss) return;
    pending.add(id);
    messages.delete(id);
    redraw(id);
    try {
      await api(
        `/project-deletion-tasks/${id}/${action}`,
        action === "keep"
          ? {
              method: "POST",
              body: { project_id: job.project_deletion.project_id },
            }
          : { method: "POST" },
      );
    } catch (error) {
      messages.set(id, error.message);
    } finally {
      pending.delete(id);
    }
    await pollJobs().catch(() => {});
    redraw(id);
  });
  return { start, card, observe, attention, refresh };
})();
