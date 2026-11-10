"use strict";
// Library choice shared by the start-up chooser page and the in-app Library dialog (ADR 0051).
// Opening a library restarts the local server; the page reloads once the new library answers.

const CBSLibraries = (() => {
  function instanceHeaders() {
    const instance = document.body?.dataset.cbsInstance;
    return instance ? { "X-CBS-Instance": instance } : {};
  }
  function resource(path) {
    const instance = document.body?.dataset.cbsInstance;
    return instance
      ? `${path}${path.includes("?") ? "&" : "?"}cbs_instance=${encodeURIComponent(instance)}`
      : path;
  }
  function stale(data) {
    if (data?.code !== "library_instance_changed") return;
    const notice = document.querySelector("#library-instance-notice");
    if (notice) notice.hidden = false;
  }
  async function request(path, options = {}) {
    const response = await fetch(`/api${path}`, {
      method: options.method || "GET",
      cache: "no-store",
      signal: options.signal,
      headers: {
        "X-CBS-Request": "1",
        ...instanceHeaders(),
        ...(options.body ? { "Content-Type": "application/json" } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    stale(data);
    if (!response.ok) {
      const error = new Error(
        typeof data.detail === "string"
          ? data.detail
          : "The request could not be completed.",
      );
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function escape(value) {
    return String(value ?? "").replace(
      /[&<>"']/g,
      (character) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[character],
    );
  }
  function facts(library) {
    if (!library.exists) return "No library here yet";
    if (library.unreadable) return "The database cannot be read";
    const changed = library.modified
      ? ` · changed ${new Date(library.modified).toLocaleString("en-GB")}`
      : "";
    return `${library.projects} project(s) · ${library.recordings} recording(s)${changed}`;
  }
  function size(bytes) {
    const units = ["bytes", "KB", "MB", "GB", "TB"];
    let value = Number(bytes) || 0;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${unit ? value.toFixed(1) : value} ${units[unit]}`;
  }
  function row(library, canDelete = false) {
    const action = library.current
      ? '<span class="badge">Open now</span>'
      : `<button type="button" class="button" data-library-open="${escape(library.path)}" ${library.unreadable ? "disabled" : ""}>Open</button>`;
    const visibility =
      library.current || !library.visibility_token
        ? ""
        : `<button type="button" class="button" data-library-visibility="${library.hidden ? "show" : "hide"}" data-library-path="${escape(library.path)}" data-library-token="${escape(library.visibility_token)}" title="${library.hidden ? "Show this library in the main list again." : "Hide this library from the main list. Nothing is deleted."}">${library.hidden ? "Show again" : "Remove from list"}</button>`;
    const removal =
      canDelete && !library.current
        ? `<button type="button" class="button danger" data-library-delete="${escape(library.path)}" title="See exactly what would be deleted. Nothing is deleted until you confirm.">Delete permanently</button>`
        : "";
    return `<li class="library-item${library.current ? " current" : ""}"><div><strong>${escape(library.kind)}</strong><span class="library-path">${escape(library.path)}</span><span class="library-facts">${escape(facts(library))}${library.synced ? " · in a cloud-synced folder" : ""}</span></div><div class="library-actions">${action}${visibility}${removal}</div></li>`;
  }
  // An interrupted permanent deletion stays listed with Retry until it is settled.
  function pendingRow(item) {
    return `<li class="library-item" data-library-deletion-pending><div><strong>Interrupted library deletion</strong><span class="library-path">${escape(item.path || item.name)}</span><span class="library-facts">${escape(item.message)}</span></div><div class="library-actions"><button type="button" class="button" data-library-deletion-retry="${escape(item.id)}">Retry</button></div></li>`;
  }
  // Plain-language preview: what is deleted, what is kept and why, and what blocks it.
  function deletionPreview(facts) {
    const list = (items, render) =>
      items.length ? `<ul>${items.map(render).join("")}</ul>` : "";
    const removed = list(
      facts.delete,
      (item) =>
        `<li>${item.kind === "library" ? "The library folder" : `The folder of project “${escape(item.project)}”`}: <span class="library-path">${escape(item.path)}</span><span class="library-facts">${item.files} file(s), ${size(item.bytes)}</span></li>`,
    );
    const kept = list(
      facts.keep,
      (item) =>
        `<li>The folder of project “${escape(item.project)}”: <span class="library-path">${escape(item.path)}</span><span class="library-facts">${escape(item.reason)}</span></li>`,
    );
    const others = facts.other_items.length
      ? `<p class="field-note">The library folder also contains files that CBS Transcriber did not create. They will be deleted too: ${facts.other_items.map(escape).join(", ")}. Move them elsewhere first if you want to keep them.</p>`
      : "";
    const blocked = facts.blockers.length > 0;
    const blockers = blocked
      ? `<div class="settings-error" role="alert" data-library-delete-blockers><p><strong>This library cannot be deleted yet.</strong> Nothing has been deleted. To continue:</p><ul>${facts.blockers.map((reason) => `<li>${escape(reason)}</li>`).join("")}</ul></div>`
      : `<label>To confirm, type the folder name <strong>${escape(facts.name)}</strong><input type="text" data-library-delete-name autocomplete="off" spellcheck="false" /></label>`;
    return `<h3 class="settings-subhead">Delete the library “${escape(facts.name)}” permanently?</h3><p class="settings-note">This deletes ${facts.projects} project(s) and ${facts.recordings} recording(s), including the original audio and video files, transcripts and their history (${facts.files} file(s), ${size(facts.bytes)} in total). <strong>This cannot be undone.</strong></p><p class="settings-note"><strong>Will be deleted</strong></p>${removed}${others}${kept ? `<p class="settings-note"><strong>Will be kept</strong></p>${kept}` : ""}${facts.notes.map((note) => `<p class="field-note">${escape(note)}</p>`).join("")}${blockers}<div class="dialog-actions"><button type="button" class="button" data-library-delete-cancel>${blocked ? "Close" : "Cancel"}</button>${facts.can_delete ? '<button type="button" class="button danger" data-library-delete-confirm>Delete permanently</button>' : ""}</div>`;
  }
  async function open(
    path,
    create,
    status,
    current = () => true,
    beforeReload = () => true,
  ) {
    status("Opening the library… The app restarts and this page reloads.");
    let library;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      ({ library } = await request("/libraries/open", {
        method: "POST",
        body: { path, create },
        signal: controller.signal,
      }));
    } catch (error) {
      if (!error.status || error.status === 408 || error.status >= 500)
        throw new Error(
          "The switch outcome is unknown. The selected library may still open. Copy unsaved edits and reload when it is ready before continuing.",
        );
      throw error;
    } finally {
      clearTimeout(timeout);
    }
    if (!current()) return;
    if (typeof library !== "string" || !library)
      throw new Error(
        "The switch outcome is unknown. The server did not confirm the selected library. Copy unsaved edits and reload when it is ready before continuing.",
      );
    const started = Date.now();
    // Only the selected library can confirm that the restart actually completed.
    while (Date.now() - started < 60000) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      if (!current()) return;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);
      try {
        const response = await fetch(
          `/api/library/switch-ready?expected=${encodeURIComponent(library)}`,
          {
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!response.ok) continue;
        const storage = await response.json();
        if (!current()) return;
        if (storage.ready === true) {
          if (!beforeReload()) {
            status(
              "The selected library is ready. Reload when your changes are safe.",
            );
            return;
          }
          location.href = "/";
          return;
        }
      } catch {
        // The server is restarting.
      } finally {
        clearTimeout(timeout);
      }
    }
    if (!current()) return;
    throw new Error(
      "The selected library has not responded yet. The switch may still be in progress; copy unsaved edits and reload the app when it is ready before continuing.",
    );
  }
  // Binds one chooser: list, "Open another folder", "New library" and a status line.
  function bind(root, { beforeOpen = () => true } = {}) {
    const list = root.querySelector("[data-library-list]");
    const status = (message, error = false) => {
      const node = root.querySelector("[data-library-status]");
      node.textContent = message;
      node.classList.toggle("settings-error", error);
    };
    let data = null,
      loading = false,
      opening = false,
      loadVersion = 0,
      choiceVersion = 0,
      deletion = null;
    const panel = root.querySelector("[data-library-delete-panel]");
    function closeDeletion() {
      deletion = null;
      if (!panel) return;
      panel.hidden = true;
      panel.innerHTML = "";
    }
    const dialog = root.closest("dialog");
    dialog?.addEventListener("close", () => {
      choiceVersion++;
      closeDeletion();
    });
    // Backup controls and the dialog's Close button have independent state owners.
    const buttons = () =>
      root.querySelectorAll(
        "[data-library-open], [data-library-browse], [data-library-new], [data-library-visibility], [data-library-delete], [data-library-deletion-retry], [data-library-delete-confirm], [data-library-delete-cancel]",
      );
    // Hiding, deleting and recovery never restart the app, so they need no launcher.
    const independent = (button) =>
      [
        "libraryVisibility",
        "libraryDelete",
        "libraryDeletionRetry",
        "libraryDeleteConfirm",
        "libraryDeleteCancel",
      ].some((key) => button.dataset[key] !== undefined);
    function updateControls() {
      const typed = panel?.querySelector("[data-library-delete-name]")?.value;
      buttons().forEach((button) => {
        button.disabled =
          loading ||
          opening ||
          (!independent(button) && !data?.can_switch) ||
          (button.dataset.libraryDeleteConfirm !== undefined &&
            (!deletion || typed !== deletion.name)) ||
          Boolean(
            data?.libraries.some(
              (library) =>
                library.path === button.dataset.libraryOpen &&
                library.unreadable,
            ),
          );
      });
    }
    async function guarded(action, visibility = false) {
      if (
        loading ||
        opening ||
        (!visibility && (!data?.can_switch || !beforeOpen()))
      )
        return;
      const version = choiceVersion;
      const current = () =>
        version === choiceVersion &&
        root.isConnected &&
        (!dialog || dialog.open);
      opening = true;
      updateControls();
      try {
        await action(current);
      } catch (error) {
        if (current()) status(error.message, true);
      } finally {
        opening = false;
        updateControls();
      }
    }
    async function load() {
      const version = ++loadVersion;
      loading = true;
      updateControls();
      status("Looking for libraries on this computer…");
      try {
        const observed = await request("/libraries");
        if (version !== loadVersion) return;
        data = observed;
        const visible = data.libraries.filter((library) => !library.hidden);
        const hidden = data.libraries.filter((library) => library.hidden);
        const listed = (library) => row(library, data.can_delete === true);
        list.innerHTML =
          (data.pending_deletions || []).map(pendingRow).join("") +
          visible.map(listed).join("") +
          (hidden.length
            ? `<li><details><summary>Hidden libraries (${hidden.length})</summary><p class="field-note">Files and any unfinished work are kept. Show a library again or open it to check its work.</p><ul>${hidden.map(listed).join("")}</ul></details></li>`
            : "");
        root.querySelector("[data-library-new]").title =
          `A new, empty library. Suggested folder: ${data.default_new}`;
        status(
          data.can_switch
            ? ""
            : "Opening another library needs the launcher. Restart with: uv run cbs-transcriber",
        );
      } catch (error) {
        if (version !== loadVersion) return;
        data = null;
        status(error.message, true);
        throw error;
      } finally {
        if (version === loadVersion) {
          loading = false;
          updateControls();
        }
      }
    }
    root.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button || button.disabled) return;
      if (button.dataset.libraryOpen !== undefined)
        guarded((current) =>
          open(button.dataset.libraryOpen, false, status, current, beforeOpen),
        );
      else if (button.dataset.libraryDelete !== undefined)
        guarded(async (current) => {
          closeDeletion();
          status("Checking what would be deleted… Nothing is deleted yet.");
          // Preview reads a private copy only; nothing in the library is changed.
          const facts = await request("/libraries/delete-preview", {
            method: "POST",
            body: { path: button.dataset.libraryDelete },
          });
          if (!current() || !panel) return;
          deletion = facts;
          panel.innerHTML = deletionPreview(facts);
          panel.hidden = false;
          panel
            .querySelector("[data-library-delete-name]")
            ?.addEventListener("input", updateControls);
          status("");
          (
            panel.querySelector("[data-library-delete-name]") ||
            panel.querySelector("[data-library-delete-cancel]")
          ).focus();
        }, true);
      else if (button.dataset.libraryDeleteCancel !== undefined) {
        closeDeletion();
        status("Nothing was deleted.");
      } else if (button.dataset.libraryDeleteConfirm !== undefined)
        guarded(async (current) => {
          const facts = deletion;
          const typed = panel.querySelector("[data-library-delete-name]").value;
          if (!facts || typed !== facts.name) return;
          status(
            "Deleting… This can take a few minutes for a large library. Keep this window open.",
          );
          let result;
          try {
            result = await request("/libraries/delete", {
              method: "POST",
              body: {
                path: facts.path,
                fingerprint: facts.fingerprint,
                confirm_name: typed,
              },
            });
          } catch (error) {
            if (!current()) return;
            closeDeletion();
            await load().catch(() => {});
            if (!error.status || error.status >= 500)
              throw new Error(
                "It is not clear whether the deletion finished. Check the library list: an interrupted deletion appears at the top with Retry.",
              );
            throw error;
          }
          if (!current()) return;
          closeDeletion();
          await load();
          status(result.message, result.state !== "complete");
        }, true);
      else if (button.dataset.libraryDeletionRetry !== undefined)
        guarded(async (current) => {
          const result = await request(
            `/libraries/deletions/${encodeURIComponent(button.dataset.libraryDeletionRetry)}/retry`,
            { method: "POST" },
          );
          if (!current()) return;
          await load();
          status(result.message, result.state === "attention");
        }, true);
      else if (button.dataset.libraryVisibility !== undefined)
        guarded(async (current) => {
          const hidden = button.dataset.libraryVisibility === "hide";
          if (
            hidden &&
            !confirm(
              "Remove this library from the main list? Its files and work are kept, and it remains available under Hidden libraries.",
            )
          )
            return;
          await request("/libraries/visibility", {
            method: "POST",
            body: {
              path: button.dataset.libraryPath,
              token: button.dataset.libraryToken,
              hidden,
            },
          });
          if (current()) await load();
        }, true);
      else if (button.dataset.libraryBrowse !== undefined)
        guarded(async (current) => {
          const { path } = await request("/storage/browse", { method: "POST" });
          if (!current()) return;
          if (!path) throw new Error("No folder was chosen.");
          await open(path, false, status, current, beforeOpen);
        });
      else if (button.dataset.libraryNew !== undefined)
        guarded(async (current) => {
          const suggested = data?.libraries.some(
            (library) => library.path === data.default_new,
          );
          let path = data?.default_new;
          if (
            suggested ||
            !confirm(`Create a new, empty library in ${path}?`)
          ) {
            ({ path } = await request("/storage/browse", { method: "POST" }));
            if (!current()) return;
            if (!path) throw new Error("No folder was chosen.");
          }
          await open(path, true, status, current, beforeOpen);
        });
    });
    updateControls();
    return { load, status };
  }
  return { bind, request, instanceHeaders, resource, stale };
})();

if (document.body?.dataset.page === "library-chooser") {
  CBSLibraries.bind(document.querySelector("#library-chooser"))
    .load()
    .catch((error) => {
      document.querySelector("[data-library-status]").textContent =
        error.message;
    });
}
