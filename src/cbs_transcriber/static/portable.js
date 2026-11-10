"use strict";
(() => {
  const node = (id) => document.getElementById(id);
  // Release profile (ADR 0145), marked on <html> by the server.
  const featureOn = (name) =>
    !(document.documentElement.dataset.off || "").split(" ").includes(name);
  let receipt = null;
  let retention = null;
  let cloudApproved = false;
  // Edits and save starts fence late replies, including reads begun during a save.
  let cloudRevision = 0;
  let savedCloudRevision = 0;
  let retentionRevision = 0;
  let savedRetentionRevision = 0;
  let settingsLoad = 0;
  let settingsLoaded = false;
  let storageRevision = null;
  let targetFingerprint = null;
  let restoreTargetIdentity = null;
  let storageConflict = false;
  let retentionPreview = 0;
  let applyingRetention = false;
  let captureListLoad = 0;
  let captureSelection = 0;
  let backupDraftRevision = 0;
  let destinationRevision = 0;
  let defaultDestinationLoad = 0;
  let destinationTimer = null;
  let backupOperations = 0;
  let restoreDraftRevision = 0;
  let loadingReceipt = false;
  let uploadIdentityChanged = false;
  let restoreIdentityChanged = false;
  let restoreReceiptTarget = null;
  let restoreHistory = [];
  let restoreCheckVersion = 0;
  let checkedRestore = null;
  let checkingRestore = false;
  const restoreSource = () => ({
    folder: node("portable-restore-source").value.trim(),
    commit_sha256: node("portable-restore-reference").value.trim(),
    cloud: node("portable-restore-cloud").checked,
    expected_revision: storageRevision,
    expected_target_fingerprint: targetFingerprint,
    receipt_server: restoreReceiptTarget?.server || null,
    receipt_project_id: restoreReceiptTarget?.project_id || null,
  });
  const restoreCheckKey = () => {
    const source = restoreSource();
    return JSON.stringify([
      source.folder,
      source.commit_sha256,
      source.cloud,
      source.cloud
        ? [
            source.expected_revision,
            source.expected_target_fingerprint,
            source.receipt_server,
            source.receipt_project_id,
            cloudRevision,
            settingsLoad,
          ]
        : null,
    ]);
  };
  const updateRestoreControl = () => {
    node("portable-restore").disabled =
      checkingRestore ||
      !checkedRestore ||
      checkedRestore !== restoreCheckKey();
  };
  const invalidateRestoreCheck = (preserveMessage = false) => {
    restoreCheckVersion += 1;
    checkedRestore = null;
    const source = restoreSource();
    node("portable-selected-backup").textContent = source.folder
      ? `Selected backup: ${source.folder} (${source.cloud ? "UCloud" : "this computer"})`
      : "No backup chosen yet.";
    if (!preserveMessage)
      node("portable-check-status").textContent =
        "Check the backup before restoring. This checks stored bytes, not a test restore.";
    updateRestoreControl();
  };
  const showRestoreRecovery = () => {
    const recovery = node("portable-restore-recovery");
    recovery.hidden = false;
    recovery.open = true;
  };
  const earlierStatus = (message) => {
    status(`${node("portable-status").textContent}\n${message}`);
  };
  const mayUseReceipt = (selection, draft) =>
    selection === captureSelection &&
    draft === backupDraftRevision &&
    !backupOperations &&
    !node("portable-upload-operation").value.trim() &&
    !node("portable-restore-operation").value.trim();
  const invalidateRetention = () => {
    retentionPreview += 1;
    retention = null;
    node("portable-apply-retention").disabled = true;
  };
  const operation = (id) => {
    const field = node(id);
    if (!field.value.trim())
      field.value = crypto.randomUUID().replaceAll("-", "");
    return field.value.trim();
  };
  const status = (message) => {
    node("portable-status").textContent = message;
  };
  const savedCloudSettings = () => {
    if (
      settingsLoaded &&
      cloudRevision === savedCloudRevision &&
      targetFingerprint
    )
      return true;
    status("Save and review the UCloud storage choices before restoring.");
    return false;
  };
  const savedRetentionSettings = () => {
    if (
      settingsLoaded &&
      cloudRevision === savedCloudRevision &&
      retentionRevision === savedRetentionRevision
    )
      return true;
    status(
      "Save the current UCloud storage and retention choices before previewing or applying retention.",
    );
    return false;
  };
  const bind = (id, action) =>
    node(id).addEventListener("click", async () => {
      const button = node(id);
      const changesBackup = [
        "portable-upload",
        "portable-restore",
        "portable-simple-upload",
        "portable-simple-resume",
      ].includes(id);
      if (changesBackup) {
        backupOperations += 1;
        backupDraftRevision += 1;
      }
      const cloudAtStart = cloudRevision;
      const loadAtStart = settingsLoad;
      button.disabled = true;
      try {
        await action();
      } catch (error) {
        if (
          id === "portable-restore" &&
          node("portable-restore-operation").value.trim()
        )
          showRestoreRecovery();
        if (id !== "portable-save-cloud" && loadAtStart !== settingsLoad) {
          const actionName =
            {
              "portable-upload": "UCloud backup upload",
              "portable-restore": "restore",
            }[id] || "Library action";
          const uncertain =
            changesBackup &&
            (!error.status || error.status === 408 || error.status >= 500)
              ? " The outcome may be uncertain; keep the operation reference and check before retrying."
              : "";
          earlierStatus(`Earlier ${actionName}: ${error.message}.${uncertain}`);
          return;
        }
        if (
          error.status === 409 &&
          id !== "portable-save-cloud" &&
          cloudAtStart === cloudRevision
        ) {
          storageConflict = true;
          storageRevision = null;
          targetFingerprint = null;
          restoreTargetIdentity = null;
          cloudRevision += 1;
          cloudApproved = false;
          node("portable-cloud-approved").checked = false;
          node("portable-upload").disabled = true;
          invalidateRetention();
          if (restoreSource().cloud)
            invalidateRestoreCheck(id === "portable-check-restore");
        }
        status(error.message);
      } finally {
        if (changesBackup) {
          backupOperations -= 1;
          backupDraftRevision += 1;
        }
        button.disabled =
          (id === "portable-apply-retention" &&
            !retention?.eligible_operations.length) ||
          (id === "portable-upload" && !cloudApproved);
        if (id === "portable-restore") updateRestoreControl();
      }
    });
  const useReceipt = (saved) => {
    receipt = saved;
    node("portable-folder").value = receipt.folder;
    node("portable-reference").value = receipt.commit_sha256;
    node("portable-restore-source").value = receipt.folder;
    node("portable-restore-reference").value = receipt.commit_sha256;
    node("portable-restore-cloud").checked = false;
    restoreReceiptTarget = null;
    invalidateRestoreCheck();
    node("portable-upload-operation").value = "";
    node("portable-restore-operation").value = "";
    node("portable-receipt").disabled = false;
    if (node("library-dialog").open && node("portable-simple-cloud").open)
      loadSimple().catch((error) => simpleMessage(error.message));
  };
  const loadCaptures = async () => {
    const load = ++captureListLoad;
    const selection = captureSelection;
    const result = await api("/library/portable-backups");
    if (load !== captureListLoad || selection !== captureSelection) return;
    const select = node("portable-captures");
    const previous = select.value;
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = result.captures.length
      ? "Choose a saved receipt…"
      : "No saved receipts found";
    select.append(placeholder);
    for (const item of result.captures) {
      const option = document.createElement("option");
      option.value = item.backup_id;
      option.textContent = `${item.created_at} — ${item.folder}`;
      select.append(option);
    }
    if (previous) select.value = previous;
  };
  node("portable-captures").addEventListener("change", () => {
    captureSelection += 1;
    invalidateRestoreCheck();
  });
  node("library-open").addEventListener("click", () => {
    invalidateRestoreCheck();
    loadCaptures().catch((error) => status(error.message));
    const load = ++defaultDestinationLoad;
    api("/storage", { background: true })
      .then((storage) => {
        if (
          load === defaultDestinationLoad &&
          node("library-dialog").open &&
          storage.default_backup_parent
        ) {
          node("portable-destination").placeholder =
            storage.default_backup_parent;
          node("portable-destination-default").textContent =
            `Default: ${storage.default_backup_parent}`;
        }
      })
      .catch(() => {});
    // Other Library listeners finish setting up the dialog before checking the path.
    setTimeout(checkDestinationVolume, 0);
  });
  node("library-dialog").addEventListener("close", () => {
    destinationRevision += 1;
    defaultDestinationLoad += 1;
    restoreDraftRevision += 1;
    invalidateRestoreCheck();
    clearTimeout(destinationTimer);
  });
  function checkDestinationVolume() {
    const revision = destinationRevision;
    const opened = settingsLoad;
    clearTimeout(destinationTimer);
    if (!node("library-dialog").open) return;
    const destination = node("portable-destination").value.trim();
    const hint = node("portable-volume-hint");
    hint.textContent = "Checking the backup location…";
    destinationTimer = setTimeout(async () => {
      try {
        const result = await api("/library/portable-backup-volume-hint", {
          method: "POST",
          body: { destination: destination || null },
          background: true,
        });
        if (
          revision !== destinationRevision ||
          opened !== settingsLoad ||
          !node("library-dialog").open
        )
          return;
        hint.textContent =
          result.relationship === "same_volume"
            ? "This folder is on the same filesystem volume as the library."
            : "The app cannot determine whether this folder is on the same volume.";
      } catch {
        if (
          revision === destinationRevision &&
          opened === settingsLoad &&
          node("library-dialog").open
        )
          hint.textContent =
            "The app cannot determine whether this folder is on the same volume.";
      }
    }, 300);
  }
  node("portable-destination").addEventListener("input", () => {
    destinationRevision += 1;
    backupDraftRevision += 1;
    checkDestinationVolume();
  });
  bind("portable-choose-destination", async () => {
    const revision = ++destinationRevision;
    const opened = settingsLoad;
    const { path } = await api("/storage/browse", { method: "POST" });
    if (
      path &&
      revision === destinationRevision &&
      opened === settingsLoad &&
      node("library-dialog").open
    ) {
      node("portable-destination").value = path;
      node("portable-destination").dispatchEvent(
        new Event("input", { bubbles: true }),
      );
    }
  });
  bind("portable-refresh-captures", async () => {
    await loadCaptures();
    status("Saved receipts refreshed. Select one to check its backup.");
  });
  bind("portable-verify-capture", async () => {
    const id = node("portable-captures").value;
    if (!id) throw new Error("Choose a saved receipt first.");
    invalidateRestoreCheck();
    const selection = ++captureSelection;
    const draft = backupDraftRevision;
    const opened = settingsLoad;
    status(
      "Checking backup files against the saved receipt. This can take time…",
    );
    node("portable-check-status").textContent =
      "Checking backup files against the saved receipt…";
    let saved;
    try {
      saved = await api(`/library/portable-backups/${id}/verify`);
      if (saved?.verification !== "transport_integrity_only")
        throw new Error(
          "The backup check did not confirm stored-byte verification.",
        );
    } catch (error) {
      if (
        !mayUseReceipt(selection, draft) ||
        opened !== settingsLoad ||
        !node("library-dialog").open
      ) {
        earlierStatus(`Earlier backup check failed: ${error.message}`);
        return;
      }
      node("portable-check-status").textContent =
        `Backup could not be checked: ${error.message}. It may be incomplete or unavailable; existing files were kept.`;
      throw new Error(
        `Backup could not be checked: ${error.message}. It may be incomplete or unavailable; existing files were kept.`,
      );
    }
    if (
      !mayUseReceipt(selection, draft) ||
      opened !== settingsLoad ||
      !node("library-dialog").open ||
      node("portable-captures").value !== id
    ) {
      earlierStatus(
        "Earlier backup check succeeded, but your current choices and operation references were kept. To load this receipt, finish any operations and clear their references, then check it again.",
      );
      return;
    }
    const uploadFolder = node("portable-folder").value.trim();
    const uploadReference = node("portable-reference").value.trim();
    const canReuseForUpload =
      (!uploadFolder && !uploadReference) ||
      (uploadFolder === saved.folder &&
        uploadReference === saved.commit_sha256);
    if (canReuseForUpload) useReceipt(saved);
    else {
      receipt = saved;
      node("portable-receipt").disabled = false;
      node("portable-restore-source").value = saved.folder;
      node("portable-restore-reference").value = saved.commit_sha256;
      node("portable-restore-cloud").checked = false;
      restoreReceiptTarget = null;
      invalidateRestoreCheck();
    }
    checkedRestore = restoreCheckKey();
    updateRestoreControl();
    node("portable-check-status").textContent =
      "Backup bytes matched the saved receipt when read. Restore checks again; this is not a test restore.";
    status(
      `Backup files matched the saved receipt when read at ${saved.folder}. This checks stored bytes, not a test restore.${canReuseForUpload ? " Save the receipt outside your backup storage." : " Existing upload choices were kept."}`,
    );
  });
  bind("portable-create", async () => {
    const selection = ++captureSelection;
    const draft = backupDraftRevision;
    status(
      "Copying originals and evidence, then checking the complete package. Changes wait until the backup finishes…",
    );
    let saved;
    try {
      saved = await api("/library/portable-backups", {
        method: "POST",
        body: {
          destination: node("portable-destination").value.trim() || null,
        },
      });
    } catch (error) {
      if (!error.status || error.status === 408 || error.status >= 500) {
        const message = featureOn("restore_ui")
          ? "The backup request did not finish normally; its outcome is unknown. Once the app is available, open Restore a backup and check any saved receipt before retrying. Incomplete copies are kept."
          : "The backup request did not finish normally; its outcome is unknown. Incomplete copies are kept. Ask the person who supports your installation to check the backup folder before retrying.";
        if (!mayUseReceipt(selection, draft)) {
          earlierStatus(`Earlier backup request: ${message}`);
          return;
        }
        throw new Error(message);
      }
      if (!mayUseReceipt(selection, draft)) {
        earlierStatus(`Earlier backup request: ${error.message}`);
        return;
      }
      throw error;
    }
    if (!mayUseReceipt(selection, draft)) {
      earlierStatus(
        `Earlier backup saved at ${saved.folder}. Your current choices and operation references were kept.${featureOn("restore_ui") ? " Use Restore a backup to check its saved receipt." : ""}`,
      );
      loadCaptures().catch(() => {});
      return;
    }
    useReceipt(saved);
    status(
      `Complete backup saved: ${receipt.originals} original(s), ${receipt.files} file(s). Location: ${receipt.folder}. Contents were checked during creation; this was not a test restore. ${receipt.legacy_gaps} historical evidence gap(s) recorded. Save the receipt outside your backup storage.`,
    );
    loadCaptures().catch(() => {});
  });
  bind("portable-receipt", async () => {
    if (!receipt) return;
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
  });
  bind("portable-load-receipt", async () =>
    node("portable-receipt-file").click(),
  );
  bind("portable-show-restore-recovery", async () => {
    showRestoreRecovery();
    node("portable-find-restores").focus();
  });
  bind("portable-find-restores", async () => {
    const load = settingsLoad;
    const draft = restoreDraftRevision;
    const result = await api("/library/portable-operations");
    if (
      load !== settingsLoad ||
      draft !== restoreDraftRevision ||
      !node("library-dialog").open
    ) {
      earlierStatus(
        "Previous restores were found; your current choices were kept.",
      );
      return;
    }
    restoreHistory = result.restores;
    const select = node("portable-restore-history");
    select.replaceChildren();
    for (const [index, item] of restoreHistory.entries()) {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = `${item.target} · ${item.cloud ? "UCloud" : "this computer"}`;
      select.append(option);
    }
    status(
      restoreHistory.length
        ? "Select a previous restore to check and resume."
        : "No previous restores were found in this library.",
    );
  });
  bind("portable-load-restore-history", async () => {
    if (backupOperations || loadingReceipt)
      throw new Error(
        "Finish the current backup or restore before choosing an earlier restore.",
      );
    const selected = node("portable-restore-history").value;
    const item = selected === "" ? null : restoreHistory[Number(selected)];
    if (!item) throw new Error("Choose a previous restore first.");
    const source = restoreSource();
    if (
      !source.commit_sha256 ||
      source.folder !== item.folder ||
      source.commit_sha256 !== item.commit_sha256 ||
      source.cloud !== item.cloud
    )
      throw new Error(
        "First load the independently saved receipt for this same backup, or check its earlier creator receipt.",
      );
    if (
      item.cloud &&
      (!restoreTargetIdentity || item.target_identity !== restoreTargetIdentity)
    )
      throw new Error(
        "The current UCloud backup destination differs from this restore. Reconnect the original destination first.",
      );
    if (
      node("portable-restore-operation").value &&
      !confirm(
        "Replace the current restore choices with the selected previous restore?",
      )
    )
      return;
    node("portable-restore-target").value = item.target;
    node("portable-restore-operation").value = item.operation_id;
    restoreIdentityChanged = false;
    restoreDraftRevision += 1;
    invalidateRestoreCheck();
    status("Previous restore selected. Check the backup before resuming.");
  });
  node("portable-receipt-file").addEventListener("change", async (event) => {
    backupOperations += 1;
    backupDraftRevision += 1;
    loadingReceipt = true;
    const draft = ++restoreDraftRevision;
    try {
      const file = event.target.files[0];
      if (!file) return;
      if (
        backupOperations > 1 ||
        node("portable-restore-operation").value.trim()
      )
        throw new Error(
          "Finish active transfers and reconcile or clear the existing restore reference before loading another receipt.",
        );
      if (file.size > 65536)
        throw new Error("The trusted receipt is too large.");
      const loaded = JSON.parse(await file.text());
      if (
        typeof loaded.commit_sha256 !== "string" ||
        !/^[a-f0-9]{64}$/.test(loaded.commit_sha256)
      )
        throw new Error("The receipt has no valid commit reference.");
      const folder = loaded.remote_folder || loaded.folder;
      if (typeof folder !== "string" || !folder || folder.length > 4000)
        throw new Error("The receipt has no valid backup folder.");
      if (
        draft !== restoreDraftRevision ||
        backupOperations > 1 ||
        !node("library-dialog").open ||
        node("portable-restore-operation").value.trim()
      ) {
        earlierStatus(
          "Earlier receipt load was not applied because the restore choices changed.",
        );
        return;
      }
      node("portable-restore-source").value = folder;
      node("portable-restore-reference").value = loaded.commit_sha256;
      node("portable-restore-cloud").checked = !!loaded.remote_folder;
      restoreReceiptTarget = loaded.remote_folder
        ? {
            server: loaded.server || null,
            project_id: loaded.project_id || null,
          }
        : null;
      invalidateRestoreCheck();
      node("portable-restore-operation").value = "";
      status(
        "Receipt loaded. Check the backup, then choose a new local library folder.",
      );
    } catch (error) {
      status(error.message);
    } finally {
      loadingReceipt = false;
      restoreDraftRevision += 1;
      backupOperations -= 1;
      backupDraftRevision += 1;
      event.target.value = "";
    }
  });
  bind("portable-check-restore", async () => {
    if (node("portable-restore-cloud").checked && !savedCloudSettings()) return;
    const source = restoreSource();
    const version = ++restoreCheckVersion;
    const key = restoreCheckKey();
    checkingRestore = true;
    checkedRestore = null;
    updateRestoreControl();
    node("portable-check-status").textContent = source.cloud
      ? "Checking the UCloud backup bytes. This reads the complete backup and can take time…"
      : "Checking the backup files. This can take time…";
    try {
      const result = await api("/library/portable-backup-check", {
        method: "POST",
        body: source,
      });
      if (result?.verification !== "transport_integrity_only")
        throw new Error(
          "The backup check did not confirm stored-byte verification.",
        );
      if (version !== restoreCheckVersion || key !== restoreCheckKey()) {
        earlierStatus(
          "An earlier backup check finished; your current restore choices were kept.",
        );
        return;
      }
      checkedRestore = key;
      node("portable-check-status").textContent =
        "Backup bytes matched the trusted receipt when read. This is not a test restore; restore checks again.";
    } catch (error) {
      if (version !== restoreCheckVersion || key !== restoreCheckKey()) {
        earlierStatus(`An earlier backup check failed: ${error.message}`);
        return;
      }
      node("portable-check-status").textContent =
        `Backup could not be checked: ${error.message}. It may be incomplete or unavailable; existing files were kept.`;
      throw error;
    } finally {
      checkingRestore = false;
      updateRestoreControl();
    }
  });
  bind("portable-save-cloud", async () => {
    const provider = node("portable-provider").value;
    if (!provider)
      throw new Error(
        "Choose a UCloud server and project before saving backup storage.",
      );
    const selected = node("portable-provider").selectedOptions[0];
    if (!selected?.dataset.server || !selected?.dataset.project)
      throw new Error("Choose a connected UCloud server and project.");
    if (!storageRevision)
      throw new Error(
        "Reopen Library to load the current backup storage settings.",
      );
    const revision = ++cloudRevision;
    invalidateRestoreCheck();
    const choice = {
      provider_id: provider,
      parent: node("portable-cloud-parent").value.trim(),
      allowed_transfer_origins: node("portable-origins")
        .value.split(/[\s,]+/)
        .filter(Boolean),
      whole_library_approved: node("portable-cloud-approved").checked,
      expected_server: selected.dataset.server,
      expected_project_id: selected.dataset.project,
      expected_revision: storageRevision,
    };
    invalidateRetention();
    cloudApproved = false;
    node("portable-upload").disabled = true;
    let saved;
    try {
      saved = await api("/library/portable-storage", {
        method: "PUT",
        body: choice,
      });
    } catch (error) {
      if (revision === cloudRevision) {
        node("portable-cloud-approved").checked = false;
        if (![400, 404].includes(error.status)) {
          // A lost reply may have committed. Reload before any further save.
          storageConflict = true;
          storageRevision = null;
        }
        throw error;
      }
      return;
    }
    if (revision !== cloudRevision) return;
    storageRevision = saved.storage_revision;
    targetFingerprint = saved.fingerprint;
    restoreTargetIdentity = saved.restore_target_identity || null;
    savedCloudRevision = revision;
    invalidateRetention();
    cloudApproved =
      choice.whole_library_approved && saved.whole_library_approved === true;
    node("portable-upload").disabled = !cloudApproved;
    status(
      "Backup storage choices saved. Institutional approval remains a separate decision.",
    );
  });
  async function uploadBackup(current = () => true) {
    if (uploadIdentityChanged && node("portable-upload-operation").value.trim())
      throw new Error(
        "The upload choices changed. Reconcile the retained reference or clear it before a new upload.",
      );
    status(
      "Uploading the sealed package and verifying each object by downloading its checksum…",
    );
    const folder = node("portable-folder").value.trim();
    const reference = node("portable-reference").value.trim();
    const sourceReceipt =
      receipt?.folder === folder && receipt?.commit_sha256 === reference
        ? receipt
        : {};
    const restoreDraft = restoreDraftRevision;
    const result = await api("/library/portable-upload", {
      method: "POST",
      body: {
        folder,
        commit_sha256: reference,
        operation_id: operation("portable-upload-operation"),
        expected_revision: storageRevision,
        expected_target_fingerprint: targetFingerprint,
      },
    });
    const updateRestore =
      current() &&
      restoreDraft === restoreDraftRevision &&
      !loadingReceipt &&
      !node("portable-restore-operation").value.trim();
    if (updateRestore) {
      node("portable-restore-source").value = result.remote_folder;
      node("portable-restore-cloud").checked = true;
      node("portable-restore-reference").value = result.commit_sha256;
      restoreReceiptTarget = {
        server: result.target.server,
        project_id: result.target.project_id,
      };
      invalidateRestoreCheck();
      restoreDraftRevision += 1;
    }
    receipt = {
      ...sourceReceipt,
      backup_id: result.backup_id,
      commit_sha256: result.commit_sha256,
      remote_folder: result.remote_folder,
      server: result.target.server,
      project_id: result.target.project_id,
    };
    node("portable-receipt").disabled = false;
    const message =
      "UCloud backup committed after download-back verification. Retain the trusted receipt separately." +
      (updateRestore
        ? ""
        : " Current restore choices and reference were kept.");
    if (current()) status(message);
    else earlierStatus(`Earlier backup: ${message}`);
  }
  bind("portable-upload", uploadBackup);
  // Simple backup uses the existing creator receipt and frozen upload journal.
  // Location discovery is independent of the local stop/recovery controls.
  let simpleChoices = null;
  let simpleOperations = [];
  let simpleStorage = null;
  let simpleGeneration = 0;
  let simpleEdit = 0;
  const simpleMessage = (message) => {
    node("portable-simple-status").textContent = message;
  };
  const markSimpleStorageUncertain = () => {
    storageConflict = true;
    storageRevision = null;
    targetFingerprint = null;
    restoreTargetIdentity = null;
    cloudApproved = false;
    simpleChoices = null;
    simpleStorage = null;
    node("portable-simple-stop").hidden = true;
    node("portable-simple-stop-context").hidden = true;
    node("portable-simple-refresh").hidden = false;
    simpleMessage(
      "Check UCloud again before uploading or recovering this backup.",
    );
    cloudRevision += 1;
    settingsLoad += 1;
    node("portable-upload").disabled = true;
    invalidateRetention();
    if (restoreSource().cloud) invalidateRestoreCheck();
  };
  const showSimpleStop = (snapshot) => {
    simpleStorage = snapshot;
    const target = snapshot?.target;
    node("portable-simple-stop").hidden = !target?.whole_library_approved;
    node("portable-simple-stop-context").hidden =
      !target?.whole_library_approved;
    if (target?.whole_library_approved)
      node("portable-simple-stop-context").textContent =
        `Stop applies to ${target.parent} in project ${target.project_id} on ${target.server}.`;
  };
  const simpleOpen = (generation) =>
    generation === simpleGeneration &&
    node("library-dialog").open &&
    node("portable-simple-cloud").open;
  async function loadSimple() {
    const generation = ++simpleGeneration;
    const edit = simpleEdit;
    simpleChoices = null;
    simpleStorage = null;
    node("portable-simple-stop").hidden = true;
    node("portable-simple-stop-context").hidden = true;
    node("portable-simple-refresh").hidden = true;
    // Without cloud_backup only earlier uploads are resolved; no location is read.
    const locationsRequest = featureOn("cloud_backup")
      ? api("/library/portable-simple/locations").then(
          (value) => ({ status: "fulfilled", value }),
          (reason) => ({ status: "rejected", reason }),
        )
      : null;
    const [operations, storage] = await Promise.allSettled([
      api("/library/portable-operations"),
      api("/library/portable-storage"),
    ]);
    if (!simpleOpen(generation)) return;
    if (storage.status === "fulfilled") {
      showSimpleStop(storage.value);
    }
    if (operations.status === "fulfilled") {
      simpleOperations = operations.value.uploads.filter(
        (item) =>
          !["complete", "trashed", "abandoned"].includes(item.state) ||
          (item.state === "complete" && item.backup_id === receipt?.backup_id),
      );
      const pending = node("portable-upload-operation").value.trim();
      if (
        pending &&
        receipt &&
        !simpleOperations.some((item) => item.operation_id === pending)
      )
        simpleOperations.push({
          operation_id: pending,
          backup_id: receipt.backup_id,
          state: "validating",
          local_package: receipt.folder,
          commit_sha256: receipt.commit_sha256,
        });
      const recovery = node("portable-simple-recovery");
      recovery.replaceChildren();
      for (const [index, item] of simpleOperations.entries()) {
        const option = document.createElement("option");
        option.value = String(index);
        option.textContent = `Earlier backup ${index + 1} — ${item.state.replaceAll("_", " ")}`;
        recovery.append(option);
      }
      node("portable-cloud-recovery").hidden = !simpleOperations.length;
      node("portable-simple-reconcile").hidden = !simpleOperations.some(
        (item) => item.state === "folder_uncertain",
      );
      node("portable-simple-abandon").hidden = !simpleOperations.some(
        (item) => item.state === "validating",
      );
    } else {
      simpleMessage(
        "Earlier backup operations could not be checked. Please reopen Library before uploading.",
      );
      return;
    }
    if (!locationsRequest) {
      node("portable-cloud-recovery").open = simpleOperations.length > 0;
      simpleMessage(
        simpleOperations.length
          ? "This version does not upload backups to UCloud. Resolve the earlier upload below."
          : "No earlier UCloud upload needs attention.",
      );
      return;
    }
    const locations = await locationsRequest;
    if (!simpleOpen(generation)) return;
    if (locations.status === "rejected") {
      node("portable-simple-refresh").hidden = false;
      simpleMessage(locations.reason.message);
      return;
    }
    if (
      storage.status !== "fulfilled" ||
      locations.value.storage_revision !== storage.value.storage_revision ||
      (locations.value.target?.fingerprint || null) !==
        (storage.value.target?.fingerprint || null)
    ) {
      node("portable-simple-refresh").hidden = false;
      simpleMessage(
        "Backup settings changed while loading. Check UCloud again before uploading.",
      );
      return;
    }
    if (edit !== simpleEdit) {
      simpleMessage(
        "Your location choice was kept. Check UCloud again before uploading.",
      );
      return;
    }
    simpleChoices = locations.value;
    const select = node("portable-simple-location");
    select.replaceChildren();
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose a backup location…";
    select.append(blank);
    for (const item of simpleChoices.locations) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      select.append(option);
    }
    if (simpleChoices.selected) select.value = simpleChoices.selected;
    simpleMessage(
      simpleChoices.problem ||
        "Create a backup first, or check an earlier backup under Restore backup, then upload it here.",
    );
  }
  node("portable-simple-cloud").addEventListener("toggle", () => {
    if (node("portable-simple-cloud").open)
      loadSimple().catch((error) => simpleMessage(error.message));
    else simpleGeneration += 1;
  });
  node("library-dialog").addEventListener("close", () => {
    simpleGeneration += 1;
  });
  node("library-open").addEventListener("click", () => {
    simpleGeneration += 1;
    if (node("portable-simple-cloud").open)
      loadSimple().catch((error) => simpleMessage(error.message));
  });
  // Without cloud_backup (ADR 0145) the panel appears only while an earlier upload needs
  // attention, so its Stop, Review and Abandon controls stay reachable.
  node("library-open").addEventListener("click", () => {
    if (featureOn("cloud_backup")) return;
    const panel = node("portable-simple-cloud");
    api("/library/portable-operations", { background: true })
      .then((result) => {
        const unresolved = result.uploads.some(
          (item) => !["complete", "trashed", "abandoned"].includes(item.state),
        );
        panel.toggleAttribute("data-feature-recovery", unresolved);
        panel.hidden = !unresolved;
      })
      .catch(() => {});
  });
  node("portable-simple-location").addEventListener("change", () => {
    simpleEdit += 1;
  });
  bind("portable-simple-refresh", loadSimple);
  async function startSimpleUpload(previous = null) {
    const shown = simpleChoices;
    const location = node("portable-simple-location").value;
    if (!shown || !location)
      throw new Error("Choose an available UCloud backup location first.");
    if (
      !previous &&
      (!receipt ||
        receipt.folder !== node("portable-folder").value ||
        receipt.commit_sha256 !== node("portable-reference").value)
    )
      throw new Error(
        "Create or check a complete local backup before uploading.",
      );
    if (!previous && simpleOperations.some((item) => item.state !== "complete"))
      throw new Error(
        "Resolve the earlier UCloud backup before starting another upload.",
      );
    if (
      !confirm(
        "Upload the entire library to this UCloud location, including Protected projects, Trash and private research notes?",
      )
    )
      return;
    const generation = simpleGeneration;
    const edit = simpleEdit;
    const savedReceipt = receipt;
    const draft = backupDraftRevision;
    let saved;
    try {
      saved = await api("/library/portable-simple/prepare", {
        method: "POST",
        body: {
          connection: shown.connection,
          revision: shown.storage_revision,
          location,
          confirmed: true,
        },
      });
    } catch (error) {
      if (!error.status || error.status === 408 || error.status >= 500)
        markSimpleStorageUncertain();
      throw error;
    }
    if (
      !simpleOpen(generation) ||
      simpleEdit !== edit ||
      node("portable-simple-location").value !== location ||
      receipt !== savedReceipt ||
      draft !== backupDraftRevision ||
      backupOperations !== 1
    ) {
      markSimpleStorageUncertain();
      throw new Error(
        "Your choices changed. Review the backup location again before uploading.",
      );
    }
    const checkedBefore = restoreCheckKey();
    settingsLoad += 1;
    cloudRevision += 1;
    savedCloudRevision = cloudRevision;
    settingsLoaded = true;
    invalidateRetention();
    storageRevision = saved.storage_revision;
    targetFingerprint = saved.fingerprint;
    restoreTargetIdentity = saved.restore_target_identity || null;
    if (checkedBefore !== restoreCheckKey()) invalidateRestoreCheck();
    storageConflict = false;
    cloudApproved = saved.whole_library_approved;
    shown.storage_revision = saved.storage_revision;
    shown.target = saved;
    showSimpleStop({ target: saved, storage_revision: saved.storage_revision });
    await api("/library/portable-simple/folder", {
      method: "POST",
      body: {
        expected_revision: storageRevision,
        expected_target_fingerprint: targetFingerprint,
      },
    });
    if (
      !simpleOpen(generation) ||
      simpleEdit !== edit ||
      node("portable-simple-location").value !== location ||
      receipt !== savedReceipt ||
      draft !== backupDraftRevision ||
      backupOperations !== 1
    )
      throw new Error(
        "Your choices changed. Review the backup location again before uploading.",
      );
    try {
      const identifier = operation("portable-upload-operation");
      await api("/library/portable-simple/admit", {
        method: "POST",
        body: {
          backup_id: savedReceipt.backup_id,
          operation_id: identifier,
          expected_revision: storageRevision,
          expected_target_fingerprint: targetFingerprint,
        },
      });
      if (
        !simpleOpen(generation) ||
        simpleEdit !== edit ||
        node("portable-simple-location").value !== location ||
        receipt !== savedReceipt ||
        draft !== backupDraftRevision ||
        backupOperations !== 1
      )
        throw new Error(
          "Your choices changed. The earlier backup is retained for recovery.",
        );
      const upload = uploadBackup(
        () =>
          simpleOpen(generation) &&
          simpleEdit === edit &&
          node("portable-simple-location").value === location,
      );
      await upload;
      if (node("portable-upload-operation").value === identifier)
        node("portable-upload-operation").value = "";
      if (
        simpleOpen(generation) &&
        simpleEdit === edit &&
        node("portable-simple-location").value === location
      )
        simpleMessage(
          "Backup uploaded and verified. Save its receipt separately.",
        );
      else
        earlierStatus(
          "An earlier UCloud backup was uploaded and verified. Save its receipt separately.",
        );
    } finally {
      if (simpleOpen(generation)) await loadSimple();
    }
  }
  bind("portable-simple-upload", () => startSimpleUpload());
  bind("portable-simple-stop", async () => {
    if (
      !confirm(
        "Stop new upload requests? A request already in progress may finish.",
      )
    )
      return;
    const current = simpleStorage;
    if (!current?.target?.whole_library_approved) return;
    const generation = simpleGeneration;
    let stopped;
    try {
      stopped = await api("/library/portable-simple/stop", {
        method: "POST",
        body: {
          expected_revision: current.storage_revision,
          expected_target_fingerprint: current.target.fingerprint,
        },
      });
    } catch (error) {
      if (!error.status || error.status === 408 || error.status >= 500)
        markSimpleStorageUncertain();
      throw error;
    }
    if (!simpleOpen(generation) || simpleStorage !== current) {
      markSimpleStorageUncertain();
      earlierStatus(
        "An earlier UCloud backup was stopped; reopen Library to refresh its status.",
      );
      return;
    }
    simpleGeneration += 1;
    const checkedBefore = restoreCheckKey();
    cloudApproved = false;
    storageRevision = stopped.storage_revision;
    targetFingerprint = stopped.fingerprint;
    restoreTargetIdentity = stopped.restore_target_identity || null;
    storageConflict = false;
    settingsLoad += 1;
    cloudRevision += 1;
    savedCloudRevision = cloudRevision;
    if (checkedBefore !== restoreCheckKey()) invalidateRestoreCheck();
    invalidateRetention();
    showSimpleStop({
      target: stopped,
      storage_revision: stopped.storage_revision,
    });
    if (simpleChoices) {
      simpleChoices.storage_revision = stopped.storage_revision;
      simpleChoices.target = stopped;
    }
    simpleMessage(
      "New requests stopped. An in-flight request may finish; the earlier backup remains available for recovery.",
    );
  });
  bind("portable-simple-resume", async () => {
    const selection = node("portable-simple-recovery").value;
    const item = simpleOperations[Number(selection)];
    if (!item) throw new Error("Choose an earlier backup first.");
    if (item.state === "folder_uncertain")
      throw new Error(
        "Review the uncertain folder before resuming this backup.",
      );
    const generation = simpleGeneration;
    const draft = backupDraftRevision;
    const saved = await api(
      `/library/portable-backups/${item.backup_id}/verify`,
    );
    if (
      !simpleOpen(generation) ||
      selection !== node("portable-simple-recovery").value ||
      simpleOperations[Number(selection)] !== item ||
      draft !== backupDraftRevision ||
      backupOperations !== 1 ||
      node("portable-restore-operation").value.trim() ||
      (node("portable-upload-operation").value.trim() &&
        node("portable-upload-operation").value.trim() !== item.operation_id)
    )
      throw new Error("Your choices changed. Select the earlier backup again.");
    if (
      saved.folder !== item.local_package ||
      saved.commit_sha256 !== item.commit_sha256
    )
      throw new Error(
        "The saved receipt does not match this earlier upload. The operation is retained for review.",
      );
    receipt = saved;
    node("portable-folder").value = saved.folder;
    node("portable-reference").value = saved.commit_sha256;
    node("portable-upload-operation").value = item.operation_id;
    uploadIdentityChanged = false;
    await startSimpleUpload(item);
  });
  bind("portable-simple-reconcile", async () => {
    const selection = node("portable-simple-recovery").value;
    const item = simpleOperations[Number(selection)];
    if (!item || item.state !== "folder_uncertain")
      throw new Error("Choose the backup with an uncertain folder.");
    const generation = simpleGeneration;
    const shown = await api(
      `/library/portable-upload/${item.operation_id}/folder-preview`,
      { method: "POST", body: {} },
    );
    if (
      !simpleOpen(generation) ||
      selection !== node("portable-simple-recovery").value ||
      simpleOperations[Number(selection)] !== item
    )
      throw new Error("Your choices changed. Review the folder again.");
    if (
      !confirm(
        `Review ${shown.remote_folder}: ${shown.observed.replaceAll("_", " ")}. Reconcile this exact folder?`,
      )
    )
      return;
    await api(
      `/library/portable-upload/${item.operation_id}/reconcile-folder`,
      {
        method: "POST",
        body: { fingerprint: shown.fingerprint, confirmed: true },
      },
    );
    await loadSimple();
  });
  bind("portable-simple-abandon", async () => {
    const item =
      simpleOperations[Number(node("portable-simple-recovery").value)];
    if (!item || item.state !== "validating")
      throw new Error("Choose the local backup check to abandon.");
    if (
      !confirm(
        "Abandon this local backup check? The local backup will be kept.",
      )
    )
      return;
    const generation = simpleGeneration;
    await api(`/library/portable-simple/${item.operation_id}/abandon`, {
      method: "POST",
      body: { confirmed: true },
    });
    if (!simpleOpen(generation)) {
      earlierStatus(
        "An earlier local backup check was abandoned; the local backup was kept.",
      );
      return;
    }
    if (node("portable-upload-operation").value === item.operation_id)
      node("portable-upload-operation").value = "";
    await loadSimple();
  });
  bind("portable-restore", async () => {
    if (!checkedRestore || checkedRestore !== restoreCheckKey())
      throw new Error("Check the selected backup before restoring.");
    if (
      restoreIdentityChanged &&
      node("portable-restore-operation").value.trim()
    )
      throw new Error(
        "The restore choices changed. Reconcile the retained reference or clear it before a new restore.",
      );
    if (node("portable-restore-cloud").checked && !savedCloudSettings()) return;
    status("Checking and copying into a new isolated library…");
    const result = await api("/library/portable-restore", {
      method: "POST",
      body: {
        folder: node("portable-restore-source").value.trim(),
        commit_sha256: node("portable-restore-reference").value.trim(),
        target: node("portable-restore-target").value.trim(),
        operation_id: operation("portable-restore-operation"),
        cloud: node("portable-restore-cloud").checked,
        expected_revision: storageRevision,
        expected_target_fingerprint: targetFingerprint,
      },
    });
    status(
      `Restored library retained at ${result.target}. ${result.registration_error || "Open it through the library chooser, then explicitly prepare playback and reconnect providers."}`,
    );
    if (typeof libraryChooser !== "undefined") await libraryChooser.load();
  });
  bind("portable-reconcile-upload", async () => {
    const id = node("portable-upload-operation").value.trim();
    if (!id) throw new Error("Enter the uncertain upload operation reference.");
    const shown = await api(`/library/portable-upload/${id}/folder-preview`, {
      method: "POST",
      body: {},
    });
    if (
      !confirm(
        `Reconcile ${shown.remote_folder}? Observed: ${shown.observed.replaceAll("_", " ")}. Only this exact empty folder may be claimed.`,
      )
    )
      return;
    await api(`/library/portable-upload/${id}/reconcile-folder`, {
      method: "POST",
      body: { fingerprint: shown.fingerprint, confirmed: true },
    });
    status("Folder creation reconciled. Resume the same upload operation.");
  });
  bind("portable-playback", async () => {
    const result = await api("/library/prepare-restored-playback", {
      method: "POST",
      body: {},
    });
    status(
      `Queued local playback preparation for ${result.queued} recording(s).`,
    );
  });
  bind("portable-save-retention", async () => {
    const revision = ++retentionRevision;
    const policy = {
      keep_last: Number(node("portable-keep").value),
      minimum_age_days: Number(node("portable-age").value),
      holds: node("portable-holds")
        .value.split(/[\s,]+/)
        .filter(Boolean),
    };
    invalidateRetention();
    try {
      await api("/library/portable-retention", { method: "PUT", body: policy });
    } catch (error) {
      if (revision === retentionRevision) throw error;
      return;
    }
    if (revision !== retentionRevision) return;
    savedRetentionRevision = revision;
    invalidateRetention();
    status("Manual retention policy saved. No deletion was scheduled.");
  });
  bind("portable-preview-retention", async () => {
    if (!savedRetentionSettings()) return;
    invalidateRetention();
    const preview = retentionPreview;
    let shown;
    try {
      shown = await api("/library/portable-retention/preview", {
        method: "POST",
        body: {
          expected_revision: storageRevision,
          expected_target_fingerprint: targetFingerprint,
        },
      });
    } catch (error) {
      if (preview === retentionPreview) throw error;
      return;
    }
    if (preview !== retentionPreview) return;
    retention = shown;
    const list = node("portable-retention-preview");
    list.replaceChildren();
    for (const item of retention.rows) {
      const row = document.createElement("li");
      row.className = "library-item";
      row.textContent = `Backup from ${item.completed_at}: ${item.eligible ? "eligible for UCloud Trash" : item.reason.replaceAll("_", " ")}`;
      list.append(row);
    }
    node("portable-apply-retention").disabled =
      applyingRetention || !retention.eligible_operations.length;
    status(
      `${retention.eligible_operations.length} backup(s) eligible. Review the list before confirming.`,
    );
  });
  bind("portable-apply-retention", async () => {
    if (!savedRetentionSettings() || applyingRetention) return;
    const shown = retention;
    const preview = retentionPreview;
    if (
      !shown ||
      !confirm(
        `Move ${shown.eligible_operations.length} previewed backups to UCloud Trash? Checked local copies remain.`,
      )
    )
      return;
    const current = () => preview === retentionPreview && shown === retention;
    const earlierOutcome = (message) =>
      status(
        `${node("portable-status").textContent}\nEarlier retention request: ${message}`,
      );
    let result;
    applyingRetention = true;
    try {
      result = await api("/library/portable-retention/apply", {
        method: "POST",
        body: { fingerprint: shown.fingerprint, confirmed: true },
      });
    } catch (error) {
      if (current()) throw error;
      earlierOutcome(error.message);
      return;
    } finally {
      applyingRetention = false;
    }
    const outcome = result.outcomes
      .map((item) => item.state.replaceAll("_", " "))
      .join("; ");
    const message = `Retention result: ${outcome || "no backups moved"}. Trash was not purged.`;
    if (current()) {
      invalidateRetention();
      status(message);
    } else earlierOutcome(message);
  });
  for (const id of ["portable-keep", "portable-age", "portable-holds"])
    node(id).addEventListener("input", () => {
      retentionRevision += 1;
      invalidateRetention();
    });
  node("library-open").addEventListener("click", async () => {
    const load = ++settingsLoad;
    const cloud = cloudRevision;
    const policy = retentionRevision;
    const cleanCloud = cloud === savedCloudRevision;
    const cleanPolicy = policy === savedRetentionRevision;
    try {
      const [settings, providers] = await Promise.all([
        api("/library/portable-storage"),
        api("/providers"),
      ]);
      if (load !== settingsLoad) return;
      const checkedBeforeLoad = restoreCheckKey();
      const loadCloud = cleanCloud && cloud === cloudRevision;
      const loadPolicy = cleanPolicy && policy === retentionRevision;
      const unchangedDraft = cloud === cloudRevision;
      const select = node("portable-provider");
      const selected = loadCloud
        ? settings.target?.provider_id || ""
        : select.value;
      const draftServer = select.selectedOptions[0]?.dataset.server;
      const draftProject = select.selectedOptions[0]?.dataset.project;
      select.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Choose a UCloud server and project";
      select.append(placeholder);
      for (const provider of providers.providers.filter(
        (item) => item.provider_type === "ucloud",
      )) {
        const option = document.createElement("option");
        option.value = provider.id;
        option.dataset.server = provider.base_url || "";
        option.dataset.project = provider.ucloud_transcriber?.project_id || "";
        option.textContent = `${provider.name} · ${provider.base_url || "server not set"} · ${provider.ucloud_transcriber?.project_id || "project not set"}`;
        select.append(option);
      }
      if (
        selected &&
        !Array.from(select.options).some((o) => o.value === selected)
      ) {
        const option = document.createElement("option");
        option.value = selected;
        option.textContent = "Unavailable UCloud connection";
        select.append(option);
      }
      if (selected) select.value = selected;
      if (
        !loadCloud &&
        selected &&
        (draftServer !== select.selectedOptions[0]?.dataset.server ||
          draftProject !== select.selectedOptions[0]?.dataset.project)
      ) {
        node("portable-cloud-approved").checked = false;
        node("portable-upload").disabled = true;
        cloudApproved = false;
        cloudRevision += 1;
        invalidateRetention();
        status("The UCloud connection changed. Review and approve it again.");
      }
      if (loadCloud) {
        storageRevision = settings.storage_revision || null;
        targetFingerprint = settings.target?.fingerprint || null;
        restoreTargetIdentity = settings.restore_target_identity || null;
        storageConflict = false;
        const option = select.selectedOptions[0];
        const sameIdentity =
          settings.target &&
          option?.dataset.server === settings.target.server &&
          option?.dataset.project === settings.target.project_id;
        node("portable-cloud-parent").value = settings.target?.parent || "";
        node("portable-origins").value =
          settings.target?.allowed_transfer_origins.join(", ") || "";
        node("portable-cloud-approved").checked =
          sameIdentity && settings.target.whole_library_approved === true;
        cloudApproved =
          !!sameIdentity && settings.target.whole_library_approved === true;
        node("portable-upload").disabled = !cloudApproved;
        if (settings.target && !sameIdentity && featureOn("cloud_backup"))
          status(
            "The saved UCloud connection changed. Review and approve it again.",
          );
      }
      if (!loadCloud && storageConflict && unchangedDraft) {
        storageRevision = settings.storage_revision || null;
        restoreTargetIdentity = settings.restore_target_identity || null;
        storageConflict = !storageRevision;
        node("portable-cloud-approved").checked = false;
        cloudApproved = false;
        node("portable-upload").disabled = true;
        const target = settings.target;
        const current = target
          ? `Connection ${target.provider_id} · ${target.server} · ${target.project_id} · ${target.parent}. Transfer origins: ${target.allowed_transfer_origins.join(", ") || "none"}. Whole-library approval: ${target.whole_library_approved ? "approved" : "not approved"}`
          : settings.storage_problem || "No saved destination";
        status(
          `Backup settings changed. Your draft was kept. Current saved settings: ${current}. Review them before saving your draft; approve your destination again if needed.`,
        );
      }
      if (loadPolicy) {
        node("portable-keep").value = settings.retention.keep_last;
        node("portable-age").value = settings.retention.minimum_age_days;
        node("portable-holds").value = settings.retention.holds.join(", ");
      }
      settingsLoaded = true;
      if (checkedBeforeLoad !== restoreCheckKey()) invalidateRestoreCheck();
      else updateRestoreControl();
      if (loadCloud || loadPolicy) invalidateRetention();
      if (loadCloud && settings.storage_problem && featureOn("cloud_backup"))
        status(settings.storage_problem);
    } catch (error) {
      if (
        load === settingsLoad &&
        cloud === cloudRevision &&
        policy === retentionRevision
      )
        status(error.message);
    }
  });
  for (const id of [
    "portable-provider",
    "portable-cloud-parent",
    "portable-origins",
    "portable-cloud-approved",
  ])
    node(id).addEventListener("input", () => {
      if (id !== "portable-cloud-approved")
        node("portable-cloud-approved").checked = false;
      cloudRevision += 1;
      invalidateRestoreCheck();
      cloudApproved = false;
      node("portable-upload").disabled = true;
      invalidateRetention();
    });
  for (const id of ["portable-folder", "portable-reference"])
    node(id).addEventListener("input", () => {
      backupDraftRevision += 1;
      if (node("portable-upload-operation").value.trim()) {
        uploadIdentityChanged = true;
        status(
          "Upload reference retained. Reconcile it or clear it before starting a different upload.",
        );
      }
    });
  for (const id of [
    "portable-restore-source",
    "portable-restore-reference",
    "portable-restore-target",
    "portable-restore-cloud",
  ])
    node(id).addEventListener("input", () => {
      backupDraftRevision += 1;
      restoreDraftRevision += 1;
      if (id !== "portable-restore-target") {
        if (id === "portable-restore-source" || id === "portable-restore-cloud")
          restoreReceiptTarget = null;
        invalidateRestoreCheck();
      }
      if (node("portable-restore-operation").value.trim()) {
        restoreIdentityChanged = true;
        status(
          "Restore reference retained. Reconcile it or clear it before starting a different restore.",
        );
      }
    });
  for (const id of ["portable-upload-operation", "portable-restore-operation"])
    node(id).addEventListener("input", () => {
      backupDraftRevision += 1;
      if (id === "portable-restore-operation") {
        restoreDraftRevision += 1;
        restoreIdentityChanged = false;
      } else uploadIdentityChanged = false;
    });
})();
